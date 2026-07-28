from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mcp_api_token: str
    tesseract_cmd: Path = _DEFAULT_TESSERACT
    output_dir: Path = Path("recordings")
    fps: int = 30
    host: str = "127.0.0.1"
    port: int = 8000

    def ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir


settings = Settings()
