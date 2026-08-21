"""Canonical models for local operational scheduling and controls."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class OperationalTask(StrEnum):
    CIVICTRACKER = "civictracker"
    BROAD_NEWS = "broad_news"
    ACTIVE_NEWS = "active_news"
    EARNINGS = "earnings"
    DAILY_PRICES = "daily_prices"
    INTRADAY_PRICES = "intraday_prices"
    AFTER_CLOSE_REPORT = "after_close_report"
    PROSPECTIVE_OUTCOMES = "prospective_outcomes"


class ManualAction(StrEnum):
    SOURCES = "sources"
    MARKET = "market"
    CANDIDATES = "candidates"
    ANALYSIS = "analysis"
    STRATEGIES = "strategies"
    OUTCOMES = "outcomes"
    ALL = "all"


class ScheduledOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: OperationalTask
    description: str
    scheduled_for: AwareDatetime
    market_session_date: date | None
    market_session_only: bool


class OperationalScheduleRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: OperationalTask
    scheduled_for: AwareDatetime
    status: str
    started_at: AwareDatetime
    finished_at: AwareDatetime | None
    error_summary: str | None


class ManualActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: ManualAction
    run_id: str
    summary: str
    completed_at: AwareDatetime


class OutcomeReconciliationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessments_scanned: int = Field(ge=0)
    baselines_recorded: int = Field(ge=0)
    outcomes_recorded: int = Field(ge=0)
    still_pending: int = Field(ge=0)
    completed_at: AwareDatetime
