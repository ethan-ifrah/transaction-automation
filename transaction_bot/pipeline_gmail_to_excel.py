"""
Full pipeline: Gmail → Portal → Excel

Usage:
    python pipeline_gmail_to_excel.py --start-date 2025-12-01 --end-date 2026-01-15
    
This script:
1. Reads Gmail for transaction notifications in the date range
2. Extracts MLS/contract numbers and builds a worklist
3. For each transaction:
   - Opens it in the portal
   - Scrapes all required fields
   - Appends rows (one per agent) to Excel
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import GMAIL, PORTAL, EXCEL
from worklist import build_worklist, extract_ids
from models import TransactionKey
from portal_browser import launch_browser
from portal_scraper import (
    goto_transactions_search,
    fill_mls_search,
    fill_contract_search,
    open_transaction_from_results,
    scrape_profil_basic,
    scrape_types,
    scrape_courtier_agents,
)
from excel_writer import RowData, append_rows


# ============================================================
# Gmail Configuration
# ============================================================

# The bot connected to Gmail through OAuth 2.0 with read-only access.
# The OAuth client file (credentials.json) and the saved token (token.json)
# stayed on the machine that ran the bot and are not part of this repository.

# Read-only scope: the bot can read emails but cannot send, change or delete them
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")
# Sender, subject filter and login page come from config.json (removed for publication)
SENDER = GMAIL["sender"]
SUBJECT_PREFIX = GMAIL["subject_prefix"]
LOGIN_URL = PORTAL["login_url"]


# ============================================================
# Excel Configuration
# ============================================================

EXCEL_PATH = Path(EXCEL["path"])
SHEET_NAME = EXCEL["sheet"]


# ============================================================
# Gmail Functions
# ============================================================

def subject_starts_with(subject: str) -> bool:
    """
    Check the subject starts with the expected prefix. The Gmail query only
    filters by sender and date, so other emails from the same sender are
    removed here.
    """
    s = " ".join(subject.strip().split()).lower()
    prefix = " ".join(SUBJECT_PREFIX.strip().split()).lower()
    return s.startswith(prefix)


def get_gmail_service():
    """Authenticate and return Gmail API service."""
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    else:
        if not CREDENTIALS_FILE.exists():
            raise FileNotFoundError(
                "Missing credentials.json in project root. "
                "Download OAuth Desktop credentials and place it there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds)


def gmail_query(sender: str, start_date: str, end_date: str) -> str:
    """Build Gmail query string for date range."""
    start = datetime.strptime(start_date, "%Y-%m-%d").strftime("%Y/%m/%d")
    end = datetime.strptime(end_date, "%Y-%m-%d").strftime("%Y/%m/%d")
    return f'from:{sender} after:{start} before:{end}'


def decode_message_payload(payload: dict) -> str:
    """Decode email body from Gmail API payload."""
    def decode_b64(data: str) -> str:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")

    # Single-part email
    if "body" in payload and payload["body"].get("data"):
        return decode_b64(payload["body"]["data"])

    # Multi-part email
    parts = payload.get("parts", [])
    text_plain = None
    text_html = None

    stack = list(parts)
    while stack:
        part = stack.pop()
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if body.get("data"):
            if mime == "text/plain" and text_plain is None:
                text_plain = decode_b64(body["data"])
            elif mime == "text/html" and text_html is None:
                text_html = decode_b64(body["data"])
        # nested parts
        stack.extend(part.get("parts", []))

    return text_plain or text_html or ""


def fetch_gmail_transactions(start_date: str, end_date: str) -> list[tuple[str | None, str | None]]:
    """
    Fetch transaction emails from Gmail and extract MLS/contract pairs.
    
    Returns:
        List of (mls, contract) tuples
    """
    print(f"\n{'='*60}")
    print(f"STEP 1: Fetching Gmail transactions")
    print(f"{'='*60}")
    print(f"Date range: {start_date} to {end_date}")
    print(f"Sender: {SENDER}")
    print(f"Subject prefix: {SUBJECT_PREFIX}")
    
    service = get_gmail_service()
    q = gmail_query(SENDER, start_date, end_date)
    
    print(f"\nGmail query: {q}")
    print("Fetching messages...")
    
    msgs = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me",
            q=q,
            maxResults=500,
            pageToken=page_token
        ).execute()
        
        msgs.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    
    print(f"Total messages from query: {len(msgs)}")
    
    pairs = []
    matched_count = 0
    
    for item in msgs:
        msg = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        
        if not subject_starts_with(subject):
            continue
        
        matched_count += 1
        body_text = decode_message_payload(msg["payload"])
        mls, contract = extract_ids(body_text)
        pairs.append((mls, contract))
        
        print(f"  [{matched_count}] MLS: {mls or 'N/A':>10} | Contract: {contract or 'N/A':>10}")
    
    print(f"\nMatched {matched_count} transaction notification emails")
    return pairs


# ============================================================
# Portal Processing
# ============================================================

def build_rows_for_excel(
    mls: str | None,
    contract: str | None,
    profil: dict,
    types_val: str,
    agents: list,
    prix_vendu: str,
) -> list[RowData]:
    """Convert scraped data into Excel row format (one row per agent)."""
    rows: list[RowData] = []

    for a in agents:
        values = {
            "No Mls": mls or "",
            "No de contrat": contract or "",
            "Nom de rue": profil.get("Nom de rue", ""),
            "Ville": profil.get("Ville", ""),
            "Province/État": profil.get("Province/État", ""),
            "Code postal": profil.get("Code postal", ""),
            "Courtier_Nom": a.get("Courtier_Nom", ""),
            "Courtier_Bureau": a.get("Courtier_Bureau", ""),
            "Courtier_Titre": a.get("Courtier_Titre", ""),
            "Date d'acceptation": profil.get("Date d'acceptation", ""),
            "Date notariée": profil.get("Date notariée", ""),
            "Prix vendu": prix_vendu,
            "Rétribution": a.get("Rétribution", ""),
            "Types": types_val,
        }
        rows.append(RowData(values=values))

    return rows


def process_transaction(page, key: TransactionKey) -> tuple[int, str]:
    """
    Process a single transaction: search, open, scrape, return rows.
    
    Returns:
        (row_count, status_message)
    """
    mls = key.mls
    contract = key.contract
    pk = key.primary_key()
    
    print(f"\n  Processing: {pk}")
    
    # Navigate to search page
    goto_transactions_search(page)
    
    # Search by MLS if available, otherwise contract
    if mls:
        fill_mls_search(page, mls)
        search_needle = mls
    elif contract:
        fill_contract_search(page, contract)
        search_needle = contract
    else:
        return (0, "ERROR: No MLS or contract")
    
    # Try to open transaction
    result = open_transaction_from_results(page, search_needle)
    
    if result.status == "not_found":
        return (0, f"NOT FOUND: {result.reason}")
    elif result.status == "multiple":
        return (0, f"MULTIPLE RESULTS: {result.reason}")
    elif result.status == "error":
        return (0, f"ERROR: {result.reason}")
    elif result.status != "opened":
        return (0, f"UNKNOWN STATUS: {result.status}")
    
    # Scrape data
    try:
        profil = scrape_profil_basic(page)
        types_val = scrape_types(page)
        agents, prix_vendu = scrape_courtier_agents(page)
        
        if not agents:
            return (0, "WARNING: No agents found")
        
        # Build Excel rows
        rows = build_rows_for_excel(mls, contract, profil, types_val, agents, prix_vendu)
        
        # Append to Excel
        added = append_rows(EXCEL_PATH, SHEET_NAME, rows, anchor_col_name="No Mls")
        
        return (added, f"OK: {added} rows (agents: {len(agents)})")
        
    except Exception as e:
        return (0, f"ERROR during scrape: {str(e)}")


# ============================================================
# Main Pipeline
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Process Gmail transaction notifications and write to Excel"
    )
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (no visible window)"
    )
    args = parser.parse_args()
    
    # Validate Excel file exists
    if not EXCEL_PATH.exists():
        print(f"\nERROR: Excel file not found: {EXCEL_PATH.resolve()}")
        print("Create it from sample_data/staging_template.xlsx (see README).")
        return
    
    # Step 1: Fetch from Gmail
    pairs = fetch_gmail_transactions(args.start_date, args.end_date)
    
    if not pairs:
        print("\nNo transaction emails found. Exiting.")
        return
    
    # Step 2: Build worklist (deduplicated)
    worklist = build_worklist(pairs)
    
    print(f"\n{'='*60}")
    print(f"STEP 2: Built worklist (deduplicated)")
    print(f"{'='*60}")
    print(f"Total unique transactions to process: {len(worklist)}")
    for i, t in enumerate(worklist, 1):
        print(f"  [{i:2d}] {t.primary_key()}")
    
    # Step 3: Process each transaction via portal
    print(f"\n{'='*60}")
    print(f"STEP 3: Processing transactions via portal")
    print(f"{'='*60}")
    
    with launch_browser(headless=args.headless) as page:
        # Manual login prompt
        page.goto(LOGIN_URL)
        
        input(
            "\n*** MANUAL LOGIN REQUIRED ***\n"
            "Please log into the portal in the browser window.\n"
            "Once you're logged in and see the portal home page,\n"
            "press ENTER here to continue...\n"
        )
        
        total_rows_added = 0
        successful = 0
        failed = 0
        
        for i, key in enumerate(worklist, 1):
            print(f"\n[{i}/{len(worklist)}] {key.primary_key()}")
            
            try:
                rows_added, status = process_transaction(page, key)
                print(f"    → {status}")
                
                total_rows_added += rows_added
                if rows_added > 0:
                    successful += 1
                else:
                    failed += 1
                    
            except Exception as e:
                print(f"    → ERROR: {str(e)}")
                failed += 1
        
        # Summary
        print(f"\n{'='*60}")
        print(f"PIPELINE COMPLETE")
        print(f"{'='*60}")
        print(f"Transactions processed: {len(worklist)}")
        print(f"  Successful: {successful}")
        print(f"  Failed: {failed}")
        print(f"Total rows added to Excel: {total_rows_added}")
        print(f"Excel file: {EXCEL_PATH.resolve()}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
