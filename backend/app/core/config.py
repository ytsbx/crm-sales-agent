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
    jwt_secret: str = "dev-secret-please-change"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720
    #: `/auth/refresh` 允许"已过期多久之内"仍然续期。过期太久就要求重新登录，
    #: 否则一个泄漏的旧 token 等于永久有效。
    refresh_grace_minutes: int = 720

    #: 登录防爆破：滑动窗口内同一（用户名+IP）连续失败达到上限即锁定。
    login_max_attempts: int = 5
    login_lockout_window_minutes: int = 10

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 文件存储：先用本地磁盘，接口按对象存储的形状写，将来换 OSS/MinIO 只改这里
    storage_provider: str = "local"
    file_root: str = "data/files"
    max_upload_mb: int = 20

    # Sales Agent 用的模型（与公司现有知识库项目保持同一套配置）
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    # ---- 定时任务调度（公海回收 / 自动任务规则的每日自动执行）----
    # scheduler_enabled=False 可整体关掉（例如多实例部署时只让一台跑）
    scheduler_enabled: bool = False
    scheduler_recycle_hour: int = 2   # 公海回收：每天几点跑（24 小时制）
    scheduler_task_rules_hour: int = 2  # 自动任务规则：每天几点跑
    # 通知失败重投：每多少分钟扫一次"到期该重试"的失败通知（文档 §六）。
    # 用分钟级而不是每天——退避最大 2 小时，按天扫等于退避毫无意义。
    scheduler_retry_minutes: int = 10
    # 通知日报：每天几点把 digest 级通知合成一条发出（验收 24）。
    # 放在上班后不久，让人一早就能看到昨天攒下的事；紧急项不走这里、即时推。
    scheduler_digest_hour: int = 9
    scheduler_digest_minute: int = 30
    agent_max_tool_rounds: int = 3

    # ---- 企业微信（PRD §8 / ER §6 / API §10）------------------------------
    # ---- 钉钉 OA 审批（文档 §11.3 :152 / 场景11）--------------------------
    # 应用已在钉钉开放平台建好（CRM询价审批对接 / AgentId 5035992137）。
    # **权限勾选后必须"发布版本"才生效**——控制台顶部那条橙色提示就是它，
    # 这是联调最常见的卡点：不发布，接口一律报无权限。
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_agent_id: str = ""
    dingtalk_base_url: str = "https://api.dingtalk.com"
    # OA 审批状态轮询：每多少分钟拉一次（文档 §11.3 :152 的"回收状态与结果"）。
    # 先走轮询而不是事件订阅——不需要公网回调地址、不需要额外的后台授权，
    # 而且不会丢消息（这轮没查到下轮还会查）。询价审批本身要几小时到几天，
    # 几分钟延迟无影响。
    scheduler_oa_sync_minutes: int = 3
    #: 钉钉外部调用总闸。**默认 True = 关**：阻止全部钉钉请求，
    #: 包括 token、人员/部门/模板查询、图片上传、审批发起/重发及状态同步。
    #: 关闭时"发起审批"只在 CRM 记一行 skipped 并写明原因——
    #: **不是失败、更不是成功**，联调和演示都能看出"这一步没真的发出去"。
    #: 要真发必须显式设 DINGTALK_PUSH_OFF=0，并且只对测试用的本人账号发。
    dingtalk_push_off: bool = True

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
    # 企微推送总闸：默认关闭。开发/回归数据不得触达真实用户；只有显式设置
    # WECOM_PUSH_OFF=0 才允许 dispatch_pending 投递外部消息。
    wecom_push_off: bool = True
    # 离职继承硬锁：该操作会变更**真实客户**在微信里看到的服务人员，
    # 默认禁止执行；需业务确认后由管理员置 1 才解锁（403 拒绝并说明）
    wecom_transfer_enabled: bool = False

    # ---- ERP / MES（API §28 / 05-TECH §15）--------------------------------
    # 与企微同样的原则：留空即"未配置"，推送接口明确报 50203，
    # 绝不把订单标成已推送（否则 ERP 里没有单、CRM 却显示已同步，最难排查）。
    # provider 可选 jushuitan / jst；填错或空都会走占位 Adapter（调用即报错）。
    erp_provider: str = ""
    erp_base_url: str = ""
    erp_app_key: str = ""
    erp_app_secret: str = ""
    #: ERP/MES 回调的共享密钥（P0 修复）。
    #: **空值 = 回调入口关闭**——没配密钥就拒绝，绝不"没配就放行"。
    #: 这两个回调会直接改订单状态（取消/签收/完成），不能匿名开放：
    #: 任何能访问到该端口的人都能伪造"已签收"。
    erp_webhook_secret: str = ""

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
