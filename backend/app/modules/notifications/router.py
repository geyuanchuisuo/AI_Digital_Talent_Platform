"""通知域路由（T-D03）：列表 / 详情 / 已读 / 枚举元数据。

由 main.py `include_router(router)` 注册（A，见 docs/requests/D/ 限流中间件挂载请求）；
读取隔离与越权语义见 service（个人仅本人，管理侧全量，已读仅收件人）。
静态路由 /meta 必须声明在 /{notification_id} 之前。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, PageParams, pagination
from app.core.auth.scope import get_account_scope
from app.core.errors import BizError
from app.core.response import ok
from app.database import get_db
from app.modules.notifications import service
from app.modules.notifications.models import EVENT_TYPES

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", summary="我的通知列表（管理侧可查全量/按收件人）")
def list_notifications(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page_params: Annotated[PageParams, Depends(pagination)],
    status: str | None = Query(None, description="pending/sent/failed/read"),
    recipient_user_id: int | None = Query(None, description="仅管理侧可用；按收件人过滤"),
):
    if recipient_user_id is not None and not get_account_scope(user).can_view_all_persons:
        raise BizError(403, "仅管理侧可按收件人过滤")
    rows, total = service.list_for_user(
        db, user=user, status=status, recipient_user_id=recipient_user_id,
        page=page_params.page, page_size=page_params.page_size,
    )
    return ok(
        {
            "items": [service.to_dict(r) for r in rows],
            "total": total,
            "page": page_params.page,
            "page_size": page_params.page_size,
        }
    )


@router.get("/meta/event-types", summary="事件枚举与状态白名单（F09 封闭清单）")
def event_meta():
    return ok(
        {
            "event_types": list(EVENT_TYPES),
            "note": "封闭清单 12 类 / 14 枚举；清单外事件一期不发送，新增须用户批准",
        }
    )


@router.get("/{notification_id}", summary="通知详情（收件人或管理侧）")
def get_notification(
    notification_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.get_for_user(db, user=user, notification_id=notification_id)
    return ok(service.to_dict(row))


@router.post("/{notification_id}/read", summary="标记已读（仅收件人本人）")
def read_notification(
    notification_id: int,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    row = service.mark_read(db, user=user, notification_id=notification_id)
    return ok(service.to_dict(row))
