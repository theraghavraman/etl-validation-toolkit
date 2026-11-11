"""Property-based cleaning invariant: f(f(x)) == f(x) across varied strings."""

from hypothesis import given, settings, strategies as st

from etlguard.cleaning import Cleaner
from etlguard.config import CleaningSettings


@given(
    name=st.text(max_size=70),
    amount=st.one_of(st.text(max_size=40), st.integers(-10_000, 10_000)),
    state=st.text(max_size=20),
)
@settings(max_examples=200)
def test_cleaning_is_idempotent(name, amount, state):
    cleaner = Cleaner(CleaningSettings(
        date_columns=["signup_date"],
        number_columns=["amount"],
        lowercase=["name"], uppercase=["state"],
    ))
    row = {" Name ": name, " Amount ": amount, "State": state}
    once = cleaner.clean(row)
    twice = cleaner.clean(once)
    assert once == twice
