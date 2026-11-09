"""Этапы воронки. Порядок, выборку, переходы и проверки решает код; модель — только исполнитель одного вызова."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from .llm import Runner, call_json
from .models import (Account, AccountState, Contact, Research, ReviewResult, WriteResult)
from .notion import Notion, plain, relation_ids, text_prop
from .render import card_blocks, card_title
from .rules import (assign_schedule, check_package, is_fresh, make_plan, size_band,
                    usable_triggers, words)
from .verify import quote_in_text, verify_quote

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
GOLDEN = ROOT / "golden"


def _load(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ---------- отбор ----------

def _account_from_page(page: dict, f: dict) -> Account:
    pr = page.get("properties", {})
    g = lambda key: plain(pr.get(f.get(key, ""))) if f.get(key) else ""
    return Account(page_id=page["id"].replace("-", ""), account_id=g("account_id"), company=g("company"),
                   segment=g("segment"), priority=g("priority"), status=g("status"), size=g("size"),
                   domain=g("domain"), role=g("role"), fact=g("fact"), hypothesis=g("hypothesis"),
                   value=g("value"), question=g("question"), fallback=g("fallback"), source=g("source"))


def already_done(runs_dir: Path) -> set[str]:
    done = set()
    for f in runs_dir.glob("*/*.json"):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if st.get("status") in ("published", "ready"):
            a = st["account"]
            done.add(a.get("account_id") or a.get("page_id"))
    return done


def select(cfg: dict, notion: Notion, runs_dir: Path, limit: int, only_ids: list[str] | None = None) -> list[AccountState]:
    n, sel = cfg["notion"], cfg["selection"]
    fa, fc = n["accounts"], n["contacts"]
    accounts = [_account_from_page(p, fa) for p in notion.query(n["accounts_data_source_id"])]
    contacts_raw = notion.query(n["contacts_data_source_id"])
    done = already_done(runs_dir)

    by_company: dict[str, list[dict]] = {}
    for cp in contacts_raw:
        pr = cp.get("properties", {})
        if fc.get("company_match") == "relation":
            keys = relation_ids(pr.get(fc["company"]))
        else:
            keys = [_norm(plain(pr.get(fc["company"])))]
        for k in keys:
            by_company.setdefault(k, []).append(pr)

    out = []
    for a in accounts:
        key = a.account_id or a.page_id
        if only_ids and key not in only_ids and a.company not in only_ids:
            continue
        if not only_ids:
            if key in done:
                continue
            if sel.get("priorities") and a.priority not in sel["priorities"]:
                continue
            if a.status in sel.get("exclude_statuses", []) or a.segment in sel.get("exclude_segments", []):
                continue
        raw = by_company.get(a.page_id if fc.get("company_match") == "relation" else _norm(a.company), [])
        cs, skipped = [], []
        for i, pr in enumerate(raw, 1):
            c = Contact(key=f"c{i}", name=plain(pr.get(fc["name"])), title=plain(pr.get(fc["title"])),
                        function=plain(pr.get(fc.get("function", ""))) if fc.get("function") else "",
                        email=plain(pr.get(fc.get("email", ""))) if fc.get("email") else "",
                        linkedin=plain(pr.get(fc.get("linkedin", ""))) if fc.get("linkedin") else "")
            if not c.name:
                continue
            allowed = sel.get("allowed_functions")
            if allowed and c.function and c.function not in allowed:
                skipped.append(f"{c.name} ({c.title}, {c.function})")
                continue
            cs.append(c)
        if sel.get("require_contact", True) and not cs:
            continue
        cs = assign_schedule(cs, size_band(a.size))
        out.append(AccountState(account=a, contacts=cs, skipped_contacts=skipped))
        if len(out) >= limit:
            break
    return out


# ---------- исследование ----------

def research(st: AccountState, runner: Runner, cfg: dict, today: date) -> AccountState:
    rc = cfg["research"]
    ctx = {
        "today": today.isoformat(),
        "freshness_days": rc["freshness_days"],
        "account": st.account.model_dump(exclude={"page_id"}),
        "contacts": [{"name": c.name, "title": c.title} for c in st.contacts],
    }
    prompt = _load("research.md") + "\n\n# ВХОДНЫЕ ДАННЫЕ\n```json\n" + json.dumps(ctx, ensure_ascii=False, indent=1) + "\n```"
    r = call_json(runner, prompt, Research, model=cfg["models"]["research"], tools=["WebSearch", "WebFetch"],
                  timeout=cfg["timeouts"]["research_sec"])
    st.research = verify_triggers(r, runner, cfg, today)
    plan = make_plan(st.research)
    if plan is None:
        st.status = "under_researched"
    else:
        st.plan, st.status = plan, "planned"
    return st


def verify_triggers(r: Research, runner: Runner | None, cfg: dict, today: date) -> Research:
    rc = cfg["research"]
    for i, t in enumerate(r.triggers, 1):
        t.id = f"t{i}"
        if words(t.quote) > rc["max_quote_words"]:
            t.verification = "too_long"
        elif not is_fresh(t, today, rc["freshness_days"]):
            t.verification = "stale"
        else:
            t.verification = verify_quote(t.url, t.quote)
            if t.verification == "unreachable" and rc.get("llm_verify_fallback") and runner is not None:
                t.verification = _llm_verify(t.url, t.quote, runner, cfg)
    return r


class _Excerpt(BaseModel):
    found: bool
    excerpt: str = ""


def _llm_verify(url: str, quote: str, runner: Runner, cfg: dict) -> str:
    prompt = (_load("verify_fallback.md") + f"\n\nURL: {url}\nЦИТАТА: {quote}")
    try:
        res = call_json(runner, prompt, _Excerpt, model=cfg["models"]["verify_fallback"], tools=["WebFetch"], timeout=180)
    except Exception:
        return "unreachable"
    return "verified_llm" if res.found and quote_in_text(quote, res.excerpt) else "not_found"


# ---------- письма ----------

def _examples() -> list[dict]:
    ex = []
    for f in sorted(GOLDEN.glob("*.json")):
        g = json.loads(f.read_text(encoding="utf-8"))
        if g.get("use_as_prompt_example"):
            ex.append({"offer": g["expected"]["d1_offer"], "touches": g["packages"][0]["touches"]})
    return ex


def write_context(st: AccountState, cfg: dict) -> dict:
    vt = usable_triggers(st.research)
    return {
        "account": {"company": st.account.company, "segment": st.account.segment,
                    "size_band": size_band(st.account.size), "fact": st.account.fact,
                    "hypothesis": st.account.hypothesis, "fallback_question": st.account.fallback},
        "contacts": [c.model_dump(include={"key", "name", "title", "level", "start_day"}) for c in st.contacts],
        "verified_triggers": [t.model_dump(include={"id", "type", "event_date", "summary", "ops_implication", "quote"}) for t in vt],
        "plan": st.plan.model_dump(),
        "process_a": st.research.process_a,
        "process_b": st.research.process_b,
        "sender": cfg["sender"],
        "examples": _examples(),
    }


def write(st: AccountState, runner: Runner, cfg: dict, extra: str = "") -> AccountState:
    ctx = write_context(st, cfg)
    base = _load("write.md") + "\n\n# ВХОДНЫЕ ДАННЫЕ\n```json\n" + json.dumps(ctx, ensure_ascii=False, indent=1) + "\n```"
    if extra:
        base += "\n\n# ИСПРАВИТЬ\n" + extra
    problems: list[str] = []
    for attempt in range(2):
        prompt = base if not problems else base + "\n\n# ПРОВЕРКИ КОДА НЕ ПРОЙДЕНЫ\n" + "\n".join(f"- {x}" for x in problems)
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
    keys = {c.key for c in st.contacts}
    got = {p.contact_key for p in res.packages}
    if got != keys:
        P.append(f"пакеты должны быть ровно для контактов {sorted(keys)}, получено {sorted(got)}")
    for pkg in res.packages:
        c = next((x for x in st.contacts if x.key == pkg.contact_key), None)
        if c is None:
            continue
        P += [f"{c.name}: {x}" for x in check_package(pkg, c, st.account.fact, st.research, st.plan, cfg["sender"])]
    return P


# ---------- рецензент ----------

def review(st: AccountState, runner: Runner, cfg: dict) -> AccountState:
    def _review() -> ReviewResult:
        ctx = {"company": st.account.company,
               "verified_triggers": [t.model_dump(include={"id", "summary", "quote", "url"}) for t in usable_triggers(st.research)],
               "plan": st.plan.model_dump(),
               "packages": [p.model_dump() for p in st.packages]}
        prompt = _load("review.md") + "\n\n# ВХОДНЫЕ ДАННЫЕ\n```json\n" + json.dumps(ctx, ensure_ascii=False, indent=1) + "\n```"
        return call_json(runner, prompt, ReviewResult, model=cfg["models"]["review"], timeout=cfg["timeouts"]["review_sec"])

    rv = _review()
    if rv.verdict == "FAIL":
        fix = "\n".join(f"- {i.type}: «{i.excerpt}» → {i.fix}" for i in rv.issues)
        write(st, runner, cfg, extra="Рецензент отклонил предыдущую версию:\n" + fix)
        if st.status == "written":
            rv = _review()
    st.review = rv
    if st.status == "written":
        st.status = "ready" if rv.verdict == "PASS" else "review_failed"
    return st


# ---------- публикация ----------

def publish(st: AccountState, notion: Notion, cfg: dict) -> AccountState:
    n = cfg["notion"]
    page = notion.create_page(n["output_parent_page_id"], card_title(st), card_blocks(st))
    st.notion_page_url = page.get("url", "")
    f = n["accounts"]
    props = {}
    if f.get("funnel_status"):
        props[f["funnel_status"]] = text_prop("Черновик на ревью", "select")
    if f.get("funnel_page"):
        props[f["funnel_page"]] = text_prop(st.notion_page_url, "url")
    if props and st.account.page_id:
        notion.update_props(st.account.page_id, props)
    st.status = "published"
    return st
