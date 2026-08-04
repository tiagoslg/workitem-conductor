"""Snapshot models for ``conductor plans sync``'s opencode.db correlation.

A snapshot is a normalized, point-in-time copy of the OpenCode sessions that
executed a plan — written to disk under ``data_home()`` so it survives
independently of ``opencode.db``'s own schema/retention. Never read live by
anything except ``sync`` itself; everything else reads the snapshot file.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

MatchedVia = Literal["plan_id_marker", "none"]


class SessionSnapshot(BaseModel):
    id: str
    agent: str | None = None
    model: str | None = None
    parent_id: str | None = None
    directory: str | None = None
    cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    time_created: int | None = None
    time_updated: int | None = None


class ExecutionTotals(BaseModel):
    session_count: int = 0
    cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0

    @classmethod
    def from_sessions(cls, sessions: list[SessionSnapshot]) -> ExecutionTotals:
        return cls(
            session_count=len(sessions),
            cost=sum(s.cost for s in sessions),
            tokens_input=sum(s.tokens_input for s in sessions),
            tokens_output=sum(s.tokens_output for s in sessions),
            tokens_reasoning=sum(s.tokens_reasoning for s in sessions),
            tokens_cache_read=sum(s.tokens_cache_read for s in sessions),
            tokens_cache_write=sum(s.tokens_cache_write for s in sessions),
        )


class PlanExecutionSnapshot(BaseModel):
    plan_id: str
    synced_at: str
    opencode_db_path: str
    matched_via: MatchedVia
    sessions: list[SessionSnapshot] = []
    totals: ExecutionTotals = ExecutionTotals()
