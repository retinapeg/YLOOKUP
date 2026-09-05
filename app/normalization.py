"""Fail-closed deterministic normalization for reconciliation inputs.

Extraction may preserve values as strings or may supply already-coerced values.
This module is deliberately independent of extraction so reconciliation can
validate both the structured value and the source evidence before comparing it
with a book-of-record value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Optional
import unicodedata


class NormalizationError(ValueError):
    """Raised when a value cannot be normalized without guessing."""


class AmbiguousValueError(NormalizationError):
    """Raised when more than one defensible interpretation is present."""


class UnsupportedValueError(NormalizationError):
    """Raised when a value uses syntax outside the supported contract."""


@dataclass(frozen=True)
class NormalizedMoney:
    """A normalized numeric amount and any currency explicitly attached to it."""

    amount: Decimal
    currency: Optional[str] = None


_CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
_CURRENCY_PREFIX = re.compile(r"^(?P<token>[A-Za-z]{3}|[£$€])\s*")
_CURRENCY_SUFFIX = re.compile(r"\s*(?P<token>[A-Za-z]{3}|[£$€])$")
_EVIDENCE_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:\(\s*)?"
    r"(?:[-+]\s*)?"
    r"(?:(?:[A-Za-z]{3}|[£$€])\s*)?"
    r"(?:[-+]\s*)?"
    r"\d(?:[\d\s.,'\u2019]*\d)?"
    r"(?:\s*(?:[A-Za-z]{3}|[£$€]))?"
    r"(?:\s*\))?"
    r"(?![A-Za-z0-9])"
)


def normalize_currency_code(value: object) -> str:
    """Normalize a supported symbol or three-letter ASCII currency code."""

    raw = getattr(value, "value", value)
    text = unicodedata.normalize("NFKC", str(raw)).strip()
    if text in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[text]
    if re.fullmatch(r"[A-Za-z]{3}", text) is None:
        raise UnsupportedValueError("currency must be a three-letter code")
    return text.upper()


def _currency_from_token(token: str) -> str:
    return _CURRENCY_SYMBOLS.get(token, token.upper())


def _strip_sign_and_currency(text: str) -> tuple[str, int, Optional[str]]:
    accounting_negative = text.startswith("(") or text.endswith(")")
    if accounting_negative:
        if not (text.startswith("(") and text.endswith(")")):
            raise UnsupportedValueError("unbalanced accounting parentheses")
        text = text[1:-1].strip()

    sign = -1 if accounting_negative else 1
    explicit_sign = False
    if text.startswith(("-", "+")):
        explicit_sign = True
        sign = -1 if text[0] == "-" else 1
        text = text[1:].strip()

    currencies = []
    prefix = _CURRENCY_PREFIX.match(text)
    if prefix is not None:
        currencies.append(_currency_from_token(prefix.group("token")))
        text = text[prefix.end() :].strip()

    if text.startswith(("-", "+")):
        if explicit_sign:
            raise AmbiguousValueError("more than one explicit sign")
        explicit_sign = True
        sign = -1 if text[0] == "-" else 1
        text = text[1:].strip()

    suffix = _CURRENCY_SUFFIX.search(text)
    if suffix is not None:
        currencies.append(_currency_from_token(suffix.group("token")))
        text = text[: suffix.start()].strip()

    if accounting_negative and explicit_sign:
        raise AmbiguousValueError("accounting parentheses and an explicit sign conflict")
    if len(set(currencies)) > 1:
        raise AmbiguousValueError("conflicting currency markers")
    currency = currencies[0] if currencies else None
    return text, sign, currency


def _decimal_separator(text: str) -> Optional[str]:
    commas = text.count(",")
    dots = text.count(".")
    if commas and dots:
        separator = "," if text.rfind(",") > text.rfind(".") else "."
        if text.count(separator) != 1:
            raise AmbiguousValueError("repeated decimal separator")
        fraction = text.rsplit(separator, 1)[1]
        if not 1 <= len(fraction) <= 2 or not fraction.isdigit():
            raise AmbiguousValueError("ambiguous mixed monetary separators")
        return separator

    count = commas or dots
    if count == 0:
        return None
    separator = "," if commas else "."
    if count > 1:
        return None

    integer, fraction = text.split(separator, 1)
    if not fraction.isdigit():
        raise UnsupportedValueError("monetary separator must be followed by digits")
    if 1 <= len(fraction) <= 2:
        return separator
    if len(fraction) == 3:
        # For the supported two-decimal monetary contract, a three-digit group
        # is a thousands group. In particular, 625,00 remains 625.00 while
        # 625,000 remains 625000.
        if integer.startswith("0"):
            raise AmbiguousValueError(
                "fractional precision beyond two digits is unsupported"
            )
        return None
    raise AmbiguousValueError("monetary precision or grouping is ambiguous")


def _normalize_integer_groups(text: str, grouping_separator: Optional[str]) -> str:
    grouping = [
        separator
        for separator in (grouping_separator, " ", "'", "\u2019")
        if separator and separator in text
    ]
    if len(grouping) > 1:
        raise AmbiguousValueError("mixed thousands separators")
    if not grouping:
        if not text.isdigit():
            raise UnsupportedValueError("monetary value contains unsupported characters")
        return text

    separator = grouping[0]
    groups = text.split(separator)
    if (
        not groups
        or not groups[0].isdigit()
        or not 1 <= len(groups[0]) <= 3
        or (len(groups) > 1 and groups[0].startswith("0"))
        or any(not group.isdigit() or len(group) != 3 for group in groups[1:])
    ):
        raise AmbiguousValueError("invalid thousands grouping")
    return "".join(groups)


def normalize_monetary_value(value: object) -> NormalizedMoney:
    """Normalize common US/EU/accounting money syntax without silent guessing.

    A comma followed by one or two digits is a decimal separator, so ``625,00``
    is normalized to ``625.00`` and is never inflated by merely deleting commas.
    Invalid or ambiguous groupings raise ``NormalizationError`` and callers must
    abstain from a deterministic comparison.
    """

    raw = getattr(value, "value", value)
    if isinstance(raw, bool) or raw is None:
        raise UnsupportedValueError("value is not a monetary amount")
    if isinstance(raw, Decimal):
        if not raw.is_finite():
            raise UnsupportedValueError("monetary values must be finite")
        return NormalizedMoney(raw)
    if isinstance(raw, int):
        return NormalizedMoney(Decimal(raw))
    if isinstance(raw, float):
        if not math.isfinite(raw):
            raise UnsupportedValueError("monetary values must be finite")
        return NormalizedMoney(Decimal(str(raw)))
    if not isinstance(raw, str):
        raise UnsupportedValueError("unsupported monetary value type")

    text = unicodedata.normalize("NFKC", raw).replace("\u2212", "-").strip()
    if not text:
        raise UnsupportedValueError("empty monetary value")
    if "%" in text:
        raise UnsupportedValueError("percentages are not monetary amounts")

    text, sign, currency = _strip_sign_and_currency(text)
    if not text or re.search(r"[^0-9.,'\u2019\s]", text):
        raise UnsupportedValueError("monetary value contains unsupported characters")

    separator = _decimal_separator(text)
    if separator is None:
        integer_text = text
        fraction = None
        punctuation_group = "," if "," in text else "." if "." in text else None
    else:
        integer_text, fraction = text.rsplit(separator, 1)
        if separator == "," and "." in integer_text:
            punctuation_group = "."
        elif separator == "." and "," in integer_text:
            punctuation_group = ","
        else:
            punctuation_group = None

    integer = _normalize_integer_groups(integer_text, punctuation_group)
    canonical = integer if fraction is None else f"{integer}.{fraction}"
    try:
        amount = Decimal(canonical) * sign
    except InvalidOperation as exc:
        raise UnsupportedValueError("invalid monetary amount") from exc
    if not amount.is_finite():
        raise UnsupportedValueError("monetary values must be finite")
    return NormalizedMoney(amount=amount, currency=currency)


def normalize_monetary_evidence(evidence: str) -> NormalizedMoney:
    """Normalize the single monetary literal cited by a provenance snippet."""

    text = unicodedata.normalize("NFKC", evidence).strip()
    candidate_region = text.split(":", 1)[1].strip() if ":" in text else text
    if "%" in candidate_region:
        raise UnsupportedValueError("percentage evidence is not a monetary amount")

    candidates = [
        match.group(0).strip()
        for match in _EVIDENCE_NUMBER.finditer(candidate_region)
    ]
    if not candidates:
        raise UnsupportedValueError("evidence does not contain a monetary amount")
    if len(candidates) != 1:
        raise AmbiguousValueError("evidence contains multiple numeric candidates")
    return normalize_monetary_value(candidates[0])
