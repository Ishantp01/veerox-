"""Org-aware phone normalization.

A number typed or uploaded without an international prefix gets the org's
``default_country_code`` prepended, so calling, WhatsApp, CRM and campaigns
all store the same E.164 form.
"""

from __future__ import annotations

import re

COUNTRY_CODE_PATTERN = re.compile(r"^\+\d{1,4}$")
DEFAULT_COUNTRY_CODE = "+91"


def normalize_country_code(value: str | None) -> str:
    """Canonical "+NN" form of a dialing code; falls back to the default when
    blank/invalid so a caller never crashes on a missing org setting."""
    digits = re.sub(r"\D", "", value or "")
    code = f"+{digits}" if digits else DEFAULT_COUNTRY_CODE
    return code if COUNTRY_CODE_PATTERN.match(code) else DEFAULT_COUNTRY_CODE


def validate_country_code(value: str) -> str:
    """Strict variant for API input: "91"/"+91" -> "+91", anything that isn't
    1-4 digits raises (pydantic surfaces it as a 422)."""
    digits = re.sub(r"\D", "", value or "")
    code = f"+{digits}"
    if not COUNTRY_CODE_PATTERN.match(code):
        raise ValueError("country code must be 1-4 digits, e.g. +91")
    return code


def normalize_phone(raw: str | None, country_code: str | None = None) -> str:
    """Return ``raw`` as E.164, prepending ``country_code`` when it has no
    international prefix.

    - ``+91 98765-43210`` / ``0091 98765 43210`` -> kept as international.
    - ``09876543210`` -> leading trunk ``0`` dropped, then prefixed.
    - ``9876543210`` -> prefixed.
    - A bare number that already starts with the country digits and is too
      long to be a national number (``919876543210`` under +91, 12 digits) is
      not double-prefixed. National numbers are at most 10 digits, so a
      10-digit local number that merely starts with "91" (``9179609988``) is
      still treated as local.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    has_plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""

    if has_plus:
        return f"+{digits}"
    if digits.startswith("00"):
        return f"+{digits[2:]}"

    code_digits = normalize_country_code(country_code)[1:]
    national = digits.lstrip("0") if digits.startswith("0") else digits
    if digits.startswith(code_digits) and len(digits) >= len(code_digits) + 10:
        return f"+{digits}"
    return f"+{code_digits}{national}"
