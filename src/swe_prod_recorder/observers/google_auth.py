
# — Google Drive —
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
import os

def initialize_google_drive(client_secrets_path: str = None) -> GoogleDrive:
    """
    Initialize Google Drive authentication with optional custom client_secrets.json path.
    
    Parameters
    ----------
    client_secrets_path : str, optional
        Path to the client_secrets.json file. If None, uses default location.
        
    Returns
    -------
    GoogleDrive
        Authenticated Google Drive client
    """
    gauth = GoogleAuth()
    
    if client_secrets_path:
        # Expand user path and get absolute path
        client_secrets_path = os.path.abspath(os.path.expanduser(client_secrets_path))
        
        # Verify the file exists
        if not os.path.exists(client_secrets_path):
            raise FileNotFoundError(f"Client secrets file not found: {client_secrets_path}")
        
        # Copy the client_secrets.json to current directory temporarily
        import shutil
        temp_client_secrets = "client_secrets.json"
        
        try:
            shutil.copy2(client_secrets_path, temp_client_secrets)
            print(f"✅ Copied client_secrets.json to current directory")
            
            # Use default behavior (PyDrive will find client_secrets.json in current directory)
            gauth.LocalWebserverAuth()  # Opens browser for first-time authentication
            
        finally:
            # Clean up temporary file
            try:
                os.remove(temp_client_secrets)
                print(f"✅ Cleaned up temporary client_secrets.json")
            except OSError:
                pass  # File might already be deleted
    else:
        # Use default behavior (looks for client_secrets.json in current directory)
        gauth.LocalWebserverAuth()  # Opens browser for first-time authentication
    
    return GoogleDrive(gauth)

# Initialize with default behavior (looks for client_secrets.json in current directory)
base_dir = os.path.dirname(os.path.abspath(__file__))
client_secrets_path = 'client_secrets.json'
client_secrets_path = os.path.join(base_dir, client_secrets_path)
drive = initialize_google_drive(client_secrets_path)

def list_folders(drive: GoogleDrive):
    """List all folders in Google Drive to help find folder IDs"""
    folders = drive.ListFile({'q': "mimeType='application/vnd.google-apps.folder' and trashed=false"}).GetList()
    print("Available folders:")
    for folder in folders:
        print(f"Name: {folder['title']}, ID: {folder['id']}")
    return folders

def find_folder_by_name(folder_name: str, drive: GoogleDrive):
    """Find a folder by name and return its ID"""
    folders = drive.ListFile({'q': f"mimeType='application/vnd.google-apps.folder' and title='{folder_name}' and trashed=false"}).GetList()
    if folders:
        return folders[0]['id']
    return None

def upload_file(path: str, drive_dir: str, drive_instance: GoogleDrive):
    """Upload a file to Google Drive and delete the local file.
    
    Parameters
    ----------
    path : str
        Path to the file to upload
    drive_dir : str
        Google Drive folder ID to upload to
    drive_instance : GoogleDrive
        Google Drive client instance.
    """
    upload_file = drive_instance.CreateFile({
        'title': path.split('/')[-1],
        'parents': [{'id': drive_dir}]
    })
    upload_file.SetContentFile(path)
    upload_file.Upload()
    os.remove(path)

