"""Deterministic regressions for generic analytical reliability guidance."""

from datetime import date
from pathlib import Path

import pandas as pd


def test_profitability_guidance_requires_grain_safe_spend_aggregation() -> None:
    guidance = Path("skills/business_analytics.md").read_text(encoding="utf-8").lower()

    assert "grain" in guidance
    assert "aggregate each source to the common reporting grain" in guidance
    assert "never" in guidance and "marketing spend" in guidance
    assert "net revenue" in guidance
    assert "cogs" in guidance
    assert "contribution" in guidance and "before marketing" in guidance
    assert "reporting contribution profit" in guidance

    spend = pd.DataFrame(
        {"date": [date(2025, 1, 1)], "channel": ["Paid"], "spend": [100.0]}
    )
    orders = pd.DataFrame(
        {
            "date": [date(2025, 1, 1), date(2025, 1, 1)],
            "channel": ["Paid", "Paid"],
            "net_revenue": [60.0, 40.0],
        }
    )
    unsafe_spend = orders.merge(spend, on=["date", "channel"])["spend"].sum()
    grouped_spend = spend.groupby(["date", "channel"], as_index=False)["spend"].sum()
    safe_spend = grouped_spend["spend"].sum()

    assert unsafe_spend == 200.0
    assert safe_spend == 100.0


def test_period_guidance_requires_explicit_q1_q2_filtering() -> None:
    guidance = Path("skills/business_analytics.md").read_text(encoding="utf-8").lower()
    statistical_guidance = (
        Path("skills/statistical_analysis.md").read_text(encoding="utf-8").lower()
    )

    assert "explicit date boundaries" in guidance
    assert "every period that is not q1 as q2" in guidance
    assert "reconcile derived cohort counts" in guidance
    assert "explicit date boundaries" in statistical_guidance
    assert "every period that is not q1 as q2" in statistical_guidance

    periods = pd.Series(
        pd.to_datetime(["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01"])
    )
    q1 = periods.dt.quarter.eq(1)
    q2 = periods.dt.quarter.eq(2)
    assert q1.sum() == 1
    assert q2.sum() == 1
    assert not q2.iloc[2]
    assert not q2.iloc[3]
