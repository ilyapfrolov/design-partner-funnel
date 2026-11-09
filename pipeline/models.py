"""Схемы данных. Модель обязана вернуть JSON ровно по этим схемам; код валидирует."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TriggerType = Literal["BUILDING", "CHANGE", "SCALE", "MARKET"]
OfferType = Literal["EMBED", "BACKTEST", "REPLAY"]
Tier = Literal["hook", "state", "context", "stale", "invalid"]


class Account(BaseModel):
    page_id: str = ""
    account_id: str = ""
    company: str
    segment: str = ""
    priority: str = ""
    status: str = ""
    size: str = ""
    domain: str = ""
    country: str = ""
    role: str = ""
    fact: str = ""
    hypothesis: str = ""
    value: str = ""
    question: str = ""
    fallback: str = ""
    source: str = ""


class Contact(BaseModel):
    key: str                      # c1, c2… внутри аккаунта
    page_id: str = ""             # строка в базе контактов (пусто — ещё не записан)
    name: str
    title: str = ""
    function: str = ""
    email: str = ""
    linkedin: str = ""
    source_url: str = ""          # где найден (для найденных пайплайном)
    found_by: str = ""            # base / web / provider
    level: Literal["leader", "head", "executor"] = "leader"
    tier: Literal["executor", "owner", "nearest", ""] = ""   # ступень лестницы ролей (roles.yaml)
    start_day: int = 1            # 3 — руководитель крупного аккаунта (снизу вверх)


class Trigger(BaseModel):
    id: str = ""
    type: TriggerType
    event_date: str               # YYYY-MM-DD или YYYY-MM; для MARKET можно будущую дату дедлайна
    url: str
    quote: str                    # дословно из источника, до 25 слов
    summary: str                  # что произошло, своими словами, с точными цифрами источника
    ops_implication: str
    confidence: Literal["EVENT", "INTERPRETATION"]
    found_in_round: int = 0
    verification: Literal["pending", "verified", "verified_llm", "not_found", "unreachable", "too_long"] = "pending"
    tier: Tier = "invalid"        # ставит код по дате


class RoundLog(BaseModel):
    round: int
    method: str
    collected: dict[str, int] = Field(default_factory=dict)   # тип источника → сколько материалов
    triggers_found: int = 0
    triggers_usable: int = 0
    note: str = ""


class Research(BaseModel):
    triggers: list[Trigger] = Field(default_factory=list)
    process_a: str = ""
    process_b: str = ""
    notes: str = ""
    rounds: list[RoundLog] = Field(default_factory=list)


class ExtractResult(BaseModel):
    """Ответ модели на один заход исследования."""
    triggers: list[Trigger] = Field(default_factory=list)
    process_a: str = ""
    process_b: str = ""
    notes: str = ""


class PersonCandidate(BaseModel):
    name: str
    title: str
    source_url: str
    linkedin_url: str = ""
    evidence: str = ""            # дословная строка из источника с именем и должностью


class PeopleResult(BaseModel):
    candidates: list[PersonCandidate] = Field(default_factory=list)
    note: str = ""


class Touch(BaseModel):
    day: Literal["D1", "D1-2", "D3", "D5"]
    channel: Literal["email", "linkedin"]
    subject: str = ""
    body: str


class ContactPackage(BaseModel):
    contact_key: str
    d1_trigger_ids: list[str]
    d3_basis: str                 # id повода или "B"
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
    cohort: Literal["company", "market"] = "company"
    primary_trigger_id: str       # повод, определяющий оффер
    hook_trigger_id: str          # повод, с которого открывается письмо
    hook_tier: Literal["hook", "state"]
    d1_offer: OfferType
    d3_options: dict[str, OfferType] = Field(default_factory=dict)
    d3_fallback_offer: OfferType = "REPLAY"


Status = Literal[
    "selected", "people_ok", "needs_people", "planned", "on_hold",
    "written", "check_failed", "review_failed", "ready", "published", "error",
]


class AccountState(BaseModel):
    """Состояние одного аккаунта между этапами: runs/<run>/<account>.json."""
    account: Account
    contacts: list[Contact] = Field(default_factory=list)
    skipped_contacts: list[str] = Field(default_factory=list)   # брак / чужая функция
    warm_contacts: list[str] = Field(default_factory=list)      # есть тёплое интро — не в холодную цепочку
    people_log: list[RoundLog] = Field(default_factory=list)
    research: Optional[Research] = None
    plan: Optional[Plan] = None
    packages: list[ContactPackage] = Field(default_factory=list)
    check_problems: list[str] = Field(default_factory=list)
    review: Optional[ReviewResult] = None
    status: Status = "selected"
    hold_reason: str = ""
    next_recheck: str = ""        # YYYY-MM-DD для холдов
    error: str = ""
    notion_page_url: str = ""
