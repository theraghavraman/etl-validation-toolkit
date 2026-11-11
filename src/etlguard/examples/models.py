"""Demonstration models for customer dimensions and transaction facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(min_length=4, max_length=8, pattern=r"^C\d{3,7}$")
    full_name: str = Field(min_length=2, max_length=80)
    email: str | None = Field(default=None, max_length=150, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    signup_date: date
    end_date: date | None = None
    status: Literal["active", "inactive"]
    credit_limit: Decimal = Field(ge=0, le=1000000, max_digits=14, decimal_places=2)

    @model_validator(mode="after")
    def check_dates(self) -> Customer:
        if self.end_date is not None and self.end_date < self.signup_date:
            raise ValueError("end_date must be on or after signup_date")
        return self


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=4, pattern=r"^T\d{3,}$")
    customer_id: str = Field(pattern=r"^C\d{3,7}$")
    transaction_date: date
    type: Literal["debit", "credit"]
    amount: Decimal = Field(ge=0, le=10000000, max_digits=16, decimal_places=2)
    description: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def debit_positive(self) -> Transaction:
        if self.type == "debit" and self.amount <= 0:
            raise ValueError("debit amount must be greater than zero")
        return self
