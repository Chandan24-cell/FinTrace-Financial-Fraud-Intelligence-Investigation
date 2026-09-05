from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    timezone: str = "Asia/Kolkata"

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000

    jwt_secret: str = Field(default="dev-only-not-for-production")
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24
    # Sliding-window rate limit per JWT (or per IP for anonymous calls).
    # Default of 0 means "use the per-env default in the model_validator
    # below" — keeps tests honest (they can pin a tiny limit explicitly)
    # while letting prod/dev pick a sensible bucket size automatically.
    rate_limit_per_min: int = 0

    # PRD §10 Day 24 — CORS lockdown. Comma-separated origin list; defaults
    # cover the Vite dev server. Production override via CORS_ALLOWED_ORIGINS.
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "sentinel_dev_pwd"
    neo4j_database: str = "neo4j"

    mistral_api_key: str = "PLACEHOLDER_MISTRAL_API_KEY"
    mistral_model: str = "mistral-small-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"
    mistral_timeout_sec: float = 20.0


    mca21_api_key: str = "PLACEHOLDER_MCA21_KEY"
    cersai_api_key: str = "PLACEHOLDER_CERSAI_KEY"
    bse_sme_api_key: str = "PLACEHOLDER_BSE_KEY"

    # PRD §10 free-source plan, Phase A — MCA Public Portal scraper.
    # Replaces both paid MCA21 V3 and CERSAI with free Playwright scraping
    # of mca.gov.in. The session-dir holds the captcha-bootstrap cookies;
    # gitignored so they never leak into a commit. See docs/INGEST_MCA_PUBLIC.md.
    mca_public_session_dir: str = "./.mca_session"

    # Durable upload overlays. The upload endpoints update this JSON file so
    # GST/bank/PDF overlays survive backend restarts without requiring Redis.
    upload_overlay_path: str = "./data/uploads/overlays.json"

    # PRD §10 Day 20 — live source-side polling. asyncio.create_task pattern
    # (PRD §2.1 compliant). Disabled in tests to keep them deterministic.
    scheduler_enabled: bool = True
    scheduler_mca21_refresh_sec: int = 86_400      # 24h
    scheduler_cersai_refresh_sec: int = 86_400     # 24h
    scheduler_nclt_poll_sec: int = 86_400          # 24h
    scheduler_wilful_poll_sec: int = 30 * 86_400   # 30 days (PRD: "Monthly refresh")
    scheduler_shared_attr_sec: int = 3_600         # 1h

    # Per-env defaults applied only when the operator didn't pin a value
    # explicitly. PRD §10 Day 24 was tuned for the 10/min dev test plan;
    # production needs more headroom (judges, demo concurrency, multi-tab
    # rehearsals), while dev should be generous so the test harness and
    # the Vite dev server aren't tripping themselves.
    _RATE_LIMIT_DEFAULTS = {"dev": 200, "staging": 120, "prod": 60}

    @model_validator(mode="after")
    def _apply_env_defaults(self) -> "Settings":
        if self.rate_limit_per_min == 0:
            object.__setattr__(
                self,
                "rate_limit_per_min",
                self._RATE_LIMIT_DEFAULTS.get(self.app_env, 60),
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
