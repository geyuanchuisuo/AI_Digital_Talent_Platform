"""岗位条件服务（T-22）· 接口 §6 + 数据库 §6.1 + 需求 F3-1。

三条硬口径：
1. **校验统一 400**（接口 §6）：weights 四维总和=100、pass_threshold > pending_threshold、
   `min_degree` 逐字等于六档字面值之一（**「高中中专」**，`"高中/中专"` 拒绝，D-G）。
2. **技能入库前归一化（D-N）**：`normalize_skills()` 是唯一实现——`Python`→`python`、
   `golang`→`go`、`Python 3.11`→`python`；岗位技能与画像技能、T-23 命中判定共用一张别名表。
3. **快照隔离**：PUT 成功 version+1；历史批次读的是自己的 `config_snapshot_json`，
   不受后续修改影响（数据库 §6.1）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import BizError
from app.core.normalize import DEGREE_VALUES, normalize_skills
from app.dao import job_profiles as job_dao
from app.models import JobProfile
from app.schemas.job_profile import JobProfileWriteRequest

JOB_NOT_FOUND = "岗位不存在"
JOB_DISABLED = "岗位已停用（存在历史批次引用）"


def _validate(payload: JobProfileWriteRequest) -> None:
    """业务校验，非法抛 400（统一 400 口径，不经 Pydantic 422）。"""
    if payload.weights.total() != 100:
        raise BizError(
            400,
            f"weights 四维权重总和必须为 100，当前为 {payload.weights.total()}"
            f"（skills/experience/education/others）",
        )
    if payload.pass_threshold <= payload.pending_threshold:
        raise BizError(
            400,
            f"pass_threshold（{payload.pass_threshold}）必须大于 pending_threshold（{payload.pending_threshold}）",
        )
    if payload.min_degree is not None and payload.min_degree not in DEGREE_VALUES:
        raise BizError(
            400,
            f"min_degree 仅支持 {'/'.join(DEGREE_VALUES)}（注意是「高中中专」，不是「高中/中专」）",
        )


def _columns(payload: JobProfileWriteRequest) -> dict[str, Any]:
    """请求体 → ORM 列值；技能在此归一化（D-N）。"""
    return {
        "title": payload.title.strip(),
        "department": (payload.department or "").strip() or None,
        "jd_text": payload.jd_text,
        "requirements_text": payload.requirements_text,
        "min_degree": payload.min_degree,
        "min_work_years": (
            None if payload.min_work_years is None else round(float(payload.min_work_years), 1)
        ),
        "must_skills_json": normalize_skills(payload.must_skills),
        "bonus_skills_json": normalize_skills(payload.bonus_skills),
        "hard_rule_switches_json": payload.hard_rule_dict(),
        "pass_threshold": round(float(payload.pass_threshold), 2),
        "pending_threshold": round(float(payload.pending_threshold), 2),
        "weights_json": payload.weights.model_dump(),
    }


def create_job(db: Session, payload: JobProfileWriteRequest, *, user_id: int) -> JobProfile:
    """新建岗位：version=1、is_active=1、created_by/updated_by 同人。"""
    _validate(payload)
    columns = _columns(payload)
    row = job_dao.create(
        db, version=1, is_active=1, created_by=user_id, updated_by=user_id, **columns
    )
    db.commit()
    db.refresh(row)
    return row


def put_job(db: Session, job_id: int, payload: JobProfileWriteRequest, *, user_id: int) -> JobProfile:
    """全量更新：校验同 POST；成功 version+1 并写 updated_by（快照隔离，历史批次不变）。"""
    row = job_dao.get(db, job_id)
    if row is None:
        raise BizError(404, JOB_NOT_FOUND)
    _validate(payload)
    columns = _columns(payload)
    columns["version"] = row.version + 1
    columns["updated_by"] = user_id
    job_dao.apply_fields(db, row, columns)
    db.commit()
    db.refresh(row)
    return row


def delete_job(db: Session, job_id: int) -> dict[str, Any]:
    """删除：有批次引用 → 软删（is_active=0）；无引用 → 物理删除；重复删除 404。"""
    row = job_dao.get(db, job_id)
    if row is None or row.is_active == 0:
        # is_active=0 即已软删（可能仍被 GET 全量列表看到，但不可再删）
        raise BizError(404, JOB_NOT_FOUND)
    if job_dao.has_batches(db, job_id):
        row.is_active = 0
        db.commit()
        return {"deleted": True, "soft_deleted": True}
    job_dao.hard_delete(db, row)
    db.commit()
    return {"deleted": True, "soft_deleted": False}


def get_job(db: Session, job_id: int) -> JobProfile:
    row = job_dao.get(db, job_id)
    if row is None:
        raise BizError(404, JOB_NOT_FOUND)
    return row


def list_jobs(
    db: Session,
    *,
    keyword: str | None = None,
    is_active: bool | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[JobProfile], int]:
    return job_dao.list_page(db, keyword=keyword, is_active=is_active, offset=offset, limit=limit)


def apply_fields(db: Session, row: JobProfile, columns: dict[str, Any]) -> None:
    """供测试/内部复用的薄封装（T-22 内不直接使用）。"""
    job_dao.apply_fields(db, row, columns)


# ---- T-A22：AI JD 草稿（真实模型生成，失败明确报错，不返回样例） ----

JD_DRAFT_PROMPT_VERSION = "jd-v1"


def _extract_jd_md(raw: str | None) -> str:
    """模型响应清洗（T-A22 真机实测发现偶发包装）：

    实测 spark-x2.6 在个别调用会把草稿包成 JSON（{"title":...,"jd_draft":"## ..."}）
    或代码块标记。清洗规则：剥 ``` 围栏 → 若整体是 JSON 对象则取已知草稿键的
    字符串值；清洗后仍无内容返回空串（上层 503 明确报错，不造样例）。
    """
    import json as _json

    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.startswith("{"):
        try:
            obj = _json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            for key in ("jd_draft", "draft_md", "jd", "content", "text"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
    return text


def generate_jd_draft(payload: "JobProfileJdDraftRequest") -> dict[str, Any]:
    """调用真实 LLM 生成 JD 草稿（T-A22）。

    口径（需求 §16 D-04 相关验收）：模型失败/超时/空返回一律明确报错（503，上游依赖不可用），
    绝不用样例文案顶替；草稿不落库——人工编辑确认后随 POST/PUT 岗位保存，
    「保存后可查」由岗位自身 jd_text 承担。模型名（model_version）取本次
    调用实际生效值（供应商启停/切换即时生效，llm_client 口径）。
    """
    import json as _json
    from datetime import datetime as _dt

    from app.core.llm_client import get_llm_client
    from app.core.prompts.loader import TEMPLATE_DIR

    payload_json = _json.dumps(
        {
            "title": payload.title,
            "department": payload.department,
            "requirements_text": payload.requirements_text,
            "min_degree": payload.min_degree,
            "must_skills": payload.must_skills,
            "bonus_skills": payload.bonus_skills,
            "remarks": payload.remarks,
        },
        ensure_ascii=False,
    )
    template = (TEMPLATE_DIR / "jd_draft.txt").read_text(encoding="utf-8")
    prompt = template.replace("{payload_json}", payload_json)

    client = get_llm_client()
    try:
        draft = client.chat_text(
            prompt,
            system_prompt=(
                "你是 JD 起草助手。只输出 markdown 正文：以 ## 岗位职责 / ## 任职要求 / ## 加分项 "
                "为结构。绝不输出 JSON、代码块标记或任何解释性文字。"
            ),
            purpose="jd_draft",
            max_tokens=2000,
        )
    except Exception as exc:  # LLMError/超时/连接失败 → 统一 503 明确报错（公共错误码白名单无 502）
        raise BizError(503, f"JD 草稿生成失败：{exc}") from exc
    draft = _extract_jd_md(draft)
    if not draft:
        raise BizError(503, "JD 草稿生成失败：模型返回空内容")
    return {
        "draft_md": draft,
        "model_version": client.model,
        "prompt_version": JD_DRAFT_PROMPT_VERSION,
        "generated_at": _dt.now(),
    }
