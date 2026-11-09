"""Оркестратор воронки дизайн-партнёров.

  python run.py measure                       # замер на 30 аккаунтах по сегментам: исследование + люди, без писем
  python run.py all --batch 20 --dry-run      # батч целиком, без записи в Notion
  python run.py all --batch 20                # батч с записью карточек и найденных людей в Notion
  python run.py recheck                       # перепроверить холды, у которых подошла дата
  python run.py sample --run runs/<id> --n 5  # случайные аккаунты для ручной проверки
  python run.py eval [--live]                 # регрессия на эталонах
  Отдельные этапы: select / research / people / write / review / publish --run runs/<id>
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from pipeline import stages
from pipeline.llm import ClaudeCLI
from pipeline.models import AccountState
from pipeline.providers import Findymail
from pipeline.render import markdown_report
from pipeline.rules import size_band

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"


def load_cfg() -> dict:
    p = ROOT / "config.yaml"
    if not p.exists():
        sys.exit("Нет config.yaml. Скопируйте config.example.yaml и заполните (README → Настройка).")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _key(st: AccountState) -> str:
    return (st.account.account_id or st.account.page_id or st.account.company).replace("/", "_")


def save(run: Path, st: AccountState) -> None:
    (run / f"{_key(st)}.json").write_text(st.model_dump_json(indent=1), encoding="utf-8")
    (run / f"{_key(st)}.md").write_text(markdown_report(st), encoding="utf-8")


def load_run(run: Path) -> list[AccountState]:
    return [AccountState.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(run.glob("*.json"))]


def each(run: Path, states: list[AccountState], want: str, fn, parallel: int) -> list[AccountState]:
    pos = [i for i, s in enumerate(states) if s.status == want]

    def one(st: AccountState) -> AccountState:
        try:
            st = fn(st)
        except Exception as e:  # noqa: BLE001
            st.status, st.error = "error", f"{type(e).__name__}: {str(e)[:400]}"
        save(run, st)
        print(f"  {st.account.company}: {st.status}" + (f" — {st.hold_reason or st.error}" if st.hold_reason or st.error else ""))
        return st

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        done = list(ex.map(one, [states[i] for i in pos]))
    out = list(states)
    for i, st in zip(pos, done):
        out[i] = st
    return out


def rate(states: list[AccountState], ok: set[str], base: set[str]) -> float:
    pool = [s for s in states if s.status in base | ok]
    return (sum(s.status in ok for s in pool) / len(pool)) if pool else 1.0


def summary(run: Path, states: list[AccountState]) -> None:
    c = Counter(s.status for s in states)
    L = [f"# Батч {run.name}", "", "| Статус | Аккаунтов |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(c.items())]
    L += ["", "| Аккаунт | Сегмент | Размер | Когорта | Оффер | Статус | Причина холда | Карточка |", "|---|---|---|---|---|---|---|---|"]
    for s in states:
        L.append(f"| {s.account.company} | {s.account.segment} | {size_band(s.account.size)} | "
                 f"{s.plan.cohort if s.plan else '—'} | {s.plan.d1_offer if s.plan else '—'} | {s.status} | "
                 f"{s.hold_reason or '—'} | {s.notion_page_url or '—'} |")
    (run / "SUMMARY.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:4 + len(c)]))


def segment_report(states: list[AccountState], g: dict) -> tuple[str, bool]:
    """Замер по сегментам: доля с поводом, доля рыночной когорты, доля с исполнителями."""
    by = defaultdict(list)
    for s in states:
        by[s.account.segment].append(s)
    L = ["| Сегмент | Аккаунтов | С поводом | Из них рыночные | Люди готовы | Холд: нет повода | Холд: нет людей |", "|---|---|---|---|---|---|---|"]
    ok = True
    for seg, ss in sorted(by.items()):
        n = len(ss)
        planned = [s for s in ss if s.plan]
        market = [s for s in planned if s.plan.cohort == "market"]
        people_ok = [s for s in ss if s.status == "people_ok"]
        tr, pr = len(planned) / n, (len(people_ok) / len(planned) if planned else 0)
        flag = tr >= g["min_trigger_rate"] and pr >= g["min_people_rate"]
        ok &= flag
        L.append(f"| {seg} | {n} | {tr:.0%} | {len(market)} | {pr:.0%} | "
                 f"{sum(s.status == 'on_hold' for s in ss)} | {sum(s.status == 'needs_people' for s in ss)} |" + ("" if flag else " ⚠"))
    return "\n".join(L), ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["select", "research", "people", "write", "review", "publish",
                                      "all", "measure", "recheck", "sample", "eval"])
    ap.add_argument("--run")
    ap.add_argument("--batch", type=int)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--ids", default="")
    ap.add_argument("--dry-run", action="store_true", help="не писать в Notion")
    ap.add_argument("--force", action="store_true", help="не останавливаться на стоп-условиях")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    if a.stage == "eval":
        from evals.run_eval import run as run_eval
        return run_eval(live=a.live, cfg=load_cfg() if a.live else None, runner=ClaudeCLI() if a.live else None)

    cfg = load_cfg()
    runner, provider = ClaudeCLI(), Findymail()
    par, g = cfg["selection"].get("parallel", 3), cfg["gates"]
    today = date.today()
    RUNS.mkdir(exist_ok=True)
    notion = None
    if not a.dry_run or a.stage in ("select", "all", "measure"):
        from pipeline.notion import Notion
        notion = Notion(cfg["notion"]["api_version"])
    write_notion = None if a.dry_run else notion

    def new_run() -> Path:
        run = RUNS / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run.mkdir()
        return run

    if a.stage in ("select", "all", "measure"):
        run = new_run()
        ids = [x.strip() for x in a.ids.split(",") if x.strip()] or None
        segs = None
        if a.stage == "measure":
            per = max(1, round(cfg["gates"]["measure_accounts"] / max(1, len(cfg["gates"]["measure_segments"]))))
            segs = {s: per for s in cfg["gates"]["measure_segments"]}
        states = stages.select(cfg, notion, RUNS, a.batch or cfg["selection"]["batch_size"], ids, segs)
        for s in states:
            save(run, s)
        print(f"Отобрано {len(states)} аккаунтов → {run}")
        if a.stage == "select":
            return 0
    elif a.stage == "recheck":
        latest: dict[str, AccountState] = {}
        for f in sorted(RUNS.glob("*/*.json")):
            st = AccountState.model_validate_json(f.read_text(encoding="utf-8"))
            latest[_key(st)] = st
        due = [s for s in latest.values() if s.status in ("on_hold", "needs_people")
               and s.next_recheck and s.next_recheck <= today.isoformat()]
        if not due:
            print("Холдов к перепроверке нет.")
            return 0
        run = new_run()
        states = []
        for s in due:
            s = s.model_copy(update={"status": "selected", "research": None, "plan": None, "packages": [],
                                     "hold_reason": "", "next_recheck": "", "people_log": []})
            save(run, s)
            states.append(s)
        print(f"Перепроверка {len(states)} холдов → {run}")
    else:
        if not a.run:
            sys.exit("Нужен --run runs/<id>")
        run = Path(a.run)
        states = load_run(run)

    if a.stage == "sample":
        ready = [s for s in states if s.status in ("ready", "published")]
        for s in random.sample(ready, min(a.n, len(ready))):
            print(f"{s.account.company} → {run / (_key(s) + '.md')}")
        return 0

    flow = a.stage in ("all", "measure", "recheck")
    if a.stage == "research" or flow:
        print("Исследование (3 захода):")
        states = each(run, states, "selected", lambda s: stages.research(s, runner, cfg, today), par)
        r = rate(states, {"planned"}, {"on_hold"})
        if flow and a.stage != "measure" and r < g["min_trigger_rate"] and not a.force:
            summary(run, states)
            print(f"\nСТОП: повод найден у {r:.0%} аккаунтов < {g['min_trigger_rate']:.0%}. Письма не пишутся. "
                  f"Разберите холды в {run}/SUMMARY.md или запустите с --force.")
            return 2
    if a.stage == "people" or flow:
        print("Люди снизу вверх:")
        states = each(run, states, "planned",
                      lambda s: stages.people(s, runner, cfg, today, provider, write_notion), par)
        r = rate(states, {"people_ok"}, {"needs_people"})
        if a.stage == "measure":
            rep, ok = segment_report(states, g)
            (run / "MEASURE.md").write_text(rep, encoding="utf-8")
            summary(run, states)
            print("\n" + rep + ("\n\nПорог пройден во всех сегментах." if ok else "\n\n⚠ Порог не пройден — дорабатываем до писем."))
            return 0 if ok else 2
        if flow and r < g["min_people_rate"] and not a.force:
            summary(run, states)
            print(f"\nСТОП: исполнители найдены у {r:.0%} аккаунтов < {g['min_people_rate']:.0%}. "
                  f"Обогатите контакты со статусом «нужно обогатить» и запустите recheck, или --force.")
            return 2
    if a.stage == "write" or flow:
        print("Письма:")
        states = each(run, states, "people_ok", lambda s: stages.write(s, runner, cfg), par)
    if a.stage == "review" or flow:
        print("Рецензент:")
        states = each(run, states, "written", lambda s: stages.review(s, runner, cfg), par)
        fail = 1 - rate(states, {"ready"}, {"review_failed", "check_failed"})
        if flow and fail > g["max_review_fail"] and not a.force:
            summary(run, states)
            print(f"\nСТОП: отказов рецензента/проверок {fail:.0%} > {g['max_review_fail']:.0%}. "
                  f"Публикация остановлена — разберите причины и превратите их в правила (SPEC §8).")
            return 2
    if (a.stage == "publish" or flow) and write_notion is not None:
        print("Публикация в Notion:")
        states = each(run, states, "ready", lambda s: stages.publish(s, write_notion, cfg), 1)
    summary(run, states)
    print(f"\nОтчёт: {run / 'SUMMARY.md'}. Ручная выборка: python run.py sample --run {run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
