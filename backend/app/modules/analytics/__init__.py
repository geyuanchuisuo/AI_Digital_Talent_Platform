"""白名单指标问数（F07）。

- `models`：查询快照表（问题/指标/参数/数据时间/结果落库，审计与同快照导出基础）
- `service`：metric registry + 受控统计函数（先过滤后聚合）+ 副本/主库选择 + Redis 缓存
- `router`：/api/analytics 指标端点（含 SSE 进度端点首用，T-D01b 切片）

契约：docs/modules/D/问数切片契约.md；指标口径：docs/modules/A/指标口径交付.md。
"""
