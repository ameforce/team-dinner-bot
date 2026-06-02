from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime_defaults import DEFAULT_TIMEZONE


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    slack_bot_token: str
    slack_app_token: str
    slack_signing_secret: str

    app_name: str = "Team Dinner Bot"
    app_slug: str = "team-dinner-bot"
    bot_display_name: str = "Team Dinner Bot"

    database_url: str = "sqlite:///./data/team-dinner-bot.db"
    lock_file_path: str = "./data/team-dinner-bot.lock"
    default_timezone: str = DEFAULT_TIMEZONE
    default_poll_hour: int = 10
    default_poll_duration_hours: int = 24
    encryption_key: str = ""

    admin_user_ids: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_calendar_id: str = "primary"

    @property
    def admin_ids(self) -> set[str]:
        if not self.admin_user_ids.strip():
            return set()
        return {x.strip() for x in self.admin_user_ids.split(",") if x.strip()}


settings = Settings()
