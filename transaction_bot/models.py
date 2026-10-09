from dataclasses import dataclass

@dataclass(frozen=True)
class TransactionKey:
    mls: str | None
    contract: str | None

    def primary_key(self) -> str:
        """
        Key used to remove duplicates and to choose the portal search:
        MLS number when present, contract number otherwise.
        """
        if self.mls:
            return f"MLS:{self.mls}"
        if self.contract:
            return f"CONTRACT:{self.contract}"
        raise ValueError("TransactionKey must have either mls or contract")
