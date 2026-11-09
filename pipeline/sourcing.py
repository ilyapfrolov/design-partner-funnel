"""Этап source — наполнение базы аккаунтов (верх воронки). SPEC §12.

Код решает: сколько компаний нужно в каждой ячейке «сегмент × регион», сколько заходов делать,
кого считать дублем, чью цитату и чей размер считать подтверждёнными, что записать в Notion.
Модель: (1) предлагает кандидатов со ссылкой на список, где компания названа; (2) квалифицирует
одного кандидата по определениям ICP с дословной цитатой с сайта компании.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from .collect import domain_of
from .llm import Runner, call_json
from .notion import Notion, plain, text_prop
from .rules import size_band
from .verify import fetch_text, quote_in_text

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
LINKEDIN_CO = re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/company/([^/?#\s]+)/?", re.I)

# Стратегии заходов поиска: каждая следующая — другой тип источника.
STRATEGIES = {
    1: "regulator registers and licence lists for the countries in this region (payment institutions, e-money, "
       "card issuers, VASPs, investment / securities licences — whatever fits the segment)",
    2: "curated lists: fintech rankings and awards (e.g. Forbes / CB Insights / regional fintech 50 lists), "
       "fintech association member directories, exhibitor and speaker lists of regional fintech conferences",
    3: "recent news: funding rounds, licence approvals, launches and partnerships in this segment and region "
       "in the last 12 months; partner pages of card schemes, BaaS and custody providers that list clients",
}


# ---------- схемы ответов модели ----------

class Candidate(BaseModel):
    company: str
    domain: str
    country: str = ""
    source_url: str               # страница списка / реестра / новости, где компания названа
    evidence: str = ""            # дословная строка с этой страницы


class CandidateResult(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    note: str = ""


class Qualification(BaseModel):
    fit: bool
    segment: str = ""             # точное название сегмента из определений
    country: str = ""
    product_scope: str = ""       # что делает компания, своими словами, одна фраза
    quote: str = ""               # дословно с сайта компании, ≤30 слов, подтверждает сегмент
    quote_url: str = ""           # страница на домене компании
    money_flow: str = ""          # какой денежный процесс у них есть (для X — факт)
    size: str = ""                # «51–200 (LinkedIn)» или «11–50 (company site)»
    size_source: str = ""
    owner_role: str = ""          # кто владеет операциями этого процесса
    reject_reason: str = ""


# ---------- результат кода ----------

class SourcedAccount(BaseModel):
    company: str
    domain: str
    segment: str
    region: str
    country: str
    product_scope: str
    quote: str
    quote_url: str
    money_flow: str
    size: str
    size_source: str
    size_check: str               # verified / linkedin_not_fetchable
    owner_role: str
    found_via: str
    round: int
    account_id: str = ""
    page_id: str = ""


class Rejection(BaseModel):
    company: str
    domain: str = ""
    cell: str
    stage: str                    # discover / dedupe / site / qualify / quote / segment / region / size
    reason: str


class CellLog(BaseModel):
    segment: str
    region: str
    need: int
    rounds: list[dict] = Field(default_factory=list)
    accepted: int = 0


class SourceRun(BaseModel):
    today: str
    base_counts: dict[str, int]
    deficits: dict[str, int]
    cells: list[CellLog] = Field(default_factory=list)
    accepted: list[SourcedAccount] = Field(default_factory=list)
    rejected: list[Rejection] = Field(default_factory=list)


# ---------- служебное ----------

def _prompt(name: str, ctx: dict) -> str:
    text = (PROMPTS / name).read_text(encoding="utf-8")
    text = text.replace("{ICP}", (PROMPTS / "_icp.md").read_text(encoding="utf-8"))
    return text + "\n\n# INPUT\n```json\n" + json.dumps(ctx, ensure_ascii=False, indent=1) + "\n```"


def norm_name(s: str) -> str:
    s = re.sub(r"\b(inc|ltd|llc|pte|plc|limited|group|holdings?|technologies|technology|fintech|bank)\b", "",
               (s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def reg_domain(url_or_domain: str) -> str:
    d = domain_of(url_or_domain or "")
    parts = d.split(".")
    # app.foo.com → foo.com; foo.com.sg / foo.co.id → foo.com.sg
    if len(parts) >= 3 and parts[-2] in ("com", "co", "net", "org", "gov", "ac") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else d


def base_counts(rows: list[dict], seg_field: str, status_field: str, exclude_statuses: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in rows:
        pr = p.get("properties", {})
        if plain(pr.get(status_field)) in exclude_statuses:
            continue
        seg = plain(pr.get(seg_field))
        out[seg] = out.get(seg, 0) + 1
    return out


def deficits(counts: dict[str, int], targets: dict[str, int]) -> dict[str, int]:
    return {s: max(0, t - counts.get(s, 0)) for s, t in targets.items() if t - counts.get(s, 0) > 0}


def split_cells(defs: dict[str, int], regions: list[str]) -> list[tuple[str, str, int]]:
    """Дефицит сегмента делится поровну по регионам; остаток — первым регионам."""
    cells = []
    for seg, need in defs.items():
        q, r = divmod(need, len(regions))
        for i, reg in enumerate(regions):
            n = q + (1 if i < r else 0)
            if n:
                cells.append((seg, reg, n))
    return cells


def next_ids(existing: list[str], prefix: str, n: int) -> list[str]:
    nums = [int(m.group(1)) for x in existing if (m := re.match(re.escape(prefix) + r"(\d+)$", x or ""))]
    start = max(nums, default=0) + 1
    width = max(4, len(str(start + n)))
    return [f"{prefix}{str(start + i).zfill(width)}" for i in range(n)]


# ---------- проверки кода ----------

def check_candidate(c: Candidate, fetch: Callable[[str], str | None]) -> str:
    """Пусто — кандидат прошёл; иначе причина отказа."""
    if not c.domain or "." not in domain_of(c.domain):
        return "нет домена"
    page = fetch(c.source_url) if c.source_url.startswith("http") else None
    if page is None:
        return "страница-источник недоступна"
    name = c.company.strip()
    if not (quote_in_text(name, page) or norm_name(name) and norm_name(name) in re.sub(r"[^a-z0-9]", "", page.lower())):
        return "компания не названа на странице-источнике"
    return ""


def check_size(q: Qualification, company: str, domain: str, fetch: Callable[[str], str | None]) -> tuple[str, str]:
    """(статус, причина). verified — числа есть на странице; linkedin_not_fetchable — страница компании
    в LinkedIn, чей адрес совпадает с компанией или доменом (LinkedIn закрыт для кода)."""
    if size_band(q.size) == "unknown":
        return "", "размер не указан"
    if not q.size_source.startswith("http"):
        return "", "нет ссылки на размер"
    m = LINKEDIN_CO.match(q.size_source)
    if m:
        slug = norm_name(m.group(2))
        if slug and (slug in norm_name(company) or norm_name(company) in slug or slug in reg_domain(domain).replace(".", "")):
            return "linkedin_not_fetchable", ""
        return "", "страница LinkedIn не совпадает с компанией"
    page = fetch(q.size_source)
    if page is None:
        return "", "источник размера недоступен"
    nums = re.findall(r"\d[\d,]*", q.size)
    if nums and all(n.replace(",", "") in page.replace(",", "") for n in nums[:2]):
        return "verified", ""
    return "", "цифры размера не найдены в источнике"


def check_qualification(q: Qualification, c: Candidate, seg: str, region_countries: list[str], open_segments: set[str],
                        fetch: Callable[[str], str | None]) -> tuple[str, str, str]:
    """(stage, reason, size_check). stage пустой — принято."""
    if not q.fit:
        return "qualify", q.reject_reason or "не подходит по определениям ICP", ""
    if q.segment not in open_segments:
        return "segment", f"сегмент «{q.segment}» вне дефицита (искали «{seg}»)", ""
    if reg_domain(q.quote_url) != reg_domain(c.domain):
        return "quote", "цитата не с сайта компании", ""
    if len(q.quote.split()) > 30 or not q.quote.strip():
        return "quote", "цитата пустая или длиннее 30 слов", ""
    page = fetch(q.quote_url)
    if page is None:
        return "quote", "страница с цитатой недоступна коду", ""
    if not quote_in_text(q.quote, page):
        return "quote", "цитата не найдена на странице", ""
    country = (q.country or c.country).strip().lower()
    if region_countries and country not in [x.lower() for x in region_countries]:
        return "region", f"страна «{q.country or c.country}» вне региона", ""
    size_check, why = check_size(q, c.company, c.domain, fetch)
    if not size_check:
        return "size", why, ""
    return "", "", size_check


# ---------- этап ----------

def source(cfg: dict, rows: list[dict], runner: Runner, today: date,
           fetch: Callable[[str], str | None] = fetch_text, on_accept: Callable[[SourcedAccount], None] | None = None,
           limit: int | None = None) -> SourceRun:
    """limit — сколько компаний принять за прогон (пробный запуск); по умолчанию весь дефицит."""
    sc, fa = cfg["sourcing"], cfg["notion"]["accounts"]
    counts = base_counts(rows, fa["segment"], fa["status"], sc.get("exclude_statuses", []))
    defs = deficits(counts, sc["targets"])
    run = SourceRun(today=today.isoformat(), base_counts=counts, deficits=defs)

    known_domains = {reg_domain(plain(p.get("properties", {}).get(fa["domain"]))) for p in rows} - {""}
    known_names = {norm_name(plain(p.get("properties", {}).get(fa["company"]))) for p in rows} - {""}
    remaining = dict(defs)
    regions = sc["regions"]

    def full() -> bool:
        return limit is not None and len(run.accepted) >= limit

    for seg, reg, need in split_cells(defs, list(regions)):
        if full():
            break
        log = CellLog(segment=seg, region=reg, need=need)
        run.cells.append(log)
        cell = f"{seg} × {reg}"
        for rnd in range(1, sc.get("rounds", 3) + 1):
            if log.accepted >= need or remaining.get(seg, 0) <= 0 or full():
                break
            res = call_json(runner, _prompt("source_discover.md", {
                "segment": seg, "region": reg, "countries": regions[reg], "round": rnd,
                "strategy": STRATEGIES.get(rnd, STRATEGIES[3]),
                "how_many": min(sc.get("per_call", 15), (need - log.accepted) * 3),
                "exclude_domains": sorted(known_domains)[:400]}),
                CandidateResult, model=cfg["models"]["research"], tools=["WebSearch", "WebFetch"],
                timeout=cfg["timeouts"]["research_sec"])
            stats = {"round": rnd, "strategy": STRATEGIES.get(rnd, STRATEGIES[3]).split(":")[0],
                     "proposed": len(res.candidates), "accepted": 0}
            for c in res.candidates:
                if log.accepted >= need or remaining.get(seg, 0) <= 0 or full():
                    break
                rd, nn = reg_domain(c.domain), norm_name(c.company)
                if rd in known_domains or (nn and nn in known_names):
                    run.rejected.append(Rejection(company=c.company, domain=rd, cell=cell, stage="dedupe", reason="уже в базе"))
                    continue
                why = check_candidate(c, fetch)
                if why:
                    run.rejected.append(Rejection(company=c.company, domain=rd, cell=cell, stage="discover", reason=why))
                    continue
                if fetch(f"https://{domain_of(c.domain)}") is None:
                    run.rejected.append(Rejection(company=c.company, domain=rd, cell=cell, stage="site", reason="сайт не открывается"))
                    continue
                q = call_json(runner, _prompt("source_qualify.md", {
                    "company": c.company, "domain": domain_of(c.domain), "country_hint": c.country,
                    "target_segment": seg, "region_countries": regions[reg]}),
                    Qualification, model=cfg["models"]["research"], tools=["WebSearch", "WebFetch"],
                    timeout=cfg["timeouts"]["research_sec"])
                stage, why, size_check = check_qualification(q, c, seg, regions[reg], {s for s, v in remaining.items() if v > 0}, fetch)
                known_domains.add(rd)
                known_names.add(nn)
                if stage:
                    run.rejected.append(Rejection(company=c.company, domain=rd, cell=cell, stage=stage, reason=why))
                    continue
                acc = SourcedAccount(company=c.company.strip(), domain=f"https://{rd}", segment=q.segment, region=reg,
                                     country=q.country or c.country, product_scope=q.product_scope, quote=q.quote,
                                     quote_url=q.quote_url, money_flow=q.money_flow, size=q.size,
                                     size_source=q.size_source, size_check=size_check,
                                     owner_role=q.owner_role or "Head of Operations", found_via=c.source_url, round=rnd)
                run.accepted.append(acc)
                remaining[q.segment] -= 1
                if q.segment == seg:
                    log.accepted += 1
                stats["accepted"] += 1
                if on_accept:
                    on_accept(acc)
            log.rounds.append(stats)
    return run


# ---------- запись в Notion ----------

def account_props(a: SourcedAccount, cfg: dict) -> dict:
    fa, sc = cfg["notion"]["accounts"], cfg["sourcing"]
    size = a.size if a.size_check == "verified" else f"{a.size} — LinkedIn, кодом не проверено"
    fact = (f"Documented / source-reported product scope: {a.product_scope} Money flow: {a.money_flow} "
            f"Evidence: \"{a.quote}\"")
    notes = (f"Добавлено этапом source {date.today().isoformat()} · регион {a.region}, {a.country} · "
             f"найдено через {a.found_via} (заход {a.round}). Гипотеза и ценность не заполнялись: воронка "
             f"исследует аккаунт сама.")
    props = {fa["company"]: {"title": [{"type": "text", "text": {"content": a.company}}]},
             fa["segment"]: {"select": {"name": a.segment}},
             fa["priority"]: {"select": {"name": sc.get("priority", "P1")}},
             fa["status"]: {"select": {"name": sc.get("status", "Ready for enrichment")}},
             fa["size"]: text_prop(size, "rich_text"),
             fa["domain"]: text_prop(a.domain, "url"),
             fa["role"]: text_prop(a.owner_role, "rich_text"),
             fa["fact"]: text_prop(fact, "rich_text"),
             fa["source"]: text_prop(a.quote_url, "url")}
    if fa.get("account_id") and a.account_id:
        props[fa["account_id"]] = text_prop(a.account_id, "rich_text")
    if fa.get("size_source"):
        props[fa["size_source"]] = text_prop(a.size_source, "url")
    if fa.get("notes"):
        props[fa["notes"]] = text_prop(notes, "rich_text")
    return props


def write_accounts(run: SourceRun, rows: list[dict], notion: Notion, cfg: dict) -> None:
    fa = cfg["notion"]["accounts"]
    existing = [plain(p.get("properties", {}).get(fa["account_id"])) for p in rows] if fa.get("account_id") else []
    todo = [a for a in run.accepted if not a.page_id]
    for a, aid in zip(todo, next_ids(existing, cfg["sourcing"].get("account_id_prefix", "HAIA-FIN-"), len(todo))):
        a.account_id = aid
        page = notion.create_row(cfg["notion"]["accounts_data_source_id"], account_props(a, cfg))
        a.page_id = page["id"].replace("-", "")


# ---------- отчёт ----------

def report(run: SourceRun) -> str:
    L = [f"# Наполнение базы · {run.today}", "", "## База и дефицит", "", "| Сегмент | В базе | Дефицит | Добавлено |", "|---|---|---|---|"]
    added: dict[str, int] = {}
    for a in run.accepted:
        added[a.segment] = added.get(a.segment, 0) + 1
    for seg in sorted(set(run.base_counts) | set(run.deficits)):
        L.append(f"| {seg} | {run.base_counts.get(seg, 0)} | {run.deficits.get(seg, 0)} | {added.get(seg, 0)} |")
    L += ["", "## Ячейки «сегмент × регион»", "", "| Ячейка | Нужно | Принято | Заходы (предложено → принято) |", "|---|---|---|---|"]
    for c in run.cells:
        r = "; ".join(f"{x['round']}: {x['strategy']} {x['proposed']}→{x['accepted']}" for x in c.rounds)
        L.append(f"| {c.segment} × {c.region} | {c.need} | {c.accepted} | {r} |")
    L += ["", "## Принятые", "", "| Компания | Сегмент | Страна | Размер | Проверка размера | Цитата | Найдено через |", "|---|---|---|---|---|---|---|"]
    for a in run.accepted:
        L.append(f"| [{a.company}]({a.domain}) | {a.segment} | {a.country} | {a.size} | {a.size_check} | "
                 f"[{a.quote[:80]}]({a.quote_url}) | [источник]({a.found_via}) |")
    by: dict[str, int] = {}
    for r in run.rejected:
        by[f"{r.stage}: {r.reason}"] = by.get(f"{r.stage}: {r.reason}", 0) + 1
    L += ["", "## Отклонено по причинам", "", "| Причина | Сколько |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in sorted(by.items(), key=lambda x: -x[1])]
    return "\n".join(L)
