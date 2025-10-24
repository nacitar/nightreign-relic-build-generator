#!/usr/bin/env python3

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

SIZE_STRINGS = ("Delicate", "Polished", "Grand")
COLOR_STRINGS = ("Red", "Blue", "Yellow", "Green")
COLOR_NAME_STRINGS = ("Burning", "Drizzly", "Luminous", "Tranquil")


def main() -> int:
    strings_json = json.loads(Path("strings.json").read_text(encoding="utf-8"))

    item_metadata: dict[int, dict[str, str | int]] = {}
    for fmg_wrapper in strings_json["FmgWrappers"]:
        if fmg_wrapper["Name"] != "AntiqueName.fmg":
            continue
        for entry in fmg_wrapper["Fmg"]["Entries"]:
            text = (entry["Text"] or "").strip()
            if text:
                id = int(entry["ID"])
                if id < 100:
                    logger.warning(f"Skipping entry with id < 100: {id}")
                    continue
                item_metadata.setdefault(id, {})["name"] = text

    antique_csv_lines = (
        Path("antique.csv").read_text(encoding="utf-8").splitlines()
    )
    for line in antique_csv_lines:
        fields = line.split(",")
        try:
            id = int(fields[0].strip())
        except ValueError:
            logger.warning(f"Skipping entry with non-numeric id: {fields[0]}")
            continue
        if id < 100:
            logger.warning(f"Skipping entry with id < 100: {id}")
            continue
        try:
            color_index = int(fields[5])
        except ValueError:
            logger.error(f"Skipping entry with non-numeric color: {fields[0]}")
            continue

        sellable = bool(int(fields[7]))
        size = sum(int(fields[i]) != -1 for i in (14, 15, 16))
        try:
            entry = item_metadata[id]
        except KeyError:
            continue
        name = str(entry["name"])
        entry["size"] = int(size)
        entry["color"] = COLOR_STRINGS[color_index]
        if not sellable:
            entry["sellable"] = False
        is_deep = name.startswith("Deep ")
        default_name = " ".join(
            (["Deep"] if is_deep else [])
            + [
                SIZE_STRINGS[size - 1],
                COLOR_NAME_STRINGS[color_index],
                "Scene",
            ]
        )
        if default_name == name:
            del entry["name"]
        if is_deep:
            entry["color"] = f"Deep{entry["color"]}"

    incomplete = {
        id: data for id, data in item_metadata.items() if not data.get("color")
    }
    # remove entries without a color
    for id in incomplete.keys():
        del item_metadata[id]
    print(json.dumps(item_metadata, indent=4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
