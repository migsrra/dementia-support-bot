import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_KB_BUCKET_NAME = os.getenv("S3_KB_BUCKET_NAME")


def _build_session():
    logger.info("Building boto3 session")
    if AWS_PROFILE:
        try:
            return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        except Exception as exc:
            logger.warning("Falling back to default session: %s", exc)
    return boto3.Session(region_name=AWS_REGION)


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body),
    }


def _normalize_document(item: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "key": item["Key"],
        "sizeBytes": item.get("Size"),
    }

    last_modified = item.get("LastModified")
    if last_modified is not None:
        normalized["lastModified"] = last_modified.isoformat()

    etag = item.get("ETag")
    if etag:
        normalized["etag"] = etag.strip('"')

    return normalized


def lambda_handler(event, context):
    logger.info("List documents lambda invoked")
    logger.info("Incoming event: %s", json.dumps(event))

    try:
        if not S3_KB_BUCKET_NAME:
            logger.error("S3_KB_BUCKET_NAME is not configured")
            return _response(500, {"error": "S3_KB_BUCKET_NAME is not configured"})

        session = _build_session()
        s3_client = session.client("s3")

        items: list[dict[str, Any]] = []
        continuation_token = None

        while True:
            list_kwargs = {
                "Bucket": S3_KB_BUCKET_NAME,
                "MaxKeys": 1000,
            }
            if continuation_token:
                list_kwargs["ContinuationToken"] = continuation_token

            response = s3_client.list_objects_v2(**list_kwargs)
            contents = response.get("Contents", [])

            for item in contents:
                key = item.get("Key")
                if not key or key.endswith(".metadata.json"):
                    continue
                items.append(_normalize_document(item))

            if not response.get("IsTruncated"):
                break

            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                break

        items.sort(key=lambda item: item.get("key", "").lower())

        return _response(
            200,
            {
                "count": len(items),
                "items": items,
            },
        )

    except ClientError as exc:
        logger.exception("Failed to list objects from KB bucket")
        return _response(
            500,
            {
                "error": "Failed to list documents from S3",
                "details": exc.response.get("Error", {}).get("Message"),
            },
        )
    except Exception as exc:
        logger.exception("Unexpected error while listing documents")
        return _response(500, {"error": f"Internal server error: {str(exc)}"})
