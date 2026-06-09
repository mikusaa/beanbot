from __future__ import annotations

import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from beanbot.app import (
    BOT_COMMANDS,
    configure_logging,
    load_runtime_data,
    resolve_log_level,
    validate_quickadd_config,
)
from beanbot.config import Settings
from beanbot.quickadd import load_quickadd_config


CONFIG_PATH = Path("config/quickadd.example.yaml")


def test_bot_command_menu_contains_operational_commands() -> None:
    commands = {command.command for command in BOT_COMMANDS}

    assert {"add", "expense", "last", "delete", "reload", "cancel"} <= commands


def test_resolve_log_level_defaults_to_info() -> None:
    assert resolve_log_level(None) == logging.INFO
    assert resolve_log_level("debug") == logging.DEBUG
    assert resolve_log_level("unknown") == logging.INFO


def test_configure_logging_sets_chinese_readable_format() -> None:
    configure_logging("INFO")

    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger().handlers
    formatter = logging.getLogger().handlers[0].formatter
    assert formatter is not None
    assert "%(asctime)s %(levelname)s %(message)s" == formatter._fmt


def test_validate_quickadd_config_accepts_known_accounts() -> None:
    config = load_quickadd_config(CONFIG_PATH)
    accounts = sorted(
        set(config.payment_accounts.values())
        | {
            choice.account
            for choices in config.payment_account_choices.values()
            for choice in choices
        }
        | set(config.expense_keywords.keys())
        | {rule.expense_account for rule in config.payee_rules if rule.expense_account}
    )

    validate_quickadd_config(config, accounts)


def test_validate_quickadd_config_rejects_unknown_account() -> None:
    config = load_quickadd_config(CONFIG_PATH)

    with pytest.raises(ValueError, match="不存在的账户"):
        validate_quickadd_config(config, [])


def test_load_runtime_data_loads_config_and_accounts(tmp_path) -> None:
    books_dir = tmp_path / "books"
    config_dir = books_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "accounts.bean").write_text(
        """2019-01-01 open Liabilities:CreditCard:Default
2019-01-01 open Assets:Digital:Wallet
2019-01-01 open Expenses:Food:Meals
""",
        encoding="utf-8",
    )
    quickadd_file = tmp_path / "quickadd.yaml"
    quickadd_file.write_text(
        """payment_accounts:
  credit card: Liabilities:CreditCard:Default
payment_account_choices:
  wallet:
    - label: Wallet
      account: Assets:Digital:Wallet
expense_keywords:
  Expenses:Food:Meals:
    - dinner
narration_stopwords:
  - dinner
payee_rules:
  - keywords: [Restaurant]
    payee: Restaurant
    expense_account: Expenses:Food:Meals
""",
        encoding="utf-8",
    )
    settings = Settings(
        token="token",
        allowed_user_ids={1},
        beancount_file=books_dir / "main.bean",
        output_dir=books_dir / "tgbot",
        quickadd_config_file=quickadd_file,
        timezone=ZoneInfo("Asia/Shanghai"),
    )

    config, accounts = load_runtime_data(settings)

    assert "Liabilities:CreditCard:Default" in accounts
    assert config.payment_accounts["credit card"] == "Liabilities:CreditCard:Default"
    assert config.payment_account_choices["wallet"][0].account == "Assets:Digital:Wallet"
