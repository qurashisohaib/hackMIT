"""Application settings (env-driven). Import `settings` everywhere."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / ".env")


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    company_name: str = os.getenv("CFO_COMPANY_NAME", "Lumen Robotics Inc.")
    db_path: str = os.getenv("CFO_DB_PATH", str(DATA_DIR / "cfo_office.db"))
    seed: int = int(os.getenv("CFO_SEED", "42"))

    # LLM (optional). When openai_api_key is None the HeuristicBrain is used.
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    openai_embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    llm_enabled: bool = _env_bool("CFO_LLM_ENABLED", True)

    # Optional Neo4j backend for the memory graph (embedded store is the default).
    neo4j_uri: str | None = os.getenv("NEO4J_URI") or None
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "neo4j")

    # Decision policy
    high_threshold: float = float(os.getenv("CFO_HIGH_THRESHOLD", "0.85"))
    medium_threshold: float = float(os.getenv("CFO_MEDIUM_THRESHOLD", "0.60"))
    audit_sample_rate: float = float(os.getenv("CFO_AUDIT_SAMPLE_RATE", "0.2"))
    approval_limit_usd: float = float(os.getenv("CFO_APPROVAL_LIMIT", "10000"))
    no_po_threshold_usd: float = float(os.getenv("CFO_NO_PO_THRESHOLD", "5000"))
    price_tolerance_pct: float = float(os.getenv("CFO_PRICE_TOLERANCE", "0.05"))

    # Demo pacing: sleep between exception investigations so viewers can follow along.
    step_delay_ms: int = int(os.getenv("CFO_STEP_DELAY_MS", "120"))

    api_port: int = int(os.getenv("CFO_API_PORT", "8000"))
    cors_origins: list[str] = ["*"]

    @property
    def llm_available(self) -> bool:
        return bool(self.openai_api_key) and self.llm_enabled


settings = Settings()
