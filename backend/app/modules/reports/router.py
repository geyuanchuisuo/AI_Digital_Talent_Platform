"""报告导出路由（T-D11）。

GET /api/reports/snapshots/{snapshot_id}/export?format=docx|pdf
导出前重新校验权限（创建者或管理侧）；每次导出写 snapshot_export_audit。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.api.deps import CurrentUser
from app.modules.reports import service

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/snapshots/{snapshot_id}/export", summary="同快照报告导出（DOCX/PDF，不重新问数）")
async def export_snapshot(
    snapshot_id: int,
    user: CurrentUser,
    format: str = Query("docx", description="docx | pdf"),
):
    payload, filename, media = await service.export_snapshot(user, snapshot_id, format)
    return Response(
        content=payload,
        media_type=media,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Snapshot-Id": str(snapshot_id),
        },
    )
