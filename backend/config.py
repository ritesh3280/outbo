from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str = ""

    # Browser Use Cloud
    browser_use_api_key: str = ""

    # Firecrawl
    firecrawl_api_key: str = ""

    # AgentMail
    agentmail_api_key: str = ""

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Database
    database_url: str = "sqlite:///./outreach.db"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
