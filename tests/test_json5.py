from nightreign_build_generator.utility import json5_load


def test_strip_json5_comments_and_trailing_commas() -> None:
    samples = {
        '{"a": "simple", // comment\n "b": "text",}': {
            "a": "simple",
            "b": "text",
        },
        '{"q": "has quote: \\"inner\\"", "x": 1,}': {
            "q": 'has quote: "inner"',
            "x": 1,
        },
        '{"b": "escaped backslash: \\\\", "y": 2, // comment\n}': {
            "b": "escaped backslash: \\",
            "y": 2,
        },
        """
        {
            "msg": "Line1\\nLine2", // multi-line escape
            "num": 5,
        }
        """: {
            "msg": "Line1\nLine2",
            "num": 5,
        },
    }

    for src, expected_obj in samples.items():
        parsed = json5_load(src)
        assert parsed == expected_obj, (
            f"\nInput:\n{src}"
            f"\nParsed:\n{parsed}"
            f"\nExpected:\n{expected_obj}"
        )
