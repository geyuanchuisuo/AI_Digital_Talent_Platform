"""问数反馈服务（T-D12）——结果页「标记不准确」。

规则：
- **仅快照创建者可反馈**（验收③：越权反馈他人结果 403——管理侧查看是只读，不
  赋予代他人反馈权）；
- 快照不存在 → 404（不泄露他人快照存在性，与 T-D08 get_for_user 同口径）；
- 同 (snapshot, user) 重复标记幂等返回已有行（唯一约束兜底）；
- **反馈不参与任何统计计算**（验收②）——metric 计算路径不读本表，测试断言；
- 列表：管理侧（admin/hr）全量可见；personal 403（结果页本身管理侧限定）。
"""

from __future__ import annotations

import asyncio

from app.core.auth.scope import DENY_MESSAGE, get_account_scope
from app.core.errors import BizError
from app.database import SessionLocal

RESULT_NOT_FOUND = "记录不存在"
_FEEDBACK_FORBIDDEN = "仅结果创建者可标记反馈"
_FEEDBACK_MANAGEMENT_ONLY = "反馈列表仅管理侧可见"


async def create_feedback(user, snapshot_id: int, comment: str | None) -> dict:
    """标记结果不准确：仅快照创建者；重复标记幂等返回已有行。"""
    scope = get_account_scope(user)
    if scope.role not in ("admin", "hr"):
        raise BizError(403, DENY_MESSAGE)
    comment = (comment or "").strip() or None
    if comment is not None and len(comment) > 500:
        raise BizError(422, "反馈说明超过 500 字符")
    return await asyncio.to_thread(_create_feedback_sync, user, snapshot_id, comment)


def _create_feedback_sync(user, snapshot_id: int, comment: str | None) -> dict:
    # 延迟导入：避免 feedback 直连入口触发 app.models 包初始化循环（见契约 §3 纪律）
    from app.modules.analytics.models import AnalyticsQuerySnapshot, MetricFeedback

    with SessionLocal() as db:
        snap = db.get(AnalyticsQuerySnapshot, snapshot_id)
        if snap is None:
            raise BizError(404, RESULT_NOT_FOUND)
        if snap.created_by != getattr(user, "id", None):
            # 越权反馈他人结果 → 403（验收③）；不泄露快照内容
            raise BizError(403, _FEEDBACK_FORBIDDEN)

        existing = (
            db.query(MetricFeedback)
            .filter(
                MetricFeedback.snapshot_id == snapshot_id,
                MetricFeedback.created_by == user.id,
            )
            .first()
        )
        if existing is not None:
            row = existing  # 幂等：重复标记不新增
        else:
            row = MetricFeedback(snapshot_id=snapshot_id, comment=comment,
                                 created_by=user.id)
            db.add(row)
        db.commit()
        db.refresh(row)
        return _to_dict(row, snap)


async def list_feedback(user) -> list[dict]:
    """反馈列表（管理侧全量可见，验收③管理端语义）。"""
    scope = get_account_scope(user)
    if not scope.can_view_all_persons:
        raise BizError(403, _FEEDBACK_MANAGEMENT_ONLY)
    return await asyncio.to_thread(_list_feedback_sync, user)


def _list_feedback_sync(user) -> list[dict]:
    with SessionLocal() as db:
        from app.modules.analytics.models import AnalyticsQuerySnapshot, MetricFeedback

        rows = (
            db.query(MetricFeedback)
            .order_by(MetricFeedback.created_at.desc(), MetricFeedback.id.desc())
            .all()
        )
        snaps = {
            s.id: s
            for s in db.query(AnalyticsQuerySnapshot)
            .filter(AnalyticsQuerySnapshot.id.in_([r.snapshot_id for r in rows]))
            .all()
        } if rows else {}
        return [
            {
                **_to_dict(r),
                "snapshot": {
                    "metric_id": s.metric_id,
                    "params_json": s.params_json,
                    "data_time": s.data_time.isoformat() if s.data_time else None,
                    "result_json": s.result_json,
                },
            }
            for r, s in ((r, snaps.get(r.snapshot_id)) for r in rows)
        ]


def _to_dict(row: MetricFeedback, snap: AnalyticsQuerySnapshot | None = None) -> dict:
    return {
        "id": row.id,
        "snapshot_id": row.snapshot_id,
        "comment": row.comment,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "snapshot_metric_id": snap.metric_id if snap is not None else None,
    }
