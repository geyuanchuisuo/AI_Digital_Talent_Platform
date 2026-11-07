# 更新：公共 CrossDbUpdatedAtMixin 跨库变体交付（功能 9，回应 C 反向请求）

- 记录 ID：UPD-A-20261107-1725-CrossDbUpdatedAtMixin交付
- 时间/身份/实际分支：2026-11-07 17:25；**A**；`feature/A/continuation`
- 对应任务：C 响应（20261107-153521）第 1 项反向请求——「公共 mixin 增加 dialect 感知默认值，交付后 C 收敛」。
- 状态：已验证（结构对照全过；两变体可导入）。
- 本次改动路径：`backend/app/models/base.py`（新增 `CrossDbUpdatedAtMixin`：server_default=CURRENT_TIMESTAMP 跨库安全 + 应用层 onupdate；`UpdatedAtMixin` docstring 补 SQLite 不可用警告）；`docs/requests/A/20261107-C-mixin收敛变体交付.md`（交付说明与 C 收敛步骤）；本记录。
- 设计说明：SQLAlchemy server_default 不支持运行时 dialect 切换（静态 Column 属性），经评估采用**双变体**方案——`UpdatedAtMixin`（MySQL 专属，ON UPDATE）与 `CrossDbUpdatedAtMixin`（跨库安全，应用层 onupdate）——后者语义与 C 的 CTimestampMixin 完全一致，实现「公共层定义、C 收敛自定义 mixin」的目标。
- 实际验证：结构对照 51 表全过；两变体导入过。过程修复：user_scope_grants 补 UpdatedAtMixin（初版漏时间戳 mixin，被对照测试抓出——护栏有效）。
- 对其他模块的影响：C 收敛后删 CTimestampMixin 换公共变体（**无需迁移**，列结构不变）；收敛步骤见交付文档。
- 提交/PR：创建后追加引用，不预填 hash。
