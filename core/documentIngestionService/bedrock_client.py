# src/bedrock_client.py 
import boto3
import os
import logging
from botocore.exceptions import ClientError
from config import SYNC_ENABLED, BEDROCK_KB_ID, BEDROCK_DS_ID

class BedrockAgentClient:
    def __init__(self, session):
        """
        Wrapper around AWS Bedrock Agent API.
        Stores client + last API response for inspection.
        """
        self.bedrockAgent = session.client("bedrock-agent")
        self.APIresponse = None
    
    def begin_ingestion_job(self, description=None):
        """
        Start a Bedrock KB ingestion job.
        Honours SYNC_ENABLED (debug mode).
        Stores API response for later use.
        """

        # Future improvement:
        # - Check existing jobs / prevent parallel ingestion
        # - Queue new jobs / wait if needed

        if not SYNC_ENABLED:
            print(f"[DEBUG] SYNC disabled. Would call start_ingestion_job")
            return
        
        # Real call
        self.APIresponse = self.bedrockAgent.start_ingestion_job(
            knowledgeBaseId=BEDROCK_KB_ID, 
            dataSourceId=BEDROCK_DS_ID, 
            description=description
        )

    # TODO: Add KB print / job status methods
