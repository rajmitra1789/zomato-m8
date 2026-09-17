"""Central configuration. Every tunable lives here rather than inline at its use site."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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

    # --- LLM ---
    # Aliased so the conventional unprefixed names work despite env_prefix.
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "ZOMATO_OPENAI_API_KEY"),
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_BASE_URL", "ZOMATO_OPENAI_BASE_URL"),
    )
    llm_model: str = "gpt-4o-mini"
    temperature: float = 0.3
    max_tokens: int = 1500
    llm_timeout_seconds: float = 30.0

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
    def llm_enabled(self) -> bool:
        """False puts the pipeline in deterministic mode instead of raising."""
        return bool(self.openai_api_key)


settings = Settings()
