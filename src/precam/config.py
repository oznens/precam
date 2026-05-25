from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    helius_api_key: str = ""
    dexscreener_base: str = "https://api.dexscreener.com"

    database_url: str = "sqlite+aiosqlite:///./data/runtime/precam.db"
    log_level: str = "INFO"

    scan_interval: int = 60
    min_liquidity_usd: float = 5000.0
    early_max_age_min: int = 720

    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8000

    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])

    @property
    def helius_rpc(self) -> str:
        if not self.helius_api_key:
            return "https://api.mainnet-beta.solana.com"
        return f"https://mainnet.helius-rpc.com/?api-key={self.helius_api_key}"

    @property
    def helius_api(self) -> str:
        return f"https://api.helius.xyz/v0"

    def ensure_dirs(self) -> None:
        (self.project_root / "data" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project_root / "logs").mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
