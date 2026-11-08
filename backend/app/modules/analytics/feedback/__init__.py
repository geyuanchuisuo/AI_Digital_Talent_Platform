"""问数反馈（T-D12）——结果页「标记不准确」。

- `service`：创建（仅快照创建者，幂等）+ 列表（管理侧全量）；
- 反馈不参与任何统计计算（T-D12 验收②）。
契约：docs/modules/D/问数切片契约.md §10。
"""

from app.modules.analytics.feedback.service import create_feedback, list_feedback

__all__ = ["create_feedback", "list_feedback"]
