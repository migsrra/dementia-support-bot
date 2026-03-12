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
PHI_CONFIDENCE_THRESHOLD = 0.8

MODEL_ID = "amazon.nova-micro-v1:0"

T_PROMPT = """
You are a document relevance classifier for a dementia knowledge base.

A document is relevant if it is substantially about:
- dementia
- Alzheimer's disease
- cognitive impairment
- memory loss
- caregiver support
- dementia care, diagnosis, management, behaviours, safety, long-term care

A document is not relevant if it is mainly about:
- unrelated medical specialties
- administration
- billing
- logistics
- employment
- generic forms
- unrelated research

Return JSON only in this exact format:
{{
  "is_relevant": true,
  "confidence": 0.0,
  "reason": "short explanation"
}}

Document text:
{document_text}
"""

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


def _normalize_phi_entity(entity: dict, chunk_index: int) -> dict:
    return {
        "text": entity.get("Text"),
        "type": entity.get("Type"),
        "category": entity.get("Category"),
        "score": entity.get("Score"),
        "beginOffset": entity.get("BeginOffset"),
        "endOffset": entity.get("EndOffset"),
        "chunkIndex": chunk_index,
        "traits": [
            {
                "name": trait.get("Name"),
                "score": trait.get("Score"),
            }
            for trait in entity.get("Traits", [])
        ],
        "attributes": [
            {
                "type": attribute.get("Type"),
                "category": attribute.get("Category"),
                "score": attribute.get("Score"),
                "text": attribute.get("Text"),
                "relationshipScore": attribute.get("RelationshipScore"),
                "relationshipType": attribute.get("RelationshipType"),
                "beginOffset": attribute.get("BeginOffset"),
                "endOffset": attribute.get("EndOffset"),
                "traits": [
                    {
                        "name": trait.get("Name"),
                        "score": trait.get("Score"),
                    }
                    for trait in attribute.get("Traits", [])
                ],
            }
            for attribute in entity.get("Attributes", [])
        ],
    }


def _get_phi_group_meta(entity_type: str | None) -> tuple[str, int]:
    if entity_type == "NAME":
        return "Names", 1
    if entity_type == "PHONE_OR_FAX":
        return "Phone Numbers", 2
    if entity_type == "EMAIL":
        return "Emails", 3
    if entity_type == "ADDRESS":
        return "Addresses", 4
    if entity_type == "DATE":
        return "Dates", 5
    if entity_type == "ID":
        return "IDs", 6
    if entity_type == "URL":
        return "URLs", 7
    if entity_type == "AGE":
        return "Ages", 8
    if entity_type == "PROFESSION":
        return "Professions", 9
    if not entity_type:
        return "Other", 99

    return " ".join(part.capitalize() for part in entity_type.lower().split("_")), 99


def _normalize_phi_value(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def build_phi_groups(entities: list[dict]) -> list[dict]:
    groups = {}

    for entity in entities:
        text = (entity.get("text") or "").strip()
        score = entity.get("score") or 0
        if not text or score < PHI_CONFIDENCE_THRESHOLD:
            continue

        group_key = entity.get("type") or entity.get("category") or "OTHER"
        label, order = _get_phi_group_meta(entity.get("type"))
        group = groups.get(group_key)

        if not group:
            group = {
                "key": group_key,
                "label": label,
                "order": order,
                "items": [],
            }
            groups[group_key] = group

        normalized_text = _normalize_phi_value(text)
        existing_item = next(
            (item for item in group["items"] if _normalize_phi_value(item.get("text")) == normalized_text),
            None,
        )

        candidate_item = {
            "text": text,
            "score": score,
        }

        if existing_item is None:
            group["items"].append(candidate_item)
        elif score > (existing_item.get("score") or 0):
            existing_item.update(candidate_item)

    return sorted(
        (
            {
                "key": group["key"],
                "label": group["label"],
                "items": sorted(group["items"], key=lambda item: item.get("score") or 0, reverse=True),
            }
            for group in groups.values()
        ),
        key=lambda group: (
            _get_phi_group_meta(group["key"])[1],
            group["label"],
        ),
    )


def has_phi(comprehend_medical_client, text: str) -> list[dict]:
    detected_entities = []

    for chunk_index, chunk in enumerate(chunk_text_for_phi(text)):
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
                detected_entities.append(_normalize_phi_entity(entity, chunk_index))

    return detected_entities

def move_object(s3_client, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str):
    s3_client.copy_object(
        Bucket=dst_bucket,
        CopySource={"Bucket": src_bucket, "Key": src_key},
        Key=dst_key,
        MetadataDirective="COPY",
    )
    s3_client.delete_object(Bucket=src_bucket, Key=src_key)

def check_relevance_with_bedrock(text: str, bedrock) -> dict:
    prompt = T_PROMPT.format(document_text=text[:12000])

    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}]
            }
        ],
        inferenceConfig={
            "temperature": 0,
            "maxTokens": 300
        }
    )

    output_text = response["output"]["message"]["content"][0]["text"]
    return json.loads(output_text)


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
        # Upload to Screening S3, perform PHI and Relevance check before acceptance into KB S3                        #
        ###############################################################################################################

        # Upload to S3
        session = _build_session()
        s3_client = session.client("s3")
        comprehend_medical = session.client("comprehendmedical")
        bedrock = boto3.client("bedrock-runtime", region_name = AWS_REGION)
        
        logger.info("Built boto3, comprehendmedical sessions, and bedrock and s3 clients")
        
        
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
            extracted_text = extract_pdf_text(BytesIO(file_bytes))
        except Exception as e:
            logger.error(f"Text extraction failed for file: {file_name}")
            logger.exception("Full traceback:")
            return _error_response(500, "Failed to extract text from PDF")

        if not extracted_text:
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
            phi_entities = has_phi(comprehend_medical, extracted_text)
            phi_groups = build_phi_groups(phi_entities)
        except Exception as e:
            logger.error(f"PHI screening failed for file: {file_name}")
            logger.exception("Full traceback:")
            return _error_response(500, "Failed to perform PHI screening")

        if phi_entities:
            try:
                logger.info(f"PHI detected in file: {file_name}. Moving to rejected/.")
                move_object(s3_client, screening_bucket_name, pending_key, screening_bucket_name, rejected_key)
                logger.info(f"File successfully moved to rejected/: {rejected_key}")

                return _success_response(200, {
                    "status": "rejected",
                    "reason": "possible_phi_detected",
                    "uploadId": upload_id,
                    "quarantineKey": rejected_key,
                    "phiGroups": phi_groups,
                })
            except Exception as e:
                logger.error(f"Failed to move PHI-flagged file to rejected/: {file_name}")
                logger.exception("Full traceback:")
                return _error_response(500, "Failed to reject document")
                
                
                
                
                
                
                
        relevance_result = check_relevance_with_bedrock(extracted_text, bedrock)

        if not relevance_result["is_relevant"]:
            return _success_response(200, {
                "status": "rejected",
                "reason": "not_relevant",
                "description": relevance_result["reason"],
                "confidence": relevance_result.get("confidence"),
                "uploadId": upload_id,
                "quarantineKey": rejected_key
            })
                        
                        
                
                
                
                
                
                
                
                

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
