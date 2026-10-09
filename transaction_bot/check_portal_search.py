"""
Manual check for the portal stage: searches one transaction, opens it and
prints what the scraper extracts, so it can be compared against the portal
by eye. Nothing is written to Excel.

Usage:
    python check_portal_search.py --mls 12345678
"""

import argparse

from portal_browser import launch_browser
from portal_scraper import (
    goto_transactions_search,
    fill_mls_search,
    open_transaction_from_results,
    scrape_profil_basic,
    scrape_types,
    scrape_courtier_agents,
)


def main():
    parser = argparse.ArgumentParser(description="Scrape one transaction and print the result")
    parser.add_argument("--mls", required=True, help="MLS number of a transaction that exists in the portal")
    mls = parser.parse_args().mls

    with launch_browser(headless=False) as page:
        goto_transactions_search(page)

        input(
            "If you're not logged in, log in now.\n"
            "Once you see the Transactions search page, press ENTER..."
        )

        fill_mls_search(page, mls)
        result = open_transaction_from_results(page, mls)
        print("Open result:", result)

        if result.status != "opened":
            print("Transaction was not opened. Stopping here.")
            input("Press ENTER to close...")
            return

        # --- Profil + Types ---
        profil = scrape_profil_basic(page)
        types_val = scrape_types(page)

        print("\n=== TRANSACTION (partial) ===")
        print(f"Province/État: {profil.get('Province/État','')}")
        print(f"Ville: {profil.get('Ville','')}")
        print(f"Nom de rue: {profil.get('Nom de rue','')}")
        print(f"Prix demandé: {profil.get('Prix demandé','')}")
        print(f"Représentation: {profil.get('Représentation','')}")
        print(f"Types: {types_val}")

        # --- Agents and sold price (both on the agents tab) ---
        agents, prix_vendu = scrape_courtier_agents(page)

        print(f"Prix vendu: {prix_vendu}")
        print("\n=== AGENTS (all, unfiltered) ===")
        print(f"Total agents found: {len(agents)}")
        for a in agents:
            print(a)

        input("\nPress ENTER to close the browser...")


if __name__ == "__main__":
    main()
