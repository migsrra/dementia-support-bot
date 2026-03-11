import base64
import json
import logging
import os
import re
import uuid

import boto3

from python_multipart import parse_form
from io import BytesIO
from pypdf import PdfReader 

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration from environment
AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_S3_FOLDER = os.getenv("DEFAULT_S3_FOLDER", "")
S3_SCREENING_BUCKET_NAME = os.getenv("S3_SCREENING_BUCKET_NAME")
S3_KB_BUCKET_NAME = os.getenv("S3_KB_BUCKET_NAME")

MAX_PHI_TEXT_BYTES = 18000

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
    
    
def sanitize_filename(name: str) -> str:
    name = name.strip()
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def extract_pdf_text(file_obj) -> str:
    file_obj.seek(0)
    reader = PdfReader(file_obj)
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)
    return "\n".join(pages).strip()


def chunk_text_for_phi(text: str, max_bytes: int = MAX_PHI_TEXT_BYTES) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    chunks = []
    current = []

    for sentence in re.split(r'(?<=[.!?])\s+', text):
        candidate = " ".join(current + [sentence]).strip()
        if len(candidate.encode("utf-8")) <= max_bytes:
            current.append(sentence)
        else:
            if current:
                chunks.append(" ".join(current))
            current = [sentence]

    if current:
        chunks.append(" ".join(current))

    return chunks


def has_phi(comprehend_medical_client, text: str) -> bool:
    for chunk in chunk_text_for_phi(text):
        response = comprehend_medical_client.detect_phi(Text=chunk)
        entities = response.get("Entities", [])
        if entities:
            for entity in entities:
                logger.info(
                    "Detected PHI entity: text=%r type=%s score=%s category=%s",
                    entity.get("Text"),
                    entity.get("Type"),
                    entity.get("Score"),
                    entity.get("Category"),
                )
            return True

    return False


def is_relevant_stub(text: str) -> tuple[bool, str]:
    """
    Replace with Bedrock later.
    Very cheap temporary fallback:
    """
    lowered = text.lower()
    keywords = [
        "dementia", "alzheimer", "alzheimer's", "memory loss",
        "cognitive decline", "caregiver", "neurodegenerative"
    ]
    if any(k in lowered for k in keywords):
        return True, "keyword match"
    return False, "no dementia-related terms found"


def move_object(s3_client, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str):
    s3_client.copy_object(
        Bucket=dst_bucket,
        CopySource={"Bucket": src_bucket, "Key": src_key},
        Key=dst_key,
        MetadataDirective="COPY",
    )
    s3_client.delete_object(Bucket=src_bucket, Key=src_key)

def lambda_handler(event, context):
    try:
        
        screening_bucket_name = S3_SCREENING_BUCKET_NAME
        kb_bucket_name = S3_KB_BUCKET_NAME
        
        if not screening_bucket_name:
            return _error_response(500, "S3_SCREENING_BUCKET_NAME is not configured")

        if not kb_bucket_name:
            return _error_response(500, "S3_KB_BUCKET_NAME is not configured")

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

        if not content_type:
            return _error_response(400, "Missing Content-Type header")

        parsed_files = {}
        
        def on_file(file) -> None:
            file.file_object.seek(0)
            file_bytes = file.file_object.read()

            parsed_files[file.field_name] = {
                "bytes": file_bytes,
                "filename": file.file_name.decode("utf-8") if isinstance(file.file_name, bytes) else file.file_name,
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
            if entry["filename"] and entry["filename"].lower().endswith(".pdf")
        ]

        if len(pdfs) != 1:
            return _error_response(400, "Expected only one PDF file")
        
        pdf_entry = pdfs[0]
        file_bytes = pdf_entry["bytes"]
        filename = pdf_entry["filename"]

        if not file_bytes:
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

        # Upload to S3
        session = _build_session()
        s3_client = session.client("s3")
        comprehend_medical = session.client("comprehendmedical")
        logger.info("Built boto3, comprehendmedical sessions, and s3 client")
        
        
        # Create unique ID to prevent key conflict in s3
        upload_id = str(uuid.uuid4())
        safe_name = sanitize_filename(file_name)
        pending_key = f"pending/{upload_id}-{safe_name}"
        rejected_key = f"rejected/{upload_id}-{safe_name}"
        accepted_key = f"{safe_name}"


        # Upload raw file to screening bucket
        try:
            logger.info(f"Attempting to upload file: {file_name} to screening s3")
            s3_client.upload_fileobj(
                Fileobj=BytesIO(file_bytes),
                Bucket=screening_bucket_name,
                Key=pending_key,
                ExtraArgs={"ContentType": "application/pdf"},
            )
            logger.info(f"Uploaded {file_name} to s3://{screening_bucket_name}/{pending_key}")
        except Exception as e:
            logger.error(f"S3 upload failed: {e}")
            logger.exception("Full traceback:")
            return _error_response(500, "Failed to upload PDF to S3")

        # Extract text
        try:
            text = extract_pdf_text(BytesIO(file_bytes))
        except Exception as e:
            logger.error(f"Text extraction failed for file: {file_name}")
            logger.exception("Full traceback:")
            return _error_response(500, "Failed to extract text from PDF")

        if not text:
            try:
                logger.info(f"No extractable text found for file: {file_name}. Moving to rejected/.")
                move_object(s3_client, screening_bucket_name, pending_key, screening_bucket_name, rejected_key)
                logger.info(f"File successfully moved to rejected/: {rejected_key}")

                return _success_response(200, {
                    "status": "rejected",
                    "reason": "unable_to_extract_text",
                    "uploadId": upload_id,
                    "quarantineKey": rejected_key,
                })
            except Exception as e:
                logger.error(f"Failed to move file to rejected/: {file_name}")
                logger.exception("Full traceback:")
                return _error_response(500, "Failed to reject document")

        # PHI screen
        try:
            has_phi_result = has_phi(comprehend_medical, text)
        except Exception as e:
            logger.error(f"PHI screening failed for file: {file_name}")
            logger.exception("Full traceback:")
            return _error_response(500, "Failed to perform PHI screening")

        if has_phi_result:
            try:
                logger.info(f"PHI detected in file: {file_name}. Moving to rejected/.")
                move_object(s3_client, screening_bucket_name, pending_key, screening_bucket_name, rejected_key)
                logger.info(f"File successfully moved to rejected/: {rejected_key}")

                return _success_response(200, {
                    "status": "rejected",
                    "reason": "possible_phi_detected",
                    "uploadId": upload_id,
                    "quarantineKey": rejected_key,
                })
            except Exception as e:
                logger.error(f"Failed to move PHI-flagged file to rejected/: {file_name}")
                logger.exception("Full traceback:")
                return _error_response(500, "Failed to reject document")
                

        # Accepted -> copy into KB bucket, then delete from screening bucket
        try:
            logger.info(
                f"Document accepted for file: {file_name}. "
                f"Copying from s3://{screening_bucket_name}/{pending_key} "
                f"to s3://{kb_bucket_name}/{accepted_key}"
            )

            s3_client.copy_object(
                Bucket=kb_bucket_name,
                CopySource={"Bucket": screening_bucket_name, "Key": pending_key},
                Key=accepted_key,
                MetadataDirective="COPY",
            )

            logger.info(f"File successfully copied to KB bucket: {accepted_key}")

        except Exception as e:
            logger.error(f"Failed to copy accepted file to KB bucket: {file_name}")
            logger.exception("Full traceback:")
            return _error_response(500, "Failed to move accepted document to KB bucket")

        try:
            logger.info(
                f"Deleting pending file from screening bucket: "
                f"s3://{screening_bucket_name}/{pending_key}"
            )
            s3_client.delete_object(Bucket=screening_bucket_name, Key=pending_key)
            logger.info(f"Pending file successfully deleted: {pending_key}")

        except Exception as e:
            logger.error(f"Accepted file copied, but failed to delete pending file: {file_name}")
            logger.exception("Full traceback:")
            return _error_response(
                500,
                "Document was copied to KB bucket but failed to delete from screening bucket"
            )
            
            
        return _success_response(200, {
            "status": "accepted",
            "message": "Document accepted and copied to KB bucket",
            "kbKey": accepted_key,
        })
        
    except Exception as exc:
        logger.exception("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")

