"""§3: код сам открывает ссылку и ищет дословную цитату. Модели на слово не верим."""
from __future__ import annotations

import difflib
import html
import re
import unicodedata

import requests

UA = "Mozilla/5.0 (compatible; design-partner-funnel/1.0; +quote-verification)"


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", html.unescape(s or ""))
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace(" ", " ")
    s = re.sub(r"[^\w%+'\- ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return html.unescape(raw)


def quote_in_text(quote: str, text: str, threshold: float = 0.9) -> bool:
    q, t = normalize(quote), normalize(text)
    if not q:
        return False
    if q in t:
        return True
    # Терпим мелкие расхождения (пунктуация, вёрстка): ищем лучшее окно по токенам.
    qt, tt = q.split(), t.split()
    n = len(qt)
    if n == 0 or len(tt) < n:
        return False
    first = {w for w in qt[:3]}
    for i, w in enumerate(tt):
        if w not in first:
            continue
        window = tt[max(0, i - 3): i + n + 3]
        if difflib.SequenceMatcher(None, qt, window).ratio() >= threshold:
            return True
    return False


def fetch_text(url: str, timeout: int = 20) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code >= 400 or not r.text:
        return None
    return html_to_text(r.text)


def verify_quote(url: str, quote: str) -> str:
    """verified / not_found / unreachable."""
    text = fetch_text(url)
    if text is None:
        return "unreachable"
    return "verified" if quote_in_text(quote, text) else "not_found"
