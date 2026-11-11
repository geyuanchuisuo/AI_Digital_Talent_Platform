"""Dify 编排画布对接（T-D16，PRD 执行表 D10 的画布对接半边）。

落库流程模型（T-D14 契约）与 Dify 画布 DSL 的双向映射：导出/导入/对账（service）
+ 运行追踪回写（writeback）。映射约定见 [Dify画布对接契约](../../modules/D/Dify画布对接契约.md)。

目标版本：Dify 1.19.0（T-D05 锁定 commit 8e689e868b63；DSL envelope 以其锁定源码
`api/constants/dsl_version.py`（CURRENT_APP_DSL_VERSION="0.7.0"）与
`api/services/entities/dsl_entities.py::make_app_dsl`、`api/models/workflow.py::WorkflowContentDict`
实核为准）。导入校验语义对齐其 `validate_graph_structure`（轻量结构校验，不按节点
类型 schema 强校验）——因此本桥以 `ait_<域节点类型>` 前缀在画布上承载本仓库六类
域节点，1:1 映射不注入合成 start/end，保持对账恒等。
"""
