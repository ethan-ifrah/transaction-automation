"""
Stage 3: enters each deal from the cleaned spreadsheet into the deal
management system.

One deal per MLS number, with seller-side and buyer-side advisors. Deals whose
commission shares don't total 100% are skipped. The script pauses on every
deal for the MLS number to be entered by hand, and records completed deals in
progress.json so a rerun never enters the same deal twice.

The system's address and the spreadsheet location come from .env
(see .env.example).
"""

import os
import re
import json
import time
import unicodedata
from dataclasses import dataclass
from typing import List

import pandas as pd
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page

# ============================================================
# CONFIG
# ============================================================

load_dotenv()

BASE_URL = os.getenv("BASE_URL", "").strip()
EXCEL_PATH = os.getenv("EXCEL_PATH", "").strip()
SHEET_NAME = os.getenv("SHEET_NAME", "Sheet1")

HEADLESS = False    # Browser stays visible so every deal can be watched as it's entered
HUMAN_DELAY = 1.5   # Seconds between actions; the form's dropdowns load slowly
OFFICE_MAP_PATH = "office_map.json"   # spreadsheet office names -> names in the system's dropdown
PROGRESS_FILE = "progress.json"       # MLS numbers already entered (contains real data; not committed)

if not BASE_URL or not EXCEL_PATH:
    raise RuntimeError("BASE_URL and EXCEL_PATH must be set in .env")

# ============================================================
# LOGGING
# ============================================================

def info(msg):
    print(f"[INFO] {msg}")

def debug(msg):
    print(f"[DEBUG] {msg}")

def error(msg):
    print(f"[ERROR] {msg}")

def pause(sec=HUMAN_DELAY):
    time.sleep(sec)

# ============================================================
# PROGRESS TRACKING
# ============================================================

def load_progress():
    if not os.path.exists(PROGRESS_FILE):
        return set()
    with open(PROGRESS_FILE, "r") as f:
        data = json.load(f)
        return set(data.get("completed_mls", []))

def save_progress(mls: str):
    completed = load_progress()
    completed.add(mls)
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"completed_mls": list(completed)}, f, indent=2)
    info(f"✓ Progress saved for MLS {mls}")

# ============================================================
# OFFICE MATCHING
# ============================================================

def norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("&", "and")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 \-]", "", s)
    return s.strip()

def load_office_map():
    if not os.path.exists(OFFICE_MAP_PATH):
        return {}
    with open(OFFICE_MAP_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {norm_text(k): v for k, v in raw.items()}

def resolve_office(excel_value: str, website_labels: List[str], office_map: dict) -> str:
    key = norm_text(excel_value)
    if key in office_map:
        return office_map[key]
    normalized_site = {norm_text(lbl): lbl for lbl in website_labels if lbl.strip()}
    if key in normalized_site:
        return normalized_site[key]
    raise ValueError(f"\nOFFICE NOT MATCHED: '{excel_value}'\nAdd to office_map.json\n")

# ============================================================
# COMMISSION
# ============================================================

def clean_percent_from_fraction(x) -> str:
    val = float(str(x).replace(",", "."))
    if val < 0 or val > 1:
        raise ValueError(f"Invalid commission fraction: {val}")
    return f"{val * 100:.2f}"

# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class Advisor:
    office: str
    name: str
    percent: str

@dataclass
class Deal:
    mls: str
    street: str
    city: str
    province: str
    postal_code: str
    contract_date: str
    close_date: str
    price: str
    sellers: List[Advisor]
    buyers: List[Advisor]
    seller_commission: float
    buyer_commission: float

# ============================================================
# LOAD ALL DEALS
# ============================================================

def load_deals():
    df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
    
    df = df[
        df["No Mls"].notna()
        & df["Nom de rue"].notna()
        & (df["Commission_percentage"] > 0)
    ]
    
    info(f"Loaded {len(df)} rows from Excel")
    
    deals = []
    completed_mls = load_progress()
    
    for mls, g in df.groupby("No Mls", sort=False):
        mls_str = str(int(mls))
        
        if mls_str in completed_mls:
            info(f"Skipping MLS {mls_str} (already completed)")
            continue
        
        total_commission = g["Commission_percentage"].sum()
        if abs(total_commission - 1.0) > 0.001:
            error(f"MLS {mls_str}: commission != 100% ({total_commission*100:.1f}%), skipping")
            continue
        
        first = g.iloc[0]
        sellers, buyers = [], []
        
        for _, r in g.iterrows():
            title = str(r["Courtier_Titre"]).strip().lower()
            advisor = Advisor(
                office=str(r["Courtier_Bureau"]).replace("&amp;", "&").strip(),
                name=str(r["Courtier_Nom"]).strip(),
                percent=clean_percent_from_fraction(r["Commission_percentage"]),
            )
            if title == "inscripteur":
                sellers.append(advisor)
            elif title == "collaborateur":
                buyers.append(advisor)
        
        # Postal code. If the cleaned sheet has none, a placeholder is entered.
        # Known limitation (see README): it should stop and ask instead.
        postal_code = str(first.get("Code postal", "")) if pd.notna(first.get("Code postal")) else ""
        if not postal_code or postal_code == "nan":
            postal_code = "H0H 0H0"
        
        # Commission amounts come straight from the Rétribution column,
        # summed per side. Nothing is estimated or calculated from the price.
        seller_rows = g[g["Courtier_Titre"].str.lower() == "inscripteur"]
        buyer_rows = g[g["Courtier_Titre"].str.lower() == "collaborateur"]
        
        seller_commission = seller_rows["Rétribution"].sum() if len(seller_rows) > 0 else 0
        buyer_commission = buyer_rows["Rétribution"].sum() if len(buyer_rows) > 0 else 0
        
        deals.append(
            Deal(
                mls=mls_str,
                street=str(first["Nom de rue"]).strip(),
                city=str(first["Ville"]).strip(),
                province=str(first["Province/État"]).strip(),
                postal_code=postal_code.strip(),
                contract_date=pd.to_datetime(first["Date d'acceptation"]).strftime("%Y-%m-%d"),
                close_date=pd.to_datetime(first["Date notariée"]).strftime("%Y-%m-%d"),
                price=str(int(first["Prix vendu"])),
                sellers=sellers,
                buyers=buyers,
                seller_commission=seller_commission,
                buyer_commission=buyer_commission,
            )
        )
    
    info(f"Ready to process {len(deals)} deals")
    return deals

# ============================================================
# PLAYWRIGHT ACTIONS
# ============================================================

def select_by_label(page: Page, name: str, label: str):
    sel = page.locator(f'select[name="{name}"]')
    sel.wait_for()
    pause(0.8)
    sel.select_option(label=label)
    pause(1.2)

def click_next(page: Page):
    btn = page.locator("button.button-next").first
    btn.wait_for()
    pause(0.8)
    btn.click()
    pause(1.5)

def fill_advisors(page: Page, block_label: str, advisors: List[Advisor], office_map):
    if not advisors:
        return
    
    block = page.locator("div", has=page.locator(f"text={block_label}")).first
    block.wait_for()
    pause(1.0)
    
    add_button = page.get_by_role("button", name=f"Add another {block_label} row")
    current_rows = block.locator("select.advisor-select").count() // 2
    
    for _ in range(len(advisors) - current_rows):
        add_button.click()
        pause(1.0)
    
    for i, adv in enumerate(advisors):
        info(f"  Filling {block_label} {i+1}/{len(advisors)}: {adv.name}")
        
        all_office_selects = block.locator("select.advisor-select").nth(i * 2)
        all_name_selects = block.locator("select.advisor-select").nth(i * 2 + 1)
        all_percent_inputs = block.locator("input.percent").nth(i)
        
        site_labels = all_office_selects.locator("option").all_inner_texts()
        resolved = resolve_office(adv.office, site_labels, office_map)
        
        all_office_selects.select_option(label=resolved)
        pause(1.5)
        
        # Choosing an office reloads the advisor list; wait until this
        # advisor's name appears before selecting it
        try:
            page.wait_for_function(
                """
                (args) => {
                    const [selectElement, advisorName] = args;
                    const options = Array.from(selectElement.options);
                    return options.some(opt => opt.textContent.trim() === advisorName);
                }
                """,
                arg=[all_name_selects.element_handle(), adv.name],
                timeout=30000
            )
        except Exception as e:
            # List the advisors that were available, to show why the match failed
            available = all_name_selects.locator("option").all_inner_texts()
            error(f"Advisor '{adv.name}' not found in dropdown")
            error(f"Available advisors: {available}")
            raise ValueError(f"Advisor '{adv.name}' not found in office '{resolved}'")
        
        all_name_selects.select_option(label=adv.name)
        pause(1.0)
        
        all_percent_inputs.click()
        pause(0.5)
        all_percent_inputs.fill(adv.percent)
        pause(1.0)

# ============================================================
# CREATE DEAL
# ============================================================

def create_deal(page: Page, deal: Deal, office_map):
    info(f"Creating MLS {deal.mls}")
    
    page.locator('button[title="create deal"]').click()
    pause(2.0)
    
    shop_labels = page.locator('select[name="shopId"] option').all_inner_texts()
    shop = resolve_office(
        deal.sellers[0].office if deal.sellers else deal.buyers[0].office,
        shop_labels,
        office_map
    )
    
    select_by_label(page, "shopId", shop)
    
    # Dates
    date_input = page.locator('input[name="contractDate"]')
    date_input.click()
    pause(0.5)
    date_input.fill(deal.contract_date)
    pause(1.0)
    
    close_input = page.locator('input[name="expectedCloseDate"]')
    close_input.click()
    pause(0.5)
    close_input.fill(deal.close_date)
    pause(1.0)
    
    # Price
    price_input = page.locator('input[placeholder="0"]').first
    price_input.wait_for(state="visible", timeout=30000)
    price_input.click()
    pause(0.5)
    price_input.fill(deal.price)
    pause(1.5)
    
    # Advisors
    fill_advisors(page, "SELLER ADVISOR", deal.sellers, office_map)
    fill_advisors(page, "BUYER ADVISOR", deal.buyers, office_map)
    
    # Category is set last on this page, just before moving on
    select_by_label(page, "category", "Sales")
    click_next(page)
    
    # Commission
    select_by_label(page, "type", "CURRENCY")
    
    if deal.seller_commission > 0:
        seller_input = page.locator('label:has-text("SELLER SIDE")').locator('..').locator('input').first
        seller_input.click()
        pause(0.5)
        seller_input.fill(str(deal.seller_commission))
        pause(1.0)
    
    if deal.buyer_commission > 0:
        buyer_input = page.locator('label:has-text("BUYER SIDE")').locator('..').locator('input').first
        buyer_input.click()
        pause(0.5)
        buyer_input.fill(str(deal.buyer_commission))
        pause(1.0)
    
    click_next(page)
    
    # Manual checkpoint: the script waits here until the MLS number has been
    # entered by hand and ENTER is pressed
    print(f"\n{'='*80}")
    print(f"⏸️  MLS PAGE - MANUAL ENTRY REQUIRED")
    print(f"{'='*80}")
    print(f"MLS Number: {deal.mls}")
    print("\nPlease:")
    print(f"  1. Type '{deal.mls}' in the MLS field")
    print(f"  2. Click 'Create: {deal.mls}' in the dropdown")
    print("  3. Press ENTER here to continue")
    print(f"{'='*80}")
    input()
    
    # Address
    street_input = page.locator('input[name="street"]')
    street_input.click()
    pause(0.5)
    street_input.fill(deal.street)
    pause(1.0)
    
    city_input = page.locator('input[name="city"]')
    city_input.click()
    pause(0.5)
    city_input.fill(deal.city)
    pause(1.0)
    
    state_input = page.locator('input[name="state"]')
    state_input.click()
    pause(0.5)
    state_input.fill(deal.province)
    pause(1.0)
    
    postal_input = page.locator('input[name="postalCode"]')
    postal_input.wait_for(state="visible", timeout=10000)
    postal_input.click()
    pause(0.5)
    postal_input.fill(deal.postal_code)
    pause(1.0)
    
    # Square footage isn't in the cleaned sheet; a fixed value of 1 is entered
    # (see README: Limitations)
    sqft_input = page.locator('input[name="sqft"]')
    sqft_input.click()
    pause(0.5)
    sqft_input.fill("1")
    pause(1.0)
    
    # Finish
    click_next(page)
    click_next(page)
    click_next(page)
    
    page.locator("button:has-text('Finish')").click()
    pause(2.0)
    
    info(f"✓ Completed MLS {deal.mls}")

# ============================================================
# MAIN
# ============================================================

def main():
    try:
        deals = load_deals()
        office_map = load_office_map()
        
        if not deals:
            print("\n✓ No deals to process (all completed)")
            return
        
        print("\n" + "="*80)
        print(f"READY TO PROCESS {len(deals)} DEALS")
        print("="*80)
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=HEADLESS)
            context = browser.new_context()
            page = context.new_page()
            page.goto(BASE_URL)
            
            input("\nLog in manually, then press ENTER to start automation...\n")
            
            for i, deal in enumerate(deals, 1):
                print(f"\n{'='*80}")
                print(f"[{i}/{len(deals)}] Processing MLS {deal.mls}")
                print(f"  Address: {deal.street}, {deal.city}")
                print(f"  Sellers: {len(deal.sellers)} | Buyers: {len(deal.buyers)}")
                print(f"{'='*80}")
                
                try:
                    create_deal(page, deal, office_map)
                    save_progress(deal.mls)
                    pause(2.0)
                    
                except Exception as e:
                    error(f"Failed to process MLS {deal.mls}")
                    error(f"Error: {str(e)}")
                    screenshot_path = f"error_mls_{deal.mls}.png"
                    page.screenshot(path=screenshot_path, full_page=True)
                    error(f"Screenshot saved: {screenshot_path}")
                    
                    print("\nOptions:")
                    print("  y - Continue to next deal")
                    print("  r - Retry this deal")
                    print("  n - Stop automation")
                    response = input("Choice (y/r/n): ").lower()
                    
                    if response == 'r':
                        # Retry: return to the deals page and queue the same deal again
                        try:
                            page.goto(BASE_URL)
                            pause(2.0)
                            deals.insert(i, deal)
                        except:
                            error("Could not reset, skipping deal")
                    elif response != 'y':
                        break
            
            print("\n" + "="*80)
            print("✅ AUTOMATION COMPLETE")
            print("="*80)
            browser.close()
    
    except Exception as e:
        error(f"Fatal error: {str(e)}")
        raise

if __name__ == "__main__":
    main()
