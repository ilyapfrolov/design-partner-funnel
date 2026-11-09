"""Поставщик контактов. Сейчас — Findymail (по документации https://app.findymail.com/docs/):
  POST /api/search/employees  {website, job_titles[≤10], count≤3} → [{name, linkedinUrl, jobTitle, ...}]  1 кредит за человека
  POST /api/search/name       {name, domain} → {contact: {email}}                                         1 кредит, только если найден
  POST /api/verify            {email} → {verified: bool}                                                    1 кредит за запрос
Ключ — только в переменной окружения FINDYMAIL_API_KEY. Нет ключа — пайплайн работает без поставщика.
"""
from __future__ import annotations

import os

import requests

BASE = "https://app.findymail.com/api"
EXEC_TITLES = ["Payment Operations Manager", "Payments Operations", "Reconciliation", "Settlement",
               "Finance Operations", "Treasury Operations", "Merchant Operations", "Operations Manager",
               "Card Operations", "Operations Lead"]


class Findymail:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("FINDYMAIL_API_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _post(self, path: str, body: dict) -> dict | list | None:
        r = requests.post(f"{BASE}{path}", json=body, timeout=60, headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "Accept": "application/json"})
        if r.status_code in (402, 423, 429):
            raise RuntimeError(f"Findymail {path}: {r.status_code} (кредиты / пауза / лимит)")
        if r.status_code >= 400:
            return None
        return r.json()

    def employees(self, domain: str, titles: list[str] | None = None, count: int = 3) -> list[dict]:
        res = self._post("/search/employees", {"website": domain, "job_titles": (titles or EXEC_TITLES)[:10],
                                               "count": min(count, 3)})
        return res if isinstance(res, list) else []

    def find_email(self, name: str, domain: str) -> str:
        res = self._post("/search/name", {"name": name, "domain": domain}) or {}
        return ((res.get("contact") or {}).get("email") or "") if isinstance(res, dict) else ""

    def verify(self, email: str) -> bool:
        res = self._post("/verify", {"email": email}) or {}
        return bool(isinstance(res, dict) and res.get("verified"))
