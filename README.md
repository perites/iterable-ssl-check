# Iterable Email Ban Checker

This script reads emails from Google Sheets and attempts to upload them to Iterable to verify they are banned/blocked.

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example environment file and fill in your credentials:

```bash
cp .env.example .env
```

Then edit `.env` and add your actual credentials:

```env
# Google Sheets Configuration
GOOGLE_TOKEN=your_actual_google_oauth_token
GOOGLE_REFRESH_TOKEN=your_actual_refresh_token
GOOGLE_CLIENT_ID=your_actual_client_id
GOOGLE_CLIENT_SECRET=your_actual_client_secret

# Google Sheets Details
SPREADSHEET_ID=your_actual_spreadsheet_id
SHEET_NAME=Main
SHEET_RANGE=A:D

# Iterable Configuration
ITERABLE_API_KEY=your_actual_iterable_api_key

# Slack Configuration (Optional)
WORKFLOW_WEBHOOK_URL=your_slack_webhook_url
SLACK_USER_ID=your_slack_user_id
```

### 3. Finding Your Credentials

#### Google Sheets ID
The Spreadsheet ID is in the URL of your Google Sheet:
```
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID_HERE/edit
```

#### Google OAuth Credentials
You need OAuth2 credentials with access to Google Sheets API. The script expects:
- `GOOGLE_TOKEN` - Current access token
- `GOOGLE_REFRESH_TOKEN` - Refresh token for renewing access
- `GOOGLE_CLIENT_ID` - OAuth client ID
- `GOOGLE_CLIENT_SECRET` - OAuth client secret

#### Iterable API Key
Get your API key from Iterable dashboard under Settings > API Keys

#### Slack Webhook (Optional)
Create a Slack workflow webhook for notifications

## Usage

Run the script:

```bash
python3 upload_banned_emails.py
```

The script will:
1. Read emails from Google Sheets (only emails with dates before today)
2. Attempt to bulk upload them to Iterable
3. Report which emails were blocked (expected) vs imported (unexpected)
4. Send a summary to Slack (if configured)
5. Log everything to `logs/` directory

## Sheet Structure

The Google Sheet should have this structure:

| Column A (Email) | Column B (Date) | Column C (Number) | Column D (Source) |
|------------------|-----------------|-------------------|-------------------|
| email@example.com | 01.12 | 123 | SourceName |
| another@email.com | | | |
| third@email.com | 02.12 | 456 | AnotherSource |

**Date Format**: DD.MM (e.g., 01.12 for December 1st)

**Date Logic**: 
- When a date appears in column B, all subsequent emails belong to that date
- Until a new date appears in column B
- Only emails with dates **before today** are processed

## Logs

All execution details are logged to:
- **Console**: INFO level and above
- **File**: `logs/iterable_check_YYYYMMDD_HHMMSS.log` - Complete DEBUG logs

## Security

⚠️ **IMPORTANT**: Never commit your `.env` file to version control!

The `.gitignore` file is configured to exclude:
- `.env` (your actual credentials)
- `logs/` (log files)
- Virtual environments
- Python cache files

Always use `.env.example` as a template and keep your actual `.env` file private.

## Expected Behavior

✅ **Success**: All emails are blocked/rejected by Iterable (they are banned)

⚠️ **Warning**: If any emails are successfully imported, they are NOT banned and the script will:
- Display them in the console
- List them in the log file
- Send an alert to Slack (if configured)

## Troubleshooting

### Missing Environment Variables
```
ValueError: Missing required environment variables: GOOGLE_TOKEN, ITERABLE_API_KEY
```
**Solution**: Make sure your `.env` file exists and contains all required variables

### Google Sheets Access Error
**Solution**: Verify your Google OAuth credentials are valid and have access to the spreadsheet

### Iterable API Error
**Solution**: Check that your Iterable API key is correct and has the necessary permissions
