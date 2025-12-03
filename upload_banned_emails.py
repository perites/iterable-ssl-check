#!/usr/bin/env python3
"""
Script to read emails from Google Sheets and attempt to upload them to Iterable.
These emails are expected to be banned and should fail during upload.
Only processes emails with dates prior to today (not including today).
"""

import json
import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import requests


# Load environment variables from .env file
load_dotenv()

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
    "scopes": ["https://www.googleapis.com/auth/spreadsheets.readonly"],
    "universe_domain": "googleapis.com",
    "account": os.getenv("GOOGLE_ACCOUNT", ""),
    "expiry": os.getenv("GOOGLE_TOKEN_EXPIRY", "2025-12-02T17:44:45.532101Z")
}

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SHEET_NAME = os.getenv("SHEET_NAME", "Main")
SHEET_RANGE = os.getenv("SHEET_RANGE", "A:D")

ITERABLE_API_KEY = os.getenv("ITERABLE_API_KEY")
ITERABLE_BULK_API_URL = os.getenv("ITERABLE_BULK_API_URL", "https://api.iterable.com/api/users/bulkUpdate")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))

# Slack Configuration
SLACK_WEBHOOK_URL = os.getenv("WORKFLOW_WEBHOOK_URL", "")
SLACK_USER_ID = os.getenv("SLACK_USER_ID", "")

# Validate required environment variables
required_vars = {
    "GOOGLE_TOKEN": GOOGLE_CREDENTIALS_JSON["token"],
    "GOOGLE_REFRESH_TOKEN": GOOGLE_CREDENTIALS_JSON["refresh_token"],
    "GOOGLE_CLIENT_ID": GOOGLE_CREDENTIALS_JSON["client_id"],
    "GOOGLE_CLIENT_SECRET": GOOGLE_CREDENTIALS_JSON["client_secret"],
    "SPREADSHEET_ID": SPREADSHEET_ID,
    "ITERABLE_API_KEY": ITERABLE_API_KEY
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
    logger.error(error_msg)
    logger.error("Please create a .env file with all required variables. See .env.example for reference.")
    raise ValueError(error_msg)


def send_message_to_slack(slack_user_id, message):
    """
    Send a message to Slack using a workflow webhook.
    
    Args:
        slack_user_id (str): Slack user ID to notify
        message (str): Message to send
        
    Returns:
        bool: True if message was sent successfully, False otherwise
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


def parse_date(date_str):
    """
    Parse date string in DD.MM format to a date object.
    Uses current year for comparison.
    
    Args:
        date_str (str): Date string in DD.MM format
        
    Returns:
        datetime.date or None: Parsed date or None if invalid
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
    
    Sheet structure:
    Column A: Email
    Column B: Date (DD.MM format)
    Column C: Number
    Column D: Source
    
    Returns:
        list: List of email addresses that match the date criteria
    """
    logger.info("Starting to read emails from Google Sheets")
    logger.debug(f"Spreadsheet ID: {SPREADSHEET_ID}, Sheet: {SHEET_NAME}")
    
    # Load credentials from OAuth2 user credentials
    creds = Credentials(
        token=GOOGLE_CREDENTIALS_JSON['token'],
        refresh_token=GOOGLE_CREDENTIALS_JSON['refresh_token'],
        token_uri=GOOGLE_CREDENTIALS_JSON['token_uri'],
        client_id=GOOGLE_CREDENTIALS_JSON['client_id'],
        client_secret=GOOGLE_CREDENTIALS_JSON['client_secret'],
        scopes=GOOGLE_CREDENTIALS_JSON['scopes']
    )
    
    logger.debug("Google credentials loaded successfully")
    
    # Build the Sheets API service
    service = build('sheets', 'v4', credentials=creds)
    
    # Define the range to read all relevant columns
    range_name = f'{SHEET_NAME}!{SHEET_RANGE}'
    
    logger.info(f"Reading data from sheet: {SPREADSHEET_ID}, range: {range_name}")
    
    # Call the Sheets API
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name
    ).execute()
    
    values = result.get('values', [])
    
    if not values:
        logger.warning("No data found in the sheet")
        print("No data found in the sheet.")
        return []
    
    logger.info(f"Retrieved {len(values)} rows from Google Sheets")
    
    # Get today's date
    # today = datetime.now().date()
    today = datetime(2025, 12, 2).date()
    print(f"Today's date: {today.strftime('%d.%m.%Y')}")
    logger.info(f"Filtering emails with dates before: {today.strftime('%d.%m.%Y')}")
    print()
    
    # Filter emails based on date
    filtered_emails = []
    skipped_future_dates = []
    current_date = None  # Track the current date as we iterate
    
    for idx, row in enumerate(values, start=1):
        # Skip empty rows
        if not row or not row[0].strip():
            continue
        
        email = row[0].strip()
        date_str = row[1].strip() if len(row) > 1 else ""
        
        # Check if this row has a new date
        row_date = parse_date(date_str)
        
        if row_date is not None:
            # This row has a date, update the current date
            current_date = row_date
            logger.debug(f"Row {idx}: New date found - {date_str} (parsed as {current_date})")
        
        # If we have a current date, check if this email should be included
        if current_date is not None:
            # Only include emails with dates before today (not including today)
            if current_date < today:
                filtered_emails.append(email)
            else:
                skipped_future_dates.append((email, current_date.strftime('%d.%m')))
    
    # Print summary
    print(f"Total rows processed: {len(values)}")
    print(f"Emails with dates before today: {len(filtered_emails)}")
    
    logger.info(f"Filtered results: {len(filtered_emails)} emails to process, {len(skipped_future_dates)} skipped (future dates)")
    
    if skipped_future_dates:
        print(f"Emails with future dates (skipped): {len(skipped_future_dates)}")
        print("  Future dates skipped:")
        for email, date in skipped_future_dates[:5]:  # Show first 5
            print(f"    - {email} ({date})")
        if len(skipped_future_dates) > 5:
            print(f"    ... and {len(skipped_future_dates) - 5} more")
    
    print()
    return filtered_emails


def upload_emails_bulk(emails):
    """
    Attempts to upload multiple emails to Iterable using bulk API.
    This is expected to fail for banned emails.
    
    Args:
        emails (list): List of email addresses to upload
        
    Returns:
        dict: Response from Iterable API
    """
    logger.info(f"Preparing bulk upload for {len(emails)} emails")
    
    headers = {
        'Api-Key': ITERABLE_API_KEY,
        'Content-Type': 'application/json'
    }
    
    # Prepare bulk update payload
    users = [
        {
            'email': email,
            'dataFields': {
                'source': 'google_sheets_banned_list'
            }
        }
        for email in emails
    ]
    
    payload = {
        'users': users
    }
    
    try:
        print(f"Sending bulk request with {len(emails)} emails...")
        logger.debug(f"Sending POST request to {ITERABLE_BULK_API_URL}")
        
        response = requests.post(
            ITERABLE_BULK_API_URL,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        logger.info(f"Received response with status code: {response.status_code}")
        
        response_data = response.json() if response.content else {}
        
        # Check if emails were actually successfully imported
        # Even with 200 status, emails can fail due to being banned/forgotten
        success_count = response_data.get('successCount', 0)
        fail_count = response_data.get('failCount', 0)
        
        logger.info(f"API Response - Success: {success_count}, Failed: {fail_count}")
        
        # Get all the different types of failed/blocked emails
        failed_updates = response_data.get('failedUpdates', {})
        forgotten_emails = failed_updates.get('forgottenEmails', [])
        invalid_emails = response_data.get('invalidEmails', [])
        not_found_emails = failed_updates.get('notFoundEmails', [])
        invalid_data_emails = failed_updates.get('invalidDataEmails', [])
        
        logger.debug(f"Blocked breakdown - Forgotten: {len(forgotten_emails)}, Invalid: {len(invalid_emails)}, "
                    f"Not Found: {len(not_found_emails)}, Invalid Data: {len(invalid_data_emails)}")
        
        # Collect ALL blocked/failed emails (convert to lowercase for comparison)
        all_blocked_emails = set(
            email.lower() for email in (
                forgotten_emails + 
                invalid_emails + 
                not_found_emails + 
                invalid_data_emails
            )
        )
        
        # Determine which emails were successfully imported
        # Only if successCount > 0, then find which ones were NOT blocked
        imported_emails = []
        if success_count > 0:
            # Case-insensitive comparison: check if email.lower() is NOT in blocked set
            imported_emails = [email for email in emails if email.lower() not in all_blocked_emails]
            logger.warning(f"UNEXPECTED: {len(imported_emails)} emails were successfully imported: {imported_emails}")
        
        # Consider it a "success" (for our purposes) only if emails were actually imported
        actually_imported = success_count > 0
        
        return {
            'status_code': response.status_code,
            'response': response_data,
            'success': actually_imported,
            'blocked_count': len(all_blocked_emails),
            'imported_count': success_count,
            'imported_emails': imported_emails  # List of emails that were imported
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return {
            'status_code': None,
            'response': {'error': str(e)},
            'success': False,
            'blocked_count': 0,
            'imported_count': 0,
            'imported_emails': []
        }


def main():
    """
    Main function to orchestrate the email upload process.
    """
    # Get today's date for reporting
    today = datetime.now().date()
    check_time = datetime.now()
    
    logger.info("=" * 60)
    logger.info("Starting Banned Email Bulk Upload Script")
    logger.info(f"Check date: {today.strftime('%A, %B %d, %Y')}")
    logger.info(f"Check time: {check_time.strftime('%H:%M:%S')}")
    logger.info("=" * 60)
    
    print("=" * 60)
    print("Starting Banned Email Bulk Upload Script")
    print(f"Check date: {today.strftime('%A, %B %d, %Y')}")
    print(f"Check time: {check_time.strftime('%H:%M:%S')}")
    print("=" * 60)
    print()
    
    # Get emails from Google Sheets
    try:
        emails = get_emails_from_sheet()
        logger.info(f"Successfully retrieved {len(emails)} emails from Google Sheets")
    except Exception as e:
        logger.error(f"ERROR reading from Google Sheets: {e}", exc_info=True)
        print(f"ERROR reading from Google Sheets: {e}")
        return
    
    if not emails:
        logger.warning("No emails to process. Exiting.")
        print("No emails to process. Exiting.")
        return
    
    print()
    print("=" * 60)
    print(f"Attempting to bulk upload {len(emails)} emails to Iterable")
    print("(These are expected to fail as banned emails)")
    print("=" * 60)
    print()
    
    logger.info(f"Starting bulk upload of {len(emails)} emails to Iterable")
    
    # Split emails into batches if needed
    batches = [emails[i:i + BATCH_SIZE] for i in range(0, len(emails), BATCH_SIZE)]
    
    logger.info(f"Split into {len(batches)} batch(es) (max {BATCH_SIZE} emails per batch)")
    print(f"Uploading in {len(batches)} batch(es) (max {BATCH_SIZE} emails per batch)")
    print()
    
    # Upload each batch
    all_results = []
    total_blocked = 0
    total_imported = 0
    all_imported_emails = []  # Track all successfully imported emails
    
    for idx, batch in enumerate(batches, 1):
        print(f"[Batch {idx}/{len(batches)}] Uploading {len(batch)} emails...")
        result = upload_emails_bulk(batch)
        all_results.append(result)
        
        total_blocked += result.get('blocked_count', 0)
        total_imported += result.get('imported_count', 0)
        
        # Collect imported emails
        imported_in_batch = result.get('imported_emails', [])
        all_imported_emails.extend(imported_in_batch)
        
        if result['success']:
            print(f"  ⚠️  UNEXPECTED: {result['imported_count']} emails were imported!")
            if imported_in_batch:
                print(f"  Imported emails:")
                for email in imported_in_batch[:10]:  # Show first 10
                    print(f"    - {email}")
                if len(imported_in_batch) > 10:
                    print(f"    ... and {len(imported_in_batch) - 10} more")
        else:
            print(f"  ✓ EXPECTED: All emails blocked/rejected")
            print(f"  Blocked: {result.get('blocked_count', 0)}")
            if result.get('response'):
                forgotten = result['response'].get('failedUpdates', {}).get('forgottenEmails', [])
                invalid = result['response'].get('invalidEmails', [])
                if forgotten:
                    print(f"  Forgotten (banned): {len(forgotten)}")
                if invalid:
                    print(f"  Invalid: {len(invalid)}")
        print()
    
    # Summary
    print("=" * 60)
    print("Upload Summary")
    print("=" * 60)
    total_emails = len(emails)
    successful_batches = sum(1 for r in all_results if r['success'])
    failed_batches = sum(1 for r in all_results if not r['success'])
    
    logger.info("=" * 60)
    logger.info("Upload Summary")
    logger.info("=" * 60)
    logger.info(f"Total emails processed: {total_emails}")
    logger.info(f"Total batches: {len(batches)}")
    logger.info(f"Emails blocked (banned/invalid): {total_blocked}")
    logger.info(f"Emails imported: {total_imported}")
    
    print(f"Total emails processed: {total_emails}")
    print(f"Total batches: {len(batches)}")
    print(f"Emails blocked (banned/invalid): {total_blocked}")
    print(f"Emails imported: {total_imported}")
    print()
    
    if total_imported > 0:
        logger.warning(f"WARNING: {total_imported} emails were imported successfully (NOT banned as expected)")
        logger.warning(f"Imported emails: {all_imported_emails}")
        
        print("⚠️  WARNING: Some emails were imported successfully!")
        print(f"   {total_imported} emails were NOT banned as expected.")
        print()
        print("List of imported emails:")
        for email in all_imported_emails:
            print(f"  - {email}")
    else:
        logger.info("SUCCESS: All emails were blocked as expected (banned/invalid emails)")
        print("✓ All emails were blocked as expected (banned/invalid emails)")
    
    # Send summary to Slack
    print()
    print("=" * 60)
    print("Sending summary to Slack...")
    print("=" * 60)
    
    logger.info("Preparing to send summary to Slack")
    
    # Build Slack message
    slack_message = "📊Iterable Banned Email Check Summary\n\n"
    slack_message += f"Check Date: {today.strftime('%A, %B %d, %Y')}\n"
    slack_message += f"Check Time: {check_time.strftime('%H:%M:%S')}\n"
    slack_message += f"Emails checked: Dates before {today.strftime('%d.%m.%Y')}\n\n"
    slack_message += f"Total emails processed: {total_emails}\n"
    slack_message += f"Total batches: {len(batches)}\n"
    slack_message += f"Emails blocked (banned/invalid): {total_blocked}\n"
    slack_message += f"Emails imported: {total_imported}\n\n"
    
    if total_imported > 0:
        slack_message += "⚠️ WARNING: Some emails were imported successfully!\n"
        slack_message += f"   {total_imported} emails were NOT banned as expected.\n\n"
        slack_message += "List of imported emails:\n"
        for email in all_imported_emails:
            slack_message += f"{email}\n"
    else:
        slack_message += "✓ All emails were blocked as expected (banned/invalid emails)"
    
    # Send to Slack
    if SLACK_USER_ID:
        logger.info(f"Sending Slack notification to user: {SLACK_USER_ID}")
        success = send_message_to_slack(SLACK_USER_ID, slack_message)
        if success:
            logger.info("Slack notification sent successfully")
            print("✓ Summary sent to Slack successfully")
        else:
            logger.error("Failed to send Slack notification")
            print("✗ Failed to send summary to Slack")
    else:
        logger.warning("SLACK_USER_ID not set - skipping Slack notification")
        print("⚠️  SLACK_USER_ID not set - skipping Slack notification")
        print("   Set SLACK_USER_ID environment variable to enable Slack notifications")
    
    logger.info("Script execution completed")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
