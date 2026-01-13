from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
from typing import List, Dict, Any

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# 
# Load config from .env in the same folder
# Keeps keys and IDs outside of the code
# 
BASE_DIR = os.path.dirname(__file__)
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

app = FastAPI(title="Bedrock Knowledge Base RAG API")

#
# Bedrock configuration
#   AWS_REGION       – region of the KB and model
#   BEDROCK_KB_ID    – Knowledge Base ID
#   BEDROCK_MODEL_ARN – foundation model ARN used for RAG
#
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_KB_ID = os.getenv("BEDROCK_KB_ID")
BEDROCK_MODEL_ARN = os.getenv("BEDROCK_MODEL_ARN")

# Create Bedrock client only if KB id is present (error debug)
bedrock_agent = None
if BEDROCK_KB_ID:
    bedrock_agent = boto3.client(
        "bedrock-agent-runtime",
        region_name=AWS_REGION,
    )


# 
# rag_response(concern)
#
# Input:
#   concern (string) – natural language question from the user
#
# Output dict:
#   {
#       "answer":  str (may be empty on error)
#       "sources": list of retrieved doc metadata
#       "backend": "bedrock" | "bedrock-error" | "bedrock-empty"
#                  | "bedrock-missing",
#       "error":   str (present only when something went wrong)
#   }
#
# This is the single place where we call Bedrock
# retrieve_and_generate() on the Knowledge Base
# 
def rag_response(concern: str) -> Dict[str, Any]:

    concern = (concern or "").strip()
    # Below is just all kinds of error outputs

    # Reject empty questions early
    if not concern:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": "Empty question. Please type something.",
        }

    # .env is missing or client not initialized
    if bedrock_agent is None:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-missing",
            "error": (
                "Bedrock client is not configured. "
                "Check AWS_REGION and BEDROCK_KB_ID in .env."
            ),
        }

    # Model ARN is required for this KB configuration (debug added)
    if not BEDROCK_MODEL_ARN:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": (
                "BEDROCK_MODEL_ARN is not set in .env. "
                "Set it to the Nova Micro model ARN for this KB."
            ),
        }

    try:
        # Core Bedrock RAG call
        response = bedrock_agent.retrieve_and_generate(
            input={"text": concern},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": BEDROCK_KB_ID,
                    "modelArn": BEDROCK_MODEL_ARN,
                },
            },
        )

        # Extract generated answer text
        answer_text = (
            response.get("output", {})
            .get("text", "")
            .strip()
        )

        # Collect source info for later display or debugging
        sources: List[Dict[str, Any]] = []
        for citation in response.get("citations", []):
            for ref in citation.get("retrievedReferences", []):
                sources.append(
                    {
                        "location": ref.get("location", {}),
                        "metadata": ref.get("metadata", {}),
                    }
                )

        # Bedrock returned successfully but without text
        if not answer_text:
            return {
                "answer": "",
                "sources": sources,
                "backend": "bedrock-empty",
                "error": "Bedrock returned an empty answer.",
            }

        # Normal successful path!
        return {
            "answer": answer_text,
            "sources": sources,
            "backend": "bedrock",
        }

    # Bedrock-level errors (wrong ARN, wrong ID, etc)
    except ClientError as e:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": f"Bedrock ClientError: {e}",
        }

    # Any other unexpected Python error
    except Exception as e:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": f"Unexpected error: {e}",
        }


# 
# GET /query
#
# Query parameter:
#   concern – question string
#
# Response:
#   JSON of the same form as rag_response()
#   Returns 500 if Bedrock fails or answer is empty
# 
@app.get("/query")
def get_concern(concern: str):
    result = rag_response(concern)

    if not result["answer"]:
        return JSONResponse(status_code=500, content=result)

    return result


# 
# POST /query
#
# JSON body:
#   { "concern": "..." }
#
# Response:
#   Same as GET endpoint. code 400 if "concern" is missing.
# 
@app.post("/query")
async def post_concern(request: Request):
    data = await request.json()
    concern = data.get("concern")

    if not concern:
        return JSONResponse(
            status_code=400,
            content={"error": "Missing 'concern' in request body."},
        )

    result = rag_response(concern)

    if not result["answer"]:
        return JSONResponse(status_code=500, content=result)

    return result


# 
# run_cli()
#
# Simple local loop to test RAG from the terminal:
#   - reads a line from stdin
#   - calls rag_response()
#   - prints answer or error
#
# This is the "local script" version of the RAG Response Job
# Knowing that the Bedrock knowledge database is successfully responding
# Rough of API to user interface
# 
def run_cli():
    print("Bedrock KB RAG – CLI mode")
    print("Type a concern and press Enter. Type 'exit' to quit.\n")

    while True:
        try:
            concern = input("You: ").strip()
        except KeyboardInterrupt:
            print("\nExiting.")
            break

        if concern.lower() in {"exit", "quit"}:
            break

        result = rag_response(concern)
        print("\nBot:")

        if result["answer"]:
            print(result["answer"])
        else:
            print(result.get("error", "No answer returned."))

        print(f"(backend = {result['backend']})\n")


if __name__ == "__main__":
    run_cli()
