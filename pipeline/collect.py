"""Заход 1 исследования: корпус собирает КОД, а не модель. Модель потом только извлекает поводы.

Источники: Google News RSS, sitemap сайта компании (блог / changelog / новости / тарифы / документация / карьера),
публичные API площадок вакансий (Greenhouse, Lever, Ashby), YouTube Data API (если задан YOUTUBE_API_KEY).
Каждый документ — с датой; всё старше окна отбрасывается.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlparse

import requests
from pydantic import BaseModel

from .verify import UA, html_to_text

SITE_PATHS = re.compile(r"/(blog|news|newsroom|press|changelog|release|updates|announc|pricing|careers|jobs|docs|product)", re.I)
JOB_TITLES = re.compile(r"(operat|reconcil|settlement|payment|finance|treasury|risk ops|merchant ops|disput|chargeback)", re.I)


class Doc(BaseModel):
    source: str        # news / company_site / jobs / youtube
    url: str
    date: str          # YYYY-MM-DD
    title: str
    text: str


def _get(url: str, timeout: int = 20) -> requests.Response | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=timeout)
        return r if r.status_code < 400 else None
    except requests.RequestException:
        return None


def _within(d: date | None, today: date, days: int) -> bool:
    return d is not None and 0 <= (today - d).days <= days


def domain_of(url_or_domain: str) -> str:
    s = url_or_domain.strip()
    host = urlparse(s if "://" in s else "https://" + s).netloc
    return host.lower().removeprefix("www.")


def google_news(company: str, today: date, days: int, limit: int = 40) -> list[Doc]:
    q = quote_plus(f'"{company}" when:{days}d')
    r = _get(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
    if not r:
        return []
    out = []
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        try:
            d = parsedate_to_datetime(it.findtext("pubDate") or "").date()
        except (TypeError, ValueError):
            d = None
        if not _within(d, today, days) or company.lower().split()[0] not in title.lower():
            continue
        desc = html_to_text(it.findtext("description") or "")
        out.append(Doc(source="news", url=link, date=d.isoformat(), title=title, text=f"{title}. {desc}"[:1500]))
        if len(out) >= limit:
            break
    return out


def _sitemap_urls(domain: str, today: date, days: int, max_maps: int = 8) -> list[tuple[str, date]]:
    queue = [f"https://{domain}/sitemap.xml"]
    rb = _get(f"https://{domain}/robots.txt")
    if rb:
        queue = re.findall(r"(?im)^sitemap:\s*(\S+)", rb.text) or queue
    seen, out = set(), []
    while queue and len(seen) < max_maps:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        r = _get(sm)
        if not r:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            continue
        ns = re.match(r"\{.*\}", root.tag)
        ns = ns.group(0) if ns else ""
        for sub in root.findall(f"{ns}sitemap"):
            loc = sub.findtext(f"{ns}loc")
            if loc and SITE_PATHS.search(loc):
                queue.insert(0, loc.strip())
            elif loc:
                queue.append(loc.strip())
        for u in root.findall(f"{ns}url"):
            loc, lm = u.findtext(f"{ns}loc"), u.findtext(f"{ns}lastmod")
            if not loc or not SITE_PATHS.search(loc):
                continue
            try:
                d = datetime.fromisoformat((lm or "").strip()[:10]).date()
            except ValueError:
                continue
            if _within(d, today, days):
                out.append((loc.strip(), d))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def company_site(domain: str, today: date, days: int, limit: int = 15) -> list[Doc]:
    out = []
    for url, d in _sitemap_urls(domain, today, days)[:limit]:
        r = _get(url)
        if not r:
            continue
        text = re.sub(r"\s+", " ", html_to_text(r.text)).strip()
        title = (re.search(r"(?is)<title>(.*?)</title>", r.text) or [None, url])[1]
        out.append(Doc(source="company_site", url=url, date=d.isoformat(), title=str(title).strip()[:200], text=text[:6000]))
    return out


def _slugs(company: str, domain: str) -> list[str]:
    base = re.sub(r"[^a-z0-9]", "", company.lower())
    dom = domain.split(".")[0]
    return list(dict.fromkeys([base, dom, dom.replace("-", ""), f"{base}app", f"{base}hq"]))


def jobs(company: str, domain: str, today: date, days: int) -> list[Doc]:
    out: list[Doc] = []
    for slug in _slugs(company, domain):
        r = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if r:
            for j in r.json().get("jobs", []):
                d = _iso(j.get("updated_at"))
                if JOB_TITLES.search(j.get("title", "")) and _within(d, today, days):
                    out.append(Doc(source="jobs", url=j.get("absolute_url", ""), date=d.isoformat(),
                                   title=j["title"], text=f"{j['title']} — {j.get('location', {}).get('name', '')}"))
        r = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if r and isinstance(r.json(), list):
            for j in r.json():
                ts = j.get("createdAt")
                d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date() if ts else None
                if JOB_TITLES.search(j.get("text", "")) and _within(d, today, days):
                    out.append(Doc(source="jobs", url=j.get("hostedUrl", ""), date=d.isoformat(), title=j["text"],
                                   text=(j.get("text", "") + " — " + (j.get("descriptionPlain") or ""))[:2000]))
        r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if r:
            for j in r.json().get("jobs", []):
                d = _iso(j.get("publishedAt"))
                if JOB_TITLES.search(j.get("title", "")) and _within(d, today, days):
                    out.append(Doc(source="jobs", url=j.get("jobUrl", ""), date=d.isoformat(), title=j["title"],
                                   text=(j["title"] + " — " + (j.get("descriptionPlain") or ""))[:2000]))
        if out:
            break
    return out


def _iso(s: str | None) -> date | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).date()
    except ValueError:
        return None


def youtube(company: str, today: date, days: int, limit: int = 10) -> list[Doc]:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return []
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _get("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults="
             f"{limit}&publishedAfter={after}&q={quote_plus(company)}&key={key}")
    if not r:
        return []
    out = []
    for it in r.json().get("items", []):
        sn = it.get("snippet", {})
        d = _iso(sn.get("publishedAt"))
        if not _within(d, today, days):
            continue
        out.append(Doc(source="youtube", url=f"https://www.youtube.com/watch?v={it['id'].get('videoId')}",
                       date=d.isoformat(), title=sn.get("title", ""),
                       text=f"{sn.get('channelTitle', '')}: {sn.get('title', '')}. {sn.get('description', '')}"[:1500]))
    return out


def collect(company: str, domain: str, today: date, days: int) -> list[Doc]:
    dom = domain_of(domain) if domain else ""
    docs = google_news(company, today, days)
    if dom:
        docs += company_site(dom, today, days)
        docs += jobs(company, dom, today, days)
    docs += youtube(company, today, days)
    return docs


def counts(docs: list[Doc]) -> dict[str, int]:
    c: dict[str, int] = {"news": 0, "company_site": 0, "jobs": 0, "youtube": 0}
    for d in docs:
        c[d.source] = c.get(d.source, 0) + 1
    return c
