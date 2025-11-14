"""Authentication modules for SWE Productivity Recorder."""

from .google_drive import (
    USE_GDRIVE,
    initialize_google_drive,
    _generate_client_secrets_from_env,
    find_folder_by_name,
    upload_file,
)

__all__ = [
    "USE_GDRIVE",
    "initialize_google_drive",
    "_generate_client_secrets_from_env",
    "find_folder_by_name",
    "upload_file",
]
