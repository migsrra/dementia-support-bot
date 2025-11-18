# src/ingestion.py
import boto3
import time
from s3_client import S3Client
from bedrock_client import BedrockAgentClient
from uploader import Uploader
from deleter import Deleter
from config import *

def main():
    """
    Main program loop:
    - Creates boto3 session
    - Creates S3 + BedrockAgent clients
    - Presents options:
        [1] Upload files
        [2] Delete files (TODO)
        [3] List files
    """
    session = boto3.Session(profile_name="default")
    s3_client = S3Client(session, DEFAULT_BUCKET)
    br_client = BedrockAgentClient(session)
    
    while True:
        print("\n*****************MAIN MENU*****************")
        action = input("Select [1] Upload files, [2] Delete files, [3] List files, [q] Quit: ").strip().lower()
        
        if action == "q":
            return

        elif action == "1":
            # Create uploader for this run cycle
            uploader = Uploader(s3_client, br_client)
            uploader.run()

        elif action == "2":
            deleter = Deleter(s3_client, br_client)
            deleter.run()

        elif action == "3":
            # Show bucket tree
            s3_client.print_tree()

        # Future TODOs:
        # - Add KB info print
        # - Add "sync now"
        # - Add ingestion job status print

    return

if __name__ == "__main__":
    main()
