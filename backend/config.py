from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str = ""

    # Browser Use Cloud
    browser_use_api_key: str = ""

    # Firecrawl
    firecrawl_api_key: str = ""

    # Serper (Google Search API — cheap, for wide-net people search)
    serper_api_key: str = ""

    # AgentMail
    agentmail_api_key: str = ""

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Database (MongoDB)
    mongodb_uri: str = ""
    mongodb_database: str = "outbo"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # ignore old env vars like DATABASE_URL
    }


settings = Settings()
