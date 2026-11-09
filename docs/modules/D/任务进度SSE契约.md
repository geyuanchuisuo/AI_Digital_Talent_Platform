# T-D06 任务进度 SSE 契约

- `GET /api/tasks/{task_id}/events?kind=parse|screen|match`，Bearer 鉴权；三类任务使用独立表主键，必须显式提供 kind。
- parse 任务由上传者本人或管理侧查看，screen/match 批次仅管理侧；无权返回 403，不存在返回 404，权限在建立流之前校验。
- 响应 `text/event-stream`；每条数据包含真实持久化的 `kind/task_id/status`，parse 附 `stage/retries`，screen/match 附 `completed/total/retry_count`。不生成百分比或推断阶段。
- SSE 事件 `progress` 对应非终态，`done` 对应终态；id 为快照内容的稳定哈希。每 2 秒读取最新行，内容变化才发送；支持 `Last-Event-ID`，相同时跳过重复的非终态，已完成/失败/取消等终态始终返回后结束连接。
- 重连只补**当前持久化快照**与终态；不保证重放断线期间每个中间状态，若需完整事件历史须增加持久化事件表。管理员撤权/停用后流在下一轮停止，不继续提供数据。
- 实现路径：`backend/app/core/tasks/events.py`，经 `app/main.py` 注册。无迁移或新增依赖。
- 本轮用户要求仅静态验证：运行时 403、重连和终态实测暂未进行，验收测试待最终阶段执行。
