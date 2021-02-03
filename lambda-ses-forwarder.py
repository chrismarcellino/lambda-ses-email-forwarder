"""
lambda-ses-forwarder.py by Chris Marcellino, Version 1.1.

Python3 rewrite based on [aws_lambda_ses_forwarder_python3](https://github.com/tedder/aws_lambda_ses_forwarder_python3),
which was a port of the original node.js forwarder [aws-lambda-ses-forwarder](https://github.com/arithmetric/aws-lambda-ses-forwarder),
but re-written to allow bounce messages, store the mapping in environment variables with JSON,
automatic determination of 'noreply' address and better email address parsing using built-in
Python utility functions. 

Requires ses:SendRawEmail, ses:SendEmail, and s3:GetObject role policy permissions (plus
CloudWatch logging if desired). See README.md for instructions on how to deliver messages
to an S3 bucket and fire this lambda, and optionally set them to expire there to avoid
accumulation. Forwarding domains (or emails) must be verified, and you must be out of the
sandbox to forward to non-verified domains. 

The required environment variables are SES_INCOMING_BUCKET, which must be the name of the
SES rule-set rule delivery bucket, and FORWARD_MAPPING which should be a 1:1 mapping of receiving
addresses or usernames (with or without a '+' prefix) to forwarding addresses in JSON, 
for example:
{"chris": "chris@destination.org", "friend@example.com": "friend@destination.com"}.
Note that a key of "chris" will also foward all "chris+xyz" suffixes, unless there is a
more specific rule for the suffix as well. 
"""

import email
import json
import logging
import os
import re

from email.utils import parseaddr, formataddr

import boto3
from botocore.exceptions import ClientError

# environment variable configuration parameters
SES_INCOMING_BUCKET = os.environ['SES_INCOMING_BUCKET']         # S3 bucket where SES stores incoming emails
FORWARD_MAPPING = json.loads(os.environ['FORWARD_MAPPING'])     # JSON dictionary of recipient to destination mapping (entries are string:string)
VERIFIED_FROM_EMAIL = os.environ.get('VERIFIED_FROM_EMAIL')     # email address that is verified by SES to use as From address (optional)
DEFAULT_VERIFIED_FROM_PREFIX = 'noreply'                        # otherwise, the verified forwarding domain is used with this prefix

s3 = boto3.client('s3')
ses = boto3.client('ses')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# the lambda handler method
def handler(event, context):
    # get the lambda event
    record = event['Records'][0]
    assert record['eventSource'] == 'aws:ses'
    
    # get the message from S3
    o = s3.get_object(Bucket=SES_INCOMING_BUCKET, Key=record['ses']['mail']['messageId'])
    raw_mail = o['Body'].read()
    msg = email.message_from_bytes(raw_mail)
    
    # determine the display, reply and bounce addresses
    original_from = msg['From']
    
    reply_to = msg['Reply-To']
    if not reply_to:
        reply_to = original_from
    
    return_path = msg['Return-Path']
    if not return_path:
        return_path = reply_to
    
    # remove non-forwarded components
    del msg['DKIM-Signature']
    del msg['Sender']
    
    at_least_one_recipient_found = False
    for recipient in record['ses']['receipt']['recipients']:
        del msg['From']
        del msg['Return-Path']
        del msg['Reply-To']     # cannot have the original Reply-To as SES will reject non-verified domains here
        
        # if no VERIFIED_FROM_EMAIL is provided, use 'noreply' at the receiving domain
        verified_from_email = VERIFIED_FROM_EMAIL
        if not verified_from_email:
            verified_from_email = DEFAULT_VERIFIED_FROM_PREFIX
        if '@' not in verified_from_email:
            verified_from_email = verified_from_email + '@' + recipient.split('@')[1]
        
        # must accept from addresses with or without a name element: e.g. "me@example.com" or "Name(s) <me@example.com>".
        # if we don't have a sender name, copy the original email address to use as a sender name so the recipient
        # can identify the sender.
        from_tuple = parseaddr(original_from)
        from_name = from_tuple[0]
        if not from_name:
            from_name = from_tuple[1]
        msg['From'] = formataddr((from_name, verified_from_email))
        
        # send replies to the original sender (note that this will be the original_from if there was no reply_to; see above)
        msg['Reply-To'] = reply_to
        
        # try to match the entire email address, then try matching just user portion with any '+' suffixes,
        # and then without the suffixes, in that order
        recipient = recipient.lower();
        forward_to = FORWARD_MAPPING.get(recipient)
        if not forward_to:
            forward_to = FORWARD_MAPPING.get(recipient.split('@')[0])
        if not forward_to:
            forward_to = FORWARD_MAPPING.get(recipient.split('+')[0])
        
        if forward_to:
            at_least_one_recipient_found = True
            try:
                o = ses.send_raw_email(Destinations=[forward_to], RawMessage=dict(Data=msg.as_string()))
                logger.info('Forwarded email from <{}> for <{}> to <{}>. SendRawEmail response={}'.format(parseaddr(original_from)[1], recipient, forward_to, json.dumps(o)))
            except ClientError as e:
                logger.info('Error while forwarding email for {} to {}: {}'.format(recipient, forward_to, e))
                send_bounce(return_path, recipient, verified_from_email, e)
        
    if not at_least_one_recipient_found:
        logger.error('Check SES rule set; no recipient found in forwarding map for message: {}'.format(msg))


# sends a bounce message, for example, for malformed email or email greater than SES's sending size limits
def send_bounce(return_path, recipient, verified_from_email, e):
    # remove any display name from the return path
    return_path = parseaddr(return_path)[1]
    
    message={
        'Subject': {
            'Data': 'Undeliverable: Auto-Reply',
            'Charset': 'UTF-8'
        },
        'Body': {
            'Text': {
                'Data':('An error occurred while forwarding email for {} to its final destination address. ' +
                        'Check that the size of the email and its attachments are not too large or contact the administrator for assistance. \n' +
                        '\n' +
                        'The error message was: {}').format(recipient, e),
                'Charset': 'UTF-8'
            }
        }
    }
    try:
        source = formataddr(('Mail Delivery Subsystem', verified_from_email))
        destination = {'ToAddresses':[return_path],'CcAddresses':[recipient]};
        o = ses.send_email(Source=source, Destination=destination, Message=message);
        logger.info('Sent bounce email to <{}>. SendRawEmail response={}'.format(return_path, json.dumps(o)))
    except ClientError as e:
        logger.error('Error while sending bounce email to <{}>: {}'.format(return_path, e))
