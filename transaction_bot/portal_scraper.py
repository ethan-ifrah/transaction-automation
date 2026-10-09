from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, Any, List

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from config import PORTAL, SELECTORS

# ============================================================
# Constants / Selectors
# ============================================================

# Real values live in config.json (not committed). See config.example.json.
SEARCH_URL = PORTAL["search_url"]

# Search inputs are located by element ID rather than visible label text,
# because the portal switches between English and French.
MLS_INPUT_ID = SELECTORS["mls_search_input"]
CONTRACT_INPUT_ID = SELECTORS["contract_search_input"]  # contract-number fallback
RESULT_ROWS = SELECTORS["search_result_rows"]

TYPES_SPAN_ID = SELECTORS["types_field"]
PROFILE_AREA_ID = SELECTORS["profile_area"]

# The tab strip inside an open transaction. Scoping tab clicks to it avoids
# clicking the portal's top navigation, which has tabs with similar names.
TRANSACTION_TABBAR_ID = SELECTORS["transaction_tab_bar"]
PROFILE_TAB_ID = SELECTORS["profile_tab_id"]
AGENTS_TAB_ID = SELECTORS["agents_tab_id"]

# Profile fields are laid out as label/value rows
FIELD_LABELS = SELECTORS["field_labels"]
FIELD_ROW_XPATH = SELECTORS["field_row_xpath"]
FIELD_VALUE = SELECTORS["field_value"]
SIDEBAR = SELECTORS["sidebar"]

# Dates are read from hidden inputs, which hold a clean YYYY-MM-DD value
ACCEPTED_DATE_INPUT = SELECTORS["accepted_date_input"]
CLOSING_DATE_INPUT = SELECTORS["closing_date_input"]

# Agents table (one row per agent on the transaction)
AGENT_INFO_CELLS = SELECTORS["agent_info_cells"]
AGENT_OFFICE_CELL = SELECTORS["agent_office_cell"]
AGENT_COMMISSION_CELL = SELECTORS["agent_commission_cell"]
SOLD_PRICE_ROW = SELECTORS["sold_price_row"]
SOLD_PRICE_VALUE = SELECTORS["sold_price_value"]


# ============================================================
# Data models
# ============================================================

@dataclass(frozen=True)
class OpenResult:
    status: str  # "opened" | "not_found" | "multiple" | "error"
    reason: str = ""


# ============================================================
# Navigation helpers
# ============================================================

def goto_transactions_search(page: Page) -> None:
    page.goto(SEARCH_URL)
    page.wait_for_load_state("networkidle")


def fill_mls_search(page: Page, mls: str) -> None:
    mls_input = page.locator(MLS_INPUT_ID)
    mls_input.wait_for(state="visible", timeout=15000)
    mls_input.click()
    mls_input.fill("")
    mls_input.fill(mls)
    mls_input.press("Enter")


def fill_contract_search(page: Page, contract: str) -> None:
    box = page.locator(CONTRACT_INPUT_ID)
    box.wait_for(state="visible", timeout=15000)
    box.click()
    box.fill("")
    box.fill(contract)
    box.press("Enter")


def open_transaction_from_results(page: Page, needle_text: str) -> OpenResult:
    """
    Tries to open a transaction from the search results by looking for table rows
    containing the given needle text (usually MLS; could be contract).
    """
    rows = page.locator(RESULT_ROWS).filter(has_text=needle_text)

    try:
        rows.first.wait_for(state="visible", timeout=8000)
    except PlaywrightTimeoutError:
        if rows.count() == 0:
            return OpenResult("not_found", f"0 results for: {needle_text}")
        return OpenResult("not_found", "Results did not become visible")

    count = rows.count()
    if count == 0:
        return OpenResult("not_found", f"0 results for: {needle_text}")
    if count > 1:
        return OpenResult("multiple", f"{count} results found for: {needle_text}")

    row = rows.nth(0)
    link = row.locator("a").first
    if link.count() > 0:
        link.click()
    else:
        row.click()

    page.wait_for_load_state("networkidle")
    return OpenResult("opened")


def click_transaction_tab(page: Page, tab_id: str) -> None:
    """
    Clicks a tab inside the open transaction's own tab strip.
    Scoped to that strip so it never matches the portal's top navigation tabs.
    """
    tabs = page.locator(TRANSACTION_TABBAR_ID)
    tabs.wait_for(state="visible", timeout=15000)

    tab = tabs.locator(f"#{tab_id}")
    tab.wait_for(state="visible", timeout=15000)
    tab.click()
    page.wait_for_load_state("networkidle")


# ============================================================
# Utility helpers
# ============================================================

def _clean_text(s: str) -> str:
    # Collapses whitespace into a single line. Not used on cells where
    # line breaks separate values (those are parsed line by line instead).
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalize_date(date_str: str) -> str:
    """
    Normalizes portal dates to YYYY-MM-DD.
    Accepts:
      - YYYY-MM-DD
      - DD/MM/YYYY
    """
    s = _clean_text(date_str)
    if not s:
        return ""

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s

    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{mm}-{dd}"

    return s


def _money_to_number_str(text: str) -> str:
    """
    Converts:
      EN: $500,000.00 -> 500000.00
      FR: 500 000,00 $ -> 500000.00
    """
    t = _clean_text(text)
    if not t:
        return ""

    t = t.replace("$", "").strip()

    # English: comma thousands + dot decimals
    if "," in t and "." in t:
        t = t.replace(",", "")
    else:
        # French: space thousands + comma decimals
        t = t.replace(" ", "").replace(",", ".")

    t = re.sub(r"[^0-9.]", "", t)
    return t


def _extract_first_money(text: str) -> str:
    """
    Returns first money-like amount from a block of text.
    Example:
      "12 500,00 $\n(2,5000 % ...)" -> "12 500,00 $"
    """
    t = text.replace("\u00a0", " ")
    m = re.search(r"(\$?\s*\d[\d\s.,]*\d\s*\$?)", t)
    return m.group(1).strip() if m else ""


def _get_value_by_any_label(container, labels: List[str]) -> str:
    """
    Finds value beside a label in profil block. Tries multiple FR/EN label variants.
    Handles every label pattern listed in FIELD_LABELS.
    """
    for label_text in labels:
        # The portal uses more than one label pattern
        for selector in FIELD_LABELS:
            label = container.locator(selector).filter(
                has_text=re.compile(rf"^{re.escape(label_text)}", re.IGNORECASE)
            ).first

            if label.count() == 0:
                continue

            form_row = label.locator(FIELD_ROW_XPATH)
            field = form_row.locator(FIELD_VALUE).first
            if field.count() == 0:
                continue

            val = _clean_text(field.inner_text())
            if val:
                return val

    return ""


# ============================================================
# Sidebar + Profil scraping
# ============================================================

def scrape_types(page: Page) -> str:
    el = page.locator(TYPES_SPAN_ID)
    el.wait_for(state="visible", timeout=15000)
    return _clean_text(el.inner_text())

def _safe_get_input_value(page_or_locator, css: str) -> str:
    loc = page_or_locator.locator(css).first
    if loc.count() == 0:
        return ""
    return loc.get_attribute("value") or ""

def _scrape_sidebar_address_parts(page: Page) -> Dict[str, str]:
    """
    Tries to parse Nom de rue / Ville / Province from the sidebar "Address:" block.
    Works in EN and FR.
    """
    # The sidebar is plain text; search it for the address line.
    sidebar = page.locator(SIDEBAR).first
    text = sidebar.inner_text() if sidebar.count() else page.inner_text("body")

    text = text.replace("\u00a0", " ")

    # The line starts with "Address:" or "Adresse:" depending on language
    m = re.search(r"(?im)^(Address|Adresse)\s*:\s*(.+)$", text)
    if not m:
        return {"Nom de rue": "", "Ville": "", "Province/État": ""}

    addr = m.group(2).strip()

    # Split by commas
    parts = [p.strip() for p in addr.split(",") if p.strip()]

    # Example of the split address:
    # [ "123 Rue Exemple",
    #   "Montréal (Example borough)",
    #   "Quebec A1A1A1",
    #   "Canada" ]

    nom_rue = parts[0] if len(parts) >= 1 else ""
    ville = parts[1] if len(parts) >= 2 else ""

    province = ""
    postal_code = ""
    if len(parts) >= 3:
        # The third part holds the province followed by the postal code.
        # It is kept whole here; the postal code is moved into its own
        # "Code postal" column during the Excel cleaning stage.
        province = parts[2].strip()

    return {
        "Nom de rue": nom_rue,
        "Ville": ville,
        "Province/État": province,
        "Code postal": postal_code,
    }

def scrape_profil_basic(page: Page) -> Dict[str, str]:
    # Open the transaction's profile tab
    click_transaction_tab(page, PROFILE_TAB_ID)

    area = page.locator(PROFILE_AREA_ID)
    area.wait_for(state="visible", timeout=15000)

    addr_parts = _scrape_sidebar_address_parts(page)
    province = addr_parts.get("Province/État", "")
    ville = addr_parts.get("Ville", "")
    nom_rue = addr_parts.get("Nom de rue", "")
    postal_code = addr_parts.get("Code postal", "")


    prix_demande = _money_to_number_str(_get_value_by_any_label(page, ["Prix demandé", "Listing price"]))
    representation = _get_value_by_any_label(page, ["Représentation", "Representation"])

    # Dates: hidden inputs first (most reliable), without waiting on them,
    # because they are missing on some page layouts
    approved_val = _safe_get_input_value(page, ACCEPTED_DATE_INPUT)
    closing_val = _safe_get_input_value(page, CLOSING_DATE_INPUT)

    # Fall back to the visible labels when the hidden inputs are missing
    if not approved_val:
        approved_val = _get_value_by_any_label(area, ["Date d'acceptation", "Accepted date"])
    if not closing_val:
        closing_val = _get_value_by_any_label(area, ["Date notariée", "Closing date"])

    date_acceptation = _normalize_date(approved_val)
    date_notariee = _normalize_date(closing_val)



    return {
        "Province/État": province,
        "Ville": ville,
        "Nom de rue": nom_rue,
        "Code postal": postal_code,
        "Prix demandé": prix_demande,
        "Représentation": representation,
        "Date d'acceptation": date_acceptation,
        "Date notariée": date_notariee,
    }


# ============================================================
# Agents tab scraping
# ============================================================

def scrape_courtier_agents(page: Page) -> tuple[List[Dict[str, str]], str]:
    """
    Scrapes every agent row from the transaction's agents tab, plus the
    sold price shown on the same tab. No office filtering here: every agent
    is kept, and filtering to our own brokerage happens in the Excel stage.
    
    Returns:
        (agents_list, prix_vendu)
    """
    click_transaction_tab(page, AGENTS_TAB_ID)

    info_cells = page.locator(AGENT_INFO_CELLS)
    info_cells.first.wait_for(state="visible", timeout=15000)

    # Sold price
    sold_price_div = page.locator(SOLD_PRICE_ROW).first
    prix_vendu_raw = ""
    if sold_price_div.count() > 0:
        field = sold_price_div.locator(SOLD_PRICE_VALUE).first
        if field.count() > 0:
            prix_vendu_raw = field.inner_text()
    prix_vendu = _money_to_number_str(prix_vendu_raw)

    agents: List[Dict[str, str]] = []

    for i in range(info_cells.count()):
        info_td = info_cells.nth(i)
        row = info_td.locator("xpath=ancestor::tr[1]")

        office_td = row.locator(AGENT_OFFICE_CELL).first
        comm_td = row.locator(AGENT_COMMISSION_CELL).first

        # Name and title are separated by <br> tags, so parse the HTML line by line
        html = info_td.inner_html()
        html = re.sub(r"(?i)<br\s*/?>", "\n", html)
        html = re.sub(r"<[^>]+>", "", html)
        lines = [ln.strip() for ln in html.splitlines() if ln.strip()]

        if len(lines) < 2:
            continue

        agent_name = lines[0]
        agent_title = lines[1]

        # Office: first line of the cell
        office_text = office_td.inner_text() if office_td.count() else ""
        office_text = office_text.replace("\u00a0", " ")
        office_lines = [ln.strip() for ln in office_text.splitlines() if ln.strip()]
        office_name = office_lines[0] if office_lines else ""

        # The commission cell holds the dollar amount and, on the next line,
        # the percentage. Take only the dollar amount; reading the whole cell
        # merged both numbers into one invalid value.
        raw_comm = comm_td.inner_text() if comm_td.count() else ""
        money_only = _extract_first_money(raw_comm)
        retribution = _money_to_number_str(money_only)

        agents.append(
            {
                "Courtier_Nom": agent_name,
                "Courtier_Titre": agent_title,
                "Courtier_Bureau": office_name,
                "Rétribution": retribution,
            }
        )

    return agents, prix_vendu
