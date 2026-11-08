"""工作流执行引擎（T-D04）。

组成：
- `saver.SQLAlchemyCheckpointSaver`：LangGraph 检查点持久化（MySQL 生产 / SQLite 测试，
  一套实现；官方 sqlite/postgres saver 是额外依赖，未引入——见契约 §2）。
- `registry.WorkflowEngineRun`：引擎运行实例登记（D_0002，MySQL 基线 53 表）；
  `recover_registry()` 把孤儿 running 置 failed 可重试，**绝不触碰 waiting**——
  人工等待/审批状态不被任何恢复/心跳机制重置（T-D04 验收②③）。
- `runner.WorkflowEngine`：有状态图运行器壳。人工节点用 langgraph `interrupt()`，
  恢复用 `Command(resume=...)`；等待态由检查点承载，与 TaskRunner 完全隔离。

契约：docs/modules/D/工作流引擎契约.md。
"""
