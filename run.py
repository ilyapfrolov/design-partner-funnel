"""Оркестратор воронки дизайн-партнёров. Один запуск — один этап (или all) на батч аккаунтов.

Примеры:
  python run.py all --batch 5 --dry-run        # всё, кроме записи в Notion; результат в runs/<id>/
  python run.py all --batch 20                 # с публикацией карточек в Notion
  python run.py select --batch 20              # только отбор → новый runs/<id>/
  python run.py research --run runs/<id>
  python run.py write --run runs/<id>
  python run.py review --run runs/<id>
  python run.py publish --run runs/<id>
  python run.py sample --run runs/<id> --n 5   # случайные аккаунты для ручной проверки
  python run.py eval                           # офлайн-проверка эталонов
  python run.py eval --live                    # писатель + рецензент на эталонных исследованиях
"""
from __future__ import annotations

import argparse
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from pipeline import stages
from pipeline.llm import ClaudeCLI
from pipeline.models import AccountState
from pipeline.render import markdown_report

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"

ORDER = ["selected", "planned", "written", "ready", "published"]


def load_cfg() -> dict:
    p = ROOT / "config.yaml"
    if not p.exists():
        sys.exit("Нет config.yaml. Скопируйте config.example.yaml и заполните (README → Настройка).")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def save(run: Path, st: AccountState) -> None:
    key = (st.account.account_id or st.account.page_id or st.account.company).replace("/", "_")
    (run / f"{key}.json").write_text(st.model_dump_json(indent=1), encoding="utf-8")
    (run / f"{key}.md").write_text(markdown_report(st), encoding="utf-8")


def load_run(run: Path) -> list[AccountState]:
    return [AccountState.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(run.glob("*.json"))]


def each(run: Path, states: list[AccountState], want: str, fn, parallel: int) -> list[AccountState]:
    """Применить этап к аккаунтам в статусе want. Ошибка одного аккаунта не останавливает батч."""
    pos = [i for i, s in enumerate(states) if s.status == want]
    todo = [states[i] for i in pos]

    def one(st: AccountState) -> AccountState:
        try:
            st = fn(st)
        except Exception as e:  # noqa: BLE001
            st.status, st.error = "error", f"{type(e).__name__}: {str(e)[:400]}"
        save(run, st)
        print(f"  {st.account.company}: {st.status}" + (f" — {st.error}" if st.error else ""))
        return st

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        done = list(ex.map(one, todo))
    out = list(states)
    for i, st in zip(pos, done):
        out[i] = st
    return out


def summary(run: Path, states: list[AccountState]) -> None:
    counts: dict[str, int] = {}
    for s in states:
        counts[s.status] = counts.get(s.status, 0) + 1
    lines = [f"# Батч {run.name}", "", "| Статус | Аккаунтов |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "| Аккаунт | Сегмент | Оффер дня 1 | Статус | Карточка |", "|---|---|---|---|---|"]
    for s in states:
        lines.append(f"| {s.account.company} | {s.account.segment} | {s.plan.d1_offer if s.plan else '—'} | "
                     f"{s.status} | {s.notion_page_url or '—'} |")
    (run / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:4 + len(counts)]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["select", "research", "write", "review", "publish", "all", "sample", "eval"])
    ap.add_argument("--run", help="папка батча runs/<id>")
    ap.add_argument("--batch", type=int, help="сколько аккаунтов взять (по умолчанию из config)")
    ap.add_argument("--ids", default="", help="Account ID или названия компаний через запятую")
    ap.add_argument("--dry-run", action="store_true", help="не писать в Notion")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    if a.stage == "eval":
        from evals.run_eval import run as run_eval
        return run_eval(live=a.live, cfg=load_cfg() if a.live else None, runner=ClaudeCLI() if a.live else None)

    cfg = load_cfg()
    runner = ClaudeCLI()
    par = cfg["selection"].get("parallel", 3)
    today = date.today()
    RUNS.mkdir(exist_ok=True)

    if a.stage in ("select", "all"):
        from pipeline.notion import Notion
        notion = Notion(cfg["notion"]["api_version"])
        ids = [x.strip() for x in a.ids.split(",") if x.strip()] or None
        run = RUNS / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run.mkdir()
        states = stages.select(cfg, notion, RUNS, a.batch or cfg["selection"]["batch_size"], ids)
        for s in states:
            save(run, s)
        print(f"Отобрано {len(states)} аккаунтов → {run}")
        if a.stage == "select":
            return 0
    else:
        if not a.run:
            sys.exit("Нужен --run runs/<id>")
        run = Path(a.run)
        states = load_run(run)

    if a.stage == "sample":
        ready = [s for s in states if s.status in ("ready", "published")]
        for s in random.sample(ready, min(a.n, len(ready))):
            print(f"\n=== {s.account.company} → {run / ((s.account.account_id or s.account.page_id) + '.md')}")
        return 0

    if a.stage in ("research", "all"):
        print("Исследование:")
        states = each(run, states, "selected", lambda s: stages.research(s, runner, cfg, today), par)
    if a.stage in ("write", "all"):
        print("Письма:")
        states = each(run, states, "planned", lambda s: stages.write(s, runner, cfg), par)
    if a.stage in ("review", "all"):
        print("Рецензент:")
        states = each(run, states, "written", lambda s: stages.review(s, runner, cfg), par)
    if a.stage in ("publish", "all") and not a.dry_run:
        from pipeline.notion import Notion
        notion = Notion(cfg["notion"]["api_version"])
        print("Публикация в Notion:")
        states = each(run, states, "ready", lambda s: stages.publish(s, notion, cfg), 1)
    summary(run, states)
    print(f"\nОтчёт: {run / 'SUMMARY.md'}. Перед отправкой: python run.py sample --run {run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
