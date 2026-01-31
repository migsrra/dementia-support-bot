import base64
import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from python_multipart import parse_form
from io import BytesIO

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_S3_FOLDER = os.getenv("DEFAULT_S3_FOLDER", "")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

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
        bucket_name = S3_BUCKET_NAME

        headers = event.get("headers") or {}
        body = event.get("body")
        if body is None:
            return _error_response(400, "Missing request body")

        path_params = event.get("pathParameters") or {}
        item_name = path_params.get("item") if isinstance(path_params, dict) else None

        body_str = body if isinstance(body, str) else ""
        body_bytes = None
        if event.get("isBase64Encoded"):
            try:
                body_bytes = base64.b64decode(body_str)
            except Exception:
                return _error_response(400, "Invalid base64 body")
        else:
            body_bytes = body_str.encode("utf-8")

        # Parse multipart/form-data
        content_type = headers.get("content-type") or headers.get("Content-Type")

        parsed_files = {}
        
        def on_file(file) -> None:
            parsed_files[file.field_name] = {
                "file": file.file_object,
                "filename": file.file_name,
            }

        parse_form(
            headers={
                "Content-Type": content_type,
                "Content-Length": headers.get("Content-Length")
            },
            input_stream=BytesIO(body_bytes),
            on_file=on_file,
            on_field=None
        )

        pdfs = [
            entry for entry in parsed_files.values() 
            if entry["filename"] and entry["filename"].lower().endswith(b".pdf")
        ]

        if len(pdfs) != 1:
            return _error_response(400, "Expected only one PDF file")
        
        pdf_entry = pdfs[0]
        file_obj = pdf_entry["file"]
        filename = pdf_entry["filename"]

        if file_obj is None:
            return _error_response(400, "Unable to parse multipart/form-data")
        

        # Ensure client set file name
        file_name = item_name or filename
        if not file_name:
            return _error_response(400, "No filename available in path parameter nor form body")


        # Ensure .pdf extension
        if not file_name.lower().endswith('.pdf'):
            file_name = file_name + '.pdf'

        ###############################################################################################################
        # Eventually use this part of code to verify relevancy with either 1. Guardrail or 2. Dedicated Bedrock Agent #
        ###############################################################################################################


        # Build S3 object key
        s3_key = f"{DEFAULT_S3_FOLDER.strip('/')}{file_name}"

        # Upload to S3
        session = _build_session()
        s3_client = session.client("s3")
        logger.info("Built boto3 session and s3 client")

        try:
            logger.info(f"Attempting to upload file: {file_name} to s3")
            file_obj.seek(0)
            s3_client.upload_fileobj(Fileobj=file_obj, Bucket=bucket_name, Key=s3_key, ExtraArgs={"ContentType": "application/pdf"})
            logger.info(f"Uploaded {file_name} to s3://{bucket_name}/{s3_key}")
        except Exception as e:
            logger.error(f"S3 upload failed: {e}")
            logger.exception("Full traceback:")
            return _error_response(500, "Failed to upload PDF to S3")

        response_body = {
            "message": "PDF uploaded successfully",
            "bucket": bucket_name,
            "key": s3_key,
        }

        return _success_response(200, response_body)

    except Exception as exc:
        logger.info("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")

