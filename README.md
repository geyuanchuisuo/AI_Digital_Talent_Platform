# AI 数字化人才平台

四人基于现有招聘子系统继续建设人才平台。现行一期实施边界仍按根 [AGENTS.md](AGENTS.md) 与原 [统一需求 V2.2](docs/需求文档.md) 的既定范围：F01–F09、人工面试、固定预置流程及白名单指标问数；AI 面试与可编辑工作流原列二期，跨模块自由查询为二期候选。[统一需求 V2.3（审阅稿）](docs/需求文档.md) 已对照细化需求，后续用户确认但尚未同步根规则的事项在 §15 明确列出，不据审阅稿直接改动实施范围。

## 当前真实状态

2026-11-06 按用户授权导入 `hhy_gycs_master` ZIP：201 个代码/配置文件原样落入 `backend/`、`frontend/`、`deploy/`；18 份历史说明与 27 份合成评测资产单独存入 [来源参考区](docs/imports/hhy_gycs_master/README.md)。逐文件去向、哈希和维护人见 [导入清单](.collaboration/imports/hhy_gycs_master.json)。未覆盖现有根文档及七份旧需求来源。

已有代码覆盖 HR 登录、PDF/DOCX 批量上传、解析任务、人才画像、查重、人工修正保护、岗位条件、筛选匹配与人工改判。当前未在本机安装业务依赖、初始化数据库、调用模型或浏览器验收；源码存在不等于功能已验证。

OCR、个人端权限、自适应测评、人工面试、培训、知识/档案问答、指标报告和正式固定流程仍待续建。现有硬淘汰/加权向量评分、正式评分输入缺经历证据、档案版本及审计缺口见 [接入与需求决策](docs/源码接入与需求决策.md)。来源的 588 后端测试与模型指标是历史记录，不作为本次通过声明。

## 四人从哪里继续

- [四人分工与试行方案](docs/四人分工与试行方案.md)：A 基础平台/招聘匹配，B 文件档案/知识，C 测评/人工面试/培训，D 模型任务/指标报告/固定流程。
- [技术框架](docs/技术框架.md)：沿用 Next.js、FastAPI、MySQL，复用已导入服务；现有 TaskRunner 要求单 API 进程。
- [模块归属](docs/模块归属.md) 与 [.collaboration/ownership.json](.collaboration/ownership.json)：既有扁平分层目录按具体文件分配，screen 页面也是混合归属。不能按一个目录推断全归某人。
- [A](tasks/A.md) / [B](tasks/B.md) / [C](tasks/C.md) / [D](tasks/D.md)：继承代码、下一交付点、依赖和验收。
- [新增需求原文 S08](docs/requirements/sources/S08-项目需求细化v1-20261106.md)及[细化需求草稿 S09](docs/AI数字化人才平台-项目需求细化v1.md)：作为对照输入，V2.3 §15 记录条目映射、后续确认与未同步的冲突。九宫格、课程审批候补、验证码、多岗位门户、双语/通知和离线容器不能仅凭来源表述自动认定已实现；其中部分事项的用户后续确认已见 D 协作请求，仍须同步公共规则与分工。

## Git 拉取后先做这些

先读 [AGENTS.md](AGENTS.md)、[更新记录](更新记录.md)、[协作操作指南](docs/协作操作指南.md) 和 [模块归属](docs/模块归属.md)。确认本人 A/B/C/D 角色，再读对应 [A](tasks/A.md) / [B](tasks/B.md) / [C](tasks/C.md) / [D](tasks/D.md) 任务卡及最新交接。

需求与协作基线已于 2026-11-06 发布到 `origin/master`（`5c3df08`）。任何成员与 Agent 都不得在 `master` 开发或创建开发提交；它仅用于经明确授权的集成与发布。每个人在自己的个人分支开发；`master_YB` 只是本次用户的个人分支，其他成员继续使用各自的分支。

Agent 不得擅自推送或合并到 `master`。必须取得用户针对具体变更和 `master` 目标的明确授权；开发、修复、本地提交或推送个人分支不代表获准集成，历史授权不自动延伸到新的改动。未获授权时，成果保留在本人分支并报告待集成。

Git 不会自动安装 hooks：每人在自己的 clone 确认角色后运行 `python scripts/setup_collaboration.py --actor B --branch <本人分支>`（B 换成本人角色，分支换成本人已有分支）。个人分支登记仅在本人 clone 生效，不会要求其他成员切换分支。新任务也可使用 `feature/B/<任务>` 等角色分支。分支名不代表角色，不要在角色未知时运行或猜测身份。规则与脚本的新变更提交到远端后，其他人 pull 才会取得。

已提供提交前路径检查、独立更新记录及跨模块请求模板；它们减少误改，不是不可绕过的权限系统。共享文件由指定角色集成，远端保护与评审另由仓库负责人配置。业务尚未实现，协作工具的通过不等于产品测试通过。

## 一键交付（推荐）：clone 后一条命令自动建好环境

客户只需预装 **Python 3.11+ / Node.js 20+ / Docker Desktop**（系统级运行时，仓库无法代装），然后：

| 系统 | 操作 |
|---|---|
| Windows | 双击 `scripts/A/bootstrap.cmd`，或在仓库根执行 `python scripts/A/bootstrap.py --start` |
| Linux / macOS | `bash scripts/A/bootstrap.sh` |

只建环境不启动服务：`python scripts/A/bootstrap.py`；复用客户已有 MySQL/Milvus：`python scripts/A/bootstrap.py --no-docker`。

脚本自动完成，**幂等**（重跑不会覆盖客户已填的配置或已有数据）：

1. 生成 `backend/.env`（随机 `JWT_SECRET`、MySQL 密码、真实供应商默认地址）与 `frontend/.env.local`（API 基址）
2. 检测本机 3306 / 19530，缺哪个就用 `deploy/milvus/docker-compose.yml` 自动起 MySQL 8 或 Milvus 2.6（含 etcd、minio），数据落在命名卷
3. 创建 `backend/.venv` 安装 `requirements.txt`；`frontend` 执行 `npm ci`（按 `package-lock.json` 锁版本）
4. `init_db.py` 幂等建库建表 + seed 账号 `hr/hr123456` 与示例岗位（MySQL 启动慢时自动重试 5 次）
5. `check_env.py` 自检；Milvus collection 由后端启动时 `lifespan` 自动创建
6. `--start` 启动 `uvicorn --workers 1`（8000）与 `next dev`（3000），Ctrl+C 停止

**唯一需要客户手填的**：仓库根 `.env` 的 `LLM_API_KEY` / `EMBEDDING_API_KEY`（首次运行自动从 `.env.example` 生成）。外部模型凭据不能写进 Git；不填也能把环境完整建好，只是简历解析、AI 评分、向量检索不可用，脚本会明确提示。默认供应商沿用历史实配：讯飞 Spark `spark-x2.6` + 阿里百炼 `text-embedding-v3`（1024 维），均为 OpenAI 兼容协议；Agnes 目标待 D 验证后，只需在根 `.env` 覆盖三行即可切换。

访问：前端 http://localhost:3000，健康检查 http://localhost:8000/api/health，登录 `hr/hr123456`（**生产环境必须立即修改**）。`docker compose -f deploy/milvus/docker-compose.yml down` 保留数据卷，`down -v` 才会清数据。

> 实现细节、边界与验证记录见 [交付环境封装](docs/modules/A/交付环境封装.md)。下面「独立环境准备」是手工步骤，供了解细节或排障用，交付场景不需要逐步执行。
## 独立环境准备与运行入口

下面命令根据导入源码整理，**尚未在本机执行验证**。每人使用独立数据库、文件目录与本地配置；不要复用其他项目的运行数据。Python、Node、MySQL 及 pymilvus/Milvus 的具体版本按源码依赖与来源兼容记录先验证，再锁定；环境差异记录在个人交接。

1. 阅读 `backend/requirements.txt`、`frontend/package.json` 与 `deploy/milvus/docker-compose.yml`。源码前端是 Next.js，不再按旧 Vite 建议另起项目。
2. 创建本项目 Python 虚拟环境并安装后端依赖；前端使用 `npm ci` 按锁文件安装。依赖不包含在此次源码导入内。
3. 手工方式：将 `backend/.env.example` 复制为本地 `backend/.env`，将 `frontend/.env.local.example` 复制为 `frontend/.env.local`。填独立 MySQL 库、JWT_SECRET 和有效模型配置；模板默认值已对齐历史实配供应商（一键交付下由 bootstrap 自动生成，无需手工复制）。前端配置不得包含模型密钥。
4. 对话目标仍为 Agnes `agnes-3.0-flash` / `https://api.agnes-ai.cn/v1`，协议及结构化输出待 D 验证。来源历史实配为 Spark + 独立 Embedding；需要向量功能时单独配置 Embedding 模型/维度与 Milvus，不能假定 Agnes 提供该能力。
5. 如需现有语义检索，在 `deploy/milvus/` 使用所附编排启动独立实例；先检查端口、容器及卷名，避免和其他项目冲突。由各人本机维护运行数据，不提交 Git。
6. 在 `backend/` 执行 `python scripts/init_db.py`，再执行 `python scripts/check_env.py`。初始化会建库建表并写示例岗位及本地 HR 演示账号；实际权限和连接以本机验证为准。现有脚本用 `create_all`，后续表结构变更须由 A 补迁移策略，不能把重复执行当成升级已有表。

后端在 `backend/` 目录运行：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

前端在 `frontend/` 目录运行：

```powershell
npm run dev
```

源码前端默认端口 3000，API 8000；`GET /api/health` 是健康检查入口。生产构建入口为 `npm run build`、`npm run start`。这些地址和命令尚未通过本机启动验收。现有 TaskRunner 在 API lifespan 启动，不同时额外启动独立 Worker，不使用多个 API worker 重复扫描任务。后续拆分必须更新契约及运行文档。

停止使用对应进程的 Ctrl+C；当前源码没有完整应用容器或一键安装包。备份/恢复及重建向量脚本已随源码导入，涉及实际数据时先在独立测试库演练，不据来源日志自动视为本机已验证。

## 测试与来源证据

- 本次协作检查：`python -B -m unittest discover -s tests/collaboration -v`，实际结果见 [接入记录](docs/源码接入与需求决策.md)。
- 业务后端：在独立环境的 `backend/` 运行 `python -m pytest`；会涉及 MySQL/Milvus 和测试数据，当前未执行。
- 业务前端：在 `frontend/` 运行 `npm test`、`npm run typecheck`、`npm run build`；当前未安装依赖或执行。
- 合成评测资产位于参考区的 `eval/`。如需复测，复制到本机忽略的 `backend/data/eval/` 后再读脚本确认输入；运行/清理脚本可能写数据库，不能当作纯只读检查。

分析基于用户 ZIP，不含来源 Git 历史。它的原 README、开发日志、测试报告只作为参考，现行工作从本 README、根规则、统一需求和四人任务开始。密钥、真实简历、数据库文件、上传目录、模型缓存和生成报告不提交。
