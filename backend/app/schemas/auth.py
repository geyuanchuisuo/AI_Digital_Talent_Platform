"""认证相关请求/响应模型（T-07）。

口径：`docs/接口设计.md` v1.3 §2、`docs/统一错误码与鉴权.md` v1.2 §五。
字段名严格照文档（`token` / `expires_in` / `user.display_name`），前端按此实现，不得改名。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50, description="登录名")
    password: str = Field(min_length=1, max_length=128, description="明文密码，仅用于本次校验")


class UserOut(BaseModel):
    """登录响应与 /auth/me 共用的用户视图；**不返回 password_hash**。

    `person_id`：绑定的主档 id（个人账号唯一；管理侧为 null）。2026-11-07
    贯通（REQ-B-20261106-02），B/C/D 据此做个人数据过滤的前端联动。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    role: str
    person_id: int | None = None


class LoginData(BaseModel):
    token: str
    expires_in: int = Field(description="有效期秒数，= JWT_EXPIRE_HOURS * 3600")
    user: UserOut
