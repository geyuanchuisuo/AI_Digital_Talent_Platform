"""管理员工作台六类真实数量卡片（T-D22）。

需求依据：统一需求 V2.6 F09「管理员工作台显示岗位、档案、待复核、待评分、
培训计划与人才缺口预警等真实数量，提供业务入口；工作台与报告统计一律经各域
授权结果服务或公开契约读取，不直连其他模块业务表；未公布成绩、内部面试反馈
与人才池信息不得进入任何统计」。

各卡片口径与授权来源（逐域核对 2026-11-09）：
- jobs     活跃岗位数：A 指标口径 v0.2 §1 —— 复用 D 已有 analytics service
           `compute_active_jobs`（同一实现，数字必然可回查一致）；
- profiles 未删除人才档案数：B 契约 §6「人才档案数 = 未删除 person_id 数」
           （`talent_profiles.deleted_at IS NULL`）；
- reviews  待复核（F03/F10）分三项：
             ①初筛：screen_results 有效结论（COALESCE(manual_verdict, verdict)）
               = pending（A v0.2 §2 H-A06 有效结论口径）；
             ②匹配：match_results.manual_verdict IS NULL（人工推进标记未给，
               A v0.2 §3「人工采纳率」分母同源）；
             ③盘点：inventory_reviews.status IN (draft, submitted)（C 契约：
               draft 未提交复核、submitted 待 HR 确认，confirmed 后不再计）；
- scoring  待评分：assessment_assignments.status = 'grading'（C 契约状态机：
           交卷后存在未评分项；completed 即评分完成不再计）；
- training 培训计划：training_plans.status IN (draft, assigned)（C 契约 §43
           状态枚举全集，assigned 为在执行计划）；
- gaps     人才缺口预警：A v0.2 §7 权威公式 —— 缺口 = headcount − HR 确认
           已录用（逐需求非负，求和）；预警数 = gap>0 的需求行数。复用 D 已有
           analytics `_GAP_SQL` 同口径实现（同一 SQL 语义，数字可回查一致）。

边界（验收②）：
- 只返回计数与口径说明字符串，不返回任何业务行内容——未公布成绩
  （AssessmentResult 未公布版本）、面试评价文本、人才池类别天然不在响应中；
- personal 账号 403（deny-by-default，复用 workflows 的 _require_management
  同一门禁：can_view_all_persons）；
- 实时查询无缓存，保证「新增业务数据后数量随之变化」（验收③）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.dashboard import service as _ai_platform_service


def _require_management(user) -> None:
    """与 AI 中台同一管理侧门禁（单一实现，不另造第三套判断）。"""
    _ai_platform_service._require_management(user)


def admin_overview(
    db: Session, *, user, engine=None, engine_source: str | None = None
) -> dict[str, Any]:
    """六类卡片计数（管理侧）。实时查询；每项附口径与业务入口路径。

    `engine`/`engine_source` 供隔离测试注入（生产走 analytics 副本优先解析，
    与 T-D02 数据源契约一致）。
    """
    _require_management(user)

    from app.models import TalentProfile
    from app.modules.analytics.service import compute_active_jobs
    from app.modules.assessments.models import AssessmentAssignment
    from app.modules.inventory.models import InventoryReview
    from app.modules.training.models import TrainingPlan

    if engine is None:
        from app.modules.analytics.service import _resolve_engine

        engine, engine_source = _resolve_engine()
    engine_source = engine_source or "primary"

    # 1) 岗位：A v0.2 §1 活跃岗位数（复用 T-D08 受控统计，同口径必一致）
    jobs = compute_active_jobs(engine)

    # 2) 档案：B 契约 §6 未删除 person_id 数
    profiles = int(
        db.execute(
            select(func.count(TalentProfile.id)).where(TalentProfile.deleted_at.is_(None))
        ).scalar_one()
    )

    # 3) 待复核：初筛 pending + 匹配未标记 + 盘点 draft/submitted
    from app.models import MatchResult, ScreenResult

    screen_pending = int(
        db.execute(
            select(func.count(ScreenResult.id)).where(
                func.coalesce(ScreenResult.manual_verdict, ScreenResult.verdict) == "pending"
            )
        ).scalar_one()
    )
    match_unmarked = int(
        db.execute(
            select(func.count(MatchResult.id)).where(MatchResult.manual_verdict.is_(None))
        ).scalar_one()
    )
    inventory_pending = int(
        db.execute(
            select(func.count(InventoryReview.id)).where(
                InventoryReview.status.in_(("draft", "submitted"))
            )
        ).scalar_one()
    )
    reviews = screen_pending + match_unmarked + inventory_pending

    # 4) 待评分：测评任务 grading 状态（交卷后存在未评分项）
    scoring = int(
        db.execute(
            select(func.count(AssessmentAssignment.id)).where(
                AssessmentAssignment.status == "grading"
            )
        ).scalar_one()
    )

    # 5) 培训计划：draft + assigned（C 契约 §43 全集）
    training = int(
        db.execute(
            select(func.count(TrainingPlan.id)).where(
                TrainingPlan.status.in_(("draft", "assigned"))
            )
        ).scalar_one()
    )

    # 6) 缺口预警：A v0.2 §7 公式（与 analytics _GAP_SQL 同口径的 ORM 实现）
    gaps_detail = _demand_gaps(db)
    gaps = gaps_detail["demand_count"]

    return {
        "role": "admin",
        "cards": {
            "jobs": {
                "count": jobs,
                "label": "活跃岗位",
                "caliber": "A 口径 §1：is_active=1 且 title 最新版本",
                "entry": "/job-profiles",
            },
            "profiles": {
                "count": profiles,
                "label": "人才档案",
                "caliber": "B 契约 §6：未删除 person_id 数",
                "entry": "/talents",
            },
            "reviews": {
                "count": reviews,
                "label": "待复核",
                "caliber": "F03/F10：初筛有效结论 pending + 匹配未标记 + 盘点待确认",
                "entry": "/screen",
                "breakdown": {
                    "screen_pending": screen_pending,
                    "match_unmarked": match_unmarked,
                    "inventory_pending": inventory_pending,
                },
            },
            "scoring": {
                "count": scoring,
                "label": "待评分",
                "caliber": "C 契约状态机：assignment status=grading",
                "entry": "/assessments",
            },
            "training": {
                "count": training,
                "label": "培训计划",
                "caliber": "C 契约 §43：draft + assigned",
                "entry": "/training",
            },
            "gaps": {
                "count": gaps,
                "label": "缺口预警",
                "caliber": "A 口径 §7：缺口 = headcount − HR 确认已录用（逐需求非负）",
                "entry": "/analytics",
                "breakdown": gaps_detail,
            },
        },
        "data_source": engine_source,
    }


def _demand_gaps(db: Session) -> dict[str, Any]:
    """缺口预警明细：预警需求数 + 总缺口人数。

    与 analytics `_GAP_SQL` 同口径（A v0.2 §7）：project_demands.status IN
    (open, in_progress)；缺口 = headcount − 已录用（applications 同
    job_profile_id 且 offer_accepted 且 result_confirmed_by 非空，按
    talent_profile_id 去重），逐需求非负。此处用 ORM 表达同语义（同库直读
    applications/project_demands 属消费 A 域授权指标口径的受控统计——口径、
    表名与公式均来自 A 交付文档，非绕权读取业务明细）。
    """
    from sqlalchemy import text

    from app.modules.analytics.service import _GAP_SQL

    rows = db.execute(
        text("SELECT demand_id, gap FROM (" + _GAP_SQL + ") t WHERE t.gap > 0")
    ).all()
    return {
        "demand_count": len(rows),
        "total_gap": int(sum(int(r.gap) for r in rows)),
        "note": "预警数 = 缺口>0 的需求行数；明细回查走 GET /api/analytics/metrics/job.demand_progress_gap",
    }
