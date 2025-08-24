from __future__ import annotations

import argparse
import logging
from dataclasses import KW_ONLY, dataclass
from importlib.resources import open_text as open_text_resource
from logging import Handler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Sequence

from .bnd_decrypter import (
    EncryptionSettings,
    RelicData,
    RelicProcessor,
    SaveData,
    SaveFile,
)

logger = logging.getLogger(__name__)


@dataclass
class LogFileOptions:
    path: Path
    _ = KW_ONLY
    max_kb: int
    backup_count: int
    level: int = logging.DEBUG
    encoding: str = "utf-8"
    append: bool = True

    def create_handler(self) -> Handler:
        handler = RotatingFileHandler(
            self.path,
            mode="a" if self.append else "w",
            encoding=self.encoding,
            maxBytes=self.max_kb * 1024,
            backupCount=self.backup_count,
        )
        handler.setLevel(self.level)
        return handler


def configure_logging(
    console_level: int, log_file_options: LogFileOptions | None = None
) -> None:
    class SuppressConsoleOutputFor__main__(logging.Filter):
        def __init__(self) -> None:
            super().__init__()

        def filter(self, record: logging.LogRecord) -> bool:
            return record.name != (
                f"{__package__}.__main__" if __package__ else "__main__"
            )

    logging.getLogger().handlers = []
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        logging.Formatter(fmt="{levelname:s}: {message:s}", style="{")
    )
    console_handler.addFilter(SuppressConsoleOutputFor__main__())
    logging.getLogger().addHandler(console_handler)
    global_level = console_level
    if log_file_options:
        global_level = min(global_level, log_file_options.level)
        file_handler = log_file_options.create_handler()
        file_handler.setFormatter(
            logging.Formatter(
                fmt=(
                    "[{asctime:s}.{msecs:03.0f}]"
                    " [{levelname:s}] {module:s}: {message:s}"
                ),
                datefmt="%Y-%m-%d %H:%M:%S",
                style="{",
            )
        )
        logging.getLogger().addHandler(file_handler)
    logging.getLogger().setLevel(global_level)
    logging.info("logging configured")


def matching_offsets(matched: list[RelicData]) -> set[int]:
    first = matched[0].data
    return set(
        i
        for i, val in enumerate(first)
        if all(r.data[i] == val for r in matched[1:])
    )


def relic_color_a(rid: int) -> str | None:
    """Return 'red', 'blue', 'yellow', or 'green' for a given relic id.
    Returns None if the id doesn't match a known color scheme."""
    # Scheme B: bands of 9 in the last two digits, within 0–35
    last2 = rid % 100
    if 0 <= last2 <= 35:
        return ["Red", "Blue", "Yellow", "Green"][last2 // 9]

    # Scheme A: the big 100xxxx grid
    s = str(rid)
    if s.startswith("100") and len(s) >= 4:
        # 4th digit from the right is the color offset (0=red,1=blue,2=yellow,3=green)
        hundreds_offset = int(s[-4])
        if hundreds_offset in (0, 1, 2, 3):
            return ["Red", "Blue", "Yellow", "Green"][hundreds_offset]

    return None


def relic_color(relic_id: int) -> str:
    """Return color (Red, Blue, Yellow, Green) if determinable from relic_id."""
    # Rule 1: 7-digit "grid" IDs
    if relic_id >= 1_000_000:
        digit = (relic_id // 100) % 10
        return (
            ("Red", "Blue", "Yellow", "Green")[digit]
            if 0 <= digit <= 3
            else None
        )

    # Rule 2: compact 0–35 bands
    suffix = relic_id % 100
    if 0 <= suffix <= 35:
        bucket = suffix // 9
        return ("Red", "Blue", "Yellow", "Green")[bucket]

    return ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Does something.")
    log_group = parser.add_argument_group("logging")
    log_group.add_argument(
        "--log-file",
        metavar="FILE",
        help="Path to a file where logs will be written, if specified.",
    )
    log_verbosity_group = log_group.add_mutually_exclusive_group(
        required=False
    )
    log_verbosity_group.add_argument(
        "-v",
        "--verbose",
        action="store_const",
        dest="console_level",
        const=logging.INFO,
        help="Increase console log level to INFO.",
    )
    log_verbosity_group.add_argument(
        "-q",
        "--quiet",
        action="store_const",
        dest="console_level",
        const=logging.ERROR,
        help="Decrease console log level to ERROR.  Overrides -v.",
    )
    log_verbosity_group.add_argument(
        "--debug",
        action="store_const",
        dest="console_level",
        const=logging.DEBUG,
        help="Maximizes console log verbosity to DEBUG.  Overrides -v and -q.",
    )
    parser.add_argument("sl2_file", help="The save file to parse.")
    args = parser.parse_args(args=argv)

    configure_logging(
        console_level=args.console_level or logging.WARNING,
        log_file_options=(
            None
            if not args.log_file
            else LogFileOptions(
                path=Path(args.log_file),
                max_kb=512,  # 0 for unbounded size and no rotation
                backup_count=1,  # 0 for no rolling backups
                # append=False
            )
        ),
    )

    save_file = SaveFile.from_sl2_file(
        Path(args.sl2_file), EncryptionSettings()
    )

    for save in save_file.saves:
        print(f"Checking: {save.name}")
        print(save.data.find(b"\xd1\x07"))

    processor = RelicProcessor(save_file.saves[0])
    matched = processor.relic_report()

    def check_inference():
        for relic in matched:
            color = relic_color(relic.item_id)
            json_data = processor.item_data.get(str(relic.item_id), {})
            json_name = json_data.get("name", "UNNAMED")
            json_color = json_data.get("color", "UNKNOWN")
            if not color:
                print(f"CANNOT IDENTIFY {relic.item_id}")
            elif color != json_color:
                if json_color != "UNKNOWN":
                    print(
                        f"MISMATCH {relic.item_id} {json_name}: calculated {color}, json {json_color}"
                    )
                else:
                    print(f"UNVERFIED: {relic.item_id} {json_name} {color}")

    # TODO: 18272 might be an id that can't be sold!

    return 0
