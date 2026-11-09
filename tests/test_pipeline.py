"""Тесты без сети и без модели: правила, проверки, проверка цитат и полный проход с подменённой моделью."""
import json
from datetime import date

import pytest

from evals.run_eval import SENDER, load_golden, offline_problems
from pipeline import stages
from pipeline.models import (Account, AccountState, Contact, ContactPackage, Research, Trigger)
from pipeline.rules import (assign_schedule, check_package, make_plan, size_band)
from pipeline.verify import quote_in_text

G = load_golden()
CFG = {
    "sender": SENDER,
    "models": {"research": "x", "write": "x", "review": "x", "verify_fallback": "x"},
    "timeouts": {"research_sec": 1, "write_sec": 1, "review_sec": 1},
    "research": {"freshness_days": 365, "max_quote_words": 25, "llm_verify_fallback": False},
}


@pytest.mark.parametrize("name", list(G))
def test_golden_passes_offline(name):
    assert offline_problems(G[name]) == []


def _t(id, type, d="2026-09-01"):
    return Trigger(id=id, type=type, event_date=d, url="u", quote="q", summary="s", ops_implication="o",
                   confidence="EVENT", verification="verified")


def test_offer_priority_and_d3_options():
    plan = make_plan(Research(triggers=[_t("t1", "SCALE"), _t("t2", "CHANGE"), _t("t3", "BUILDING")]))
    assert plan.primary_trigger_id == "t3" and plan.d1_offer == "EMBED"
    assert plan.d3_options == {"t1": "REPLAY", "t2": "BACKTEST"}


def test_no_verified_trigger_no_plan():
    t = _t("t1", "BUILDING").model_copy(update={"verification": "not_found"})
    assert make_plan(Research(triggers=[t])) is None


def test_size_bands():
    assert size_band("11–50 (LinkedIn)") == "small"
    assert size_band("51–200 (LinkedIn)") == "mid"
    assert size_band("1,001–5,000 (LinkedIn)") == "large"
    assert size_band("3 (YC W26, 2026)") == "small"
    assert size_band("NOT VERIFIED") == "unknown"


def test_large_account_bottom_up():
    cs = assign_schedule([Contact(key="c1", name="A B", title="Chief Operating Officer"),
                          Contact(key="c2", name="C D", title="Payment Operations Manager")], "large")
    assert [c.start_day for c in cs] == [3, 1]


def _golden_parts(name):
    g = G[name]
    r = Research.model_validate(g["research"])
    return g, r, make_plan(r), ContactPackage.model_validate(g["packages"][0]), Contact.model_validate(g["contacts"][0])


@pytest.mark.parametrize("bad,needle", [
    ("We have the base in place", "обязательной фразы"),
    ("My guess", "обязательной фразы"),
    ("or is another part of the CFO Stack roadmap a better fit?", "финал"),
])
def test_d1_required_parts(bad, needle):
    g, r, plan, pkg, c = _golden_parts("hitpay")
    d1 = pkg.touches[0]
    d1.body = d1.body.replace(bad, "Xx")
    assert any(needle in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


@pytest.mark.parametrize("insert,needle", [
    ("Haia does this. ", "HAIA"),
    ("Could we book a quick call? ", "созвон"),
    ("Our customers love it. ", "клиентах"),
    ("It is free for design partners. ", "цены"),
    ("Only 3 spots left. ", "лимит"),
    ("We cut exceptions by 40%. ", "числа без источника"),
])
def test_forbidden_content_caught(insert, needle):
    g, r, plan, pkg, c = _golden_parts("hitpay")
    d1 = pkg.touches[0]
    d1.body = d1.body.replace("My guess", insert + "My guess")
    assert any(needle in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


def test_d3_rule_enforced():
    g, r, plan, pkg, c = _golden_parts("hitpay")
    pkg.d3_basis = "B"   # у HitPay есть неиспользованный SCALE-повод → B запрещено
    assert any("гипотеза B не допускается" in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


def test_embed_only_when_embed():
    g, r, plan, pkg, c = _golden_parts("nomod")
    pkg.touches[0].body = pkg.touches[0].body.replace("From an export", "Under your brand. From an export")
    assert any("только для EMBED" in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


def test_linkedin_limit():
    g, r, plan, pkg, c = _golden_parts("nomod")
    pkg.touches[1].body = "x" * 201
    assert any("LinkedIn" in p for p in check_package(pkg, c, g["account"]["fact"], r, plan, SENDER))


def test_quote_matching_tolerates_typography():
    page = "<p>Dibsy’s MCP roadmap will allow deeper AI-powered workflows across the payment lifecycle, including payment recovery, support automation, settlement visibility, reconciliation, fraud prevention.</p>"
    assert quote_in_text(G["dibsy"]["research"]["triggers"][0]["quote"], page)
    assert not quote_in_text("Dibsy raised a Series B round of funding", page)


# ---------- полный проход с подменённой моделью ----------

class FakeRunner:
    """Отвечает по типу промпта: research / write / review."""
    def __init__(self, g):
        self.g = g
        self.calls = []

    def run(self, prompt, *, model, tools, timeout):
        self.calls.append(tools)
        if "You research ONE company" in prompt:
            r = json.loads(json.dumps(self.g["research"]))
            for t in r["triggers"]:
                t["verification"] = "pending"
            return json.dumps(r)
        if "You write a one-week outreach" in prompt:
            return json.dumps({"packages": self.g["packages"]})
        if "independent reviewer" in prompt:
            return json.dumps({"verdict": "PASS", "issues": []})
        raise AssertionError("unexpected prompt")


def test_full_flow_with_fake_model(monkeypatch):
    g = G["hitpay"]
    monkeypatch.setattr(stages, "verify_quote", lambda url, quote: "verified")
    runner = FakeRunner(g)
    st = AccountState(account=Account.model_validate(g["account"]),
                      contacts=[Contact.model_validate(c) for c in g["contacts"]])
    st = stages.research(st, runner, CFG, date(2026, 11, 9))
    assert st.status == "planned" and st.plan.d1_offer == "EMBED"
    st = stages.write(st, runner, CFG)
    assert st.status == "written", st.check_problems
    st = stages.review(st, runner, CFG)
    assert st.status == "ready"
    assert runner.calls[0] == ["WebSearch", "WebFetch"]


def test_stale_trigger_blocks_letter(monkeypatch):
    g = json.loads(json.dumps(G["nomod"]))
    g["research"]["triggers"][0]["event_date"] = "2024-01-01"
    monkeypatch.setattr(stages, "verify_quote", lambda url, quote: "verified")
    st = AccountState(account=Account.model_validate(g["account"]),
                      contacts=[Contact.model_validate(c) for c in g["contacts"]])
    st = stages.research(st, FakeRunner(g), CFG, date(2026, 11, 9))
    assert st.status == "under_researched"
