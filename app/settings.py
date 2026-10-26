from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = ""
    admin_ids: str = ""
    app_secret: str = ""
    database_url: str = "sqlite:///./vpn-project.db"
    remnawave_api_url: str = ""
    remnawave_api_key: str = ""
    cookie_secure: bool = False
    web_origin: str = "http://127.0.0.1:8000"
    app_name: str = "VPN Studio"

    @property
    def admins(self) -> set[int]:
        return {int(item.strip()) for item in self.admin_ids.split(",") if item.strip()}


settings = Settings()
