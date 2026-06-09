from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from beanbot.quickadd import load_quickadd_config, parse_quickadd
from beanbot.parsers import parse_entry_text


CONFIG_PATH = Path("config/quickadd.example.yaml")


def test_parse_coffee_shop_sentence() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("在咖啡店买了 25 块钱的拿铁，用的信用卡", config)

    assert draft.is_complete
    assert draft.amount == Decimal("25")
    assert draft.discount_amount is None
    assert draft.source_account == "Liabilities:CreditCard:Default"
    assert draft.expense_account == "Expenses:Food:Drinks"
    assert draft.payee == "Coffee Shop"
    assert draft.narration == "拿铁"


def test_parse_short_coffee_sentence() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("Coffee Shop 9.9 latte credit card", config)

    assert draft.is_complete
    assert draft.amount == Decimal("9.9")
    assert draft.source_account == "Liabilities:CreditCard:Default"
    assert draft.expense_account == "Expenses:Food:Drinks"
    assert draft.payee == "Coffee Shop"


def test_parse_fruit_sentence() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("买水果 18 钱包", config)

    assert draft.is_complete
    assert draft.amount == Decimal("18")
    assert draft.source_account == "Assets:Digital:Wallet"
    assert draft.expense_account == "Expenses:Groceries"
    assert draft.payee == "水果"


def test_ambiguous_payment_account_returns_choices() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("fruit 18 card", config)

    assert not draft.is_complete
    assert draft.amount == Decimal("18")
    assert draft.source_account is None
    assert draft.expense_account == "Expenses:Groceries"
    assert draft.payee == "fruit"
    assert draft.missing == ("付款账户",)
    assert [choice.label for choice in draft.source_account_choices] == [
        "Main credit card",
        "Backup credit card",
    ]
    assert [choice.account for choice in draft.source_account_choices] == [
        "Liabilities:CreditCard:Default",
        "Liabilities:CreditCard:Backup",
    ]


def test_missing_account_is_reported() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("taxi 32", config)

    assert not draft.is_complete
    assert draft.amount == Decimal("32")
    assert draft.expense_account == "Expenses:Transport:Taxi"
    assert "付款账户" in draft.missing


def test_payee_rule_leaves_remaining_text_as_narration() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("Coffee Shop sandwich 25 lunch credit card", config)

    assert draft.is_complete
    assert draft.amount == Decimal("25")
    assert draft.source_account == "Liabilities:CreditCard:Default"
    assert draft.expense_account == "Expenses:Food:Drinks"
    assert draft.payee == "Coffee Shop"
    assert draft.narration == "sandwich"


def test_payee_rule_allows_free_word_order() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("credit card 25 Coffee Shop sandwich lunch", config)

    assert draft.is_complete
    assert draft.payee == "Coffee Shop"
    assert draft.narration == "sandwich"


def test_parse_discount_decimal() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("Coffee Shop latte 25 信用卡 优惠0.5", config)

    assert draft.is_complete
    assert draft.amount == Decimal("25")
    assert draft.discount_amount == Decimal("0.5")
    assert draft.payee == "Coffee Shop"
    assert draft.narration == "latte"


def test_parse_discount_jiao_unit() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("Coffee Shop latte 25 信用卡 优惠了5毛", config)

    assert draft.is_complete
    assert draft.amount == Decimal("25")
    assert draft.discount_amount == Decimal("0.5")
    assert draft.payee == "Coffee Shop"
    assert draft.narration == "latte"


def test_parse_relative_yesterday_date() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd(
        "昨天 Coffee Shop latte 25 lunch credit card",
        config,
        today=date(2026, 6, 9),
    )

    assert draft.is_complete
    assert draft.tx_date == date(2026, 6, 8)
    assert draft.amount == Decimal("25")
    assert draft.payee == "Coffee Shop"
    assert draft.narration == "latte"


def test_parse_relative_before_yesterday_date() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd(
        "前天 Coffee Shop latte 25 lunch credit card",
        config,
        today=date(2026, 6, 9),
    )

    assert draft.is_complete
    assert draft.tx_date == date(2026, 6, 7)
    assert draft.narration == "latte"


def test_parse_explicit_full_date() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd("2026-06-08 Coffee Shop latte 25 lunch credit card", config)

    assert draft.is_complete
    assert draft.tx_date == date(2026, 6, 8)
    assert draft.amount == Decimal("25")
    assert draft.narration == "latte"


def test_parse_month_day_date_uses_current_year() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd(
        "6月8日 Coffee Shop latte 25 lunch credit card",
        config,
        today=date(2026, 6, 9),
    )

    assert draft.is_complete
    assert draft.tx_date == date(2026, 6, 8)
    assert draft.amount == Decimal("25")
    assert draft.narration == "latte"


def test_date_text_does_not_become_narration_or_amount() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    draft = parse_quickadd(
        "6/8 Coffee Shop latte 25 lunch credit card",
        config,
        today=date(2026, 6, 9),
    )

    assert draft.is_complete
    assert draft.tx_date == date(2026, 6, 8)
    assert draft.amount == Decimal("25")
    assert draft.narration == "latte"


def test_parser_entry_point_preserves_quickadd_behavior() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    result = parse_entry_text(
        "昨天 Coffee Shop latte 25 lunch credit card 优惠0.5",
        config,
        today=date(2026, 6, 9),
    )

    assert result.parser_name == "quickadd"
    assert result.draft.is_complete
    assert result.draft.amount == Decimal("25")
    assert result.draft.discount_amount == Decimal("0.5")
    assert result.draft.payee == "Coffee Shop"
    assert result.draft.tx_date == date(2026, 6, 8)
