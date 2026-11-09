# 缺陷报告：/analytics 页面 Unhandled Runtime Error（持久复现）

- 请求方：A（UI 自查 T-A02 验收发现）
- 目标维护人：D（analytics 页面与 metric_registry 归 D）
- 日期：2026-11-09 00:30（2026-11-08 深夜发现）
- 状态：待 D 修复；A 不代改（analytics 归 D）

## 复现（100% 复现率，刷新后持续）

1. hr 登录 → 访问 `http://localhost:3101/analytics`
2. 页面 **Unhandled Runtime Error**（Next.js 错误覆盖层）：
   `TypeError: Invalid attempt to spread non-iterable instance`
   调用栈：`_toConsumableArray` → **`antd/es/list/index.js (146:44) dataSource`**
3. 页面同时弹两条 message：**「未登记的指标（白名单外不支持）」**×2

## 根因分析（A 侧证据，供 D 定位）

1. **页面默认查询的指标不在白名单**：metric_registry（D_0004 七行种子）无该条目 → registry 准入链按设计返回「未登记的指标」（此为正确防御）——**但前端把错误响应直接喂给了 antd List 的 dataSource**；
2. **dataSource 防御缺失**：`GET /api/analytics/metrics` 错误/空响应时，页面的 List 组件收到 `undefined`/非数组 → spread 崩溃（antd list index.js:146）。

## 建议修复（D 执行）

1. metric_registry 补登记页面默认查询的指标（或前端默认指标改为已登记项）；
2. 前端对 analytics 响应做防御：`dataSource = Array.isArray(data.items) ? data.items : []`（错误态渲染空态/错误提示，不崩溃）；
3. 两处均在 D 的 ownership 内，A 不代改。

## 验收影响

- `/analytics` 页面**不可用**（白屏级错误覆盖层）——T-A02 验收清单中该项标记为**阻塞（D 缺陷）**；
- 其余管理端页面（screen/match/training/interviews/assessments/talents/job-profiles）UI 自查全部正常。
