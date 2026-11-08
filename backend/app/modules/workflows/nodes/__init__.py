"""工作流节点执行器包（T-D17）。

结构：
- runtime.py        运行状态（RunState）、输入引用解析、节点包装（步骤记录 + 异常回写）
- human_approval.py 人工审批节点（langgraph interrupt 挂起/恢复；超时与权限在 run_service 恢复口校验）
- business.py       五类业务节点薄适配（各调对应域 service，不自查他域表）
- graph_builder.py  落库版本（nodes/edges）→ langgraph StateGraph 的确定性构建

引擎执行语义见 app/core/workflows/engine/runner.py（T-D04）：引擎不理解业务节点，
本包负责把白名单节点类型翻译成引擎可执行的图。
"""
