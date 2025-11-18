# src/config.py

import os
from dotenv import load_dotenv

load_dotenv()

# S3 settings
DEFAULT_BUCKET = os.getenv("DEFAULT_BUCKET")
DEFAULT_S3_FOLDER = os.getenv("DEFAULT_S3_FOLDER")

# Bedrock Knowledge Base
BEDROCK_KB_ID = os.getenv("BEDROCK_KB_ID")
BEDROCK_DS_ID = os.getenv("BEDROCK_DS_ID")

# AWS session settings
AWS_PROFILE = os.getenv("AWS_PROFILE", "default")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Toggle uploads for debugging
UPLOAD_ENABLED = False
SYNC_ENABLED = False
DELETE_ENABLED = False

