import json
import logging
import os
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME")


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return datetime.min

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min

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
        if not DYNAMODB_TABLE_NAME:
            logger.error("DYNAMODB_TABLE_NAME not configured")
            return _error_response(500, "Configuration details missing. DYNAMODB_TABLE_NAME is required.")

        session = _build_session()
        table = session.resource("dynamodb").Table(DYNAMODB_TABLE_NAME)

        # Keep entries where deleted is missing or set to anything other than True.
        filter_expression = Attr("deleted").not_exists() | Attr("deleted").ne(True)

        items = []
        scan_kwargs = {"FilterExpression": filter_expression}

        while True:
            response = table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))

            last_evaluated_key = response.get("LastEvaluatedKey")
            if not last_evaluated_key:
                break

            scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

        items.sort(key=lambda item: _parse_timestamp(item.get("timestamp")), reverse=True)

        return _success_response(
            200,
            {
                "count": len(items),
                "items": items,
            },
        )

    except ClientError as exc:
        logger.error("DynamoDB client error while listing unsupported prompts: %s", exc)
        return _error_response(500, "Failed to fetch items from DynamoDB")

    except Exception as exc:
        logger.exception("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")

