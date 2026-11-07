"""F10 人才盘点 HTTP 路由（prefix `/api/inventory`，由 A 注册）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.core.response import ok
from app.database import get_db
from app.modules.inventory import schemas, service

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.post("/reviews")
def create_review(payload: schemas.ReviewCreate, user: CurrentUser, db: Session = Depends(get_db)):
    review = service.create_review(db, payload, user)
    db.commit()
    return ok({"id": review.id})


@router.patch("/reviews/{review_id}")
def update_review(review_id: int, payload: schemas.ReviewUpdate, user: CurrentUser, db: Session = Depends(get_db)):
    review = service.update_review(db, review_id, payload, user)
    db.commit()
    return ok({"id": review.id, "grid_cell": review.grid_cell})


@router.post("/reviews/{review_id}/submit")
def submit_review(review_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    review = service.submit_review(db, review_id, user)
    db.commit()
    return ok({"id": review.id, "grid_cell": review.grid_cell})


@router.post("/reviews/{review_id}/confirm")
def confirm_review(review_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    review = service.confirm_review(db, review_id, user)
    db.commit()
    return ok({"id": review.id})


@router.get("/reviews")
def list_reviews(user: CurrentUser, db: Session = Depends(get_db), employee_user_id: int | None = Query(default=None)):
    return ok(service.list_reviews(db, user, employee_user_id=employee_user_id))


@router.get("/reviews/{review_id}")
def get_review(review_id: int, user: CurrentUser, db: Session = Depends(get_db)):
    return ok(service.get_review(db, review_id, user))


@router.get("/summary")
def department_summary(user: CurrentUser, db: Session = Depends(get_db)):
    return ok(service.department_summary(db, user))


@router.post("/pools")
def nominate_pool(payload: schemas.PoolNominate, user: CurrentUser, db: Session = Depends(get_db)):
    pool = service.nominate_pool(db, payload, user)
    db.commit()
    return ok({"id": pool.id})


@router.post("/pools/{pool_id}/decide")
def decide_pool(pool_id: int, payload: schemas.PoolDecide, user: CurrentUser, db: Session = Depends(get_db)):
    pool = service.decide_pool(db, pool_id, payload, user)
    db.commit()
    return ok({"id": pool.id, "status": pool.status})


@router.get("/pools")
def list_pools(user: CurrentUser, db: Session = Depends(get_db)):
    return ok([
        {"id": p.id, "employee_user_id": p.employee_user_id, "pool_type": p.pool_type,
         "status": p.status, "note": p.note}
        for p in service.list_pools(db, user)
    ])


@router.post("/reviews/{review_id}/disputes")
def create_dispute(review_id: int, payload: schemas.DisputeCreate, user: CurrentUser, db: Session = Depends(get_db)):
    dispute = service.create_dispute(db, review_id, payload, user)
    db.commit()
    return ok({"id": dispute.id})


@router.post("/disputes/{dispute_id}/respond")
def respond_dispute(dispute_id: int, payload: schemas.DisputeRespond, user: CurrentUser, db: Session = Depends(get_db)):
    dispute = service.respond_dispute(db, dispute_id, payload, user)
    db.commit()
    return ok({"id": dispute.id, "status": dispute.status})


@router.put("/reviews/{review_id}/idp")
def upsert_idp(review_id: int, payload: schemas.IdpUpsert, user: CurrentUser, db: Session = Depends(get_db)):
    plan = service.upsert_idp(db, review_id, payload, user)
    db.commit()
    return ok({"id": plan.id, "status": plan.status})
