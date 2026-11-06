"""模型层枚举字面值白名单（T-03）。

`docs/数据库设计.md` v1.2 §1：枚举用 VARCHAR(20) + **应用层枚举校验**，取值写死为文档约定。
学历字面值以《算法与判定规则》v1.2 §1 为唯一口径——注意是「高中中专」，不是「高中/中专」。

这些常量只做「合法取值集合」，不实现归一化/排序/判定（那是算法层 T-16/T-23 的职责，
见《算法与判定规则》§1/§2，T-03 不抢做）。
"""

from __future__ import annotations

# 学历：入库、job_profiles.min_degree 校验、GET /talents?degree= 精确字符串比较共用
DEGREE_VALUES: tuple[str, ...] = ("博士", "硕士", "本科", "大专", "高中中专", "其他")
DEGREES = frozenset(DEGREE_VALUES)

# parse_tasks.status（§4.2，含 cancelled）；stage 为断点续跑起点
PARSE_TASK_STATUSES: tuple[str, ...] = (
    "pending", "running", "success", "failed", "duplicate_pending", "cancelled",
)
PARSE_STAGES: tuple[str, ...] = ("extract", "profile", "vector")

# talent_profiles.vector_status（§5.1 三态）
VECTOR_STATUSES: tuple[str, ...] = ("pending", "synced", "failed")

# 批次状态（§6.2 / §6.4）
BATCH_STATUSES: tuple[str, ...] = ("pending", "running", "done", "failed")

# screen_results.verdict / manual_verdict（§6.3）
VERDICTS: tuple[str, ...] = ("passed", "pending", "rejected")

# 查重 reason（§5.3，hash_match 已删除）
DUPLICATE_REASONS: tuple[str, ...] = ("phone_match", "email_match", "name_phone_match")
DUPLICATE_STATUSES: tuple[str, ...] = ("pending", "merged", "kept")

# 标签与画像来源
TAG_TYPES: tuple[str, ...] = ("skill", "industry", "level", "general")
TAG_SOURCES: tuple[str, ...] = ("llm", "manual")
PROFILE_SOURCES: tuple[str, ...] = ("resume", "manual")

# sys_settings.value_type（§3.2）
SETTING_VALUE_TYPES: tuple[str, ...] = ("int", "bool", "str", "json")

# scope 类型（§6.2 / §6.4 附录 B）；成员快照 resolved_ids 在创建时追加
SCREEN_SCOPE_TYPES: tuple[str, ...] = ("all_unscreened", "upload_batch", "resume_file_ids", "all")
MATCH_SCOPE_TYPES: tuple[str, ...] = ("all", "filter", "upload_batch")

# 一期唯一可配项，合法范围 1–5（越界 400 由接口层校验）
SETTING_TASK_CONCURRENCY = "task_concurrency"
TASK_CONCURRENCY_MIN = 1
TASK_CONCURRENCY_MAX = 5
