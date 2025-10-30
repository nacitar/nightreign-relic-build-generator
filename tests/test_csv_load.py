from __future__ import annotations

from dataclasses import InitVar, dataclass, field

from nightreign_relic_build_generator.utility import csv_load


@dataclass
class FromConstructor:
    value: int = field(init=False, default=0)
    str_value: InitVar[str | None] = None

    def __post_init__(self, str_value: str | None) -> None:
        if str_value is not None:
            self.value = int(str_value)


@dataclass
class FromMethod:
    value: int

    @classmethod
    def from_string(cls, value: str) -> FromMethod:
        return cls(int(value))


@dataclass
class ComplexClass:
    number: int = field(metadata={"csv_key": "CUSTOM_number"})
    float_number: float
    from_constructor: FromConstructor
    from_method: FromMethod
    string: str


def test_csv_load_dataclass() -> None:
    values = ["1,3.14,2,3,Hello", "4,2.71,5,6,Goodbye"]
    i = 0
    for instance in csv_load(
        ["CUSTOM_number,float_number,from_constructor,from_method,string"]
        + values,
        dataclass=ComplexClass,
    ):
        tokens = values[i].split(",")
        i = i + 1
        assert instance.number == int(tokens[0])
        assert instance.float_number == float(tokens[1])
        assert instance.from_constructor.value == int(tokens[2])
        assert instance.from_method.value == int(tokens[3])
        assert instance.string == tokens[4]
