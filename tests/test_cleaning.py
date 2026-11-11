from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from etlguard.cleaning import Cleaner, date_iso, decimal_number, snake_case
from etlguard.config import CleaningSettings


def test_snake_case_camel_spaces_and_hyphens():
    assert snake_case("Customer ID") == "customer_id"
    assert snake_case("SignupDate") == "signup_date"
    assert snake_case(" Credit-Limit (€) ") == "credit_limit"


def test_all_cleaning_steps():
    cleaner = Cleaner(CleaningSettings(
        rename_map={"Client No": "Customer ID"},
        date_columns=["signup_date"], number_columns=["credit_limit"],
        lowercase=["email"], uppercase=["customer_id"],
    ))
    assert cleaner.clean({
        "Client No": "  c001 ", "E-mail": " NA ",
        "Signup Date": "13/02/2026", "Credit Limit": " ₹1,245.00 ",
    }) == {
        "customer_id": "C001", "e_mail": None,
        "signup_date": "2026-02-13", "credit_limit": Decimal("1245.00"),
    }


@pytest.mark.parametrize("token", ["", "  ", "NA", "N/A", "null", "NuLl", "-"])
def test_null_tokens(token):
    assert Cleaner(CleaningSettings()).clean({"Field": token}) == {"field": None}


@pytest.mark.parametrize("value,expected", [
    ("$1,200.90", Decimal("1200.90")),
    ("(1,200.90)", Decimal("-1200.90")),
    ("€2,000", Decimal("2000")),
    ("123", Decimal("123")),
    ("nope", "nope"),
    ("NaN", "NaN"),
])
def test_decimal_normalization(value, expected):
    assert decimal_number(value) == expected


def test_dates_with_errors_and_native_types():
    formats = CleaningSettings().date_formats
    assert date_iso("2026/01/05", formats) == "2026-01-05"
    assert date_iso("05/01/2026", formats) == "2026-01-05"
    assert date_iso("2026.01.05", formats) == "2026-01-05"
    assert date_iso("bad date", formats) == "bad date"
    assert date_iso(date(2026, 1, 5), formats) == "2026-01-05"
    assert date_iso(datetime(2026, 1, 5, 12), formats) == "2026-01-05"


def test_rename_collision_becomes_a_row_reject():
    with pytest.raises(ValueError, match="normalise"):
        Cleaner(CleaningSettings()).clean({"Customer ID": "C001", "customer_id": "C002"})


def test_trim_disabled():
    assert Cleaner(CleaningSettings(trim=False)).clean({"title": " hello "}) == {
        "title": " hello "
    }


def test_case_conversion_and_idempotence():
    cleaner = Cleaner(CleaningSettings(lowercase=["email"], uppercase=["status"]))
    raw = {" Email ": " Alice@EXAMPLE.COM ", "Status": " active "}
    once = cleaner.clean(raw)
    assert once == {"email": "alice@example.com", "status": "ACTIVE"}
    assert cleaner.clean(once) == once
