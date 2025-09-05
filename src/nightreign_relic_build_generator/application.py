from __future__ import annotations

import argparse
import logging
from dataclasses import KW_ONLY, dataclass
from logging import Handler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Sequence

from .finder import get_top_builds
from .nightreign import (
    CLASS_URNS,
    UNIVERSAL_URNS,
    Database,
    Relic,
    load_save_file,
)
from .utility import (
    get_builtin_scores,
    list_builtin_score_resources,
    load_scores,
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Determines the best combinations of relics according to"
            " user-provided scores."
        )
    )
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
    subparsers = parser.add_subparsers(dest="operation", required=True)

    subparsers.add_parser("list-builtins", help="List builtin score profiles")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("sl2_file", help="The save file to parse.")
    common.add_argument(
        "-i",
        "--index",
        type=int,
        choices=range(10),  # 0..9 inclusive
        metavar="N",
        help="save slot index (0-9)",
        default=0,
    )

    subparsers.add_parser(
        "dump-relics",
        parents=[common],
        help="Dumps a list of all parsed relics.",
    )

    compute_parser = subparsers.add_parser(
        "compute",
        parents=[common],
        help="Computes the best possible relic combinations.",
    )

    scores_group = compute_parser.add_mutually_exclusive_group(required=True)
    scores_group.add_argument(
        "-s",
        "--scores",
        metavar="JSON_FILE",
        help="A json file mapping relic effect names to integral scores.",
    )
    scores_group.add_argument(
        "-b",
        "--builtin-scores",
        metavar="NAME",
        choices=list_builtin_score_resources(),
        help="The name of a builtin score profile.",
    )
    compute_parser.add_argument(
        "-c",
        "--character-class",
        metavar="NAME",
        help='The name of the class whose urns will be used, or "universal".',
        choices=tuple(CLASS_URNS.keys()) + ("universal",),
        required=True,
    )
    compute_parser.add_argument(
        "-l",
        "--limit",
        metavar="COUNT",
        help="The number of highest-scoring results to provide.",
        type=int,
        default=50,
    )
    compute_parser.add_argument(
        "-m",
        "--minimum",
        metavar="SCORE",
        help="The minimum score required for a build to be accepted.",
        type=int,
        default=1,
    )
    compute_parser.add_argument(
        "-p",
        "--prune",
        metavar="SCORE",
        help=(
            "The minimum value that the effects of a relic must score in"
            " order for that relic to be considered in selection."
        ),
        type=int,
        default=1,
    )
    compute_parser.add_argument(
        "--no-progress-bar",
        action="store_true",
        help="Disables the progress bar typically rendered to stderr.",
    )
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

    if args.operation == "list-builtins":
        for resource_name in list_builtin_score_resources():
            print(resource_name)
    elif args.operation in ("dump-relics", "compute"):
        save_title = f"USER_DATA{args.index:03d}"
        logger.info(f"Looking for {save_title} in save: {args.sl2_file}")
        save_data = load_save_file(Path(args.sl2_file), save_title)
        logger.info(f"Loaded entry {save_data.title}: {save_data.name}")

        database = Database()
        relics: list[Relic] = []
        incomplete_relics: list[Relic] = []
        for relic_data in save_data.relics:
            relic = database.get_relic(relic_data)
            if relic.is_incomplete:
                incomplete_relics.append(relic)
            else:
                relics.append(relic)
        logger.info(f"Loaded {len(relics)} complete relics.")
        if incomplete_relics:
            logger.warning(
                f"Excluded {len(incomplete_relics)} incomplete relics"
                '; run "dump-relics" operation to see them.'
            )
        if args.operation == "dump-relics":
            print("COMPLETE RELICS:")
            for relic in relics:
                print(relic)
            if incomplete_relics:
                print("")
                print("INCOMPLETE RELICS:")
                for relic in incomplete_relics:
                    print(relic)
            print("")
            print(
                f"Listed {len(relics)} complete and {len(incomplete_relics)}"
                " incomplete relics."
            )
        else:
            incomplete_relics.clear()  # free this memory
            if args.operation == "compute":
                if args.scores:
                    score_table = load_scores(Path(args.scores))
                elif args.builtin_scores:
                    score_table = get_builtin_scores(args.builtin_scores)

                urns = set(UNIVERSAL_URNS.values())
                if args.character_class != "universal":
                    urns.update(CLASS_URNS[args.character_class].values())

                for build in reversed(
                    get_top_builds(
                        relics,
                        urns,
                        score_table=score_table,
                        count=args.limit,
                        prune=args.prune,
                        minimum=args.minimum,
                        progress_bar=not args.no_progress_bar,
                    )
                ):
                    print("")
                    print(build)

                print("")
                print(f"TOP {args.limit} scores, listed in reverse order.")

            else:
                raise NotImplementedError()
    else:
        raise NotImplementedError()
    return 0
