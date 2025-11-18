# src/file_utils.py
import os

def get_local_pdfs_from_folder(folder_path, recursive=False):
    """
    Return list of full paths for .pdf files inside a folder.
    If recursive=True, search subdirectories via os.walk.
    """
    file_paths = []
    if recursive:
        for root, _, files in os.walk(folder_path):
            for f in files:
                if f.lower().endswith(".pdf"):
                    file_paths.append(os.path.join(root, f))
    else:
        for f in os.listdir(folder_path):
            full_path = os.path.join(folder_path, f)
            if os.path.isfile(full_path) and f.lower().endswith(".pdf"):
                file_paths.append(full_path)

    return file_paths


def validate_file_path(path, extension=None):
    """
    Verify that:
    - Path exists
    - Path points to a file
    - Optional extension matches (case-insensitive)
    Returns True if valid, False if not.
    """
    if not os.path.exists(path):
        print(f"Path does not exist: {path}. Please try again.")
        return False

    if not os.path.isfile(path):
        print(f"Path is not a file: {path}. Please try again.")
        return False

    if extension and not path.lower().endswith(extension.lower()):
        print(f"File does not have the required extension '{extension}': {path}. Please try again.")
        return False

    return True


def check_duplicates(s3_objects, local_files):
    """
    Check for duplicate filenames between local files and S3 objects.
    Returns list of (local_path, s3_key) duplicates.
    """
    duplicates = []


    # Global duplicate search across entire bucket
    for f in local_files:
        for obj in s3_objects:
            if os.path.basename(f) == os.path.basename(obj):
                duplicates.append((f, obj))

    return duplicates
