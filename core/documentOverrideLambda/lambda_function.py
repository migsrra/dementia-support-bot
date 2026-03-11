import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_SCREENING_BUCKET_NAME = os.getenv("S3_SCREENING_BUCKET_NAME")
S3_KB_BUCKET_NAME = os.getenv("S3_KB_BUCKET_NAME")


def _response(status_code, body):
    logger.info("Returning response: status_code=%s body=%s", status_code, body)
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "http://localhost:5173",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
        },
        "body": json.dumps(body),
    }


def move_object(s3_client, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str):
    s3_client.copy_object(
        Bucket=dst_bucket,
        CopySource={"Bucket": src_bucket, "Key": src_key},
        Key=dst_key,
        MetadataDirective="COPY",
    )
    s3_client.delete_object(Bucket=src_bucket, Key=src_key)


def extract_final_filename(quarantine_key: str, upload_id: str) -> str:
    logger.info(
        "Extracting final filename from quarantine_key=%s upload_id=%s",
        quarantine_key,
        upload_id,
    )

    if not quarantine_key:
        logger.error("Missing quarantineKey")
        raise ValueError("Missing quarantineKey")

    if not upload_id:
        logger.error("Missing uploadId")
        raise ValueError("Missing uploadId")

    if not quarantine_key.startswith("rejected/"):
        logger.error("Override attempted on non-rejected object: %s", quarantine_key)
        raise ValueError("Override is only allowed for rejected/ objects")

    leaf = quarantine_key[len("rejected/"):]

    expected_prefix = f"{upload_id}-"
    if not leaf.startswith(expected_prefix):
        logger.error(
            "quarantineKey does not match uploadId: quarantine_key=%s upload_id=%s expected_prefix=%s",
            quarantine_key,
            upload_id,
            expected_prefix,
        )
        raise ValueError("quarantineKey does not match uploadId")

    final_name = leaf[len(expected_prefix):].strip()
    if not final_name:
        logger.error("Missing filename in quarantineKey: %s", quarantine_key)
        raise ValueError("Missing filename in quarantineKey")

    if not final_name.lower().endswith(".pdf"):
        logger.info("Filename missing .pdf extension, appending it: %s", final_name)
        final_name += ".pdf"

    logger.info("Extracted final filename: %s", final_name)
    return final_name


def lambda_handler(event, context):
    logger.info("Override lambda invoked")
    logger.info("Incoming event: %s", json.dumps(event))

    try:
        if not S3_SCREENING_BUCKET_NAME:
            logger.error("S3_SCREENING_BUCKET_NAME is not configured")
            return _response(500, {"error": "S3_SCREENING_BUCKET_NAME is not configured"})

        if not S3_KB_BUCKET_NAME:
            logger.error("S3_KB_BUCKET_NAME is not configured")
            return _response(500, {"error": "S3_KB_BUCKET_NAME is not configured"})

        body = event.get("body")
        if not body:
            logger.error("Missing request body")
            return _response(400, {"error": "Missing request body"})

        logger.info("Raw request body: %s", body)

        try:
            payload = json.loads(body)
            logger.info("Parsed payload successfully: %s", payload)
        except Exception:
            logger.error("Body must be valid JSON")
            return _response(400, {"error": "Body must be valid JSON"})

        upload_id = payload.get("uploadId")
        quarantine_key = payload.get("quarantineKey")

        logger.info(
            "Received override request: upload_id=%s quarantine_key=%s",
            upload_id,
            quarantine_key,
        )

        try:
            kb_key = extract_final_filename(quarantine_key, upload_id)
        except ValueError as e:
            logger.error("Filename extraction failed: %s", str(e))
            return _response(400, {"error": str(e)})

        override_key = quarantine_key.replace("rejected/", "override/", 1)

        logger.info(
            "Creating S3 client in region=%s screening_bucket=%s kb_bucket=%s",
            AWS_REGION,
            S3_SCREENING_BUCKET_NAME,
            S3_KB_BUCKET_NAME,
        )
        s3 = boto3.client("s3", region_name=AWS_REGION)

        # Make sure source exists
        logger.info(
            "Checking source object exists: bucket=%s key=%s",
            S3_SCREENING_BUCKET_NAME,
            quarantine_key,
        )
        try:
            s3.head_object(Bucket=S3_SCREENING_BUCKET_NAME, Key=quarantine_key)
            logger.info("Source object found")
        except ClientError as e:
            logger.error(
                "Quarantined file not found: bucket=%s key=%s error=%s",
                S3_SCREENING_BUCKET_NAME,
                quarantine_key,
                e,
            )
            return _response(404, {"error": "Quarantined file not found"})

        # Optional: prevent overwrite in KB
        logger.info(
            "Checking for existing KB object: bucket=%s key=%s",
            S3_KB_BUCKET_NAME,
            kb_key,
        )
        try:
            s3.head_object(Bucket=S3_KB_BUCKET_NAME, Key=kb_key)
            logger.error("KB object already exists: %s", kb_key)
            return _response(409, {
                "error": "A file with this name already exists in the KB bucket",
                "kbKey": kb_key,
            })
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            logger.info("KB existence check returned error_code=%s", error_code)
            if error_code not in ("404", "NoSuchKey", "NotFound"):
                logger.error(
                    "Unexpected error while checking KB object existence: %s",
                    e,
                )
                raise

        logger.info(
            "Moving overridden object within screening bucket: source_bucket=%s source_key=%s dest_bucket=%s dest_key=%s",
            S3_SCREENING_BUCKET_NAME,
            quarantine_key,
            S3_SCREENING_BUCKET_NAME,
            override_key,
        )
        move_object(
            s3,
            S3_SCREENING_BUCKET_NAME,
            quarantine_key,
            S3_SCREENING_BUCKET_NAME,
            override_key,
        )
        logger.info("Override staging move successful")

        logger.info(
            "Copying object from screening to KB: source_bucket=%s source_key=%s dest_bucket=%s dest_key=%s",
            S3_SCREENING_BUCKET_NAME,
            override_key,
            S3_KB_BUCKET_NAME,
            kb_key,
        )
        s3.copy_object(
            Bucket=S3_KB_BUCKET_NAME,
            CopySource={
                "Bucket": S3_SCREENING_BUCKET_NAME,
                "Key": override_key,
            },
            Key=kb_key,
            MetadataDirective="COPY",
            ContentType="application/pdf",
        )
        logger.info("Copy successful")

        # logger.info(
        #     "Deleting source object from screening bucket: bucket=%s key=%s",
        #     S3_SCREENING_BUCKET_NAME,
        #     quarantine_key,
        # )
        # s3.delete_object(
        #     Bucket=S3_SCREENING_BUCKET_NAME,
        #     Key=quarantine_key,
        # )
        # logger.info("Source delete successful")

        logger.info(
            "Override completed successfully: upload_id=%s quarantine_key=%s kb_key=%s",
            upload_id,
            quarantine_key,
            kb_key,
        )
        return _response(200, {
            "status": "overridden",
            "message": "Document override successful",
            "kbKey": kb_key,
            "overrideKey": override_key,
            "uploadId": upload_id,
            "quarantineKey": quarantine_key,
        })

    except Exception as e:
        logger.exception("Override failed with unhandled exception")
        return _response(500, {"error": f"Internal server error: {str(e)}"})
