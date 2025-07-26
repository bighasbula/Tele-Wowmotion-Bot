import os
import io
import requests
import pandas as pd
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
from supabase_utils import fetch_registrations, get_service_account_credentials
from apscheduler.schedulers.background import BackgroundScheduler
import time

# Load environment variables
load_dotenv()
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_API_KEY = os.getenv('SUPABASE_API_KEY')
GOOGLE_DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID')
EXCEL_FILE_NAME = 'WebinarRegistrations.xlsx'  # Fixed file name

# Easily editable sync interval (in minutes)
SYNC_INTERVAL_MINUTES = 30

# Google Drive API setup
def get_drive_service():
    creds = get_service_account_credentials()
    return build('drive', 'v3', credentials=creds)

def find_file_metadata(service, folder_id, file_name):
    query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get('files', [])
    if not files:
        raise FileNotFoundError(f"File '{file_name}' not found in folder '{folder_id}'")
    return files[0]  # returns dict with id, name, mimeType

def download_excel_file(service, file_id, mime_type, local_path):
    if mime_type == 'application/vnd.google-apps.spreadsheet':
        # Export Google Sheet as Excel
        request = service.files().export_media(fileId=file_id, mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    else:
        # Download native Excel file
        request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.close()

def update_excel_sheet(local_path, registrations):
    # Load Excel file
    with pd.ExcelWriter(local_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as writer:
        df = pd.DataFrame(registrations)
        df.to_excel(writer, sheet_name='Sheet1', index=False)
    # openpyxl preserves other sheets/styles

def upload_excel_file(service, file_id, local_path, mime_type):
    if mime_type == 'application/vnd.google-apps.spreadsheet':
        # Re-upload as Google Sheet (convert Excel to Google Sheet)
        media = MediaFileUpload(local_path, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        updated = service.files().update(
            fileId=file_id,
            media_body=media,
            body={'mimeType': 'application/vnd.google-apps.spreadsheet'}
        ).execute()
    else:
        # Replace Excel file
        media = MediaFileUpload(local_path, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        updated = service.files().update(fileId=file_id, media_body=media).execute()
    return updated

def main():
    try:
        # 1. Fetch registrations from Supabase
        registrations = fetch_registrations()
        # 2. Authenticate and find file in Drive
        service = get_drive_service()
        file_metadata = find_file_metadata(service, GOOGLE_DRIVE_FOLDER_ID, EXCEL_FILE_NAME)
        file_id = file_metadata['id']
        mime_type = file_metadata['mimeType']
        # 3. Download the file (export if Google Sheet)
        local_path = EXCEL_FILE_NAME
        download_excel_file(service, file_id, mime_type, local_path)
        # 4. Update Sheet1
        update_excel_sheet(local_path, registrations)
        # 5. Upload back to Drive (replace original, convert if needed)
        upload_excel_file(service, file_id, local_path, mime_type)
        print(f"Successfully updated '{EXCEL_FILE_NAME}' in Google Drive.")
    except Exception as e:
        print(f"Error: {e}")

# Set up scheduler to run sync every SYNC_INTERVAL_MINUTES
scheduler = BackgroundScheduler()
scheduler.add_job(main, 'interval', minutes=SYNC_INTERVAL_MINUTES)
scheduler.start()

if __name__ == "__main__":
    print(f"Starting sync service. Will sync every {SYNC_INTERVAL_MINUTES} minutes.")
    print("Press Ctrl+C to stop.")
    # Run initial sync
    main()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("Stopping sync service...")
        scheduler.shutdown()
    print("\n--- CREDENTIALS & IDS REQUIRED ---")
    print("1. SUPABASE_URL and SUPABASE_API_KEY in your .env")
    print("2. GOOGLE_DRIVE_FOLDER_ID in your .env (the folder containing the Excel file)")
    print("3. GOOGLE_SERVICE_ACCOUNT_FILE in your .env (path to your Google service account JSON)")
    print(f"4. The Excel file in Drive must be named exactly: {EXCEL_FILE_NAME}")
    print(f"5. Sync interval: {SYNC_INTERVAL_MINUTES} minutes (editable in SYNC_INTERVAL_MINUTES constant)") 