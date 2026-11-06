"""登录与 JWT 签发/校验（T-07）。

唯一口径：`docs/统一错误码与鉴权.md` v1.2 §四/§五、`docs/接口设计.md` v1.3 §2。
- 算法固定 HS256，`JWT_EXPIRE_HOURS=24`；payload 只放 sub/username/role/iat/exp。
- 用户名不存在与密码错误**统一 401「用户名或密码错误」**，不区分（防账号枚举）。
- 密码正确但 `is_active=0` → 403「账号已停用」。
- 一期不做 RBAC，不校验角色。

安全细节：
- 用户不存在时也跑一次 bcrypt 校验（DUMMY_HASH），让响应耗时与"密码错误"一致，
  避免通过响应时间判断用户名是否存在。
- bcrypt 4.x 对超过 72 字节的密码抛 ValueError，此处按"密码错误"处理，不向上冒 500。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import BizError
from app.dao import users as users_dao
from app.models import User

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
LOGIN_FAILED_MESSAGE = "用户名或密码错误"
ACCOUNT_DISABLED_MESSAGE = "账号已停用"
TOKEN_INVALID_MESSAGE = "未登录或登录已过期"

# 仅用于恒定耗时，不对应任何真实账号（bcrypt cost 与种子账号一致：12）
DUMMY_HASH = b"$2b$12$ZrsOmaypUt1d.uvnvQzsHePnKCa/IFkRsu4bg5RJtkCFQUlBLVbN."
_BCRYPT_MAX_BYTES = 72


@dataclass(frozen=True)
class TokenPayload:
    user_id: int
    username: str
    role: str


def verify_password(plain_password: str, password_hash: str | bytes) -> bool:
    """bcrypt 校验；哈希格式异常或密码过长一律判失败，不抛异常。"""
    raw = plain_password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        return False
    hashed = password_hash.encode("utf-8") if isinstance(password_hash, str) else password_hash
    try:
        return bcrypt.checkpw(raw, hashed)
    except ValueError:
        logger.warning("password_hash 格式非法，按校验失败处理")
        return False


def hash_password(plain_password: str) -> str:
    """生成 bcrypt 哈希（种子/测试/改密复用，避免各处自带一套参数）。"""
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def create_access_token(user: User, *, now: datetime | None = None) -> tuple[str, int]:
    """签发 JWT，返回 `(token, expires_in 秒)`（接口 §2 的 `expires_in`）。"""
    issued_at = now or datetime.now(timezone.utc)
    expires_delta = timedelta(hours=settings.jwt_expire_hours)
    payload = {
        # sub 必须是字符串（python-jose 对 int sub 会在部分版本报类型错）
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + expires_delta).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> TokenPayload:
    """解码并校验 JWT；过期/篡改/字段缺失统一 401，不暴露具体原因。"""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except ExpiredSignatureError as exc:
        raise BizError(401, TOKEN_INVALID_MESSAGE) from exc
    except JWTError as exc:
        raise BizError(401, TOKEN_INVALID_MESSAGE) from exc

    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise BizError(401, TOKEN_INVALID_MESSAGE) from exc
    return TokenPayload(
        user_id=user_id,
        username=str(payload.get("username") or ""),
        role=str(payload.get("role") or ""),
    )


def authenticate(db: Session, username: str, password: str) -> User:
    """校验账号密码；成功返回用户行，失败抛 401 / 403。"""
    user = users_dao.get_by_username(db, username)
    if user is None:
        # 恒定耗时：不存在也走一次 bcrypt，避免响应时间泄露账号是否存在
        verify_password(password, DUMMY_HASH)
        raise BizError(401, LOGIN_FAILED_MESSAGE)
    if not verify_password(password, user.password_hash):
        raise BizError(401, LOGIN_FAILED_MESSAGE)
    if not user.is_active:
        raise BizError(403, ACCOUNT_DISABLED_MESSAGE)
    return user


def login(db: Session, username: str, password: str) -> tuple[User, str, int]:
    """登录主流程：认证 → 更新 last_login_at → 签 token。

    `last_login_at` 用本地时间（接口 §1.3 时间口径 Asia/Shanghai），
    与 JWT 内部使用的 UTC 时间戳分开，避免在业务字段里混入时区。
    """
    user = authenticate(db, username, password)
    users_dao.touch_last_login(db, user, datetime.now())
    db.commit()
    db.refresh(user)
    token, expires_in = create_access_token(user)
    logger.info("login ok user_id=%s username=%s", user.id, user.username)
    return user, token, expires_in
