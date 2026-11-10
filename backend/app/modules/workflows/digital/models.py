"""数字员工 ORM（T-D21，纯站内版——2026-11-10 用户确认方案一）。

⚠️ 与 workflows/models.py 同一纪律：定义 ORM 类、被 app/models/__init__.py
导入，**不要作为 import 入口**；使用方一律 `from app.models import MemberImAccount`。

结构：
- MemberImAccount      成员 IM 账号绑定（member_im_accounts）
- DigitalTask          数字员工任务运行（digital_tasks）
- DigitalOutboundRequest  站外外发请求（digital_outbound_requests，一律挂起待审批）

设计口径（2026-11-10 用户确认，登记更新记录）：
- IM 通道一期不依赖任何平台凭据：channel 预留 wecom/dingtalk/feishu，
  source=manual（管理员手动登记外部账号 ID）；
- 三类只读场景（数据日报/简历队列巡检/待办提醒）结果仅入库 + 站内通知
  指定管理员，不做站外外发（DEC-20261107-DLINE-CONFIRMED）；
- 外发请求只登记不执行，pending 审批通过且通道可用才可发出（验收②）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import (
    BOOL_TYPE,
    JSONDict,
    JSONList,
    MYSQL_TABLE_OPTIONS,
    PK_TYPE,
    PKMixin,
    UpdatedAtMixin,
    num_default,
    str_default,
)

# IM 通道白名单（枚举外 422 拒绝入库）。wecom 为冻结口径目标；dingtalk/feishu
# 为通道预留（未批准不启用 adapter）。
IM_CHANNELS: tuple[str, ...] = ("wecom", "dingtalk", "feishu")

# 绑定来源：manual=管理员手动登记外部账号 ID（一期无凭据的实际形态）；
# oauth=平台授权绑定（凭据到位后的升级形态，一期不实现）。
IM_SOURCES: tuple[str, ...] = ("manual", "oauth")

# 数字员工任务类型白名单（三类只读场景，一期封闭）。
TASK_TYPES: tuple[str, ...] = ("daily_report", "resume_patrol", "todo_reminder")

TASK_TYPE_LABELS: dict[str, str] = {
    "daily_report": "数据日报整理",
    "resume_patrol": "简历队列巡检",
    "todo_reminder": "待办提醒",
}

# 任务状态：running（执行中）→ success/failed（终态）。幂等重跑不重复落行。
TASK_STATUSES: tuple[str, ...] = ("running", "success", "failed")

# 外发请求状态：只登记不执行；approve 后由人工接管发送，不自动外发。
OUTBOUND_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "sent", "cancelled")


class MemberImAccount(PKMixin, UpdatedAtMixin, Base):
    """成员 IM 账号绑定（`member_im_accounts`）。

    (user_id, channel) 唯一——同一人同一通道只有一个外部账号；im_user_id 为
    外部平台侧账号标识，一期 manual 登记不校验真实性（凭据到位后走 oauth 升级）。
    """

    __tablename__ = "member_im_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "channel", name="uk_member_im_user_channel"),
        Index("idx_mia_user", "user_id"),
        MYSQL_TABLE_OPTIONS,
    )

    user_id: Mapped[int] = mapped_column(PK_TYPE, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    im_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", server_default=str_default("manual")
    )
    # 软停用：停用后只读场景不向该账号发站外通知（站内通知不受影响）
    enabled: Mapped[int] = mapped_column(
        BOOL_TYPE, nullable=False, default=1, server_default=num_default(1)
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DigitalTask(PKMixin, UpdatedAtMixin, Base):
    """数字员工任务运行（`digital_tasks`）。

    三类只读场景各跑一次落一行；result_json 为场景产物（日报摘要/巡检报告/
    待办清单），summary 为站内通知正文。idempotency_key 业务级唯一——同场景
    同周期重跑不产生第二行（验收①幂等）。
    """

    __tablename__ = "digital_tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_digital_tasks_idem"),
        Index("idx_dt_type_status", "task_type", "status"),
        MYSQL_TABLE_OPTIONS,
    )

    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running", server_default=str_default("running")
    )
    # 业务级幂等键（同场景同周期重跑不产生第二行）；通知键另含 task id
    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False)
    # 触发方式：manual=管理员手动 / schedule=定时（默认关闭，启用须管理员）
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    triggered_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    # 通知的目标管理员（站内通知收件人；可空=仅入库不发通知）
    notify_admin_ids: Mapped[list | None] = mapped_column(JSONList, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DigitalOutboundRequest(PKMixin, UpdatedAtMixin, Base):
    """站外外发请求（`digital_outbound_requests`）——**只登记，不执行**。

    任何把数字员工产物送到 IM 通道的意图先落一行 pending；审批通过（管理侧 +
    四眼）也只置 approved，实际发送由人工接管（可接管红线）。pending/approved
    期间业务状态不动（验收③）。
    """

    __tablename__ = "digital_outbound_requests"
    __table_args__ = (
        Index("idx_dor_status", "status"),
        Index("idx_dor_task", "task_id"),
        MYSQL_TABLE_OPTIONS,
    )

    task_id: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    target_im_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 外发内容快照（审批对象；通道未配置/审批挂起时原样保留）
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=str_default("pending")
    )
    requested_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    decided_by: Mapped[int | None] = mapped_column(PK_TYPE, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
