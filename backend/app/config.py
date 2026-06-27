from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://prowin:prowin_dev@localhost:5432/prowin"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin_dev"
    s3_bucket_name: str = "prowin-belege"
    s3_region: str = "eu-central-1"

    whatsapp_provider: str = "stub"
    ocr_provider: str = "stub"
    llm_provider: str = "stub"

    # Mistral API (OCR_PROVIDER=live + LLM_PROVIDER=live)
    mistral_api_key: str = ""
    mistral_api_base_url: str = "https://api.mistral.ai/v1"
    ocr_model: str = "mistral-ocr-latest"
    llm_model: str = "mistral-small-latest"

    # Meta WhatsApp Cloud API
    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_graph_version: str = "v21.0"

    secret_key: str = "dev-secret-key"
    debug: bool = False
    log_level: str = "info"


settings = Settings()
