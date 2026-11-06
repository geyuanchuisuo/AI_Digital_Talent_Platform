# AI 数字化人才平台

简历解析 → 人才画像 → 查重 → 岗位筛选 / 人岗匹配的一期实现（内网单机部署）。

> 运行手册（T-34）。口径文档见 `docs/`；开发进度与接力入口见 **`开发日志.md` Layer 0**。

## 当前进度

| 项 | 值 |
|---|---|
| 阶段 | M3 开发 · **B0–B5 ✅ 全部完成**（T-01~T-28 + B0 环境项）；B6 联调验收进行中（T-29 核心链路 ✅ / T-35 ✅ / T-32 ✅；T-30/T-31/T-33 待决策） |
| 已完成 | B0 环境（T-37/T-38a）、B1 骨架（T-01~T-05、T-06、T-34）、B2 登录鉴权（T-07~T-09）、B3 上传解析链路（T-10~T-17）、B4 人才画像（T-18~T-21）、B5 岗位+筛选+匹配（T-22~T-28） |
| 测试基线 | 后端 `pytest` **588 passed / 0 skip**；前端 `npm test` **65 passed**；鉴权 E2E **5/5 PASS**；**T-29 真实链路**：上传→解析→筛选（LLM 92.6/87.35 通过 + 4 人硬淘汰）→匹配（rank 89.96/88.03 + 向量相似度 0.79）→改判全通过 |

## 目录

```
main/
├── 需求文档.md / 业务逻辑说明.md / 开发日志.md   # 需求、业务视角、接力进度
├── docs/                                      # 架构/数据库/接口/算法/三份公共设计文档/todolist
├── deploy/milvus/docker-compose.yml           # 本项目独立 Milvus（T-37）
├── backups/                                   # 数据库备份（backup_db.py 输出，git 忽略）
├── backend/                                   # FastAPI 后端
│   ├── app/                                   # main.py / config.py / database.py / api / core / service / dao / models / tasks / utils
│   ├── scripts/                               # check_env / init_db / backup_db / rebuild_vectors / smoke_llm
│   ├── tests/                                 # pytest（L1 单元 / L2 集成，588 例）
│   ├── .env / .env.example                    # 实配不入库
│   └── requirements.txt
└── frontend/                                  # Next.js 14 + TS + antd 5
```

## 环境要求

- Python ≥3.11（项目 venv：`backend/.venv`，**必须用它**——机器有多套 Python，见开发日志 H-6）
- Node ≥18（两个版本并存均可）
- MySQL 8.x（localhost:3306，库 `ai_talent` 由 `init_db.py` 创建）
- Docker Desktop + 本项目 Milvus Standalone（`deploy/milvus/docker-compose.yml`，端口 19530）
- LLM（讯飞 spark-x2.6）与 Embedding（百炼 text-embedding-v3，1024 维）——配在 `backend/.env`

## 首次部署

```bash
# 0) Milvus（Docker Desktop 需已启动）
cd deploy/milvus && docker compose up -d && cd ../..

# 1) 后端
cd backend
python -m venv .venv                       # 首次
./.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env                       # 填入 LLM/Embedding key 与 JWT_SECRET
./.venv/Scripts/python.exe scripts/init_db.py        # 建库建表 + 种子（幂等）
./.venv/Scripts/python.exe scripts/check_env.py      # 环境自检（--skip-llm 跳过外呼）

# 2) 前端
cd ../frontend
npm install
cp .env.local.example .env.local           # NEXT_PUBLIC_API_BASE 默认 http://localhost:8000
npm run build
```

## 启动

```bash
# 后端（必须 --workers 1：TaskRunner 常驻单例，多进程会重复扫描任务）
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# 前端（生产）
cd frontend
npm run start          # http://localhost:3000
```

- 登录：种子账号 **hr / hr123456**（由 `init_db.py` 生成；一期未提供改密接口）。
- 健康检查：`GET /api/health`（免鉴权；db/milvus/worker/embedding_alive 一目了然）。
- 局域网访问：前端 `NEXT_PUBLIC_API_BASE` 指向部署机 IP；后端 CORS 默认 `*`。

## 日常运维

| 操作 | 命令/入口 |
|---|---|
| 数据库备份 | `./.venv/Scripts/python.exe scripts/backup_db.py`（默认输出 `backups/ai_talent_YYYYMMDD.sql`；T-35 已演练恢复） |
| 数据库恢复 | `./.venv/Scripts/python.exe scripts/backup_db.py --restore <file> --database <临时库名>`（目标库名禁止等于生产库） |
| 向量全量重建 | `./.venv/Scripts/python.exe scripts/rebuild_vectors.py`（`--dry-run` 预览 / `--force` 强制 / `--recreate --yes` 重建 collection） |
| LLM 冒烟 | `./.venv/Scripts/python.exe scripts/smoke_llm.py` |
| 队列/批次并发 | 页面「简历筛选 → 并发设置」（1–5，写 `sys_settings`，下一个扫描周期生效） |
| 语义检索不可用 | 画像/岗位页提示 503 = Embedding/Milvus 不可用；恢复后详情页"重建向量"或跑 `rebuild_vectors.py` |
| 首次向量同步失败 | 冷连接 embedding ~1.7s（H-14 已解决：预算调至 5s，运行配置 v1.4）；仍失败则重试一次或跑 rebuild；详见开发日志 H-14 |

## 故障排查（详见开发日志《环境坑复现速查卡》）

| 现象 | 处置 |
|---|---|
| Milvus 容器退出（宿主机休眠后） | `docker start aitalent-milvus-standalone`，等 19530 监听（对 HTTP 返回 404 = 正常）；勿删卷勿重建 |
| 脚本报"未就绪"但服务在跑 | 换 `backend/.venv/Scripts/python.exe` 重跑（H-6 多解释器） |
| pytest 报 `PermissionError WinError 5` | 用 `--basetemp=.pytest_tmp_run`（H-7） |
| 前端测试 EPERM/文件数变少 | 只跑 `npm test`（H-11），看 Test Files 数量是否对 |
| curl 上传中文文件名乱码 | 是 curl 的编码问题（H-13），用浏览器或 TestClient 验证，**禁止**服务端转码 |

## 实现口径（不要在代码里另立一套）

| 文档 | 内容 |
|---|---|
| `docs/运行配置.md` v1.3 | 配置键名 / 默认值 / `.env.example` |
| `docs/统一错误码与鉴权.md` v1.2 | 响应体 / 错误码 / JWT |
| `docs/LLM提示词设计.md` v1.2 | Prompt A/B/C/D 模板 / 输出 schema / 别名表 |
| `docs/架构设计.md` v1.4 · `docs/接口设计.md` v1.3 · `docs/数据库设计.md` v1.2 | 分层 / 接口 / 13 张表 |
| `docs/算法与判定规则.md` v1.2 | 学历/年限/技能判定、评分公式、阈值口径 |
| `main/开发日志.md` | **接力入口**：进度 / 规范 / 决策 / 环境快照 / 坑速查卡 |
