"""All settings come from environment variables (or a local .env file).

Nothing secret is hard-coded: JWT_SECRET and LLM_API_KEY must come from the environment.
"""
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Max cosine distance for a chunk to count as "relevant" (0 = identical, 1 = unrelated).
# Each embedding model has a different distance scale, so each gets its own default.
# Tune with:  python -m scripts.evaluate --show-distances
DEFAULT_MAX_DISTANCE = {"minilm": 0.75, "hash": 0.85}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- auth ---
    jwt_secret: str = Field(min_length=32)  # app refuses to start without a long secret
    jwt_algorithm: str = "HS256"
    token_ttl_minutes: int = 60

    # --- storage ---
    data_dir: str = "storage"  # runtime data (SQLite + Chroma). Not committed.
    policies_dir: str = "data/policies"  # source policy files. Committed.
    auto_seed: bool = False  # ingest demo policies at startup (idempotent)

    # --- models ---
    embedding_provider: Literal["minilm", "hash"] = "minilm"
    llm_provider: Literal["groq", "gemini", "extractive"] = "extractive"
    llm_api_key: str = ""
    llm_model: str = ""  # empty = provider default
    llm_base_url: str = ""  # empty = provider default
    llm_timeout_seconds: float = 3.0
    retrieval_timeout_seconds: float = 0.8

    # --- chunking / retrieval ---
    chunk_mode: Literal["section", "fixed"] = "section"
    chunk_size_chars: int = 500  # used by "fixed" mode
    chunk_overlap_chars: int = 100
    max_section_chars: int = 1200  # "section" mode only splits sections longer than this
    candidate_pool: int = 5
    max_evidence: int = 3  # hard cap on chunks sent to the LLM and cited
    max_distance: Optional[float] = None  # None = use DEFAULT_MAX_DISTANCE

    @property
    def sqlite_path(self) -> str:
        return f"{self.data_dir}/metadata.db"

    @property
    def chroma_path(self) -> str:
        return f"{self.data_dir}/chroma"

    @property
    def distance_limit(self) -> float:
        if self.max_distance is not None:
            return self.max_distance
        return DEFAULT_MAX_DISTANCE[self.embedding_provider]
