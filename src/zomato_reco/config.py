"""Central configuration. Every tunable lives here rather than inline at its use site."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    """Repo root, whether this file lives under src/ or in site-packages."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _project_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ZOMATO_",
        extra="ignore",
    )

    # --- Paths ---
    raw_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    processed_data_dir: Path = PROJECT_ROOT / "data" / "processed"
    artifact_name: str = "restaurants.parquet"
    facets_name: str = "facets.json"

    # --- Dataset source ---
    hf_dataset_id: str = "ManikaSaini/zomato-restaurant-recommendation"
    hf_split: str = "train"

    # --- LLM (Groq via the OpenAI-compatible SDK) ---
    # GROQ_API_KEY is the primary name. OPENAI_API_KEY is accepted so existing
    # OpenAI-compatible tooling still works when pointed at Groq's base URL.
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GROQ_API_KEY",
            "OPENAI_API_KEY",
            "ZOMATO_GROQ_API_KEY",
            "ZOMATO_OPENAI_API_KEY",
        ),
    )
    openai_base_url: str | None = Field(
        default="https://api.groq.com/openai/v1",
        validation_alias=AliasChoices(
            "OPENAI_BASE_URL",
            "GROQ_BASE_URL",
            "ZOMATO_OPENAI_BASE_URL",
        ),
    )
    llm_model: str = "openai/gpt-oss-120b"
    temperature: float = 0.3
    # Sized so five 1–2 sentence explanations fit after gpt-oss reasoning tokens.
    max_tokens: int = 2048
    llm_timeout_seconds: float = 30.0
    # gpt-oss hidden reasoning. "low" keeps the JSON reply from being truncated.
    reasoning_effort: str = "low"

    # Groq free-tier caps for openai/gpt-oss-120b. The client refuses the call
    # (deterministic fallback) rather than waiting or getting a 429.
    llm_rpm: int = Field(default=30, gt=0)
    llm_rpd: int = Field(default=1_000, gt=0)
    llm_tpm: int = Field(default=8_000, gt=0)
    llm_tpd: int = Field(default=200_000, gt=0)
    # Pre-flight output-token guess. max_tokens is the API ceiling; using it here
    # would reserve 2k+ per call and allow only one request per minute.
    llm_output_token_estimate: int = Field(default=1_500, gt=0)
    # Stay under the published caps so a slightly-short estimate cannot 429 us.
    llm_quota_margin: float = Field(default=0.9, gt=0.0, le=1.0)
    llm_cache: bool = True

    # Comma-separated browser origins allowed to call the API.
    # A lone "*" is dropped when a Groq key is set — see app/api.py.
    cors_origins: str = (
        "http://localhost:5173,http://localhost:3000,"
        "http://127.0.0.1:5173,http://127.0.0.1:3000"
    )

    # --- Recommendation pipeline ---
    candidate_count: int = 20
    result_count: int = 5
    relaxation_floor: int = 5

    # Bayesian shrinkage constant for weighted_rating. Constrained > 0 because 3,186
    # restaurants have zero votes and would divide by (votes + min_votes_m). The median
    # restaurant has only 24 votes, so this shrinks over half the catalogue toward the mean.
    min_votes_m: int = Field(default=50, gt=0)

    # Cap on results sharing one brand name. Cafe Coffee Day alone has 54 outlets.
    max_per_brand: int = 2

    # --- Soft-preference weights, added on top of weighted_rating ---
    weight_family_friendly: float = 0.15
    weight_quick_service: float = 0.15
    weight_online_order: float = 0.05
    weight_book_table: float = 0.05
    weight_dish_match: float = 0.20
    weight_rest_type_match: float = 0.10

    @property
    def artifact_path(self) -> Path:
        return self.processed_data_dir / self.artifact_name

    @property
    def facets_path(self) -> Path:
        return self.processed_data_dir / self.facets_name

    @property
    def llm_quota_path(self) -> Path:
        """Daily request/token counters. Under /data so it stays gitignored."""
        return PROJECT_ROOT / "data" / "runtime" / "llm_quota.json"

    @property
    def llm_enabled(self) -> bool:
        """False puts the pipeline in deterministic mode instead of raising."""
        return bool(self.openai_api_key and self.openai_api_key.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [part.strip() for part in self.cors_origins.split(",") if part.strip()]


settings = Settings()
