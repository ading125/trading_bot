"""Immutable strategy-evaluation history and setup intent persistence."""

from __future__ import annotations

from datetime import datetime
import json

from pydantic import AwareDatetime, BaseModel, ConfigDict

from investing_bot.db.database import Database
from investing_bot.models import (
    EntryIntent,
    ExitIntent,
    SetupState,
    StopIntent,
    StrategyExplanation,
    StrategyFeatures,
    StrategyResearchStatus,
    StrategySignal,
)


class StoredStrategyEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_id: str
    cache_key: str
    assessment_id: str
    parameters: dict[str, object]
    parameters_hash: str
    input_hash: str
    provider_id: str
    as_of: AwareDatetime
    research_status: StrategyResearchStatus
    signal: StrategySignal
    created_at: AwareDatetime


class StrategyRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def find_cached(self, cache_key: str) -> StoredStrategyEvaluation | None:
        row = self._database.fetchone(
            f"SELECT {_COLUMNS} FROM strategy_evaluations WHERE cache_key=?",
            [cache_key],
        )
        return None if row is None else _from_row(row)

    def store(self, evaluation: StoredStrategyEvaluation) -> None:
        signal = evaluation.signal
        self._database.execute(
            """
            INSERT INTO strategy_evaluations (
                evaluation_id, cache_key, assessment_id, symbol, strategy_id,
                strategy_version, research_status, parameters_json,
                parameters_hash, input_hash, provider_id, as_of, data_through,
                state, stale, confirmation_blocked, features_json,
                explanation_json, entry_json, stop_json, exit_json,
                reward_to_risk, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            [
                evaluation.evaluation_id,
                evaluation.cache_key,
                evaluation.assessment_id,
                signal.symbol,
                signal.strategy_id,
                signal.strategy_version,
                evaluation.research_status.value,
                _json(evaluation.parameters),
                evaluation.parameters_hash,
                evaluation.input_hash,
                evaluation.provider_id,
                evaluation.as_of,
                signal.features.data_through,
                signal.state.value,
                signal.stale,
                signal.confirmation_blocked,
                signal.features.model_dump_json(),
                signal.explanation.model_dump_json(),
                _optional_json(signal.entry),
                _optional_json(signal.stop),
                _optional_json(signal.exit),
                signal.reward_to_risk,
                evaluation.created_at,
            ],
        )

    def latest(
        self, symbol: str, strategy_id: str
    ) -> StoredStrategyEvaluation | None:
        row = self._database.fetchone(
            f"""
            SELECT {_COLUMNS} FROM strategy_evaluations
            WHERE symbol=? AND strategy_id=?
            ORDER BY as_of DESC, created_at DESC LIMIT 1
            """,
            [symbol.upper(), strategy_id],
        )
        return None if row is None else _from_row(row)

    def list_latest(self, *, limit: int = 100) -> list[StoredStrategyEvaluation]:
        rows = self._database.fetchall(
            f"""
            SELECT {_COLUMNS} FROM strategy_evaluations
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, strategy_id
                ORDER BY as_of DESC, created_at DESC
            ) = 1
            ORDER BY as_of DESC, symbol, strategy_id LIMIT ?
            """,
            [limit],
        )
        return [_from_row(row) for row in rows]

    def history(
        self,
        symbol: str,
        *,
        strategy_id: str | None = None,
        limit: int = 200,
    ) -> list[StoredStrategyEvaluation]:
        where = "symbol=?"
        parameters: list[object] = [symbol.upper()]
        if strategy_id is not None:
            where += " AND strategy_id=?"
            parameters.append(strategy_id)
        parameters.append(limit)
        rows = self._database.fetchall(
            f"""
            SELECT {_COLUMNS} FROM strategy_evaluations WHERE {where}
            ORDER BY as_of DESC, strategy_id LIMIT ?
            """,
            parameters,
        )
        return [_from_row(row) for row in rows]

    def count(self) -> int:
        row = self._database.fetchone("SELECT COUNT(*) FROM strategy_evaluations")
        return 0 if row is None else int(row[0])


_COLUMNS = """
evaluation_id, cache_key, assessment_id, symbol, strategy_id, strategy_version,
research_status, parameters_json, parameters_hash, input_hash, provider_id,
as_of, data_through, state, stale, confirmation_blocked, features_json,
explanation_json, entry_json, stop_json, exit_json, reward_to_risk, created_at
"""


def _from_row(row: tuple[object, ...]) -> StoredStrategyEvaluation:
    (
        evaluation_id,
        cache_key,
        assessment_id,
        symbol,
        strategy_id,
        strategy_version,
        research_status,
        parameters_json,
        parameters_hash,
        input_hash,
        provider_id,
        as_of,
        _data_through,
        state,
        stale,
        confirmation_blocked,
        features_json,
        explanation_json,
        entry_json,
        stop_json,
        exit_json,
        reward_to_risk,
        created_at,
    ) = row
    signal = StrategySignal(
        symbol=symbol,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        state=SetupState(state),
        stale=stale,
        confirmation_blocked=confirmation_blocked,
        features=StrategyFeatures.model_validate_json(features_json),
        explanation=StrategyExplanation.model_validate_json(explanation_json),
        entry=(
            None if entry_json is None else EntryIntent.model_validate_json(entry_json)
        ),
        stop=None if stop_json is None else StopIntent.model_validate_json(stop_json),
        exit=None if exit_json is None else ExitIntent.model_validate_json(exit_json),
        reward_to_risk=reward_to_risk,
    )
    return StoredStrategyEvaluation(
        evaluation_id=evaluation_id,
        cache_key=cache_key,
        assessment_id=assessment_id,
        parameters=json.loads(parameters_json),
        parameters_hash=parameters_hash,
        input_hash=input_hash,
        provider_id=provider_id,
        as_of=as_of,
        research_status=StrategyResearchStatus(research_status),
        signal=signal,
        created_at=created_at,
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _optional_json(value: BaseModel | None) -> str | None:
    return None if value is None else value.model_dump_json()
