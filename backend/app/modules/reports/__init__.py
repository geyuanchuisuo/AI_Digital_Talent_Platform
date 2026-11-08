"""报告导出域（T-D11）——同快照 DOCX/PDF 导出。

- `models.SnapshotExportAudit`：导出审计（谁/何时/什么格式/哪个快照）；
- `service`：权限复核（创建者或管理侧）→ 从快照 result_json 确定性构建
  DOCX/PDF（**不重新问数**——只用快照留存的 value/table/interpretation/chart）→
  审计落库 → 返回字节流；
- `router`：GET /api/reports/snapshots/{id}/export?format=docx|pdf。

契约：docs/modules/D/问数切片契约.md §11（T-D11）。
"""

