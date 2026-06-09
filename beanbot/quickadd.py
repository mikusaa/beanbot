from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from .ledger import parse_amount


AMOUNT_RE = re.compile(
    r"(?:¥|￥)?\s*(?P<amount>\d+(?:\.\d+)?)\s*(?:块钱|块|元|人民币)?"
)
DISCOUNT_RE = re.compile(
    r"(?:优惠了?|立减|满减|减免|减)\s*(?:¥|￥)?\s*(?P<amount>\d+(?:\.\d+)?)\s*(?P<unit>毛|角|块钱|块|元|人民币)?"
)
FULL_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)"
)
CHINESE_DATE_RE = re.compile(r"(?<!\d)(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]?(?!\d)")
SLASH_DATE_RE = re.compile(r"(?<!\d)(?P<month>\d{1,2})/(?P<day>\d{1,2})(?!\d)")
RELATIVE_DATE_RE = re.compile(r"(?P<relative>今天|昨天|前天)")
PAYEE_RE = re.compile(r"(?:在|从|给)(?P<payee>.+?)(?:点|买|消费|花|充|订|下单|付|用了|用的|，|,)")
NARRATION_PATTERNS = [
    re.compile(r"点了(?:一|1)?份?\s*(?P<narration>.+?)(?:，|,|用|花|$)"),
    re.compile(r"买了?\s*(?P<narration>.+?)(?:，|,|用|花|$)"),
    re.compile(r"消费\s*(?P<narration>.+?)(?:，|,|用|花|$)"),
]


@dataclass(frozen=True)
class PayeeRule:
    keywords: tuple[str, ...]
    payee: str
    expense_account: str | None


@dataclass(frozen=True)
class AccountChoice:
    label: str
    account: str


@dataclass(frozen=True)
class QuickAddConfig:
    payment_accounts: dict[str, str]
    payment_account_choices: dict[str, tuple[AccountChoice, ...]]
    expense_keywords: dict[str, tuple[str, ...]]
    narration_stopwords: tuple[str, ...]
    payee_rules: tuple[PayeeRule, ...]


@dataclass(frozen=True)
class QuickAddDraft:
    tx_date: date | None
    amount: Decimal | None
    discount_amount: Decimal | None
    source_account: str | None
    source_account_choices: tuple[AccountChoice, ...]
    expense_account: str | None
    payee: str | None
    narration: str
    missing: tuple[str, ...]
    original_text: str

    @property
    def is_complete(self) -> bool:
        return not self.missing


def load_quickadd_config(path: Path | None = None) -> QuickAddConfig:
    config_path = path or Path(__file__).with_name("quickadd.yaml")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    payee_rules = tuple(
        PayeeRule(
            keywords=tuple(str(keyword) for keyword in item.get("keywords", [])),
            payee=str(item["payee"]),
            expense_account=item.get("expense_account"),
        )
        for item in data.get("payee_rules", [])
    )
    expense_keywords = {
        str(account): tuple(str(keyword) for keyword in keywords)
        for account, keywords in data.get("expense_keywords", {}).items()
    }

    return QuickAddConfig(
        payment_accounts={
            str(alias): str(account)
            for alias, account in data.get("payment_accounts", {}).items()
        },
        payment_account_choices=load_payment_account_choices(data),
        expense_keywords=expense_keywords,
        narration_stopwords=tuple(
            str(keyword) for keyword in data.get("narration_stopwords", [])
        ),
        payee_rules=payee_rules,
)


def load_payment_account_choices(data: dict[str, Any]) -> dict[str, tuple[AccountChoice, ...]]:
    groups: dict[str, tuple[AccountChoice, ...]] = {}
    for alias, choices in data.get("payment_account_choices", {}).items():
        groups[str(alias)] = tuple(
            AccountChoice(
                label=str(item["label"]),
                account=str(item["account"]),
            )
            for item in choices
        )
    return groups


def parse_quickadd(
    text: str,
    config: QuickAddConfig,
    today: date | None = None,
) -> QuickAddDraft:
    base_date = today or date.today()
    normalized = normalize_text(text)
    tx_date, text_without_date = extract_transaction_date(normalized, base_date)
    discount_amount = extract_discount_amount(text_without_date)
    amount = extract_amount(remove_discount_text(text_without_date))
    matched_payment_alias = match_alias_text(text_without_date, config.payment_accounts)
    source_account = config.payment_accounts[matched_payment_alias] if matched_payment_alias else None
    source_account_choices: tuple[AccountChoice, ...] = ()
    if source_account is None:
        matched_payment_alias = match_choice_alias(
            text_without_date,
            config.payment_account_choices,
        )
        if matched_payment_alias:
            source_account_choices = config.payment_account_choices[matched_payment_alias]
    payee_rule = match_payee_rule(text_without_date, config.payee_rules)
    expense_account = (
        payee_rule.expense_account
        if payee_rule
        else match_expense_account(text_without_date, config)
    )
    payee = payee_rule.payee if payee_rule else extract_payee(text_without_date)
    matched_payee_keyword = (
        match_payee_keyword(text_without_date, payee_rule) if payee_rule else payee
    )
    category_keyword = match_expense_keyword(text_without_date, config, expense_account)
    stopwords = matched_narration_stopwords(text_without_date, config)
    narration = (
        extract_narration(
            text_without_date,
            amount,
            matched_payment_alias,
            matched_payee_keyword,
            stopwords,
        )
        or text_without_date
    )
    if not payee and category_keyword:
        payee = category_keyword
        narration = category_keyword
    elif not payee and narration:
        payee = narration

    missing = []
    if amount is None:
        missing.append("金额")
    if source_account is None:
        missing.append("付款账户")
    if expense_account is None:
        missing.append("支出分类")
    if not payee:
        missing.append("交易对象")

    return QuickAddDraft(
        tx_date=tx_date,
        amount=amount,
        discount_amount=discount_amount,
        source_account=source_account,
        source_account_choices=source_account_choices,
        expense_account=expense_account,
        payee=payee,
        narration=narration,
        missing=tuple(missing),
        original_text=text,
    )


def normalize_text(text: str) -> str:
    return " ".join(text.strip().replace("，", ",").split())


def extract_transaction_date(text: str, base_date: date) -> tuple[date | None, str]:
    for pattern in (FULL_DATE_RE, CHINESE_DATE_RE, SLASH_DATE_RE, RELATIVE_DATE_RE):
        match = pattern.search(text)
        if not match:
            continue
        tx_date = build_transaction_date(match, base_date)
        if not tx_date:
            continue
        return tx_date, cleanup_fragment(pattern.sub(" ", text, count=1))
    return None, text


def build_transaction_date(match: re.Match[str], base_date: date) -> date | None:
    if "relative" in match.groupdict() and match.group("relative"):
        days = {"今天": 0, "昨天": 1, "前天": 2}[match.group("relative")]
        return base_date - timedelta(days=days)

    year = int(match.groupdict().get("year") or base_date.year)
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_amount(text: str) -> Decimal | None:
    for match in AMOUNT_RE.finditer(text):
        try:
            return parse_amount(match.group("amount"))
        except ValueError:
            continue
    return None


def extract_discount_amount(text: str) -> Decimal | None:
    match = DISCOUNT_RE.search(text)
    if not match:
        return None
    amount = parse_amount(match.group("amount"))
    if match.group("unit") in ("毛", "角"):
        return amount / Decimal("10")
    return amount


def remove_discount_text(text: str) -> str:
    return DISCOUNT_RE.sub(" ", text)


def match_alias(text: str, aliases: dict[str, str]) -> str | None:
    alias = match_alias_text(text, aliases)
    return aliases[alias] if alias else None


def match_alias_text(text: str, aliases: dict[str, str]) -> str | None:
    for alias in sorted(aliases, key=len, reverse=True):
        if alias in text:
            return alias
    return None


def match_choice_alias(
    text: str,
    choices: dict[str, tuple[AccountChoice, ...]],
) -> str | None:
    for alias in sorted(choices, key=len, reverse=True):
        if alias in text:
            return alias
    return None


def match_payee_rule(text: str, rules: tuple[PayeeRule, ...]) -> PayeeRule | None:
    for rule in rules:
        if any(keyword in text for keyword in rule.keywords):
            return rule
    return None


def match_payee_keyword(text: str, rule: PayeeRule | None) -> str | None:
    if not rule:
        return None
    for keyword in sorted(rule.keywords, key=len, reverse=True):
        if keyword in text:
            return keyword
    return rule.payee


def match_expense_account(text: str, config: QuickAddConfig) -> str | None:
    for account, keywords in config.expense_keywords.items():
        if any(keyword in text for keyword in keywords):
            return account
    return None


def match_expense_keyword(
    text: str,
    config: QuickAddConfig,
    expense_account: str | None,
) -> str | None:
    if not expense_account:
        return None
    for keyword in sorted(config.expense_keywords.get(expense_account, ()), key=len, reverse=True):
        if keyword in text:
            return keyword
    return None


def matched_narration_stopwords(text: str, config: QuickAddConfig) -> tuple[str, ...]:
    keywords = config.narration_stopwords
    return tuple(keyword for keyword in keywords if keyword in text)


def extract_payee(text: str) -> str | None:
    match = PAYEE_RE.search(text)
    if not match:
        return None
    payee = cleanup_fragment(match.group("payee"))
    return payee or None


def extract_narration(
    text: str,
    amount: Decimal | None,
    payment_alias: str | None,
    payee_keyword: str | None,
    stopwords: tuple[str, ...],
) -> str:
    for pattern in NARRATION_PATTERNS:
        match = pattern.search(text)
        if match:
            narration = cleanup_fragment(match.group("narration"))
            narration = remove_discount_text(narration)
            narration = remove_amount_text(narration, amount)
            if payment_alias:
                narration = narration.replace(payment_alias, "")
            narration = remove_known_fragments(narration, payee_keyword, stopwords)
            narration = cleanup_fragment(narration)
            if narration:
                return narration

    narration = text
    narration = remove_known_fragments(narration, payee_keyword, stopwords)
    if payment_alias:
        narration = narration.replace(payment_alias, "")
    narration = remove_discount_text(narration)
    narration = remove_amount_text(narration, amount)
    for filler in ("在", "从", "给", "用的", "用", "花了", "花", "买了", "买", "点了份", "点了"):
        narration = narration.replace(filler, " ")
    return cleanup_fragment(narration)


def remove_known_fragments(
    text: str,
    payee_keyword: str | None,
    stopwords: tuple[str, ...],
) -> str:
    if payee_keyword:
        text = text.replace(payee_keyword, " ")
    for keyword in sorted(stopwords, key=len, reverse=True):
        text = text.replace(keyword, " ")
    return text


def remove_amount_text(text: str, amount: Decimal | None) -> str:
    if amount is None:
        return text
    return AMOUNT_RE.sub(" ", text)


def cleanup_fragment(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" ,，。:：")
    text = re.sub(r"^(的|了|一份|份)\s*", "", text).strip()
    text = re.sub(r"\s*(的|了)$", "", text)
    return text.strip()
