"""Conservative literal source candidates, never invented model citations."""
import re

from sqlalchemy import select
from app.models.resume import ParsedResume, ResumePage


def _candidates(profile, structured):
    basic = structured.get("basic") or {}
    for field in ("name", "phone", "email", "city"):
        value = basic.get(field)
        if value and str(value).strip() == str(getattr(profile, field, None) or "").strip():
            yield "basic." + field, str(value)
    for field in ("degree", "current_title", "expected_title"):
        value = getattr(profile, field, None)
        if value:
            yield "profile." + field, str(value)
    skills = {str(value).casefold() for value in (profile.skills_json or [])}
    for value in structured.get("skills") or []:
        if str(value).casefold() in skills:
            yield "skills", str(value)
    for group, keys in (("educations", ("school", "major")),
                        ("employments", ("company", "title")), ("projects", ("name", "role"))):
        for entry in structured.get(group) or []:
            for key in keys:
                if entry.get(key):
                    yield group, str(entry[key])
    for value in structured.get("certificates") or []:
        yield "certificates", str(value)


def collect_evidence(db, profile, version):
    from app.modules.talents.history import add_evidence
    count = 0
    for parsed in db.scalars(select(ParsedResume).where(ParsedResume.talent_profile_id == profile.id)):
        pages = db.scalars(select(ResumePage).where(ResumePage.resume_file_id == parsed.resume_file_id)
                          .order_by(ResumePage.page_no)).all()
        for field, value in _candidates(profile, parsed.structured_json):
            value = value.strip()
            if len(value) < 2:
                continue
            boundary = r"(?<!\w)" if value[0].isascii() and value[0].isalnum() else ""
            end = r"(?!\w)" if value[-1].isascii() and value[-1].isalnum() else ""
            pattern = re.compile(boundary + re.escape(value) + end, re.IGNORECASE)
            for page in pages:
                if page.quality_status in {"failed", "empty"}:
                    continue
                match = pattern.search(page.text_content)
                if match is None:
                    continue
                start = max(0, match.start() - 50)
                snippet = page.text_content[start:start + 300]
                add_evidence(db, profile.id, version, field, file_id=parsed.resume_file_id,
                             page_no=page.page_no, snippet=snippet,
                             source="ocr" if page.source_type == "ocr" else "text",
                             confidence=float(page.confidence) if page.confidence is not None else None)
                count += 1
                if count >= 200:
                    return
