# -*- coding: utf-8 -*-
"""장소 이름 찾기 — 우리 카탈로그 → (없으면) 카카오로 **존재만** 확인. `read.place_lookup` 의 본체.

    ① `place_catalog` 에서 이름 조회(`db_search/place_by_name.py`)
         found · ambiguous → 그대로 돌려준다
         not_found         → ②
    ② 카카오 키워드 검색 — 실재하는지만 본다
         이름이 맞는 곳이 있다 → `exists_unregistered` (실재하지만 우리 목록에 없다)
         맞는 이름이 없다     → `not_found`           (없음)
         못 물었다(키 없음·예산·시간 초과) → `unknown`  (**없음이 아니다**)

★★약관(카카오 운영정책 제5조) — **카카오 응답은 어디에도 저장하지 않는다.** 이 함수는 카카오가 준 이름·좌표·주소·
  id 를 결과에 싣지 않는다(결과는 Case 의 근거로 저장된다). 싣는 것은 「있다/없다/못 물었다」 판정뿐이다.
★이름이 맞는지는 `intake.places._kakao_match` 와 같은 규칙으로 본다 — 같은 이름이거나, 질의로 시작하는 이름이
  **하나뿐**일 때. 카카오 1위가 아무 가게여도 「있다」고 하지 않는다.
★없는 곳을 DB 에 올리는 방법은 아직 정해지지 않았다 — 여기서는 판정까지만 하고 쓰지 않는다.
"""
from __future__ import annotations

from typing import Any, Callable

from app.modules.travel_ops.intake.places import _kakao_match

from .db_search.place_by_name import find_place_by_name


def lookup_place(connection_factory: Callable[[], Any], tenant_id: str, name: str | None,
                 kakao: Any | None) -> dict[str, Any]:
    """항상 `status` 가 있는 dict. `status`: found · ambiguous · exists_unregistered · not_found · unknown."""
    found = find_place_by_name(connection_factory, tenant_id, name)
    if found["status"] != "not_found" or found.get("reason") == "name_too_short":
        return found

    if kakao is None:
        # ★키가 없어 못 물었다 — 「없다」고 말할 근거가 없다
        return {"status": "unknown", "via": "place_catalog", "reason": "kakao_unavailable"}
    hits = kakao.search((name or "").strip())
    if hits is None:
        misses = getattr(kakao, "misses", None) or {}
        return {"status": "unknown", "via": "kakao", "reason": "kakao_blocked",
                "detail": max(misses, key=misses.get) if misses else None}
    if _kakao_match((name or "").strip(), hits) is not None:
        return {"status": "exists_unregistered", "via": "kakao"}
    return {"status": "not_found", "via": "kakao"}
