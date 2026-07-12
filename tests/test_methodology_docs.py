from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CORE_DOCS = [ROOT / "README.md", ROOT / "00-方法论边界与阶段门.md"] + sorted(ROOT.glob("0[1-9]-*.md"))
CORE_DOCS.append(ROOT / "10-Code-Loop与Phase0契约差距.md")


def _markdown_links(text: str) -> list[str]:
    return re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text)


def _fenced_blocks(text: str, language: str) -> list[str]:
    pattern = rf"```{language}\s*\n(.*?)```"
    return re.findall(pattern, text, flags=re.DOTALL | re.IGNORECASE)


def test_phase_names_and_code_loop_navigation_are_consistent() -> None:
    required = [
        "Phase 0", "Phase 1", "Phase 2", "Phase 3", "Phase 4", "Phase 5",
        "局部调查", "普查与覆盖", "验证", "评估", "决策", "执行与治理",
    ]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    control = (ROOT / "00-方法论边界与阶段门.md").read_text(encoding="utf-8")
    for term in required:
        assert term in readme
        assert term in control
    assert "六个任务视图" in readme
    assert "Overview、Graph、Claims、Evidence、Activity、Revisions" in readme
    assert "`1–6`" in readme
    assert "Phase 0" in readme and "局部调查工具" in readme


def test_all_internal_markdown_links_exist() -> None:
    for document in [*CORE_DOCS, ROOT / "templates" / "README.md"]:
        for raw_target in _markdown_links(document.read_text(encoding="utf-8")):
            target = raw_target.split("#", 1)[0].strip().replace("%20", " ")
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            assert (document.parent / target).resolve().exists(), f"broken link in {document.name}: {raw_target}"


def test_template_index_targets_exist_and_new_contracts_have_provenance() -> None:
    index = ROOT / "templates" / "README.md"
    for raw_target in _markdown_links(index.read_text(encoding="utf-8")):
        target = raw_target.split("#", 1)[0]
        if target.startswith("../"):
            continue
        assert (index.parent / target).exists(), target

    required_templates = {
        "artifact-metadata.yaml": ["generated_by", "commit", "scan_scope", "time_window", "owner", "stale_when"],
        "coverage-ledger.yaml": ["declared_denominator", "discovery_methods", "differences", "unknown_sources", "coverage_rate"],
        "demand-signals.yaml": ["source_reference", "time_window", "owner", "status", "review_date"],
        "analysis-policy.yaml": ["classification", "remote_model", "excluded_paths", "redaction", "retention", "audit"],
    }
    for name, fields in required_templates.items():
        text = (ROOT / "templates" / name).read_text(encoding="utf-8")
        payload = yaml.safe_load(text)
        assert isinstance(payload, dict)
        for field in fields:
            assert field in text, f"{name} missing {field}"

    assert (ROOT / "templates" / "asset-inventory.csv").exists()
    assert (ROOT / "templates" / "asset-inventory.metadata.yaml").exists()


def test_yaml_templates_and_markdown_yaml_examples_parse() -> None:
    for path in sorted((ROOT / "templates").glob("*.yaml")):
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None, path.name
    for document in CORE_DOCS:
        for block in _fenced_blocks(document.read_text(encoding="utf-8"), "yaml"):
            assert yaml.safe_load(block) is not None, document.name


def test_mermaid_fences_are_closed_and_have_a_diagram_directive() -> None:
    for document in CORE_DOCS:
        text = document.read_text(encoding="utf-8")
        assert text.count("```mermaid") == len(_fenced_blocks(text, "mermaid")), document.name
        for block in _fenced_blocks(text, "mermaid"):
            first = block.strip().splitlines()[0]
            assert first.startswith(("flowchart", "graph", "sequenceDiagram", "stateDiagram", "classDiagram"))


def test_bare_verified_only_appears_in_compatibility_explanations() -> None:
    allowed_context = ("兼容", "历史", "旧", "当前", "Code Loop", "映射", "解释")
    bare_verified = re.compile(r"(?<![-\w])verified(?![-\w])")
    for document in [*CORE_DOCS, *sorted((ROOT / "templates").glob("*"))]:
        if not document.is_file() or document.suffix not in {".md", ".yaml", ".csv"}:
            continue
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            if bare_verified.search(line):
                assert any(term in line for term in allowed_context), f"{document}:{line_number}: {line}"


def test_every_methodology_chapter_states_scope_and_handoff_contract() -> None:
    required_concepts = ("输入", "产物", "不能", "进入", "维护")
    for document in sorted(ROOT.glob("0[1-9]-*.md")) + [ROOT / "10-Code-Loop与Phase0契约差距.md"]:
        text = document.read_text(encoding="utf-8")
        for concept in required_concepts:
            assert concept in text, f"{document.name} missing {concept}"
