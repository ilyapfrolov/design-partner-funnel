"""Карточка аккаунта в Notion и markdown-отчёт. Каждый текст — в двух форматах: READ (проза) + COPY (код-блок)."""
from __future__ import annotations

from .models import AccountState, Touch
from .rules import d3_offer

DAY_TITLE = {"D1": "День 1 · Email", "D1-2": "День 1–2 · LinkedIn-заметка",
             "D3": "День 3 · Email в том же треде", "D5": "День 5 · Переадресация"}


def _rt(text: str, link: str | None = None, bold: bool = False) -> list[dict]:
    out = []
    for i in range(0, max(len(text), 1), 1900):
        item = {"type": "text", "text": {"content": text[i:i + 1900]}}
        if link:
            item["text"]["link"] = {"url": link}
        if bold:
            item["annotations"] = {"bold": True}
        out.append(item)
    return out


def h(text: str, level: int = 2) -> dict:
    k = f"heading_{level}"
    return {"object": "block", "type": k, k: {"rich_text": _rt(text)}}


def p(text: str, link: str | None = None, bold: bool = False) -> dict:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rt(text, link, bold)}}


def bullet(parts: list[tuple[str, str | None]]) -> dict:
    rt = []
    for text, link in parts:
        rt += _rt(text, link)
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rt}}


def code(text: str) -> dict:
    return {"object": "block", "type": "code", "code": {"language": "plain text", "rich_text": _rt(text)}}


def callout(text: str) -> dict:
    return {"object": "block", "type": "callout", "callout": {"rich_text": _rt(text), "icon": {"emoji": "🧪"}}}


def copy_text(t: Touch) -> str:
    return (f"Subject: {t.subject}\n\n{t.body}" if t.channel == "email" and t.subject else t.body)


def touch_blocks(t: Touch) -> list[dict]:
    out = [h(DAY_TITLE[t.day], 3)]
    if t.subject:
        out.append(p(f"Тема: {t.subject}", bold=True))
    out += [p(par) for par in t.body.split("\n\n")]   # READ
    out.append(code(copy_text(t)))                     # COPY
    return out


def card_title(st: AccountState) -> str:
    offer = st.plan.d1_offer if st.plan else "—"
    return f"{st.account.company} · {st.account.segment} · {offer}"


def card_blocks(st: AccountState) -> list[dict]:
    a, r, plan = st.account, st.research, st.plan
    B = [callout("Черновик на ревью. Пайплайн ничего не отправляет. Отправка — вручную, после одобрения.")]
    B.append(h("Аккаунт"))
    B.append(p(f"{a.segment} · размер: {a.size or 'не указан'} · {a.domain}"))
    if plan and r:
        B.append(h("Поводы"))
        for t in r.triggers:
            mark = "✓" if t.verification in ("verified", "verified_llm") else "✗"
            B.append(bullet([(f"{mark} {t.id} · {t.type} · {t.event_date} · {t.confidence} · {t.verification} — {t.summary} ", None),
                             ("источник", t.url)]))
        B.append(p(f"Оффер дня 1: {plan.d1_offer} (основной повод {plan.primary_trigger_id})", bold=True))
    if st.skipped_contacts:
        B.append(p("Пропущенные контакты (функция вне воронки): " + "; ".join(st.skipped_contacts)))
    for pkg in st.packages:
        c = next(x for x in st.contacts if x.key == pkg.contact_key)
        B.append(h(f"{c.name} · {c.title}"))
        B.append(p(f"{c.email or '—'} · уровень: {c.level} · цепочка с дня {c.start_day}"))
        if c.linkedin:
            B.append(p("LinkedIn", link=c.linkedin))
        if plan:
            B.append(p(f"День 3: основание {pkg.d3_basis}, оффер {d3_offer(plan, pkg.d3_basis)}"))
        for t in pkg.touches:
            B += touch_blocks(t)
    return B


def markdown_report(st: AccountState) -> str:
    a = st.account
    L = [f"# {a.company} — {st.status}", ""]
    if st.error:
        L += [f"Ошибка: {st.error}", ""]
    if st.research:
        L.append("## Поводы")
        for t in st.research.triggers:
            L.append(f"- {t.id} {t.type} {t.event_date} {t.verification}: {t.summary} — {t.url}")
        L.append("")
        L.append("## Покрытие источников")
        for k, v in st.research.coverage.items():
            L.append(f"- {k}: {v}")
        L.append("")
    if st.plan:
        L += [f"Оффер дня 1: {st.plan.d1_offer}; допустимые основания дня 3: {st.plan.d3_options or 'B'}", ""]
    if st.check_problems:
        L += ["## Проверки кода"] + [f"- {x}" for x in st.check_problems] + [""]
    if st.review:
        L += [f"## Рецензент: {st.review.verdict}"] + [f"- {i.type}: «{i.excerpt}» → {i.fix}" for i in st.review.issues] + [""]
    for pkg in st.packages:
        L.append(f"## Контакт {pkg.contact_key} (день 3: {pkg.d3_basis})")
        for t in pkg.touches:
            L += [f"### {DAY_TITLE[t.day]}", "```", copy_text(t), "```", ""]
    return "\n".join(L)
