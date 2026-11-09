"""Детерминированные правила из SPEC.md. Модель здесь ничего не решает."""
from __future__ import annotations

import re
from datetime import date, datetime

from .models import Contact, ContactPackage, Plan, Research, Trigger

# §4 — повод → оффер
OFFER_BY_TRIGGER = {"BUILDING": "EMBED", "CHANGE": "BACKTEST", "SCALE": "REPLAY", "MARKET": "BACKTEST"}
TRIGGER_PRIORITY = {"BUILDING": 0, "CHANGE": 1, "SCALE": 2, "MARKET": 3}
VERIFIED = {"verified", "verified_llm"}

# §3 — свежесть
HOOK_DAYS = 60          # можно открывать письмо и поздравлять
STATE_DAYS = 270        # можно опираться как на текущее состояние, без поздравлений
CONTEXT_DAYS = 365      # только фон в отчёте, в письмо не идёт
MARKET_FUTURE_DAYS = 120  # рыночный дедлайн в будущем не дальше этого

# §6 — лимиты
D1_MAX_WORDS = 180
D3_MAX_WORDS = 90
D5_MAX_WORDS = 45
LINKEDIN_MAX_CHARS = 200
SUBJECT_WORDS = (1, 6)

D1_REQUIRED = [
    "my guess",
    "we have the base in place",
    "the interesting part is what to build on top",
    "from an export, no integration needed",
]

# §5 — запрещено в теле (регистр не важен)
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
CONGRATS = re.compile(r"\bcongrat", re.I)


# ---------- даты и уровни свежести ----------

def parse_event_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def tier(t: Trigger, today: date) -> str:
    d = parse_event_date(t.event_date)
    if d is None:
        return "invalid"
    age = (today - d).days
    if age < 0:
        return "hook" if (t.type == "MARKET" and -age <= MARKET_FUTURE_DAYS) else "invalid"
    if age <= HOOK_DAYS:
        return "hook"
    if age <= STATE_DAYS:
        return "state"
    if age <= CONTEXT_DAYS:
        return "context"
    return "stale"


def apply_tiers(r: Research, today: date) -> Research:
    for t in r.triggers:
        t.tier = tier(t, today)
    return r


def usable_triggers(r: Research) -> list[Trigger]:
    """В письмо идут только проверенные поводы уровня hook или state."""
    return [t for t in r.triggers if t.verification in VERIFIED and t.tier in ("hook", "state")]


def _recency(t: Trigger) -> int:
    return -(parse_event_date(t.event_date) or date.min).toordinal()


def make_plan(r: Research) -> Plan | None:
    """§4. Оффер — по типу среди проверенных поводов ≤270 дней (компания важнее рынка).
    Зацепка — сам повод оффера, если он свежий (≤60 дней), иначе самый свежий повод ≤60 дней,
    иначе повод оффера в формулировке «текущее состояние»."""
    us = usable_triggers(r)
    company = [t for t in us if t.type != "MARKET"]
    pool, cohort = (company, "company") if company else ([t for t in us if t.type == "MARKET"], "market")
    if not pool:
        return None
    primary = sorted(pool, key=lambda t: (TRIGGER_PRIORITY[t.type], _recency(t)))[0]
    if primary.tier == "hook":
        hook = primary
    else:
        fresh = sorted([t for t in us if t.tier == "hook"], key=lambda t: (t.type == "MARKET", _recency(t)))
        hook = fresh[0] if fresh else primary
    d1_offer = OFFER_BY_TRIGGER[primary.type]
    d3 = {t.id: OFFER_BY_TRIGGER[t.type] for t in us
          if t.type != primary.type and t.id != hook.id and OFFER_BY_TRIGGER[t.type] != d1_offer}
    return Plan(cohort=cohort, primary_trigger_id=primary.id, hook_trigger_id=hook.id,
                hook_tier=hook.tier, d1_offer=d1_offer, d3_options=d3, d3_fallback_offer=d1_offer)


def d3_offer(plan: Plan, basis: str) -> str:
    return plan.d3_options.get(basis, plan.d3_fallback_offer)


def check_d3_basis(plan: Plan, pkg: ContactPackage) -> list[str]:
    P = []
    for need in {plan.primary_trigger_id, plan.hook_trigger_id}:
        if need not in pkg.d1_trigger_ids:
            P.append(f"день 1 должен опираться на повод {need}")
    unused = [tid for tid in plan.d3_options if tid not in pkg.d1_trigger_ids]
    if pkg.d3_basis == "B":
        if unused:
            P.append(f"день 3: есть неиспользованный повод {unused[0]} с другим оффером — гипотеза B не допускается")
    elif pkg.d3_basis not in plan.d3_options:
        P.append(f"день 3: основание {pkg.d3_basis} недопустимо")
    elif pkg.d3_basis in pkg.d1_trigger_ids:
        P.append(f"день 3: повод {pkg.d3_basis} уже упомянут в дне 1")
    return P


# ---------- размер и уровни контактов (§2) ----------

def size_band(size: str) -> str:
    s = (size or "").replace(",", "")
    nums = re.findall(r"\d+", s)
    if not nums:
        return "unknown"
    m = re.search(r"(\d+)\s*[–—-]\s*(\d+)", s)
    top = int(m.group(2)) if m else int(nums[0])
    return "small" if top <= 50 else "mid" if top <= 200 else "large"


def executors_needed(band: str) -> int:
    """Снизу вверх обязательно: 51–200 — минимум 1 исполнитель, 200+ — минимум 1 (цель 2).
    Неизвестный размер считаем средним."""
    return 0 if band == "small" else 1


def executors_target(band: str) -> int:
    return 0 if band == "small" else 2 if band == "large" else 1


LEADER = re.compile(r"\b(founder|co-?founder|ceo|coo|cxo|cfo|cto|chief|president|managing director)\b", re.I)
HEAD = re.compile(r"\b(head|director|vp|vice president|svp|evp)\b", re.I)


def contact_level(title: str) -> str:
    if LEADER.search(title or ""):
        return "leader"
    if HEAD.search(title or ""):
        return "head"
    return "executor"


def assign_schedule(contacts: list[Contact], band: str) -> list[Contact]:
    """51+: исполнители с дня 1; руководители — с дня 3 (если есть исполнитель)."""
    out = []
    has_exec = any(contact_level(c.title) == "executor" for c in contacts)
    for c in contacts:
        lvl = contact_level(c.title)
        start = 3 if (band in ("mid", "large", "unknown") and lvl != "executor" and has_exec) else 1
        out.append(c.model_copy(update={"level": lvl, "start_day": start}))
    return out


def reachable(c: Contact) -> bool:
    return bool(c.email)


def people_gate(contacts: list[Contact], band: str) -> tuple[bool, str]:
    execs = [c for c in contacts if contact_level(c.title) == "executor" and reachable(c)]
    need = executors_needed(band)
    if len(execs) < need:
        return False, f"размер {band}: нужен минимум {need} исполнитель с email, найдено {len(execs)}"
    if not any(reachable(c) for c in contacts):
        return False, "нет ни одного контакта с email"
    return True, ""


# ---------- проверка текстов (§8, код) ----------

def _strip_signature(body: str, sig: list[str]) -> str:
    lines = body.rstrip().splitlines()
    if sig and [l.strip() for l in lines[-len(sig):]] == sig:
        lines = lines[: -len(sig)]
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

    known = set()
    for t in usable_triggers(research):
        known |= numbers_in(t.quote) | numbers_in(t.summary)
    known |= numbers_in(account_fact)

    first = (contact.name.split() or [""])[0]
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
            m = re.search(rx, text, re.I)
            if m:
                P.append(f"{day}: {why} ({m.group(0)!r})")
        extra = numbers_in(text + " " + t.subject) - known
        if extra:
            P.append(f"{day}: числа без источника: {sorted(extra)}")
        if CONGRATS.search(text) and plan.hook_tier != "hook":
            P.append(f"{day}: поздравление допустимо только с событием моложе {HOOK_DAYS} дней")

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
            if plan.d1_offer == "EMBED" and "under your brand" not in low:
                P.append("D1: оффер EMBED должен содержать «under your brand»")
        if day == "D1-2":
            if t.channel != "linkedin":
                P.append("D1-2 должен быть linkedin")
            if len(text) > LINKEDIN_MAX_CHARS:
                P.append(f"LinkedIn: {len(text)} знаков > {LINKEDIN_MAX_CHARS}")
        if day in ("D3", "D5") and t.channel != "email":
            P.append(f"{day} должен быть email")
        if day == "D3" and words(text) > D3_MAX_WORDS:
            P.append(f"D3: {words(text)} слов > {D3_MAX_WORDS}")
        if day == "D5" and words(text) > D5_MAX_WORDS:
            P.append(f"D5: {words(text)} слов > {D5_MAX_WORDS}")
        offer_here = plan.d1_offer if day in ("D1", "D1-2") else d3_offer(plan, pkg.d3_basis) if day == "D3" else None
        if offer_here and offer_here != "EMBED" and "under your brand" in low:
            P.append(f"{day}: «under your brand» допустимо только для EMBED")
    return P
