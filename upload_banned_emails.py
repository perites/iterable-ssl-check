#!/usr/bin/env python3
"""
Script to read emails from Google Sheets and attempt to upload them to Iterable 
across multiple domains/API keys.
These emails are expected to be banned and should fail during upload.
Only processes emails with dates prior to today (not including today).
"""

import json
import os
import logging
import argparse
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import requests


# Load environment variables from .env file
load_dotenv()

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Check banned emails in Iterable across multiple domains')
parser.add_argument(
    '--ssl-check-channel', '-scc',
    action='store_true',
    help='Send results to SSL check channel (uses SCC_WORKFLOW_WEBHOOK_URL)'
)
args = parser.parse_args()

# Setup logging - write to both console and file
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_filename = os.path.join(log_dir, f"iterable_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Create formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Setup logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Console handler (INFO level and above)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

# File handler (DEBUG level and above - everything)
file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

logger.info(f"Logging initialized. Log file: {log_filename}")


# Configuration - Load from environment variables
GOOGLE_CREDENTIALS_JSON = {
    "token": os.getenv("GOOGLE_TOKEN"),
    "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN"),
    "token_uri": os.getenv("GOOGLE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
    "client_id": os.getenv("GOOGLE_CLIENT_ID"),
    "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
    "scopes": [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.file"  # For uploading CSV files
    ],
    "universe_domain": "googleapis.com",
    "account": os.getenv("GOOGLE_ACCOUNT", ""),
    "expiry": os.getenv("GOOGLE_TOKEN_EXPIRY", "2025-12-02T17:44:45.532101Z")
}

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "Main")
SHEET_RANGE = os.getenv("SHEET_RANGE", "A:D")

# LOAD ITERABLE KEYS
# Expected format in .env: ITERABLE_API_KEYS_JSON='{"Domain1": "Key1", "Domain2": "Key2"}'
ITERABLE_KEYS_JSON_STR = os.getenv("ITERABLE_API_KEYS_JSON", "{}")

try:
    ITERABLE_API_KEYS = json.loads(ITERABLE_KEYS_JSON_STR)
except json.JSONDecodeError:
    logger.error("Failed to parse ITERABLE_API_KEYS_JSON. Ensure it is valid JSON.")
    ITERABLE_API_KEYS = {}

ITERABLE_BULK_API_URL = os.getenv("ITERABLE_BULK_API_URL", "https://api.iterable.com/api/users/bulkUpdate")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))

# Slack Configuration - Select webhook based on command-line argument
if args.ssl_check_channel:
    SLACK_WEBHOOK_URL = os.getenv("SCC_WORKFLOW_WEBHOOK_URL", "")
    logger.info("Using SSL check channel webhook (SCC_WORKFLOW_WEBHOOK_URL)")
else:
    SLACK_WEBHOOK_URL = os.getenv("WORKFLOW_WEBHOOK_URL", "")
    logger.info("Using default webhook (WORKFLOW_WEBHOOK_URL)")

SLACK_USER_ID = os.getenv("SLACK_USER_ID", "")

# Validate required environment variables
required_vars = {
    "GOOGLE_TOKEN": GOOGLE_CREDENTIALS_JSON["token"],
    "GOOGLE_REFRESH_TOKEN": GOOGLE_CREDENTIALS_JSON["refresh_token"],
    "GOOGLE_CLIENT_ID": GOOGLE_CREDENTIALS_JSON["client_id"],
    "GOOGLE_CLIENT_SECRET": GOOGLE_CREDENTIALS_JSON["client_secret"],
    "SPREADSHEET_ID": SPREADSHEET_ID,
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
    logger.error(error_msg)
    raise ValueError(error_msg)

if not ITERABLE_API_KEYS:
    error_msg = "ITERABLE_API_KEYS_JSON is missing or empty."
    logger.error(error_msg)
    raise ValueError(error_msg)


def send_message_to_slack(slack_user_id, message):
    """
    Send a message to Slack using a workflow webhook.
    """
    workflow_webhook_url = SLACK_WEBHOOK_URL
    if not workflow_webhook_url:
        logger.warning("WORKFLOW_WEBHOOK_URL not set — Slack messages will be skipped.")
        return False

    payload = {
        "message": message,
        "slack_user_id": slack_user_id
    }

    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(
            workflow_webhook_url,
            headers=headers,
            data=json.dumps(payload),
            timeout=10
        )

        if not response.ok:
            logger.warning(
                f"Slack webhook returned {response.status_code}: {response.text}. "
                f"Slack will retry automatically."
            )
        else:
            logger.debug("Slack message delivered successfully.")
        
        return response.ok
    except requests.exceptions.RequestException as e:
        logger.warning(f"Failed to send Slack message: {e}")
        return False


def upload_to_google_drive(filepath):
    """
    Upload a file to Google Drive and return a shareable link.
    Uses the same Google credentials as the Sheets API.
    File is shared with domain users only (organization members).
    Optionally uploads to a specific folder if GOOGLE_DRIVE_FOLDER_ID is set.
    
    Returns:
        str: Shareable link to the file, or None if upload failed
    """
    try:
        # Load credentials from OAuth2 user credentials
        creds = Credentials(
            token=GOOGLE_CREDENTIALS_JSON['token'],
            refresh_token=GOOGLE_CREDENTIALS_JSON['refresh_token'],
            token_uri=GOOGLE_CREDENTIALS_JSON['token_uri'],
            client_id=GOOGLE_CREDENTIALS_JSON['client_id'],
            client_secret=GOOGLE_CREDENTIALS_JSON['client_secret'],
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        
        # Build Drive API service
        from googleapiclient.http import MediaFileUpload
        drive_service = build('drive', 'v3', credentials=creds)
        
        # File metadata
        file_metadata = {
            'name': os.path.basename(filepath),
            'mimeType': 'text/csv'
        }
        
        # Add folder if specified
        folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '')
        if folder_id:
            file_metadata['parents'] = [folder_id]
            logger.debug(f"Uploading to folder: {folder_id}")
        
        # Upload file
        media = MediaFileUpload(filepath, mimetype='text/csv', resumable=True)
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        file_id = file.get('id')
        
        # Make file accessible to domain users only (organization members)
        # This restricts access to users in your Google Workspace domain
        drive_service.permissions().create(
            fileId=file_id,
            body={
                'type': 'domain',  # Only users in your domain
                'role': 'reader',   # Can view but not edit
                'domain': os.getenv('GOOGLE_WORKSPACE_DOMAIN', '')  # Your organization domain
            }
        ).execute()
        
        # Get shareable link
        shareable_link = file.get('webViewLink')
        
        logger.info(f"File uploaded to Google Drive (domain-restricted): {shareable_link}")
        return shareable_link
        
    except Exception as e:
        logger.error(f"Failed to upload file to Google Drive: {e}")
        return None


def parse_date(date_str):
    """
    Parse date string in DD.MM format to a date object.
    Uses current year for comparison.
    """
    if not date_str or not date_str.strip():
        return None
    
    try:
        # Parse DD.MM format and add current year
        current_year = datetime.now().year
        date_obj = datetime.strptime(f"{date_str.strip()}.{current_year}", "%d.%m.%Y")
        return date_obj.date()
    except ValueError:
        return None


def get_emails_from_sheet():
    """
    Reads emails from Google Sheets using the credentials file.
    Only returns emails with dates prior to today (not including today).
    """
    logger.info("Starting to read emails from Google Sheets")
    
    # Load credentials from OAuth2 user credentials
    creds = Credentials(
        token=GOOGLE_CREDENTIALS_JSON['token'],
        refresh_token=GOOGLE_CREDENTIALS_JSON['refresh_token'],
        token_uri=GOOGLE_CREDENTIALS_JSON['token_uri'],
        client_id=GOOGLE_CREDENTIALS_JSON['client_id'],
        client_secret=GOOGLE_CREDENTIALS_JSON['client_secret'],
        scopes=GOOGLE_CREDENTIALS_JSON['scopes']
    )
    
    service = build('sheets', 'v4', credentials=creds)
    range_name = f'{SHEET_NAME}!{SHEET_RANGE}'
    
    logger.info(f"Reading data from sheet: {SPREADSHEET_ID}, range: {range_name}")
    
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name
    ).execute()
    
    values = result.get('values', [])
    
    if not values:
        logger.warning("No data found in the sheet")
        return []
    
    logger.info(f"Retrieved {len(values)} rows from Google Sheets")
    
    today = datetime.now().date()
    logger.info(f"Filtering emails with dates before: {today.strftime('%d.%m.%Y')}")
    
    filtered_emails = []
    current_date = None
    
    for row in values:
        if not row or not row[0].strip():
            continue
        
        email = row[0].strip()
        date_str = row[1].strip() if len(row) > 1 else ""
        
        row_date = parse_date(date_str)
        
        if row_date is not None:
            current_date = row_date
        
        if current_date is not None:
            if current_date < today:
                filtered_emails.append(email)
    
    logger.info(f"Filtered results: {len(filtered_emails)} emails to process")
    return filtered_emails


def upload_emails_bulk(emails, api_key):
    """
    Attempts to upload multiple emails to Iterable using bulk API.
    Returns details on success/fail counts and specifically imported (failed to ban) emails.
    """
    headers = {
        'Api-Key': api_key,
        'Content-Type': 'application/json'
    }
    
    # Prepare bulk update payload
    users = [
        {
            'email': email,
            'dataFields': {
                'source': 'google_sheets_banned_list_check'
            }
        }
        for email in emails
    ]
    
    payload = {
        'users': users
    }
    
    try:
        response = requests.post(
            ITERABLE_BULK_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        response_data = response.json() if response.content else {}
        
        success_count = response_data.get('successCount', 0)
        
        # Get all the different types of failed/blocked emails
        failed_updates = response_data.get('failedUpdates', {})
        forgotten_emails = failed_updates.get('forgottenEmails', [])
        invalid_emails = response_data.get('invalidEmails', [])
        not_found_emails = failed_updates.get('notFoundEmails', [])
        invalid_data_emails = failed_updates.get('invalidDataEmails', [])
        
        # Collect ALL blocked/failed emails (convert to lowercase for comparison)
        all_blocked_emails = set(
            email.lower() for email in (
                forgotten_emails + 
                invalid_emails + 
                not_found_emails + 
                invalid_data_emails
            )
        )
        
        # Identify imported emails (Successes = Not Blocked)
        imported_emails = []
        if success_count > 0:
            imported_emails = [email for email in emails if email.lower() not in all_blocked_emails]
        
        return {
            'success': success_count > 0, # "Success" here means the API accepted them (which is BAD for us)
            'blocked_count': len(all_blocked_emails),
            'imported_count': success_count,
            'imported_emails': imported_emails
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return {
            'success': False,
            'blocked_count': 0,
            'imported_count': 0,
            'imported_emails': []
        }


def process_domain(domain_name, api_key, emails):
    """
    Process a specific domain: Upload emails and report results to Slack.
    """
    logger.info(f"--- Processing Domain: {domain_name} ---")
    print(f"Processing Domain: {domain_name}")

    batches = [emails[i:i + BATCH_SIZE] for i in range(0, len(emails), BATCH_SIZE)]
    
    total_imported = 0
    all_imported_emails = []
    
    for batch in batches:
        result = upload_emails_bulk(batch, api_key)
        total_imported += result.get('imported_count', 0)
        all_imported_emails.extend(result.get('imported_emails', []))

    # Logic for Slack Notification
    if total_imported == 0:
        # GOOD: All emails were blocked
        logger.info(f"Domain {domain_name}: SUCCESS - All emails blocked.")
        slack_msg = f"SSL на {domain_name} залито"
        
        if SLACK_USER_ID:
            send_message_to_slack(SLACK_USER_ID, slack_msg)
        else:
            print(f"SKIP SLACK: {slack_msg}")
    else:
        # BAD: Some emails got through
        logger.warning(f"Domain {domain_name}: FAIL - {total_imported} emails imported.")
        logger.debug("\n".join(all_imported_emails))

        if total_imported <= 20:
            # Send as text message
            emails_str = "\n".join(all_imported_emails)
            slack_msg = f"На {domain_name} не залилось {total_imported} контактів: \n{emails_str}"
            
            if SLACK_USER_ID:
                send_message_to_slack(SLACK_USER_ID, slack_msg)
            else:
                print(f"SKIP SLACK: {slack_msg}")
        else:
            # Send as CSV file via Google Drive
            today = datetime.now()
            date_str = today.strftime("%d%m")  # Format: DDMM (e.g., 1712 for Dec 17)
            time_str = today.strftime("%H%M")  # Format: HHMM (e.g., 2046 for 20:46)
            csv_filename = f"{domain_name}_{date_str}_{time_str}_SSL_IMPORTED.csv"
            csv_filepath = os.path.join("logs", csv_filename)
            
            # Create CSV file
            try:
                with open(csv_filepath, 'w', encoding='utf-8') as f:
                    f.write("email\n")  # Header
                    for email in all_imported_emails:
                        f.write(f"{email}\n")
                
                logger.info(f"Created CSV file: {csv_filepath}")
                
                # Upload to Google Drive and get shareable link
                drive_link = upload_to_google_drive(csv_filepath)
                
                if drive_link:
                    # Send message with Google Drive link
                    slack_msg = f"На {domain_name} не залилось {total_imported} контактів.\nФайл з контактами які не залились: {drive_link}"
                    
                    if SLACK_USER_ID:
                        send_message_to_slack(SLACK_USER_ID, slack_msg)
                        print(f"✓ CSV file uploaded to Google Drive and link sent to Slack")
                    else:
                        print(f"SKIP SLACK: {slack_msg}")
                else:
                    # Fallback: send message with local file reference
                    slack_msg = f"На {domain_name} не залилось {total_imported} emails. Local file: {csv_filename}"
                    
                    if SLACK_USER_ID:
                        send_message_to_slack(SLACK_USER_ID, slack_msg)
                    else:
                        print(f"SKIP SLACK: {slack_msg}")
                    
                    print(f"⚠️  CSV file created locally (Google Drive upload failed): {csv_filepath}")
                
            except Exception as e:
                logger.error(f"Failed to create CSV file: {e}")
                # Fallback to truncated text message
                emails_str = ", ".join(all_imported_emails[:20])
                slack_msg = f"На {domain_name} не залилось {total_imported} emails (showing first 20): {emails_str}"
                
                if SLACK_USER_ID:
                    send_message_to_slack(SLACK_USER_ID, slack_msg)
                else:
                    print(f"SKIP SLACK: {slack_msg}")


def main():
    """
    Main function to orchestrate the email upload process across multiple domains.
    """
    today = datetime.now().date()
    
    logger.info("=" * 60)
    logger.info("Starting Banned Email Bulk Upload Script (Multi-Domain)")
    if args.ssl_check_channel:
        logger.info("Mode: SSL Check Channel")
    else:
        logger.info("Mode: Default Channel")
    logger.info("=" * 60)
    
    # 1. Get emails from Google Sheets (Once)
    try:
        emails = get_emails_from_sheet()
        if not emails:
            logger.warning("No emails to process. Exiting.")
            return
        logger.info(f"Loaded {len(emails)} emails from source.")
    except Exception as e:
        logger.error(f"ERROR reading from Google Sheets: {e}", exc_info=True)
        return

    # 2. Iterate through domains
    print(f"Found {len(ITERABLE_API_KEYS)} domains to process.")
    
    for domain, key in ITERABLE_API_KEYS.items():
        try:
            process_domain(domain, key, emails)
        except Exception as e:
            logger.error(f"Error processing domain {domain}: {e}")
            if SLACK_USER_ID:
                send_message_to_slack(SLACK_USER_ID, f"Error processing {domain}: {str(e)}")

    logger.info("Script execution completed")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
