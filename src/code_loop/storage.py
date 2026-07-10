from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
import re
import shutil
import uuid

from .config import RepositoryConfig
from .models import (
    AnalysisDraft,
    AnalysisRevision,
    Correction,
    Evidence,
    InvestigationAgenda,
    InvestigationTopic,
    ReviewStatus,
    Session,
    StoredAnalysis,
    utc_now,
)


class SessionStore:
    def __init__(self, repository: Path, config: RepositoryConfig, session_id: str):
        self.repository = repository.resolve()
        self.config = config
        self.directory = self.repository / config.output_dir / session_id
        self.session_path = self.directory / "session.json"
        self.analysis_path = self.directory / "analysis.json"
        self.agenda_path = self.directory / "agenda.json"
        self.revisions_directory = self.directory / "revisions"
        self.corrections_path = self.directory / "corrections.jsonl"
        self.events_path = self.directory / "events.jsonl"
        self.report_path = self.directory / "report.md"
        self.journey_path = self.directory / "journey.mmd"
        self.html_report_path = self.directory / "report.html"

    @staticmethod
    def new_id(question: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")[:28] or "analysis"
        return f"{slug}-{uuid.uuid4().hex[:8]}"

    def create(
        self,
        question: str,
        git_head: str | None,
        *,
        scope_items: list[str] | None = None,
        scope_mode: str = "natural_language",
        parent_session_id: str | None = None,
        parent_revision_id: str | None = None,
        source_topic_id: str | None = None,
        seed_evidence_ids: list[str] | None = None,
    ) -> Session:
        self.directory.mkdir(parents=True, exist_ok=False)
        session = Session(
            id=self.directory.name,
            repository=str(self.repository),
            question=question,
            git_head=git_head,
            scope_items=scope_items or [],
            scope_mode=scope_mode,
            parent_session_id=parent_session_id,
            parent_revision_id=parent_revision_id,
            source_topic_id=source_topic_id,
            seed_evidence_ids=seed_evidence_ids or [],
        )
        self.save_session(session)
        return session

    def save_session(self, session: Session) -> None:
        session.updated_at = utc_now()
        self._write_json(self.session_path, session.model_dump(mode="json"))

    def load_session(self) -> Session:
        return Session.model_validate_json(self.session_path.read_text(encoding="utf-8"))

    def save_analysis(self, draft: AnalysisDraft, evidence: list[Evidence]) -> StoredAnalysis:
        previous = self.load_analysis_or_none()
        corrections = self.load_corrections()
        by_claim = {item.claim_id: item for item in corrections}
        for claim in draft.claims:
            correction = by_claim.get(claim.id)
            if correction is None or claim.review_status == ReviewStatus.STALE:
                continue
            claim.review_status = {
                "confirm": ReviewStatus.CONFIRMED,
                "reject": ReviewStatus.REJECTED,
                "amend": ReviewStatus.AMENDED,
            }[correction.verdict]
            if correction.verdict == "amend":
                claim.human_text = correction.amended_statement
        if previous:
            self._preserve_missing_human_claims(draft, previous, by_claim)
        self.merge_agenda(draft)
        stored = StoredAnalysis(draft=draft, evidence=evidence)
        self._write_json(self.analysis_path, stored.model_dump(mode="json"))
        self.render_artifacts()
        return stored

    def load_agenda(self, session: Session | None = None) -> InvestigationAgenda:
        if self.agenda_path.exists():
            return InvestigationAgenda.model_validate_json(self.agenda_path.read_text(encoding="utf-8"))
        session = session or self.load_session()
        return InvestigationAgenda(primary_focus=session.question)

    def save_agenda(self, agenda: InvestigationAgenda) -> None:
        agenda.updated_at = utc_now()
        self._write_json(self.agenda_path, agenda.model_dump(mode="json"))

    def merge_agenda(self, draft: AnalysisDraft) -> InvestigationAgenda:
        session = self.load_session()
        existed = self.agenda_path.exists()
        agenda = self.load_agenda(session)
        if not existed and draft.primary_focus.strip():
            agenda.primary_focus = draft.primary_focus.strip()
        draft.primary_focus = agenda.primary_focus
        known = {self._normalize_question(item.question) for item in agenda.topics}
        next_number = max((int(item.id.removeprefix("topic_")) for item in agenda.topics if item.id.removeprefix("topic_").isdigit()), default=0) + 1
        added: list[InvestigationTopic] = []
        for suggestion in draft.suggested_investigations:
            question = suggestion.question.strip()
            normalized = self._normalize_question(question)
            if not question or normalized in known or normalized == self._normalize_question(agenda.primary_focus):
                continue
            topic = InvestigationTopic(
                id=f"topic_{next_number}",
                question=question,
                rationale=suggestion.rationale.strip(),
                evidence_ids=list(dict.fromkeys(suggestion.evidence_ids)),
            )
            next_number += 1
            known.add(normalized)
            agenda.topics.append(topic)
            added.append(topic)
        self.save_agenda(agenda)
        for topic in added:
            self.event({"type": "investigation_queued", "topic_id": topic.id, "question": topic.question[:500]})
        return agenda

    @staticmethod
    def _normalize_question(question: str) -> str:
        return " ".join(question.casefold().split()).rstrip("？?。.!！")

    def create_revision(self, *, kind: str, user_message: str = "") -> AnalysisRevision:
        analysis = self.load_analysis_or_none()
        if analysis is None:
            raise ValueError("Cannot create a revision before analysis exists")
        session = self.load_session()
        self.revisions_directory.mkdir(parents=True, exist_ok=True)
        number = max(
            (int(path.stem.removeprefix("rev_")) for path in self.revisions_directory.glob("rev_*.json") if path.stem.removeprefix("rev_").isdigit()),
            default=0,
        ) + 1
        revision_id = f"rev_{number:04d}"
        revision = AnalysisRevision(
            id=revision_id,
            parent_revision_id=session.current_revision_id,
            kind=kind,
            user_message=user_message,
            analysis=analysis,
        )
        path = self.revisions_directory / f"{revision_id}.json"
        if path.exists():
            raise FileExistsError(path)
        self._write_json(path, revision.model_dump(mode="json"))
        session.current_revision_id = revision_id
        self.save_session(session)
        self.event({"type": "revision_created", "revision_id": revision_id, "kind": kind})
        self.render_artifacts()
        return revision

    def list_revisions(self) -> list[AnalysisRevision]:
        if not self.revisions_directory.exists():
            return []
        revisions: list[AnalysisRevision] = []
        for path in sorted(self.revisions_directory.glob("rev_*.json")):
            try:
                revisions.append(AnalysisRevision.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return revisions

    def load_revision(self, revision_id: str) -> AnalysisRevision:
        if not re.fullmatch(r"rev_\d{4,}", revision_id):
            raise ValueError("Invalid revision ID")
        return AnalysisRevision.model_validate_json((self.revisions_directory / f"{revision_id}.json").read_text(encoding="utf-8"))

    def create_child_session(
        self,
        question: str,
        git_head: str | None,
        *,
        evidence_ids: list[str] | None = None,
        source_topic_id: str | None = None,
        parent_revision_id: str | None = None,
    ) -> tuple["SessionStore", Session]:
        parent = self.load_session()
        child_id = self.new_id(question)
        child_store = SessionStore(self.repository, self.config, child_id)
        child = child_store.create(
            question,
            git_head,
            scope_items=parent.scope_items,
            scope_mode=parent.scope_mode,
            parent_session_id=parent.id,
            parent_revision_id=parent_revision_id or parent.current_revision_id,
            source_topic_id=source_topic_id,
            seed_evidence_ids=list(dict.fromkeys(evidence_ids or [])),
        )
        if source_topic_id:
            agenda = self.load_agenda(parent)
            topic = next((item for item in agenda.topics if item.id == source_topic_id), None)
            if topic is not None:
                topic.status = "started"
                topic.child_session_id = child.id
                self.save_agenda(agenda)
        self.event({"type": "child_session_created", "child_session_id": child.id, "source_topic_id": source_topic_id, "question": question[:500]})
        child_store.event({"type": "child_session_started", "parent_session_id": parent.id, "parent_revision_id": child.parent_revision_id})
        self.render_artifacts()
        return child_store, child

    def _repository_config(self) -> RepositoryConfig:
        """Recover the configured output policy without coupling storage to Config."""

        return self.config

    def mark_parent_topic_completed(self, session: Session) -> None:
        if not session.parent_session_id or not session.source_topic_id:
            return
        parent_store = SessionStore(self.repository, self._repository_config(), session.parent_session_id)
        try:
            parent = parent_store.load_session()
            agenda = parent_store.load_agenda(parent)
        except (OSError, ValueError):
            return
        topic = next((item for item in agenda.topics if item.id == session.source_topic_id), None)
        if topic is None:
            return
        topic.status = "completed"
        topic.child_session_id = session.id
        parent_store.save_agenda(agenda)
        parent_store.event({"type": "child_session_completed", "child_session_id": session.id, "topic_id": topic.id})
        parent_store.render_artifacts()

    def _preserve_missing_human_claims(self, draft: AnalysisDraft, previous: StoredAnalysis, corrections: dict[str, Correction]) -> None:
        existing = {claim.id for claim in draft.claims}
        for prior in previous.draft.claims:
            if prior.id not in existing and prior.id in corrections:
                draft.claims.append(prior)

    def load_analysis_or_none(self) -> StoredAnalysis | None:
        if not self.analysis_path.exists():
            return None
        return StoredAnalysis.model_validate_json(self.analysis_path.read_text(encoding="utf-8"))

    def append_correction(self, correction: Correction) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.corrections_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(correction.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def load_corrections(self) -> list[Correction]:
        if not self.corrections_path.exists():
            return []
        return [Correction.model_validate_json(line) for line in self.corrections_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def load_events(self) -> list[dict]:
        if not self.events_path.exists():
            return []
        events: list[dict] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def render_artifacts(self) -> None:
        from .renderer import render_html_report, render_journey_mermaid, render_report

        if not self.session_path.exists():
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        session = self.load_session()
        analysis = self.load_analysis_or_none()
        agenda = self.load_agenda(session)
        related = {item.id: item for item in self.list_sessions(self.repository, self.config) if item.parent_session_id == session.id}
        self.report_path.write_text(render_report(session, analysis, agenda=agenda, related_sessions=related), encoding="utf-8")
        self.journey_path.write_text(render_journey_mermaid(analysis), encoding="utf-8")
        self._ensure_mermaid_asset()
        self.html_report_path.write_text(
            render_html_report(session, analysis, agenda=agenda, related_sessions=related),
            encoding="utf-8",
        )

    def _ensure_mermaid_asset(self) -> None:
        asset_root = self.directory.parent / "_assets"
        asset_root.mkdir(parents=True, exist_ok=True)
        for name in ("mermaid.min.js", "MERMAID_LICENSE"):
            target = asset_root / name
            if target.exists():
                continue
            source = files("code_loop").joinpath("assets", name)
            with source.open("rb") as input_file, target.open("wb") as output_file:
                shutil.copyfileobj(input_file, output_file)

    @classmethod
    def list_sessions(cls, repository: Path, config: RepositoryConfig) -> list[Session]:
        root = repository.resolve() / config.output_dir
        if not root.exists():
            return []
        sessions: list[Session] = []
        for directory in root.iterdir():
            if not directory.is_dir() or directory.name.startswith("_"):
                continue
            try:
                sessions.append(cls(repository, config, directory.name).load_session())
            except (OSError, ValueError):
                continue
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def event(self, event: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        event = {"timestamp": utc_now(), **event}
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
