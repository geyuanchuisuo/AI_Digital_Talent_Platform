"""L1 单元测试 · 招聘域五阶段映射与枚举（M3-V2.6 功能 8a-1）。

纯逻辑，不连库：EXTERNAL_STAGE_MAP 的完整性与保守回退、终态/结果类别语义、
五表注册。状态机接口测试在集成层（T-A60c）。
"""

from __future__ import annotations

import app.models
from app.models import TABLE_NAMES, Base
from app.modules.recruitment.models import (
    EXTERNAL_STAGE_MAP,
    EXTERNAL_STAGES,
    INTERNAL_STAGES,
    RESULT_CATEGORIES,
    TERMINAL_STAGES,
    external_stage_of,
)


def test_external_map_covers_all_internal_stages():
    assert set(EXTERNAL_STAGE_MAP) == set(INTERNAL_STAGES)
    assert set(EXTERNAL_STAGE_MAP.values()) <= set(EXTERNAL_STAGES)


def test_five_stage_mapping_matches_confirmed_baseline():
    """V2.6 F01 已确认映射逐条核对。"""
    assert external_stage_of("new") == "submitted"
    assert external_stage_of("screening") == "screening"
    assert external_stage_of("matched") == "screening"
    assert external_stage_of("invited") == "interviewing"
    assert external_stage_of("interviewing") == "interviewing"
    assert external_stage_of("deciding") == "deciding"
    for terminal in TERMINAL_STAGES:
        assert external_stage_of(terminal) == "closed"


def test_unknown_stage_falls_back_to_submitted():
    """未知内部阶段保守映射为「已提交」，不外露后续进展。"""
    assert external_stage_of("nonexistent") == "submitted"


def test_terminal_semantics_result_categories():
    """终态四枚举全部映射到结果类别二值（录用仅 offer_accepted）。"""
    assert set(RESULT_CATEGORIES) == {"hired", "not_hired"}
    assert set(TERMINAL_STAGES) == {"offer_accepted", "rejected", "withdrawn", "job_closed"}
    # 录用只来自 offer_accepted；其余终态为未录用（service 层写入时判定）


def test_five_recruitment_tables_registered():
    """五张招聘表在 `TABLE_NAMES` 与实际 ORM 元数据中双注册。

    一期表数会随 B/C/D 各自新增而增长（76 → 84，D 的 T-D19~D21 新增 8 表），
    故不再硬编码总数（2026-11-10 并发更新时曾因此产生 1 例基线失败）。
    改为「清单无重复 + 与 ORM 元数据集合完全一致」，既能发现漏注册、
    也能发现重复注册，比固定数字更严格；具体表数看 `app/models/__init__.py`。
    """
    for t in ("project_groups", "projects", "project_demands", "jd_publications", "applications"):
        assert t in TABLE_NAMES and t in Base.metadata.tables
    assert len(TABLE_NAMES) == len(set(TABLE_NAMES)), "TABLE_NAMES 存在重复表"
    assert set(TABLE_NAMES) == set(Base.metadata.tables), "注册表与实际 ORM 元数据不一致"
