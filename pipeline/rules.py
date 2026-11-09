"""Детерминированные правила из SPEC.md. Модель здесь ничего не решает."""
from __future__ import annotations

import re
from datetime import date

from .models import Contact, ContactPackage, Plan, Research, Trigger

# §4 — повод → оффер
OFFER_BY_TRIGGER = {"BUILDING": "EMBED", "CHANGE": "BACKTEST", "SCALE": "REPLAY"}
TRIGGER_PRIORITY = {"BUILDING": 0, "CHANGE": 1, "SCALE": 2}
VERIFIED = {"verified", "verified_llm"}

# §6 — лимиты
D1_MAX_WORDS = 180
D3_MAX_WORDS = 90
D5_MAX_WORDS = 45
LINKEDIN_MAX_CHARS = 200
SUBJECT_WORDS = (1, 6)

# §6 — обязательные фразы дня 1
D1_REQUIRED = [
    "my guess",
    "we have the base in place",
    "the interesting part is what to build on top",
    "from an export, no integration needed",
]

# §5 — запрещено в теле (регулярки, регистр не важен)
FORBIDDEN = [
    (r"\bhaia\b", "название HAIA в теле письма"),
    (r"help us (build|shape|create)", "просьба «помогите нам»"),
    (r"\b(book|schedule|jump on|hop on)\b.{0,20}\b(call|meeting|demo)\b", "просьба о созвоне"),
    (r"\b(quick|short) (call|chat)\b", "просьба о созвоне"),
    (r"\b\d+\s*(-|–)?\s*min(ute)?s?\b", "просьба о созвоне на N минут"),
    (r"\b(our|existing) (customers|clients)\b", "утверждение о клиентах"),
    (r"\bcustomers like\b", "утверждение о клиентах"),
    (r"\bintegrat(es|ed) with\b", "утверждение об интеграциях"),
    (r"\bpilot\b", "слово «pilot» — у нас нет условий пилота"),
    (r"\b(pricing|price|discount)\b|\bfree\b(?! to)", "условия / цены"),
    (r"\b(limited|only \d+) (spots|slots|places|seats)\b", "лимит мест"),
    (r"\b(value proposition|wtp|oem|control plane|ai-native)\b", "внутренний язык"),
    (r"\bjob[- ]to[- ]be[- ]done\b", "внутренний язык"),
    (r"\bautonomous(ly)?\b", "автономность — не заявляем"),
    (r"\b(you (don't|do not) have|lacks?|falls? short|struggl(e|es|ing) with)\b", "утверждение о слабости компании"),
    (r"\b(unlike|better than|outperforms?)\b", "сравнение с конкурентами"),
]


# ---------- даты и поводы ----------

def parse_event_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            from datetime import datetime
            d = datetime.strptime(s, fmt).date()
            return d
        except ValueError:
            continue
    return None


def is_fresh(t: Trigger, today: date, days: int) -> bool:
    d = parse_event_date(t.event_date)
    return d is not None and 0 <= (today - d).days <= days


def usable_triggers(r: Research) -> list[Trigger]:
    return [t for t in r.triggers if t.verification in VERIFIED]


def sort_triggers(ts: list[Trigger]) -> list[Trigger]:
    return sorted(ts, key=lambda t: (TRIGGER_PRIORITY[t.type], -(parse_event_date(t.event_date) or date.min).toordinal()))


def make_plan(r: Research) -> Plan | None:
    """§4: основной повод и допустимые основания дня 3. None — нет проверенных поводов."""
    ts = sort_triggers(usable_triggers(r))
    if not ts:
        return None
    primary = ts[0]
    d1_offer = OFFER_BY_TRIGGER[primary.type]
    d3 = {t.id: OFFER_BY_TRIGGER[t.type] for t in ts if t.type != primary.type}
    return Plan(primary_trigger_id=primary.id, d1_offer=d1_offer, d3_options=d3, d3_fallback_offer=d1_offer)


def d3_offer(plan: Plan, basis: str) -> str:
    return plan.d3_options.get(basis, plan.d3_fallback_offer)


def check_d3_basis(plan: Plan, pkg: ContactPackage) -> list[str]:
    """День 3: повод другого типа, не упомянутый в дне 1; если такой есть — B запрещено."""
    problems = []
    if plan.primary_trigger_id not in pkg.d1_trigger_ids:
        problems.append(f"день 1 должен опираться на основной повод {plan.primary_trigger_id}")
    unused = [tid for tid in plan.d3_options if tid not in pkg.d1_trigger_ids]
    if pkg.d3_basis == "B":
        if unused:
            problems.append(f"день 3: есть неиспользованный повод другого типа {unused[0]} — гипотеза B не допускается")
    elif pkg.d3_basis not in plan.d3_options:
        problems.append(f"день 3: основание {pkg.d3_basis} недопустимо (нужен повод другого типа или B)")
    elif pkg.d3_basis in pkg.d1_trigger_ids:
        problems.append(f"день 3: повод {pkg.d3_basis} уже упомянут в дне 1")
    return problems


# ---------- размер и уровни контактов (§2) ----------

def size_band(size: str) -> str:
    """small (≤50) / mid (51–200) / large (>200) / unknown. Берём верхнюю границу первого диапазона."""
    s = (size or "").replace(",", "").replace(" ", "")
    nums = [int(n) for n in re.findall(r"\d+", s)]
    if not nums:
        return "unknown"
    m = re.search(r"(\d+)\s*[–—-]\s*(\d+)", s)
    top = int(m.group(2)) if m else nums[0]
    if top <= 50:
        return "small"
    if top <= 200:
        return "mid"
    return "large"


LEADER = re.compile(r"\b(founder|co-?founder|ceo|coo|cxo|chief|president|managing director)\b", re.I)
HEAD = re.compile(r"\b(head|director|vp|vice president|svp|evp|lead)\b", re.I)


def contact_level(title: str) -> str:
    if LEADER.search(title or ""):
        return "leader"
    if HEAD.search(title or ""):
        return "head"
    return "executor"


def assign_schedule(contacts: list[Contact], band: str) -> list[Contact]:
    """Крупные: исполнители с дня 1, руководители с дня 3 (если есть исполнитель)."""
    out = []
    has_exec = any(contact_level(c.title) == "executor" for c in contacts)
    for c in contacts:
        lvl = contact_level(c.title)
        start = 3 if (band == "large" and lvl != "executor" and has_exec) else 1
        out.append(c.model_copy(update={"level": lvl, "start_day": start}))
    return out


# ---------- проверка текстов (§8, код) ----------

def _strip_signature(body: str, sender_lines: list[str]) -> str:
    lines = body.rstrip().splitlines()
    tail = [l.strip() for l in lines[-len(sender_lines):]] if sender_lines else []
    if sender_lines and tail == sender_lines:
        lines = lines[: -len(sender_lines)]
    return "\n".join(lines).rstrip()


def words(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][\w'’%.+-]*", s))


def numbers_in(s: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", s))


def _paragraphs(s: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", s.strip()) if p.strip()]


def check_package(pkg: ContactPackage, contact: Contact, account_fact: str, research: Research,
                  plan: Plan, sender: dict) -> list[str]:
    P: list[str] = []
    full_sig = [sender["name"], sender["title"], sender["site"]]
    short_sig = [sender["short"]]
    by_day = {t.day: t for t in pkg.touches}
    for need in ("D1", "D1-2", "D3", "D5"):
        if need not in by_day:
            P.append(f"нет касания {need}")
    if P:
        return P

    P += check_d3_basis(plan, pkg)

    known_numbers = set()
    for t in usable_triggers(research):
        known_numbers |= numbers_in(t.quote) | numbers_in(t.summary)
    known_numbers |= numbers_in(account_fact)

    first = (contact.name.split() or [""])[0]
    expect_embed = {"D1": plan.d1_offer == "EMBED", "D3": d3_offer(plan, pkg.d3_basis) == "EMBED"}

    for day, t in by_day.items():
        if t.channel == "email":
            sig = full_sig if day == "D1" else short_sig
            if [l.strip() for l in t.body.rstrip().splitlines()[-len(sig):]] != sig:
                P.append(f"{day}: подпись должна быть «{' / '.join(sig)}»")
            text = _strip_signature(t.body, sig)
        else:
            text = t.body.strip()
        low = text.lower()

        for rx, why in FORBIDDEN:
            if re.search(rx, text, re.I):
                P.append(f"{day}: {why} ({re.search(rx, text, re.I).group(0)!r})")

        extra = numbers_in(text + " " + t.subject) - known_numbers
        if extra:
            P.append(f"{day}: числа без источника: {sorted(extra)}")

        if day == "D1":
            if t.channel != "email":
                P.append("D1 должен быть email")
            if not text.startswith(f"Hi {first},"):
                P.append(f"D1 должен начинаться с «Hi {first},»")
            if words(text) > D1_MAX_WORDS:
                P.append(f"D1: {words(text)} слов > {D1_MAX_WORDS}")
            for need in D1_REQUIRED:
                if need not in low:
                    P.append(f"D1: нет обязательной фразы «{need}»")
            sw = len(t.subject.split())
            if not (SUBJECT_WORDS[0] <= sw <= SUBJECT_WORDS[1]):
                P.append(f"D1: тема {sw} слов, нужно {SUBJECT_WORDS}")
            last = _paragraphs(text)[-1]
            if not last.endswith("?") or " or " not in last.lower():
                P.append("D1: финал должен быть вопросом «A или ваш вариант» (с «or», заканчивается «?»)")
        if day == "D1-2":
            if t.channel != "linkedin":
                P.append("D1-2 должен быть linkedin")
            if len(text) > LINKEDIN_MAX_CHARS:
                P.append(f"LinkedIn: {len(text)} знаков > {LINKEDIN_MAX_CHARS}")
        if day == "D3" and words(text) > D3_MAX_WORDS:
            P.append(f"D3: {words(text)} слов > {D3_MAX_WORDS}")
        if day == "D5" and words(text) > D5_MAX_WORDS:
            P.append(f"D5: {words(text)} слов > {D5_MAX_WORDS}")
        if day in ("D3", "D5") and t.channel != "email":
            P.append(f"{day} должен быть email")
        if day in expect_embed:
            has = "under your brand" in low or "under their brand" in low
            if day == "D1" and expect_embed[day] and not has:
                P.append("D1: оффер EMBED должен содержать «under your brand»")
            if not expect_embed[day] and "under your brand" in low:
                P.append(f"{day}: «under your brand» допустимо только для EMBED")
    return P
