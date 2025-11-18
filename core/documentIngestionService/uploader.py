# src/uploader.py
from file_utils import get_local_pdfs_from_folder, validate_file_path, check_duplicates
import os
from config import *


UPLOAD_SUCCESS = "SUCCESS"
UPLOAD_CANCELLED = "CANCELLED"
UPLOAD_FAILED = "FAILED"

FOLDER_EXISTS = 1
FOLDER_NEW = 0


class Uploader:
    def __init__(self, s3_client, bedrockAgent_client):
        # Store S3 and Bedrock Agent clients
        self.s3 = s3_client
        self.brAgent = bedrockAgent_client
        
    def get_valid_s3_folder(self):
        """
        Prompt the user for an S3 folder to upload into.
        - Applies default folder name if none entered.
        - Checks if the folder exists.
        - Asks user if they want to create it if missing.
        - Honors UPLOAD_ENABLED (debug mode).
        Returns:
            folder_name (str) if valid or created,
            None if cancelled.
        """
        while True:
            s3_folder = input(
                f"Enter S3 folder to upload your file to (default '{DEFAULT_S3_FOLDER}') or 'q' to cancel upload: "
            ).strip() or DEFAULT_S3_FOLDER

            if s3_folder == "q":
                return UPLOAD_CANCELLED, 0
            elif self.s3.folder_exists(s3_folder):  # Folder already exists -> use it
                return s3_folder, FOLDER_EXISTS
            else:   # Folder missing -> ask user what they want to name it
                choice = input(f"Folder '{s3_folder}' does not exist. Create? (y/n): ").strip().lower()
                if choice == "y":
                    return s3_folder, FOLDER_NEW
                else:
                    print("Folder not created. Please try again.")      # return to choosing folder to upload to
                    continue

        
    def upload_single_file(self):
        """
        Handles uploading exactly one file.
        - Prompts user for path.
        - Validates file exists & is PDF.
        - Gets valid S3 folder using helper.
        - Skips files that are duplicates.
        - Uploads file.
        Returns:
            UPLOAD_SUCCESS, UPLOAD_FAILED, or UPLOAD_CANCELLED.
        """
        while True:
            # Ask for file path
            file_path = input(f"\nEnter path of file to upload or 'q' to cancel upload: ").strip().strip("'\"")
            if file_path.lower() == "q":
                return UPLOAD_CANCELLED        # cancel, return to upload menu

            # Validate local file path, already prints out error message
            if not validate_file_path(file_path, ".pdf"):
                continue

            # Duplicate check
            duplicates = check_duplicates(self.s3.get_objects(), [file_path])
            if duplicates:
                print(f"ERROR: Duplicate found, '{duplicates[0][1]}'. Please try again.")
                continue
            
            # get name of folder they want to upload to
            s3_folder, folder_state = self.get_valid_s3_folder()
            if s3_folder == UPLOAD_CANCELLED:
                print("Upload cancelled. Please try again.")        # return to singe file upload
                continue

            confirm = input(f"Upload '{file_path}' to '{s3_folder}? (y/n): ").lower()     # verification message
            if confirm == "y":
                # create folder if needed
                if folder_state == FOLDER_NEW:
                    if UPLOAD_ENABLED:
                        self.s3.create_folder(s3_folder)
                    else:
                        print(f"[DEBUG] Upload disabled. Would create folder.")

                # upload file
                if self.s3.upload_file(file_path, s3_folder):
                    print(f"Uploaded '{file_path}' → s3://{self.s3.bucket_name}/{s3_folder}/")
                    return UPLOAD_SUCCESS
                else:
                    return UPLOAD_FAILED
            else:
                return UPLOAD_CANCELLED


    def upload_folder(self):
        """
        Handles uploading all PDF files inside a folder.
        - User selects local folder & recursive scanning.
        - Collects list of PDFs.
        - Validates S3 folder.
        - Detects duplicates and allows skipping or cancelling.
        - Confirms uploads before running.
        - Uploads files while tracking successes/failures.
        Returns:
            (status, total_count, success_count)
            where status is UPLOAD_SUCCESS or UPLOAD_FAILED.
        """
        while True:
            # Ask user for a folder path
            folder_path = input(f"\nEnter path of folder to upload or 'q' to cancel upload: ").strip().strip("'\"")
            if folder_path.lower() == "q":
                return UPLOAD_CANCELLED, 0, 0        # cancel, return to upload menu

            # Local folder must exist
            if not folder_path or not os.path.isdir(folder_path):
                print(f"ERROR: Invalid folder '{folder_path}'. Please try again.")     # returns to folder upload
                continue

            # Whether to scan subdirectories
            recursive = input("Scan subfolders for files to upload as well? (y/n): ").strip().lower() == "y"

            # Collect all PDFs
            files = get_local_pdfs_from_folder(folder_path, recursive)
            if not files:
                print("ERROR: No PDF files found. Please try again.")
                continue

            # Duplicate detection
            duplicates = check_duplicates(self.s3.get_objects(), files)
            if duplicates:
                print("Duplicate files found:")
                for local, s3_obj in duplicates:
                    print(f"  • {local} → {s3_obj}")

                if len(files) == len(duplicates):     # if no non-duplicate files, retry
                    print("ERROR: There are no non-duplicate files to upload. Please try again.")
                    continue

                # Option to skip duplicates
                if input("Skip duplicates? (y/n): ").strip().lower() == "y":
                    files = [f for f in files if f not in [d[0] for d in duplicates]]
                else:
                    print("Upload cancelled due to duplicates. Please try again")       # return to upload folder menu
                    continue

                # Confirm upload list after filtering
                print(f"\nPlease confirm upload of {len(files)} files")
                for f in files:
                    print(f"  • {f}")

                confirm = input("Continue? (y/n): ").strip().lower()
                if confirm != "y":
                    print("Upload cancelled. Please try again")       # return to upload folder menu
                    continue

            # Prompt for S3 folder
            s3_folder, folder_state = self.get_valid_s3_folder()
            if s3_folder == UPLOAD_CANCELLED:
                print("Upload cancelled. Please try again.")        # return to folder upload menu
                continue

            # Begin uploads
            if not duplicates:      # if no duplicates, still need to print all files that are going to be uploaded
                print(f"List of files in '{folder_path}':")
                for f in files:
                    print(f"  • {f}")

            confirm = input(f"Upload all {len(files)} files from '{folder_path}' to '{s3_folder}? (y/n): ").lower()
            if confirm == "y":
                print(f"Now uploading files from '{folder_path}' to s3://{self.s3.bucket_name}/{s3_folder}/")
                
                # create folder if needed
                if folder_state == FOLDER_NEW:
                    if UPLOAD_ENABLED:
                        self.s3.create_folder(s3_folder)
                    else:
                        print(f"[DEBUG] Upload disabled. Would create folder.")

                total_count = len(files)
                success_count = 0
                failed_files = []

                # Upload each file
                for f in files:
                    success = self.s3.upload_file(f, s3_folder)
                    if success:
                        print(f"  • Uploaded: {f}")
                        success_count += 1
                    else:
                        failed_files.append(f)

                print(f"Successful uploads: {success_count}")

                # Report failures if any
                if failed_files:
                    print("\nThe following files failed to upload:")
                    for f in failed_files:
                        print(f"  • {f}")
                    return UPLOAD_FAILED, total_count, success_count
                else:
                    return UPLOAD_SUCCESS, total_count, success_count
            else:
                return UPLOAD_CANCELLED, 0, 0            # return to upload menu if "n"


    def run(self):
        """
        Main interactive loop.
        Allows user to:
            [1] Upload a single file
            [2] Upload a folder
            [q] Quit
        Also triggers Bedrock KB ingestion jobs after successful uploads.
        """
        while True:
            print(f"\n****************UPLOAD MENU****************")
            self.s3.print_tree()
            
            action = input("Select [1] Single file, [2] Folder, [m] Return to Main Menu: ").strip()

            # === SINGLE FILE UPLOAD ===
            if action == "1":
                status = self.upload_single_file()

                if status == UPLOAD_SUCCESS:
                    print("Upload successful. Triggering KB sync...")
                    self.brAgent.begin_ingestion_job(description="Sync after successful single file upload")
                elif status == UPLOAD_FAILED:
                    print("Upload failed. Please try again.")
                    continue
                else:
                    print("Upload cancelled. Please try again.")
                    continue

            # === FOLDER UPLOAD ===
            elif action == "2":
                status, total_count, success_count = self.upload_folder()

                if status == UPLOAD_SUCCESS:
                    print("All folder uploads successful. Triggering KB sync...")
                    self.brAgent.begin_ingestion_job(description=f"Sync {total_count} files after successful folder upload")
                elif status == UPLOAD_FAILED:
                    print(f"{success_count}/{total_count} files uploaded successfully. Syncing {success_count} files")
                    self.brAgent.begin_ingestion_job(description=f"Sync {success_count}/{total_count} folder upload")
                else:
                    print("Upload cancelled. Please try again.")
                    continue

            # === RETURN TO MAIN MENU ===
            elif action == "m":
                print("Returning to main menu.")
                break

            else:
                print("ERROR: Invalid selection. Please enter 1, 2, or m.")
