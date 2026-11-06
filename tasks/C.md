# C 工作包：动态自适应测评、文字面试、培训成长

维护角色：C；成员姓名待小组内部绑定，不能从工具或 Git 用户名猜测。状态：未开始业务实现（2026-11-06 基线）。

先读根 AGENTS.md、README、根更新记录、四个角色目录的最新交接，再读统一需求、分工与技术框架。任务详情以 docs/四人分工与试行方案.md 的 C 节为准。

## 写入范围

机器规则以 .collaboration/ownership.json 为准；以下是初始化时的范围：

- `frontend/src/features/assessments/**`
- `backend/app/modules/assessments/**`
- `frontend/src/features/interviews/**`
- `backend/app/modules/interviews/**`
- `frontend/src/features/training/**`
- `backend/app/modules/training/**`
- `tests/C/**`
- `scripts/C/**`
- `docs/modules/C/**`
- `docs/updates/C/**`
- `docs/requests/C/**`
- `tasks/C.md`
- `backend/migrations/versions/C_*.py`

AGENTS.md/CLAUDE.md 是治理例外，仅 A 可维护；只读来源、敏感/运行文件及未知路径不允许提交。文件不存在时可在归属范围内创建业务实现，不代表可初始化/覆盖别人的目录。

## 开工与交付

1. 身份确认是 C，使用 feature/C/<任务>；检查已有工作，不动别人的改动。
2. 新接口先在 docs/modules/C/ 写契约，公共改动放 docs/requests/C/ 请求维护人处理。
3. 只在本包内实现页面、接口、模型/数据与必要测试；同一职责复用公共服务。
4. 验证本模块真实流程、失败/重试及相关权限，记录实际结果；未联调保持待联调。
5. 在 docs/updates/C/ 新增记录，精确暂存、检查范围，再提交本人变更。

初始进度只代表协作规则就绪，不代表上述业务已实现。后续进度更新优先新增本人记录，不反复修改本任务的范围定义。
