from __future__ import annotations

from dataclasses import InitVar, dataclass, field

from nightreign_relic_build_generator.utility import (
    csv_load,
    register_converter,
)


@dataclass
class FromRegistered:
    value: int = field(init=False, default=0)
    str_value: InitVar[str | None] = None

    def __post_init__(self, str_value: str | None) -> None:
        if str_value is not None:
            self.value = int(str_value)


register_converter(FromRegistered, FromRegistered)


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
    from_registered: FromRegistered
    from_method: FromMethod
    string: str
    extra_argument: int = 0


def test_csv_load_dataclass() -> None:
    values = ["1,3.14,2,3,Hello", "4,2.71,5,6,Goodbye"]
    i = 0
    for instance in csv_load(
        ["CUSTOM_number,float_number,from_registered,from_method,string"]
        + values,
        dataclass=ComplexClass,
    ):
        tokens = values[i].split(",")
        i = i + 1
        assert instance.number == int(tokens[0])
        assert instance.float_number == float(tokens[1])
        assert instance.from_registered.value == int(tokens[2])
        assert instance.from_method.value == int(tokens[3])
        assert instance.string == tokens[4]


def test_csv_load_dataclass_custom_init() -> None:
    def create_instance(
        number: int,
        float_number: float,
        from_registered: FromRegistered,
        from_method: FromMethod,
        string: str,
        extra_argument: int = 1,
    ) -> ComplexClass:
        return ComplexClass(
            number=number + 10,
            float_number=float_number + 1,
            from_registered=from_registered,
            from_method=from_method,
            string=string,
            extra_argument=extra_argument,
        )

    values = ["1,3.14,2,3,Hello", "4,2.71,5,6,Goodbye"]
    i = 0
    for instance in csv_load(
        ["CUSTOM_number,float_number,from_registered,from_method,string"]
        + values,
        dataclass=ComplexClass,
        init_arguments={"extra_argument": 5},
        init_function=create_instance,
    ):
        tokens = values[i].split(",")
        i = i + 1
        assert instance.number == int(tokens[0]) + 10
        assert instance.float_number == float(tokens[1]) + 1
        assert instance.from_registered.value == int(tokens[2])
        assert instance.from_method.value == int(tokens[3])
        assert instance.string == tokens[4]
        assert instance.extra_argument == 5


def test_csv_load_dict() -> None:
    values = ["1,3.14,2,3,Hello", "4,2.71,5,6,Goodbye"]
    i = 0
    for instance in csv_load(
        ["number,float_number,from_registered,from_method,string"] + values,
        field_to_column_name={
            "the_float": "float_number",
            "the_string": "string",
        },
        init_arguments={"extra_argument": "extra"},
    ):
        tokens = values[i].split(",")
        i = i + 1
        assert instance["the_float"] == tokens[1]
        assert instance["the_string"] == tokens[4]
        assert instance["extra_argument"] == "extra"
        assert len(instance) == 3
