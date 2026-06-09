from __future__ import annotations

from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from beanbot.app import read_last_entry
from beanbot.ledger import (
    LedgerError,
    Transaction,
    append_transaction,
    default_metadata,
    delete_entry,
    filter_accounts,
    list_recent_entries,
    make_expense,
    make_income,
    make_transfer,
    output_path,
    parse_amount,
    render_transaction,
)


def test_parse_amount_accepts_positive_decimal() -> None:
    assert parse_amount("1,234.50") == Decimal("1234.50")


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_parse_amount_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(value)


def test_filter_accounts_by_prefix() -> None:
    accounts = [
        "Assets:Digital:Wallet",
        "Expenses:Food:Meals",
        "Income:Salary",
    ]
    assert filter_accounts(accounts, ("Expenses:",)) == ["Expenses:Food:Meals"]


def test_render_expense_direction() -> None:
    tx = make_expense(
        tx_date=date(2026, 6, 8),
        amount=Decimal("25.5"),
        payee="Coffee Shop",
        narration="latte",
        source_account="Assets:Digital:Wallet",
        expense_account="Expenses:Food:Drinks",
        currency="CNY",
        metadata={"source": "telegram"},
    )
    text = render_transaction(tx)
    assert '2026-06-08 * "Coffee Shop" "latte"' in text
    assert "Assets:Digital:Wallet" in text
    assert "-25.5 CNY" in text
    assert "Expenses:Food:Drinks" in text
    assert "25.5 CNY" in text


def test_render_expense_with_discount() -> None:
    tx = make_expense(
        tx_date=date(2026, 6, 8),
        amount=Decimal("25"),
        payee="Restaurant",
        narration="lunch",
        source_account="Liabilities:CreditCard:Default",
        expense_account="Expenses:Food:Meals",
        currency="CNY",
        metadata={"source": "telegram"},
        discount_amount=Decimal("0.5"),
    )
    text = render_transaction(tx)

    assert "Liabilities:CreditCard:Default" in text
    assert "-25 CNY" in text
    assert "Expenses:Food:Meals" in text
    assert "25.5 CNY" in text
    assert "Income:Discount" in text
    assert "-0.5 CNY" in text


def test_render_income_direction() -> None:
    tx = make_income(
        tx_date=date(2026, 6, 8),
        amount=Decimal("100"),
        payee="Client",
        narration="freelance",
        target_account="Assets:Bank:Checking",
        income_account="Income:Freelance",
        currency="CNY",
        metadata={},
    )
    text = render_transaction(tx)
    assert "Assets:Bank:Checking" in text
    assert "100 CNY" in text
    assert "Income:Freelance" in text
    assert "-100 CNY" in text


def test_render_transfer_direction() -> None:
    tx = make_transfer(
        tx_date=date(2026, 6, 8),
        amount=Decimal("50"),
        payee="Transit Card",
        narration="top up",
        from_account="Liabilities:CreditCard:Default",
        to_account="Assets:Prepaid:Transit",
        currency="CNY",
        metadata={},
    )
    text = render_transaction(tx)
    assert "Liabilities:CreditCard:Default" in text
    assert "-50 CNY" in text
    assert "Assets:Prepaid:Transit" in text


def test_output_path_uses_year_month_directory(tmp_path) -> None:
    assert output_path(tmp_path, date(2026, 6, 8)) == tmp_path / "2026" / "06.bean"


def test_append_transaction_rolls_back_failed_check(monkeypatch, tmp_path) -> None:
    existing = "; existing\n\n"
    output_file = tmp_path / "2026" / "06.bean"
    output_file.parent.mkdir()
    output_file.write_text(existing, encoding="utf-8")

    def fail_check(_beancount_file) -> None:
        raise LedgerError("bad ledger")

    monkeypatch.setattr("beanbot.ledger.check_ledger", fail_check)
    tx = Transaction(
        date=date(2026, 6, 8),
        payee="测试",
        narration="失败回滚",
        postings=(),
        metadata={},
    )

    with pytest.raises(LedgerError):
        append_transaction(output_dir=tmp_path, beancount_file=tmp_path / "main.bean", tx=tx)

    assert output_file.read_text(encoding="utf-8") == existing


def test_default_metadata_omits_user_id() -> None:
    metadata = default_metadata(ZoneInfo("Asia/Shanghai"))

    assert metadata["source"] == "telegram"
    assert "telegram_message_id" not in metadata
    assert "telegram_user_id" not in metadata
    assert "time" not in metadata
    assert "created_at" in metadata
    assert len(metadata["created_at"]) == len("2026-06-08 12:15:18")


def test_read_last_entry_ignores_main_bean(tmp_path) -> None:
    (tmp_path / "main.bean").write_text("; marker file\n", encoding="utf-8")
    old_file = tmp_path / "2026-05.bean"
    old_file.write_text(
        """2026-05-01 * "旧结构" "旧账单"
  Assets:Cash                                                -1 CNY
  Expenses:Miscellaneous                                      1 CNY
""",
        encoding="utf-8",
    )
    new_dir = tmp_path / "2026"
    new_dir.mkdir()
    (new_dir / "06.bean").write_text(
        """; Generated by Telegram bot. Edit only if manual correction is needed.

2026-06-08 * "Coffee Shop" "latte"
  source: "telegram"
  created_at: "2026-06-08 12:15:18"
  Liabilities:CreditCard:Default                           -22.1 CNY
  Expenses:Food:Drinks                                     22.1 CNY
""",
        encoding="utf-8",
    )

    entry = read_last_entry(tmp_path)

    assert entry is not None
    assert '2026-06-08 * "Coffee Shop" "latte"' in entry
    assert "Liabilities:CreditCard:Default" in entry


def test_list_recent_entries_ignores_main_and_orders_by_file_and_index(tmp_path) -> None:
    (tmp_path / "main.bean").write_text("; root marker\n", encoding="utf-8")
    year_dir = tmp_path / "2026"
    year_dir.mkdir()
    (year_dir / "main.bean").write_text("; year marker\n", encoding="utf-8")
    (year_dir / "05.bean").write_text(
        """2026-05-01 * "旧账单" "五月"
  Assets:Cash                                                -1 CNY
  Expenses:Miscellaneous                                      1 CNY
""",
        encoding="utf-8",
    )
    (year_dir / "06.bean").write_text(
        """; Generated by Telegram bot.

2026-06-01 * "第一笔" "午饭"
  Assets:Cash                                                -10 CNY
  Expenses:Food:Meals                                        10 CNY

2026-06-02 * "第二笔" "晚饭"
  Assets:Cash                                                -20 CNY
  Expenses:Food:Meals                                        20 CNY
""",
        encoding="utf-8",
    )

    entries = list_recent_entries(tmp_path, limit=5)

    assert [entry.payee for entry in entries] == ["第二笔", "第一笔", "旧账单"]
    assert entries[0].amount == "20"


def test_delete_entry_removes_selected_transaction(monkeypatch, tmp_path) -> None:
    month_file = tmp_path / "2026" / "06.bean"
    month_file.parent.mkdir()
    month_file.write_text(
        """; Generated by Telegram bot.

2026-06-01 * "第一笔" "午饭"
  Assets:Cash                                                -10 CNY
  Expenses:Food:Meals                                        10 CNY

2026-06-02 * "第二笔" "晚饭"
  Assets:Cash                                                -20 CNY
  Expenses:Food:Meals                                        20 CNY
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("beanbot.ledger.check_ledger", lambda _file: None)
    entry = list_recent_entries(tmp_path, limit=1)[0]

    delete_entry(entry, tmp_path / "main.bean")

    content = month_file.read_text(encoding="utf-8")
    assert '"第二笔"' not in content
    assert '"第一笔"' in content
    assert content.startswith("; Generated by Telegram bot.")


def test_delete_entry_can_remove_last_written_reference(monkeypatch, tmp_path) -> None:
    month_file = tmp_path / "2026" / "06.bean"
    month_file.parent.mkdir()
    month_file.write_text(
        """2026-06-01 * "重复" "午饭"
  Assets:Cash                                                -10 CNY
  Expenses:Food:Meals                                        10 CNY

2026-06-01 * "重复" "午饭"
  Assets:Cash                                                -10 CNY
  Expenses:Food:Meals                                        10 CNY
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("beanbot.ledger.check_ledger", lambda _file: None)
    entries = list_recent_entries(tmp_path, limit=2)
    entry = entries[0]

    delete_entry(entry, tmp_path / "main.bean")

    assert month_file.read_text(encoding="utf-8").count('2026-06-01 * "重复" "午饭"') == 1


def test_delete_entry_rolls_back_when_check_fails(monkeypatch, tmp_path) -> None:
    month_file = tmp_path / "2026" / "06.bean"
    month_file.parent.mkdir()
    original = """2026-06-01 * "第一笔" "午饭"
  Assets:Cash                                                -10 CNY
  Expenses:Food:Meals                                        10 CNY
"""
    month_file.write_text(original, encoding="utf-8")

    def fail_check(_file) -> None:
        raise LedgerError("bad ledger")

    monkeypatch.setattr("beanbot.ledger.check_ledger", fail_check)
    entry = list_recent_entries(tmp_path, limit=1)[0]

    with pytest.raises(LedgerError):
        delete_entry(entry, tmp_path / "main.bean")

    assert month_file.read_text(encoding="utf-8") == original


def test_list_recent_entries_empty_for_placeholders(tmp_path) -> None:
    (tmp_path / "main.bean").write_text("; marker\n", encoding="utf-8")
    year_dir = tmp_path / "2026"
    year_dir.mkdir()
    (year_dir / "main.bean").write_text("; marker\n", encoding="utf-8")

    assert list_recent_entries(tmp_path) == []
