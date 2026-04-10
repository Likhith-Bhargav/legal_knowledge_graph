"""
Core configuration management using pydantic-settings.
Follows the 12-factor app methodology for configuration.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Neo4jSettings(BaseSettings):
    uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    password: str = Field(default="password", alias="NEO4J_PASSWORD")
    database: str = Field(default="neo4j", alias="NEO4J_DATABASE")
    max_connection_pool_size: int = Field(default=50, alias="NEO4J_MAX_POOL_SIZE")

    model_config = SettingsConfigDict(env_file="config/.env", extra="ignore")


class LLMSettings(BaseSettings):
    # Which provider to use: "openai" | "anthropic" | "gemini"
    provider: str = Field(default="openai", alias="LLM_PROVIDER")

    # Model name — interpreted by the chosen provider
    # OpenAI:    gpt-4o, gpt-4-turbo, gpt-3.5-turbo, ...
    # Anthropic: claude-sonnet-4-6, claude-opus-4-6, ...
    # Gemini:    gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash, ...
    model: str = Field(default="gpt-4o", alias="LLM_MODEL")

    # Provider API keys — only the key for the active provider is required
    api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")

    temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    max_tokens: int = Field(default=2048, alias="LLM_MAX_TOKENS")

    model_config = SettingsConfigDict(env_file="config/.env", extra="ignore")


class AppSettings(BaseSettings):
    app_name: str = "Legal Knowledge Graph API"
    version: str = "1.0.0"
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    api_prefix: str = "/api/v1"

    model_config = SettingsConfigDict(env_file="config/.env", extra="ignore")


class Settings:
    """Aggregate settings container."""
    neo4j: Neo4jSettings = Neo4jSettings()
    llm: LLMSettings = LLMSettings()
    app: AppSettings = AppSettings()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
