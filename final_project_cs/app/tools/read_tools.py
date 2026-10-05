"""Tenant-scoped, named read tools for Team modules.

The tool layer deliberately exposes no SQL interface.  A Team supplies only
the tool name and business arguments; tenant/customer scope is taken from the
validated ContextPack.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from app.core.contracts import ContextPack, ToolNotAllowed
from app.infrastructure.rag.retriever import search_policy


class ToolBudgetExceeded(RuntimeError):
    """도구 호출이 `manifest.max_steps` 예산을 넘었다.

    ★`ToolLoopExceeded` 와 **다른 예외다.** 원인이 다르기 때문이다 —
      전자는 "같은 것을 또 불렀다"(중복), 이쪽은 "너무 많이 불렀다"(예산).
      한 예외로 묶으면 로그에서 둘을 못 가른다.

    ★2026-09-09 신설. 그전까지 `max_steps` 는 **선언만 되고 아무도 강제하지
      않았다** — 정의·화면표시·introspection 에만 있고 실행 경로 3곳
      (executor·controller·read_tools)에 0회였다. 테스트까지 있었지만
      `manifest.max_steps == 6` 이라는 **선언값**만 봤다.
      경위: wiki/records/reports/debugs/2026-09-09_max_steps가_강제되지_않는다.md
    """


class ToolLoopExceeded(RuntimeError):
    """The same named tool and normalized arguments were requested twice."""


def _as_datetime(value: Any) -> Any:
    """문자열로 온 시각을 `datetime` 으로. ★못 읽으면 `None` — 지금으로 대체하지 않는다.

    지금 시각으로 대체하면 「내일 오후 골프」를 물었는데 오늘 날씨로 답한다.
    """
    from datetime import datetime

    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass
class ToolContext:
    tenant_id: str
    customer_id: UUID
    case_id: UUID
    knowledge_scope: list[str]

    @classmethod
    def from_pack(cls, pack: ContextPack) -> "ToolContext":
        customer = pack.current_state.get("customer_id")
        if customer is None:
            raise ValueError("ContextPack.current_state.customer_id is required for read tools")
        return cls(pack.tenant_id, UUID(str(customer)), pack.case_id, pack.knowledge_scope)


@dataclass
class ReadToolbox:
    """Named database operations with injectable connection and policy search."""

    connection_factory: Callable[[], Any]
    policy_search: Callable[..., list[Any]] = search_policy
    #: 바깥 데이터 소스 묶음(`app/infrastructure/travel/`). ★기본값이 `None` 이다 —
    #:  **안 넣으면 여행 도구가 전부 「모름」을 돌려주고 네트워크를 타지 않는다.**
    #:  테스트가 조용히 바깥으로 나가는 사고를 구조로 막는다.
    travel: Any | None = None
    #: ★`[결정 2026-09-17]` 성립 점검기를 갈아 끼우는 자리. 없으면 `travel` 로 만든다.
    #:  시나리오(재생 소스·한도)와 실운영이 **같은 도구 이름**으로 다른 점검기를 쓴다.
    check: Callable[..., dict[str, Any]] | None = None
    #: 경로 사건 소스. 없으면 `travel.route_events` 를 쓴다.
    route_events: Any | None = None
    #: 고객 문장에서 신고 내용(늦음·휴무·품절·재요청)을 뽑는 함수. 없으면 「모름」.
    report_extractor: Callable[[str], dict[str, Any] | None] | None = None
    #: 카카오 로컬(키워드 검색). ★`read.place_lookup` 의 **존재 확인**에만 쓰고 응답은 저장하지 않는다.
    #:  없으면(`None`) 존재를 못 물은 것이라 「없음」이 아니라 「모름」으로 답한다.
    kakao: Any | None = None
    #: ★`[2026-09-30]` 구글 장소(`GooglePlaces`) — 식당 가격(`read.place_price`)만 쓴다. 없으면 「모름」.
    google_places: Any | None = None

    def _one(self, sql: str, params: tuple[Any, ...], columns: tuple[str, ...]) -> dict[str, Any] | None:
        with self.connection_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        return None if row is None else dict(zip(columns, row))

    # ─────────────────────────────────────────────────────────────
    # 도메인 무관 도구
    #
    # ★2026-09-10 — 여기 있던 커머스 도구 다섯(`read.order`·`read.shipment`·
    #   `read.catalog`·`read.order_items`·`read.return`)을 걷어냈다.
    #   `orders`·`order_items`·`shipments`·`returns`·`products` 를 읽던
    #   함수들이고, 등록된 여행 Team 여섯 중 **아무도 선언하지 않는다**
    #   (`build_registry().manifests()` 로 확인). 남겨 두면 도구 표가
    #   "쓸 수 있는 것" 이 아니라 "옛날에 쓰던 것" 목록이 된다.
    #
    # ★기록해 둔다 — `wiki/records/handoff/10_도메인_교체_가이드.md` §1 은 이
    #   파일을 교체 지점으로 놓지 않았다. basement 순수성 게이트가 `app/tools/`
    #   를 대상에서 빼먹었기 때문이다. 실제로 이 파일은 `app/modules/` 와
    #   마찬가지로 **도메인을 안다.** 게이트 확장은 별도 작업으로 남아 있다.
    def policy(self, scope: ToolContext, *, query: str, **_: Any) -> list[Any]:
        return self.policy_search(scope.tenant_id, query, scope.knowledge_scope)

    def account(self, scope: ToolContext, **_: Any) -> dict[str, Any] | None:
        # ★`customers` 는 core 테이블이다(001_schema.sql). 도메인 유출이 아니다.
        # ★`[미확보]` 2026-09-10 현재 이 도구를 `allowed_tools` 에 적은 Team 은
        #   **없다**(등록 여섯의 manifest 를 세어 확인). 도메인 무관이라 남겨
        #   두지만, 계속 아무도 안 쓰면 지운다.
        return self._one(
            "SELECT customer_id, external_id, email_hash, created_at FROM customers WHERE tenant_id=%s AND customer_id=%s",
            (scope.tenant_id, scope.customer_id),
            ("customer_id", "external_id", "email_hash", "created_at"),
        )

    # ─────────────────────────────────────────────────────────────
    # 여행 도메인 도구 (v10 §5-A · §7 "Mock 허용")
    #
    # ★지금은 전부 **"모름"을 돌려준다.** 지어낸 값을 주지 않는다 —
    #   Team 은 모르면 `escalated` 로 가야 하고, 그 갈래가 처음부터
    #   돌아야 나중에 실데이터를 꽂았을 때 무엇이 달라지는지 알 수 있다.
    #   빈 dict 가 아니라 `None` 인 이유: 빈 값은 "없다"로 읽히고
    #   `None` 은 "모른다"로 읽힌다. 이 도메인에서 둘은 다르다.
    #
    # ★실데이터를 꽂을 때 **확인 시각(confirmed_at)과 출처를 같이** 담는다.
    #   제공자의 영업시간은 "오늘부터 며칠의 예정"이지 조회 순간의 확인이
    #   아니다(v10 §4-D). 재조회 시각을 현장 관찰처럼 표시하면 안 된다.
    def _travel_tools(self) -> dict[str, Any]:
        return {
            "read.booking":  self.booking,
            # ★수치(취소 기한·위약금율)는 여기서 온다. `read.policy` 는 문장 근거만 댄다
            "read.booking_terms": self.booking_terms,
            "read.disruptions": self.disruptions,
            "read.weather_warning": self.weather_warning,
            "read.travel_advisory": self.travel_advisory,
            "read.place":    self.place,
            "read.place_search": self.place_search,
            "read.place_candidates": self.place_candidates,
            "read.place_lookup": self.place_lookup,
            # ★요식 원장. `read.place` 와 달리 **시각을 받는다** —
            #   「그 시각에 여는가」는 시각이 있어야 답할 수 있다.
            "read.dining_state": self.dining_state,
            "read.dining_states": self.dining_states,
            # ★`[2026-09-30]` 식당 가격(구글 1인당 범위 · 가격대) — 대안을 세울 때만. 값은 비교에만 쓰고 버린다(구글 약관)
            "read.place_price": self.place_price,
            "read.weather":  self.weather,
            "read.disaster": self.disaster,
            "read.route":    self.route,
            "read.transit":  self.transit,
            "read.supplier": self.supplier,
            "read.holiday":  self.holiday,
            # ★`[결정 2026-09-17]` Case 버전의 일정 관리 — 일정·옛 버전·장소 목록·경로 사건·신고 내용
            "read.itinerary": self.itinerary,
            "read.itinerary_version": self.itinerary_version,
            "read.place_catalog": self.place_catalog,
            "read.route_events": self.route_events_for,
            "read.customer_report": self.customer_report,
        }

    #: 여행 예약의 컬럼. ★`_one()` 이 zip 으로 붙이므로 SELECT 순서와 **같아야** 한다.
    _BOOKING_COLUMNS = ("booking_id", "booking_no", "kind", "status", "starts_at",
                        "party_size", "capacity", "amount_cents", "locked", "place_id")
    _PLACE_COLUMNS = ("place_id", "name", "kind", "latitude", "longitude",
                      "weather_sensitive", "confirmed_at", "open_at_slot",
                      "dietary", "dietary_absent",
                      "source_content_id", "source_content_type_id")

    def booking(self, scope: ToolContext, *, booking_id: str | None = None,
                **_: Any) -> dict[str, Any] | None:
        """예약 한 건. 없으면 `None`(모름).

        ★`booking_id` 를 주면 그 건을, 안 주면 **가장 임박한** 예약을 본다.
          「가장 최근에 만든 것」이 아니다 — 여행 CS 에서 문제가 되는 것은
          다가오는 일정이지 방금 만든 예약이 아니다.

        ★tenant·customer 조건을 뺀 조회는 그 자체가 보안 결함이다
          (`CLAUDE.md` §1, `tests/security/`).
        """
        select = ("SELECT booking_id, booking_no, kind, status, starts_at, party_size, "
                  "capacity, amount_cents, locked, place_id FROM bookings "
                  "WHERE tenant_id=%s AND customer_id=%s")
        if booking_id is not None:
            return self._one(select + " AND booking_id=%s",
                             (scope.tenant_id, scope.customer_id, booking_id),
                             self._BOOKING_COLUMNS)
        return self._one(select + " ORDER BY starts_at ASC LIMIT 1",
                         (scope.tenant_id, scope.customer_id), self._BOOKING_COLUMNS)

    #: 취소 조건을 찾는 순서 — 좁은 것부터. v11 결정 15 「값마다 대체 소스를 둔다」를
    #: 데이터로 구현한 것이다(마이그레이션 023).
    _TERM_SCOPES = ("booking", "supplier", "kind")
    _TERM_COLUMNS = ("scope_type", "scope_id", "cancel_deadline_hours",
                     "penalty_by_hours", "source", "observed_at")

    def booking_terms(self, scope: ToolContext, *, booking_id: str | None = None,
                      **_: Any) -> dict[str, Any] | None:
        """이 예약의 **취소 조건**. 없으면 `None`(모름). `[2026-09-23]`

        ★★**왜 도구가 따로 있나.** 전에는 `activity` 가 취소 기한·위약금율을 `read.policy`
          가 준 RAG 청크에서 꺼내려 했다. 청크는 `PolicyChunk` 이고 코드는 `dict` 를 보고
          있어서 **두 값이 언제나 `None`** 이었다 — 어떤 코퍼스를 넣어도 그랬다
          (`wiki/records/reports/debugs/2026-09-22_정책청크에서_수치를_못_꺼낸다.md`).
          이제 **수치는 이 도구가, 문장 근거는 `read.policy` 가** 댄다.

        ★찾는 순서는 예약 → 공급자 → 종류다. 좁은 것이 이긴다 — 이 예약에 따로 적힌
          조건이 있으면 공급자 기본값을 덮는다. 셋 다 없으면 `None` 이고,
          **모름이면 부르는 쪽이 금액을 만들지 않는다.**

        ★`matched_scope` 를 같이 돌려준다. 「이 예약의 조건」인지 「종류 기본값」인지를
          받는 쪽이 알아야 답변의 확신도가 달라진다. `source` 는 어디서 온 값인가다 —
          시연용 Mock 과 실제 업체 약관이 섞이면 고객에게 지어낸 금액을 말하게 된다.
        """
        booking = self.booking(scope, booking_id=booking_id)
        if booking is None:
            return None
        keys = {"booking": str(booking.get("booking_id")), "kind": booking.get("kind")}
        supplier = self._one(
            "SELECT supplier FROM supplier_bookings WHERE tenant_id=%s AND booking_id=%s "
            "ORDER BY confirmed_at DESC NULLS LAST LIMIT 1",
            (scope.tenant_id, booking.get("booking_id")), ("supplier",))
        if supplier is not None:
            keys["supplier"] = supplier["supplier"]
        for scope_type in self._TERM_SCOPES:
            scope_id = keys.get(scope_type)
            if not scope_id:
                continue
            found = self._one(
                "SELECT scope_type, scope_id, cancel_deadline_hours, penalty_by_hours, "
                "source, observed_at FROM cancellation_terms "
                "WHERE tenant_id=%s AND scope_type=%s AND scope_id=%s",
                (scope.tenant_id, scope_type, str(scope_id)), self._TERM_COLUMNS)
            if found is not None:
                return {"booking_id": str(booking.get("booking_id")),
                        "matched_scope": found["scope_type"],
                        "cancel_deadline_hours": float(found["cancel_deadline_hours"]),
                        "penalty_by_hours": found["penalty_by_hours"] or {},
                        "source": found["source"], "observed_at": found["observed_at"]}
        return None

    def place(self, scope: ToolContext, *, place_id: str | None = None,
              **_: Any) -> dict[str, Any] | None:
        """장소·운영 정보. 없으면 `None`(모름).

        ★`weather_sensitive`·`open_at_slot` 은 NULL 일 수 있고 그건 **모름**이다.
          받는 쪽이 NULL 을 「아니다」로 읽으면 안 된다 — 그 구분을 Team 이 한다.

        ★`confirmed_at` 은 「영업 정보를 확인한 시각」이다. 이 값이 없으면
          Team 은 "확인했다" 고 말하지 않는다(v10 §4-D).
        """
        if place_id is None:
            return None      # ★어느 장소인지 모르면 조회하지 않는다
        row = self._one(
            "SELECT place_id, name, kind, latitude, longitude, weather_sensitive, "
            "hours_confirmed_at, open_at_slot, dietary, dietary_absent, "
            "source_content_id, source_content_type_id FROM places "
            "WHERE tenant_id=%s AND place_id=%s",
            (scope.tenant_id, place_id), self._PLACE_COLUMNS)
        return self._fill_operating(self._fill_coordinates(row))

    def _fill_operating(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        """TourAPI 운영시간 **원문**을 붙인다. ★boolean으로 만들지 않는다.

        `tour_api.py`의 `operating()`이 이미 그렇게 설계돼 있다 — `usetime`·
        `restdate`는 자연어이고 예외 조건(「화요일 휴무, 단 공휴일과 겹치면
        개방」류)이 섞여 있어 규칙으로 펴면 하나 틀린 게 고객을 문 닫힌 곳
        앞에 세운다. 여기서도 파싱하지 않는다 — `place["operating"]`에
        원문 그대로 실어 Team에 넘기고, Team은 그걸 판정이 아니라 **안내
        문구**로만 쓴다(activity.py 참고).

        ★신원(`source_content_id`)이 아직 해소 안 됐거나(013) TourAPI 소스가
          안 붙어 있으면(키 없음) **채우지 않는다** — 모름을 모름으로 둔다.
        """
        if row is None:
            return None
        content_id = row.get("source_content_id")
        content_type_id = row.get("source_content_type_id")
        source = getattr(self.travel, "place", None) if self.travel else None
        if source is None or not content_id or not content_type_id:
            return row
        operating = source.operating(str(content_id), str(content_type_id))
        if operating is not None:
            row["operating"] = operating
        return row

    def dining_state(self, scope: ToolContext, *, place_id: str | None = None,
                     at: Any = None, until: Any = None, **_: Any) -> dict[str, Any] | None:
        """그 시각 그 장소의 요식 판정. 없으면 `None`(모름).

        ★`read.place` 와 달리 **시각을 받는다.** `places.open_at_slot` 은 칸
          하나라 어느 예약이든 같은 값이고, 12시 예약과 22시 예약을 가를 수 없다.

        ★코어 표를 읽지도 쓰지도 않는다. 코어 `place_id` 를 요식 원장 장소로
          바꾸는 것은 `dining.dn_core_place_link` 이고 그것도 요식 표다.

        ★`open_at_slot` 이 NULL 이면 **모름**이다. 받는 쪽이 「아니다」로 읽으면
          안 된다 — 그 구분은 Team 이 한다.
        """
        from app.modules.travel_ops.dining.ledger import dining_state
        with self.connection_factory() as conn:
            return dining_state(conn, scope.tenant_id, place_id, at, until)

    def dining_states(self, scope: ToolContext, *, slots: list[dict[str, Any]],
                      **_: Any) -> dict[str, dict[str, Any] | None]:
        """대체 식당들의 방문 시간대를 한 번에 읽는다. 테넌트는 검증된 scope에서만 받는다."""
        from app.modules.travel_ops.dining.ledger import dining_states

        with self.connection_factory() as conn:
            return dining_states(conn, scope.tenant_id, slots)

    def place_price(self, scope: ToolContext, *, place_ids: list[str] | None = None,
                    **_: Any) -> dict[str, dict[str, int | None] | None] | None:
        """장소들의 구글 가격 {place_id: {"level", "low", "high"} 또는 None}. 구글이 꺼져 있으면 `None`(모름).

        ★`[2026-09-30 사용자 결정]` 하루 상한 없이 부르고, 월 무료 한도를 넘으면 운영자에게 알린다
          (`GooglePlaces.price`). ★돌려준 값을 근거·기록에 그대로 싣지 않는다 — 비교 결과만 남긴다.
        """
        if self.google_places is None or not place_ids:
            return None
        from app.infrastructure.travel.google_places import prices_for

        return prices_for(self.connection_factory, scope.tenant_id, self.google_places, list(place_ids))

    def _fill_coordinates(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        """좌표가 비었으면 국가유산청에서 채운다. ★**어디서 왔는지 남긴다.**

        ★좌표가 없으면 `read.weather` 가 아무것도 못 한다 — 「어디인지 모르는
          곳의 날씨」는 없다. 그래서 채울 수 있으면 채운다.

        ★★**출처를 안 남기면 안 된다.** 우리 DB 값과 바깥에서 온 값이 한
          dict 에 섞이는데, 나중에 좌표가 틀렸을 때 어디를 고쳐야 하는지
          알 수 없게 된다. `coordinates_source` 가 그걸 말한다.
        """
        if row is None:
            return None
        if row.get("latitude") is not None and row.get("longitude") is not None:
            row["coordinates_source"] = "db"
            return row
        source = getattr(self.travel, "heritage", None) if self.travel else None
        if source is None or not row.get("name"):
            return row      # ★못 채우면 비운 채 둔다. 지어내지 않는다
        found = source.locate(str(row["name"]))
        if found is None:
            return row
        row["latitude"] = found["latitude"]
        row["longitude"] = found["longitude"]
        row["coordinates_source"] = found["source"]
        row["coordinates_confirmed_at"] = found["confirmed_at"]
        return row

    def place_search(self, scope: ToolContext, *, name: str | None = None,
                     kind: str | None = None, **_: Any) -> dict[str, Any] | None:
        """이름으로 장소 후보 하나를 찾는다(공급자 카탈로그, **우리 DB 조회가 아니다**).

        ★`place()`와 다르다 — `place()`는 **이미 아는** `place_id`로 우리
          `places` 테이블을 읽고, 이건 **아직 모르는** 장소를 이름만으로
          TourAPI에서 찾는다. 「일정 제출」처럼 고객이 새로 말한 장소를
          다루는 자리에서 쓴다.

        ★애매하면 **`None`(모름)** — 확정하지 않는다. `tour_api.py.find()`가
          이미 이 규율을 지킨다(제목 정확일치 1건일 때만 확정, 2건 이상이면
          "경복궁"이 서울 궁궐·울산 음식점으로 갈리는 것처럼 애매함 자체를
          답으로 준다). 여기서 그 규율을 느슨하게 만들지 않는다.

        ★`kind`(activity/dining/lodging/flight)를 주면 그 종류로만 좁혀
          받는다 — `watch.py`의 `KIND_TO_CONTENT_TYPES`와 같은 매핑을 쓴다.
        """
        if self.travel is None or self.travel.place is None:
            return None
        if not name or not name.strip():
            return None
        # ★`watch.py`의 `KIND_TO_CONTENT_TYPES`와 같은 매핑이다. Team 내부
        #   모듈을 이 파일에서 import하지 않으려고(basement가 Team을 모르는
        #   경계) 작게 복제한다 — 값이 갈라지면 나란히 있는 두 자리가 서로
        #   드러내 준다.
        allowed = {"activity": {"12", "14", "28", "38"}, "dining": {"39"},
                  "lodging": {"32"}, "flight": set()}.get(kind or "")
        narrow = next(iter(allowed)) if allowed and len(allowed) == 1 else None
        return self.travel.place.find(
            name.strip(), content_type_id=narrow, allowed_types=allowed or None)

    def place_lookup(self, scope: ToolContext, *, name: str | None = None,
                     **_: Any) -> dict[str, Any] | None:
        """고객이 말한 장소 이름 → 우리 카탈로그, 없으면 카카오로 **존재만** 확인한다.

        반환 `status`: `found` · `ambiguous` · `exists_unregistered` · `not_found` · `unknown`.
        ★`read.place_search` 와 달리 「없음」과 「못 물어봄」을 가른다(`not_found` ≠ `unknown`).
        ★카카오 응답(이름·좌표·주소)은 결과에 싣지 않는다 — 결과는 Case 근거로 저장되기 때문이다.
        이름이 비면 `None`(모름). 본체는 `activity/place_lookup.py`.
        """
        if not name or not name.strip():
            return None
        from app.modules.travel_ops.activity.place_lookup import lookup_place

        return lookup_place(self.connection_factory, scope.tenant_id, name, self.kakao)

    def place_candidates(self, scope: ToolContext, *, content_id: str | None = None,
                         **_: Any) -> dict[str, Any] | None:
        """대체 장소 후보 풀. `place_catalog`(TourAPI 적재분)를 읽는다.

        ★실제 조회는 `app/modules/travel_ops/activity/db_search/place_candidates.py` 가 한다 — 여기는 테넌트
          범위를 넘겨 부르는 얇은 연결이다. 원래 장소가 카탈로그에 없으면
          `None`(모름)이고 Team 은 「후보를 조회하지 못했다」로 답한다.

        인자: `content_id` — 문제있음 판정이 난 원래 장소의 TourAPI
        `contentid`(`read.place` 의 `source_content_id`). 없으면 `None`.

        반환 모양(행은 CSV·TourAPI 컬럼명 그대로 — `alternatives.py` 가 읽는다)::

            {"origin":     {"contentid", "title", "contenttypeid",
                            "lclsSystm1", "lclsSystm2", "lclsSystm3",
                            "sigungucode", "brand", "mapx", "mapy",
                            "closed_days", "business_hours"},
             "candidates": [<origin 과 같은 모양의 행>, ...],
             "source": "...", "confirmed_at": "..."}

        ★후보 풀을 유사도 필드로 **미리 좁히지 않는다** — 폴백이 필드를
          하나씩 풀 수 있어야 한다. 원래 장소 좌표 기준 최대 반경(10km)의
          바운딩 박스로만 좁힌다(`[2026-10-02]` 시군구 대신 반경).
        ★원래 장소 행(`origin`)을 모르면 `None` — 유사도를 잴 기준이 없다.
        """
        if not content_id:
            return None      # ★어느 장소인지 모르면 조회하지 않는다
        from app.modules.travel_ops.activity.db_search.place_candidates import find_place_candidates

        return find_place_candidates(self.connection_factory, scope.tenant_id, content_id)

    def holiday(self, scope: ToolContext, *, on: Any = None,
                **_: Any) -> dict[str, Any] | None:
        """그 날짜가 공휴일인가. 모르면 `None`.

        ★★**「그 장소가 쉬는가」는 답하지 않는다.** 공휴일에 여는 식당도 많고
          공휴일에만 여는 곳도 있다. 둘을 같게 다루면 근거 없는 확정이 된다 —
          응답의 `answers_whether_the_place_is_closed` 가 그 사실을 들고 있다.
        """
        from datetime import date, datetime

        if self.travel is None or self.travel.holiday is None:
            return None
        when = _as_datetime(on) if not isinstance(on, date) or isinstance(on, datetime) else on
        if isinstance(when, datetime):
            when = when.date()
        if not isinstance(when, date):
            return None      # ★언제인지 모르면 묻지 않는다
        return self.travel.holiday.is_holiday(when)

    def weather(self, scope: ToolContext, *, latitude: float | None = None,
                longitude: float | None = None, at: Any = None,
                **_: Any) -> dict[str, Any] | None:
        """그 좌표·그 시각의 기상 예보. 모르면 `None`.

        ★**좌표를 모르면 묻지 않는다.** 「어디인지 모르는 곳의 날씨」는 없다.
          좌표는 예약(`read.booking`)이나 장소(`read.place`)에서 온다.

        ★`place_id` 만 주는 호출도 받는다(인자를 `**_` 로 흘린다). 그때는
          좌표가 없으므로 `None` = 모름이다. 장소 소스가 붙으면 그 자리에서
          좌표를 얻어 넘기게 바꾼다.

        ★반환값은 **예보**다(`kind="forecast"`). 관찰이 아니다 — Team 이
          문구를 그렇게 쓴다(v10 §4-D).
        """
        if self.travel is None or self.travel.weather is None:
            return None
        if latitude is None or longitude is None:
            return None
        return self.travel.weather.forecast(
            latitude=float(latitude), longitude=float(longitude),
            at=_as_datetime(at))

    def disaster(self, scope: ToolContext, *, latitude: float | None = None,
                 longitude: float | None = None, at: Any = None,
                 **_: Any) -> dict[str, Any] | None:
        """그 좌표 인근·그 시각 기준의 재난문자 목록. 모르면 `None`.

        ★`weather()`와 같은 규율이다 — **좌표를 모르면 묻지 않는다.**
          "어디인지 모르는 곳의 재난"은 없다.

        ★`[구현 2026-09-20]` `DisasterMsgSource`(`disaster_msg.py`)가 생겼고
          `build_travel_sources()`가 `ACOP_DISASTER_API_KEY`(또는 공통 키)가
          있으면 조립한다. 키가 없으면 `self.travel.disaster`는 여전히 `None`
          이고 이 함수는 `None`을 돌려준다 — 모름은 정상 갈래다.
          ★★실 키로 검증되지 않았다(`disaster_msg.py` 모듈 docstring 참고).

        ★반환 모양: `{"messages": [{"SN":...,
          "EMRG_STEP_NM":...,"DST_SE_NM":...,"MSG_CN":...}, ...],
          "confirmed_at":..., "source":...}`. 재난문자 원문(`MSG_CN`)은
          자연어다 — TourAPI 운영시간과 같은 이유로 Team은 이걸 통으로
          해석하지 않는다(`activity.py`의 `_disaster_blocks()` 참고).
        """
        if self.travel is None or self.travel.disaster is None:
            return None
        if latitude is None or longitude is None:
            return None
        return self.travel.disaster.near(
            latitude=float(latitude), longitude=float(longitude),
            at=_as_datetime(at))

    # ── 일정(Trip) ──────────────────────────────────────────────
    def _trip_store(self, scope: ToolContext):
        from app.modules.travel_ops.itinerary import TripStore

        return TripStore(scope.tenant_id)

    def itinerary(self, scope: ToolContext, *, trip_id: Any = None,
                  **_: Any) -> dict[str, Any] | None:
        """그 여행의 **최신 일정 버전**. 이 고객의 여행이 아니면 `None`(있는지도 말하지 않는다)."""
        if not trip_id:
            return None
        from app.modules.travel_ops.itinerary import item_to_dict

        store = self._trip_store(scope)
        with self.connection_factory() as conn:
            try:
                trip, items = store.latest(conn, UUID(str(trip_id)))
            except (KeyError, ValueError):
                return None
        if str(trip["customer_id"]) != str(scope.customer_id):
            return None
        return {"trip": {"trip_id": str(trip["trip_id"]), "version": trip["version"],
                         "title": trip["title"], "locale": trip["locale"],
                         "party_size": trip["party_size"], "constraints": trip["constraints"] or {}},
                "items": [item_to_dict(item) for item in items]}

    def itinerary_version(self, scope: ToolContext, *, trip_id: Any = None, version: Any = None,
                          **_: Any) -> dict[str, Any] | None:
        """옛 일정 버전의 항목(되돌림용). 이 고객의 여행이 아니거나 없는 버전이면 `None`."""
        if not trip_id or version is None:
            return None
        from app.modules.travel_ops.itinerary import item_to_dict

        store = self._trip_store(scope)
        with self.connection_factory() as conn:
            try:
                trip, _ = store.latest(conn, UUID(str(trip_id)))
                if str(trip["customer_id"]) != str(scope.customer_id):
                    return None
                wanted = int(version)
                if not 1 <= wanted <= trip["version"]:
                    return None
                items = store.items(conn, UUID(str(trip_id)), wanted)
            except (KeyError, ValueError):
                return None
        return {"trip_id": str(trip_id), "version": wanted,
                "items": [item_to_dict(item) for item in items]}

    def place_catalog(self, scope: ToolContext, **_: Any) -> list[dict[str, Any]] | None:
        """이 테넌트의 장소 목록 — 대안 후보를 고르는 재료. ★빈 목록은 「장소가 없다」는 아는 사실이다."""
        store = self._trip_store(scope)
        with self.connection_factory() as conn:
            return store.places(conn)

    def route_events_for(self, scope: ToolContext, *, targets: list[str] | None = None,
                         **_: Any) -> dict[str, Any] | None:
        """구간 대상들에 걸린 운행·통제 사건. 소스가 없으면 `None`.

        ★`events=None` 은 「사건 없음」이 아니라 **못 읽음**이다(결정 15 의 치명).
          `unsupported` 는 소스가 **답할 수 없는** 대상 — 「사건 없음」과 다르다.
        """
        source = self.route_events or (getattr(self.travel, "route_events", None) if self.travel else None)
        if source is None:
            return None
        wanted = [str(t) for t in (targets or [])]
        unsupported = getattr(source, "unsupported", None)
        return {"events": source.affecting(wanted),
                "unsupported": list(unsupported(wanted)) if callable(unsupported) else []}

    def customer_report(self, scope: ToolContext, *, text: str | None = None,
                        **_: Any) -> dict[str, Any] | None:
        """고객 문장에서 신고 내용을 뽑는다. 뽑는 함수가 없으면 `None`(모름).

        ★뽑기가 **실패한 것**은 `{"type": None, "error": ...}` 로 돌려준다 — 「못 알아들음」과
          「뽑을 수단이 없음」을 Team 이 가르게. 실패를 빈 결과로 바꾸지 않는다.
        """
        if self.report_extractor is None or not text:
            return None
        try:
            return self.report_extractor(str(text))
        except Exception as exc:                      # ★세어서 남긴다 — 조용히 삼키지 않는다
            return {"type": None, "error": f"{type(exc).__name__}: {exc}"[:200]}

    def disruptions(self, scope: ToolContext, *, place_id: Any = None,
                    latitude: float | None = None, longitude: float | None = None,
                    weather_sensitive: bool | None = None, region: str = "서울",
                    district: str | None = None, starts_at: Any = None,
                    **_: Any) -> dict[str, Any] | None:
        """일정 항목 하나의 **성립 점검** — 바깥 소스를 한 번에 돌려 판정 하나로.

        ★Team 은 예보·특보를 따로 부르지 않고 이것 하나만 부른다(도구 1회).
          감시 루프도 같은 판정을 쓴다 — 문의 때와 감시 때 기준이 갈리지 않게.
        ★소스 묶음이 주입되지 않았으면 `None` — 바깥으로 나가지 않는다.
        """
        place = {"place_id": place_id, "latitude": latitude, "longitude": longitude,
                 "weather_sensitive": bool(weather_sensitive), "district": district}
        if self.check is not None:
            return self.check(place=place, starts_at=_as_datetime(starts_at), region=str(region))
        if self.travel is None:
            return None
        from app.infrastructure.travel.disruptions import DisruptionCheck

        return DisruptionCheck(self.travel).check(
            place=place, starts_at=_as_datetime(starts_at), region=str(region))

    def weather_warning(self, scope: ToolContext, *, region: str = "서울",
                        **_: Any) -> dict[str, Any] | None:
        """지금 발효 중인 기상특보와 그중 `region` 을 덮는 것. 모르면 `None`.

        ★빈 목록(`for_region=[]`)은 「그 지역 특보 없음」이라는 **아는 사실**이고,
          `None` 은 조회를 못 한 것이다. Team 이 둘을 섞으면 조회가 죽은 날
          「특보 없음」이라고 답한다.
        """
        if self.travel is None or getattr(self.travel, "warning", None) is None:
            return None
        return self.travel.warning.active(region=str(region))

    def travel_advisory(self, scope: ToolContext, *, country_iso2: str | None = None,
                        **_: Any) -> dict[str, Any] | None:
        """그 나라의 외교부 여행경보. 나라 코드를 모르면 묻지 않는다(`None`).

        ★v11 MVP 는 서울뿐이라 이 도구를 선언한 Team 은 아직 없다 — 해외로
          넓힐 때 쓸 자리를 먼저 둔다.
        """
        if self.travel is None or getattr(self.travel, "advisory", None) is None:
            return None
        if not country_iso2:
            return None
        return self.travel.advisory.country(iso2=str(country_iso2))

    def route(self, scope: ToolContext, **_: Any) -> None:
        """이동 시간. `[미구현]` — Routes API 를 붙일 자리."""
        return None

    def transit(self, scope: ToolContext, **_: Any) -> None:
        """운행·막차. `[미구현]`."""
        return None

    def supplier(self, scope: ToolContext, *, booking_id: str | None = None,
                 **_: Any) -> dict[str, Any] | None:
        """공급자 원장의 그 예약. 없으면 `None`(모름).

        ★우리 `bookings.status` 와 **따로** 산다. 어긋나는 것이 정상이고,
          어긋남을 찾는 것이 `booking.verify` 의 일이다. 한 곳에서 읽어 오면
          그 capability 가 자기 자신과 비교하는 꼴이 된다.

        ★지금 원본은 우리 DB 의 `supplier_bookings` 다(시연용 Mock 공급자).
          실제 공급자 API 가 붙으면 이 함수만 바뀌고 Team 은 안 바뀐다.
        """
        if booking_id is None:
            return None
        return self._one(
            "SELECT supplier_booking_id, supplier, supplier_ref, status, confirmed_at "
            "FROM supplier_bookings WHERE tenant_id=%s AND booking_id=%s",
            (scope.tenant_id, booking_id),
            ("supplier_booking_id", "supplier", "supplier_ref", "status", "confirmed_at"))

    def call(self, name: str, context: ContextPack, arguments: dict[str, Any],
             allowed_tools: list[str], seen: set[str], budget: int | None = None) -> Any:
        """`budget` 은 이 Team 이 쓸 수 있는 도구 호출 수 상한이다.

        ★기본값이 `None` 이라 **안 넘기면 예전과 똑같이 동작한다.** 호출부를
          한꺼번에 고치지 않아도 되게 한 것이고, 넘기는 쪽은
          `self.manifest.max_steps` 를 준다.
        ★`seen` 의 크기를 그대로 센다 — 새 카운터를 만들지 않았다.
          `seen` 은 중복 차단기지만 그 크기가 곧 호출 횟수라 예산으로 맞는다.
        """
        # ★예산 검사를 allowlist 보다 **먼저** 두지 않는다. 권한 없는 도구는
        #   예산과 무관하게 거부돼야 하고, 그 편이 오류 메시지도 정확하다.
        if name not in allowed_tools:
            raise ToolNotAllowed(f"tool '{name}' is not allowed for this task")
        functions = {
            **self._travel_tools(),
            "read.policy": self.policy,
            "read.account": self.account,
        }
        if name not in functions:
            raise ToolNotAllowed(f"unknown tool '{name}'")
        signature = name + ":" + json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
        if signature in seen:
            raise ToolLoopExceeded(f"repeated tool request: {name}")
        if budget is not None and len(seen) >= budget:
            raise ToolBudgetExceeded(
                f"tool budget {budget} exhausted before '{name}' "
                f"(already called {len(seen)})")
        seen.add(signature)
        return functions[name](ToolContext.from_pack(context), **arguments)


ALLOWED_PROMPT_KEYS = frozenset({"response.generate", "response.review_tone"})


def register_prompt_files(
    conn: Any, prompt_root: str = "prompts", model_family: str = "unknown"
) -> tuple[list[UUID], list[str]]:
    """Register and activate the supported versioned prompt files atomically."""
    import hashlib
    from pathlib import Path

    root = Path(prompt_root)
    ids: list[UUID] = []
    skipped: list[str] = []
    candidates: list[tuple[Any, str, str, str, str]] = []
    for path in sorted(root.glob("*/**/*.v*.md")):
        stem, version = path.name.rsplit(".v", 1)
        version = version.removesuffix(".md")
        key = f"{path.parent.name}.{stem}"
        if key not in ALLOWED_PROMPT_KEYS:
            skipped.append(str(path))
            continue
        text = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        candidates.append((path, key, version, text, digest))

    with conn.transaction():
        with conn.cursor() as cur:
            for path, key, version, text, digest in candidates:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
                cur.execute(
                    "SELECT prompt_id, version, sha256 FROM prompts "
                    "WHERE prompt_key=%s AND (sha256=%s OR version=%s)",
                    (key, digest, version),
                )
                rows = cur.fetchall()
                same_hash = next((row for row in rows if row[2] == digest), None)
                same_version = next((row for row in rows if row[1] == version), None)
                if same_version is not None and same_version[2] != digest:
                    raise ValueError(
                        f"prompt version collision for {key} v{version}: content differs"
                    )
                if same_hash is not None:
                    prompt_id = same_hash[0]
                else:
                    cur.execute(
                        "INSERT INTO prompts (prompt_key, version, template, sha256, model_family, active) "
                        "VALUES (%s,%s,%s,%s,%s,false) RETURNING prompt_id",
                        (key, version, text, digest, model_family),
                    )
                    prompt_id = cur.fetchone()[0]
                cur.execute("UPDATE prompts SET active=false WHERE prompt_key=%s", (key,))
                cur.execute("UPDATE prompts SET active=true WHERE prompt_id=%s", (prompt_id,))
                cur.execute("SELECT count(*) FROM prompts WHERE prompt_key=%s AND active=true", (key,))
                if cur.fetchone()[0] != 1:
                    raise RuntimeError(f"expected exactly one active prompt for {key}")
                ids.append(prompt_id)
    return ids, skipped


register_prompts = register_prompt_files


def record_llm_call(conn: Any, *, run_id: UUID | None, prompt_id: UUID, provider: str, model: str, response_json: dict[str, Any] | None = None, input_tokens: int | None = None, output_tokens: int | None = None, latency_ms: int | None = None, cost_microusd: int | None = None) -> UUID:
    """Record an invocation with the exact registered prompt FK."""
    from app.infrastructure.db.repository import create_llm_call

    return create_llm_call(conn, run_id=run_id, prompt_id=prompt_id, provider=provider, model=model, response_json=response_json, input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=latency_ms, cost_microusd=cost_microusd)
