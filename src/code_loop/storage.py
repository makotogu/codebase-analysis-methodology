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
    ClaimDisposition,
    Correction,
    Evidence,
    FinalCard,
    InvestigationAgenda,
    InvestigationTopic,
    MergeSuggestion,
    ReviewStatus,
    Session,
    StoredAnalysis,
    Synthesis,
    SynthesisCoverage,
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
        self.journeys_directory = self.directory / "journeys"
        self.synthesis_path = self.directory / "synthesis.json"
        self.syntheses_directory = self.directory / "syntheses"
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

    def save_analysis(
        self,
        draft: AnalysisDraft,
        evidence: list[Evidence],
        *,
        apply_corrections: bool = True,
        new_round: bool = False,
    ) -> StoredAnalysis:
        if apply_corrections:
            session = self.load_session()
            by_claim = {item.claim_id: item for item in self.corrections_for_revision(session.current_revision_id)}
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
        self.merge_agenda(draft)
        stored = StoredAnalysis(draft=draft, evidence=evidence)
        self._write_json(self.analysis_path, stored.model_dump(mode="json"))
        if new_round:
            self.mark_synthesis_stale()
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
        if kind == "model_analysis":
            from .renderer import render_journey_mermaid

            self.journeys_directory.mkdir(parents=True, exist_ok=True)
            (self.journeys_directory / f"{revision_id}.mmd").write_text(
                render_journey_mermaid(revision.analysis),
                encoding="utf-8",
            )
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

    def _correction_revision_id(self, correction: Correction, revisions: list[AnalysisRevision]) -> str | None:
        if correction.revision_id:
            return correction.revision_id
        candidates = [item for item in revisions if item.kind == "model_analysis" and item.created_at <= correction.timestamp]
        return candidates[-1].id if candidates else None

    def corrections_for_revision(self, revision_id: str | None) -> list[Correction]:
        revisions = self.list_revisions()
        return [
            item for item in self.load_corrections()
            if self._correction_revision_id(item, revisions) == revision_id
        ]

    def reviewed_revisions(self) -> list[AnalysisRevision]:
        revisions = [item.model_copy(deep=True) for item in self.list_revisions()]
        by_id = {item.id: item for item in revisions}
        for correction in self.load_corrections():
            revision_id = self._correction_revision_id(correction, revisions)
            revision = by_id.get(revision_id or "")
            if revision is None:
                continue
            claim = next((item for item in revision.analysis.draft.claims if item.id == correction.claim_id), None)
            if claim is None or claim.review_status == ReviewStatus.STALE:
                continue
            claim.review_status = {
                "confirm": ReviewStatus.CONFIRMED,
                "reject": ReviewStatus.REJECTED,
                "amend": ReviewStatus.AMENDED,
            }[correction.verdict]
            if correction.verdict == "amend":
                claim.human_text = correction.amended_statement
        return revisions

    def confirmed_claim_history(self) -> list[dict]:
        history: list[dict] = []
        for revision in self.reviewed_revisions():
            if revision.kind != "model_analysis":
                continue
            for claim in revision.analysis.draft.claims:
                if claim.review_status not in {ReviewStatus.CONFIRMED, ReviewStatus.AMENDED}:
                    continue
                history.append({
                    "revision_id": revision.id,
                    "claim_id": claim.id,
                    "statement": claim.human_text or claim.statement,
                    "evidence_level": claim.evidence_level,
                    "evidence_ids": claim.evidence_ids,
                })
        return history

    def claim_ledger(self) -> list[dict]:
        ledger: list[dict] = []
        for revision in self.reviewed_revisions():
            if revision.kind != "model_analysis":
                continue
            for claim in revision.analysis.draft.claims:
                ledger.append({
                    "source_claim_id": f"{revision.id}::{claim.id}",
                    "revision_id": revision.id,
                    "claim_id": claim.id,
                    "statement": claim.human_text or claim.statement,
                    "category": claim.category,
                    "review_status": claim.review_status.value,
                    "evidence_level": claim.evidence_level.value,
                    "evidence_ids": list(dict.fromkeys(claim.evidence_ids)),
                })
        return ledger

    def ensure_model_revision(self) -> None:
        """Give pre-revision sessions a stable namespace before synthesis review."""
        if any(item.kind == "model_analysis" for item in self.list_revisions()):
            return
        if self.load_analysis_or_none() is not None:
            self.create_revision(kind="model_analysis", user_message=self.load_session().question)

    def load_synthesis_or_none(self) -> Synthesis | None:
        if not self.synthesis_path.exists():
            return None
        synthesis = Synthesis.model_validate_json(self.synthesis_path.read_text(encoding="utf-8"))
        dispositions = {item.source_claim_id: item.card_id for item in synthesis.dispositions}
        for suggestion in synthesis.suggestions:
            if suggestion.candidate_card_ids:
                continue
            suggestion.candidate_card_ids = list(dict.fromkeys(
                dispositions[item]
                for item in suggestion.source_claim_ids
                if dispositions.get(item)
            ))
            if len(suggestion.candidate_card_ids) < 2 and suggestion.status == "proposed":
                suggestion.status = "invalidated"
                suggestion.invalid_reason = "旧建议无法映射到两张有效卡片"
        return synthesis

    def save_synthesis(self, synthesis: Synthesis) -> None:
        synthesis.updated_at = utc_now()
        synthesis.coverage = self._synthesis_coverage(synthesis)
        self._write_json(self.synthesis_path, synthesis.model_dump(mode="json"))
        self.render_artifacts()

    def build_synthesis(self, *, force: bool = False) -> Synthesis:
        self.ensure_model_revision()
        ledger = self.claim_ledger()
        confirmed = [item for item in ledger if item["review_status"] in {"confirmed", "amended"}]
        source_revision_ids = list(dict.fromkeys(item["revision_id"] for item in confirmed))
        existing = self.load_synthesis_or_none()
        confirmed_ids = {item["source_claim_id"] for item in confirmed}
        existing_ids = {item.source_claim_id for item in existing.dispositions} if existing else set()
        if (
            existing
            and not force
            and existing.status == "draft"
            and existing.source_revision_ids == source_revision_ids
            and existing_ids == confirmed_ids
        ):
            return existing
        cards: list[FinalCard] = []
        dispositions: list[ClaimDisposition] = []
        for number, item in enumerate(confirmed, start=1):
            card_id = f"card_{number:04d}"
            statement = item["statement"].strip()
            title = statement.splitlines()[0][:80] or item["source_claim_id"]
            cards.append(FinalCard(
                id=card_id,
                title=title,
                statement=statement,
                source_claim_ids=[item["source_claim_id"]],
                evidence_ids=item["evidence_ids"],
            ))
            dispositions.append(ClaimDisposition(source_claim_id=item["source_claim_id"], card_id=card_id))
        synthesis = Synthesis(
            source_revision_ids=source_revision_ids,
            cards=cards,
            dispositions=dispositions,
        )
        self.save_synthesis(synthesis)
        self.event({"type": "synthesis_created", "confirmed": len(confirmed), "cards": len(cards)})
        return synthesis

    def mark_synthesis_stale(self) -> None:
        synthesis = self.load_synthesis_or_none()
        if synthesis is None or synthesis.status == "stale":
            return
        synthesis.status = "stale"
        synthesis.updated_at = utc_now()
        self._write_json(self.synthesis_path, synthesis.model_dump(mode="json"))
        self.event({"type": "synthesis_stale"})

    def accept_merge(self, suggestion_id: str) -> Synthesis:
        synthesis = self._require_draft_synthesis()
        suggestion = next((item for item in synthesis.suggestions if item.id == suggestion_id), None)
        if suggestion is None or suggestion.status != "proposed":
            raise ValueError("Merge suggestion is not available")
        if suggestion.relation == "conflict":
            raise ValueError("冲突评估不能直接接受为合并")
        ledger = {item["source_claim_id"]: item for item in self.claim_ledger()}
        active_cards = {item.id: item for item in synthesis.cards if item.status == "active"}
        candidate_ids = list(dict.fromkeys(suggestion.candidate_card_ids))
        if len(candidate_ids) < 2 or any(item not in active_cards for item in candidate_ids):
            raise ValueError("Merge suggestion contains an inactive or unknown Card")
        cards = [active_cards[item] for item in candidate_ids]
        from .synthesis import card_fingerprint

        if suggestion.input_fingerprint and suggestion.input_fingerprint != card_fingerprint(cards):
            suggestion.status = "invalidated"
            suggestion.invalid_reason = "卡片内容已变化，请重新评估"
            self.save_synthesis(synthesis)
            raise ValueError(suggestion.invalid_reason)
        source_ids = list(dict.fromkeys(
            source_id for card in cards for source_id in card.source_claim_ids
        ))
        if len(source_ids) < 2 or any(
            item not in ledger or ledger[item]["review_status"] not in {"confirmed", "amended"}
            for item in source_ids
        ):
            raise ValueError("Merge suggestion contains an invalid source Claim")
        if not suggestion.suggested_title.strip() or not suggestion.suggested_statement.strip():
            raise ValueError("Merge suggestion title and statement are required")
        dispositions = {item.source_claim_id: item for item in synthesis.dispositions}
        if any(item not in dispositions or dispositions[item].action != "mapped" for item in source_ids):
            raise ValueError("Merge suggestion overlaps an unavailable Claim")
        next_number = max(
            (int(item.id.removeprefix("card_")) for item in synthesis.cards if item.id.removeprefix("card_").isdigit()),
            default=0,
        ) + 1
        card_id = f"card_{next_number:04d}"
        evidence_ids = list(dict.fromkeys(
            evidence_id for source_id in source_ids for evidence_id in ledger[source_id]["evidence_ids"]
        ))
        synthesis.cards.append(FinalCard(
            id=card_id,
            title=suggestion.suggested_title,
            statement=suggestion.suggested_statement,
            source_claim_ids=source_ids,
            evidence_ids=evidence_ids,
            scope="\n".join(dict.fromkeys(item.scope for item in cards if item.scope.strip())),
            limitations=list(dict.fromkeys(
                limitation for item in cards for limitation in item.limitations
            )),
            verification_tasks=list(dict.fromkeys(
                task for item in cards for task in item.verification_tasks
            )),
        ))
        for card in synthesis.cards:
            if card.id in candidate_ids:
                card.status = "superseded"
                card.superseded_by = card_id
        for source_id in source_ids:
            dispositions[source_id].card_id = card_id
        suggestion.status = "accepted"
        suggestion.result_card_id = card_id
        candidate_set = set(candidate_ids)
        for item in synthesis.suggestions:
            if item.status == "proposed" and item.id != suggestion_id and candidate_set.intersection(item.candidate_card_ids):
                item.status = "invalidated"
                item.invalid_reason = f"与已接受建议 {suggestion_id} 重叠"
        self.save_synthesis(synthesis)
        self.event({
            "type": "synthesis_merge_accepted",
            "suggestion_id": suggestion_id,
            "candidate_card_ids": candidate_ids,
            "relation": suggestion.relation,
            "card_id": card_id,
        })
        return synthesis

    def reject_merge(self, suggestion_id: str) -> Synthesis:
        synthesis = self._require_draft_synthesis()
        suggestion = next((item for item in synthesis.suggestions if item.id == suggestion_id), None)
        if suggestion is None or suggestion.status != "proposed":
            raise ValueError("Merge suggestion is not available")
        suggestion.status = "rejected"
        self.save_synthesis(synthesis)
        self.event({
            "type": "synthesis_merge_rejected",
            "suggestion_id": suggestion_id,
            "candidate_card_ids": suggestion.candidate_card_ids,
            "relation": suggestion.relation,
        })
        return synthesis

    def reject_all_merges(self) -> Synthesis:
        synthesis = self._require_draft_synthesis()
        for suggestion in synthesis.suggestions:
            if suggestion.status == "proposed":
                suggestion.status = "rejected"
        self.save_synthesis(synthesis)
        return synthesis

    def edit_card(self, card_id: str, *, title: str, statement: str) -> Synthesis:
        synthesis = self._require_draft_synthesis()
        card = next((item for item in synthesis.cards if item.id == card_id and item.status == "active"), None)
        if card is None or not title.strip() or not statement.strip():
            raise ValueError("Active card, title, and statement are required")
        card.title = title.strip()[:160]
        card.statement = statement.strip()[:4_000]
        card.human_edited = True
        self._invalidate_card_suggestions(synthesis, {card_id}, "卡片内容已编辑")
        self.save_synthesis(synthesis)
        return synthesis

    def set_claim_disposition(self, source_claim_id: str, action: str, reason: str) -> Synthesis:
        if action not in {"deferred", "disputed"} or not reason.strip():
            raise ValueError("Disposition and reason are required")
        synthesis = self._require_draft_synthesis()
        disposition = next((item for item in synthesis.dispositions if item.source_claim_id == source_claim_id), None)
        if disposition is None:
            raise ValueError("Unknown source Claim")
        card = next((item for item in synthesis.cards if item.id == disposition.card_id), None)
        if card is not None and card.status == "active":
            self._invalidate_card_suggestions(synthesis, {card.id}, f"来源 Claim 已标记 {action}")
            card.source_claim_ids = [item for item in card.source_claim_ids if item != source_claim_id]
            ledger = {item["source_claim_id"]: item for item in self.claim_ledger()}
            card.evidence_ids = list(dict.fromkeys(
                evidence_id
                for claim_id in card.source_claim_ids
                for evidence_id in ledger.get(claim_id, {}).get("evidence_ids", [])
            ))
            if not card.source_claim_ids:
                card.status = "superseded"
        disposition.action = action
        disposition.card_id = None
        disposition.reason = reason.strip()[:1_000]
        self.save_synthesis(synthesis)
        return synthesis

    def seal_synthesis(self) -> Synthesis:
        synthesis = self._require_draft_synthesis()
        synthesis.coverage = self._synthesis_coverage(synthesis)
        if synthesis.coverage.missing:
            raise ValueError("Coverage 尚未处理完成")
        ignored = 0
        for suggestion in synthesis.suggestions:
            if suggestion.status == "proposed":
                suggestion.status = "ignored"
                ignored += 1
        self._validate_synthesis(synthesis)
        self.syntheses_directory.mkdir(parents=True, exist_ok=True)
        number = max(
            (int(path.stem.removeprefix("syn_")) for path in self.syntheses_directory.glob("syn_*.json") if path.stem.removeprefix("syn_").isdigit()),
            default=0,
        ) + 1
        synthesis.id = f"syn_{number:04d}"
        synthesis.status = "sealed"
        synthesis.sealed_at = utc_now()
        synthesis.updated_at = synthesis.sealed_at
        self._write_json(self.syntheses_directory / f"{synthesis.id}.json", synthesis.model_dump(mode="json"))
        self._write_json(self.synthesis_path, synthesis.model_dump(mode="json"))
        session = self.load_session()
        session.status = "completed"
        self.save_session(session)
        if self.analysis_path.exists():
            self.create_revision(kind="completion")
        self.mark_parent_topic_completed(session)
        self.event({
            "type": "synthesis_sealed",
            "synthesis_id": synthesis.id,
            "cards": synthesis.coverage.mapped,
            "ignored_assessments": ignored,
        })
        self.render_artifacts()
        return synthesis

    @staticmethod
    def _invalidate_card_suggestions(synthesis: Synthesis, card_ids: set[str], reason: str) -> None:
        for suggestion in synthesis.suggestions:
            if suggestion.status == "proposed" and card_ids.intersection(suggestion.candidate_card_ids):
                suggestion.status = "invalidated"
                suggestion.invalid_reason = reason

    def _validate_synthesis(self, synthesis: Synthesis) -> None:
        ledger = {
            item["source_claim_id"]: item
            for item in self.claim_ledger()
            if item["review_status"] in {"confirmed", "amended"}
        }
        active_cards = {item.id: item for item in synthesis.cards if item.status == "active"}
        known_evidence_ids = {
            evidence.id
            for revision in self.reviewed_revisions()
            if revision.kind == "model_analysis"
            for evidence in revision.analysis.evidence
        }
        seen: set[str] = set()
        for disposition in synthesis.dispositions:
            if disposition.source_claim_id not in ledger:
                raise ValueError(f"未知或已失效的来源 Claim：{disposition.source_claim_id}")
            if disposition.action != "mapped":
                if not disposition.reason.strip():
                    raise ValueError(f"{disposition.action} 必须填写人工理由")
                continue
            card = active_cards.get(disposition.card_id or "")
            if card is None or disposition.source_claim_id not in card.source_claim_ids:
                raise ValueError(f"Claim 未映射到有效卡片：{disposition.source_claim_id}")
            if disposition.source_claim_id in seen:
                raise ValueError(f"Claim 被重复映射：{disposition.source_claim_id}")
            seen.add(disposition.source_claim_id)
        for card in active_cards.values():
            if not card.source_claim_ids:
                raise ValueError(f"卡片没有来源 Claim：{card.id}")
            expected = list(dict.fromkeys(
                evidence_id
                for source_id in card.source_claim_ids
                for evidence_id in ledger.get(source_id, {}).get("evidence_ids", [])
            ))
            unknown_evidence = set(expected) - known_evidence_ids
            if unknown_evidence:
                raise ValueError(f"卡片引用未知 Evidence：{card.id} · {', '.join(sorted(unknown_evidence))}")
            if card.evidence_ids != expected:
                raise ValueError(f"卡片 Evidence 与来源 Claim 不一致：{card.id}")

    def _require_draft_synthesis(self) -> Synthesis:
        synthesis = self.load_synthesis_or_none()
        if synthesis is None or synthesis.status != "draft":
            raise ValueError("A draft synthesis is required")
        return synthesis

    @staticmethod
    def _synthesis_coverage(synthesis: Synthesis) -> SynthesisCoverage:
        confirmed = len(synthesis.dispositions)
        mapped = sum(1 for item in synthesis.dispositions if item.action == "mapped" and item.card_id)
        deferred = sum(1 for item in synthesis.dispositions if item.action == "deferred")
        disputed = sum(1 for item in synthesis.dispositions if item.action == "disputed")
        covered_ids = {
            source_id
            for card in synthesis.cards
            if card.status == "active"
            for source_id in card.source_claim_ids
        }
        missing = sum(
            1 for item in synthesis.dispositions
            if item.action == "mapped" and (not item.card_id or item.source_claim_id not in covered_ids)
        )
        return SynthesisCoverage(
            confirmed=confirmed,
            mapped=mapped,
            deferred=deferred,
            disputed=disputed,
            missing=missing,
        )

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
        synthesis = self.load_synthesis_or_none()
        agenda = self.load_agenda(session)
        revisions = self.reviewed_revisions()
        self.journeys_directory.mkdir(parents=True, exist_ok=True)
        for revision in revisions:
            if revision.kind != "model_analysis":
                continue
            (self.journeys_directory / f"{revision.id}.mmd").write_text(
                render_journey_mermaid(revision.analysis),
                encoding="utf-8",
            )
        related = {item.id: item for item in self.list_sessions(self.repository, self.config) if item.parent_session_id == session.id}
        self.report_path.write_text(
            render_report(
                session,
                analysis,
                agenda=agenda,
                related_sessions=related,
                synthesis=synthesis,
                revisions=revisions,
            ),
            encoding="utf-8",
        )
        self.journey_path.write_text(render_journey_mermaid(analysis), encoding="utf-8")
        self._ensure_mermaid_asset()
        self.html_report_path.write_text(
            render_html_report(
                session,
                analysis,
                agenda=agenda,
                related_sessions=related,
                revisions=revisions,
                synthesis=synthesis,
            ),
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
