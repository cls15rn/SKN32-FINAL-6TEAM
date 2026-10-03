# -*- coding: utf-8 -*-
"""위임을 **주고 거두고 보는** 경로 — v11 §12 DoD-18·19 · wiki `teams/booking-handoff.md`.

★**왜 생겼나.** 2026-09-22 에 위임 범위 판정(`delegation.py`)과 `delegations` 표
  (마이그레이션 019)는 만들었는데 **운영자가 누르는 자리가 없었다** — 위임을 주려면 사람이
  손으로 SQL 을 쳐야 했다. wiki 가 그것을 「아직 없는 것」으로 적고 있었다.

★**왜 `app/modules/` 에 있나** — presentation 은 도메인을 import 하지 못한다
  (INV-CS-ARCH-001). 라우터를 여기서 만들고 `composition.build_domain_routers()` 가 앱에
  넣는다. `trip_api.py` 와 같은 모양이다.

★**scope 를 `action:approve` 와 나눈 이유.** 승인은 **제안 한 건**에 "이 변경을 해도 된다"
  이고, 위임은 **서 있는 권한**이다 — 한 번 주면 거둘 때까지 그 고객의 모든 자동 실행이
  한계 안에서 열린다. 영향 범위가 다르면 scope 를 나누는 것이 이 저장소의 방식이다
  (`composer:admin`·`ops:reload` 가 같은 기준으로 갈라졌다). 정의는 `config/guardrails.yaml`
  `security.scopes` 한 곳이다(INV-CS-SEC-007).

★**근거 없이 상태를 바꾸지 않는다.** 주기·거두기 둘 다 **누가(`actor_id`)·왜(`note`)** 를
  받는다. 공백만 보내면 `422` 다 — `/v1/outbox/{id}/resolve` 와 같은 계약이다.

★**거둘 것이 없는데 「거뒀다」고 답하지 않는다.** 살아 있는 위임이 없으면 `409
  no_live_delegation` 이다. 200 으로 조용히 넘기면 운영자는 "눌렀으니 됐겠지" 로 넘어간다
  (CLAUDE.md §3 — 조용한 스킵을 만들지 않는다). 이미 막혀 있다는 사실 자체는 응답이 말한다.

★**한계 값을 여기서 만들지 않는다.** `config/guardrails.yaml` `travel.delegation` 이 정본이고
  (RULE.md §3.1) 이 모듈은 읽어서 **사람이 읽을 이름표를 붙일 뿐**이다. 이름표까지 여기서
  붙이는 이유는 운영 화면(presentation)이 여행 어휘를 몰라야 하기 때문이다 —
  화면은 `label`·`value` 쌍을 받아 표로만 그린다.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.db.session import get_connection
from app.presentation.security import Principal, require_scope

from . import delegation

#: 금액은 DB·설정 모두 **전**(원의 100배)이다. 화면에는 원으로 적는다 — 단위가 섞이면
#: 운영자가 100배 틀린 수를 읽는다.
CENTS_PER_WON = 100


class DelegationChange(BaseModel):
    """주기·거두기 공통 몸통. ★`extra='forbid'` — 조용한 필드 유입을 막는다."""

    model_config = ConfigDict(extra="forbid")
    actor_id: str = Field(min_length=1)
    note: str = Field(min_length=1)


# ★`status` 를 **위치 전용**(`/`)으로 받는다 — `**extra` 에 상세로 `status` 가 들어오면(예: 아직 등록할 수 없는 접수의 현재 상태
#   `IntakeConflict(..., status=...)`) 같은 이름이 둘이라 `TypeError: got multiple values for argument 'status'` 로 409 가 서버 오류(500)가 됐다.
def _error(status: int, code: str, message: str, /, **extra: Any) -> HTTPException:
    return HTTPException(status, {"error": {"code": code, "message": message, **extra}})


def _won(cents: int | None) -> str:
    return "확인되지 않았습니다" if cents is None else f"{cents // CENTS_PER_WON:,}원"


def limits_view(scope: delegation.Scope | None = None) -> list[dict[str, str]]:
    """위임이 **여는 범위**를 사람이 읽는 이름표로. 화면은 이것을 표로만 그린다.

    ★숫자를 여기서 정하지 않는다 — `travel.delegation` 에서 읽은 값에 이름만 붙인다.
    """
    scope = scope or delegation.Scope.from_guardrails()
    return [
        {"key": "max_per_action_cents", "label": "건당 상한",
         "value": _won(scope.max_per_action_cents)},
        {"key": "max_total_cents", "label": "이 고객 누적 상한",
         "value": _won(scope.max_total_cents)},
        {"key": "kinds", "label": "맡기는 대상 종류", "value": " · ".join(scope.kinds)},
        {"key": "max_changes_per_booking", "label": "같은 건에 몇 번까지",
         "value": f"{scope.max_changes_per_booking}회"},
        {"key": "free_cancellation_lead_hours", "label": "무료 취소 구간",
         "value": f"시작 {scope.free_cancellation_lead_hours}시간 전까지"},
        {"key": "revert_window_hours", "label": "되돌림 기한",
         "value": f"실행 뒤 {scope.revert_window_hours}시간(무료 취소 구간 끝보다 늦지 않게)"},
    ]


def _row_view(row: dict[str, Any], scope: delegation.Scope) -> dict[str, Any]:
    """★`customer_id` 말고 다른 고객 식별자를 싣지 않는다(PII — `CLAUDE.md` §1)."""
    remaining = max(0, scope.max_total_cents - int(row.get("spent_cents") or 0))
    return {
        "customer_id": str(row["customer_id"]),
        "state": row["state"],
        "granted_at": row["granted_at"].isoformat() if row.get("granted_at") else None,
        "revoked_at": row["revoked_at"].isoformat() if row.get("revoked_at") else None,
        "granted_by": row.get("granted_by"),
        "revoked_by": row.get("revoked_by"),
        "note": row.get("note"),
        "spent_cents": int(row.get("spent_cents") or 0),
        "spent_label": _won(int(row.get("spent_cents") or 0)),
        "remaining_label": _won(remaining),
    }


def _customer_or_404(conn: Any, *, tenant_id: str, customer_id: UUID) -> None:
    """★다른 테넌트의 고객은 **있는지도 말하지 않는다**(404). 모든 조회에 tenant 조건."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM customers WHERE tenant_id=%s AND customer_id=%s",
                    (tenant_id, str(customer_id)))
        if cur.fetchone() is None:
            raise _error(404, "not_found", "resource not found")


def build_delegation_router() -> APIRouter:
    router = APIRouter()

    @router.get("/v1/delegations")
    def listing(principal: Principal = Depends(require_scope("delegation:read"))):
        """이 테넌트의 위임 전부 + 지금 맡기는 범위.

        ★범위(`limits`)를 목록과 **같은 응답**에 싣는다. 무엇을 여는 것인지 모르는 채로
          버튼을 누르게 하지 않는다.
        """
        scope = delegation.Scope.from_guardrails()
        with get_connection() as conn:
            rows = delegation.listing(conn, tenant_id=principal.tenant_id)
        views = [_row_view(row, scope) for row in rows]
        return {
            "limits": limits_view(scope),
            "rows": views,
            "counts": {
                "live": sum(1 for v in views if v["state"] == delegation.STATE_LIVE),
                "revoked": sum(1 for v in views if v["state"] == delegation.STATE_REVOKED),
                "total": len(views),
            },
        }

    @router.get("/v1/delegations/{customer_id}")
    def detail(customer_id: UUID,
               principal: Principal = Depends(require_scope("delegation:read"))):
        """한 고객의 지금 상태 + **누가 언제 왜 주고 거뒀나**(021, append-only)."""
        scope = delegation.Scope.from_guardrails()
        with get_connection() as conn:
            _customer_or_404(conn, tenant_id=principal.tenant_id, customer_id=customer_id)
            current = delegation.state(conn, tenant_id=principal.tenant_id, customer_id=customer_id)
            events = delegation.history(conn, tenant_id=principal.tenant_id,
                                        customer_id=customer_id)
        view = _row_view({**current, "customer_id": customer_id}, scope)
        view["limits"] = limits_view(scope)
        view["history"] = [{"action": e["action"], "actor_id": e["actor_id"], "note": e["note"],
                            "at": e["at"].isoformat()} for e in events]
        return view

    @router.post("/v1/delegations/{customer_id}/grant")
    def grant(customer_id: UUID, request: DelegationChange,
              principal: Principal = Depends(require_scope("delegation:write"))):
        """위임을 준다. **다시 주면 철회가 풀린다** — 그 사실도 응답이 말한다.

        ★이력은 지워지지 않는다(021). 다시 주기 전의 철회 기록은 그대로 남는다.
        """
        tenant = principal.tenant_id
        with get_connection() as conn, conn.transaction():
            _customer_or_404(conn, tenant_id=tenant, customer_id=customer_id)
            before = delegation.state(conn, tenant_id=tenant, customer_id=customer_id)
            delegation.grant(conn, tenant_id=tenant, customer_id=customer_id,
                             by=request.actor_id, note=request.note)
            after = delegation.state(conn, tenant_id=tenant, customer_id=customer_id)
        view = _row_view({**after, "customer_id": customer_id},
                         delegation.Scope.from_guardrails())
        view["was"] = before["state"]
        view["regranted"] = before["state"] == delegation.STATE_REVOKED
        return view

    @router.post("/v1/delegations/{customer_id}/revoke")
    def revoke(customer_id: UUID, request: DelegationChange,
               principal: Principal = Depends(require_scope("delegation:write"))):
        """위임을 거둔다. 판정은 **적용 순간**에 하므로 승인 뒤에 거둬도 그 건은 열리지 않는다.

        ★거둘 것이 없으면 `409 no_live_delegation` — 「거뒀다」고 답하지 않는다.
          자동 실행이 이미 막힌 상태라는 것은 `state` 가 말한다.
        """
        tenant = principal.tenant_id
        with get_connection() as conn, conn.transaction():
            _customer_or_404(conn, tenant_id=tenant, customer_id=customer_id)
            changed = delegation.revoke(conn, tenant_id=tenant, customer_id=customer_id,
                                        by=request.actor_id, note=request.note)
            after = delegation.state(conn, tenant_id=tenant, customer_id=customer_id)
        if not changed:
            raise _error(409, "no_live_delegation",
                         "거둘 위임이 없습니다 — 이 고객의 자동 실행은 이미 막혀 있습니다",
                         state=after["state"])
        return _row_view({**after, "customer_id": customer_id},
                         delegation.Scope.from_guardrails())

    return router


__all__ = ["build_delegation_router", "limits_view"]
