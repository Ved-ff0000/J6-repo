"""Conservative literal parsing only. No locale/currency guesses, date arithmetic or rule decisions."""

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from app.extraction.schemas import (
    GroupName,
    NormalizedDate,
    NormalizedDuration,
    NormalizedMoney,
    NormalizedPercentage,
    NormalizedValue,
    Proposal,
)

NUMBER = r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?"
MONEY_NUMBER = r"(?:0|[1-9][0-9]{0,11}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,3})(?:\.[0-9]{1,6})?"
CURRENCIES = "USD|EUR|GBP|INR|AUD|CAD|NZD|SGD|HKD|JPY|CNY|CHF|AED|SAR"
PERIODS: dict[str, Literal["month", "year", "week", "day", "one_time"]] = {
    "per month": "month",
    "per year": "year",
    "per week": "week",
    "per day": "day",
    "one-time": "one_time",
}
DURATION_FIELDS = {
    ("renewals", "duration"),
    ("renewals", "notice_period"),
    ("termination", "notice_period"),
    ("notices", "period"),
}
MONEY_FIELDS = {
    ("rent", "base_rent"),
    ("rent", "security_deposit"),
    ("termination", "fee"),
    ("cam", "charge"),
    ("insurance", "limit"),
}
PERCENT_FIELDS = {
    ("rent", "percentage_rent"),
    ("escalation", "rate"),
    ("escalation", "cap"),
    ("escalation", "floor"),
    ("cam", "cap"),
}


def decimal_text(value: str) -> str:
    return format(Decimal(value.replace(",", "")), "f")


def normalize(
    group: GroupName, proposal: Proposal
) -> tuple[NormalizedValue | None, Literal["NORMALIZED", "REQUIRES_REVIEW", "NOT_APPLICABLE"]]:
    if proposal.interpretation != "EXPLICIT":
        return None, "REQUIRES_REVIEW"
    raw = proposal.value.strip()
    key = (group, proposal.field)
    if group == "dates":
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
            try:
                return NormalizedDate(iso_date=date.fromisoformat(raw).isoformat()), "NORMALIZED"
            except ValueError:
                pass
        return None, "REQUIRES_REVIEW"
    if key in MONEY_FIELDS:
        match = re.fullmatch(
            rf"({CURRENCIES}) ({MONEY_NUMBER})(?: (per month|per year|per week|per day|one-time))?",
            raw,
        )
        if match:
            return NormalizedMoney(
                currency=match[1], amount=decimal_text(match[2]), period=PERIODS.get(match[3])
            ), "NORMALIZED"
        return None, "REQUIRES_REVIEW"
    if key in DURATION_FIELDS:
        match = re.fullmatch(rf"({NUMBER}) (days|weeks|months|years)", raw)
        if match:
            return NormalizedDuration.model_validate(
                {"amount": decimal_text(match[1]), "unit": match[2]}
            ), "NORMALIZED"
        return None, "REQUIRES_REVIEW"
    if key in PERCENT_FIELDS:
        match = re.fullmatch(rf"({NUMBER})%", raw)
        if match:
            return NormalizedPercentage(percent=decimal_text(match[1])), "NORMALIZED"
        return None, "REQUIRES_REVIEW"
    return None, "NOT_APPLICABLE"
