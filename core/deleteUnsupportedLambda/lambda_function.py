import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME")


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

        path_params = event.get("pathParameters") or {}
        query_params = event.get("queryStringParameters") or {}
        
        print(f"path_params: {path_params}")
        print(f"query_params: {query_params}")
        
        query_id = None
        if isinstance(path_params, dict):
            query_id = path_params.get("queryID") or path_params.get("query_id")

        timestamp = None
        if isinstance(query_params, dict):
            timestamp = query_params.get("timestamp")
        if not timestamp and isinstance(path_params, dict):
            # Backward-compatible fallback for older route patterns.
            timestamp = path_params.get("timestamp")

        if not query_id:
            logger.error("queryID not received via path parameter")
            return _error_response(400, "Missing queryID path parameter.")

        session = _build_session()
        table = session.resource("dynamodb").Table(DYNAMODB_TABLE_NAME)

        # For tables with a sort key, UpdateItem must include both query_id and timestamp.
        # If timestamp is not provided via query param, look up the latest item for this query_id.
        if not timestamp:
            lookup = table.query(
                KeyConditionExpression=Key("query_id").eq(query_id),
                ScanIndexForward=False,
                Limit=1,
            )
            items = lookup.get("Items", [])
            if not items:
                return _error_response(404, "Item not found for provided query_id")
            timestamp = items[0].get("timestamp")

        if not timestamp:
            logger.error("timestamp not available for query_id: %s", query_id)
            return _error_response(400, "Missing timestamp for target item.")

        table.update_item(
            Key={"query_id": query_id, "timestamp": timestamp},
            UpdateExpression="SET deleted = :deleted",
            ExpressionAttributeValues={":deleted": True},
            ExpressionAttributeNames={
                "#pk": "query_id",
                "#sk": "timestamp",
            },
            ConditionExpression="attribute_exists(#pk) AND attribute_exists(#sk)",
            ReturnValues="UPDATED_NEW",
        )

        return _success_response(
            200,
            {
                "message": "Item updated successfully",
                "query_id": query_id,
                "timestamp": timestamp,
                "deleted": True,
            },
        )

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code == "ConditionalCheckFailedException":
            return _error_response(404, "Item not found for provided query_id")

        logger.error("DynamoDB client error while updating item: %s", exc)
        return _error_response(500, "Failed to update item in DynamoDB")

    except Exception as exc:
        logger.exception("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")

