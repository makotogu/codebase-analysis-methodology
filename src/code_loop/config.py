from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class Price:
    input_cache_hit: float
    input_cache_miss: float
    output: float


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = "https://api.deepseek.com"
    flash_model: str = "deepseek-v4-flash"
    pro_model: str = "deepseek-v4-pro"
    max_session_cost_usd: float = 0.25
    request_timeout_seconds: float = 90.0
    max_retries: int = 1
    max_agent_steps: int = 20
    flash_max_steps: int = 8
    stagnation_threshold: int = 3
    flash_price: Price = Price(0.0028, 0.14, 0.28)
    pro_price: Price = Price(0.003625, 0.435, 0.87)


@dataclass(frozen=True)
class RepositoryConfig:
    output_dir: str = "notes/code-loop"
    max_file_bytes: int = 200_000
    max_read_lines: int = 800
    max_evidence_context_chars: int = 16_000
    max_search_results: int = 60
    exclude: tuple[str, ...] = (
        ".git", ".env", ".env.", "node_modules", "vendor", "dist", "build",
        "target", "*.pem", "*.key",
    )


@dataclass(frozen=True)
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    repository: RepositoryConfig = field(default_factory=RepositoryConfig)


def _price(raw: dict, fallback: Price) -> Price:
    return Price(
        float(raw.get("input_cache_hit", fallback.input_cache_hit)),
        float(raw.get("input_cache_miss", fallback.input_cache_miss)),
        float(raw.get("output", fallback.output)),
    )


def load_config(repository: Path) -> Config:
    path = repository / "code-loop.toml"
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    model_raw = raw.get("model", {})
    prices = model_raw.get("prices", {})
    default_model = ModelConfig()
    model = ModelConfig(
        base_url=model_raw.get("base_url", default_model.base_url),
        flash_model=model_raw.get("flash_model", default_model.flash_model),
        pro_model=model_raw.get("pro_model", default_model.pro_model),
        max_session_cost_usd=float(model_raw.get("max_session_cost_usd", default_model.max_session_cost_usd)),
        request_timeout_seconds=float(model_raw.get("request_timeout_seconds", default_model.request_timeout_seconds)),
        max_retries=int(model_raw.get("max_retries", default_model.max_retries)),
        max_agent_steps=max(1, int(model_raw.get("max_agent_steps", default_model.max_agent_steps))),
        flash_max_steps=max(1, int(model_raw.get("flash_max_steps", default_model.flash_max_steps))),
        stagnation_threshold=max(1, int(model_raw.get("stagnation_threshold", default_model.stagnation_threshold))),
        flash_price=_price(prices.get("flash", {}), default_model.flash_price),
        pro_price=_price(prices.get("pro", {}), default_model.pro_price),
    )
    repo_raw = raw.get("repository", {})
    default_repo = RepositoryConfig()
    repo = RepositoryConfig(
        output_dir=repo_raw.get("output_dir", default_repo.output_dir),
        max_file_bytes=int(repo_raw.get("max_file_bytes", default_repo.max_file_bytes)),
        max_read_lines=max(1, int(repo_raw.get("max_read_lines", default_repo.max_read_lines))),
        max_evidence_context_chars=max(1, int(repo_raw.get("max_evidence_context_chars", default_repo.max_evidence_context_chars))),
        max_search_results=int(repo_raw.get("max_search_results", default_repo.max_search_results)),
        exclude=tuple(repo_raw.get("exclude", default_repo.exclude)),
    )
    return Config(model=model, repository=repo)
