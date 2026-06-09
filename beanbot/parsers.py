from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .quickadd import QuickAddConfig, QuickAddDraft, parse_quickadd


@dataclass(frozen=True)
class ParserResult:
    draft: QuickAddDraft
    parser_name: str


class EntryParser(Protocol):
    name: str

    def parse(
        self,
        text: str,
        config: QuickAddConfig,
        today: date | None = None,
    ) -> QuickAddDraft:
        raise NotImplementedError


class QuickAddParser:
    name = "quickadd"

    def parse(
        self,
        text: str,
        config: QuickAddConfig,
        today: date | None = None,
    ) -> QuickAddDraft:
        return parse_quickadd(text, config, today=today)


PARSERS: tuple[EntryParser, ...] = (QuickAddParser(),)


def parse_entry_text(
    text: str,
    config: QuickAddConfig,
    today: date | None = None,
) -> ParserResult:
    return ParserResult(
        draft=PARSERS[0].parse(text, config, today=today),
        parser_name=PARSERS[0].name,
    )
