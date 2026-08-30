"""Local roster of the Doubao accounts logged into the browser profile.

Passport refuses to enumerate a profile's accounts for Doubao's aid
(error_code 16, "该应用无权限"), and the only account it will describe is the
active one. So the roster is kept on disk: seeded by hand, and topped up with
whichever account happens to be active when the server starts or switches.

Free video quota resets daily, so an exhausted account is parked by date
rather than removed.
"""

import json
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_PATH = ".doubao_accounts.json"


class AccountPool:
    """Ordered list of accounts, each {sec_user_id, label, exhausted_on}."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.environ.get("DOUBAO_ACCOUNTS_FILE", DEFAULT_PATH)
        self._accounts: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("accounts: cannot read %s: %s", self.path, exc)
            return []
        if not isinstance(data, list):
            log.warning("accounts: %s is not a list, ignoring", self.path)
            return []
        return [a for a in data if isinstance(a, dict) and a.get("sec_user_id")]

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._accounts, fh, ensure_ascii=False, indent=2)
        except OSError as exc:
            log.warning("accounts: cannot write %s: %s", self.path, exc)

    def all(self) -> List[Dict[str, Any]]:
        return list(self._accounts)

    def find(self, sec_user_id: str) -> Optional[Dict[str, Any]]:
        return next(
            (a for a in self._accounts if a["sec_user_id"] == sec_user_id), None
        )

    def remember(self, sec_user_id: str, label: str = "") -> None:
        """Add an account, or refresh its label if already known."""
        if not sec_user_id:
            return
        existing = self.find(sec_user_id)
        if existing:
            if label and existing.get("label") != label:
                existing["label"] = label
                self._save()
            return
        self._accounts.append({"sec_user_id": sec_user_id, "label": label})
        self._save()
        log.info("accounts: remembered %s (%d total)",
                 label or sec_user_id[:12], len(self._accounts))

    def mark_exhausted(self, sec_user_id: str) -> None:
        """Park an account until the next daily quota reset."""
        account = self.find(sec_user_id)
        if account is None:
            self.remember(sec_user_id)
            account = self.find(sec_user_id)
        account["exhausted_on"] = date.today().isoformat()
        self._save()
        log.info("accounts: %s marked exhausted for today",
                 account.get("label") or sec_user_id[:12])

    def candidates(self, exclude: str = "") -> List[Dict[str, Any]]:
        """Accounts worth trying now: not excluded, not exhausted today."""
        today = date.today().isoformat()
        return [
            a for a in self._accounts
            if a["sec_user_id"] != exclude and a.get("exhausted_on") != today
        ]
