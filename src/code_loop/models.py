from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvidenceLevel(StrEnum):
    VERIFIED = "verified"
    INFERRED = "inferred"
    UNVERIFIED = "unverified"


class ReviewStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    AMENDED = "amended"
    STALE = "stale"


class Evidence(BaseModel):
    id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    excerpt: str
    content_hash: str
    source: Literal["read_lines", "search_text", "git_log", "git_show"]


class JourneyNode(BaseModel):
    id: str
    label: str
    kind: str
    evidence_ids: list[str] = Field(default_factory=list)


class JourneyEdge(BaseModel):
    source_id: str
    target_id: str
    relation: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.INFERRED


class Claim(BaseModel):
    id: str
    statement: str
    category: Literal["code_fact", "business_meaning", "risk", "question"]
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_level: EvidenceLevel
    confidence: Literal["high", "medium", "low"]
    review_status: ReviewStatus = ReviewStatus.PROPOSED
    human_text: str | None = None


class OpenQuestion(BaseModel):
    id: str
    question: str
    blocking: bool = False
    suggested_verification: str


class SuggestedInvestigation(BaseModel):
    question: str
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class AnalysisDraft(BaseModel):
    status: Literal["draft", "needs_user", "ready_to_complete", "needs_deeper_reasoning"]
    summary: str
    journey_nodes: list[JourneyNode] = Field(default_factory=list)
    journey_edges: list[JourneyEdge] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    primary_focus: str = ""
    suggested_investigations: list[SuggestedInvestigation] = Field(default_factory=list)
    next_action: str


class Correction(BaseModel):
    claim_id: str
    verdict: Literal["confirm", "reject", "amend"]
    amended_statement: str | None = None
    note: str = ""
    actor: str = "local-user"
    timestamp: str = Field(default_factory=utc_now)


class Usage(BaseModel):
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


class Session(BaseModel):
    id: str
    repository: str
    question: str
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    git_head: str | None = None
    status: Literal["active", "completed", "paused_budget"] = "active"
    usage: Usage = Field(default_factory=Usage)
    model_events: list[str] = Field(default_factory=list)
    scope_items: list[str] = Field(default_factory=list)
    scope_mode: Literal["natural_language", "selected", "mixed"] = "natural_language"
    scope_expansions: list[str] = Field(default_factory=list)
    parent_session_id: str | None = None
    parent_revision_id: str | None = None
    source_topic_id: str | None = None
    seed_evidence_ids: list[str] = Field(default_factory=list)
    current_revision_id: str | None = None


class StoredAnalysis(BaseModel):
    draft: AnalysisDraft
    evidence: list[Evidence] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


class InvestigationTopic(BaseModel):
    id: str
    question: str
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["queued", "started", "completed", "dismissed"] = "queued"
    child_session_id: str | None = None


class InvestigationAgenda(BaseModel):
    primary_focus: str
    topics: list[InvestigationTopic] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


class AnalysisRevision(BaseModel):
    id: str
    parent_revision_id: str | None = None
    kind: Literal["model_analysis", "completion"]
    created_at: str = Field(default_factory=utc_now)
    user_message: str = ""
    analysis: StoredAnalysis
