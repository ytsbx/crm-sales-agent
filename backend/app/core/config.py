"""应用配置。

所有可变参数集中在这里，通过 .env 覆盖；代码里不散落硬编码的连接串。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CRM-Sales-Agent"
    api_prefix: str = "/api/v1"
    debug: bool = True

    # 数据库。默认 PostgreSQL；改 MySQL 只需换这一行连接串。
    database_url: str = "postgresql+asyncpg://zhaorongkai@127.0.0.1:5432/crm_sales_agent"
    redis_url: str = "redis://127.0.0.1:6379/0"

    jwt_secret: str = "dev-secret-please-change"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 文件存储：先用本地磁盘，接口按对象存储的形状写，将来换 OSS/MinIO 只改这里
    storage_provider: str = "local"
    file_root: str = "data/files"
    max_upload_mb: int = 20

    # Sales Agent 用的模型（与公司现有知识库项目保持同一套配置）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    agent_max_tool_rounds: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
