import os
from pathlib import Path
from typing import Optional


def load_env_file(dotenv_path: Optional[Path] = None) -> None:
    """
    Lightweight .env loader that parses key=value pairs into os.environ
    if not already present. Avoids mandatory external dependencies.
    """
    if dotenv_path is None:
        dotenv_path = Path(".env")
    
    if not dotenv_path.exists() or not dotenv_path.is_file():
        return

    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Strip surrounding matching quotes
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = val
    except Exception:
        pass


# Attempt to load .env on module import
load_env_file()


class Settings:
    """Application runtime settings loaded from environment variables."""

    @property
    def GOOGLE_API_KEY(self) -> str:
        return os.getenv("GOOGLE_API_KEY", "")

    @property
    def GEMINI_MODEL(self) -> str:
        # Verified models: gemini-2.5-flash, gemini-1.5-flash, gemini-2.0-flash
        # Assignment mentions gemini-3.8-flash, which can be passed if supported
        return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    @property
    def GITHUB_TOKEN(self) -> str:
        return os.getenv("GITHUB_TOKEN", "")

    @property
    def GITHUB_WEBHOOK_SECRET(self) -> str:
        return os.getenv("GITHUB_WEBHOOK_SECRET", "")

    @property
    def SMTP_HOST(self) -> str:
        return os.getenv("SMTP_HOST", "smtp.gmail.com")

    @property
    def SMTP_PORT(self) -> int:
        try:
            return int(os.getenv("SMTP_PORT", "587"))
        except ValueError:
            return 587

    @property
    def SMTP_EMAIL(self) -> str:
        return os.getenv("SMTP_EMAIL", "")

    @property
    def SMTP_APP_PASSWORD(self) -> str:
        return os.getenv("SMTP_APP_PASSWORD", "")

    @property
    def RECIPIENT_EMAIL(self) -> str:
        return os.getenv("RECIPIENT_EMAIL", "") or self.SMTP_EMAIL

    @property
    def HOST(self) -> str:
        return os.getenv("HOST", "0.0.0.0")

    @property
    def PORT(self) -> int:
        try:
            return int(os.getenv("PORT", "8000"))
        except ValueError:
            return 8000


settings = Settings()
