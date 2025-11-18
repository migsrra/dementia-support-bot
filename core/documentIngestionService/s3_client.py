# src/s3_client.py
import boto3
import os
import logging
from botocore.exceptions import ClientError
from config import UPLOAD_ENABLED, DELETE_ENABLED

class S3Client:
    def __init__(self, session, bucket_name):
        """
        Wrapper around S3 API for interacting with a specific bucket.
        Stores the underlying boto3 S3 client and bucket name.
        """
        self.s3 = session.client("s3")
        self.bucket_name = bucket_name

    def upload_file(self, file_path, s3_folder=None, object_name=None):
        """
        Upload a file to S3.
        - Handles optional folder prefix.
        - Honors UPLOAD_ENABLED for dry-run mode.
        Returns True on (real or simulated) success, False on error.
        """
        if object_name is None:
            object_name = os.path.basename(file_path)

        # Prepend folder prefix if provided
        if s3_folder:
            object_name = f"{s3_folder.strip('/')}/{object_name}"

        if UPLOAD_ENABLED:
            # Real upload
            try:
                self.s3.upload_file(file_path, self.bucket_name, object_name)
            except ClientError as e:
                logging.error(e)
                return False
            return True
        else:
            # Dry-run / debug behavior
            print(f"[DEBUG] Upload disabled. Would upload: {file_path} → s3://{self.bucket_name}/{object_name}")
            return True

    def folder_exists(self, folder):
        """
        Check if an S3 folder exists by asking for 1 key with that prefix.
        Returns True if any object exists in that folder.
        """
        response = self.s3.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=folder.strip("/") + "/",
            MaxKeys=1
        )
        return "Contents" in response

    def create_folder(self, folder):
        """
        Creates a "folder" in S3 by uploading a zero-byte placeholder object:
          folder_name/
        """
        folder_key = folder.strip("/") + "/"
        self.s3.put_object(Bucket=self.bucket_name, Key=folder_key)
        print(f"Folder '{folder}' created in S3.")

    def delete_file(self, key): 
        """
        Deletes a file from s3.
        """
        if DELETE_ENABLED:      # Real delete
            try:
                self.s3.delete_object(Bucket=self.bucket_name, Key=key)
            except ClientError as e:
                logging.error(e)
                return False
            return True
        else:
            # Dry-run / debug behavior
            print(f"[DEBUG] Delete disabled. Would delete: {key}")
            return True
        
    def get_objects(self, prefix=""):
        """
        Return a list of object keys under a prefix.
        Uses pagination to handle large folders gracefully.
        """
        objects = []
        paginator = self.s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
            if "Contents" in page:
                objects.extend([obj["Key"] for obj in page["Contents"]])

        return objects
    
        
    def print_tree(self, prefix="", left_pad="    * "):
        """
        Pretty-print the S3 bucket key structure as a tree.
        Folders get a trailing '/', even empty ones.
        """
        tree = {}

        # Build nested dict structure from object keys
        for key in self.get_objects(prefix):
            is_folder = key.endswith("/")
            parts = [p for p in key.split("/") if p]  # avoid empty segments
            d = tree

            for part in parts:
                d = d.setdefault(part, {})

            # Mark this node as a folder if key ends with '/'
            if is_folder:
                d["_is_folder"] = True

        # Recursive pretty-printer
        def _print(d, prefix_str=""):
            items = [(k, v) for k, v in d.items() if k != "_is_folder"]
            total = len(items)

            for i, (k, v) in enumerate(items):
                connector = "└─" if i == total - 1 else "├─"

                is_folder = v or v.get("_is_folder", False)

                label = f"{k}/" if is_folder else k

                print(f"{left_pad}{prefix_str}{connector} {label}")

                if v:
                    extension = "   " if i == total - 1 else "│  "
                    _print(v, prefix_str + extension)

        print("\n    ***************S3 File Structure*************")
        print(f"{left_pad}{self.bucket_name}/")
        _print(tree)
        print("    *********************************************\n")
