# B 模块新表模型设计稿（9 表 + 1 列）

> 状态：**设计稿**，待 B 确认 + V2.5 冻结后编码。  
> 依据：tasks/B.md（T-B06/T-B07/T-B08/T-B12/T-B18、B-01~B-03）、A 开工指引、`backend/app/models/base.py` 公共 mixin。  
> 迁移挂载点：`down_revision = "A_0004_merge_heads"`，命名 `B_0001_*`，**纯 ASCII**。

## 通用约定（对齐现有代码）

- 主键：`PKMixin` → `BIGINT UNSIGNED AUTO_INCREMENT`
- 时间戳：`CreatedAtMixin`（仅 created_at）或 `UpdatedAtMixin`（created_at + updated_at）
- JSON：`JSONDict`（dict）/ `JSONList`（list），Mutable 包裹
- 表参数：`MYSQL_TABLE_OPTIONS`（InnoDB + utf8mb4）
- 字符串默认值：`str_default(...)`；数值默认值：`num_default(...)`
- 外键**不建物理 FK 约束**（与现有 13 表一致，靠应用层保证）

---

## 一、档案质量域（4 表）

### 1. `talent_profile_versions` — 档案版本快照（T-B06）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| talent_profile_id | BIGINT UNSIGNED | NOT NULL, idx | 关联 talent_profiles.id（= person_id） |
| version | INT UNSIGNED | NOT NULL | 单调递增版本号 |
| change_type | VARCHAR(20) | NOT NULL, default 'reparse' | reparse / manual_edit / merge / restore |
| profile_snapshot_json | JSON | NOT NULL | 该版本完整画像快照（结构化 JSON） |
| changed_by | BIGINT UNSIGNED | NULL | 操作人 user_id（系统触发为 NULL） |
| created_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP | |

**索引/约束：**
- `uk_profile_version` UNIQUE (talent_profile_id, version)
- `idx_tpv_profile` (talent_profile_id)

**模型类：** `TalentProfileVersion(PKMixin, CreatedAtMixin, Base)`

---

### 2. `talent_field_evidence` — 字段证据溯源（T-B07）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| talent_profile_id | BIGINT UNSIGNED | NOT NULL, idx | 关联 talent_profiles.id |
| profile_version | INT UNSIGNED | NOT NULL | 对应 talent_profile_versions.version |
| field_name | VARCHAR(50) | NOT NULL, idx | 字段名（如 name/phone/skills） |
| source_page | INT UNSIGNED | NULL | 来源页码（OCR 场景） |
| source_text | TEXT | NULL | 来源文本片段 |
| confidence | DECIMAL(4,3) | NULL | 置信度 0.000~1.000 |
| evidence_status | VARCHAR(20) | NOT NULL, default 'unavailable' | available / unavailable / processing / failed |
| created_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP | |

**索引/约束：**
- `uk_evidence_field_version` UNIQUE (talent_profile_id, profile_version, field_name)
- `idx_tfe_profile` (talent_profile_id, field_name)

**模型类：** `TalentFieldEvidence(PKMixin, CreatedAtMixin, Base)`

---

### 3. `talent_profile_edits` — 人工修改记录（T-B08）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| talent_profile_id | BIGINT UNSIGNED | NOT NULL, idx | |
| profile_version | INT UNSIGNED | NOT NULL | 修改前版本号 |
| field_name | VARCHAR(50) | NOT NULL | |
| old_value | TEXT | NULL | 修改前值（序列化为字符串） |
| new_value | TEXT | NULL | 修改后值 |
| edited_by | BIGINT UNSIGNED | NOT NULL | 操作人 user_id |
| edit_reason | VARCHAR(255) | NULL | 修改原因 |
| created_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP | |

**索引/约束：**
- `idx_tpe_profile` (talent_profile_id, created_at)

**模型类：** `TalentProfileEdit(PKMixin, CreatedAtMixin, Base)`

---

### 4. `parse_attempts` — 解析重试记录（T-B12）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| parse_task_id | BIGINT UNSIGNED | NOT NULL, idx | 关联 parse_tasks.id |
| attempt_no | INT UNSIGNED | NOT NULL | 第几次尝试（从 1 开始） |
| status | VARCHAR(20) | NOT NULL, default 'processing' | success / failed / cancelled / processing |
| stage | VARCHAR(20) | NULL | 失败阶段：ocr / llm / persist |
| error_message | TEXT | NULL | |
| started_at | DATETIME | NULL | |
| finished_at | DATETIME | NULL | |

**索引/约束：**
- `uk_attempt_task_no` UNIQUE (parse_task_id, attempt_no)
- `idx_pa_task` (parse_task_id)

**模型类：** `ParseAttempt(PKMixin, Base)`（无 created_at，用 started_at）

---

## 二、简历域扩展（1 表 + 1 列）

### 5. `resume_pages` — PDF 逐页 OCR 结果

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| resume_file_id | BIGINT UNSIGNED | NOT NULL, idx | 关联 resume_files.id |
| page_no | INT UNSIGNED | NOT NULL | 页码（从 1 开始） |
| text_content | MEDIUMTEXT | NOT NULL | 该页 OCR 文本 |
| ocr_engine | VARCHAR(50) | NULL | rapidocr / pymupdf 等 |
| confidence | DECIMAL(4,3) | NULL | 整页置信度 |
| created_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP | |

**索引/约束：**
- `uk_page_file_no` UNIQUE (resume_file_id, page_no)

**模型类：** `ResumePage(PKMixin, CreatedAtMixin, Base)`

### 6. `resume_files` 新增列 — 上传意图

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| job_profile_id | BIGINT UNSIGNED | NULL, idx | 上传时关联的岗位画像（A 的 job_profiles.id），用于定向解析 |

---

## 三、知识与问答域（3 表，B-01~B-03）

### 7. `knowledge_documents` — 知识库文档（B-01）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| title | VARCHAR(255) | NOT NULL | |
| category | VARCHAR(50) | NOT NULL, default 'general', idx | 制度/流程/FAQ 等 |
| source_type | VARCHAR(20) | NOT NULL, default 'upload' | upload / link / manual |
| file_path | VARCHAR(500) | NULL | 源文件存储路径 |
| content_hash | CHAR(64) | NULL, idx | 内容哈希，用于去重 |
| version | INT UNSIGNED | NOT NULL, default 1 | 文档版本 |
| status | VARCHAR(20) | NOT NULL, default 'active' | active / archived |
| uploaded_by | BIGINT UNSIGNED | NOT NULL | |
| created_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP | |
| updated_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP ON UPDATE | |

**索引/约束：**
- `idx_kd_category` (category)
- `idx_kd_hash` (content_hash)

**模型类：** `KnowledgeDocument(PKMixin, UpdatedAtMixin, Base)`

### 8. `knowledge_chunks` — 知识片段（B-02，Milvus 向量化）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| knowledge_document_id | BIGINT UNSIGNED | NOT NULL, idx | |
| chunk_no | INT UNSIGNED | NOT NULL | 文档内片段序号 |
| content | TEXT | NOT NULL | 片段文本 |
| vector_id | VARCHAR(100) | NULL | Milvus 中的向量 ID |
| vector_status | VARCHAR(20) | NOT NULL, default 'pending' | pending / synced / failed |
| token_count | INT UNSIGNED | NULL | |
| created_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP | |

**索引/约束：**
- `uk_chunk_doc_no` UNIQUE (knowledge_document_id, chunk_no)
- `idx_kc_vector_status` (vector_status)

**模型类：** `KnowledgeChunk(PKMixin, CreatedAtMixin, Base)`

### 9. `qa_records` — 问答记录（B-03）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | BIGINT UNSIGNED | PK, AI | |
| question | TEXT | NOT NULL | 用户问题 |
| answer | MEDIUMTEXT | NULL | 回答内容（流式完成后回填） |
| cited_chunks_json | JSON | NULL | 引用的 knowledge_chunks.id 列表 |
| model_version | VARCHAR(50) | NULL | 对齐 A 的快照口径 |
| prompt_version | VARCHAR(20) | NULL | |
| asked_by | BIGINT UNSIGNED | NOT NULL, idx | |
| status | VARCHAR(20) | NOT NULL, default 'processing' | processing / completed / failed |
| created_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP | |
| updated_at | DATETIME | NOT NULL, default CURRENT_TIMESTAMP ON UPDATE | |

**索引/约束：**
- `idx_qa_asked_by` (asked_by, created_at)

**模型类：** `QaRecord(PKMixin, UpdatedAtMixin, Base)`

---

## 四、迁移设计（B_0001）

**文件名：** `backend/migrations/versions/B_0001_b_module_9_tables.py`（纯 ASCII）

```python
"""B module 9 tables + resume_files.job_profile_id (T-B06/07/08/12, B-01/02/03).

Revision ID: B_0001_b_module
Revises: A_0004_merge_heads
Create Date: 2026-11-07

Creates 8 new B tables and adds job_profile_id to resume_files.
Idempotent: inspector checks for tables/columns before creating.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.engine.reflection import Inspector

revision = "B_0001_b_module"
down_revision = "A_0004_merge_heads"
branch_labels = None
depends_on = None


def _table_exists(insp: Inspector, name: str) -> bool:
    return name in insp.get_table_names()


def _column_exists(insp: Inspector, table: str, col: str) -> bool:
    return col in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)

    # --- resume_files.job_profile_id ---
    if not _column_exists(insp, "resume_files", "job_profile_id"):
        op.add_column(
            "resume_files",
            sa.Column("job_profile_id", mysql.BIGINT(unsigned=True), nullable=True),
        )
        op.create_index("idx_rf_job_profile", "resume_files", ["job_profile_id"])

    # --- talent_profile_versions ---
    if not _table_exists(insp, "talent_profile_versions"):
        op.create_table(
            "talent_profile_versions",
            sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),
            sa.Column("talent_profile_id", mysql.BIGINT(unsigned=True), nullable=False),
            sa.Column("version", mysql.INTEGER(unsigned=True), nullable=False),
            sa.Column("change_type", sa.String(20), nullable=False, server_default="reparse"),
            sa.Column("profile_snapshot_json", mysql.JSON, nullable=False),
            sa.Column("changed_by", mysql.BIGINT(unsigned=True), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
        )
        op.create_unique_constraint("uk_profile_version", "talent_profile_versions", ["talent_profile_id", "version"])
        op.create_index("idx_tpv_profile", "talent_profile_versions", ["talent_profile_id"])

    # --- talent_field_evidence, talent_profile_edits, parse_attempts, ---
    # --- resume_pages, knowledge_documents, knowledge_chunks, qa_records ---
    # (同 pattern，略；完整见迁移文件)
    ...


def downgrade() -> None:
    bind = op.get_bind()
    insp = Inspector.from_engine(bind)
    for t in [
        "talent_profile_versions", "talent_field_evidence", "talent_profile_edits",
        "parse_attempts", "resume_pages", "knowledge_documents",
        "knowledge_chunks", "qa_records",
    ]:
        if _table_exists(insp, t):
            op.drop_table(t)
    if _column_exists(insp, "resume_files", "job_profile_id"):
        op.drop_index("idx_rf_job_profile", "resume_files")
        op.drop_column("resume_files", "job_profile_id")
```

> ⚠️ 完整迁移需展开全部 8 张表的 create_table，上面是结构骨架。编码时每个表严格对齐本设计稿的字段/索引/约束。

---

## 五、模型注册（由 A 执行）

B 提交模型代码后，通知 A 在 `backend/app/models/__init__.py`：
1. import 9 个新模型类
2. 加入 `MODEL_CLASSES`
3. 断言 35 → **44**
4. A 同步扩展 `test_models_structure.py` 的 `EXPECTED_COLUMNS`

## 六、与现有表的关系

```
talent_profiles (person_id)
  ├── talent_profile_versions (1:N)
  ├── talent_field_evidence (1:N)
  └── talent_profile_edits (1:N)

parse_tasks
  └── parse_attempts (1:N)

resume_files
  └── resume_pages (1:N)

knowledge_documents
  └── knowledge_chunks (1:N)

qa_records → cited_chunks_json → knowledge_chunks.id (逻辑引用)
```

## 七、待确认项

1. `talent_profile_versions.profile_snapshot_json` 存全量画像还是仅差异？（建议全量，便于回滚）
2. `knowledge_chunks.vector_id` 用 Milvus 自增 ID 还是业务生成 ULID？
3. `qa_records.answer` 是否需要流式分段存储（vs 仅存最终结果）？
4. `resume_files.job_profile_id` 是否需要唯一约束（一个文件只能对应一个岗位）？
