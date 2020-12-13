# AWS Lambda SES Email Forwarder

Yet another Python3 script for AWS Lambda that uses the inbound/outbound
capabilities of AWS Simple Email Service (SES) to run a "serverless" email
forwarding service.

(This README.md is adapted from [aws_lambda_ses_forwarder_python3](https://github.com/tedder/aws_lambda_ses_forwarder_python3)
under MIT license.)

Compared to previous versions, this version:
- has been re-written to allow bounce messages to the sender, for example when
size of the message is > 10 mb and cannot be forwarded
(note that this means that the role policy also requires SendEmail access and not
just SendRawEmail as in other versions)
- store the mapping and bucket name in environment variables with JSON
- permit automatic determination of 'noreply' address to be relative to (and
match the) receiving domain name
- better email address parsing using built in python methods to allow for from
emails to have names and emails or just email addresses
- remove unnecessary options in the spirit of minimalism
- this uses slightly less memory and is 50% faster than the node.js version on
small emails (though similar for large emails)

Recommend a 512 MB Lambda session to allow for bouncing of messages of arbitrary
size, but normally uses less than 128 MB. Performance increases make the time-GB
product and hence price per email similar at all relatively small lambda sizes.

#Instructions

1. Caveats:
- SES only allows sending email from addresses or domains that are verified.
Since this script is meant to allow forwarding email from any sender, the
message is modified to allow forwarding through SES and reflect the original
sender. This script adds a Reply-To header with the original sender, but the
From header is changed to display the original sender but to be sent from a
'noreply' header.

  For example, if an email sent by `Jane Example <jane@example.com>` to
  `info@example.com` is processed by this script, the From and Reply-To headers
  will be set to:

  ```
  From: Jane Example at jane@example.com <noreply@example.com>
  Reply-To: jane@example.com
  ```

  To override the name of the return address behavior, set a verified from
  address (e.g., forwarder@example.com) in the VERIFIED_FROM_EMAIL environment
  variable and the header will look like this.

  ```
  From: Jane Example <forwarder@example.com>
  Reply-To: jane@example.com
  ```

- SES only allows receiving email sent to addresses within verified domains. For
more information, see:
http://docs.aws.amazon.com/ses/latest/DeveloperGuide/verify-domains.html

- SES only allows sending emails up to 10 MB in size (including attachments
after encoding). See:
https://docs.aws.amazon.com/ses/latest/DeveloperGuide/limits.html

- Initially SES users are in a sandbox environment that has a number of
limitations. See:
http://docs.aws.amazon.com/ses/latest/DeveloperGuide/limits.html

2. Configure the role policy to
 the following:
 ```
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "ses:SendRawEmail",
                "ses:SendEmail"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject"
            ],
         	"Resource": "arn:aws:s3:::S3-BUCKET-NAME/*"
       }
    ]
 }
 ```

3. In AWS SES, verify the domains for which you want to receive and forward
email. Also configure the DNS MX record for these domains to point to the email
receiving (or inbound) SES endpoint. See [SES documentation](http://docs.aws.amazon.com/ses/latest/DeveloperGuide/regions.html#region-endpoints)
for the email receiving endpoints in each region.

If you have the sandbox level of access to SES, then also verify any email
addresses to which you want to forward email that are not on verified domains.

4. If you have not configured inbound email handling, create a new Rule Set.
Otherwise, you can use an existing one.

5. Create a rule for handling email forwarding functionality.

 - On the Recipients configuration page, add any email addresses from which you
 want to forward email.

 - On the Actions configuration page, add an S3 action first and then an Lambda
 action.

 - For the S3 action: Create or choose an existing S3 bucket. Optionally, add an
 object key prefix. Leave Encrypt Message unchecked and SNS Topic set to [none].

 - For the Lambda action: Choose the SesForwarder Lambda function. Leave
 Invocation Type set to Event and SNS Topic set to [none].

 - Finish by naming the rule, ensuring it's enabled and that spam and virus
 checking are used.

 - If you get an error like "Could not write to bucket", follow step 7 before
 completing this one

 - If you are asked by SES to add permissions to access `lambda:InvokeFunction`,
 agree to it.

6. The S3 bucket policy needs to be configured so that your IAM user has read
and write access to the S3 bucket. When you set up the S3 action in SES, it may
add a bucket policy statement that denies all users other than root access to
get objects. This causes access issues from the Lambda script, so you will
likely need to adjust the bucket policy statement with one like this:
 ```
 {
    "Version": "2012-10-17",
    "Statement": [
       {
          "Sid": "GiveSESPermissionToWriteEmail",
          "Effect": "Allow",
          "Principal": {
             "Service": "ses.amazonaws.com"
          },
          "Action": "s3:PutObject",
          "Resource": "arn:aws:s3:::S3-BUCKET-NAME/*",
          "Condition": {
             "StringEquals": {
                "aws:Referer": "AWS-ACCOUNT-ID"
             }
          }
       }
    ]
 }
 ```

7. Optionally set the S3 lifecycle for this bucket to delete/expire objects
after a n days to clean up the saved emails.

8. Set the environment variables to configure the script forwarding addresses etc.
There are two required variables:
SES_INCOMING_BUCKET is the S3 bucket to pull email from. 

FORWARD_MAPPING is a JSON dictionary of recipient to destination mapping (entries are string:string)
The strings can be full email addresses, usernames, or usernames and prefixes and they will match
with precedence for the most specific pattern first.

VERIFIED_FROM_EMAIL can be either omitted, a username or full email address to return messages to. 
If a username is provided, the domain will be the receiving address's domain. 
