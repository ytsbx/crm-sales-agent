from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class WeComSsoCallback(BaseModel):
    """企微网页授权登录回调（03-API §2）。

    `wecom_userid` 是企微侧换出来的用户标识，与 `users.wecom_userid` 对应。
    """

    wecom_userid: str = Field(min_length=1, max_length=128)
    #: 企微侧回传的 state，前端用它防 CSRF；这里只记录进审计，不做校验
    state: str | None = None
