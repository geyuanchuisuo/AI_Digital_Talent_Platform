"""受控工作流域（F08）。

- `engine`：LangGraph 有状态图执行引擎壳（T-D04）——持久化检查点、
  人工等待/审批、断点续跑；并行于 TaskRunner（后者保留为解析/OCR 轻任务执行器）。
- 业务流程模型/节点/触发（T-D14 起）后续挂 `app/modules/workflows/`。
"""
