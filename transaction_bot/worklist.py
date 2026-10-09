import re
from typing import Iterable
from models import TransactionKey

MLS_RE = re.compile(r"\bMLS\s+(\d+)\b", re.IGNORECASE)
CONTRACT_FR_RE = re.compile(r"\bnum(?:é|e)ro\s+de\s+contrat\s+(\d+)\b", re.IGNORECASE)
CONTRACT_EN_RE = re.compile(r"\bcontract\s+number\s+(\d+)\b", re.IGNORECASE)


def extract_ids(text: str) -> tuple[str | None, str | None]:
    mls = None
    contract = None

    m = MLS_RE.search(text)
    if m:
        mls = m.group(1)

    m = CONTRACT_FR_RE.search(text)
    if m:
        contract = m.group(1)
    else:
        m = CONTRACT_EN_RE.search(text)
        if m:
            contract = m.group(1)

    return mls, contract


def build_worklist(extracted_pairs: Iterable[tuple[str | None, str | None]]) -> list[TransactionKey]:
    """
    Turns (mls, contract) pairs from the emails into a list of transactions
    to process, keeping email order. The same transaction can trigger more
    than one email, so duplicates are removed by key: MLS number, or the
    contract number when the MLS is missing.
    """
    seen: set[str] = set()
    worklist: list[TransactionKey] = []

    for mls, contract in extracted_pairs:
        # Skip if both missing
        if not mls and not contract:
            continue

        key = TransactionKey(mls=mls, contract=contract)
        pk = key.primary_key()

        if pk in seen:
            continue
        seen.add(pk)
        worklist.append(key)

    return worklist
