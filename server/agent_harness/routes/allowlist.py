from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models
from ..auth import require_auth
from ..db import get_session
from ..schemas import AllowlistRuleCreate, AllowlistRuleOut

router = APIRouter(
    prefix="/api/allowlist", tags=["allowlist"], dependencies=[Depends(require_auth)]
)


def _to_out(r: models.AllowlistRule) -> AllowlistRuleOut:
    return AllowlistRuleOut(id=r.id, rule=r.rule, project_id=r.project_id, created_at=r.created_at)


@router.get("", response_model=list[AllowlistRuleOut])
def list_rules(
    project_id: str | None = Query(default=None),
    s: Session = Depends(get_session),
) -> list[AllowlistRuleOut]:
    q = s.query(models.AllowlistRule)
    if project_id is not None:
        q = q.filter(
            (models.AllowlistRule.project_id == project_id)
            | (models.AllowlistRule.project_id.is_(None))
        )
    return [_to_out(r) for r in q.order_by(models.AllowlistRule.created_at).all()]


@router.post("", response_model=AllowlistRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: AllowlistRuleCreate, s: Session = Depends(get_session)) -> AllowlistRuleOut:
    if body.project_id is not None and s.get(models.Project, body.project_id) is None:
        raise HTTPException(400, "unknown project")
    rule = body.rule.strip()
    if not rule:
        raise HTTPException(400, "rule cannot be empty")
    existing = (
        s.query(models.AllowlistRule)
        .filter(
            models.AllowlistRule.project_id == body.project_id,
            models.AllowlistRule.rule == rule,
        )
        .first()
    )
    if existing is not None:
        return _to_out(existing)
    r = models.AllowlistRule(project_id=body.project_id, rule=rule)
    s.add(r)
    s.commit()
    s.refresh(r)
    return _to_out(r)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: str, s: Session = Depends(get_session)) -> None:
    r = s.get(models.AllowlistRule, rule_id)
    if r is None:
        raise HTTPException(404, "not found")
    s.delete(r)
    s.commit()
