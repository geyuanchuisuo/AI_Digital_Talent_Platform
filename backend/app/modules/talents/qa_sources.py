"""Version-frozen page chunks; legacy versions use their own evidence only."""
from app.models.resume import ResumePage, ResumeFile, ParsedResume
from sqlalchemy import select


def freeze_pages(db, person_id):
    pages=[]
    total=0
    rows=db.execute(select(ResumePage,ResumeFile).join(ResumeFile,ResumeFile.id==ResumePage.resume_file_id)
        .join(ParsedResume,ParsedResume.resume_file_id==ResumeFile.id)
        .where(ParsedResume.talent_profile_id==person_id)
        .order_by(ResumeFile.id,ResumePage.page_no)).all()
    for page,file in rows:
        if page.quality_status in ('failed','empty') or not page.text_content.strip():
            continue
        total+=len(page.text_content)
        if total>2_000_000:
            raise ValueError('档案原文超过版本快照上限，需拆分后处理')
        pages.append(dict(file_id=file.id,filename=file.filename,
            page_no=None if file.file_ext=='docx' else page.page_no,
            locator_type='document_text' if file.file_ext=='docx' else 'page',
            text=page.text_content,source=page.source_type,quality_status=page.quality_status,
            confidence=float(page.confidence) if page.confidence is not None else None))
    return pages


def chunks(snapshot):
    if not snapshot.get('source_pages'):
        return list(snapshot.get('evidence',[]))
    rows=[]
    for page in snapshot['source_pages']:
        text=page['text']
        for start in range(0,len(text),500):
            end=min(start+600,len(text))
            rows.append({**{k:v for k,v in page.items() if k!='text'},
                'snippet':text[start:end],'char_start':start,'char_end':end,
                'field_path':'source_text','review_status':'pending_review'})
            if end==len(text):break
    if len(rows)>4000:
        raise ValueError('档案片段数量过多，需拆分后检索')
    return rows
