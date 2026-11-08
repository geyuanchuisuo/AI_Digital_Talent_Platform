"""自由问数 NL2SQL 受控执行（T-D09）。

分层（外层依赖内层，测试可逐层注入替身）：
- `whitelist`：副本 schema 发现——表/字段白名单 = ETL 同步宇宙（列级 allowlist 的
  落地物即字段白名单的执行面）；
- `validator`：sqlparse SELECT-only 校验——单语句/禁写关键词/表白名单/强制 LIMIT；
- `executor`：只读事务 + statement_timeout + 行数上限的受控执行；
- `generator`：问题 → SQL 生成器协议（OpenAI 兼容 spark/Agnes；无法安全映射返回
  None 拒答不猜测）；
- `service`：编排 + `nl2sql_audit` 全链路审计落库。

安全模型（纵深防御）：校验器是快速失败的 UX 层；**真正的强制层是 PG 只读事务**
（`SET TRANSACTION READ ONLY` + `statement_timeout`）与副本列级 allowlist（副本里
根本没有敏感列可查）。问题文本是不可信输入——提示注入的兜底同样是校验器+只读事务。

契约：docs/modules/D/问数切片契约.md §8（T-D09）。
"""
