"""A08 申请输入快照与对外字段分级（需求 §61 / §73 / §201 / §280；执行表 A08）。

逐条固化以下验收点，缺一不可：
1. 创建申请时把岗位/JD 条件、人才档案、简历原件与解析结果做**值拷贝**冻结；
2. 岗位、JD、档案后续变更**不修改历史申请**的冻结内容（含阶段推进与 HR 确认后）；
3. 同人多岗位申请各自独立快照；迁移前历史行明确报 available=false；
4. 完整快照只经管理侧角色门禁接口可读（401/403/404 反例）；
5. 个人端只看到获准的对外字段：内部阶段/推进原因/操作人/快照一律不外露，
   未经 HR 确认的终态结果不外露；后端 AccountScope 强制收窄，忽略伪造参数。

造数与清理自包含（隔离库 ai_talent_a_probe），不依赖其它测试文件的 module-scope 状态。
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete

from app.database import SessionLocal
from app.main import app
from app.models import (
    JobProfile,
    ParsedResume,
    ResumeFile,
    TalentProfile,
    User,
)
from app.modules.recruitment import service as recruitment_service
from app.modules.recruitment.models import Application, JdPublication
from app.service.auth_service import hash_password

pytestmark = pytest.mark.integration

PWD = "a08-pass-123456"
PUBLIC_FIELDS = {
    "id", "job_profile_id", "external_stage", "result_category",
    "result_published", "stage_updated_at",
}
FORBIDDEN_FIELDS = {
    "internal_stage", "stage_reason", "stage_updated_by", "result_confirmed_by",
    "talent_profile_id", "resume_file_id", "job_profile_version", "closed_at",
    "input_snapshot_json", "input_snapshot_at", "created_by", "created_at",
}


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


@pytest.fixture()
def env():
    """每个用例独立造数 + teardown，避免同岗在途 409 互相干扰。"""
    tag = uuid4().hex[:8]
    with SessionLocal() as db:
        job_a = JobProfile(
            title=f"A08 岗位A-{tag}", department="技术部", jd_text="负责核心服务 A",
            requirements_text="本科 3 年 Python", min_degree="本科",
            min_work_years=Decimal("3"), must_skills_json=["python", "mysql"],
            bonus_skills_json=["milvus"],
            hard_rule_switches_json={"degree": True, "work_years": True},
            pass_threshold=Decimal("75"), pending_threshold=Decimal("60"),
            weights_json={"skills": 40, "experience": 30},
            version=1, is_active=1, created_by=1,
        )
        job_b = JobProfile(
            title=f"A08 岗位B-{tag}", department="产品部", jd_text="负责产品 B",
            requirements_text="本科 2 年", min_degree="本科",
            min_work_years=Decimal("2"), must_skills_json=["sql"],
            bonus_skills_json=[], hard_rule_switches_json={"degree": True},
            pass_threshold=Decimal("80"), pending_threshold=Decimal("65"),
            weights_json={"skills": 50, "experience": 50},
            version=1, is_active=1, created_by=1,
        )
        talent_a = TalentProfile(
            name=f"A08候选人A-{tag}", degree="本科", work_years=Decimal("5.0"),
            skills_json=["python", "mysql"], vector_status="synced", source="resume",
        )
        talent_b = TalentProfile(
            name=f"A08候选人B-{tag}", degree="硕士", work_years=Decimal("2.0"),
            skills_json=["sql"], vector_status="synced", source="resume",
        )
        db.add_all([job_a, job_b, talent_a, talent_b])
        db.flush()

        file_row = ResumeFile(
            filename=f"A08-{tag}.pdf", stored_path=f"data/uploads/{tag}.pdf",
            file_hash=tag * 8, file_ext="pdf", file_size=1024,
            upload_batch_id=tag.upper() * 2, uploaded_by=1,
        )
        db.add(file_row)
        db.flush()
        parsed_row = ParsedResume(
            resume_file_id=file_row.id, talent_profile_id=talent_a.id,
            structured_json={"name": f"A08候选人A-{tag}", "skills": ["python", "mysql"]},
            raw_text="候选人 A 的完整简历原文", llm_model="spark-x2.6",
        )
        jd_row = JdPublication(
            job_profile_id=job_a.id, job_profile_version=1,
            title=f"A08 JD-A-v1-{tag}", content_snapshot_json={"text": "JD 第一版"},
            channel="internal", status="published", published_by=1,
            published_at=datetime.now(),
        )
        db.add_all([parsed_row, jd_row])
        db.flush()

        u_personal = User(
            username=f"a08_per_{tag}", password_hash=hash_password(PWD),
            display_name="A08 个人A", role="personal", person_id=talent_a.id, is_active=1,
        )
        u_personal_b = User(
            username=f"a08_perb_{tag}", password_hash=hash_password(PWD),
            display_name="A08 个人B", role="personal", person_id=talent_b.id, is_active=1,
        )
        u_unbound = User(
            username=f"a08_unb_{tag}", password_hash=hash_password(PWD),
            display_name="A08 未绑定", role="employee", is_active=1,
        )
        db.add_all([u_personal, u_personal_b, u_unbound])
        db.commit()

        ids = {
            "job_a": job_a.id, "job_b": job_b.id,
            "talent_a": talent_a.id, "talent_b": talent_b.id,
            "file": file_row.id, "parsed": parsed_row.id, "jd": jd_row.id,
            "user_personal": u_personal.id, "user_personal_b": u_personal_b.id,
            "user_unbound": u_unbound.id, "tag": tag,
        }

    with TestClient(app) as client:
        ctx = {
            "client": client,
            "hr": _hdr(_login(client, "hr", "hr123456")),
            "personal": _hdr(_login(client, f"a08_per_{tag}", PWD)),
            "personal_b": _hdr(_login(client, f"a08_perb_{tag}", PWD)),
            "unbound": _hdr(_login(client, f"a08_unb_{tag}", PWD)),
            **ids,
        }
        yield ctx

    with SessionLocal() as db:
        db.execute(sa_delete(Application).where(
            Application.talent_profile_id.in_([ids["talent_a"], ids["talent_b"]])
        ))
        db.execute(sa_delete(JdPublication).where(
            JdPublication.job_profile_id.in_([ids["job_a"], ids["job_b"]])
        ))
        db.execute(sa_delete(ParsedResume).where(ParsedResume.resume_file_id == ids["file"]))
        db.execute(sa_delete(ResumeFile).where(ResumeFile.id == ids["file"]))
        db.execute(sa_delete(TalentProfile).where(
            TalentProfile.id.in_([ids["talent_a"], ids["talent_b"]])
        ))
        db.execute(sa_delete(JobProfile).where(
            JobProfile.id.in_([ids["job_a"], ids["job_b"]])
        ))
        db.execute(sa_delete(User).where(
            User.id.in_([ids["user_personal"], ids["user_personal_b"], ids["user_unbound"]])
        ))
        db.commit()


def _create(client, headers, job_id, talent_id, file_id=None) -> dict:
    resp = client.post(
        "/api/recruitment/applications",
        json={
            "talent_profile_id": talent_id, "job_profile_id": job_id,
            "job_profile_version": 1, "resume_file_id": file_id,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _snapshot(client, headers, application_id):
    return client.get(
        f"/api/recruitment/applications/{application_id}/input-snapshot", headers=headers
    )


def _list(client, headers, **params):
    resp = client.get("/api/recruitment/applications", params={"page_size": 200, **params}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["items"]


# --------------------------------------------------------------------------- #
# 1. 快照内容：值拷贝、可序列化、缺源容错
# --------------------------------------------------------------------------- #
def test_capture_freezes_all_sources(env):
    with SessionLocal() as db:
        snap = recruitment_service.capture_application_input_snapshot(
            db, talent_profile_id=env["talent_a"], job_profile_id=env["job_a"],
            job_profile_version=1, resume_file_id=env["file"],
        )
    assert snap["schema_version"] == "A08-v1"
    assert snap["job_profile"]["title"].startswith("A08 岗位A-")
    assert snap["job_profile"]["weights_json"] == {"skills": 40, "experience": 30}
    assert snap["job_profile"]["pass_threshold"] == 75.0
    assert snap["talent_profile"]["name"].startswith("A08候选人A-")
    assert snap["talent_profile"]["skills_json"] == ["python", "mysql"]
    assert snap["resume_file"]["file_hash"] == env["tag"] * 8
    assert snap["parsed_resume"]["structured_json"]["skills"] == ["python", "mysql"]
    assert snap["jd_publication"]["title"].startswith("A08 JD-A-v1-")
    assert snap["missing_sources"] == []
    assert snap["captured_at"]


def test_capture_is_json_serializable(env):
    with SessionLocal() as db:
        snap = recruitment_service.capture_application_input_snapshot(
            db, talent_profile_id=env["talent_a"], job_profile_id=env["job_a"],
            job_profile_version=1, resume_file_id=env["file"],
        )
    # Decimal / datetime 都已在 _plain 中转换，落 JSON 列不应抛 TypeError
    assert json.loads(json.dumps(snap, ensure_ascii=False))["job_profile"]["min_work_years"] == 3.0


def test_capture_tolerates_missing_sources(env):
    with SessionLocal() as db:
        snap = recruitment_service.capture_application_input_snapshot(
            db, talent_profile_id=999_000_001, job_profile_id=999_000_002,
            job_profile_version=7, resume_file_id=None,
        )
    assert snap["job_profile"] is None and snap["talent_profile"] is None
    assert snap["resume_file"] is None and snap["parsed_resume"] is None
    assert snap["job_profile_version"] == 7
    assert {"job_profile", "talent_profile", "resume_file", "parsed_resume"} <= set(snap["missing_sources"])


def test_capture_raw_text_fingerprint_not_full_copy(env):
    with SessionLocal() as db:
        snap = recruitment_service.capture_application_input_snapshot(
            db, talent_profile_id=env["talent_a"], job_profile_id=env["job_a"],
            job_profile_version=1, resume_file_id=env["file"],
        )
    parsed = snap["parsed_resume"]
    assert "raw_text" not in parsed
    assert len(parsed["raw_text_sha256"]) == 64
    assert parsed["raw_text_length"] == len("候选人 A 的完整简历原文")


def test_capture_falls_back_to_latest_parsed_resume(env):
    """未显式指定原件时按档案回退，仅用于快照，不改申请行的 resume_file_id。"""
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], file_id=None)
    payload = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]
    assert payload["available"] is True
    assert payload["snapshot"]["resume_file_id"] == env["file"]
    assert payload["snapshot"]["job_profile"] is not None
    with SessionLocal() as db:
        assert db.get(Application, row["id"]).resume_file_id is None


# --------------------------------------------------------------------------- #
# 2. 持久化与不可变：源变更、阶段推进、HR 确认都不回写
# --------------------------------------------------------------------------- #
def test_create_application_persists_snapshot(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    resp = _snapshot(env["client"], env["hr"], row["id"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["application_id"] == row["id"]
    assert data["available"] is True and data["schema_version"] == "A08-v1"
    assert data["captured_at"] is not None
    assert data["snapshot"]["job_profile"]["title"].startswith("A08 岗位A-")
    with SessionLocal() as db:
        db_row = db.get(Application, row["id"])
        assert db_row.input_snapshot_at is not None
        assert db_row.input_snapshot_json == data["snapshot"]


def test_snapshot_unchanged_after_job_profile_update(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    before = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]["snapshot"]
    with SessionLocal() as db:
        job = db.get(JobProfile, env["job_a"])
        job.title = "岗位已改名"
        job.pass_threshold = Decimal("99")
        job.weights_json = {"skills": 100}
        job.version = 2
        db.commit()
        assert db.get(JobProfile, env["job_a"]).title == "岗位已改名"
    after = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]["snapshot"]
    assert after == before
    assert after["job_profile"]["title"].startswith("A08 岗位A-")
    assert after["job_profile"]["pass_threshold"] == 75.0


def test_snapshot_unchanged_after_talent_profile_update(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    before = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]["snapshot"]
    with SessionLocal() as db:
        profile = db.get(TalentProfile, env["talent_a"])
        profile.name = "档案已改名"
        profile.degree = "博士"
        profile.skills_json = ["go"]
        db.commit()
    after = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]["snapshot"]
    assert after == before
    assert after["talent_profile"]["skills_json"] == ["python", "mysql"]


def test_snapshot_unchanged_after_jd_withdraw_and_republish(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    before = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]["snapshot"]
    with SessionLocal() as db:
        old = db.get(JdPublication, env["jd"])
        old.status = "withdrawn"
        db.add(JdPublication(
            job_profile_id=env["job_a"], job_profile_version=2,
            title="JD 第二版", content_snapshot_json={"text": "JD 第二版内容"},
            channel="internal", status="published", published_by=1,
            published_at=datetime.now(),
        ))
        db.commit()
    after = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]["snapshot"]
    assert after == before
    assert after["jd_publication"]["title"].startswith("A08 JD-A-v1-")


def test_snapshot_unchanged_after_stage_and_result_confirm(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    before = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]
    for stage in ("screening", "invited", "deciding", "offer_accepted"):
        resp = env["client"].patch(
            f"/api/recruitment/applications/{row['id']}/stage",
            json={"internal_stage": stage, "reason": "A08 推进"}, headers=env["hr"],
        )
        assert resp.status_code == 200, resp.text
    resp = env["client"].post(
        f"/api/recruitment/applications/{row['id']}/result-confirm", headers=env["hr"]
    )
    assert resp.status_code == 200, resp.text
    after = _snapshot(env["client"], env["hr"], row["id"]).json()["data"]
    assert after["snapshot"] == before["snapshot"]
    assert after["captured_at"] == before["captured_at"]


def test_two_applications_have_independent_snapshots(env):
    a = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    b = _create(env["client"], env["hr"], env["job_b"], env["talent_b"], env["file"])
    snap_a = _snapshot(env["client"], env["hr"], a["id"]).json()["data"]["snapshot"]
    snap_b = _snapshot(env["client"], env["hr"], b["id"]).json()["data"]["snapshot"]
    assert snap_a["job_profile"]["title"].startswith("A08 岗位A-")
    assert snap_b["job_profile"]["title"].startswith("A08 岗位B-")
    assert snap_a["talent_profile"]["name"].startswith("A08候选人A-")
    assert snap_b["talent_profile"]["name"].startswith("A08候选人B-")
    with SessionLocal() as db:
        job = db.get(JobProfile, env["job_a"])
        job.title = "岗位A已改"
        db.commit()
    assert _snapshot(env["client"], env["hr"], a["id"]).json()["data"]["snapshot"] == snap_a
    assert _snapshot(env["client"], env["hr"], b["id"]).json()["data"]["snapshot"] == snap_b


def test_legacy_application_reports_unavailable(env):
    with SessionLocal() as db:
        legacy = Application(
            talent_profile_id=env["talent_a"], job_profile_id=env["job_a"],
            job_profile_version=1, internal_stage="new", external_stage="submitted",
            created_by=1,
        )
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    data = _snapshot(env["client"], env["hr"], legacy_id).json()["data"]
    assert data["available"] is False
    assert data["snapshot"] is None and data["captured_at"] is None
    assert data["schema_version"] is None
    # 历史行仍可正常出现在管理侧列表，不因缺快照报错
    assert legacy_id in {
        r["id"] for r in _list(env["client"], env["hr"], talent_profile_id=env["talent_a"])
    }


# --------------------------------------------------------------------------- #
# 3. 快照接口：管理侧角色门禁
# --------------------------------------------------------------------------- #
def test_snapshot_endpoint_requires_token(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    assert _snapshot(env["client"], {}, row["id"]).status_code == 401


def test_snapshot_endpoint_denies_personal(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    assert _snapshot(env["client"], env["personal"], row["id"]).status_code == 403
    assert _snapshot(env["client"], env["personal_b"], row["id"]).status_code == 403


def test_snapshot_endpoint_denies_unbound_employee(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    assert _snapshot(env["client"], env["unbound"], row["id"]).status_code == 403


def test_snapshot_endpoint_allows_hr(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    resp = _snapshot(env["client"], env["hr"], row["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["available"] is True


def test_snapshot_endpoint_404_for_unknown_application(env):
    assert _snapshot(env["client"], env["hr"], 999_000_003).status_code == 404


# --------------------------------------------------------------------------- #
# 4. 个人侧对外字段分级与授权收紧
# --------------------------------------------------------------------------- #
def test_public_list_only_whitelisted_fields(env):
    _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    rows = _list(env["client"], env["personal"], talent_profile_id=env["talent_a"])
    assert rows, "个人侧应能看到本人申请"
    for row in rows:
        assert set(row) == PUBLIC_FIELDS, set(row)
        assert not (set(row) & FORBIDDEN_FIELDS)


def test_public_list_ignores_forged_talent_profile_id(env):
    mine = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    other = _create(env["client"], env["hr"], env["job_b"], env["talent_b"], env["file"])
    rows = _list(env["client"], env["personal"], talent_profile_id=env["talent_b"])
    ids = {r["id"] for r in rows}
    assert mine["id"] in ids
    assert other["id"] not in ids


def test_public_unbound_account_sees_nothing(env):
    _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    assert _list(env["client"], env["unbound"], talent_profile_id=env["talent_a"]) == []


def test_public_hides_unconfirmed_terminal_result(env):
    row = _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    resp = env["client"].patch(
        f"/api/recruitment/applications/{row['id']}/stage",
        json={"internal_stage": "offer_accepted"}, headers=env["hr"],
    )
    assert resp.status_code == 200, resp.text

    public = next(r for r in _list(env["client"], env["personal"]) if r["id"] == row["id"])
    assert public["result_published"] is False
    assert public["result_category"] is None
    # 管理侧仍看到内部结果类别（既有行为不变）
    internal = next(r for r in _list(env["client"], env["hr"]) if r["id"] == row["id"])
    assert internal["result_category"] == "hired"

    resp = env["client"].post(
        f"/api/recruitment/applications/{row['id']}/result-confirm", headers=env["hr"]
    )
    assert resp.status_code == 200, resp.text
    public = next(r for r in _list(env["client"], env["personal"]) if r["id"] == row["id"])
    assert public["result_published"] is True
    assert public["result_category"] == "hired"


def test_management_list_keeps_full_fields(env):
    _create(env["client"], env["hr"], env["job_a"], env["talent_a"], env["file"])
    rows = _list(env["client"], env["hr"])
    assert rows
    assert "internal_stage" in rows[0]
    assert "result_confirmed_by" in rows[0]
    assert "resume_file_id" in rows[0]
    # 完整快照内容依然不进列表出参
    assert "input_snapshot_json" not in rows[0]
