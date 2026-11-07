"""通用依赖（架构 §4：`api/deps.py` = JWT 鉴权、分页依赖）。

- `pagination`：T-02 落地，`page` 默认 1、`page_size` 默认 20、上限 200（接口 §1.3）。
- `get_current_user`：T-07 落地，口径见 `docs/统一错误码与鉴权.md` v1.2 §四。
  除 `POST /auth/login`、`GET /health` 外所有业务路由都挂它；一期不做 RBAC。
- `get_account_scope`：2026-11-07 落地（REQ-B-20261106-02 阶段 1）——B/C/D 的
  数据范围授权统一入口，契约见 `app/core/auth/scope.py` 与 A→B/A→D 响应。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.auth.scope import AccountScope, get_account_scope
from app.core.errors import BizError
from app.database import get_db
from app.models import User
from app.service.auth_service import ACCOUNT_DISABLED_MESSAGE, TOKEN_INVALID_MESSAGE, decode_access_token

# auto_error=False：无 Authorization 头时不抛框架英文 403，统一由本函数抛中文 401
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def pagination(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数，默认 20，上限 200"),
) -> PageParams:
    """接口 §1.3：`page` 默认 1、`page_size` 默认 20、上限 200。

    超出上限 / 小于 1 由 Pydantic 抛 422，再由 T-02 全局处理器归一为统一错误体。
    """
    return PageParams(page=page, page_size=page_size)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """解析 Bearer token → 用户行；无 token / 过期 / 篡改 401，停用账号 403。

    顺序要求（文档 §四）：先判 token 有效性，再查库，再判 `is_active`——
    停用账号持旧 token 访问也必须 403，不能因为"token 没过期"就放行。
    """
    if creds is None or not creds.credentials:
        raise BizError(401, TOKEN_INVALID_MESSAGE)
    payload = decode_access_token(creds.credentials)
    user = db.get(User, payload.user_id)
    if user is None:
        raise BizError(401, TOKEN_INVALID_MESSAGE)
    if not user.is_active:
        raise BizError(403, ACCOUNT_DISABLED_MESSAGE)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def _account_scope_from_user(user: User) -> AccountScope:
    """把登录用户映射为数据范围（阶段 1 三值角色；纯函数见 core/auth/scope.py）。"""
    return get_account_scope(user)


def get_account_scope_dep(
    current_user: Annotated[User, Depends(get_current_user)],
) -> AccountScope:
    """FastAPI 依赖：登录用户 → AccountScope；B/C/D 的路由直接注入使用。"""
    return _account_scope_from_user(current_user)


AccountScopeDep = Annotated[AccountScope, Depends(get_account_scope_dep)]
