from pathlib import Path

from code_loop.config import load_config


def test_agent_convergence_settings_load_from_toml(tmp_path: Path) -> None:
    (tmp_path / "code-loop.toml").write_text(
        """[model]
max_agent_steps = 12
flash_max_steps = 5
stagnation_threshold = 2
[repository]
max_read_lines = 900
max_evidence_context_chars = 20000
""",
        encoding="utf-8",
    )

    model = load_config(tmp_path).model

    assert model.max_agent_steps == 12
    assert model.flash_max_steps == 5
    assert model.stagnation_threshold == 2
    repository = load_config(tmp_path).repository
    assert repository.max_read_lines == 900
    assert repository.max_evidence_context_chars == 20000
