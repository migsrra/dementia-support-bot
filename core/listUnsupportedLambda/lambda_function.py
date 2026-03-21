import base64
import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME")
DYNAMODB_PK_ALL_INDEX_NAME = os.getenv(
    "DYNAMODB_PK_ALL_INDEX_NAME", "pk_all-timestamp-index"
)
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
PK_ALL_VALUE = "ALL"


def _encode_next_token(last_evaluated_key):
    if not last_evaluated_key:
        return None

    payload = json.dumps(last_evaluated_key, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")

def _decode_next_token(next_token):
    try:
        padded_token = next_token + "=" * (-len(next_token) % 4)
        decoded = base64.urlsafe_b64decode(padded_token.encode("utf-8")).decode("utf-8")
        parsed = json.loads(decoded)
    except Exception as exc:
        raise ValueError("Query parameter 'nextToken' is invalid.") from exc

    if not isinstance(parsed, dict):
        raise ValueError("Query parameter 'nextToken' is invalid.")

    return parsed

def _get_query_params(event):
    query_params = event.get("queryStringParameters") or {} if isinstance(event, dict) else {}
    if not isinstance(query_params, dict):
        return {}
    return query_params

def _parse_pagination(query_params):
    if not query_params:
        return None, None, False

    raw_limit = query_params.get("limit")
    raw_next_token = query_params.get("nextToken")
    pagination_requested = raw_limit not in (None, "") or raw_next_token not in (None, "")

    if not pagination_requested:
        return None, None, False

    if raw_limit in (None, ""):
        limit = DEFAULT_PAGE_SIZE
    else:
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("Query parameter 'limit' must be a positive integer.") from exc

        if limit <= 0:
            raise ValueError("Query parameter 'limit' must be a positive integer.")
        if limit > MAX_PAGE_SIZE:
            raise ValueError(f"Query parameter 'limit' cannot exceed {MAX_PAGE_SIZE}.")

    exclusive_start_key = None
    if raw_next_token not in (None, ""):
        if not isinstance(raw_next_token, str):
            raise ValueError("Query parameter 'nextToken' must be a string.")
        exclusive_start_key = _decode_next_token(raw_next_token.strip())

    return limit, exclusive_start_key, True

def _parse_sort_direction(query_params):
    raw_sort_direction = query_params.get("sortDirection")
    if raw_sort_direction in (None, "", "latest"):
        return "latest", False
    if raw_sort_direction == "oldest":
        return "oldest", True
    raise ValueError("Query parameter 'sortDirection' must be either 'latest' or 'oldest'.")

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

def _query_unsupported_items(
    table,
    filter_expression,
    page_size,
    exclusive_start_key,
    scan_index_forward,
):
    query_kwargs = {
        "IndexName": DYNAMODB_PK_ALL_INDEX_NAME,
        "KeyConditionExpression": Key("pk_all").eq(PK_ALL_VALUE),
        "FilterExpression": filter_expression,
        "ScanIndexForward": scan_index_forward,
    }
    if exclusive_start_key:
        query_kwargs["ExclusiveStartKey"] = exclusive_start_key
    if page_size is not None:
        query_kwargs["Limit"] = page_size

    items = []
    last_evaluated_key = None

    while page_size is None or len(items) < page_size:
        if page_size is not None:
            query_kwargs["Limit"] = max(1, page_size - len(items))

        response = table.query(**query_kwargs)
        items.extend(response.get("Items", []))

        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

        query_kwargs["ExclusiveStartKey"] = last_evaluated_key

    if page_size is not None:
        items = items[:page_size]

    return items, last_evaluated_key

def lambda_handler(event, context):
    try:
        if not DYNAMODB_TABLE_NAME:
            logger.error("DYNAMODB_TABLE_NAME not configured")
            return _error_response(500, "Configuration details missing. DYNAMODB_TABLE_NAME is required.")

        query_params = _get_query_params(event)
        page_size, exclusive_start_key, pagination_requested = _parse_pagination(query_params)
        sort_direction, scan_index_forward = _parse_sort_direction(query_params)

        session = _build_session()
        table = session.resource("dynamodb").Table(DYNAMODB_TABLE_NAME)

        # Keep entries where deleted is missing or set to anything other than True.
        filter_expression = Attr("deleted").not_exists() | Attr("deleted").ne(True)

        requested_page_size = page_size if pagination_requested else None
        items, last_evaluated_key = _query_unsupported_items(
            table,
            filter_expression,
            requested_page_size,
            exclusive_start_key,
            scan_index_forward,
        )
        next_token = _encode_next_token(last_evaluated_key) if pagination_requested else None

        payload = {
            "count": len(items),
            "items": items,
        }
        if pagination_requested:
            payload["nextToken"] = next_token
            payload["pageSize"] = page_size
            payload["sortDirection"] = sort_direction

        return _success_response(200, payload)

    except ValueError as exc:
        logger.warning("Invalid pagination request: %s", exc)
        return _error_response(400, str(exc))

    except ClientError as exc:
        logger.error("DynamoDB client error while listing unsupported prompts: %s", exc)
        return _error_response(500, "Failed to fetch items from DynamoDB")

    except Exception as exc:
        logger.exception("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")
