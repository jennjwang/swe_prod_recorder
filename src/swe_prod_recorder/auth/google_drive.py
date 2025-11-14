from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

try:
    from pydrive.auth import GoogleAuth
    from pydrive.drive import GoogleDrive
except ImportError:  # pragma: no cover - optional dependency
    USE_GDRIVE = False
    GoogleAuth = None  # type: ignore[assignment]
    GoogleDrive = None  # type: ignore[assignment]
else:
    USE_GDRIVE = True

if TYPE_CHECKING:  # pragma: no cover
    from pydrive.drive import GoogleDrive as _GoogleDrive


def _require_pydrive() -> None:
    if not USE_GDRIVE:
        raise RuntimeError(
            "PyDrive is not installed; Google Drive uploads are unavailable."
        )


def _generate_client_secrets_from_env() -> bool:
    """
    Generate config/.google_auth/client_secrets.json from config/.env file if it doesn't exist.

    Returns True if generated successfully or if file already exists.
    Returns False if .env is missing or incomplete.
    """
    client_secrets_path = "config/.google_auth/client_secrets.json"

    # If client_secrets.json already exists, no need to generate
    if os.path.exists(client_secrets_path):
        return True

    # Check if .env file exists in config/
    env_file = "config/.env"
    if not os.path.exists(env_file):
        return False

    # Try to load environment variables from .env
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
    except ImportError:
        # python-dotenv not installed, try reading manually
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()

    # Read required environment variables
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    project_id = os.getenv("GOOGLE_PROJECT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")

    # Check if all required variables are present
    if not all([client_id, project_id, client_secret]):
        return False

    # Create the client_secrets.json structure
    client_secrets = {
        "installed": {
            "client_id": client_id,
            "project_id": project_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }

    # Create output directory if it doesn't exist
    os.makedirs("config/.google_auth", exist_ok=True)

    # Write the JSON file
    with open(client_secrets_path, "w") as f:
        json.dump(client_secrets, f, indent=2)

    return True


def initialize_google_drive(client_secrets_path: str | None = None) -> "GoogleDrive":
    """
    Initialize Google Drive authentication with optional custom client_secrets.json path.

    Credentials are cached in 'config/.google_auth/credentials.json' to avoid repeated authentication.

    Parameters
    ----------
    client_secrets_path : str, optional
        Path to the client_secrets.json file. If None, uses default location.

    Returns
    -------
    GoogleDrive
        Authenticated Google Drive client
    """
    _require_pydrive()

    # Set up credential caching directory
    credentials_dir = "config/.google_auth"
    os.makedirs(credentials_dir, exist_ok=True)
    credentials_file = os.path.join(credentials_dir, "credentials.json")

    gauth = GoogleAuth()

    # Configure settings for credential caching
    gauth.settings['save_credentials'] = True
    gauth.settings['save_credentials_backend'] = 'file'
    gauth.settings['save_credentials_file'] = credentials_file
    gauth.settings['get_refresh_token'] = True

    if client_secrets_path:
        # Expand user path and get absolute path
        client_secrets_path = os.path.abspath(os.path.expanduser(client_secrets_path))

        # Verify the file exists
        if not os.path.exists(client_secrets_path):
            raise FileNotFoundError(
                f"Client secrets file not found: {client_secrets_path}"
            )

        # Set the client secrets file path
        gauth.settings['client_config_file'] = client_secrets_path
    else:
        # Use default location (config/.google_auth/client_secrets.json)
        gauth.settings['client_config_file'] = 'config/.google_auth/client_secrets.json'

    # Try to load cached credentials
    gauth.LoadCredentialsFile(credentials_file)

    if gauth.credentials is None:
        # No cached credentials, need to authenticate
        gauth.LocalWebserverAuth()  # Opens browser for first-time authentication
    elif gauth.access_token_expired:
        # Credentials exist but expired, refresh them
        gauth.Refresh()
    else:
        # Credentials are valid, authorize
        gauth.Authorize()

    # Save credentials for next time
    gauth.SaveCredentialsFile(credentials_file)

    return GoogleDrive(gauth)


def find_folder_by_name(folder_name: str, drive: "_GoogleDrive"):
    """Find a folder by name and return its ID"""
    _require_pydrive()
    folders = drive.ListFile(
        {
            "q": f"mimeType='application/vnd.google-apps.folder' and title='{folder_name}' and trashed=false"
        }
    ).GetList()
    if folders:
        return folders[0]["id"]
    return None


def upload_file(
    path: str,
    drive_dir: str,
    drive_instance: "_GoogleDrive",
    *,
    delete_local: bool = True,
):
    """Upload a file to Google Drive and optionally delete the local file.

    Parameters
    ----------
    path : str
        Path to the file to upload
    drive_dir : str
        Google Drive folder ID to upload to
    drive_instance : GoogleDrive
        Google Drive client instance.
    delete_local : bool, keyword-only
        Whether to delete the local file after upload (default: True).
    """
    _require_pydrive()
    upload_file = drive_instance.CreateFile(
        {"title": path.split("/")[-1], "parents": [{"id": drive_dir}]}
    )
    upload_file.SetContentFile(path)
    upload_file.Upload()
    if delete_local:
        os.remove(path)
