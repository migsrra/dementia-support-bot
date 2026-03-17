import json
import logging
import os
from urllib.parse import unquote

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_SCREENING_BUCKET_NAME = os.getenv("S3_SCREENING_BUCKET_NAME")


def _build_session():
    logger.info("Building boto3 session")
    if AWS_PROFILE:
        try:
            return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        except Exception as exc:
            logger.warning("Falling back to default session: %s", exc)
    return boto3.Session(region_name=AWS_REGION)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body),
    }


def _extract_quarantine_key(event: dict) -> str | None:
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    quarantine_key = None
    if isinstance(path_params, dict):
        quarantine_key = (
            path_params.get("quarantineKey")
            or path_params.get("proxy")
            or path_params.get("key")
        )

    if not quarantine_key and isinstance(query_params, dict):
        quarantine_key = (
            query_params.get("quarantineKey")
            or query_params.get("key")
        )

    if not isinstance(quarantine_key, str):
        return None

    quarantine_key = unquote(quarantine_key).strip()
    return quarantine_key or None


def _head_object(s3_client, bucket_name: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket_name, Key=key)
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def lambda_handler(event, context):
    logger.info("Delete quarantine document lambda invoked")
    logger.info("Incoming event: %s", json.dumps(event))

    try:
        if not S3_SCREENING_BUCKET_NAME:
            logger.error("S3_SCREENING_BUCKET_NAME is not configured")
            return _response(
                500,
                {"error": "S3_SCREENING_BUCKET_NAME is not configured"},
            )

        quarantine_key = _extract_quarantine_key(event)
        if not quarantine_key:
            logger.error("quarantineKey was not provided")
            return _response(400, {"error": "Missing quarantineKey."})

        if not quarantine_key.startswith("rejected/"):
            logger.error("Invalid quarantineKey outside rejected/: %s", quarantine_key)
            return _response(
                400,
                {
                    "error": "Only rejected quarantine objects can be deleted.",
                    "key": quarantine_key,
                },
            )

        session = _build_session()
        s3_client = session.client("s3")

        if not _head_object(s3_client, S3_SCREENING_BUCKET_NAME, quarantine_key):
            logger.error("Quarantine document not found: %s", quarantine_key)
            return _response(
                404,
                {
                    "error": "Quarantine document not found.",
                    "key": quarantine_key,
                },
            )

        s3_client.delete_object(
            Bucket=S3_SCREENING_BUCKET_NAME,
            Key=quarantine_key,
        )

        return _response(
            200,
            {
                "message": "Quarantine document deleted successfully.",
                "key": quarantine_key,
            },
        )

    except ClientError as exc:
        logger.exception("Failed to delete quarantine document from screening bucket")
        return _response(
            500,
            {
                "error": "Failed to delete quarantine document from S3",
                "details": exc.response.get("Error", {}).get("Message"),
            },
        )
    except Exception as exc:
        logger.exception("Unexpected error while deleting quarantine document")
        return _response(500, {"error": f"Internal server error: {str(exc)}"})
