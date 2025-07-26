import os
import requests
import json
from dotenv import load_dotenv
from datetime import datetime
from google.oauth2 import service_account

load_dotenv()
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_API_KEY')

REGISTRATIONS_ENDPOINT = f"{SUPABASE_URL}/rest/v1/registrations"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

def get_service_account_credentials():
    """
    Get Google service account credentials from environment variable.
    Returns credentials object for Google APIs.
    """
    service_account_json = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not service_account_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not found in environment variables")
    
    try:
        # Parse the JSON string from environment variable
        service_account_info = json.loads(service_account_json)
        return service_account.Credentials.from_service_account_info(service_account_info)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in GOOGLE_SERVICE_ACCOUNT_JSON: {e}")

def save_registration_to_supabase(user_data, telegram_id, username=None):
    print("save_registration_to_supabase called with:", user_data, telegram_id)
    data = {
        "telegram_id": f"@{username}" if username else str(telegram_id),
        "full_name": user_data.get("full_name"),
        "email": user_data.get("email"),
        "phone": user_data.get("phone"),
        "webinar_date": user_data.get("date"),
    }
    print("Data to send:", data)
    print("Endpoint:", REGISTRATIONS_ENDPOINT)
    print("Headers:", HEADERS)
    try:
        response = requests.post(REGISTRATIONS_ENDPOINT, json=data, headers=HEADERS)
        print("Supabase response:", response.status_code, response.text)
        if response.status_code in (200, 201):
            print("Registration saved to Supabase.")
            return True
        else:
            print(f"Failed to save registration: {response.status_code} {response.text}")
            return False
    except Exception as e:
        print(f"Exception during Supabase registration: {e}")
        return False 

def format_date_to_iso(date_str):
    # Converts "26 July, 10:00" -> "2025-07-26T10:00:00"
    # Customize year as needed
    date_obj = datetime.strptime(date_str, "%d %B, %H:%M")
    date_obj = date_obj.replace(year=2025)
    return date_obj.isoformat()

def get_webinar_dates():
    """
    Fetch all webinar dates from the Supabase 'dates' table.
    Returns a list of dicts with keys: id, date, link.
    """
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_API_KEY = os.getenv('SUPABASE_API_KEY')
    if not SUPABASE_URL or not SUPABASE_API_KEY:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_API_KEY in environment variables.")
    endpoint = f"{SUPABASE_URL}/rest/v1/webinars"
    headers = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {SUPABASE_API_KEY}",
    }
    response = requests.get(endpoint, headers=headers)
    response.raise_for_status()
    return response.json()

def fetch_registrations():
    """
    Fetch all registrations from the Supabase 'registrations' table.
    Returns a list of dicts.
    """
    SUPABASE_URL = os.getenv('SUPABASE_URL')
    SUPABASE_API_KEY = os.getenv('SUPABASE_API_KEY')
    if not SUPABASE_URL or not SUPABASE_API_KEY:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_API_KEY in environment variables.")
    endpoint = f"{SUPABASE_URL}/rest/v1/registrations"
    headers = {
        "apikey": SUPABASE_API_KEY,
        "Authorization": f"Bearer {SUPABASE_API_KEY}",
    }
    response = requests.get(endpoint, headers=headers)
    response.raise_for_status()
    return response.json()