# src/deleter.py
from file_utils import get_local_pdfs_from_folder, validate_file_path, check_duplicates
import os
from config import *


DELETE_SUCCESS = "SUCCESS"
DELETE_CANCELLED = "CANCELLED"
DELETE_FAILED = "FAILED"


class Deleter:
    def __init__(self, s3_client, bedrockAgent_client):
        self.s3 = s3_client
        self.brAgent = bedrockAgent_client
    
    def find_file_anywhere(self, filename):
        """
        Search all S3 keys for a matching filename (shouldn't have duplicates).
        Returns the full key if found, else None.
        """
        objects = self.s3.get_objects()  # returns list of S3 keys

        for key in objects:
            if key.endswith(f"/{filename}") or key == filename:
                parts = key.split("/")
                parent_folder = "/".join(parts[:-1])  # empty string if at root
                return parent_folder, key       # returns parent and file

        return None, None
    
    def find_folder_anywhere(self, foldername):
        """
        Search all S3 keys for files in that folder.
        Returns list of all files under that folder name.
        """
        objects = self.s3.get_objects()  # returns list of S3 keys
        
        matching_files = []

        for key in objects:
            # split key into parts
            parts = key.split("/")[:-1]  # all folder parts, exclude the filename
            if foldername in parts:
                matching_files.append(key)

        return matching_files       # list of all (if any) files in that folder

    def delete_single_file(self):
        while True:
            file_name = input(f"\nEnter name_of_file.pdf to delete (full path not needed) or 'q' to cancel delete: ").strip().strip("'\"")
            if file_name.lower() == "q":
                return DELETE_CANCELLED        # cancel, return to delete menu
            
            if not file_name.endswith(".pdf"):
                file_name += ".pdf"     # if does not end with .pdf, then add .pdf manually

            # search for the file in all folders automatically
            parent, key = self.find_file_anywhere(file_name)

            if not key:     # if not found, stay in delete single file and let them try again
                print(f"ERROR: '{file_name}' not found anywhere in S3. Please try again.")
                continue

            # delete
            confirm = input(f"Delete '{key}' (if folder is empty after this deletion, the folder will be deleted too)? (y/n): ").lower()     # verification message
            if confirm == "y":
                if self.s3.delete_file(key):        # in s3, deletes the file
                    
                    # check if parent folder is now empty
                    if parent:  # skip root
                        remaining_objects = [
                            obj for obj in self.s3.get_objects(prefix=parent + "/")
                            if obj != parent + "/"
                        ]
                        
                        if not remaining_objects:       # empty
                            print(f"Parent folder '{parent}' is empty. Deleting folder...")
                            # delete the folder placeholder
                            self.s3.delete_file(parent + "/")
                    return DELETE_SUCCESS
                else:
                    return DELETE_FAILED
            else:
                return DELETE_CANCELLED     # return to delete menu if "n"

    def delete_folder(self):
        while True:
            folder_name = input(f"\nEnter folder name to delete (full path not needed) or 'q' to cancel delete: ").strip().strip("'\"")
            if folder_name == "q":
                return DELETE_CANCELLED, 0, 0        # cancel, return to delete menu
            
            # search for folder everywhere
            folder_files = self.find_folder_anywhere(folder_name)

            # if not found, stay in delete folder and let them try again
            if not folder_files:
                print(f"ERROR: '{folder_name}' not found anywhere in S3. Please try again.")
                continue

            # delete all files in the folder and the folder itself
            print(f"List of files in '{folder_name}':")
            for file in folder_files:
                if file.endswith("/"):  # skip the first entry which is folder placeholder object, not actual file
                    continue

                print(f"  • {file}")

            confirm = input(f"Delete all {len(folder_files) - 1} files in '{folder_name}'? (y/n): ").lower()     # verification message
            if confirm == "y":      # loop through list and use the delete_file implemented earlier, add count for successful deletions, delete folder
                print(f"Now deleting files in {folder_name}.")
                total_count = len(folder_files)
                success_count = 0
                failed_files = []

                for file in folder_files:
                    if self.s3.delete_file(file):       
                        if file.endswith("/"):  # skip the first entry which is folder placeholder object, not actual file
                            continue

                        print(f"  • Deleted: {file}")
                        success_count += 1       # track number of successful deletions
                    else:
                        failed_files.append(file)
                
                print(f"Successful deletes: {success_count}")

                # Report failures if any
                if failed_files:
                    print("\nThe following files failed to delete:")
                    for f in failed_files:
                        print(f"  • {f}")
                    return DELETE_FAILED, total_count, success_count
                else:
                    return DELETE_SUCCESS, total_count, success_count
            else:
                return DELETE_CANCELLED, 0, 0     # return to delete menu if "n"
            

    def run(self):
        while True:
            print(f"\n****************DELETE MENU****************")
            self.s3.print_tree()
            
            action = input("Select [1] Single file, [2] Folder, [m] Return to Main Menu: ").strip()

            # === SINGLE FILE DELETE ===
            if action == "1":
                status = self.delete_single_file()

                if status == DELETE_SUCCESS:
                    print("Delete successful. Triggering KB sync...")
                    self.brAgent.begin_ingestion_job(description="Sync after successful single file delete")
                elif status == DELETE_FAILED:
                    print("Delete failed. Please try again.")
                    continue
                else:
                    print("Delete cancelled. Please try again.")
                    continue

            # === FOLDER UPLOAD ===
            elif action == "2":
                status, total_count, success_count = self.delete_folder()
                if status == DELETE_SUCCESS:
                    print("All folder deletes successful. Triggering KB sync...")
                    self.brAgent.begin_ingestion_job(description=f"Sync {total_count} files after successful folder delete")
                elif status == DELETE_FAILED:
                    print(f"{success_count}/{total_count} files deleted successfully. The folder still exists. Syncing {success_count} files")
                    self.brAgent.begin_ingestion_job(description=f"Sync {success_count}/{total_count} folder upload")
                else:
                    print("Delete cancelled. Please try again.")
                    continue

            # === RETURN TO MAIN MENU ===
            elif action == "m":
                print("Returning to main menu.")
                break

            else:
                print("ERROR: Invalid selection. Please enter 1, 2, or m.")