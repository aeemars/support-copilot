from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "support_copilot"
    wekoya_api_key: str = ""
    wekoya_model: str = "gemini-2.5-flash-lite"
    cors_origins: str = "http://localhost:5173"
    app_env: str = "development"

    model_config = SettingsConfigDict(
        env_file=[".env", "backend/.env", "../.env"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
