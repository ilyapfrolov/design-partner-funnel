"""Этапы воронки. Порядок, выборку, переходы, проверки и холды решает код; модель — исполнитель одного вызова.

select → research (3 захода) → people (3 захода, гейт снизу вверх) → write → review → publish
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from . import collect as collect_mod
from .llm import Runner, call_json
from .models import (Account, AccountState, Contact, ExtractResult, PeopleResult, Research, ReviewResult,
                     RoundLog, Trigger, WriteResult)
from .notion import Notion, plain, relation_ids, text_prop
from .providers import OWNER_TITLES, Findymail
from .render import card_blocks, card_title
from .rules import (apply_tiers, check_package, contact_level, executors_target, make_plan, nearest_titles,
                    people_gate, pick_recipients, role_tier, segment_roles, size_band, usable_triggers, words)
from .verify import fetch_text, quote_in_text, verify_quote

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
GOLDEN = ROOT / "golden"
RECHECK_DAYS = 7
LINKEDIN_RX = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/?#\s]+/?$", re.I)


def _load(name: str) -> str:
    text = (PROMPTS / name).read_text(encoding="utf-8")
    return text.replace("{TRIGGERS}", (PROMPTS / "_triggers.md").read_text(encoding="utf-8"))


def _prompt(name: str, ctx: dict) -> str:
    return _load(name) + "\n\n# INPUT\n```json\n" + json.dumps(ctx, ensure_ascii=False, indent=1) + "\n```"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _hold(st: AccountState, status: str, reason: str, today: date) -> AccountState:
    st.status, st.hold_reason = status, reason
    st.next_recheck = (today + timedelta(days=RECHECK_DAYS)).isoformat()
    return st


# ======================= отбор =======================

def _account_from_page(page: dict, f: dict) -> Account:
    pr = page.get("properties", {})
    g = lambda key: plain(pr.get(f[key])) if f.get(key) else ""
    return Account(page_id=page["id"].replace("-", ""), account_id=g("account_id"), company=g("company"),
                   segment=g("segment"), priority=g("priority"), status=g("status"), size=g("size"),
                   domain=g("domain"), role=g("role"), fact=g("fact"), hypothesis=g("hypothesis"),
                   value=g("value"), question=g("question"), fallback=g("fallback"), source=g("source"))


def seen_accounts(runs_dir: Path) -> set[str]:
    """Всё, что уже проходило через воронку (холды перепроверяет команда recheck, а не select)."""
    seen = set()
    for f in runs_dir.glob("*/*.json"):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if st.get("status") != "error":
            a = st["account"]
            seen.add(a.get("account_id") or a.get("page_id"))
    return seen


def select(cfg: dict, notion: Notion, runs_dir: Path, limit: int, only_ids: list[str] | None = None,
           segments: dict[str, int] | None = None) -> list[AccountState]:
    """segments — стратифицированный отбор для замера: {сегмент: сколько аккаунтов}."""
    n, sel = cfg["notion"], cfg["selection"]
    fa, fc = n["accounts"], n["contacts"]
    accounts = [_account_from_page(p, fa) for p in notion.query(n["accounts_data_source_id"])]
    by_company: dict[str, list[dict]] = {}
    for cp in notion.query(n["contacts_data_source_id"]):
        pr = cp.get("properties", {})
        keys = relation_ids(pr.get(fc["company"])) if fc.get("company_match") == "relation" \
            else [_norm(plain(pr.get(fc["company"])))]
        for k in keys:
            by_company.setdefault(k, []).append(cp)
    seen = set() if only_ids else seen_accounts(runs_dir)

    out: list[AccountState] = []
    quota = dict(segments or {})
    for a in accounts:
        key = a.account_id or a.page_id
        if only_ids:
            if key not in only_ids and a.company not in only_ids:
                continue
        else:
            if key in seen:
                continue
            if sel.get("priorities") and a.priority not in sel["priorities"]:
                continue
            if a.status in sel.get("exclude_statuses", []) or a.segment in sel.get("exclude_segments", []):
                continue
            if segments is not None:
                if quota.get(a.segment, 0) <= 0:
                    continue
        rows = by_company.get(a.page_id if fc.get("company_match") == "relation" else _norm(a.company), [])
        st = AccountState(account=a)
        for i, cp in enumerate(rows, 1):
            pr = cp.get("properties", {})
            g = lambda key: plain(pr.get(fc[key])) if fc.get(key) else ""
            c = Contact(key=f"c{i}", page_id=cp["id"].replace("-", ""), name=g("name"), title=g("title"),
                        function=g("function"), email=g("email"), linkedin=g("linkedin"), found_by="base")
            if not c.name:
                continue
            if not st.account.country:
                st.account.country = g("country")
            if g("defect"):
                st.skipped_contacts.append(f"{c.name} — брак: {g('defect')}")
                continue
            if g("warm_intro"):
                st.warm_contacts.append(f"{c.name} ({c.title}) — тёплое интро через {g('warm_intro')}")
                continue
            allowed = sel.get("allowed_functions")
            if allowed and c.function and c.function not in allowed:
                st.skipped_contacts.append(f"{c.name} ({c.title}, {c.function}) — функция вне воронки")
                continue
            st.contacts.append(c)
        if sel.get("require_contact", True) and not st.contacts and not st.warm_contacts:
            continue
        out.append(st)
        if segments is not None:
            quota[a.segment] -= 1
            if all(v <= 0 for v in quota.values()):
                break
        elif len(out) >= limit:
            break
    return out


# ======================= исследование: 3 захода =======================

def _verify_against_docs(t: Trigger, docs: dict[str, collect_mod.Doc]) -> str:
    d = docs.get(t.url)
    if d is None:
        return "not_found"
    return "verified" if quote_in_text(t.quote, d.title + " " + d.text) else "not_found"


def _verify_web(t: Trigger, runner: Runner, cfg: dict) -> str:
    v = verify_quote(t.url, t.quote)
    if v == "unreachable" and cfg["research"].get("llm_verify_fallback"):
        v = _llm_verify(t.url, t.quote, runner, cfg)
    return v


class _Excerpt(BaseModel):
    found: bool
    excerpt: str = ""


def _llm_verify(url: str, quote: str, runner: Runner, cfg: dict) -> str:
    prompt = _load("verify_fallback.md") + f"\n\nURL: {url}\nQUOTE: {quote}"
    try:
        res = call_json(runner, prompt, _Excerpt, model=cfg["models"]["verify_fallback"], tools=["WebFetch"], timeout=180)
    except Exception:  # noqa: BLE001
        return "unreachable"
    return "verified_llm" if res.found and quote_in_text(quote, res.excerpt) else "not_found"


def _company_usable(r: Research) -> list[Trigger]:
    return [t for t in usable_triggers(r) if t.type != "MARKET"]


def _absorb(r: Research, res: ExtractResult, rnd: int, verify: Callable[[Trigger], str], today: date,
            max_words: int) -> int:
    known = {t.url + "|" + t.quote for t in r.triggers}
    added = 0
    for t in res.triggers:
        if t.url + "|" + t.quote in known:
            continue
        t.found_in_round = rnd
        t.verification = "too_long" if words(t.quote) > max_words else verify(t)
        r.triggers.append(t)
        added += 1
    if not r.process_a and res.process_a:
        r.process_a = res.process_a
    if not r.process_b and res.process_b:
        r.process_b = res.process_b
    if res.notes:
        r.notes = (r.notes + "\n" if r.notes else "") + f"[заход {rnd}] {res.notes}"
    for i, t in enumerate(r.triggers, 1):
        t.id = f"t{i}"
    apply_tiers(r, today)
    return added


def research(st: AccountState, runner: Runner, cfg: dict, today: date,
             collector: Callable = collect_mod.collect) -> AccountState:
    rc = cfg["research"]
    days, maxw = rc.get("collect_days", 270), rc["max_quote_words"]
    acc = st.account.model_dump(exclude={"page_id"})
    r = Research()

    # Заход 1 — корпус собирает код, модель только извлекает.
    docs = collector(st.account.company, st.account.domain, today, days)
    by_url = {d.url: d for d in docs}
    log = RoundLog(round=1, method="корпус кода: новости, сайт, вакансии, YouTube", collected=collect_mod.counts(docs))
    if docs:
        res = call_json(runner, _prompt("research_extract.md", {
            "today": today.isoformat(), "account": acc,
            "documents": [d.model_dump() for d in docs]}),
            ExtractResult, model=cfg["models"]["research"], timeout=cfg["timeouts"]["research_sec"])
        log.triggers_found = _absorb(r, res, 1, lambda t: _verify_against_docs(t, by_url), today, maxw)
    else:
        log.note = "корпус пуст"
    log.triggers_usable = len(_company_usable(r))
    r.rounds.append(log)

    # Заход 2 — углубление, если проверенных свежих поводов компании меньше двух.
    if len(_company_usable(r)) < 2:
        res = call_json(runner, _prompt("research_deep.md", {
            "today": today.isoformat(), "account": acc,
            "already_known_urls": sorted({t.url for t in r.triggers} | set(by_url))}),
            ExtractResult, model=cfg["models"]["research"], tools=["WebSearch", "WebFetch"],
            timeout=cfg["timeouts"]["research_sec"])
        found = _absorb(r, res, 2, lambda t: _verify_web(t, runner, cfg), today, maxw)
        r.rounds.append(RoundLog(round=2, method="углубление: региональная пресса, партнёры, конференции, посты, реестры",
                                 triggers_found=found, triggers_usable=len(_company_usable(r))))

    # Заход 3 — рыночный повод, если у компании нет ни одного.
    if not _company_usable(r):
        res = call_json(runner, _prompt("research_market.md", {
            "today": today.isoformat(), "account": acc}),
            ExtractResult, model=cfg["models"]["research"], tools=["WebSearch", "WebFetch"],
            timeout=cfg["timeouts"]["research_sec"])
        for t in res.triggers:
            t.type = "MARKET"
        found = _absorb(r, res, 3, lambda t: _verify_web(t, runner, cfg), today, maxw)
        r.rounds.append(RoundLog(round=3, method="рыночный повод: регулятор, рельсы, дедлайны",
                                 triggers_found=found, triggers_usable=len(usable_triggers(r))))

    st.research = r
    plan = make_plan(r)
    if plan is None:
        tried = "; ".join(f"заход {x.round}: {x.method} — найдено {x.triggers_found}, годных {x.triggers_usable}" for x in r.rounds)
        return _hold(st, "on_hold", f"нет проверенного повода ≤270 дней после всех заходов ({tried})", today)
    st.plan, st.status = plan, "planned"
    return st


# ======================= люди снизу вверх: 3 захода =======================

def _domain(st: AccountState) -> str:
    return collect_mod.domain_of(st.account.domain) if st.account.domain else ""


def _valid_web_candidate(c) -> bool:
    """Человека принимаем, только если код сам подтвердил: имя есть на странице-источнике
    или ссылка LinkedIn — профиль, чей адрес совпадает с именем. Слова модели недостаточно."""
    if c.linkedin_url and not LINKEDIN_RX.match(c.linkedin_url):
        return False
    parts = [w for w in re.split(r"\s+", c.name.strip()) if len(w) > 1]
    if len(parts) < 2:
        return False
    if c.source_url.startswith("http"):
        page = fetch_text(c.source_url)
        if page is not None and quote_in_text(c.name, page):
            return True
    return bool(c.linkedin_url) and all(_norm(p)[:4] in _norm(c.linkedin_url) for p in parts[:2])


def people(st: AccountState, runner: Runner, cfg: dict, today: date, provider: Findymail | None = None,
           notion: Notion | None = None) -> AccountState:
    """Снизу вверх по лестнице ролей (roles.yaml): исполнители до цели по размеру, затем владелец процесса,
    затем — если владельца с email нет — ближайший по смыслу. Пишем только тем, кто на лестнице."""
    band, seg = size_band(st.account.size), st.account.segment
    target = executors_target(band)
    sr = segment_roles(seg)
    dom = _domain(st)
    provider = provider or Findymail()
    known = {_norm(c.name) for c in st.contacts}

    def have(tier: str) -> list[Contact]:
        return [c for c in st.contacts if c.email and role_tier(c.title, seg, band) == tier]

    def add(name: str, title: str, linkedin: str, source_url: str, found_by: str, want: str) -> bool:
        if not name or _norm(name) in known or role_tier(title, seg, band) != want:
            return False
        email = ""
        if provider.enabled and dom:
            try:
                e = provider.find_email(name, dom)
                email = e if e and provider.verify(e) else ""
            except RuntimeError as err:
                st.people_log.append(RoundLog(round=0, method="email-поставщик", note=str(err)))
        st.contacts.append(Contact(key=f"c{len(st.contacts) + 1}", name=name, title=title, linkedin=linkedin,
                                   email=email, source_url=source_url, found_by=found_by,
                                   function="Operations" if want == "executor" else ""))
        known.add(_norm(name))
        return True

    titles = {"executor": sr["executor_titles"], "owner": sr["owner_titles"], "nearest": nearest_titles(seg)}
    if band == "small":
        titles["owner"] = ["Founder", "CEO"] + titles["owner"]
    # (кого ищем, способ, описание). Заходы идут, пока ступень не закрыта.
    plan: list[tuple[str, str, str]] = []
    for want, label in (("executor", "исполнители"), ("owner", "владелец процесса"), ("nearest", "ближайший по смыслу")):
        if want == "executor" and not target:
            continue
        if provider.enabled and dom:
            plan.append((want, "provider", f"поставщик: {label}"))
        plan.append((want, "web1", f"веб: {label} — команда, спикеры, профили"))
        if want == "executor":
            plan.append((want, "web2", f"веб: {label} — вакансии, пресса, кейсы партнёров"))

    def closed(want: str) -> bool:
        if want == "executor":
            return len(have("executor")) >= target
        if want == "owner":
            return bool(have("owner"))
        return bool(have("owner") or have("nearest"))

    for want, kind, method in plan:
        if closed(want):
            continue
        added = 0
        if kind == "provider":
            try:
                for e in provider.employees(dom, titles[want]):
                    added += add(e.get("name", ""), e.get("jobTitle", ""), e.get("linkedinUrl", ""),
                                 "findymail", "provider", want)
            except RuntimeError as err:
                method += f" — {err}"
        else:
            res = call_json(runner, _prompt("people.md", {
                "mode": want, "round": 1 if kind == "web1" else 2, "company": st.account.company, "domain": dom,
                "country": st.account.country, "segment": seg, "titles_wanted": titles[want],
                "already_known": sorted(c.name for c in st.contacts)}),
                PeopleResult, model=cfg["models"]["research"], tools=["WebSearch", "WebFetch"],
                timeout=cfg["timeouts"]["research_sec"])
            for c in res.candidates:
                if _valid_web_candidate(c):
                    added += add(c.name, c.title, c.linkedin_url, c.source_url, "web", want)
        st.people_log.append(RoundLog(round=len(st.people_log) + 1, method=method, triggers_found=added,
                                      triggers_usable=len(have(want))))

    if notion is not None:
        write_new_contacts(st, notion, cfg)       # в базу контактов — все найденные, даже без email
    ok, why = people_gate(st.contacts, band, seg)
    st.contacts, skipped = pick_recipients(st.contacts, band, seg)
    st.skipped_contacts += skipped
    if not ok:
        tried = "; ".join(f"{x.method}: +{x.triggers_found}" for x in st.people_log) or "заходы не требовались"
        return _hold(st, "needs_people", f"{why} ({tried})", today)
    st.status = "people_ok"
    return st


def write_new_contacts(st: AccountState, notion: Notion, cfg: dict) -> None:
    """Найденные пайплайном люди → строки в базе контактов (без email — со статусом «нужно обогатить»)."""
    n, fc, w = cfg["notion"], cfg["notion"]["contacts"], cfg["notion"]["contacts"].get("write", {})
    for c in st.contacts:
        if c.page_id or c.found_by == "base":
            continue
        props = {fc["name"]: {"title": [{"type": "text", "text": {"content": c.name}}]},
                 fc["title"]: text_prop(c.title, "rich_text")}
        if fc.get("company_match") == "relation" and st.account.page_id:
            props[fc["company"]] = {"relation": [{"id": st.account.page_id}]}
        if c.linkedin and fc.get("linkedin"):
            props[fc["linkedin"]] = {"url": c.linkedin}
        if c.email and fc.get("email"):
            props[fc["email"]] = {"email": c.email}
        func = w.get("new_leader_function") if contact_level(c.title) == "leader" else w.get("new_contact_function")
        if fc.get("function") and func:
            props[fc["function"]] = {"select": {"name": func}}
        if fc.get("source") and w.get("new_contact_source"):
            props[fc["source"]] = {"select": {"name": w["new_contact_source"]}}
        status = w.get("status_ready") if c.email else w.get("status_needs_enrichment")
        if fc.get("status") and status:
            props[fc["status"]] = {"status": {"name": status}}
        page = notion.create_row(n["contacts_data_source_id"], props)
        c.page_id = page["id"].replace("-", "")


# ======================= письма =======================

def _examples() -> list[dict]:
    ex = []
    for f in sorted(GOLDEN.glob("*.json")):
        g = json.loads(f.read_text(encoding="utf-8"))
        if g.get("use_as_prompt_example"):
            ex.append({"offer": g["expected"]["d1_offer"], "touches": g["packages"][0]["touches"]})
    return ex


def write_context(st: AccountState, cfg: dict) -> dict:
    return {
        "account": {"company": st.account.company, "segment": st.account.segment, "country": st.account.country,
                    "size_band": size_band(st.account.size), "fact": st.account.fact,
                    "hypothesis": st.account.hypothesis, "fallback_question": st.account.fallback},
        "contacts": [c.model_dump(include={"key", "name", "title", "level", "start_day"})
                     for c in st.contacts if c.email],
        "verified_triggers": [t.model_dump(include={"id", "type", "event_date", "tier", "summary", "ops_implication", "quote"})
                              for t in usable_triggers(st.research)],
        "plan": st.plan.model_dump(),
        "process_a": st.research.process_a,
        "process_b": st.research.process_b,
        "research_notes": st.research.notes,
        "sender": cfg["sender"],
        "examples": _examples(),
    }


def write(st: AccountState, runner: Runner, cfg: dict, extra: str = "") -> AccountState:
    base = _prompt("write.md", write_context(st, cfg))
    if extra:
        base += "\n\n# FIX\n" + extra
    problems: list[str] = []
    for _ in range(2):
        prompt = base if not problems else base + "\n\n# CODE CHECKS FAILED — fix all\n" + "\n".join(f"- {x}" for x in problems)
        res = call_json(runner, prompt, WriteResult, model=cfg["models"]["write"], timeout=cfg["timeouts"]["write_sec"])
        problems = _check_all(st, res, cfg)
        st.packages = res.packages
        if not problems:
            break
    st.check_problems = problems
    st.status = "written" if not problems else "check_failed"
    return st


def _check_all(st: AccountState, res: WriteResult, cfg: dict) -> list[str]:
    P = []
    keys = {c.key for c in st.contacts if c.email}
    got = {p.contact_key for p in res.packages}
    if got != keys:
        P.append(f"пакеты должны быть ровно для контактов {sorted(keys)}, получено {sorted(got)}")
    for pkg in res.packages:
        c = next((x for x in st.contacts if x.key == pkg.contact_key), None)
        if c is not None:
            P += [f"{c.name}: {x}" for x in check_package(pkg, c, st.account.fact, st.research, st.plan, cfg["sender"])]
    return P


def review(st: AccountState, runner: Runner, cfg: dict) -> AccountState:
    def _review() -> ReviewResult:
        return call_json(runner, _prompt("review.md", {
            "company": st.account.company, "country": st.account.country,
            "verified_triggers": [t.model_dump(include={"id", "type", "event_date", "tier", "summary", "quote", "url"})
                                  for t in usable_triggers(st.research)],
            "plan": st.plan.model_dump(), "packages": [p.model_dump() for p in st.packages]}),
            ReviewResult, model=cfg["models"]["review"], timeout=cfg["timeouts"]["review_sec"])

    rv = _review()
    if rv.verdict == "FAIL":
        write(st, runner, cfg, extra="The reviewer rejected the previous version:\n"
              + "\n".join(f"- {i.type}: «{i.excerpt}» → {i.fix}" for i in rv.issues))
        if st.status == "written":
            rv = _review()
    st.review = rv
    if st.status == "written":
        st.status = "ready" if rv.verdict == "PASS" else "review_failed"
    return st


# ======================= публикация =======================

def publish(st: AccountState, notion: Notion, cfg: dict) -> AccountState:
    n = cfg["notion"]
    page = notion.create_page(n["output_parent_page_id"], card_title(st), card_blocks(st))
    st.notion_page_url = page.get("url", "")
    fa, w = n["accounts"], n["contacts"].get("write", {})
    props = {}
    if fa.get("funnel_status"):
        props[fa["funnel_status"]] = text_prop("Черновик на ревью", "select")
    if fa.get("funnel_page"):
        props[fa["funnel_page"]] = text_prop(st.notion_page_url, "url")
    if props and st.account.page_id:
        notion.update_props(st.account.page_id, props)
    # Поля эксперимента — на строках контактов, которым написаны письма.
    primary = next((t for t in st.research.triggers if t.id == st.plan.primary_trigger_id), None)
    for pkg in st.packages:
        c = next((x for x in st.contacts if x.key == pkg.contact_key), None)
        if not c or not c.page_id:
            continue
        cp = {}
        for field, value in (("offer_type", st.plan.d1_offer), ("trigger_type", primary.type if primary else ""),
                             ("cohort", st.plan.cohort), ("confidence", primary.confidence if primary else "")):
            if w.get(field) and value:
                cp[w[field]] = {"select": {"name": value}}
        if w.get("card"):
            cp[w["card"]] = {"url": st.notion_page_url or None}
        if w.get("start_day"):
            cp[w["start_day"]] = {"number": c.start_day}
        if cp:
            notion.update_props(c.page_id, cp)
    st.status = "published"
    return st
