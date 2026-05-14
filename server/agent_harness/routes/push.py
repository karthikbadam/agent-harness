from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..config import get_settings
from ..db import get_session
from ..schemas import PushSubscribeIn, PushSubscriptionOut, VapidKey

router = APIRouter(prefix="/api/push", tags=["push"])


@router.get("/vapid-public-key", response_model=VapidKey)
def vapid_public_key() -> VapidKey:
    """Public endpoint by design: the public key is meant to be public."""
    s = get_settings()
    if not s.vapid_public_key:
        raise HTTPException(503, "vapid not configured; run agent-harness gen-vapid")
    return VapidKey(public_key=s.vapid_public_key)


@router.post(
    "/subscribe",
    response_model=PushSubscriptionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
def subscribe(body: PushSubscribeIn, s: Session = Depends(get_session)) -> PushSubscriptionOut:
    existing = (
        s.query(models.PushSubscription).filter_by(endpoint=body.endpoint).one_or_none()
    )
    if existing is not None:
        existing.p256dh = body.keys.p256dh
        existing.auth = body.keys.auth
        existing.label = body.label
        s.commit()
        s.refresh(existing)
        sub = existing
    else:
        sub = models.PushSubscription(
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            label=body.label,
        )
        s.add(sub)
        s.commit()
        s.refresh(sub)
    return PushSubscriptionOut(
        id=sub.id, endpoint=sub.endpoint, label=sub.label, created_at=sub.created_at
    )


@router.delete(
    "/subscribe/{sub_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_auth)],
)
def unsubscribe(sub_id: str, s: Session = Depends(get_session)) -> None:
    sub = s.get(models.PushSubscription, sub_id)
    if sub is None:
        raise HTTPException(404, "not found")
    s.delete(sub)
    s.commit()


@router.get(
    "/subscriptions",
    response_model=list[PushSubscriptionOut],
    dependencies=[Depends(require_auth)],
)
def list_subscriptions(s: Session = Depends(get_session)) -> list[PushSubscriptionOut]:
    rows = (
        s.query(models.PushSubscription)
        .order_by(models.PushSubscription.created_at)
        .all()
    )
    return [
        PushSubscriptionOut(id=r.id, endpoint=r.endpoint, label=r.label, created_at=r.created_at)
        for r in rows
    ]
