"""Регрессия на эталонных аккаунтах (SPEC.md §10).

Офлайн: правила выбора оффера дают ожидаемый результат, одобренные тексты проходят все проверки кода.
Live (--live): писатель и рецензент на эталонных исследованиях; сверка оффера, проверок и вердикта.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.models import Account, AccountState, Contact, ContactPackage, Research
from pipeline.rules import check_package, d3_offer, make_plan

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "golden"
SENDER = {"name": "Ilia Frolov", "title": "CRO & Product Architect, HAIA", "site": "haia.finance", "short": "Ilia"}


def load_golden() -> dict[str, dict]:
    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(GOLDEN.glob("*.json"))}


def offline_problems(g: dict, sender: dict = SENDER) -> list[str]:
    r = Research.model_validate(g["research"])
    plan = make_plan(r)
    exp = g["expected"]
    P = []
    if plan is None:
        return ["нет плана"]
    if plan.d1_offer != exp["d1_offer"]:
        P.append(f"оффер дня 1: {plan.d1_offer} ≠ {exp['d1_offer']}")
    for raw in g["packages"]:
        pkg = ContactPackage.model_validate(raw)
        c = Contact.model_validate(g["contacts"][0] | {"key": pkg.contact_key})
        if pkg.d3_basis != exp["d3_basis"]:
            P.append(f"основание дня 3: {pkg.d3_basis} ≠ {exp['d3_basis']}")
        if d3_offer(plan, pkg.d3_basis) != exp["d3_offer"]:
            P.append(f"оффер дня 3: {d3_offer(plan, pkg.d3_basis)} ≠ {exp['d3_offer']}")
        P += check_package(pkg, c, g["account"]["fact"], r, plan, sender)
    return P


def run(live: bool = False, cfg: dict | None = None, runner=None) -> int:
    G = load_golden()
    lines, fails = ["# Eval эталонов", ""], 0
    for name, g in G.items():
        P = offline_problems(g)
        fails += bool(P)
        lines.append(f"- офлайн {name}: {'PASS' if not P else 'FAIL — ' + '; '.join(P)}")
    if live:
        from pipeline import stages
        for name, g in G.items():
            st = AccountState(account=Account.model_validate(g["account"]),
                              contacts=[Contact.model_validate(c) for c in g["contacts"]],
                              research=Research.model_validate(g["research"]))
            st.plan = make_plan(st.research)
            st.status = "planned"
            try:
                st = stages.write(st, runner, cfg)
                if st.status == "written":
                    st = stages.review(st, runner, cfg)
            except Exception as e:  # noqa: BLE001
                st.status, st.error = "error", str(e)[:300]
            pkg = st.packages[0] if st.packages else None
            ok = (st.status == "ready" and pkg is not None
                  and d3_offer(st.plan, pkg.d3_basis) == g["expected"]["d3_offer"])
            fails += not ok
            lines.append(f"- live {name}: {st.status} · оффер {st.plan.d1_offer} · день 3 {pkg.d3_basis if pkg else '—'}"
                         + (f" · проверки: {st.check_problems}" if st.check_problems else "")
                         + (f" · рецензент: {[i.type for i in st.review.issues]}" if st.review and st.review.issues else "")
                         + (f" · {st.error}" if st.error else ""))
    report = "\n".join(lines)
    (ROOT / "evals" / "last_report.md").write_text(report, encoding="utf-8")
    print(report)
    return 1 if fails else 0
