from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property

logger = logging.getLogger(__name__)


class TermStyle(StrEnum):
    @dataclass
    class __TermStyleSettings:
        enabled: bool = True
        overrides: dict[TermStyle, str] = field(default_factory=dict)

    __settings: __TermStyleSettings = __TermStyleSettings()
    RESET = "sgr0"
    RESET_COLOR = "op"
    BOLD = "bold"
    UNDERLINE = "smul"
    REVERSE = "rev"

    BLACK = "setaf 0"
    RED = "setaf 1"
    GREEN = "setaf 2"
    YELLOW = "setaf 3"
    BLUE = "setaf 4"
    MAGENTA = "setaf 5"
    CYAN = "setaf 6"
    WHITE = "setaf 7"

    BG_BLACK = "setab 0"
    BG_RED = "setab 1"
    BG_GREEN = "setab 2"
    BG_YELLOW = "setab 3"
    BG_BLUE = "setab 4"
    BG_MAGENTA = "setab 5"
    BG_CYAN = "setab 6"
    BG_WHITE = "setab 7"

    @staticmethod
    def tput(arguments: list[str]) -> str:
        try:
            return subprocess.run(
                ["tput"] + arguments,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            ).stdout
        except FileNotFoundError:
            logger.debug("tput not in path, returning an empty string.")
            return ""

    @cached_property
    def escape(self) -> str:
        return type(self).tput(self.value.split())

    def __str__(self) -> str:
        if type(self).__settings.enabled:
            override = type(self).__settings.overrides.get(self)
            if override is not None:
                return override
            return self.escape
        return ""

    @classmethod
    def set_overrides(cls, escapes: dict[TermStyle, str]) -> None:
        cls.__settings.overrides = escapes.copy()

    @classmethod
    def set_enabled(cls, enabled: bool) -> None:
        cls.__settings.enabled = enabled
