"""Карточка аккаунта в Notion и markdown-отчёт. Каждый текст — в двух форматах: READ (проза) + COPY (код-блок)."""
from __future__ import annotations

from .models import AccountState, Touch
from .rules import d3_offer

DAY_TITLE = {"D1": "День 1 · Email", "D1-2": "День 1–2 · LinkedIn-заметка",
             "D3": "День 3 · Email в том же треде", "D5": "День 5 · Переадресация"}
TIER_RU = {"hook": "≤60 дн", "state": "61–270 дн", "context": "271–365 дн", "stale": "старше года", "invalid": "без даты"}


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
    return f"Subject: {t.subject}\n\n{t.body}" if t.channel == "email" and t.subject else t.body


def touch_blocks(t: Touch) -> list[dict]:
    out = [h(DAY_TITLE[t.day], 3)]
    if t.subject:
        out.append(p(f"Тема: {t.subject}", bold=True))
    out += [p(par) for par in t.body.split("\n\n")]   # READ
    out.append(code(copy_text(t)))                     # COPY
    return out


def card_title(st: AccountState) -> str:
    offer = st.plan.d1_offer if st.plan else "—"
    cohort = " · MARKET" if st.plan and st.plan.cohort == "market" else ""
    return f"{st.account.company} · {st.account.segment} · {offer}{cohort}"


def card_blocks(st: AccountState) -> list[dict]:
    a, r, plan = st.account, st.research, st.plan
    B = [callout("Черновик на ревью. Пайплайн ничего не отправляет. Отправка — вручную, после одобрения.")]
    B.append(h("Аккаунт"))
    B.append(p(f"{a.segment} · {a.country or 'страна не указана'} · размер: {a.size or 'не указан'} · {a.domain}"))
    if plan and r:
        B.append(p(f"Когорта: {plan.cohort} · оффер дня 1: {plan.d1_offer} · основа оффера: {plan.primary_trigger_id} · "
                   f"зацепка: {plan.hook_trigger_id} ({TIER_RU[plan.hook_tier]})", bold=True))
        B.append(h("Поводы"))
        for t in r.triggers:
            mark = "✓" if t.verification in ("verified", "verified_llm") and t.tier in ("hook", "state") else "✗"
            B.append(bullet([(f"{mark} {t.id} · {t.type} · {t.event_date} ({TIER_RU[t.tier]}) · {t.verification} · "
                              f"заход {t.found_in_round} — {t.summary} ", None), ("источник", t.url)]))
        B.append(h("Заходы исследования", 3))
        for x in r.rounds:
            B.append(bullet([(f"{x.round}. {x.method}: собрано {x.collected or '—'}, найдено {x.triggers_found}, годных {x.triggers_usable}", None)]))
    if st.people_log:
        B.append(h("Поиск людей снизу вверх", 3))
        for x in st.people_log:
            B.append(bullet([(f"{x.method}: +{x.triggers_found}, исполнителей с email: {x.triggers_usable} {x.note}", None)]))
    if st.warm_contacts:
        B.append(p("Тёплый путь (не в холодной цепочке): " + "; ".join(st.warm_contacts), bold=True))
    if st.skipped_contacts:
        B.append(p("Пропущены: " + "; ".join(st.skipped_contacts)))
    no_email = [c for c in st.contacts if not c.email]
    if no_email:
        B.append(p("Найдены, нужно обогатить email: " + "; ".join(f"{c.name} ({c.title})" for c in no_email)))
    for pkg in st.packages:
        c = next(x for x in st.contacts if x.key == pkg.contact_key)
        B.append(h(f"{c.name} · {c.title}"))
        B.append(p(f"{c.email or '—'} · уровень: {c.level} · цепочка с дня {c.start_day} · найден: {c.found_by}"))
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
    if st.hold_reason:
        L += [f"**Холд:** {st.hold_reason} · перепроверка {st.next_recheck}", ""]
    if st.error:
        L += [f"Ошибка: {st.error}", ""]
    if st.research:
        L.append("## Заходы исследования")
        L += [f"- {x.round}. {x.method}: собрано {x.collected or '—'}, найдено {x.triggers_found}, годных {x.triggers_usable} {x.note}"
              for x in st.research.rounds]
        L += ["", "## Поводы"]
        L += [f"- {t.id} {t.type} {t.event_date} [{TIER_RU[t.tier]}] {t.verification}: {t.summary} — {t.url}"
              for t in st.research.triggers]
        L.append("")
    if st.plan:
        L += [f"Когорта {st.plan.cohort}; оффер дня 1: {st.plan.d1_offer} (основа {st.plan.primary_trigger_id}, "
              f"зацепка {st.plan.hook_trigger_id} {TIER_RU[st.plan.hook_tier]}); день 3: {st.plan.d3_options or 'B'}", ""]
    if st.people_log:
        L += ["## Поиск людей"] + [f"- {x.method}: +{x.triggers_found}; исполнителей с email {x.triggers_usable} {x.note}"
                                   for x in st.people_log] + [""]
    L += ["## Контакты"] + [f"- {c.name} · {c.title} · {c.level} · день {c.start_day} · {c.email or 'нет email'} · {c.found_by}"
                            for c in st.contacts]
    L += [f"- тёплый путь: {w}" for w in st.warm_contacts] + [f"- пропущен: {s}" for s in st.skipped_contacts] + [""]
    if st.check_problems:
        L += ["## Проверки кода"] + [f"- {x}" for x in st.check_problems] + [""]
    if st.review:
        L += [f"## Рецензент: {st.review.verdict}"] + [f"- {i.type}: «{i.excerpt}» → {i.fix}" for i in st.review.issues] + [""]
    for pkg in st.packages:
        L.append(f"## Контакт {pkg.contact_key} (день 3: {pkg.d3_basis})")
        for t in pkg.touches:
            L += [f"### {DAY_TITLE[t.day]}", "```", copy_text(t), "```", ""]
    return "\n".join(L)
