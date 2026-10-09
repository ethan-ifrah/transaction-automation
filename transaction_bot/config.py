"""
Loads local settings from config.json: portal addresses, the email filter,
page selectors and the staging workbook location.

config.json is not committed because it describes private systems. In this
published version, config.example.json marks every address and email detail as
removed, and the page selectors are invented examples of the real format.
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("config.json")


def _load() -> dict:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            "config.json not found. Copy config.example.json to config.json "
            "and fill in the real values."
        )
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


CONFIG = _load()

GMAIL = CONFIG["gmail"]
PORTAL = CONFIG["portal"]
SELECTORS = CONFIG["selectors"]
EXCEL = CONFIG["excel"]
