from __future__ import annotations

from dataclasses import dataclass

from nightreign_relic_build_generator.utility import csv_load


def test_csv_load_dataclass_from_constructor() -> None:
    @dataclass
    class FromConstructor:
        value: str

    values = ["moo", "1"]
    i = 0
    for instance in csv_load(["value"] + values, dataclass=FromConstructor):
        assert isinstance(
            instance, FromConstructor
        ), f"\nWrong Type: {type(instance).__name__}"
        assert instance.value == values[i]
        i += 1


def test_csv_load_dataclass_from_method() -> None:
    @dataclass
    class FromMethod:
        value: int

        @classmethod
        def from_string(cls, value: str) -> FromMethod:
            return cls(int(value))

    values = ["1", "2"]
    i = 0
    for instance in csv_load(["value"] + values, dataclass=FromMethod):
        assert isinstance(
            instance, FromMethod
        ), f"\nWrong Type: {type(instance).__name__}"
        assert instance.value == int(values[i])
        i += 1
