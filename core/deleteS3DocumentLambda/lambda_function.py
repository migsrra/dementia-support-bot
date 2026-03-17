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
S3_KB_BUCKET_NAME = os.getenv("S3_KB_BUCKET_NAME")


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


def _extract_document_key(event: dict) -> str | None:
    path_params = event.get("pathParameters") or {}
    query_params = event.get("queryStringParameters") or {}

    document_key = None
    if isinstance(path_params, dict):
        document_key = (
            path_params.get("documentKey")
            or path_params.get("key")
            or path_params.get("document_key")
        )

    if not document_key and isinstance(query_params, dict):
        document_key = query_params.get("documentKey") or query_params.get("key")

    if not isinstance(document_key, str):
        return None

    document_key = unquote(document_key).strip()
    return document_key or None


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
    logger.info("Delete document lambda invoked")
    logger.info("Incoming event: %s", json.dumps(event))

    try:
        if not S3_KB_BUCKET_NAME:
            logger.error("S3_KB_BUCKET_NAME is not configured")
            return _response(500, {"error": "S3_KB_BUCKET_NAME is not configured"})

        document_key = _extract_document_key(event)
        if not document_key:
            logger.error("documentKey was not provided")
            return _response(400, {"error": "Missing documentKey path parameter."})

        metadata_key = f"{document_key}.metadata.json"

        session = _build_session()
        s3_client = session.client("s3")

        if not _head_object(s3_client, S3_KB_BUCKET_NAME, document_key):
            logger.error("Document not found: %s", document_key)
            return _response(404, {"error": "Document not found.", "key": document_key})

        delete_objects = [{"Key": document_key}]
        metadata_found = _head_object(s3_client, S3_KB_BUCKET_NAME, metadata_key)
        if metadata_found:
            delete_objects.append({"Key": metadata_key})

        delete_response = s3_client.delete_objects(
            Bucket=S3_KB_BUCKET_NAME,
            Delete={
                "Objects": delete_objects,
                "Quiet": False,
            },
        )

        errors = delete_response.get("Errors", [])
        if errors:
            logger.error("Failed to delete one or more objects: %s", errors)
            return _response(
                500,
                {
                    "error": "Failed to delete one or more S3 objects.",
                    "details": errors,
                },
            )

        deleted_keys = [entry.get("Key") for entry in delete_response.get("Deleted", [])]

        return _response(
            200,
            {
                "message": "Document deleted successfully.",
                "key": document_key,
                "metadataKey": metadata_key if metadata_found else None,
                "deletedKeys": deleted_keys,
            },
        )

    except ClientError as exc:
        logger.exception("Failed to delete document from KB bucket")
        return _response(
            500,
            {
                "error": "Failed to delete document from S3",
                "details": exc.response.get("Error", {}).get("Message"),
            },
        )
    except Exception as exc:
        logger.exception("Unexpected error while deleting document")
        return _response(500, {"error": f"Internal server error: {str(exc)}"})
