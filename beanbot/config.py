from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    token: str
    allowed_user_ids: set[int]
    beancount_file: Path
    output_dir: Path
    quickadd_config_file: Path
    default_currency: str = "CNY"
    timezone: ZoneInfo = ZoneInfo("Asia/Shanghai")


def _parse_user_ids(value: str) -> set[int]:
    ids: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        ids.add(int(part))
    return ids


def load_settings() -> Settings:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_user_ids = _parse_user_ids(os.environ.get("TELEGRAM_ALLOWED_USER_IDS", ""))
    beancount_file = Path(os.environ.get("BEANCOUNT_FILE", "/books/main.bean"))
    output_dir = Path(
        os.environ.get("BEANBOT_OUTPUT_DIR")
        or os.environ.get("TGBOT_OUTPUT_DIR", "/books/tgbot")
    )
    quickadd_config_file = Path(
        os.environ.get("BEANBOT_QUICKADD_CONFIG", "/config/quickadd.yaml")
    )
    currency = os.environ.get("DEFAULT_CURRENCY", "CNY").strip() or "CNY"
    timezone_name = os.environ.get("TZ", "Asia/Shanghai").strip() or "Asia/Shanghai"

    missing = []
    if not token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not allowed_user_ids:
        missing.append("TELEGRAM_ALLOWED_USER_IDS")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    return Settings(
        token=token,
        allowed_user_ids=allowed_user_ids,
        beancount_file=beancount_file,
        output_dir=output_dir,
        quickadd_config_file=quickadd_config_file,
        default_currency=currency,
        timezone=ZoneInfo(timezone_name),
    )
