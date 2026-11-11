"""SELECT-only SQL 校验器（T-D09）。

sqlparse（非验证型解析器）保守校验 + 表白名单核对 + LIMIT 强制：
1. 恰好一条语句，类型 SELECT（WITH CTE 视为 SELECT 族，仍走关键词扫描）；
2. 全树扫描禁写关键词（DML/DDL/实用命令），带引号的标识符/字符串不误伤；
3. FROM/JOIN 收集表引用，逐个核对表白名单（子查询递归）；
4. 顶层无 LIMIT → 包一层 `SELECT * FROM (...) AS _q LIMIT max_rows`。

定位：快速失败的 UX 层；硬强制在 PG 只读事务 + 副本列级 allowlist。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlparse
from sqlparse import sql as _sql
from sqlparse import tokens as _t

#: 全树禁止出现的关键词（保守白名单哲学：宁可错杀，执行面还有只读事务兜底）
FORBIDDEN_KEYWORDS = frozenset(
    {
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
        "GRANT", "REVOKE", "EXECUTE", "EXEC", "CALL", "DO", "COPY", "VACUUM",
        "MERGE", "REPLACE", "SET", "SHOW", "USE", "HANDLER", "LOCK", "UNLOCK",
        "LISTEN", "NOTIFY", "PREPARE", "DECLARE", "FETCH", "REFRESH",
        "IMPORT", "COMMENT",
        # SELECT ... INTO new_table / INTO OUTFILE（写文件/建表）
        "INTO",
        # EXPLAIN ANALYZE 会真实执行查询（DoS 向量）；EXPLAIN 本身也拒绝以保守
        "EXPLAIN",
        # ANALYZE 收集统计信息（维护命令）
        "ANALYZE",
        # PERFORM expression（执行表达式，可触发函数副作用）
        "PERFORM",
    }
)


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    sql: str = ""                        # 校验通过的最终 SQL（可能带 LIMIT 包装）
    tables: set[str] = field(default_factory=set)


def validate_sql(
    sql: str,
    whitelist: dict[str, frozenset[str]],
    *,
    max_rows: int,
) -> ValidationResult:
    """校验并返回最终可执行 SQL；不通过时 ok=False + reason（不含敏感细节）。"""
    if not sql or not sql.strip():
        return ValidationResult(False, "生成 SQL 为空")

    stripped = sql.strip().rstrip(";").strip()
    statements = [s for s in sqlparse.parse(stripped) if s.get_type() or s.tokens]
    if len(statements) != 1:
        return ValidationResult(False, "仅允许单条语句")

    stmt = statements[0]
    stype = (stmt.get_type() or "").upper()
    if stype not in ("SELECT", "UNKNOWN"):
        return ValidationResult(False, "仅允许 SELECT 查询")
    # UNKNOWN 需要「以 WITH 开头」才放行（CTE 查询）
    if stype == "UNKNOWN" and not stripped.upper().startswith("WITH"):
        return ValidationResult(False, "仅允许 SELECT/WITH 查询")

    # 全树禁写关键词扫描（引号内字符串/标识符的 ttype 不是 Keyword，不误伤）
    for tok in stmt.flatten():
        if tok.ttype in (_t.Keyword, _t.Keyword.DML, _t.Keyword.DDL, _t.Keyword.CTE) \
                and tok.normalized in FORBIDDEN_KEYWORDS:
            return ValidationResult(False, "包含非查询关键词")

    # 表引用白名单核对（FROM/JOIN，含子查询递归）；CTE 别名（X AS (…)）豁免
    tables = _extract_tables(stmt)
    cte_aliases = set(re.findall(r"\b([A-Za-z_]\w*)\s+AS\s*\(", stripped, flags=re.IGNORECASE))
    for t in tables:
        if t in cte_aliases:
            continue
        if t not in whitelist:
            return ValidationResult(False, f"表不在白名单：{t}")

    # FOR UPDATE / FOR SHARE（读出加锁）拒绝
    if "FOR UPDATE" in stripped.upper() or "FOR SHARE" in stripped.upper():
        return ValidationResult(False, "包含加锁子句")

    # 顶层 LIMIT 强制（已带 LIMIT 则不动；无则包装限行）
    upper = stripped.upper()
    has_limit = _has_top_level_limit(stmt)
    final = stripped if has_limit else (
        f"SELECT * FROM ({stripped}) AS _q LIMIT {max_rows}"
    )
    return ValidationResult(True, sql=final, tables=tables)


def _has_top_level_limit(stmt) -> bool:
    """顶层（非子查询）是否已有 LIMIT——按括号深度扫描，子查询内的 LIMIT 不计入。

    修复前用 flatten() 会把子查询里的 LIMIT 误判为顶层，导致外层不限行。
    """
    depth = 0
    for tok in stmt.flatten():
        if tok.ttype in _t.Punctuation and tok.normalized == "(":
            depth += 1
        elif tok.ttype in _t.Punctuation and tok.normalized == ")":
            depth -= 1
        elif depth == 0 and tok.ttype in _t.Keyword and tok.normalized == "LIMIT":
            return True
    return False


def _extract_tables(stmt) -> set[str]:
    """从语句树收集 FROM/JOIN 后的表名（小写；子查询递归；函数调用忽略）。"""
    tables: set[str] = set()
    _walk_tables(stmt.tokens, tables)
    return tables


def _walk_tables(tokens, tables: set[str], *, pending: bool = False) -> None:
    """pending=True 表示刚扫过 FROM/JOIN，下一个标识符就是表引用。"""
    for tok in tokens:
        if isinstance(tok, _sql.IdentifierList):
            if pending:
                for ident in tok.get_identifiers():
                    name = (ident.get_real_name() or "").lower()
                    if name and name not in _NON_TABLE_WORDS:
                        tables.add(name)
            _walk_tables(tok.tokens, tables, pending=False)
            pending = False
            continue
        if isinstance(tok, _sql.Identifier):
            # 含 Parenthesis 子节点 = 子查询别名（(SELECT ...) AS sub），
            # 不作为表名；仍递归提取内部真实表引用。
            is_subquery_alias = any(isinstance(c, _sql.Parenthesis) for c in tok.tokens)
            if pending and not is_subquery_alias:
                name = (tok.get_real_name() or "").lower()
                if name and name not in _NON_TABLE_WORDS:
                    tables.add(name)
            _walk_tables(tok.tokens, tables, pending=False)  # 子查询/点路径递归
            pending = False
            continue
        if isinstance(tok, _sql.Parenthesis):
            _walk_tables(tok.tokens, tables, pending=pending)
            pending = False
            continue
        if isinstance(tok, _sql.Where):
            _walk_tables(tok.tokens, tables, pending=False)
            pending = False
            continue
        if tok.is_group:
            _walk_tables(tok.tokens, tables, pending=pending)
            pending = False
            continue
        if tok.ttype in _t.Keyword:
            pending = tok.normalized in _FROM_JOIN_KEYWORDS
            continue


#: 触发表引用收集的连接词
_FROM_JOIN_KEYWORDS = frozenset({
    "FROM", "JOIN", "INNER JOIN", "LEFT JOIN", "LEFT OUTER JOIN", "RIGHT JOIN",
    "RIGHT OUTER JOIN", "FULL JOIN", "CROSS JOIN", "OUTER JOIN", "STRAIGHT_JOIN",
})

#: FROM/JOIN 后出现的非表词（保守排除，漏网表会被白名单核对拦截）
_NON_TABLE_WORDS = frozenset({"select", "where", "on", "as", "generate_series", "unnest", "values"})
