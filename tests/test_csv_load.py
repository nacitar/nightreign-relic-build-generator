from __future__ import annotations

from dataclasses import InitVar, dataclass, field

import pytest

from nightreign_build_generator.utility import (
    ColumnSubsetError,
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


register_converter(FromRegistered, FromRegistered)  # use constructor


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


@pytest.fixture
def csv_header() -> str:
    return "CUSTOM_number,float_number,from_registered,from_method,string"


@pytest.fixture
def csv_rows() -> list[str]:
    return ["1,3.14,2,3,Hello", "4,2.71,5,6,Goodbye"]


@pytest.fixture
def csv_lines(csv_header: str, csv_rows: list[str]) -> list[str]:
    return [csv_header, *csv_rows]


def test_csv_load_into_dataclass(
    csv_lines: list[str], csv_rows: list[str]
) -> None:
    """Verify normal dataclass loading with automatic converters
    and metadata mapping."""
    instances = list(csv_load(csv_lines, dataclass=ComplexClass))
    assert len(instances) == len(csv_rows)

    for instance, row in zip(instances, csv_rows):
        n, f, r, m, s = row.split(",")
        assert instance.number == int(n)
        assert instance.float_number == float(f)
        assert instance.from_registered.value == int(r)
        assert instance.from_method.value == int(m)
        assert instance.string == s


def test_csv_load_raises_if_not_dataclass(csv_lines: list[str]) -> None:
    """Passing a non-dataclass type to `dataclass` should raise TypeError."""

    class NotADataClass:
        pass

    with pytest.raises(TypeError, match="isn't a dataclass"):
        list(csv_load(csv_lines, dataclass=NotADataClass))


def test_csv_load_dataclass_with_custom_init(
    csv_lines: list[str], csv_rows: list[str]
) -> None:
    """Verify `init_function` and `init_arguments` alter the resulting
    instances as expected."""

    def custom_factory(
        number: int,
        float_number: float,
        from_registered: FromRegistered,
        from_method: FromMethod,
        string: str,
        extra_argument: int = 1,
    ) -> ComplexClass:
        return ComplexClass(
            number=number + 10,
            float_number=float_number + 1.0,
            from_registered=from_registered,
            from_method=from_method,
            string=string,
            extra_argument=extra_argument,
        )

    instances = list(
        csv_load(
            csv_lines,
            dataclass=ComplexClass,
            init_function=custom_factory,
            init_arguments={"extra_argument": 5},
        )
    )
    for instance, row in zip(instances, csv_rows):
        n, f, r, m, s = row.split(",")
        assert instance.number == int(n) + 10
        assert instance.float_number == float(f) + 1
        assert instance.from_registered.value == int(r)
        assert instance.from_method.value == int(m)
        assert instance.string == s
        assert instance.extra_argument == 5


def test_csv_load_into_dict(csv_rows: list[str]) -> None:
    """Ensure csv_load produces dicts when no dataclass is provided."""
    header = "number,float_number,from_registered,from_method,string"
    lines = [header, *csv_rows]
    mapping = {"the_float": "float_number", "the_string": "string"}
    init_args = {"extra_argument": "extra"}

    with pytest.raises(ColumnSubsetError):
        next(
            csv_load(
                lines,
                field_to_column_name=mapping,
                init_arguments=init_args,
                allow_column_subset=False,
            )
        )

    results = list(
        csv_load(lines, field_to_column_name=mapping, init_arguments=init_args)
    )

    for row, result in zip(csv_rows, results):
        n, f, r, m, s = row.split(",")
        assert result["the_float"] == f
        assert result["the_string"] == s
        assert result["extra_argument"] == "extra"
        # Only mapped + injected keys should exist
        assert set(result.keys()) == {
            "the_float",
            "the_string",
            "extra_argument",
        }


def test_csv_load_missing_column_name(csv_rows: list[str]) -> None:
    """Unknown column names should simply be skipped (converter not built)."""
    header = "only_this"
    result = list(csv_load([header, *csv_rows]))
    assert all(isinstance(r, dict) for r in result)
    assert all(
        r == {"only_this": v.split(",")[0]} for r, v in zip(result, csv_rows)
    )


def test_csv_load_custom_delimiter() -> None:
    """Verify custom delimiter works as expected."""
    csv_data = ["a|b|c", "1|2|3"]
    results = list(csv_load(csv_data, delimiter="|"))
    assert results == [{"a": "1", "b": "2", "c": "3"}]
