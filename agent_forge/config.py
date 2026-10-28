"""Configuration models for Agent Forge."""

import os
from pathlib import Path
from pydantic import BaseModel, Field


def _load_dotenv_if_present() -> None:
    """Load key=value pairs from .env in cwd or project root if not already in os.environ."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if candidate.is_file():
            for raw_line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = val


class ForgeConfig(BaseModel):
    """Safety, budget, retry, and LLM configuration for Agent Forge."""

    def __init__(self, **data):
        _load_dotenv_if_present()
        super().__init__(**data)

    max_attempts: int = 5
    max_files_changed: int = 5
    max_diff_lines: int = 200
    max_total_tokens: int = 100_000
    max_cost_usd: float = 0.50
    test_timeout_seconds: int = 60
    consecutive_error_reflect_threshold: int = 2

    llm_base_url: str = Field(
        default_factory=lambda: os.environ.get("FORGE_LLM_BASE_URL", "https://api.deepseek.com")
    )
    llm_model: str = Field(
        default_factory=lambda: os.environ.get("FORGE_LLM_MODEL", "deepseek-chat")
    )
    llm_api_key: str = Field(
        default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    )
    prompt_price_per_1m: float = 0.27
    completion_price_per_1m: float = 1.10
