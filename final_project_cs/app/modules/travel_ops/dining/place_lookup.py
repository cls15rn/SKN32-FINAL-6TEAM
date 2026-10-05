"""일정 접수의 이름 찾기에 넣는 요식 원장 — 관광공사 자리와 같은 계약(`find` + `misses`). `[2026-10-01]`

★미리 적재해 둔 원장 DB 만 읽는다. 외부 API 가 아니다. 원장에 없으면 이름 찾기가 다음 단계(카카오)로 간다.
★못 읽으면(요식 표가 없는 DB · 연결 실패) `misses["unavailable"]` 를 센다 — 이름 찾기가 「없는 곳」이 아니라
  「조회가 막혔다」로 적는다(`intake/places.py` 의 NOT_THERE 밖의 사유).
"""
from __future__ import annotations

from typing import Any, Callable

from .ledger import find_place_by_name


class LedgerPlaceLookup:
    name = "dining_ledger"

    def __init__(self, connection_factory: Callable[[], Any],
                 on_found: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._connect = connection_factory
        #: 찾은 가게를 알린다 — 일정 접수가 그 좌표를 다음 항목의 근처 힌트로 쓴다(`trip_api._PlaceCtx`)
        self._on_found = on_found
        self.misses: dict[str, int] = {}

    def find(self, place_name: str, *, area_code: str | None = None, **_: Any) -> dict[str, Any] | None:
        try:
            with self._connect() as conn:
                found = find_place_by_name(conn, place_name)
        except Exception:   # noqa: BLE001 — 드라이버를 import 하지 않는다(Team 경계). 못 읽으면 막힌 것
            self.misses["unavailable"] = self.misses.get("unavailable", 0) + 1
            return None
        if found is None:
            self.misses["not_found"] = self.misses.get("not_found", 0) + 1
        elif self._on_found is not None:
            self._on_found(found)
        return found
