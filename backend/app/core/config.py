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

    # ---- 企业微信（PRD §8 / ER §6 / API §10）------------------------------
    # 全部留空即为"未配置"状态：同步接口会明确返回"还没配置凭据"，
    # 而不是静默假装成功。拿到 corp id / secret 后填这里即可，代码不用改。
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    # 内部成员与部门：通讯录同步密钥
    wecom_contact_secret: str = ""
    # 外部联系人：客户联系密钥（企微里是独立的一把 secret）
    wecom_external_contact_secret: str = ""
    # 事件回调（企微要求公网 HTTPS）：URL 上的 token 与 EncodingAESKey
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""
    # 企微 API 基址，一般不用改
    wecom_api_base: str = "https://qyapi.weixin.qq.com"
    # 单次同步最多拉多少页，防止配错时把对方接口打爆
    wecom_sync_max_pages: int = 50

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]

    @property
    def wecom_contact_ready(self) -> bool:
        """通讯录（部门 / 成员）同步是否具备条件。"""
        return bool(self.wecom_corp_id and self.wecom_contact_secret)

    @property
    def wecom_external_ready(self) -> bool:
        """外部联系人与跟进关系同步是否具备条件。"""
        return bool(self.wecom_corp_id and self.wecom_external_contact_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
