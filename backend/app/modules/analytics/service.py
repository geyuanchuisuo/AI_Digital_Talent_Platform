"""问数域服务（T-D01b 切片 + T-D08 registry 化）。

准入链（顺序即验收④「先过滤后聚合」的实现口径）：
1. **AccountScope 角色门**：metric.allowed_roles 之外的角色 403，先于一切查询；
2. **registry 准入**：`metric_registry` 表——未登记 422（白名单外）；
   `status=awaiting_caliber` 422（B/C 口径待交付，拒绝不猜测）；
3. **参数白名单**：params_json 之外的键 422、类型不合法 422；
4. 缓存（data 类键，TTL 取 registry 行）→ 受控统计（先 WHERE 过滤再聚合）→ 快照落库。

指标公式唯一依据：`docs/modules/A/指标口径交付.md`（T-A53 / v0.2 §7）与
`docs/modules/D/指标目录.md`。数据源选择沿用 T-D02 契约：副本优先、不可用降级
主库，结果带 `source` 不静默。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import settings
from app.core.auth.scope import DENY_MESSAGE, get_account_scope
from app.core.cache import get_cache
from app.core.errors import BizError
from app.database import SessionLocal
from app.modules.analytics.models import (
    METRIC_STATUS_ACTIVE,
    AnalyticsQuerySnapshot,
    MetricRegistry,
)
from app.modules.analytics.charts import build_chart
from app.modules.analytics.insight import build_interpretation_with_llm

METRIC_ACTIVE_JOBS = "jobs.active"
METRIC_DEMAND_GAP = "job.demand_progress_gap"
METRIC_THRESHOLD_REF = "match.threshold_reference"

_UNKNOWN_METRIC = "未登记的指标（白名单外不支持）"
_AWAITING_CALIBER = "指标口径待交付（B/C 域），暂不可查询"
_PARAM_NOT_ALLOWED = "参数不在指标允许范围"
_PARAM_INVALID = "参数类型非法"
_DIM_NOT_ALLOWED = "分组维度不在指标允许范围"

_replica_engine: Engine | None = None


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    name: str
    value: Any
    source: str  # replica | primary
    cache_hit: bool
    data_time: datetime
    snapshot_id: int | None = None
    table: dict | None = field(default=None)
    note: str | None = field(default=None)
    group_by: str | None = field(default=None)
    chart: dict | None = field(default=None)
    interpretation: dict | None = field(default=None)


def _replica_engine_lazy() -> Engine | None:
    """副本引擎懒建（进程内复用）；未配置返回 None。"""
    global _replica_engine
    if _replica_engine is None and settings.analytics_db_url:
        _replica_engine = create_engine(settings.analytics_db_url, pool_pre_ping=True)
    return _replica_engine


def compute_active_jobs(engine: Engine) -> int:
    """受控统计：活跃岗位数（A 口径 §1：is_active=1 且 title 最新 version）。"""
    sql = text(
        """
        SELECT COUNT(*) FROM job_profiles jp
        WHERE jp.is_active = 1
          AND jp.version = (
              SELECT MAX(j2.version) FROM job_profiles j2 WHERE j2.title = jp.title
          )
        """
    )
    with engine.connect() as conn:
        return int(conn.execute(sql).scalar_one())


def _resolve_engine() -> tuple[Engine, str]:
    replica = _replica_engine_lazy()
    if replica is not None:
        try:
            with replica.connect():
                return replica, "replica"
        except Exception:  # noqa: BLE001 - 副本不可用降级主库（T-D02 回退口径）
            pass
    from app.database import engine as primary_engine

    return primary_engine, "primary"


# --------------------------------------------------------------------------- #
# 受控统计函数（engine 注入；先过滤后聚合；结果含 value/table/note）
# --------------------------------------------------------------------------- #

_GAP_SQL = """
SELECT pd.id AS demand_id, pd.position_name, pd.headcount, pd.project_group_id,
       CASE WHEN pd.headcount - COALESCE((SELECT COUNT(DISTINCT a.talent_profile_id)
               FROM applications a
               WHERE a.job_profile_id = pd.job_profile_id
                 AND a.internal_stage = 'offer_accepted'
                 AND a.result_confirmed_by IS NOT NULL), 0) > 0
            THEN pd.headcount - COALESCE((SELECT COUNT(DISTINCT a.talent_profile_id)
               FROM applications a
               WHERE a.job_profile_id = pd.job_profile_id
                 AND a.internal_stage = 'offer_accepted'
                 AND a.result_confirmed_by IS NOT NULL), 0)
            ELSE 0 END AS gap
FROM project_demands pd
WHERE pd.status IN ('open', 'in_progress')
"""


def _stat_demand_gap(engine: Engine, params: dict, *, group_by: str | None = None) -> dict:
    """人才需求缺口（A v0.2 §7）：缺口 = headcount − HR 确认已录用（逐需求非负，求和）。

    group_by="project_group" → 按项目群聚合（LEFT JOIN project_groups 取名）。
    """
    if group_by == "project_group":
        sql = f"""
        SELECT COALESCE(pg.name, '未分组') AS project_group, SUM(g.gap) AS gap,
               SUM(g.headcount) AS headcount
        FROM ({_GAP_SQL}) g
        LEFT JOIN project_groups pg ON pg.id = g.project_group_id
        GROUP BY g.project_group_id, pg.name ORDER BY gap DESC
        """
        bind: dict = {}
        if params.get("project_group_id") is not None:
            sql = sql.replace("WHERE pd.status IN ('open', 'in_progress')",
                              "WHERE pd.status IN ('open', 'in_progress')"
                              " AND pd.project_group_id = :pgid")
            bind["pgid"] = params["project_group_id"]
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(sql), bind).mappings()]
        value = int(sum(r["gap"] for r in rows))
        table = {
            "columns": ["project_group", "headcount", "gap"],
            "rows": [
                {
                    "project_group": r["project_group"],
                    "headcount": int(r["headcount"]),
                    "gap": int(r["gap"]),
                }
                for r in rows
            ],
        }
        return {"value": value, "table": table, "group_by": group_by}

    sql = _GAP_SQL
    bind: dict = {}
    if params.get("project_group_id") is not None:
        sql += " AND pd.project_group_id = :pgid"
        bind["pgid"] = params["project_group_id"]
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(sql), bind).mappings()]
    value = int(sum(r["gap"] for r in rows))
    table = {
        "columns": ["demand_id", "position_name", "headcount", "gap"],
        "rows": [
            {
                "demand_id": r["demand_id"],
                "position_name": r["position_name"],
                "headcount": int(r["headcount"]),
                "gap": int(r["gap"]),
            }
            for r in rows
        ],
    }
    return {"value": value, "table": table}


def _stat_threshold_ref(engine: Engine, params: dict, *, group_by: str | None = None) -> dict:
    """匹配参考达线（指标目录 卡3）：score≥threshold 行数 / 有效评分行数。

    硬淘汰 score=0 排除；仅参考标签。零分母 → 暂无数据（验收③）。
    group_by="job" → 表本身即按岗位分组的 breakdown（同口径复用）。
    """
    sql = """
    SELECT jp.id AS job_id, jp.title, jp.pass_threshold AS threshold,
           COUNT(*) AS total,
           SUM(CASE WHEN mr.score >= jp.pass_threshold THEN 1 ELSE 0 END) AS reached
    FROM match_results mr
    JOIN match_batches mb ON mb.id = mr.match_batch_id
    JOIN job_profiles jp ON jp.id = mb.job_profile_id AND jp.version = mb.job_profile_version
    WHERE mr.score IS NOT NULL AND mr.score > 0
    """
    bind: dict = {}
    if params.get("job_profile_id") is not None:
        sql += " AND mb.job_profile_id = :jpid"
        bind["jpid"] = params["job_profile_id"]
    sql += " GROUP BY jp.id, jp.title, jp.pass_threshold ORDER BY jp.id"
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(sql), bind).mappings()]
    total = int(sum(r["total"] for r in rows))
    reached = int(sum(r["reached"] for r in rows))
    if total == 0:
        return {"value": None, "note": "暂无数据", "table": {"columns": [], "rows": []},
                "group_by": group_by}
    table = {
        "columns": ["job_id", "title", "threshold", "total", "reached", "ratio"],
        "rows": [
            {
                "job_id": r["job_id"],
                "title": r["title"],
                "threshold": float(r["threshold"]),
                "total": int(r["total"]),
                "reached": int(r["reached"]),
                "ratio": round(int(r["reached"]) / int(r["total"]), 4),
            }
            for r in rows
        ],
    }
    return {"value": round(reached / total, 4), "table": table, "group_by": group_by}


def _stat_active_jobs(engine: Engine, params: dict, *, group_by: str | None = None) -> dict:
    if group_by == "department":
        sql = """
        SELECT COALESCE(jp.department, '未指定') AS department, COUNT(*) AS active_jobs
        FROM job_profiles jp
        WHERE jp.is_active = 1
          AND jp.version = (
              SELECT MAX(j2.version) FROM job_profiles j2 WHERE j2.title = jp.title
          )
        GROUP BY jp.department ORDER BY active_jobs DESC
        """
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(sql)).mappings()]
        value = int(sum(r["active_jobs"] for r in rows))
        table = {
            "columns": ["department", "active_jobs"],
            "rows": [
                {"department": r["department"], "active_jobs": int(r["active_jobs"])}
                for r in rows
            ],
        }
        return {"value": value, "table": table, "group_by": group_by}
    return {"value": compute_active_jobs(engine), "group_by": None}


#: metric_id → 受控统计函数（registry.status=active 之外不在表内，注册即准入）。
#: 命名函数经模块属性调用，测试可 monkeypatch。
STAT_FUNCS: dict[str, Any] = {
    METRIC_ACTIVE_JOBS: _stat_active_jobs,
    METRIC_DEMAND_GAP: _stat_demand_gap,
    METRIC_THRESHOLD_REF: _stat_threshold_ref,
}

# --------------------------------------------------------------------------- #
# 多维分组（T-D10 ①）——metric_id → {dim_key: label}；breakdown 函数逐维度实现。
# 代码级注册（与 STAT_FUNCS 同模式）；registry 表为查询准入依据，维度为分析配置。
# --------------------------------------------------------------------------- #

DIMENSIONS: dict[str, dict[str, str]] = {
    METRIC_ACTIVE_JOBS: {"department": "按部门"},
    METRIC_DEMAND_GAP: {"project_group": "按项目群"},
    METRIC_THRESHOLD_REF: {"job": "按岗位"},
}

BREAKDOWN_FUNCS: dict[str, dict[str, Any]] = {
    METRIC_ACTIVE_JOBS: {"department": lambda engine, params: _stat_active_jobs(engine, params, group_by="department")},
    METRIC_DEMAND_GAP: {"project_group": lambda engine, params: _stat_demand_gap(engine, params, group_by="project_group")},
    METRIC_THRESHOLD_REF: {"job": lambda engine, params: _stat_threshold_ref(engine, params, group_by="job")},
}


# --------------------------------------------------------------------------- #
# registry 准入 + 通用查询
# --------------------------------------------------------------------------- #

def _get_registry_def(metric_id: str) -> dict | None:
    """读 registry 行（每次查询直查，表极小）；返回扁平 dict 或 None。"""
    with SessionLocal() as db:
        row = (
            db.query(MetricRegistry)
            .filter(MetricRegistry.metric_id == metric_id)
            .first()
        )
        if row is None:
            return None
        return {
            "metric_id": row.metric_id,
            "name": row.name,
            "domain": row.domain,
            "status": row.status,
            "allowed_roles": tuple(r.strip() for r in (row.allowed_roles or "").split(",") if r.strip()),
            "params": dict(row.params_json or {}),
            "cache_ttl": int(row.cache_ttl or 300),
        }


def list_registry() -> list[dict]:
    """指标目录全量（管理侧列表端点用）。"""
    with SessionLocal() as db:
        rows = db.query(MetricRegistry).order_by(MetricRegistry.domain, MetricRegistry.metric_id).all()
        return [
            {
                "metric_id": r.metric_id,
                "name": r.name,
                "domain": r.domain,
                "status": r.status,
                "definition_ref": r.definition_ref,
                "allowed_roles": r.allowed_roles,
                "cache_ttl": r.cache_ttl,
                "note": r.note,
            }
            for r in rows
        ]


def _validate_params(defn: dict, params: dict) -> dict:
    """参数白名单与类型校验（registry.params_json：键→'int'）。"""
    allowed = defn["params"]
    clean: dict = {}
    for k, v in params.items():
        if k not in allowed:
            raise BizError(422, _PARAM_NOT_ALLOWED)
        if v is None:
            continue
        if allowed[k] == "int":
            try:
                clean[k] = int(v)
            except (TypeError, ValueError):
                raise BizError(422, _PARAM_INVALID) from None
        else:  # 未知类型声明按原样透传
            clean[k] = v
    return clean


def _cache_key(metric_id: str, params: dict, group_by: str | None = None) -> str:
    base = f"{metric_id}:v1" if not params else f"{metric_id}:v1:{json.dumps(params, sort_keys=True)}"
    return base if group_by is None else f"{base}:by_{group_by}"


async def query_metric(
    user,
    metric_id: str,
    *,
    params: dict | None = None,
    group_by: str | None = None,
    bypass_cache: bool = False,
) -> MetricResult:
    """registry 准入的通用指标查询（403/422 先于任何统计）。

    group_by（T-D10 多维分组）须在 DIMENSIONS[metric_id] 白名单内；
    同一 metric+params+group_by 组合完全可复现（确定性管道 + 快照留痕，验收①）。
    """
    params = dict(params or {})
    scope = get_account_scope(user)

    defn = _get_registry_def(metric_id)
    if defn is None:
        raise BizError(422, _UNKNOWN_METRIC)
    if scope.role not in defn["allowed_roles"]:
        raise BizError(403, DENY_MESSAGE)
    if defn["status"] != METRIC_STATUS_ACTIVE:
        raise BizError(422, _AWAITING_CALIBER)
    if group_by is not None and group_by not in DIMENSIONS.get(metric_id, {}):
        raise BizError(422, _DIM_NOT_ALLOWED)
    clean_params = _validate_params(defn, params)

    stat_fn = STAT_FUNCS.get(metric_id)
    if stat_fn is None:
        raise BizError(422, _UNKNOWN_METRIC)

    cache = get_cache()
    cache_hit = False
    result: dict | None = None
    source = "primary"
    data_time = datetime.now()
    key = _cache_key(metric_id, clean_params, group_by)

    if not bypass_cache:
        cached = await cache.get("data", key)
        if isinstance(cached, dict) and "result" in cached:
            cache_hit = True
            result = cached["result"]
            source = cached.get("source", "primary")
            data_time = (
                datetime.fromisoformat(cached["data_time"]) if cached.get("data_time") else data_time
            )

    if not cache_hit:
        engine, source = await asyncio.to_thread(_resolve_engine)
        result = await asyncio.to_thread(
            stat_fn, engine, clean_params, group_by=group_by
        )
        result = dict(result)
        result["chart"] = build_chart(metric_id, defn["name"], result.get("value"),
                                      result.get("table"), result.get("note"),
                                      group_by=group_by)
        result["interpretation"] = build_interpretation_with_llm(
            metric_id, defn["name"], result.get("value"), result.get("table"),
            result.get("note"), group_by=group_by, data_time=datetime.now(),
        )
        data_time = datetime.now()
        await cache.set(
            "data",
            key,
            {"result": result, "source": source, "data_time": data_time.isoformat()},
            ttl=defn["cache_ttl"],
        )

    snapshot_id = await asyncio.to_thread(
        _write_snapshot, user, metric_id, clean_params, result or {}, source, cache_hit, data_time
    )
    return MetricResult(
        metric_id=metric_id,
        name=defn["name"],
        value=(result or {}).get("value"),
        source=source,
        cache_hit=cache_hit,
        data_time=data_time,
        snapshot_id=snapshot_id,
        table=(result or {}).get("table"),
        note=(result or {}).get("note"),
        group_by=group_by,
        chart=(result or {}).get("chart"),
        interpretation=(result or {}).get("interpretation"),
    )


def _write_snapshot(
    user, metric_id: str, params: dict, result: dict, source: str, cache_hit: bool, data_time: datetime
) -> int:
    """快照落库（始终写主库 MySQL；副本只读）。"""
    with SessionLocal() as db:
        row = AnalyticsQuerySnapshot(
            metric_id=metric_id,
            params_json=params or None,
            data_time=data_time,
            result_json=result,
            source=source,
            cache_hit=1 if cache_hit else 0,
            created_by=getattr(user, "id", None),
        )
        db.add(row)
        db.commit()
        return int(row.id)


# --------------------------------------------------------------------------- #
# 切片兼容入口（T-D01b 语义不变，内部已 registry 化）
# --------------------------------------------------------------------------- #

async def get_jobs_active(user, *, group_by: str | None = None, bypass_cache: bool = False) -> MetricResult:
    return await query_metric(user, METRIC_ACTIVE_JOBS, group_by=group_by, bypass_cache=bypass_cache)
