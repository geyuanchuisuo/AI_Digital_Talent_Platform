# A 工作包：基础平台、岗位招聘、AI匹配与人工复核

维护角色：A；成员姓名待小组内部绑定，不能从工具或 Git 用户名猜测。状态：未开始业务实现（2026-11-06 基线）。

先读根 AGENTS.md、README、根更新记录、四个角色目录的最新交接，再读统一需求、分工与技术框架。任务详情以 docs/四人分工与试行方案.md 的 A 节为准。

## 写入范围

机器规则以 .collaboration/ownership.json 为准；以下是初始化时的范围：

- `frontend/src/features/identity/**`
- `backend/app/modules/identity/**`
- `frontend/src/features/recruitment/**`
- `backend/app/modules/recruitment/**`
- `frontend/src/features/matching/**`
- `backend/app/modules/matching/**`
- `backend/app/core/auth/**`
- `backend/app/core/db/**`
- `tests/A/**`
- `scripts/A/**`
- `docs/modules/A/**`
- `docs/updates/A/**`
- `docs/requests/A/**`
- `tasks/A.md`
- `backend/migrations/versions/A_*.py`
- `frontend/src/app/**`
- `frontend/src/shared/**`
- `frontend/src/main.tsx`
- `frontend/src/index.css`
- `frontend/src/vite-env.d.ts`
- `frontend/public/**`
- `frontend/index.html`
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/tsconfig.json`
- `frontend/tsconfig.app.json`
- `frontend/tsconfig.node.json`
- `frontend/vite.config.ts`
- `frontend/eslint.config.js`
- `backend/app/main.py`
- `backend/app/__init__.py`
- `backend/app/core/__init__.py`
- `backend/app/modules/__init__.py`
- `backend/pyproject.toml`
- `backend/requirements.txt`
- `backend/requirements-dev.txt`
- `backend/uv.lock`
- `backend/alembic.ini`
- `backend/migrations/env.py`
- `backend/migrations/script.py.mako`
- `README.md`
- `更新记录.md`
- `.gitignore`
- `.gitattributes`
- `.env.example`
- `.collaboration/**`
- `.githooks/**`
- `scripts/check_scope.py`
- `scripts/setup_collaboration.py`
- `tests/collaboration/**`
- `docs/模块归属.md`
- `docs/协作操作指南.md`
- `docs/需求文档.md`
- `docs/四人分工与试行方案.md`
- `docs/技术框架.md`

AGENTS.md/CLAUDE.md 是治理例外，仅 A 可维护；只读来源、敏感/运行文件及未知路径不允许提交。文件不存在时可在归属范围内创建业务实现，不代表可初始化/覆盖别人的目录。

## 开工与交付

1. 身份确认是 A，使用 feature/A/<任务>；检查已有工作，不动别人的改动。
2. 新接口先在 docs/modules/A/ 写契约，公共改动放 docs/requests/A/ 请求维护人处理。
3. 只在本包内实现页面、接口、模型/数据与必要测试；同一职责复用公共服务。
4. 验证本模块真实流程、失败/重试及相关权限，记录实际结果；未联调保持待联调。
5. 在 docs/updates/A/ 新增记录，精确暂存、检查范围，再提交本人变更。

初始进度只代表协作规则就绪，不代表上述业务已实现。后续进度更新优先新增本人记录，不反复修改本任务的范围定义。
