# D PgSQL 只读分析副本与 ETL 契约（T-D02，v1.0）

> 版本：v1.0　|　日期：2026-11-07　|　维护：D　|　上游：统一需求 V2.6 §1（PostgreSQL 强制栈）、DEC-20261107-DLINE-CONFIRMED（一期定时 ETL 批同步、二期按需 CDC）
> 实现：`deploy/postgres/docker-compose.yml` + `scripts/D/etl_pgsql.py`；测试：`tests/D/test_etl_pgsql.py`（真实 MySQL→PG 集成）

## 1. 组件与配置

| 项 | 值 |
|---|---|
| PG 容器 | `aitalent-app-pg-analytics`（postgres:16-alpine），端口 `${ANALYTICS_PG_PORT:-5433}`，库 `${ANALYTICS_PG_DB:-ai_talent_analytics}` |
| 消费端配置 | `config.py: analytics_db_url`（**空 = 副本未启用** → 自由问数/报表降级主库 MySQL 直查，T-D08 落实；ETL 脚本本身不静默降级，直接退出） |
| ETL 入口 | `python scripts/D/etl_pgsql.py --full / --incremental / --verify [--tables a,b]` |

## 2. 同步范围（列级 allowlist，按指标目录圈定）

| 表 | 用途 | 增量列 | 排除的敏感/大字段 |
|---|---|---|---|
| `project_groups` / `projects` | 维度过滤 | updated_at | — |
| `project_demands` | 卡片 1 缺口/需求 | updated_at | — |
| `applications` | 卡片 1 人工推进 | updated_at | `stage_reason`（敏感原因文本）、`*_by` |
| `job_profiles` | 卡片 3 阈值 | updated_at | `jd_text`/`requirements_text`（JD 全文）、`*_json` 配置 |
| `screen_results` | 卡片 3 达线 | created_at | `reason_summary`/`hits_json`/`misses_json`/`profile_input_snapshot_json`（画像快照） |
| `match_results` | 卡片 3 | created_at | `reason`/`dimension_scores_json`/`hits`/`misses` |
| `parse_tasks` | 卡片 2 解析成败 | updated_at | `error`（失败原因分组待 B 契约后评估） |

**整体不进副本**：`resume_files`、`parsed_resumes`（简历原文/解析内容）、`talent_profiles`（人才主档 PII）——问数一律经 person_id 级授权的统计口径，不搬原始主档。

## 3. 同步机制

- **类型映射**：从 MySQL `information_schema` 自动映射（bigint→BIGINT、datetime→TIMESTAMP、json→JSONB、decimal(m,n)→NUMERIC 等），不手抄 DDL；规格列在源库缺失即报错退出（schema 漂移防护）。
- **全量**：`TRUNCATE` + 分块重灌（500/批）。
- **增量**：水位 = 目标表 `MAX(incr_col)`，无状态；`ON CONFLICT (id) DO NOTHING` 幂等，重复跑不产生重复行。
- **核对**：`--verify` 输出逐表 source/target 行数、是否一致、延迟秒数；延迟目标 ≤15 分钟（验收②），实际秒级。

## 4. 调度（一期）

手动/定时脚本均可（`--incremental` 幂等）；接入调度管理页（T-D20 任务调度）时由 D 提供命令行，定时默认关闭+管理员启用（V2.6 口径）。二期按需 CDC 另行评审。

## 5. 验证与边界

- 集成测试 4 例（真实 MySQL 3307 → 真实 PG 5433）：敏感排除断言、全量行数一致、增量拾取新行+幂等、延迟达标；依赖不可达自动 skip。
- **未验证项**：定时调度端到端（属 T-D20）；CDC（二期）；副本被查询端消费（T-D08/T-D09）。
