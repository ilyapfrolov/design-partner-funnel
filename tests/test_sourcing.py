"""Этап source без сети и без модели: дефицит, ячейки, дубли, проверки кандидата, цитаты, размера, региона."""
import json
from datetime import date

from pipeline.sourcing import (Candidate, Qualification, account_props, deficits, hard_reject, next_ids, reg_domain,
                               report, segment_confidence, size_status, source, source_named, split_cells)

FA = {"company": "Компания", "segment": "Сегмент", "status": "Статус", "domain": "Домен", "account_id": "Account ID",
      "priority": "Приоритет", "size": "Размер", "role": "Роль", "fact": "X — факт", "source": "Источник",
      "size_source": "Размер — источник", "notes": "Заметки"}
SEG3, SEG5 = "ICP-3 Cards / issuing / BaaS", "ICP-5 Digital assets / crypto / RWA"
CFG = {
    "notion": {"accounts": FA, "accounts_data_source_id": "x"},
    "models": {"research": "x"}, "timeouts": {"research_sec": 1},
    "sourcing": {"targets": {SEG3: 3, SEG5: 1}, "regions": {"SEA": ["Singapore"], "GCC": ["United Arab Emirates", "UAE"]},
                 "rounds": 3, "per_call": 10, "exclude_statuses": ["Research hold"], "account_id_prefix": "HAIA-FIN-",
                 "priority_by_segment": {SEG3: "P1", SEG5: "P2"}},
}


def row(company, seg, domain, status="Ready for enrichment", aid=""):
    t = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}]}
    return {"id": company, "properties": {
        "Компания": {"type": "title", "title": [{"plain_text": company}]},
        "Сегмент": {"type": "select", "select": {"name": seg}}, "Статус": {"type": "select", "select": {"name": status}},
        "Домен": {"type": "url", "url": domain}, "Account ID": t(aid)}}


ROWS = [row("Highnote", SEG3, "https://highnote.com", aid="HAIA-FIN-0139"),
        row("MoonPay", SEG5, "https://moonpay.com", aid="HAIA-FIN-0190"),
        row("Old", SEG3, "https://old.com", status="Research hold", aid="HAIA-FIN-0700")]


def test_deficit_and_cells():
    from pipeline.sourcing import base_counts
    c = base_counts(ROWS, "Сегмент", "Статус", ["Research hold"])
    assert c == {SEG3: 1, SEG5: 1}
    d = deficits(c, CFG["sourcing"]["targets"])
    assert d == {SEG3: 2}                                   # ICP-5 уже на цели
    assert split_cells({SEG3: 5}, ["SEA", "GCC"]) == [(SEG3, "SEA", 3), (SEG3, "GCC", 2)]


def test_reg_domain_and_ids():
    assert reg_domain("https://app.foo.com/x") == "foo.com"
    assert reg_domain("www.bar.com.sg") == "bar.com.sg"
    assert next_ids(["HAIA-FIN-0139", "HAIA-FIN-0700", "X"], "HAIA-FIN-", 2) == ["HAIA-FIN-0701", "HAIA-FIN-0702"]


def test_source_named_is_label_not_filter():
    c = Candidate(company="Nium", domain="nium.com", source_url="https://list.example/a")
    assert source_named(c, lambda u: "Top fintechs: Nium, Thunes") == "named"
    assert source_named(c, lambda u: "Top fintechs: Thunes") == "not_named"
    assert source_named(c, lambda u: None) == "unreachable"


def _q(**kw):
    base = dict(fit=True, segment=SEG3, country="Singapore", product_scope="Card issuing platform.",
                quote="Launch and manage card programmes for your customers", quote_url="https://acme.sg/cards",
                money_flow="authorisation to settlement", size="51–200 (LinkedIn)",
                size_source="https://www.linkedin.com/company/acme-cards", owner_role="Head of Card Operations")
    return Qualification(**(base | kw))


C = Candidate(company="Acme Cards", domain="acme.sg", country="Singapore", source_url="https://x")
PAGE = lambda u: "Launch and manage card programmes for your customers, globally."


def test_only_junk_is_rejected():
    ok = {SEG3}
    assert hard_reject(_q(), C, ["Singapore"], ok) == ("", "")
    assert hard_reject(_q(country=""), C.model_copy(update={"country": ""}), ["Singapore"], ok) == ("", "")  # регион неизвестен — оставляем
    assert hard_reject(_q(quote="", quote_url="", size="", size_source=""), C, ["Singapore"], ok) == ("", "")  # нет доказательств — не отказ
    assert hard_reject(_q(fit=False, reject_reason="KYC only"), C, ["Singapore"], ok)[0] == "qualify"
    assert hard_reject(_q(country="Brazil"), C, ["Singapore"], ok)[0] == "region"
    assert hard_reject(_q(segment="ICP-1 PSP / acquiring"), C, ["Singapore"], ok)[0] == "segment"


def test_segment_confidence_labels():
    assert segment_confidence(_q(), "acme.sg", PAGE) == "site"
    assert segment_confidence(_q(quote_url="https://news.example/acme"), "acme.sg", PAGE) == "other_source"
    assert segment_confidence(_q(quote="Something else entirely here"), "acme.sg", PAGE) == "inferred"
    assert segment_confidence(_q(quote="", quote_url=""), "acme.sg", PAGE) == "inferred"


def test_size_labels():
    assert size_status(_q(), "Acme Cards", "acme.sg", PAGE) == "linkedin"
    assert size_status(_q(size="11–50 (site)", size_source="https://acme.sg/about"), "Acme Cards", "acme.sg",
                       lambda u: "A team of 11-50 people") == "verified"
    assert size_status(_q(size="11–50 (estimate)", size_source=""), "Acme Cards", "acme.sg", PAGE) == "estimate"
    assert size_status(_q(size="", size_source=""), "Acme Cards", "acme.sg", PAGE) == "unknown"


class FakeRunner:
    """Заход 1 даёт дубль и кандидата, которого нет на странице; заход 2 — двух годных."""
    def __init__(self):
        self.calls = []

    def run(self, prompt, *, model, tools, timeout):
        inp = json.loads(prompt.split("```json\n")[-1].rstrip("`\n"))
        if "find candidate companies" in prompt:
            self.calls.append(("discover", inp["region"], inp["round"]))
            if inp["round"] == 1:
                return json.dumps({"candidates": [
                    {"company": "Highnote", "domain": "highnote.com", "country": "Singapore", "source_url": "https://l/1"},
                    {"company": "Ghost Co", "domain": "ghost.sg", "country": "Singapore", "source_url": "https://l/1"}]})
            return json.dumps({"candidates": [
                {"company": f"Acme {inp['region']}", "domain": f"acme-{inp['region'].lower()}.com", "country": "x", "source_url": "https://l/2"},
                {"company": f"Beta {inp['region']}", "domain": f"beta-{inp['region'].lower()}.com", "country": "x", "source_url": "https://l/2"}]})
        if "You qualify ONE company" in prompt:
            self.calls.append(("qualify", inp["company"]))
            country = "UAE" if "GCC" in inp["company"] else "Singapore"
            return json.dumps(_q(country=country, quote_url=f"https://{inp['domain']}/cards",
                                 size_source=f"https://www.linkedin.com/company/{inp['company'].lower().replace(' ', '-')}").model_dump())
        raise AssertionError(prompt[:60])


def fake_fetch(url):
    if url == "https://l/1":
        return "List: Highnote"
    if url == "https://l/2":
        return "List: Acme SEA, Beta SEA, Acme GCC, Beta GCC"
    return "Launch and manage card programmes for your customers"


def test_source_end_to_end():
    r = FakeRunner()
    res = source(CFG, ROWS, r, date(2026, 11, 9), fetch=fake_fetch)
    assert res.deficits == {SEG3: 2}
    # заход 1: Highnote — дубль; Ghost Co не назван на странице, но это пометка, а не отказ → принят
    assert [a.company for a in res.accepted] == ["Ghost Co", "Acme GCC"]
    assert res.accepted[0].source_named == "not_named" and res.accepted[0].priority == "P1"
    stages = {(x.company, x.stage) for x in res.rejected}
    assert ("Highnote", "dedupe") in stages
    assert ("qualify", "Highnote") not in r.calls                           # дубль не квалифицируется
    props = account_props(res.accepted[1], CFG)
    assert props["Сегмент"] == {"select": {"name": SEG3}} and props["Приоритет"] == {"select": {"name": "P1"}}
    assert "кодом не проверено" in props["Размер"]["rich_text"][0]["text"]["content"]
    assert "Отклонено по причинам" in report(res)


def test_limit_stops_early():
    res = source(CFG, ROWS, FakeRunner(), date(2026, 11, 9), fetch=fake_fetch, limit=1)
    assert len(res.accepted) == 1
