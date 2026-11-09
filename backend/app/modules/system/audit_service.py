"""操作审计日志服务（F09：必要操作日志——高影响动作落 sys_operation_logs）。

`record_operation` 由中间件/显式调用写入；记录脱敏后的 path 与参数长度，
不落密钥与个人原文（V2.6 §12.4「不写密钥或不必要的个人原文」）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.modules.system.models import SysOperationLog


def record_operation(
    db: Session,
    *,
    user_id: int | None,
    username: str | None,
    method: str,
    path: str,
    status: str,
    duration_ms: int | None = None,
    ip: str | None = None,
    module: str | None = None,
    action: str | None = None,
    params_summary: str | None = None,
) -> SysOperationLog:
    """写一条操作审计；失败不抛出（审计写入不得影响业务响应）。"""
    try:
        row = SysOperationLog(
            user_id=user_id,
            username=username,
            module=module,
            action=action,
            method=method[:10] if method else None,
            path=path[:255] if path else None,
            params=params_summary[:2000] if params_summary else None,
            ip=ip,
            status=status,
            duration_ms=duration_ms,
            created_at=datetime.now(),
        )
        db.add(row)
        db.commit()
        return row
    except Exception:
        db.rollback()
        return None  # type: ignore[return-value]
