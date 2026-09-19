import pytest

from apps.api.core.phone import normalize_country_code, normalize_phone


@pytest.mark.parametrize(
    ("raw", "code", "expected"),
    [
        ("9876543210", "+91", "+919876543210"),
        ("09876543210", "+91", "+919876543210"),
        ("+919876543210", "+1", "+919876543210"),
        ("0091 98765 43210", "+1", "+919876543210"),
        ("919876543210", "+91", "+919876543210"),
        ("9179609988", "+91", "+919179609988"),
        ("(415) 555-2671", "+1", "+14155552671"),
        ("98765-43210", None, "+919876543210"),
        ("", "+91", ""),
        ("abc", "+91", ""),
    ],
)
def test_normalize_phone(raw, code, expected):
    assert normalize_phone(raw, code) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("+44", "+44"), ("44", "+44"), (" +1 ", "+1"), (None, "+91"), ("", "+91"), ("+12345", "+91")],
)
def test_normalize_country_code(value, expected):
    assert normalize_country_code(value) == expected
