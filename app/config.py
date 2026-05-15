from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    env: str = "development"
    debug: bool = True

    gestix_api_url: str = "http://localhost:8001"
    gestix_api_url_public: str = "https://gestix.pro"

    whatsapp_numero: str = "51967317946"
    email_contacto: str = "info@perusistemas.pro"

    port: int = 4000

    # BD (Railway PostgreSQL)
    database_url: str = ""

    # Admin
    admin_bootstrap_password: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def whatsapp_link(self) -> str:
        return f"https://wa.me/{self.whatsapp_numero}"

    @property
    def api_backend_browser(self) -> str:
        return self.gestix_api_url_public if self.env == "production" else self.gestix_api_url

    @property
    def api_backend_server(self) -> str:
        return self.gestix_api_url


@lru_cache()
def get_settings() -> Settings:
    return Settings()
