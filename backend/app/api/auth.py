"""认证接口（T-07）：`POST /api/auth/login`、`GET /api/auth/me`。

口径：`docs/接口设计.md` v1.3 §2、`docs/统一错误码与鉴权.md` v1.2 §四/§五。
- 两个接口都返回统一成功体 `{code,message,data}`（`ok()`），登录成功 200（不是 202）。
- 登录失败 401 / 停用 403 由 `auth_service` 抛 `BizError`，全局处理器归一为中文错误体。
- `/auth/me` 挂 `get_current_user`，前端启动时用它校验本地 token 是否仍有效。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.response import ok
from app.database import get_db
from app.schemas.auth import LoginData, LoginRequest, UserOut
from app.service import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", summary="账号密码登录")
def login(
    payload: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """校验账号密码并签发 JWT；`expires_in` 单位秒（默认 86400）。"""
    user, token, expires_in = auth_service.login(db, payload.username, payload.password)
    data = LoginData(
        token=token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )
    return ok(data.model_dump())


@router.get("/me", summary="当前登录用户")
def me(user: CurrentUser):
    """返回 token 对应的用户信息；token 失效 401、账号停用 403。

    依赖由 `app.api.deps.get_current_user` 提供：无 token / 过期 / 篡改 → 401，
    token 合法但账号停用 → 403（一期不做 RBAC）。
    """
    return ok(UserOut.model_validate(user).model_dump())
