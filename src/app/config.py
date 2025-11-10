"""
Application configuration module using Pydantic BaseSettings v2.

This module provides centralized configuration management for the InvoiceIQ
application, loading settings from environment variables and .env files.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings can be configured via environment variables or a .env file.
    Environment variable names are case-insensitive.
    """

    # WhatsApp Business API Configuration
    waba_token: str
    waba_phone_id: str
    waba_verify_token: str

    # SMS Provider Configuration
    sms_api_key: str
    sms_username: str

    # M-PESA Configuration
    mpesa_consumer_key: str
    mpesa_consumer_secret: str
    mpesa_shortcode: str
    mpesa_passkey: str
    mpesa_callback_url: str

    # Database Configuration
    database_url: str

    # Application Configuration
    app_name: str = "InvoiceIQ"
    debug: bool = False
    environment: str = "development"

    # Pydantic v2 configuration using model_config
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Create singleton settings instance
# Settings will be loaded from environment variables or .env file
settings = Settings()  # type: ignore[call-arg]