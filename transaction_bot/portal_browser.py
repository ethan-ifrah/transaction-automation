from contextlib import contextmanager
from playwright.sync_api import sync_playwright


@contextmanager
def launch_browser(headless: bool = False):
    """
    Opens a fresh browser for the portal; login is done by hand in the window.

    The original version kept a saved, logged-in browser profile between runs.
    That was removed for publication, so nothing about a session is stored.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
