# 交付说明

- **交付日期**：2026-11-06
- **分支**：`hhy_gycs_master`（独立根提交，不基于仓库既有的 `master` / `Lon` / `master_YB` 历史）
- **来源**：本地 `main/` 全量源码、设计与活文档（`开发日志.md` v2.31 为当前权威口径）
- **交付内容**：后端 / 前端 / 部署编排 / 全部文档 / 评测集与评测报告

## 已包含

| 类别 | 路径 |
|---|---|
| 后端 | `backend/app/`、`backend/tests/`、`backend/scripts/`、`backend/requirements.txt`、`backend/.env.example` |
| 前端 | `frontend/src/`、`frontend/package.json`、`frontend/.env.local.example`、配置与单测 |
| 部署 | `deploy/milvus/docker-compose.yml` |
| 文档 | `README.md`、`需求文档.md`、`业务逻辑说明.md`、`开发日志.md`、`docs/` 12 份（接口 / 架构 / 数据库 / 算法 / 运行配置 / 提示词 / 错误码 / todolist / 测试报告 / 安全走查等） |
| 评测资产 | `backend/data/eval/resumes/` **22 份 .docx**（已修日期口径的正式评测集）、`eval_parse_report.json`、`eval_match_report.json`、`perf_t31_report.json`、`match_eval.json`、`resume_parse_eval.json` |

## 有意排除（本地仍保留，未删除）

| 项 | 原因 |
|---|---|
| `backend/.env` | 含 MySQL 密码、LLM / Embedding API Key、JWT_SECRET。请按 `backend/.env.example` 自行配置 |
| `frontend/.env.local` | 本机浏览器 API 地址（源目录本就只有模板、无实体文件）；按 `frontend/.env.local.example` 复制即可，跨机访问需改 `NEXT_PUBLIC_API_BASE` |
| `backend/.venv`、`frontend/node_modules`、`frontend/.next` | 依赖与构建产物，可重建（`requirements.txt` / `npm ci` / `npm run build`） |
| `backend/data/uploads/` | 系统上传落盘的 uuid 文件，含人工测试残留 |
| `backend/data/eval/resumes_original_20261106/` | 修日期**之前**的原件备份，推上去会造成评测口径混淆 |
| `backend/backups/*.sql` | 数据库备份（b6），含运行数据 |
| `.pytest_tmp/`、日志、`.idea/`、`.workbuddy/` | 临时与工具侧产物 |

## 部署与运行

按 `README.md`「首次部署」顺序：Milvus（`deploy/milvus/docker-compose.yml`）→ 后端 venv + `init_db.py` + `check_env.py` → 前端 `npm ci` / `npm run build`。

- 后端必须 `--workers 1`（多进程会重复扫描 TaskRunner 任务）。
- 登录账号由 `init_db.py` 生成，见 `README.md`。
- 三份公共设计文档（`docs/LLM提示词设计.md`、`docs/运行配置.md`、`docs/统一错误码与鉴权.md`）是实现口径的唯一依据，改动前须登记开发日志 Layer 4。

### ⚠️ `.env` 不能只复制模板再填 3 个 Key

`backend/.env.example` 中 `LLM_BASE_URL` / `LLM_MODEL` / `EMBEDDING_BASE_URL` 是**占位示例**（DeepSeek 地址），照抄后只填 API Key 会导致 Embedding 调用失败、向量同步降级。请按项目实配口径改（决策 D-L：**双供应商混配**，均为 OpenAI 兼容协议，勿改回同一供应商）：

| 键 | 值 |
| --- | --- |
| `LLM_BASE_URL` | `https://maas-api.cn-huabei-1.xf-yun.com/v2` |
| `LLM_MODEL` | `spark-x2.6` |
| `EMBEDDING_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `EMBEDDING_MODEL` | `text-embedding-v3`（`EMBEDDING_DIM=1024`，必须与 Milvus collection 维度一致） |

另需自备：MySQL 8.x（所用账号需有 `CREATE DATABASE` 权限）、Docker Desktop + Milvus（`cd deploy/milvus && docker compose up -d`）、讯飞与百炼两家有效 API Key、随机 `JWT_SECRET`。配完先跑 `scripts/check_env.py`（`--skip-llm` 可先跳过外呼），确认无 FAIL 再起服务。

### 排除项不影响启动

`scripts/init_db.py` 幂等建库建表并生成 `hr/hr123456` + 示例岗位 + `sys_settings`；`app/main.py` 的 lifespan 自动 `ensure_collection()` 创建 Milvus collection；`upload_service.py` 自动 `mkdir` 上传目录。因此 SQL 备份、`data/uploads/`、旧版简历备份、`tsconfig.tsbuildinfo`、venv / node_modules / `.next` 均**不是运行前置条件**。首次部署数据库为空（无历史画像与向量），上传简历后才会产生数据；如需恢复既有画像，须另行提供数据库与 Milvus 数据。

## 关键结论速览

- B0–B6 全部完成，`docs/测试报告.md` v1.0：13 条功能验收全过。
- T-30 解析加权 0.9749 / 匹配一致率 1.0；T-31 性能数据见 `perf_t31_report.json`。
- 已知约束：H-15 语义检索冷 query 按 D-10 口径（剔除 embedding 网络耗时）达标；H-16 前端批量上传 422 已修复。
