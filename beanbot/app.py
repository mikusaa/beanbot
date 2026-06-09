from __future__ import annotations

import logging
import os
import re
import warnings
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import Settings, load_settings
from .ledger import (
    LedgerEntry,
    Transaction,
    append_transaction,
    delete_entry,
    default_metadata,
    filter_accounts,
    list_recent_entries,
    load_accounts,
    make_expense,
    make_income,
    make_transfer,
    parse_amount,
    render_transaction,
)
from .parsers import parse_entry_text
from .quickadd import QuickAddConfig, QuickAddDraft, load_quickadd_config


AMOUNT, SOURCE, CONTRA, PAYEE, NARRATION, CONFIRM = range(6)
PAGE_SIZE = 12
ENTRY_START_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+[*!]\s+")
BOT_COMMANDS = (
    BotCommand("add", "自然语句快记"),
    BotCommand("expense", "新增支出"),
    BotCommand("income", "新增收入"),
    BotCommand("transfer", "新增转账"),
    BotCommand("last", "查看最近一笔记录"),
    BotCommand("delete", "删除最近 bot 记录"),
    BotCommand("reload", "重载快记配置"),
    BotCommand("cancel", "取消当前录入"),
)

logger = logging.getLogger(__name__)


def resolve_log_level(value: str | None) -> int:
    name = (value or "INFO").strip().upper()
    return getattr(logging, name, logging.INFO)


def configure_logging(log_level: str | None = None) -> None:
    warnings.filterwarnings(
        "ignore",
        message=r"If 'per_message=False'.*",
        category=PTBUserWarning,
    )
    logging.basicConfig(
        level=resolve_log_level(log_level or os.environ.get("LOG_LEVEL")),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    for name in ("httpx", "telegram", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.WARNING)


def user_allowed(settings: Settings, update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id in settings.allowed_user_ids


async def reject(update: Update) -> None:
    logger.warning("[安全] 未授权访问 kind=%s", update_kind(update))
    if update.effective_message:
        await update.effective_message.reply_text("未授权。")


def update_kind(update: Update) -> str:
    if update.callback_query:
        return "callback"
    if update.effective_message and update.effective_message.text:
        text = update.effective_message.text
        return "command" if text.startswith("/") else "message"
    return "unknown"


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def truncate_text(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def settings_from_context(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def accounts_from_context(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return context.application.bot_data["accounts"]


def quickadd_config_from_context(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["quickadd_config"]


def today_from_context(context: ContextTypes.DEFAULT_TYPE) -> date:
    return datetime.now(settings_from_context(context).timezone).date()


async def register_bot_commands(application: Application) -> None:
    await application.bot.set_my_commands(BOT_COMMANDS)
    logger.info("[启动] Telegram 指令菜单已同步 commands=%s", len(BOT_COMMANDS))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("[指令] /start 收到")
    if not user_allowed(settings_from_context(context), update):
        await reject(update)
        return
    await update.message.reply_text(
        "可用命令：/add 快记，/expense 新增支出，/income 新增收入，/transfer 新增转账，/last 查看最近记录，/delete 删除测试记录，/reload 重载配置。也可以直接发一句账单描述，例如：昨天 Coffee Shop latte 25 credit card。"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[指令] /cancel 收到")
    context.user_data.clear()
    if update.effective_message:
        await update.effective_message.reply_text("已取消。")
    return ConversationHandler.END


async def start_expense(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[指令] /expense 收到")
    return await begin(update, context, "expense")


async def start_income(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[指令] /income 收到")
    return await begin(update, context, "income")


async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[指令] /transfer 收到")
    return await begin(update, context, "transfer")


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[指令] /add 收到")
    if not user_allowed(settings_from_context(context), update):
        await reject(update)
        return ConversationHandler.END
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "请在 /add 后输入账单描述，例如：/add 昨天 Coffee Shop latte 25 credit card"
        )
        return ConversationHandler.END
    return await handle_quickadd_text(update, context, text)


async def quickadd_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("[消息] 收到快记文本")
    if not user_allowed(settings_from_context(context), update):
        await reject(update)
        return ConversationHandler.END
    return await handle_quickadd_text(update, context, update.message.text or "")


async def handle_quickadd_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> int:
    logger.debug("[快记] 原始输入 text=%s", truncate_text(text))
    today = today_from_context(context)
    draft = parse_entry_text(text, quickadd_config_from_context(context), today=today).draft
    if needs_source_account_choice(draft):
        logger.info(
            "[快记] 需要选择付款账户 payee=%s narration=%s choices=%s",
            draft.payee,
            draft.narration,
            len(draft.source_account_choices),
        )
        store_quickadd_draft(context, draft, today)
        buttons = [
            [
                InlineKeyboardButton(
                    choice.label,
                    callback_data=f"quickacct:{index}",
                )
            ]
            for index, choice in enumerate(draft.source_account_choices)
        ]
        buttons.append([InlineKeyboardButton("取消", callback_data="quickacct:cancel")])
        await update.message.reply_text(
            "请选择付款账户：",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return ConversationHandler.END

    if not draft.is_complete:
        logger.info("[快记] 解析缺字段 missing=%s", "、".join(draft.missing))
        await update.message.reply_text(
            "这句话还差：" + "、".join(draft.missing) + "\n可以补充后再发，或使用 /expense 走按钮录入。"
        )
        return ConversationHandler.END
    logger.info(
        "[快记] 解析成功 date=%s payee=%s narration=%s amount=%s discount=%s source=%s expense=%s",
        (draft.tx_date or today).isoformat(),
        draft.payee,
        draft.narration,
        draft.amount,
        draft.discount_amount or "",
        draft.source_account,
        draft.expense_account,
    )

    context.user_data.clear()
    store_quickadd_draft(context, draft, today)
    tx = build_transaction(update, context)
    return await send_confirmation(update, context, tx)


def needs_source_account_choice(draft: QuickAddDraft) -> bool:
    return draft.source_account is None and draft.source_account_choices and draft.missing == ("付款账户",)


def store_quickadd_draft(
    context: ContextTypes.DEFAULT_TYPE,
    draft: QuickAddDraft,
    today: date,
) -> None:
    context.user_data.clear()
    context.user_data.update(
        {
            "type": "expense",
            "date": (draft.tx_date or today).isoformat(),
            "amount": str(draft.amount) if draft.amount is not None else None,
            "discount_amount": str(draft.discount_amount) if draft.discount_amount else None,
            "source_account": draft.source_account,
            "source_account_choices": draft.source_account_choices,
            "contra_account": draft.expense_account,
            "payee": draft.payee,
            "narration": draft.narration,
        }
    )


async def choose_quickadd_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "quickacct:cancel":
        logger.info("[按钮] 取消快记账户选择")
        context.user_data.clear()
        await query.edit_message_text("已取消。")
        return

    choices = context.user_data.get("source_account_choices") or ()
    try:
        index = int((query.data or "").split(":")[-1])
        choice = choices[index]
    except (ValueError, IndexError):
        await query.edit_message_text("账户选择已失效，请重新发送账单。")
        return

    context.user_data["source_account"] = choice.account
    logger.info("[按钮] 快记付款账户已选 label=%s account=%s", choice.label, choice.account)
    tx = build_transaction(update, context)
    await send_confirmation(update, context, tx)


async def begin(update: Update, context: ContextTypes.DEFAULT_TYPE, tx_type: str) -> int:
    if not user_allowed(settings_from_context(context), update):
        await reject(update)
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["type"] = tx_type
    context.user_data["date"] = today_from_context(context).isoformat()
    await update.message.reply_text("请输入金额，例如 25.5")
    return AMOUNT


async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = parse_amount(update.message.text or "")
    except ValueError as exc:
        logger.info("[录入] 金额无效 error=%s", exc)
        await update.message.reply_text(str(exc))
        return AMOUNT
    logger.info("[录入] 金额已输入 type=%s amount=%s", context.user_data["type"], amount)
    context.user_data["amount"] = str(amount)

    tx_type = context.user_data["type"]
    accounts = accounts_from_context(context)
    choices = filter_accounts(accounts, ("Assets:", "Liabilities:"))
    if tx_type == "expense":
        prompt = "请选择付款账户"
    elif tx_type == "income":
        prompt = "请选择收款账户"
    else:
        prompt = "请选择转出账户"

    context.user_data["choices"] = choices
    await send_account_page(update, context, prompt, "source", 0)
    return SOURCE


def account_keyboard(choices: list[str], field: str, page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    buttons = [
        [InlineKeyboardButton(account, callback_data=f"acct:{field}:{account}")]
        for account in choices[start:end]
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("上一页", callback_data=f"page:{field}:{page - 1}"))
    if end < len(choices):
        nav.append(InlineKeyboardButton("下一页", callback_data=f"page:{field}:{page + 1}"))
    if nav:
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons)


async def send_account_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    field: str,
    page: int,
) -> None:
    choices = context.user_data["choices"]
    markup = account_keyboard(choices, field, page)
    if update.callback_query:
        await update.callback_query.edit_message_text(prompt, reply_markup=markup)
    else:
        await update.message.reply_text(prompt, reply_markup=markup)


async def select_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("page:"):
        logger.info("[按钮] 账户翻页 data=%s", data)
        _, field, page = data.split(":", 2)
        await send_account_page(update, context, "请选择账户", field, int(page))
        return SOURCE

    _, _, account = data.split(":", 2)
    context.user_data["source_account"] = account
    logger.info("[按钮] 已选来源账户 account=%s", account)

    tx_type = context.user_data["type"]
    accounts = accounts_from_context(context)
    if tx_type == "expense":
        choices = filter_accounts(accounts, ("Expenses:",))
        prompt = "请选择支出分类"
    elif tx_type == "income":
        choices = filter_accounts(accounts, ("Income:",))
        prompt = "请选择收入分类"
    else:
        choices = filter_accounts(accounts, ("Assets:", "Liabilities:"))
        prompt = "请选择转入账户"

    context.user_data["choices"] = choices
    await send_account_page(update, context, prompt, "contra", 0)
    return CONTRA


async def select_contra(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data.startswith("page:"):
        logger.info("[按钮] 账户翻页 data=%s", data)
        _, field, page = data.split(":", 2)
        await send_account_page(update, context, "请选择账户", field, int(page))
        return CONTRA

    _, _, account = data.split(":", 2)
    context.user_data["contra_account"] = account
    logger.info("[按钮] 已选对方账户 account=%s", account)
    await query.edit_message_text("请输入交易对象 payee，例如 Coffee Shop")
    return PAYEE


async def receive_payee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    payee = (update.message.text or "").strip()
    if not payee:
        logger.info("[录入] payee 为空")
        await update.message.reply_text("payee 不能为空。")
        return PAYEE
    context.user_data["payee"] = payee
    logger.info("[录入] payee 已输入 payee=%s", payee)
    await update.message.reply_text("请输入说明 narration，可输入 - 留空。")
    return NARRATION


async def receive_narration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    narration = (update.message.text or "").strip()
    context.user_data["narration"] = "" if narration == "-" else narration
    logger.info("[录入] narration 已输入 narration=%s", context.user_data["narration"])
    tx = build_transaction(update, context)
    return await send_confirmation(update, context, tx)


async def send_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tx: Transaction,
) -> int:
    context.user_data["preview"] = render_transaction(tx)
    context.user_data["tx"] = tx
    text = f"请确认：\n\n```\n{context.user_data['preview']}```"
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("确认写入", callback_data="confirm:yes"),
                InlineKeyboardButton("取消", callback_data="confirm:no"),
            ]
        ]
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    return CONFIRM


def build_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Transaction:
    settings = settings_from_context(context)
    metadata = default_metadata(settings.timezone)
    tx_type = context.user_data["type"]
    amount = Decimal(context.user_data["amount"])
    discount_amount = (
        Decimal(context.user_data["discount_amount"])
        if context.user_data.get("discount_amount")
        else None
    )
    common: dict[str, Any] = {
        "tx_date": date.fromisoformat(context.user_data["date"]),
        "amount": amount,
        "payee": context.user_data["payee"],
        "narration": context.user_data["narration"],
        "currency": settings.default_currency,
        "metadata": metadata,
    }
    if tx_type == "expense":
        return make_expense(
            **common,
            source_account=context.user_data["source_account"],
            expense_account=context.user_data["contra_account"],
            discount_amount=discount_amount,
        )
    if tx_type == "income":
        return make_income(
            **common,
            target_account=context.user_data["source_account"],
            income_account=context.user_data["contra_account"],
        )
    return make_transfer(
        **common,
        from_account=context.user_data["source_account"],
        to_account=context.user_data["contra_account"],
    )


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "confirm:no":
        logger.info("[按钮] 取消写入")
        context.user_data.clear()
        await query.edit_message_text("已取消。")
        return ConversationHandler.END

    settings = settings_from_context(context)
    tx = context.user_data["tx"]
    try:
        path = append_transaction(
            output_dir=settings.output_dir,
            beancount_file=settings.beancount_file,
            tx=tx,
        )
    except Exception as exc:
        logger.exception("[写入] 写入失败，已回滚 payee=%s narration=%s", tx.payee, tx.narration)
        await query.edit_message_text(f"写入失败，已回滚：\n{exc}")
        return ConversationHandler.END

    logger.info(
        "[写入] 已写入 file=%s payee=%s narration=%s amount=%s discount=%s",
        display_path(path, settings.output_dir),
        tx.payee,
        tx.narration,
        context.user_data.get("amount"),
        context.user_data.get("discount_amount") or "",
    )
    context.application.bot_data["last_entry"] = render_transaction(tx)
    context.application.bot_data["last_written_entry"] = LedgerEntry(
        path=path,
        index=-1,
        text=render_transaction(tx).rstrip(),
        date=tx.date.isoformat(),
        payee=tx.payee,
        narration=tx.narration,
        amount=None,
    )
    context.user_data.clear()
    await query.edit_message_text(
        f"已写入 {path.name}。",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("删除这笔", callback_data="delete:last_written")]]
        ),
    )
    return ConversationHandler.END


async def reload_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("[指令] /reload 收到")
    if not user_allowed(settings_from_context(context), update):
        await reject(update)
        return
    settings = settings_from_context(context)
    try:
        quickadd_config, accounts = load_runtime_data(settings)
    except Exception as exc:
        logger.exception("[配置] 重载失败，旧配置继续生效")
        await update.message.reply_text(f"重载失败，旧配置仍然生效：\n{exc}")
        return
    context.application.bot_data["quickadd_config"] = quickadd_config
    context.application.bot_data["accounts"] = accounts
    logger.info(
        "[配置] 重载成功 accounts=%s aliases=%s payee_rules=%s",
        len(accounts),
        len(quickadd_config.payment_accounts),
        len(quickadd_config.payee_rules),
    )
    await update.message.reply_text("已重载 quickadd 配置和账户列表。")


async def last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("[指令] /last 收到")
    if not user_allowed(settings_from_context(context), update):
        await reject(update)
        return
    entry = context.application.bot_data.get("last_entry")
    if not entry:
        entry = read_last_entry(settings_from_context(context).output_dir)
    if not entry:
        logger.info("[查询] 暂无最近记录")
        await update.message.reply_text("暂无 bot 写入记录。")
        return
    logger.info("[查询] 返回最近记录")
    await update.message.reply_text(f"最近一笔：\n\n```\n{entry}```", parse_mode="Markdown")


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("[指令] /delete 收到")
    if not user_allowed(settings_from_context(context), update):
        await reject(update)
        return
    settings = settings_from_context(context)
    candidates = list_recent_entries(settings.output_dir, limit=5)
    if not candidates:
        logger.info("[删除] 暂无可删除记录")
        await update.message.reply_text("暂无 bot 写入记录。")
        return

    logger.info("[删除] 展示最近记录 count=%s", len(candidates))
    context.user_data["delete_candidates"] = candidates
    buttons = [
        [
            InlineKeyboardButton(
                delete_button_text(index, entry),
                callback_data=f"delete:list:{index}",
            )
        ]
        for index, entry in enumerate(candidates)
    ]
    await update.message.reply_text(
        "选择要删除的账单：",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def choose_delete_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    logger.info("[按钮] 选择删除候选 data=%s", query.data)
    candidates = context.user_data.get("delete_candidates") or []
    try:
        index = int((query.data or "").split(":")[-1])
        entry = candidates[index]
    except (ValueError, IndexError):
        await query.edit_message_text("删除候选已失效，请重新发送 /delete。")
        return

    context.user_data["delete_index"] = index
    await query.edit_message_text(
        f"确认删除这笔账单？\n\n```\n{entry.text}\n```",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("确认删除", callback_data="delete:confirm"),
                    InlineKeyboardButton("取消", callback_data="delete:cancel"),
                ]
            ]
        ),
    )


async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "delete:cancel":
        logger.info("[按钮] 取消删除")
        context.user_data.pop("delete_candidates", None)
        context.user_data.pop("delete_index", None)
        await query.edit_message_text("已取消删除。")
        return

    candidates = context.user_data.get("delete_candidates") or []
    index = context.user_data.get("delete_index")
    try:
        entry = candidates[int(index)]
    except (TypeError, ValueError, IndexError):
        await query.edit_message_text("删除候选已失效，请重新发送 /delete。")
        return

    settings = settings_from_context(context)
    try:
        delete_entry(entry, settings.beancount_file)
    except Exception as exc:
        logger.exception("[删除] 删除失败，已回滚 file=%s payee=%s", entry.path.name, entry.payee)
        await query.edit_message_text(f"删除失败，已回滚：\n{exc}")
        return

    logger.info(
        "[删除] 已删除 file=%s payee=%s narration=%s",
        display_path(entry.path, settings.output_dir),
        entry.payee,
        entry.narration,
    )
    context.application.bot_data.pop("last_entry", None)
    context.user_data.pop("delete_candidates", None)
    context.user_data.pop("delete_index", None)
    await query.edit_message_text(f"已删除 {entry.path.name} 中的账单。")


async def delete_last_written(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    logger.info("[按钮] 删除这笔")
    if not user_allowed(settings_from_context(context), update):
        await query.edit_message_text("未授权。")
        return
    entry = context.application.bot_data.get("last_written_entry")
    if not entry:
        await query.edit_message_text("删除目标已失效，请使用 /delete。")
        return
    settings = settings_from_context(context)
    try:
        delete_entry(entry, settings.beancount_file)
    except Exception as exc:
        logger.exception("[删除] 删除刚写入交易失败，已回滚 file=%s payee=%s", entry.path.name, entry.payee)
        await query.edit_message_text(f"删除失败，已回滚：\n{exc}")
        return
    logger.info(
        "[删除] 已删除刚写入交易 file=%s payee=%s narration=%s",
        display_path(entry.path, settings.output_dir),
        entry.payee,
        entry.narration,
    )
    context.application.bot_data.pop("last_entry", None)
    context.application.bot_data.pop("last_written_entry", None)
    await query.edit_message_text(f"已删除 {entry.path.name} 中的账单。")


def delete_button_text(index: int, entry) -> str:
    amount = f" {entry.amount}" if entry.amount else ""
    text = f"{index + 1}. {entry.date} {entry.payee} {entry.narration}{amount}"
    return text[:64]


def read_last_entry(output_dir: Path) -> str | None:
    files = sorted(
        (
            path
            for path in output_dir.rglob("*.bean")
            if path.name != "main.bean"
        ),
        key=entry_file_sort_key,
        reverse=True,
    )
    for path in files:
        entries = parse_entries(path.read_text(encoding="utf-8"))
        if entries:
            return entries[-1] + "\n"
    return None


def entry_file_sort_key(path: Path) -> tuple[int, int, str]:
    parent = path.parent.name
    stem = path.stem
    if parent.isdigit() and len(parent) == 4 and stem.isdigit() and len(stem) == 2:
        return (int(parent), int(stem), str(path))

    match = re.match(r"^(\d{4})-(\d{2})$", stem)
    if match:
        return (int(match.group(1)), int(match.group(2)), str(path))

    return (0, 0, str(path))


def parse_entries(content: str) -> list[str]:
    entries: list[list[str]] = []
    current: list[str] = []
    for line in content.splitlines():
        if ENTRY_START_RE.match(line):
            if current:
                entries.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        entries.append(current)
    return ["\n".join(entry).rstrip() for entry in entries]


def load_runtime_data(settings: Settings) -> tuple[QuickAddConfig, list[str]]:
    accounts = load_accounts(settings.beancount_file.parent)
    quickadd_config = load_quickadd_config(settings.quickadd_config_file)
    validate_quickadd_config(quickadd_config, accounts)
    return quickadd_config, accounts


def validate_quickadd_config(config: QuickAddConfig, accounts: list[str]) -> None:
    account_set = set(accounts)
    configured_accounts = set(config.payment_accounts.values())
    for choices in config.payment_account_choices.values():
        configured_accounts.update(choice.account for choice in choices)
    configured_accounts.update(config.expense_keywords.keys())
    configured_accounts.update(
        rule.expense_account for rule in config.payee_rules if rule.expense_account
    )
    missing = sorted(account for account in configured_accounts if account not in account_set)
    if missing:
        raise ValueError("quickadd 配置引用了不存在的账户：" + "、".join(missing))


def build_application(settings: Settings) -> Application:
    configure_logging()
    application = Application.builder().token(settings.token).post_init(register_bot_commands).build()
    quickadd_config, accounts = load_runtime_data(settings)
    application.bot_data["settings"] = settings
    application.bot_data["accounts"] = accounts
    application.bot_data["quickadd_config"] = quickadd_config
    logger.info(
        "[启动] beanbot 启动完成 ledger=%s output=%s accounts=%s aliases=%s payee_rules=%s",
        settings.beancount_file,
        settings.output_dir,
        len(accounts),
        len(quickadd_config.payment_accounts),
        len(quickadd_config.payee_rules),
    )

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("expense", start_expense),
            CommandHandler("income", start_income),
            CommandHandler("transfer", start_transfer),
        ],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_amount)],
            SOURCE: [CallbackQueryHandler(select_source, pattern=r"^(acct:source:|page:source:)")],
            CONTRA: [CallbackQueryHandler(select_contra, pattern=r"^(acct:contra:|page:contra:)")],
            PAYEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payee)],
            NARRATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_narration)],
            CONFIRM: [CallbackQueryHandler(confirm, pattern=r"^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add))
    application.add_handler(CommandHandler("last", last))
    application.add_handler(CommandHandler("delete", delete))
    application.add_handler(CommandHandler("reload", reload_config))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(conversation)
    application.add_handler(CallbackQueryHandler(confirm, pattern=r"^confirm:"))
    application.add_handler(CallbackQueryHandler(choose_quickadd_source, pattern=r"^quickacct:"))
    application.add_handler(CallbackQueryHandler(delete_last_written, pattern=r"^delete:last_written$"))
    application.add_handler(CallbackQueryHandler(choose_delete_entry, pattern=r"^delete:list:\d+$"))
    application.add_handler(CallbackQueryHandler(confirm_delete, pattern=r"^delete:(confirm|cancel)$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, quickadd_text))
    return application


def main() -> None:
    settings = load_settings()
    application = build_application(settings)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
