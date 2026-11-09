"""Минимальный клиент Notion API. Токен — только из переменной окружения NOTION_TOKEN."""
from __future__ import annotations

import os
import time

import requests

API = "https://api.notion.com/v1"


class Notion:
    def __init__(self, version: str, token: str | None = None):
        token = token or os.environ.get("NOTION_TOKEN")
        if not token:
            raise SystemExit("Нет NOTION_TOKEN. Токен интеграции выдаёт администратор Notion (см. README).")
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Notion-Version": version,
            "Content-Type": "application/json",
        })

    def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(5):
            r = self.s.request(method, f"{API}{path}", timeout=60, **kw)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", "2")))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Notion {method} {path}: {r.status_code} {r.text[:400]}")
            return r.json()
        raise RuntimeError(f"Notion {method} {path}: rate limited")

    def query(self, data_source_id: str) -> list[dict]:
        out, cursor = [], None
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            res = self._req("POST", f"/data_sources/{data_source_id}/query", json=body)
            out += res.get("results", [])
            if not res.get("has_more"):
                return out
            cursor = res.get("next_cursor")

    def create_page(self, parent_page_id: str, title: str, blocks: list[dict]) -> dict:
        page = self._req("POST", "/pages", json={
            "parent": {"page_id": parent_page_id},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title[:200]}}]}},
            "children": blocks[:100],
        })
        for i in range(100, len(blocks), 100):
            self._req("PATCH", f"/blocks/{page['id']}/children", json={"children": blocks[i:i + 100]})
        return page

    def update_props(self, page_id: str, props: dict) -> None:
        self._req("PATCH", f"/pages/{page_id}", json={"properties": props})


def plain(prop: dict | None) -> str:
    """Текст из свойства любой распространённой типа."""
    if not prop:
        return ""
    t = prop.get("type")
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in v or [])
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(x.get("name", "") for x in v or [])
    if t in ("url", "email", "phone_number"):
        return v or ""
    if t == "number":
        return "" if v is None else str(v)
    if t == "relation":
        return ",".join(x.get("id", "") for x in v or [])
    if t == "formula":
        return str((v or {}).get((v or {}).get("type"), "") or "")
    return ""


def relation_ids(prop: dict | None) -> list[str]:
    if not prop or prop.get("type") != "relation":
        return []
    return [x["id"].replace("-", "") for x in prop.get("relation", [])]


def text_prop(value: str, kind: str) -> dict:
    """Свойство для записи: select / rich_text / url."""
    if kind == "select":
        return {"select": {"name": value}}
    if kind == "url":
        return {"url": value or None}
    return {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}
