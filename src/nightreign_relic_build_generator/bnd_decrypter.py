from __future__ import annotations

import hashlib
import json
import logging
import struct
from dataclasses import dataclass, field
from functools import cached_property
from importlib.resources import open_text as open_text_resource
from pathlib import Path
from typing import Any, ByteString, ClassVar

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

IV_SIZE = 0x10
PADDING_SIZE = 0xC
START_OF_CHECKSUM_DATA = 4
END_OF_CHECKSUM_DATA = PADDING_SIZE + 16


@dataclass(frozen=True)
class EncryptionSettings:
    iv_size: int = 0x10
    key: bytes = (
        b"\x18\xf6\x32\x66\x05\xbd\x17\x8a\x55\x24\x52\x3a\xc0\xa0\xc6\x09"
    )


def get_resource_text(name: str) -> str:
    with open_text_resource(f"{__package__}.resources", name) as json_file:
        return json_file.read()


def get_resource_json(name: str) -> Any:
    return json.loads(get_resource_text(name))


@dataclass(frozen=True)
class BND4Entry:
    data: bytes
    initialization_vector: bytes

    @classmethod
    def decrypt(
        cls, data: bytes, *, settings: EncryptionSettings | None = None
    ) -> BND4Entry:
        if not settings:
            settings = EncryptionSettings()
        initialization_vector = data[: settings.iv_size]
        decryptor = Cipher(
            algorithms.AES(settings.key), modes.CBC(initialization_vector)
        ).decryptor()
        return BND4Entry(
            data=(
                decryptor.update(data[settings.iv_size :])
                + decryptor.finalize()
            ),
            initialization_vector=initialization_vector,
        )

    # def patch_checksum(self) -> None:
    #    checksum = self.calculate_checksum()
    #    checksum_end = len(self.data) - END_OF_CHECKSUM_DATA
    #    self.data = (
    #        self.data[:checksum_end]
    #        + checksum
    #        + self.data[checksum_end + 16 :]
    #    )

    def calculate_checksum(self) -> bytes:
        checksum_end = len(self.data) - END_OF_CHECKSUM_DATA
        data_for_hash = self.data[START_OF_CHECKSUM_DATA:checksum_end]
        return hashlib.md5(data_for_hash).digest()

    # TODO: test?  remove?
    def encrypt_sl2_data(
        self, settings: EncryptionSettings | None = None
    ) -> bytes:
        if not settings:
            settings = EncryptionSettings()
        encryptor = Cipher(
            algorithms.AES(settings.key), modes.CBC(self.initialization_vector)
        ).encryptor()
        encrypted_payload = encryptor.update(self.data) + encryptor.finalize()
        return self.initialization_vector + encrypted_payload

    @classmethod
    def from_sl2_file(
        cls, path: Path, settings: EncryptionSettings
    ) -> list[BND4Entry]:
        # global original_sl2_path, bnd4_entries
        # original_sl2_path = input_file
        bnd4_entries = []

        # try:
        #    with open(input_file, "rb") as f:
        #        raw = f.read()
        # except Exception as e:
        #    logger.debug(f"ERROR: Could not read input file: {e}")
        #    return None
        raw = path.read_bytes()
        logger.debug(f"Read {len(raw)} bytes from {path}.")
        if raw[0:4] != b"BND4":
            raise ValueError(
                "'BND4' header not found! This doesn't appear to be a valid SL2 file."
            )
        else:
            logger.debug("Found BND4 header.")

        num_bnd4_entries = struct.unpack("<i", raw[12:16])[0]
        logger.debug(f"Number of BND4 entries: {num_bnd4_entries}")

        unicode_flag = raw[48] == 1
        logger.debug(f"Unicode flag: {unicode_flag}")
        logger.debug("")

        BND4_HEADER_LEN = 64
        BND4_ENTRY_HEADER_LEN = 32

        # script_dir = os.path.dirname(os.path.abspath(__file__))
        # output_folder = os.path.join(script_dir, "decrypted_output")

        for i in range(num_bnd4_entries):
            pos = BND4_HEADER_LEN + (BND4_ENTRY_HEADER_LEN * i)
            if pos + BND4_ENTRY_HEADER_LEN > len(raw):
                logger.debug(
                    f"Warning: File too small to read entry #{i} header"
                )
                break
            entry_header = raw[pos : pos + BND4_ENTRY_HEADER_LEN]
            if entry_header[0:8] != b"\x40\x00\x00\x00\xff\xff\xff\xff":
                logger.debug(
                    f"Warning: Entry header #{i} does not match expected magic value - skipping"
                )
                continue
            entry_size = struct.unpack("<i", entry_header[8:12])[0]
            entry_data_offset = struct.unpack("<i", entry_header[16:20])[0]
            entry_name_offset = struct.unpack("<i", entry_header[20:24])[0]
            # TODO: remove?
            # entry_footer_length = struct.unpack("<i", entry_header[24:28])[0]
            if entry_size <= 0 or entry_size > 1000000000:
                logger.debug(
                    f"Warning: Entry #{i} has invalid size: {entry_size} - skipping"
                )
                continue
            if entry_data_offset <= 0 or entry_data_offset + entry_size > len(
                raw
            ):
                logger.debug(
                    f"Warning: Entry #{i} has invalid data offset: {entry_data_offset} - skipping"
                )
                continue
            if entry_name_offset <= 0 or entry_name_offset >= len(raw):
                logger.debug(
                    f"Warning: Entry #{i} has invalid name offset: {entry_name_offset} - skipping"
                )
                continue
            logger.debug(
                f"Processing Entry #{i} (Size: {entry_size}, Offset: {entry_data_offset})"
            )

            encrypted_data = raw[
                entry_data_offset : entry_data_offset + entry_size
            ]

            try:
                # TODO: settings?
                entry = BND4Entry.decrypt(encrypted_data)
                bnd4_entries.append(entry)
            except Exception as e:
                logger.debug(f"Error processing entry #{i}: {str(e)}")
                continue

        logger.debug(
            f"\nDONE! Successfully decrypted {len(bnd4_entries)} of {num_bnd4_entries} entries."
        )
        # save_index_mapping(bnd4_entries, output_folder)
        return bnd4_entries


def read_int(data: ByteString, offset: int, *, size: int) -> int:
    return int.from_bytes(data[offset : offset + size], "little")


def read_int32(data: ByteString, offset: int) -> int:
    return read_int(data, offset, size=4)


def read_int16(data: ByteString, offset: int) -> int:
    return read_int(data, offset, size=2)


@dataclass
class RelicData:
    EMPTY_SLOT_DATA: ClassVar[bytes] = b"\x00\x00\x00\x00\xff\xff\xff\xff"
    EMPTY_EFFECT_ID: ClassVar[int] = 0xFFFFFFFF
    data: bytes
    slot_index: int = 0
    item_id: int = 0
    effect_ids: list[int] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        # NOTE: not using self.data
        return not self.slot_index and not self.item_id and not self.effect_ids

    @classmethod
    def from_data_offset(cls, data: ByteString, offset: int) -> RelicData:
        if len(data) <= offset + 8:
            raise ValueError("not enough data for a relic, even an empty one.")

        memory = memoryview(data)
        if memory[offset : offset + 8] == cls.EMPTY_SLOT_DATA:
            return RelicData(data=cls.EMPTY_SLOT_DATA)
        # index = memory[offset:offset+2]  # unused?
        type_bytes = memory[offset + 2 : offset + 2 + 2]
        # check for a valid first id byte
        # TODO: it looks like all my relics are 0x80... what are the others?
        if type_bytes[0] not in (0x80, 0x83, 0x81, 0x82, 0x84, 0x85):
            raise ValueError(f"type byte 0 is invalid: {type_bytes[0]}")
        slot_size = {
            0x80: 80,  # ???
            0x90: 16,  # ???
            0xC0: 72,  # these are relics
        }.get(type_bytes[1], 0)
        if not slot_size:
            raise ValueError(f"type byte 1 is invalid: {type_bytes[1]}")
        if type_bytes[1] != 0xC0:
            print(f"Found relic entry of unknown type: 0x{type_bytes.hex()}")
            return RelicData(data=bytes(memory[offset : offset + slot_size]))
        return cls(
            data=bytes(memory[offset : offset + slot_size]),  # copy the data
            slot_index=read_int16(memory, offset),
            # TODO: what's at offset + 2 to 4 ?
            item_id=read_int16(memory, offset + 4),
            effect_ids=[
                read_int32(memory, offset + 16),
                read_int32(memory, offset + 20),
                read_int32(memory, offset + 24),
            ],
        )


@dataclass(frozen=True)
class SaveData:
    MINIMUM_NAME_LENGTH: ClassVar[int] = 3  # smaller matches non-name things

    data: bytes = field(repr=False)
    name: str = field(default="", kw_only=True)
    name_offset: int = field(default=-1, init=False)

    def read_int(self, offset: int, *, size: int = 4) -> int:
        return int.from_bytes(self.data[offset : offset + size], "little")

    @cached_property
    def murk(self) -> int:
        return self.read_int(self.name_offset + 52)

    @cached_property
    def sigils(self) -> int:
        return self.read_int(self.name_offset - 64)

    def __post_init__(self) -> None:
        if self.name:
            encoded_name = self.name.encode("utf-16le")
            name_offset = self.data.find(encoded_name)
            i = name_offset + len(encoded_name)
        else:
            name_offset = -1
            for i in range(0, len(self.data), 2):
                if 32 <= self.data[i] <= 126 and not self.data[i + 1]:
                    if name_offset == -1:
                        name_offset = i
                elif name_offset != -1:
                    if (i - name_offset) > SaveData.MINIMUM_NAME_LENGTH * 2:
                        break
                    name_offset = -1
        if name_offset == -1:
            raise ValueError("name couldn't be located in the data.")
        # because frozen...
        object.__setattr__(
            self, "name", self.data[name_offset:i].decode("utf-16le")
        )
        object.__setattr__(self, "name_offset", name_offset)

    def get_relics(self) -> list[RelicData]:
        pos = 0  # start at the beginning; other tools use 32
        search_end = self.name_offset  # - 100
        while pos < (search_end - 8):  # 8 bytes needed for check?
            # TODO: what's at 0 and 1 offset?  looks like slot_index for 0xC0
            offset = pos
            relics: list[RelicData] = []
            entries = 0
            while offset < (search_end - 8):
                try:
                    relic = RelicData.from_data_offset(self.data, offset)
                    offset += len(relic.data)
                    entries += 1
                    if not relic.empty:
                        relics.append(relic)
                except Exception as ex:
                    print(ex)
                    break
            if entries >= 2:
                break
            relics.clear()
            pos += 1
        print(f"Found relics at offset {pos}")
        return relics


@dataclass
class SaveFile:
    saves: list[SaveData]

    @classmethod
    def from_sl2_file(
        cls, path: Path, settings: EncryptionSettings, *, name: str = ""
    ) -> SaveFile:
        bnd4_entries = BND4Entry.from_sl2_file(path, settings)
        saves: list[SaveData] = []

        for entry in bnd4_entries:
            try:
                save_data = SaveData(entry.data, name=name)
                saves.append(save_data)
                print(f"Parsed save slot with name: {save_data.name}")
            except Exception:
                print("WARNING: couldn't parse")
        print(f"Parsed {len(saves)} saves successfully.")
        return SaveFile(saves)


@dataclass
class RelicProcessor:
    save_data: SaveData
    effect_data: dict[str, dict[str, str]] = field(
        default_factory=dict, init=False
    )
    item_data: dict[str, dict[str, str]] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        self.effect_data: dict[str, dict[str, str]] = get_resource_json(
            "effects.json"
        )
        self.item_data: dict[str, dict[str, str]] = get_resource_json(
            "items.json"
        )
        print(f"LOADED PROCESSOR FOR: {self.save_data.name}")

    def relic_report(self, color_filter: str = "") -> list[RelicData]:
        matched: list[RelicData] = []
        count = 0
        for relic in self.save_data.get_relics():
            count += 1
            attributes = self.item_data.get(
                str(relic.item_id)
            )  # TODO: why str?
            color = "MISSING"
            if attributes:
                name = attributes["name"]
                color = attributes.get("color", color)
                print(f"RELIC {relic.item_id}: [{color}] {name}")
            else:
                print(f"MISSING RELIC: couldn't find id {relic.item_id}")

            for effect_id in relic.effect_ids:
                if effect_id == RelicData.EMPTY_EFFECT_ID:
                    continue
                attributes = self.effect_data.get(str(effect_id))
                if attributes:
                    print(f"- {effect_id} {attributes['name']}")
                else:
                    print(f"- WARNING: couldn't find effect id {effect_id}")

            if not color_filter or color == color_filter:
                matched.append(relic)
        print(f"==== Listed {count} relics. ====")
        return matched
