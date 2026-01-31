import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
BEDROCK_KB_ID = os.getenv("BEDROCK_KB_ID")
BEDROCK_DS_ID = os.getenv("BEDROCK_DS_ID")
AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

def _build_session():
    logger.info("Building boto3 session")
    if AWS_PROFILE:
        try:
            return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        except Exception as exc:
            logger.warning("Falling back to default session: %s", exc)
    return boto3.Session(region_name=AWS_REGION)

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

def lambda_handler(event, context):
    try:
        session = _build_session()

        # Start Bedrock KB ingestion job
        if not BEDROCK_KB_ID or not BEDROCK_DS_ID:
            logger.warning("BEDROCK_KB_ID or BEDROCK_DS_ID not configured, skipping ingestion")
        else:
            try:
                bedrock_client = session.client("bedrock-agent")
                ingestion_response = bedrock_client.start_ingestion_job(
                    knowledgeBaseId=BEDROCK_KB_ID,
                    dataSourceId=BEDROCK_DS_ID,
                    description="Syncing knowledge on S3 changes",
                )
                logger.info(f"Ingestion job started: {ingestion_response.get('ingestionJobId')}")
            except Exception as e:
                logger.error(f"Failed to start ingestion job: {e}")
                return _error_response(500, "Failed to start Bedrock ingestion job")

        response_body = {
            "message": "Ingestion job started",
        }

        return _success_response(200, response_body)

    except Exception as exc:
        logger.info("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")

