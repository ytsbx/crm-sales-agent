from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class WeComSsoCallback(BaseModel):
    """企微网页授权登录回调入参（03-API §2）。

    只声明企微 OAuth 真正提供的东西：一次性 `code` 与一次性 `state`。

    **这里没有 `wecom_userid`**：前端自报的员工标识不是凭证。它此前是一个入参，
    等于把"前端已经换好 userid 了"当成可信认证——任何知道同事工号的人都能换到该
    同事的 token。身份只能由服务端用 code 换取，或由企业桥接的服务端签名证明；
    在服务端换取链路接通前，接口一律拒绝（见 router.wecom_sso_callback）。
    """

    # 旧客户端仍可能带 wecom_userid：多余字段忽略而不是报参数错。
    # 忽略 ≠ 接受——接口无论如何都拒绝签发，没必要用一条"参数校验失败"
    # 盖掉真正的拒绝原因（凭据/换取链路未配置）。
    model_config = ConfigDict(extra="ignore")

    #: 企微 OAuth 的一次性授权 code，只由服务端拿去换取 userid，绝不当作身份本身
    code: str | None = Field(default=None, max_length=512)
    #: 企微侧回传的 state：接通后必须做一次性校验（防伪造与重放），不能只记审计
    state: str | None = Field(default=None, max_length=512)


class DingTalkExchange(BaseModel):
    """钉钉扫码登录第三步：用一次性 ticket 换令牌。

    为什么要有这一步、而不是回调时直接把令牌交给前端：回调是**浏览器重定向**
    （GET），令牌只能挂在 URL 上——那会进浏览器历史、也会进服务器访问日志。
    所以回调只落一个 60 秒的一次性 ticket，前端再用 POST 把它换成令牌
    （走请求体，不进 URL）。ticket 用过即废、过期即废。
    """

    ticket: str = Field(min_length=1, max_length=128)


class PersonalAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str | None = Field(default=None, min_length=6, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_length(cls, value):
        if value is not None and len(value.encode("utf-8")) > 72:
            raise ValueError("密码最多 72 个字节")
        return value
