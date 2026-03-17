import json
import logging
import os
import argparse
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_SCREENING_BUCKET_NAME = os.getenv("S3_SCREENING_BUCKET_NAME")
S3_KB_BUCKET_NAME = os.getenv("S3_KB_BUCKET_NAME")
AWS_PROFILE = os.getenv("AWS_PROFILE")




def _success_response(status_code, data):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(data),
    }

def _error_response(status_code, message):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }

def _build_session():
    logger.info("Building boto3 session")
    if AWS_PROFILE:
        try:
            return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        except Exception as exc:
            logger.warning("Falling back to default session: %s", exc)
    return boto3.Session(region_name=AWS_REGION)

def lambda_handler(event, context):
    logger.info("Override lambda invoked")
    logger.info("Incoming event: %s", json.dumps(event))

    try:
        kb_bucket_name = S3_KB_BUCKET_NAME

        path_params = event.get("pathParameters") or {}
          
        documentKey = None
        if isinstance(path_params, dict):
            documentKey = path_params.get("documentKey")
        
        if not documentKey:
            logger.error("documentKey not received via path parameter")
            return _error_response(400, "Missing documentKey path parameter.")
        
        if not kb_bucket_name:
            logger.error("bucket name not configured")
            return _error_response(500, "S3_KB_BUCKET_NAME is not configured")
        
        session = _build_session()
        s3_client = session.client("s3")

        try: 
            url = s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params= {"Bucket": S3_KB_BUCKET_NAME, "Key": documentKey},
                ExpiresIn=60*20,
            )
            logger.info(f"Generated GET presigned URL: {url}")
            return _success_response(
                200,
                {
                    "message": "Presigned URL received successfully",
                    "presigned_url": url,
                },
            )
        
        except ClientError as exc:
            logger.exception(f"Couldn't get a presigned URL for client method 'get_object'.")
            return _error_response(500, f"Internal server error: {str(exc)}")

    except Exception as exc:
        logger.exception("Unexpected Error")
        return _error_response(500, f"Internal server error: {str(exc)}")
