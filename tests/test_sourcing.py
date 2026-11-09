"""Этап source без сети и без модели: дефицит, ячейки, дубли, проверки кандидата, цитаты, размера, региона."""
import json
from datetime import date

from pipeline.sourcing import (Candidate, Qualification, account_props, check_candidate, check_qualification,
                               check_size, deficits, next_ids, reg_domain, report, source, split_cells)

FA = {"company": "Компания", "segment": "Сегмент", "status": "Статус", "domain": "Домен", "account_id": "Account ID",
      "priority": "Приоритет", "size": "Размер", "role": "Роль", "fact": "X — факт", "source": "Источник",
      "size_source": "Размер — источник", "notes": "Заметки"}
SEG3, SEG5 = "ICP-3 Cards / issuing / BaaS", "ICP-5 Digital assets / crypto / RWA"
CFG = {
    "notion": {"accounts": FA, "accounts_data_source_id": "x"},
    "models": {"research": "x"}, "timeouts": {"research_sec": 1},
    "sourcing": {"targets": {SEG3: 3, SEG5: 1}, "regions": {"SEA": ["Singapore"], "GCC": ["United Arab Emirates", "UAE"]},
                 "rounds": 3, "per_call": 10, "exclude_statuses": ["Research hold"], "account_id_prefix": "HAIA-FIN-"},
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


def test_candidate_must_be_named_on_source():
    c = Candidate(company="Nium", domain="nium.com", source_url="https://list.example/a")
    assert check_candidate(c, lambda u: "Top fintechs: Nium, Thunes, Aspire") == ""
    assert check_candidate(c, lambda u: "Top fintechs: Thunes, Aspire") == "компания не названа на странице-источнике"
    assert check_candidate(c, lambda u: None) == "страница-источник недоступна"


def _q(**kw):
    base = dict(fit=True, segment=SEG3, country="Singapore", product_scope="Card issuing platform.",
                quote="Launch and manage card programmes for your customers", quote_url="https://acme.sg/cards",
                money_flow="authorisation to settlement", size="51–200 (LinkedIn)",
                size_source="https://www.linkedin.com/company/acme-cards", owner_role="Head of Card Operations")
    return Qualification(**(base | kw))


C = Candidate(company="Acme Cards", domain="acme.sg", country="Singapore", source_url="https://x")
PAGE = lambda u: "Launch and manage card programmes for your customers, globally."


def test_qualification_accepts_verified():
    assert check_qualification(_q(), C, SEG3, ["Singapore"], {SEG3}, PAGE) == ("", "", "linkedin_not_fetchable")


def test_qualification_rejections():
    cases = [
        (_q(fit=False, reject_reason="KYC only"), "qualify"),
        (_q(segment="ICP-1 PSP / acquiring"), "segment"),
        (_q(quote_url="https://medium.com/acme"), "quote"),
        (_q(quote="We are the largest bank in the world"), "quote"),
        (_q(country="Brazil"), "region"),
        (_q(size="", size_source=""), "size"),
        (_q(size_source="https://www.linkedin.com/company/someone-else"), "size"),
    ]
    for q, stage in cases:
        assert check_qualification(q, C, SEG3, ["Singapore"], {SEG3}, PAGE)[0] == stage, q


def test_size_verified_on_open_page():
    q = _q(size="11–50 (company site)", size_source="https://acme.sg/about")
    assert check_size(q, "Acme Cards", "acme.sg", lambda u: "A team of 11-50 people")[0] == "verified"
    assert check_size(q, "Acme Cards", "acme.sg", lambda u: "A growing team")[1] == "цифры размера не найдены в источнике"


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
            country = "Singapore" if "SEA" in inp["company"] else "UAE"
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
    assert [a.company for a in res.accepted] == ["Acme SEA", "Acme GCC"]      # по 1 на регион, дальше дефицит закрыт
    stages = {(x.company, x.stage) for x in res.rejected}
    assert ("Highnote", "dedupe") in stages and ("Ghost Co", "discover") in stages
    assert ("qualify", "Highnote") not in r.calls                           # дубль не квалифицируется
    props = account_props(res.accepted[0], CFG)
    assert props["Сегмент"] == {"select": {"name": SEG3}} and "кодом не проверено" in props["Размер"]["rich_text"][0]["text"]["content"]
    assert "Отклонено по причинам" in report(res)


def test_limit_stops_early():
    res = source(CFG, ROWS, FakeRunner(), date(2026, 11, 9), fetch=fake_fetch, limit=1)
    assert len(res.accepted) == 1
