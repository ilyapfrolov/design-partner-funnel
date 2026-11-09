"""Схемы данных. Модель обязана вернуть JSON ровно по этим схемам; код валидирует."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TriggerType = Literal["BUILDING", "CHANGE", "SCALE", "MARKET"]
OfferType = Literal["PREBUILD", "BACKTEST", "REPLAY"]
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
    owner: str = ""               # заполнен — компания уже в работе у человека, воронка не трогает
    known_trigger: str = ""       # повод, уже записанный в базе (подсказка для исследования)
    known_trigger_url: str = ""
    funnel_status: str = ""


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
    touches_done: int = 0         # «Касаний» в «Диалогах»: >0 — переписка начата, страницу не трогаем
    dialog_status: str = ""       # «Статус» в «Диалогах»
    variant: str = ""             # «Вариант вопроса»: Мягкий / 20 минут
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
    verification: Literal["pending", "verified", "verified_llm", "not_found", "unreachable", "too_long",
                          "unofficial"] = "pending"   # unofficial — пересказ без подтверждения самой компанией
    source_kind: Literal["company", "regulator", "press", "unknown"] = "unknown"   # ставит код по домену url
    company_statement: str = ""   # для press: дословная строка, где говорит сама компания / регулятор
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


# График касаний (Плейбук): день 0, +3, +7, +12. D0-LI — первое сообщение в LinkedIn,
# если основной канал LinkedIn (нет email). D7 — LinkedIn после принятого запроса на контакт.
DAYS = ("D0", "D0-LI", "D3", "D7", "D12")


class Touch(BaseModel):
    day: Literal["D0", "D0-LI", "D3", "D7", "D12"]
    channel: Literal["email", "linkedin"]
    subject: str = ""
    body: str


class ContactPackage(BaseModel):
    contact_key: str
    d1_trigger_ids: list[str]     # поводы первого сообщения (день 0)
    d3_basis: str                 # основание касания +3: id повода или "B"
    ending_call: str = ""         # концовка «20 минут» — заменяет последний абзац дня 0 (вариант вопроса)
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
    primary_trigger_id: str = ""  # повод, определяющий оффер
    hook_trigger_id: str = ""     # повод, с которого открывается письмо
    hook_tier: Literal["hook", "state"] = "state"
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
