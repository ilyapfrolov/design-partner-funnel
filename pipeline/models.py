"""Схемы данных. Модель обязана вернуть JSON ровно по этим схемам; код валидирует."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TriggerType = Literal["BUILDING", "CHANGE", "SCALE"]
OfferType = Literal["EMBED", "BACKTEST", "REPLAY"]
SourceType = Literal["news", "company_site", "jobs", "people", "base"]


class Account(BaseModel):
    page_id: str = ""
    account_id: str = ""
    company: str
    segment: str = ""
    priority: str = ""
    status: str = ""
    size: str = ""
    domain: str = ""
    role: str = ""
    fact: str = ""
    hypothesis: str = ""
    value: str = ""
    question: str = ""
    fallback: str = ""
    source: str = ""


class Contact(BaseModel):
    key: str                      # стабильный ключ внутри аккаунта: c1, c2...
    name: str
    title: str = ""
    function: str = ""
    email: str = ""
    linkedin: str = ""
    level: Literal["leader", "head", "executor"] = "leader"
    start_day: int = 1            # 1 — цепочка с дня 1; 3 — руководитель крупного аккаунта


class Trigger(BaseModel):
    id: str                       # t1, t2...
    type: TriggerType
    event_date: str               # YYYY-MM-DD или YYYY-MM
    url: str
    quote: str                    # дословно со страницы, до 25 слов
    summary: str                  # что произошло, своими словами
    ops_implication: str          # что это значит для операций
    confidence: Literal["EVENT", "INTERPRETATION"]
    verification: Literal["pending", "verified", "verified_llm", "not_found", "unreachable", "stale", "too_long"] = "pending"


class Research(BaseModel):
    triggers: list[Trigger] = Field(default_factory=list)
    coverage: dict[SourceType, str] = Field(default_factory=dict)  # тип источника → что найдено / "none"
    process_a: str = ""           # операционный процесс, на который бьёт день 1
    process_b: str = ""           # другой процесс для гипотезы B (из базы или исследования)
    notes: str = ""


class Touch(BaseModel):
    day: Literal["D1", "D1-2", "D3", "D5"]
    channel: Literal["email", "linkedin"]
    subject: str = ""
    body: str


class ContactPackage(BaseModel):
    contact_key: str
    d1_trigger_ids: list[str]     # какие поводы упомянуты в дне 1
    d3_basis: str                 # id повода для дня 3 или "B"
    touches: list[Touch]


class WriteResult(BaseModel):
    packages: list[ContactPackage]


class ReviewIssue(BaseModel):
    type: str
    excerpt: str
    fix: str


class ReviewResult(BaseModel):
    verdict: Literal["PASS", "FAIL"]
    issues: list[ReviewIssue] = Field(default_factory=list)


class Plan(BaseModel):
    primary_trigger_id: str
    d1_offer: OfferType
    d3_options: dict[str, OfferType] = Field(default_factory=dict)  # trigger_id → offer, допустимые основания дня 3
    d3_fallback_offer: OfferType = "REPLAY"


class AccountState(BaseModel):
    """Состояние одного аккаунта между этапами. Хранится в runs/<run>/<account>.json."""
    account: Account
    contacts: list[Contact] = Field(default_factory=list)
    skipped_contacts: list[str] = Field(default_factory=list)
    research: Optional[Research] = None
    plan: Optional[Plan] = None
    packages: list[ContactPackage] = Field(default_factory=list)
    check_problems: list[str] = Field(default_factory=list)
    review: Optional[ReviewResult] = None
    status: Literal[
        "selected", "researched", "under_researched", "planned",
        "written", "check_failed", "review_failed", "ready", "published", "error",
    ] = "selected"
    error: str = ""
    notion_page_url: str = ""
