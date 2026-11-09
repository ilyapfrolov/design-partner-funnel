"""Тесты без сети и без модели: правила, проверки, свежесть, заходы исследования, гейт снизу вверх, полный проход."""
import json
from datetime import date

import pytest

from evals.run_eval import SENDER, golden_research, load_golden, offline_problems
from pipeline import stages
from pipeline.collect import Doc
from pipeline.models import (Account, AccountState, Contact, ContactPackage, Research, Trigger)
from pipeline.rules import (apply_tiers, assign_schedule, check_package, make_plan, people_gate, size_band, tier)
from pipeline.verify import quote_in_text

G = load_golden()
TODAY = date(2026, 11, 9)
CFG = {
    "sender": SENDER,
    "models": {"research": "x", "write": "x", "review": "x", "verify_fallback": "x"},
    "timeouts": {"research_sec": 1, "write_sec": 1, "review_sec": 1},
    "research": {"collect_days": 270, "max_quote_words": 25, "llm_verify_fallback": False},
}


# ---------- эталоны ----------

@pytest.mark.parametrize("name", list(G))
def test_golden_passes_offline(name):
    assert offline_problems(G[name]) == []


# ---------- свежесть и выбор оффера ----------

def _t(id, type, d, v="verified"):
    return Trigger(id=id, type=type, event_date=d, url=f"https://x/{id}", quote="q", summary="s",
                   ops_implication="o", confidence="EVENT", verification=v)


@pytest.mark.parametrize("d,typ,exp", [
    ("2026-10-01", "SCALE", "hook"), ("2026-06-01", "SCALE", "state"), ("2025-12-17", "BUILDING", "context"),
    ("2025-06-01", "SCALE", "stale"), ("2026-12-31", "MARKET", "hook"), ("2026-12-31", "CHANGE", "invalid"),
    ("2027-06-01", "MARKET", "invalid"), ("bad", "SCALE", "invalid"),
])
def test_tiers(d, typ, exp):
    assert tier(_t("t1", typ, d), TODAY) == exp


def test_offer_by_type_hook_by_freshness():
    r = apply_tiers(Research(triggers=[_t("t1", "BUILDING", "2026-04-30"), _t("t2", "SCALE", "2026-10-09")]), TODAY)
    p = make_plan(r)
    assert (p.primary_trigger_id, p.d1_offer, p.hook_trigger_id, p.hook_tier) == ("t1", "EMBED", "t2", "hook")


def test_old_event_never_used():
    r = apply_tiers(Research(triggers=[_t("t1", "BUILDING", "2025-12-17")]), TODAY)
    assert make_plan(r) is None


def test_market_only_when_no_company_trigger():
    r = apply_tiers(Research(triggers=[_t("t1", "MARKET", "2026-12-31"), _t("t2", "CHANGE", "2026-07-17")]), TODAY)
    assert make_plan(r).cohort == "company"
    r = apply_tiers(Research(triggers=[_t("t1", "MARKET", "2026-12-31")]), TODAY)
    p = make_plan(r)
    assert (p.cohort, p.d1_offer) == ("market", "BACKTEST")


def test_congrats_only_for_fresh_hook():
    g = json.loads(json.dumps(G["nomod"]))  # повод июля — state, поздравлять нельзя
    r = golden_research(g)
    plan = make_plan(r)
    pkg = ContactPackage.model_validate(g["packages"][0])
    pkg.touches[0].body = pkg.touches[0].body.replace("I saw Nomod's", "Congrats on Nomod's")
    c = Contact.model_validate(g["contacts"][0])
    assert any("поздравление" in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


# ---------- размер, уровни, гейт снизу вверх ----------

def test_size_bands():
    assert [size_band(s) for s in ["11–50 (LinkedIn)", "51–200", "1,001–5,000", "3 (YC W26)", "NOT VERIFIED"]] == \
           ["small", "mid", "large", "small", "unknown"]


def test_bottom_up_schedule_and_gate():
    head = Contact(key="c1", name="A B", title="Chief Operating Officer", email="a@x.com")
    ex = Contact(key="c2", name="C D", title="Payment Operations Manager", email="c@x.com")
    assert people_gate([head], "large")[0] is False          # одного руководителя мало
    assert people_gate([head], "small")[0] is True
    cs = assign_schedule([head, ex], "large")
    assert [c.start_day for c in cs] == [3, 1]
    assert people_gate(cs, "large")[0] is True
    no_email = ex.model_copy(update={"email": ""})
    assert people_gate([head, no_email], "mid")[0] is False   # исполнитель без email не считается


# ---------- проверки текстов ----------

def _parts(name):
    g = G[name]
    r = golden_research(g)
    return g, r, make_plan(r), ContactPackage.model_validate(g["packages"][0]), Contact.model_validate(g["contacts"][0])


@pytest.mark.parametrize("insert,needle", [
    ("Haia does this. ", "HAIA"), ("Could we book a quick call? ", "созвон"), ("Our customers love it. ", "клиентах"),
    ("It is free for design partners. ", "цены"), ("Only 3 spots left. ", "лимит"), ("We cut exceptions by 40%. ", "числа без источника"),
])
def test_forbidden_content_caught(insert, needle):
    g, r, plan, pkg, c = _parts("hitpay")
    pkg.touches[0].body = pkg.touches[0].body.replace("My guess", insert + "My guess")
    assert any(needle in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


def test_d3_must_use_unused_other_trigger():
    g, r, plan, pkg, c = _parts("hitpay")
    pkg.d3_basis = "B"
    assert any("гипотеза B не допускается" in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


def test_embed_phrase_only_for_embed():
    g, r, plan, pkg, c = _parts("nomod")
    pkg.touches[0].body = pkg.touches[0].body.replace("From an export", "Under your brand. From an export")
    assert any("только для EMBED" in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


def test_quote_matching_tolerates_typography():
    page = "<p>Dibsy’s MCP roadmap will allow deeper AI-powered workflows across the payment lifecycle, including payment recovery, support automation, settlement visibility, reconciliation, fraud prevention.</p>"
    assert quote_in_text(G["dibsy"]["research"]["triggers"][0]["quote"], page)
    assert not quote_in_text("Dibsy raised a Series B round of funding", page)


# ---------- заходы исследования и полный проход ----------

class FakeRunner:
    def __init__(self, extract=None, deep=None, market=None, people=None, packages=None):
        self.extract, self.deep, self.market = extract or [], deep or [], market or []
        self.people, self.packages = people or [], packages or []
        self.seen = []

    def run(self, prompt, *, model, tools, timeout):
        if "You extract outreach triggers" in prompt:
            self.seen.append("extract"); return json.dumps({"triggers": self.extract, "process_a": "A", "process_b": "B"})
        if "DEEPER search" in prompt:
            self.seen.append("deep"); return json.dumps({"triggers": self.deep})
        if "Find MARKET triggers" in prompt:
            self.seen.append("market"); return json.dumps({"triggers": self.market})
        if "Find real people" in prompt:
            self.seen.append("people"); return json.dumps({"candidates": self.people})
        if "You write a one-week outreach" in prompt:
            self.seen.append("write"); return json.dumps({"packages": self.packages})
        if "independent reviewer" in prompt:
            self.seen.append("review"); return json.dumps({"verdict": "PASS", "issues": []})
        raise AssertionError(prompt[:80])


def _docs_for(triggers):
    return [Doc(source="news", url=t["url"], date=t["event_date"][:10], title="t", text=t["quote"]) for t in triggers]


def _state(g):
    return AccountState(account=Account.model_validate(g["account"]), contacts=[Contact.model_validate(c) for c in g["contacts"]])


def test_round1_only_when_two_fresh_company_triggers(monkeypatch):
    g = G["hitpay"]
    trig = g["research"]["triggers"]          # порядок сохраняется → id после перенумерации совпадают с эталоном
    runner = FakeRunner(extract=trig, packages=g["packages"])
    st = stages.research(_state(g), runner, CFG, TODAY, collector=lambda *a: _docs_for(trig))
    assert runner.seen == ["extract"] and st.status == "planned" and st.plan.d1_offer == "EMBED"
    st = stages.people(st, runner, CFG, TODAY)          # small: исполнитель не обязателен
    assert st.status == "people_ok"
    st = stages.write(st, runner, CFG)
    assert st.status == "written", st.check_problems
    st = stages.review(st, runner, CFG)
    assert st.status == "ready"


def test_all_three_rounds_before_hold(monkeypatch):
    monkeypatch.setattr(stages, "verify_quote", lambda url, quote: "verified")
    runner = FakeRunner()
    st = stages.research(_state(G["nomod"]), runner, CFG, TODAY, collector=lambda *a: [])
    assert runner.seen == ["deep", "market"] and st.status == "on_hold"
    assert "заход 1" in st.hold_reason and "заход 3" in st.hold_reason and st.next_recheck == "2026-11-16"


def test_market_round_rescues_account(monkeypatch):
    monkeypatch.setattr(stages, "verify_quote", lambda url, quote: "verified")
    m = [t for t in G["nextpay"]["research"]["triggers"] if t["type"] == "MARKET"]
    st = stages.research(_state(G["nextpay"]), FakeRunner(market=m), CFG, TODAY, collector=lambda *a: [])
    assert st.status == "planned" and st.plan.cohort == "market" and st.plan.d1_offer == "BACKTEST"


def test_unverified_quote_rejected():
    g = G["nomod"]
    t = dict(g["research"]["triggers"][0], quote="Nomod raised a hundred million dollars")
    docs = _docs_for(g["research"]["triggers"])
    st = stages.research(_state(g), FakeRunner(extract=[t]), CFG, TODAY, collector=lambda *a: docs)
    assert all(x.verification == "not_found" for x in st.research.triggers if x.found_in_round == 1)


def test_large_account_held_without_executor(monkeypatch):
    g = json.loads(json.dumps(G["nomod"]))
    g["account"]["size"] = "201–500 (LinkedIn)"
    st = _state(g)
    st.research = golden_research(g)
    st.plan, st.status = make_plan(st.research), "planned"
    runner = FakeRunner(people=[{"name": "Ghost Person", "title": "Payment Operations Manager",
                                 "source_url": "https://example.com/team", "linkedin_url": "https://www.linkedin.com/in/someone-else"}])
    monkeypatch.setattr(stages, "fetch_text", lambda url: "no names here")
    st = stages.people(st, runner, CFG, TODAY)
    assert st.status == "needs_people" and "исполнитель" in st.hold_reason
    assert runner.seen.count("people") == 2                 # оба веб-захода выполнены


def test_new_account_without_contacts_searches_owner(monkeypatch):
    """Аккаунт из этапа source: контактов нет. Малая компания → ищем владельца процесса."""
    g = json.loads(json.dumps(G["nomod"]))
    g["account"]["size"] = "11–50 (LinkedIn)"
    g["account"]["domain"] = "https://nomod.com"
    st = AccountState(account=Account.model_validate(g["account"]), contacts=[])
    st.research = golden_research(g)
    st.plan, st.status = make_plan(st.research), "planned"

    class P:  # поставщик с email
        enabled = True
        def employees(self, dom, titles=None): return [{"name": "Omar Founder", "jobTitle": "Co-Founder & CEO", "linkedinUrl": ""}]
        def find_email(self, n, d): return "omar@nomod.com"
        def verify(self, e): return True

    runner = FakeRunner()
    st = stages.people(st, runner, CFG, TODAY, provider=P())
    assert st.status == "people_ok" and st.contacts[0].title == "Co-Founder & CEO" and st.contacts[0].start_day == 1
    assert runner.seen == []                                     # поставщик нашёл — веб не понадобился


# ---------- лестница ролей ----------

from pipeline.rules import pick_recipients, role_tier  # noqa: E402

C3, C1 = "ICP-3 Cards / issuing / BaaS", "ICP-1 PSP / acquiring"


@pytest.mark.parametrize("title,seg,band,exp", [
    ("Card Operations Manager", C3, "large", "executor"),
    ("Head of Card Programs", C3, "large", "owner"),
    ("COO", C3, "large", "owner"),
    ("Co-Founder & CEO", C1, "small", "owner"),
    ("Co-Founder & CEO", C1, "large", "nearest"),        # CEO крупной компании — только как ближайший
    ("Head of Product", C3, "mid", "nearest"),
    ("Chief Risk Officer", C1, "mid", "nearest"),        # risk — ближайший для PSP
    ("Chief Risk Officer", C3, "mid", ""),               # для карт risk не в лестнице
    ("Head of Sales", C1, "small", ""),
    ("Payment Operations Engineer", C1, "mid", ""),      # разработка — никогда
    ("Business Operations Director", C1, "small", "owner"),
])
def test_role_tiers(title, seg, band, exp):
    assert role_tier(title, seg, band) == exp


def test_recipients_bottom_up_with_nearest_fallback():
    ex = Contact(key="c1", name="A A", title="Card Operations Manager", email="a@x.com")
    pm = Contact(key="c2", name="B B", title="Head of Product", email="b@x.com")
    sales = Contact(key="c3", name="C C", title="VP Sales", email="c@x.com")
    out, skipped = pick_recipients([ex, pm, sales], "large", C3)
    assert [(c.name, c.tier, c.start_day) for c in out] == [("A A", "executor", 1), ("B B", "nearest", 3)]
    assert any("роль вне лестницы" in s for s in skipped)
    head = Contact(key="c4", name="D D", title="Head of Card Operations", email="d@x.com")
    out, skipped = pick_recipients([ex, pm, head], "large", C3)
    assert [c.name for c in out] == ["A A", "D D"]                         # владелец есть — ближайший не нужен
    assert any("B B" in s and "ступень уже занята" in s for s in skipped)
