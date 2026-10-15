# -*- coding: utf-8 -*-
"""여행 API — 일정 등록 · 조회 · 고객 신고 · 재요청(다른 안 · 되돌림) · 계획서 링크.

★**왜 `app/domains/` 에 있나** — presentation 은 도메인을 import 하지 못한다
  (INV-CS-ARCH-001, `tests/architecture/test_basement_is_domain_free.py`). 라우터를
  여기서 만들고 `composition.build_domain_routers()` 가 앱에 넣는다. Composer 라우터를
  주입하는 것과 같은 모양이다.

★접점 둘(v11 §1):
    ① 개인 에이전트 API   `/v1/trips/*`            Bearer + scope(`trip:read`·`trip:write`)
    ② 여행계획서 링크      `/plan/{trip_id}?t=…`    로그인 없음 · 여행별 토큰(HMAC)

★`[2026-09-22]` ②와 **같은 모양의 링크가 하나 더** 있다 — 업체 예약 변경 링크
  `/booking-change/{booking_id}?t=…`(예약별 토큰, v11 §4-C · DoD-16·17). 접점을 늘린 것이
  아니라 ② 안의 한 장면이다: 우리 일정은 고쳐 두고 **업체 건만** 고객이 직접 진행하도록 넘긴다.

★**상태의 정본은 링크다.** 통지를 못 봐도 링크에서 맞는 것을 본다 — 링크는 매번
  최신 버전을 읽는다(DoD-25). 통지가 닿았는지는 사양에 넣지 않는다.

★**같은 요청을 두 번 받아 두 번 고치지 않는다.** 등록은 `request_id` 로 만든 멱등 키,
  신고·재요청은 원인 칸에 남긴 `request_id` 로 막는다(`TripStore.version_for_request`).

`[미구현]` 고객 **자유 문장**을 Case → 분류 → 여기로 잇는 배선. 지금 신고는
  **구조화된 몸통**(`type`·`minutes`·`products`)으로 들어온다 — 에이전트가 옮겨 보낸다.
`[미구현]` 계획서의 고객 언어 생성(결정 14). 지금은 한국어 원문을 보인다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import contextlib
import hashlib
import hmac
import html
import json
import logging
from typing import Any, Callable, Literal
from uuid import UUID, uuid4
from urllib.parse import quote       # 계획서 내려받기 파일 이름(`/plan/{id}?download=1` — D-CS-011)
from zoneinfo import ZoneInfo

from fastapi import (APIRouter, BackgroundTasks, Body, Depends, File, Form, Header, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

from app.core import settings as settings_module
from app.core.idempotency import idempotency_key
from app.core.contracts import StateConflict
from app.application.controller import ControllerError
from app.core.team_availability import TeamUnavailable
from app.core.team_availability import unavailable_payload

from app.infrastructure.db.session import get_connection
from app.presentation.security import Principal, require_scope

from app.domains.travel_ops.modules.web_account import guest_policy
from app.domains.travel_ops.components.itinerary import trip_delete
from app.domains.travel_ops.components.itinerary.itinerary import Item, TripStore
from app.domains.travel_ops.components.conversation.trip_desk import TripDesk
from app.domains.travel_ops.components.planning import guardian as guardian_module
from app.domains.travel_ops.components.watch import safety_pause, safety_recovery
from app.domains.travel_ops.components import settings_hook

# ★`[2026-10-05 병합]` 팀 판의 `_CsvFallbackTour`(활동팀 CSV 로 관광공사를 대신 조회) 는 싣지 않았다 — 관광공사 호출 제한 · 카카오 403 을
#   피하려는 활동팀의 임시 우회이고 자료 파일(`activity_total_data.csv`)이 우리 쪽에 없다. 접수의 관광공사 조회는 전처럼 `place_factory` 가 한다.
class _PlaceCtx:
    """요청 단위 장소 좌표 힌트 — 확정된 장소들의 일정 시각·좌표를 저장하고,
    현재 항목 일정 시각에 가장 가까운 장소 좌표를 반환한다."""

    def __init__(self) -> None:
        self._entries: list[tuple[str | None, float, float]] = []  # (ISO datetime, lat, lon)
        self.current_datetime: str | None = None  # "YYYY-MM-DDTHH:MM" 또는 "YYYY-MM-DD"

    def add(self, lat: float, lon: float) -> None:
        self._entries.append((self.current_datetime, lat, lon))

    def get_near(self) -> tuple[float, float] | None:
        if not self._entries:
            return None
        if self.current_datetime is None or len(self._entries) == 1:
            return (self._entries[-1][1], self._entries[-1][2])
        try:
            cur = datetime.fromisoformat(self.current_datetime)
        except ValueError:
            return (self._entries[-1][1], self._entries[-1][2])

        def _diff(dt_str: str | None) -> timedelta:
            if not dt_str:
                return timedelta(days=999999)
            try:
                return abs(datetime.fromisoformat(dt_str) - cur)
            except ValueError:
                return timedelta(days=999999)

        # 일정 시각 차이 최소, 동점이면 나중에 추가된 것(직전 항목 우선)
        best = min(range(len(self._entries)), key=lambda i: (_diff(self._entries[i][0]), -i))
        return (self._entries[best][1], self._entries[best][2])


def _dining_lookup(ctx: _PlaceCtx | None = None) -> Any:
    """일정 접수의 요식 원장 자리(`intake/places.py` — 식사는 원장이 먼저). 액티비티 CSV 자리와 따로 둔다.

    ctx 가 있으면 원장에서 찾은 식당 좌표도 다음 조회의 near 힌트로 넘긴다(액티비티 CSV · 카카오와 같은 힌트).
    """
    from app.domains.travel_ops.components.team_hooks import dining_ledger

    def found(place: dict[str, Any]) -> None:
        if ctx is not None:
            try:
                ctx.add(float(place["latitude"]), float(place["longitude"]))
            except (KeyError, TypeError, ValueError):
                pass

    class DeferredDiningLookup:
        # Activity-only intake does not acquire Dining until it actually queries Dining.
        name = "dining_ledger"

        def __init__(self):
            self._lookup = None

        def _provider(self):
            if self._lookup is None:
                self._lookup = dining_ledger.lookup_factory(
                    get_connection, on_found=found, near=ctx.get_near if ctx is not None else None)
            return self._lookup

        @property
        def misses(self):
            return self._lookup.misses if self._lookup is not None else {}

        def find(self, *args, **kwargs):
            return self._provider().find(*args, **kwargs)

        def find_dish_near(self, *args, **kwargs):
            return self._provider().find_dish_near(*args, **kwargs)

    return DeferredDiningLookup()


#: ★대상 도시는 서울 하나다(v11 §1). 시간대 없이 온 시각은 서울 시각으로 읽는다.
KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)

CheckFactory = Callable[[], Callable[..., dict[str, Any]]]


class PlaceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    lat: float
    lon: float
    #: ★`[2026-09-29]` None = 모름. 전에는 bool 기본 False 라 모르는 곳이 「야외 아님」으로 저장됐다(경희궁·둘레길)
    weather_sensitive: bool | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


#: ★외부 서비스에서 받은 장소 — 공용 장소 표에 쌓지 않고 **그 여행 전용 행**으로 넣는다(마이그레이션 029).
#:  `places[].attributes.source` 로 가린다. 일정 생성기의 관광공사 후보도 이 값을 단다(`planner.py`).
EXTERNAL_PLACE_SOURCES = frozenset({"tour_api", "kakao", "google_places",
                                    # ★`[2026-10-02]` 고객이 확인 화면에서 후보 · 검색 · 지도로 고른 장소(`intake/assemble._place_in`)
                                    "customer_pick",
                                    # ★`[2026-10-01 · 10-05 병합]` 요식 원장(`dining_ledger`)에서 찾은 가게도 여행 전용 행이다 — 공용 표에 쌓으면
                                    #   일정 접수의 이름 찾기 · 오타 교정이 그 이름들을 먼저 보게 된다(액티비티 항목까지 영향)
                                    "dining_ledger",
                                    # ★`[2026-10-05]` 인허가(사업자 등록) 자료에서 이름으로 찾은 식당 — 역시 그 여행 전용 행(영업시간 모름)
                                    "dining_license"})


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int
    kind: str = Field(min_length=1)
    title: str = Field(min_length=1)
    place: str | None = None          # places[].key
    starts_at: datetime
    ends_at: datetime | None = None
    route: str | None = None          # routes 의 키 — 이동 항목
    detail: dict[str, Any] = Field(default_factory=dict)


class CreateTrip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    customer_id: UUID
    title: str = Field(min_length=1)
    locale: str | None = None
    party_size: int | None = Field(default=None, ge=1)
    constraints: dict[str, Any] = Field(default_factory=dict)
    places: list[PlaceIn] = Field(default_factory=list)
    items: list[ItemIn] = Field(min_length=1)
    routes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class IntakeEditOne(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str | None = None      # 항목 칸이면 필수(어느 원본의 몇째 항목인가)
    field: str = Field(min_length=1)  # 예: items[2].place · items[0].starts_at · trip.first_day
    value: Any = None


class IntakeEditIn(BaseModel):
    """계획 읽기 확인 화면의 고치기. `revision` = 화면이 보고 있던 판(낡으면 409)."""
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    edits: list[IntakeEditOne] = Field(min_length=1, max_length=50)


class ReopenIn(BaseModel):
    """`[2026-10-13]` 「일정 수정」 — 화면이 보고 있는 여행 판. 다르면 409 `stale_itinerary`(계약 「등록한 여행 고치기」)."""
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class IntakeConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    #: ★`[2026-09-28]` 여행 시작 설문(`TripSurvey`, 판 `2026-09-24.v1`) — 선택. 등록 몸통의 `constraints.survey` 로
    #:  실어 `_create_trip` 이 검사한다(틀리면 422 `invalid_survey`). 전에는 이 흐름에 설문을 실을 곳이 없었다
    survey: dict[str, Any] | None = None


class IntakeSurveyIn(BaseModel):
    """로딩 중 질문의 답 — `{문항: 선택지 번호}`. 한 개 이상 · 부분 답 · 같은 문항을 다시 보내면 덮어쓴다(`components/intake/survey_answers.py`)."""
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str | dict[str, str] | None] = Field(min_length=1, max_length=8)


class MoveEndIn(BaseModel):
    """이동 판정의 한쪽 끝 — 일정 항목(`item_id` + 위 `trip_id`)이거나 이름 · 좌표(`lat` · `lon`)."""
    model_config = ConfigDict(extra="forbid")
    item_id: UUID | None = None
    name: str | None = Field(default=None, max_length=80)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class MoveJudgeIn(BaseModel):
    """`POST /v1/web/moves/judge` — 읽기 전용. `depart_at` 과 `arrive_by` 는 **둘 중 하나**(둘 다 없으면 지금 출발). 시간대 없는 값은 서울 시각."""
    model_config = ConfigDict(extra="forbid")
    origin: MoveEndIn
    destination: MoveEndIn
    depart_at: datetime | None = None
    arrive_by: datetime | None = None
    #: 일정 항목으로 한쪽 끝을 가리킬 때 필요하다. 주면 그 여행의 인원 · 이동 선호(설문)도 판정에 쓴다 — **본인 여행만**
    trip_id: UUID | None = None


class IntakeRevisionIn(BaseModel):
    """전체 자동 추천 · 재검증 — 화면이 보고 있던 판(낡으면 409)만 보낸다."""
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class IntakeModeIn(IntakeRevisionIn):
    """이동수단 고르기 — `mode` = `subway|bus|taxi|walk`(그 수단으로) · `recommended`(추천으로 되돌리기). `[2026-10-07]`"""
    mode: str = Field(min_length=1, max_length=16)


class IntakeRestoreItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    index: int = Field(ge=0)


class IntakeRestoreIn(IntakeRevisionIn):
    """전체 되돌리기 — 현재 판과 같은 접수에서 복원할 완료된 과거 판."""
    restore_revision: int = Field(ge=1)
    item: IntakeRestoreItem | None = None


class IntakeAutofixIn(IntakeRevisionIn):
    """전체 자동 추천 — `dry_run` 이면 **저장하지 않고** 바뀔 모습만(`view.preview`) 돌려준다. `[2026-10-03 ui 세션 요청서 3번]`"""
    dry_run: bool = False


class RecoveryPlanIn(BaseModel):
    """★`[결정 2026-10-07 사용자]` 재난 뒤 다시 짤 때 사용자가 고른 제약(`GET …/safety/recovery` 의 `constraints`) — 일정 생성기가 받는다.

    `avoid_districts` 의 구는 후보에서 빠지고(구를 모르는 후보는 남는다), `lighter_day` 는 **사용자가 골랐을 때만** 밀도 목표를 한 단계 낮춘다."""
    model_config = ConfigDict(extra="forbid")
    avoid_districts: list[str] = Field(default_factory=list, max_length=25)
    lighter_day: StrictBool = False

    @model_validator(mode="after")
    def _known_districts(self) -> "RecoveryPlanIn":
        unknown = [name for name in self.avoid_districts if name not in safety_recovery.SEOUL_DISTRICTS]
        if unknown:
            raise ValueError(f"서울 자치구 이름이 아니다: {unknown}")
        return self


class IntakePlanIn(BaseModel):
    """「일정 짜 줘」 — 확인 화면에서 고객이 조건을 확인하고 누른다. ★누르는 것이 곧 등록 요청이다(통지가 나간다)."""
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    start_date: date
    days: int = Field(ge=1, le=7)
    party_size: int = Field(ge=1, le=4)
    #: 읽은 일정(고객이 이미 정한 것)은 그대로 두고 빈 곳만 채운다. 끄면 읽은 일정 없이 새로 짠다
    keep_read_items: bool = True
    #: ★`[2026-09-28]` 여행 시작 설문 — 선택. 일정 생성기가 먼저 적용하고(16번 여유 → 하루 곳 수) 등록에도 실린다
    survey: dict[str, Any] | None = None
    #: ★`[2026-10-07]` 재난 뒤 다시 짤 때 사용자가 고른 제약 — 선택. 없으면 아무것도 안 바뀐다
    recovery: RecoveryPlanIn | None = None


class PlanIn(BaseModel):
    """★`[2026-09-22]` **일정 생성 요청** — v11 §4-A(「계획 생성은 우리 일이 아니다」)를 뒤집는
    경로다. 사용자 지시로 만들었고, 계획서는 읽기 전용이라 고치지 않았다. 뒤집는다는 사실과
    이 생성기가 못 하는 것은 리포트에 적었다.

    ★**`register` 기본값은 `false` 다.** 등록은 여행 상태를 만들고 **통지를 내보낸다**(계획서
      링크가 처음 나가는 자리, v11 §6-B). 초안을 보자고 부른 요청이 조용히 고객에게 링크를
      보내면 안 된다 — 에이전트가 초안을 보고 **명시적으로** `register:true` 를 보낼 때만 등록한다.
    """

    #: ★칸 이름은 `register_now` 인데 **바깥 이름은 `register`** 다. 둘을 가르는 이유:
    #:  `register` 를 필드 이름으로 쓰면 `BaseModel` 의 이름을 가려 pydantic 이 경고하고,
    #:  `Field(alias=...)` 로 붙이면 FastAPI 가 몸통 모델을 다시 감쌀 때
    #:  `UnsupportedFieldAttributeWarning` 이 매 요청마다 뜬다(둘 다 실측). 그래서 **받기 전에
    #:  이름만 옮긴다** — 바깥 계약(`register`)은 그대로 두고 경고도 남기지 않는다.
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    customer_id: UUID
    city: str = "서울"
    start_date: date
    days: int = Field(ge=1, le=7)
    party_size: int = Field(ge=1, le=4)
    locale: str | None = None
    title: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    #: 고객의 자유 문장. 예 「실내 위주, 아이 동반, 매운 음식 싫어요」
    preferences: str = ""
    register_now: bool = False

    @model_validator(mode="before")
    @classmethod
    def _accept_register(cls, data: Any) -> Any:
        if isinstance(data, dict) and "register" in data:
            # ★`{**data, ...}` 로 쓰면 `**data` 가 먼저 펴져 `register` 가 그대로 남고
            #   `extra="forbid"` 에 걸린다(실측 — 422 가 났다). 복사한 뒤 옮긴다.
            data = dict(data)
            data["register_now"] = data.pop("register")
        return data


class ChooseIn(BaseModel):
    """보류 제안 고르기. `key` 가 없으면(null) **원래 일정을 그대로 둔다**(kept)."""
    model_config = ConfigDict(extra="forbid")
    key: str | None = None


class GuardianIn(BaseModel):
    """항로 지킴이 켜기 · 끄기. `via` 는 어디서 눌렀나(기록용) — card(계획 담기 전 카드) · header(상단 아이콘) · notice(알림 링크) · settings(여행 설정)."""
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool                      # ★글자 「yes」 · 「1」 을 참으로 읽지 않는다 — 자동 변경을 켜는 값이라 JSON 참 · 거짓만 받는다
    via: Literal["card", "header", "notice", "settings"]


class RecoveryChoiceIn(BaseModel):
    """재난 뒤 다시 시작할 때의 선택(`components/watch/safety_recovery.py`). 선택은 기록한다(`user_activity_events`)."""
    model_config = ConfigDict(extra="forbid")
    pause_id: UUID
    choice: Literal["keep", "replace_affected", "replan_all"]
    lighter_day: StrictBool = False          # 「오늘은 가볍게」 — 우리가 임의로 낮추지 않는다. 기록하고 에이전트에게 제약으로 전한다
    answers: dict[Literal["lodging", "companions"], Literal["yes", "no", "unknown"]] | None = None


class ReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    type: Literal["delay", "closed", "stock_out"]
    message: str = Field(min_length=1)
    at: datetime | None = None
    minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    products: list[str] = Field(default_factory=list)


class AlternateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    base_version: int = Field(ge=1)
    choice: str | None = None
    message: str | None = None


class LocationIn(BaseModel):
    """고객의 **현재 위치** — 브라우저 Geolocation 이 준 값. ★이 요청을 처리하는 데만 쓰고 어디에도 남기지 않는다(`trip_here`)."""
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    at: datetime | None = None

    def __repr__(self) -> str:                    # 검증 오류 · 로그에 좌표가 실리지 않게
        return "LocationIn(<좌표 가림>)"


class MessageIn(BaseModel):
    """고객 **자유 문장** — 에이전트가 옮기지 않고 그대로 보낸다."""
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    at: datetime | None = None
    #: ★`[2026-09-29]` 화면에서 고른 일정 — 「다른 데로 바꿔 줘」가 가리키는 항목(없으면 문장 · 다음 일정으로 정한다)
    item_id: UUID | None = None
    #: ★`[2026-09-30 사용자 지시]` 고객의 현재 위치(선택) — 「여기서 경복궁 어떻게 가」 같은 질문의 출발지. 없으면 지금과 같다
    location: LocationIn | None = None
    case_id: UUID | None = None
    resume_token: str | None = Field(default=None, min_length=1)
    expected_version: int | None = Field(default=None, ge=0)


class WebMessageIn(MessageIn):
    # 요약 버튼의 명시적 읽기 요청. 에이전트 자유 문장 계약과 분리한다.
    summary_day: date | None = None


class RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    base_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    message: str | None = None


def _place_view(key: str | None, places: list[Any]) -> dict[str, Any] | None:
    """등록 요청 안의 장소를 판정기가 읽는 모양으로. 저장 전이라 DB 를 보지 않는다."""
    if key is None:
        return None
    for place in places:
        if place.key == key:
            return {"name": place.name, "attributes": dict(place.attributes or {})}
    return None


def _ledger_verdicts(slots: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """등록 판정용 요식 원장 조회 — 원장 가게(또는 관광공사 ID) · 방문 시각 → 그 시각에 여는가(`dining.ledger.slot_verdicts`).

    ★못 읽으면 남기고 다시 던진다 — 받는 쪽(`with_ledger`)이 판정 없이(모름) 등록을 잇는다. 조용히 넘기면 원장이 죽어도
      아무도 모른 채 식당 판정이 꺼진다.
    """
    from app.domains.travel_ops.components.team_hooks import dining_ledger

    dining_ledger.require_slot_verdicts()

    try:
        with get_connection() as conn:
            return dining_ledger.slot_verdicts(conn, slots)
    except Exception:
        logger.warning("요식 원장 판정을 읽지 못해 식당 영업 판정 없이 등록을 판정한다", exc_info=True)
        raise


def _planner_ledger(conn) -> Any:
    """일정 생성기의 식사 후보 · 판정은 요식 원장으로(`[2026-10-02]` 사용자 결정 — 식당은 관광공사를 실시간으로 부르지 않는다)."""
    from app.domains.travel_ops.components.team_hooks import dining_ledger

    return dining_ledger.planner_factory(conn)


# ★`status` 를 **위치 전용**(`/`)으로 받는다 — `**extra` 에 상세로 `status` 가 들어오면(예: 아직 등록할 수 없는 접수의 현재 상태
#   `IntakeConflict(..., status=...)`) 같은 이름이 둘이라 `TypeError: got multiple values for argument 'status'` 로 409 가 서버 오류(500)가 됐다.
def _error(status: int, code: str, message: str, /, **extra: Any) -> HTTPException:
    return HTTPException(status, {"error": {"code": code, "message": message, **extra}})



def _plan_refused(refused: Any) -> HTTPException:
    """생성기 거절 → HTTP. ★바깥 소스 속도 한도(`source_busy`)는 조건 문제가 아니라 **잠시 뒤 다시**다 —
    503 + `Retry-After`. 나머지는 422(무엇이 왜 안 됐는지와 완화 조건)."""
    if refused.code == "source_busy":
        wait = int(refused.detail.get("retry_after_seconds") or 60)
        return HTTPException(503, {"error": {"code": refused.code, "message": refused.message, **refused.detail}},
                             headers={"Retry-After": str(wait)})
    return _error(422, refused.code, refused.message, **refused.detail)


def _seoul(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    # ★`[2026-10-03 ui 검증 세션 지적]` 시간대 있는 값도 **서울 시각으로 바꾼다** — 전에는 시간대 없는 값에만 서울을 붙이고 있는 값은 그대로 돌려줘, 끼니 이름표(`_meal_label`) 등이
    #   DB 세션이 서울이 아니면(UTC 서버) 어긋났다(서울 12:00 점심 = UTC 03:00 → 「아침」)
    return moment.replace(tzinfo=KST) if moment.tzinfo is None else moment.astimezone(KST)


# ── 계획서 링크 ──────────────────────────────────────────────────
# ★`[2026-09-20]` 구현은 `plan_link.py` 로 옮겼다 — 통지·안내를 만드는 쪽이 FastAPI 를 끌고 오지
#   않고 링크를 붙일 수 있게. 여기서 다시 내보내므로 부르는 쪽은 안 바뀐다.
from app.domains.travel_ops.components.booking.change_link import change_token, change_url, change_view, render_change  # noqa: E402
from app.domains.travel_ops.components.itinerary.itinerary_checks import Part, check_itinerary, parts_from_items, with_ledger
from app.domains.travel_ops.components.itinerary.density import measure_density
from app.domains.travel_ops.components.itinerary.itinerary_quality import quality_warnings
from app.domains.travel_ops.components.itinerary.plan_link import plan_token, plan_url        # noqa: E402  (자리를 지켜 읽기 쉽게 둔다)
from app.domains.travel_ops.components.itinerary.route_uses import route_problems  # noqa: E402
from app.domains.travel_ops.components.planning.survey import apply_survey  # noqa: E402
from app.domains.travel_ops.components.conversation.trip_facts import booking_fact  # noqa: E402


# ── 보기 ────────────────────────────────────────────────────────
# ★`[2026-10-01 사용자 지적]` 화면이 「개화」만 보고는 식당인지 활동인지 숙소인지 알 수 없었다 — 종류는 늘 있었지만
#   (`kind`) 사람이 읽는 말이 없어 화면마다 따로 만들어야 했다. 서버가 한 번만 정해 준다.
_KIND_LABEL = {"dining": "식사", "activity": "활동", "mobility": "이동", "lodging": "숙소", "flight": "항공"}


def _meal_label(item: Item) -> str | None:
    """식사 항목의 끼니 — 일정 생성기가 적은 아침 표시가 먼저, 없으면 시작 시각(KST)으로 센다. 식사가 아니면 None."""
    if item.kind != "dining":
        return None
    if (item.detail.get("planner") or {}).get("meal") == "breakfast":
        return "아침"
    hour = (_seoul(item.starts_at) or item.starts_at).hour
    return "아침" if hour < 10 else "점심" if hour < 16 else "저녁"


def _badges(place: dict[str, Any] | None) -> list[dict[str, str]]:
    """장소에 남은 가게 표시 [{"code", "label", "source"}]. 모양이 다른 것은 버린다(화면이 지어내지 않게)."""
    out = []
    for badge in (place or {}).get("badges") or []:
        if isinstance(badge, dict) and all(isinstance(badge.get(key), str) and badge.get(key)
                                           for key in ("code", "label", "source")):
            out.append({key: badge[key] for key in ("code", "label", "source")})
    return out


def _item_view(item: Item, info: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"item_id": str(item.item_id), "seq": item.seq, "kind": item.kind,
            "kind_label": _KIND_LABEL.get(item.kind, item.kind), "meal": _meal_label(item),
            "title": item.title, "place": (item.place or {}).get("name"),
            # ★`[2026-10-03 ui 검증 세션 지적]` 서울 시각(+09:00)으로 내보낸다 — 지도의 날짜 묶음(`starts_at[:10]`)과 화면의 시계 표시가 DB 세션 시간대에 안 흔들린다
            "starts_at": _seoul(item.starts_at).isoformat(),
            "ends_at": _seoul(item.ends_at).isoformat() if item.ends_at else None,
            "changed": item.replaces_item_id is not None,
            # `[2026-10-13]` 이동 항목이 「그날 묵는 숙소에서 출발」(아침 숙소 → 첫 일정)인가 — 순서상 바로 앞은 전날 마지막 일정이라 화면이 이걸로 출발지를 숙소로 잡는다
            **({"from_stay": True} if item.kind == "mobility" and item.detail.get("from_stay") else {}),
            # ★`[2026-09-30 · 10-05 병합]` 식당 가격 비교(`same_or_lower` · `higher` · `unknown` · `won`) — 원래 식당 대비.
            #   구글 금액은 내려 주지 않는다(저장 금지 — 비교 결과만 기록돼 있다). 비교하지 않았으면 None
            "price_compare": item.detail.get("price_compare"),
            "other_options": [{"key": a["key"], "name": a.get("option_label") or a["name"],
                               "price_compare": a.get("price_compare")}
                              for a in item.detail.get("alternates") or []],
            "customer_pinned": bool(item.detail.get("customer_pinned")),
            # ★`[2026-10-07]` 미쉐린 · 노포 표시와 출처 — 일정 생성기가 원장에서 실어 장소에 남긴 것(`planner.ledger_candidates`)
            "badges": _badges(item.place),
            # ★`[2026-09-27]` 웹 지도 핀 · 예약 표시. 좌표는 그 고객 자신의 여행 장소다(다른 고객에게 가지 않는다)
            "lat": (item.place or {}).get("latitude"), "lon": (item.place or {}).get("longitude"),
            # ★`[2026-09-28]` 채팅의 예약 답과 같은 판정(`trip_facts.booking_fact`) — 전에는 `detail.reserved` 를 안 봤다
            "booked": booking_fact(item)[0] == "있음",
            # ★`[2026-09-29 사용자 지적]` 장소 정보 — 요식 원장(주소·전화·분류·영업시간·미쉐린·카드 결제·주차 …)이 먼저,
            #   없으면 코어 장소 속성. 전에는 이름·시각·좌표만 있었다(`place_info.py`)
            "place_info": info,
            # ★`[2026-09-29]` 고객 자기 지도 앱으로 여는 링크(키 없음) — 좌표가 있으면 `map_view` 가 채운다
            "map_url": None}


_CAUSE_FIELDS = ("category", "type", "kind", "summary", "message", "to_version", "mode")


def _trip_view(conn, store: TripStore, trip_id: UUID) -> dict[str, Any]:
    from app.domains.travel_ops.components.places.place_info import place_info
    trip, items = store.latest(conn, trip_id)
    from app.domains.travel_ops.components.intake import registered_moves
    checked = registered_moves.load_trip_review(conn, tenant_id=store.tenant_id,
                                               customer_id=trip["customer_id"], trip_id=trip_id)
    items = registered_moves.recover_for_routes(items, checked)
    history = [{"version": row["version"], "reason": row["reason"],
                "causes": [{k: cause.get(k) for k in _CAUSE_FIELDS if cause.get(k) is not None}
                           for cause in (row["causes"] or [])],
                "at": row["created_at"].isoformat()}
               for row in store.versions(conn, trip_id)]
    views = [_item_view(item, place_info(conn, store.tenant_id, item.place) if item.kind != "mobility" else None)
             for item in items]
    parts = parts_from_items(items)
    measured = measure_density(parts, trip.get("constraints") or {})
    # ★`[2026-10-06 사용자 결정]` 재난으로 일정이 정지돼 있으면 상단이 「정지 중」을 보이고, 정지에 든 항목은 `paused: true` 다(`components/watch/safety_pause.py`)
    safety = safety_pause.view(conn, tenant_id=store.tenant_id, trip_id=trip["trip_id"])
    since = datetime.fromisoformat(safety["since"]) if safety["paused"] else None
    for item, shown in zip(items, views):
        shown["paused"] = bool(since is not None and (item.ends_at or item.starts_at) >= since
                               and (safety["level"] == "trip" or _seoul(item.starts_at).date().isoformat() == safety["day"]))
    return {"trip_id": str(trip["trip_id"]), "customer_id": str(trip["customer_id"]),
            "title": trip["title"], "locale": trip["locale"], "party_size": trip["party_size"],
            "simulation": trip.get("simulation"),
            "version": trip["version"],
            # ★`[2026-10-06]` 항로 지킴이(일정이 꼬이면 알아서 고치는 모드) 켜짐 여부 — 상단 아이콘이 읽는다(`components/planning/guardian.py`)
            "guardian": guardian_module.view(conn, tenant_id=store.tenant_id, trip_id=trip["trip_id"],
                                             constraints=trip.get("constraints")),
            "safety": safety,
            # ★`[2026-10-15 사용자 결정 — ST4F-494]` 여행 화면 「지금 HH:MM」 시각 꼬리표 — 관리 콘솔 스위치(기본 켜짐). 계약 rest-endpoints/web-trips.md
            "now_label": settings_hook.trip_now_label_enabled(store.tenant_id),
            "items": views, "map": map_view(views),
            "history": history, "plan_url": plan_url(store.tenant_id, trip["trip_id"]),
            # ★`[2026-10-03]` 「살펴볼 점」 — 밀도 경고 + 일정 품질 경고(같은 곳 두 번 · 끼니 빠짐 · 왔다 갔다 · 하루 마감 · 식당 라스트오더). 둘 다 거절이 아니라 알림이다
            **measured, "warnings": [*measured["warnings"], *quality_warnings(parts, trip.get("constraints") or {})]}


def _maps_query(view: dict[str, Any]) -> str:
    """구글 지도 링크의 장소 — 이름 + 주소를 알면 그것(가게 정보가 뜬다), 모르면 좌표."""
    address = (view.get("place_info") or {}).get("address")
    if view.get("place") and address:
        return f"{view['place']} {address}"
    return f"{view['lat']},{view['lon']}"


def _leg_url(a: dict[str, Any], b: dict[str, Any]) -> str:
    from urllib.parse import urlencode

    return "https://www.google.com/maps/dir/?" + urlencode(
        {"api": 1, "origin": _maps_query(a), "destination": _maps_query(b), "travelmode": "transit"})


def map_view(views: list[dict[str, Any]]) -> dict[str, Any]:
    """★`[2026-09-29 사용자 결정]` 지도 조합 — 고객 **자기 지도 앱으로 여는 링크**(구글 지도 링크는 API 키가 필요 없다 — 공식 문서).

    · 장소 항목 `items[].map_url` — 그 장소.
    · 이동 항목 `items[].map_url` — 앞 장소 → 다음 장소 **대중교통 길찾기**(들를 곳 없이 두 곳만).
    · 날짜마다 `days[].stops`(우리 번호 · 좌표 — 화면의 무료 지도가 번호 핀으로 찍는다)와 `days[].legs`(이어지는 두 곳마다 길찾기).
    ☆`[2026-09-29 ui 세션 실측]` 처음엔 하루 경로 링크(들를 곳 여러 개)와 구글 퍼가기 경로 지도를 실었다. 한국에서는 구글이
      자동차·도보 길찾기를 주지 않고 대중교통은 들를 곳을 받지 않아 **둘 다 경로를 못 그렸다**(퍼가기는 선 없이 빈 동그라미).
      그래서 뺐다 — 두 곳 사이 대중교통은 구글이 한국에서도 계산한다(웹 확인, 휴대폰 앱은 미확인).
    ★좌표가 없는 항목은 지도에 넣지 않는다.
    """
    from urllib.parse import urlencode

    places = [v for v in views if v["kind"] != "mobility" and v.get("lat") is not None and v.get("lon") is not None]
    for view in places:
        view["map_url"] = "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": _maps_query(view)})
    order = {v["item_id"]: n for n, v in enumerate(views)}
    for n, view in enumerate(views):
        if view["kind"] != "mobility":
            continue
        before = next((v for v in reversed(places) if order[v["item_id"]] < n), None)
        after = next((v for v in places if order[v["item_id"]] > n), None)
        if before and after:
            view["map_url"] = _leg_url(before, after)
    days: dict[str, list[dict[str, Any]]] = {}
    for view in places:
        days.setdefault(view["starts_at"][:10], []).append(view)
    out = [{"date": day,
            "stops": [{"number": n, "item_id": v["item_id"], "name": v.get("place") or v["title"],
                       "lat": v["lat"], "lon": v["lon"], "map_url": v["map_url"]}
                      for n, v in enumerate(stops, start=1)],
            "legs": [{"from_item_id": a["item_id"], "to_item_id": b["item_id"],
                      "from": a.get("place") or a["title"], "to": b.get("place") or b["title"], "url": _leg_url(a, b)}
                     for a, b in zip(stops, stops[1:])]}
           for day, stops in sorted(days.items())]
    return {"days": out, "note": "구글 지도 링크는 API 키 없이 고객 지도 앱으로 연다 — 한국은 두 곳 사이 대중교통만 경로가 나온다"}


#: ★고객이 보는 화면에 내부 이름(`customer_report · delay`)을 그대로 싣지 않는다 —
#:  브라우저로 열어 보고 고쳤다(2026-09-14). 모르는 값은 원래 이름을 그대로 보인다.
_REASON_LABELS = {"created": "등록", "auto_adjusted": "자동 변경", "customer_report": "고객 신고",
                  "customer_request": "고객 요청", "rollback": "되돌림"}
_CAUSE_LABELS = {"delay": "늦어짐", "closed_today": "당일 휴무", "stock_out": "품절",
                 "alternate": "다른 안으로 교체", "rollback": "옛 버전으로",
                 "air_quality": "대기질", "weather_warning": "기상특보", "forecast": "날씨",
                 "route_event": "교통 통제·운행 변경", "disaster_msg": "재난문자",
                 "traffic_control": "교통 통제"}


def _cause_label(cause: dict[str, Any]) -> str:
    if cause.get("summary"):
        return str(cause["summary"])
    for key in ("type", "category"):
        if cause.get(key) in _CAUSE_LABELS:
            return _CAUSE_LABELS[cause[key]]
    return str(cause.get("kind") or cause.get("type") or cause.get("category") or "")


#: ★`[2026-09-27]` 한국관광콘텐츠랩 이용약관 제11조 — 관광공사 값이 나가는 고객 화면에 출처를 적는다.
#:  이 화면의 장소가 관광공사 자료에서 왔는지 항목마다 가리지 않고 **늘** 붙인다(보수적으로).
TOUR_API_POLICY_URL = "https://api.visitkorea.or.kr/#/useServiceGuide/2"


#: 내려받는 파일 이름에 못 쓰는 글자(경로 구분자 · 윈도 금지 글자 · 줄바꿈) — 계획서 제목이 이름이 된다
_FILENAME_BAD = frozenset(chr(c) for c in (47, 92, 58, 42, 63, 34, 60, 62, 124, 10, 13))


def _render_plan(view: dict[str, Any]) -> str:
    esc = lambda value: html.escape(str(value or ""))  # noqa: E731
    rows = []
    for item in view["items"]:
        start = item["starts_at"][11:16]
        end = (item["ends_at"] or "")[11:16]
        badge = '<span class="badge">변경됨</span>' if item["changed"] else ""
        others = ""
        if item["other_options"]:
            others = ('<div class="others">다른 안: '
                      + ", ".join(esc(o["name"]) for o in item["other_options"]) + "</div>")
        rows.append(f'<li><div class="time">{start}–{end}</div><div><div class="title">'
                    f'{esc(item["title"])} {badge}</div>{others}</div></li>')
    changes = "".join(
        f"<li>버전 {h['version']} · {esc(_REASON_LABELS.get(h['reason'], h['reason']))}"
        + "".join(f" · {esc(_cause_label(c))}" for c in h["causes"]) + "</li>"
        for h in reversed(view["history"]))
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(view['title'])}</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e1d8;--accent:#2f6f4f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161614;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#7cc4a0}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:40rem;margin:0 auto;padding:1.25rem}}
h1{{font-size:1.3rem;margin:.2rem 0}} .meta{{color:var(--muted);font-size:.85rem}}
ul{{list-style:none;padding:0;margin:1rem 0}}
.plan li{{display:grid;grid-template-columns:6.5rem 1fr;gap:.5rem;padding:.6rem 0;border-bottom:1px solid var(--line)}}
.time{{font-variant-numeric:tabular-nums;color:var(--muted)}}
.badge{{font-size:.72rem;border:1px solid var(--accent);color:var(--accent);border-radius:.3rem;padding:0 .3rem}}
.others{{color:var(--muted);font-size:.85rem}} h2{{font-size:1rem;margin-top:1.5rem}}
.hist li{{color:var(--muted);font-size:.85rem;padding:.15rem 0}}
.credit{{margin-top:2rem;color:var(--muted);font-size:.78rem}} .credit a{{color:inherit}}
</style></head><body><main>
<h1>{esc(view['title'])}</h1>
<div class="meta">일정 버전 {view['version']} · 이 페이지가 최신 일정입니다</div>
<ul class="plan">{''.join(rows)}</ul>
<h2>바뀐 기록</h2><ul class="hist">{changes}</ul>
<footer class="credit">장소 정보 출처 : ⓒ한국관광공사 ·
<a href="{TOUR_API_POLICY_URL}" rel="noopener" target="_blank">저작권 정책</a></footer>
</main></body></html>"""


# ── 결과 → HTTP ─────────────────────────────────────────────────
_CONFLICTS = {"stale": "stale_itinerary", "no_alternate": "no_alternate",
              "unknown_choice": "unknown_choice", "conflicts_next": "conflicts_next",
              "alternate_invalid": "alternate_invalid", "invalid_version": "invalid_version"}


def _outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    status = outcome.get("status")
    if status == "not_found":
        raise _error(404, "not_found", "resource not found")
    if status in _CONFLICTS:
        details = {k: v for k, v in outcome.items() if k in ("version", "choices", "next", "verdict")}
        raise _error(409, _CONFLICTS[status], f"request not applied: {status}", **details)
    return outcome


#: 구글 지도 표시의 요금 단위 — 가드레일 `travel.google_budget.free_monthly` 의 이름과 같다
MAP_METER = "google_maps_dynamic_maps"


def map_budget():
    """구글 지도 불러오기 예산 — 시험이 이 함수를 바꿔 실제 사용량 줄을 건드리지 않는다."""
    from app.domains.travel_ops.ports.data_sources.call_budget import CallBudget, google_caps

    return CallBudget(connection_factory=get_connection, caps=google_caps())


def build_trip_router(*, check_factory: CheckFactory | None = None,
                      classifier_factory: Callable[[], Any] | None = None,
                      chat_factory: Callable[[], Any] | None = None,
                      place_factory: Callable[[], Any] | None = None,
                      kakao_factory: Callable[[], Any] | None = None,
                      policy_search_factory: Callable[[], Any] | None = None,
                      human_verify: Callable[..., dict[str, Any]] | None = None,
                      cached_check_factory: CheckFactory | None = None,
                      fresh_check_factory: CheckFactory | None = None) -> APIRouter:
    """★점검기·분류기·추출용 LLM 은 **처음 쓸 때** 만든다 — 앱 기동이 기다리지 않게.
    `cached_check_factory` · `fresh_check_factory` — ★`[2026-10-06]` 「일정 위험 점검」(`GET …/risks`)이 쓰는 점검기: 앞은 **공유 응답 캐시만 읽는** 것(바깥에 안 나간다), 뒤는 캐시에 없을 때
    새로 부르되 **낮은 우선순위**(하루 한도의 일부만)인 것. ★뒤가 없으면 `fresh=true` 는 503 이다 — 몫이 없는 점검기로 **조용히 대신하지 않는다**(`[2026-10-06 검토]`).
    `human_verify` — 사람 확인(Turnstile `siteverify`)을 갈아 끼우는 자리(시험). 없으면 Cloudflare 에 묻는다."""
    from app.domains.travel_ops.modules.web_account import web_guard

    # ★`[2026-09-28]` 사람 확인이 필요한 환경(운영)에서 비밀키가 없으면 **여기서 멈춘다** — 모르게 꺼진 채 뜨지 않게
    web_guard.assert_human_check_configured()
    router = APIRouter()
    cache: dict[str, Any] = {}

    def _ip(http: Request) -> str:
        return web_guard.client_ip(http.client.host if http.client else None, http.headers.get("x-forwarded-for"))

    def _human(token: str | None, http: Request) -> str:
        """`passed` · `skipped`. 실패 403 · Cloudflare 불통 503 — **통과로 보지 않는다**(RULE §3.2)."""
        try:
            return web_guard.human_check(token, ip=_ip(http), verify=human_verify)
        except web_guard.HumanCheckFailed as exc:
            raise _error(403, "human_check_failed", "사람 확인을 통과하지 못했다 — 화면에서 다시 확인한다",
                         reasons=exc.reasons) from None
        except web_guard.HumanCheckUnavailable:
            error = _error(503, "human_check_unavailable", "지금은 사람 확인을 할 수 없다 — 잠시 뒤 다시 한다",
                           retry_after_seconds=30)
            error.headers = {"Retry-After": "30"}
            raise error from None

    def _count(action: str, tenant: str, customer: UUID, http: Request) -> None:
        """비싼 작업 한 번(`web_guard.count`). 켜져 있을 때만 막는다 — 429(키·주소) · 503(서비스 전체)."""
        try:
            web_guard.count(tenant, action, customer_id=customer, ip=_ip(http))
        except web_guard.UsageRefused as refused:
            message = ("오늘 이 작업을 할 수 있는 횟수를 다 썼다 — 내일 다시 한다" if refused.status == 429
                       else "오늘 서비스 전체가 이 작업을 할 수 있는 횟수를 다 썼다 — 내일 다시 한다")
            error = _error(refused.status, refused.code, message, **refused.detail())
            error.headers = {"Retry-After": str(refused.retry_after)}
            raise error from None

    def _lazy(name: str, factory: Callable[[], Any] | None):
        if factory is None:
            return None
        if name not in cache:
            cache[name] = factory()
        return cache[name]

    def _check():
        return _lazy("check", check_factory)

    def _travel_sources():
        """`[2026-10-13]` 숙소 검색 소스(마이리얼트립 — 키 없음 · 구글 호텔 — SerpApi 키 · 월 250 · 하루 8 예산). 고를 때 숙소 가격 재확인(`_stay_recheck`)만 쓴다. 부를 때만 만든다."""
        from app.core.settings import get_settings
        from app.domains.travel_ops.ports.data_sources.base import build_travel_sources

        return build_travel_sources(get_settings())

    def _desk(store: TripStore) -> TripDesk:
        # ★`[2026-09-29]` 활동 대체 후보를 관광공사 목록에서 넓힐 때 운영시간을 읽는다(`catalog_pool`) — 부를 때만 만든다
        return TripDesk(store=store, connection_factory=get_connection, check=_check(), catalog_pool=True)

    def _trip_or_404(conn, store: TripStore, trip_id: UUID, customer_id: UUID | None = None):
        try:
            trip, _ = store.latest(conn, trip_id)
        except KeyError:
            raise _error(404, "not_found", "resource not found") from None
        if customer_id is not None and trip["customer_id"] != customer_id:
            raise _error(404, "not_found", "resource not found")
        return trip

    def _already(store: TripStore, trip_id: UUID, request_id: str) -> dict[str, Any] | None:
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id)
            done = store.version_for_request(conn, trip_id, request_id)
        return None if done is None else {"status": "duplicate", "version": done}

    @router.post("/v1/trips", status_code=201)
    def create(request: CreateTrip, principal: Principal = Depends(require_scope("trip:write"))):
        return _create_trip(principal.tenant_id, request)

    def _create_trip(tenant: str, request: CreateTrip) -> dict[str, Any]:
        """★등록의 **유일한** 본문이다. `/v1/trips` 도 `/v1/trips/plan?register=true` 도 여기로
        들어온다 — 생성기가 판정을 건너뛰는 길을 만들지 않으려고 하나로 둔다."""
        store = TripStore(tenant)
        # ★`[2026-09-24]` 여행 시작 설문(`constraints.survey`, D-020). 틀린 모양은 거절하고, 판정에 쓰는
        #   몫(16번 여유 → 밀도 목표)만 채운다. 사용자가 밀도를 직접 줬으면 그것이 이긴다.
        try:
            request = request.model_copy(update={"constraints": apply_survey(
                request.constraints, (_seoul(it.starts_at).date() for it in request.items))})
        except ValidationError as exc:
            raise _error(422, "invalid_survey", "constraints.survey 가 설문 계약과 다르다",
                         problems=[{"field": ".".join(str(p) for p in e["loc"]), "reason": e["msg"]}
                                   for e in exc.errors()]) from None
        keys = [p.key for p in request.places]
        if len(set(keys)) != len(keys):
            raise _error(422, "duplicate_place_key", "places[].key must be unique")
        if len({it.seq for it in request.items}) != len(request.items):
            raise _error(422, "duplicate_seq", "items[].seq must be unique")
        for it in request.items:
            if it.place is not None and it.place not in keys:
                raise _error(422, "unknown_place", f"item {it.seq} refers to unknown place")
            if it.route is not None and it.route not in request.routes:
                raise _error(422, "unknown_route", f"item {it.seq} refers to unknown route")
        # ★`[2026-09-23]` `uses` 표기를 **받을 때** 본다. 이 값은 운행·통제 사건과 문자열로
        #   대조돼서, 표기가 다르면 사건이 있어도 못 잡고 오류도 안 난다 — 전에는 `잠실역`·
        #   `02호선`·`버스:성수동` 을 그대로 받아 두고 나중에 조용히 놓쳤다. 틀린 값을 **전부**
        #   이유와 함께 돌려준다. 계약 `wiki/external/rest-endpoints.md` 「options[].uses」 절.
        bad_uses = route_problems(request.routes)
        if bad_uses:
            raise _error(422, "invalid_route_uses",
                         f"routes 의 uses 표기 {len(bad_uses)}건이 계약과 다르다 — 이대로 받으면 "
                         "그 구간의 운행·통제 사건을 대조하지 못한다", problems=bad_uses)
        # ★`[2026-09-21]` 받을 때 **코드로 판정**한다(v11 §12 DoD-2). 불가능하면 이유와 완화 조건을
        #   붙여 거절한다(DoD-3) — 전에는 참조·순서만 보고 그대로 받아 감시가 뒤에서 고쳤다.
        # ★`[2026-10-02]` 식당은 요식 원장의 그 시각 판정을 붙여 본다(`with_ledger`) — 전에는 원장에서 찾기만 하고
        #   영업 정보를 안 넘겨, 휴무 · 브레이크 · 영업 종료 뒤 식당이 등록을 통과했다. 원장을 못 읽으면 예전 그대로
        violations = check_itinerary(
            with_ledger([Part(seq=it.seq, kind=it.kind, title=it.title, starts_at=_seoul(it.starts_at),
                              ends_at=_seoul(it.ends_at),
                              place=_place_view(it.place, request.places), route=request.routes.get(str(it.route)),
                              detail=it.detail)
                         for it in request.items], _ledger_verdicts),
            constraints=request.constraints, party_size=request.party_size)
        if violations:
            raise _error(422, "itinerary_infeasible",
                         "이 일정은 그대로 수행할 수 없습니다: "
                         + " / ".join(v.reason for v in violations),
                         violations=[v.as_dict() for v in violations])
        key = idempotency_key(tenant_id=tenant, request_id=request.request_id,
                              action_type="trip.create", business_subject=str(request.customer_id))
        if any(place.attributes.get("dining_place_uid") for place in request.places):
            from app.domains.travel_ops.components.team_hooks import dining_ledger

            dining_ledger.require_link_core_place()
        body_sha = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with get_connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # ★동시에 온 같은 등록 둘이 둘 다 「없다」를 보지 않게 잠근다(cases.py 와 같다).
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{tenant}:{key}",))
                existing = store.by_request_key(conn, key)
                if existing is not None:
                    trip_id, stored_sha = existing
                    if stored_sha != body_sha:
                        raise _error(409, "idempotency_key_reused",
                                     "same request_id was used for a different body")
                    created = False
                else:
                    # ★`[2026-10-04 D-CS-011]` 게스트는 여행 1개 · 계획 기간 상한 — 같은 트랜잭션에서 사용자 행을 잠가 동시 생성까지 막는다
                    guest_policy.check_new_trip(conn, tenant_id=tenant, customer_id=request.customer_id,
                                                starts=[it.starts_at for it in request.items],
                                                ends=[it.ends_at for it in request.items])
                    trip_id = _insert(conn, store, request, key, body_sha)
                    created = True
            view = _trip_view(conn, store, trip_id)
        return {**view, "created": created}

    def _store_place(conn, cur, tenant: str, trip_id: UUID, *, name: str, kind: str, lat: float, lon: float,
                     weather_sensitive: bool | None, attributes: dict[str, Any]) -> UUID:
        """장소 한 곳 → 장소 행 id. 등록(`_insert`)과 수정 모드의 「완료」(`_confirm_reopened`)가 **같은 규칙**으로 넣는다."""
        # ★장소는 테넌트 안에서 (이름, 종류)로 하나다(UNIQUE). 이미 있으면 **그것을 쓴다.**
        #   ☆2026-09-14 — 처음엔 무조건 INSERT 해서, 두 번째 여행이 경복궁을 적자
        #     500 이 났다(개발 서버에서 발견. 시험은 여행마다 테넌트를 새로 만들어
        #     못 잡았다). 보낸 속성은 **빈 칸만 채운다** — 카탈로그 값을 고객 한 명의
        #     제출이 덮어쓰지 않게(`EXCLUDED || places` 는 오른쪽이 이긴다).
        if str(attributes.get("source") or "") in EXTERNAL_PLACE_SOURCES:
            # ☆같은 여행 안에서 같은 장소를 두 번 적을 수 있다 — 그 여행 행을 다시 쓴다
            cur.execute(
                "INSERT INTO places (tenant_id,name,kind,latitude,longitude,weather_sensitive,"
                "attributes,trip_scope) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id, trip_scope, name, kind) WHERE trip_scope IS NOT NULL "
                "DO UPDATE SET attributes = EXCLUDED.attributes || places.attributes "
                "RETURNING place_id",
                (tenant, name, kind, lat, lon, weather_sensitive,
                 json.dumps(attributes, ensure_ascii=False), trip_id))
        else:
            cur.execute(
                "INSERT INTO places (tenant_id,name,kind,latitude,longitude,weather_sensitive,"
                "attributes) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id, name, kind) WHERE trip_scope IS NULL "
                # ★`[2026-09-28]` 요식 식당 행은 이 유일 조건 밖이다(마이그레이션 222) — 충돌 대상을 맞춘다
                "AND source_name IS DISTINCT FROM 'dining_ledger' DO UPDATE "
                "SET attributes = EXCLUDED.attributes || places.attributes "
                "RETURNING place_id",
                (tenant, name, kind, lat, lon, weather_sensitive,
                 json.dumps(attributes, ensure_ascii=False)))
        place_id = cur.fetchone()[0]
        # ★`[2026-10-02]` 원장에서 고른 식당은 원장 가게와 바로 잇는다 — 새벽 확인 · 하루 점검이 코어 장소로
        #   원장을 찾는다(`dining.ledger.resolve_place`). 관광공사 ID 가 없는 원장 가게는 이렇게만 이어진다
        if attributes.get("dining_place_uid"):
            from app.domains.travel_ops.components.team_hooks import dining_ledger

            dining_ledger.link_core_place(conn, tenant, str(place_id), str(attributes["dining_place_uid"]))
        return place_id

    def _insert(conn, store: TripStore, request: CreateTrip, key: str, body_sha: str) -> UUID:
        tenant = store.tenant_id
        ids: dict[str, UUID] = {}
        # ★`[2026-09-27]` 외부 서비스(관광공사 · 카카오 · 구글)에서 온 장소는 **그 여행 전용 행**으로 넣는다
        #   (마이그레이션 029 · 설계서 §4-5·§4-6). 여행 id 를 미리 정해 장소 행에 적는다.
        trip_id = uuid4()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM customers WHERE tenant_id=%s AND customer_id=%s",
                        (tenant, request.customer_id))
            if cur.fetchone() is None:
                raise _error(404, "not_found", "customer not found")
            for place in request.places:
                ids[place.key] = _store_place(conn, cur, tenant, trip_id, name=place.name, kind=place.kind, lat=place.lat,
                                              lon=place.lon, weather_sensitive=place.weather_sensitive,
                                              attributes=place.attributes)
        items = [Item(item_id=uuid4(), seq=it.seq, kind=it.kind, title=it.title,
                      place_id=ids.get(it.place) if it.place else None,
                      starts_at=_seoul(it.starts_at), ends_at=_seoul(it.ends_at),
                      detail={**it.detail,
                              **({"route": it.route, "route_def": request.routes[it.route]}
                                 if it.route else {})})
                 for it in request.items]
        # ★`[2026-10-13 사용자 결정 — 숙소 항목 B안]` 묵는 날 아침 「숙소 → 그날 첫 일정」 이동을 넣는다(이동 계산기가 길을 찾을 때만)
        if any(it.kind == "lodging" for it in request.items):
            from app.domains.travel_ops.components.itinerary.itinerary_changes import with_stay_moves

            coords = {ids[p.key]: {"place_id": str(ids[p.key]), "name": p.name, "latitude": p.lat, "longitude": p.lon}
                      for p in request.places if p.lat is not None and p.lon is not None}

            def place_of(item: Item) -> dict[str, Any] | None:
                place = coords.get(item.place_id)
                return None if place is None else {"key": place["place_id"], "name": place["name"],
                                                   "lat": float(place["latitude"]), "lon": float(place["longitude"])}

            items = with_stay_moves(items, place_of=place_of, party_size=request.party_size, constraints=request.constraints)
        trip_id, version = store.create_trip(
            conn, customer_id=request.customer_id, title=request.title, locale=request.locale,
            party_size=request.party_size, items=items, constraints=request.constraints,
            request_key=key, request_sha256=body_sha, trip_id=trip_id)
        # ★알림 ① 은 **생성도 포함**한다 — 링크가 처음 나가는 자리다(v11 §6-B).
        url = plan_url(tenant, trip_id)
        store.enqueue_notice(conn, trip_id=trip_id, version=version, payload={
            "text": f"여행 일정이 준비되었습니다 — {request.title}. 계획서: {url}",
            "language": "ko", "causes": [], "changed": None, "other_options": [],
            "replay": False, "version": version, "plan_url": url})
        return trip_id

    @router.post("/v1/trips/plan")
    def plan_draft(request: PlanIn, principal: Principal = Depends(require_scope("trip:write"))):
        """요청 → 초안 일정 → **판정 통과** → (원하면) 등록. v11 §4-A 를 뒤집는 경로다.

        ★**판정을 건너뛰는 길이 없다.** 생성기가 `check_itinerary` 로 스스로 판정해 고치고,
          등록하면 `_create_trip` 이 **같은 판정기를 한 번 더** 돌린다.
        ★**멱등** — 같은 `request_id` 로 등록까지 두 번 오면 모델도 부르지 않고 이미 만든
          여행을 그대로 돌려준다(`trips.request_key`).
        """
        from app.domains.travel_ops.components.planning import planner as planner_module

        tenant = principal.tenant_id
        store = TripStore(tenant)
        key = idempotency_key(tenant_id=tenant, request_id=request.request_id,
                              action_type="trip.create", business_subject=str(request.customer_id))
        if request.register_now:
            with get_connection() as conn:
                existing = store.by_request_key(conn, key)
                if existing is not None:
                    return {"status": "duplicate", "created": False,
                            "trip": _trip_view(conn, store, existing[0])}
        ask = planner_module.PlanRequest(
            city=request.city, start_date=request.start_date, days=request.days,
            party_size=request.party_size, constraints=request.constraints,
            preferences=request.preferences, title=request.title, locale=request.locale)
        from app.domains.travel_ops.components.team_hooks import dining_ledger

        dining_ledger.require_planner_factory()
        try:
            with get_connection() as conn:
                outcome = planner_module.plan_trip(conn=conn, tenant_id=tenant, request=ask,
                                                   chat=_lazy("chat", chat_factory),
                                                   tour_api=_lazy("place", place_factory),
                                                   ledger=_planner_ledger(conn))
        except planner_module.PlanRefused as refused:
            raise _plan_refused(refused) from None
        result: dict[str, Any] = {"status": "drafted", **outcome.as_dict()}
        if request.register_now:
            body = outcome.draft.as_create_body(request_id=request.request_id,
                                                customer_id=request.customer_id)
            result = {**result, "status": "registered",
                      "trip": _create_trip(tenant, CreateTrip.model_validate(body))}
        return result

    @router.get("/v1/trips/{trip_id}")
    def detail(trip_id: UUID, customer_id: UUID | None = Query(None),
               principal: Principal = Depends(require_scope("trip:read"))):
        store = TripStore(principal.tenant_id)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer_id)
            return _trip_view(conn, store, trip_id)

    @router.post("/v1/trips/{trip_id}/reports")
    def report(trip_id: UUID, request: ReportIn,
               principal: Principal = Depends(require_scope("trip:write"))):
        return _report_result(TripStore(principal.tenant_id), trip_id, request)

    def _report_result(store: TripStore, trip_id: UUID, request: ReportIn) -> dict[str, Any]:
        """신고 한 건의 처리 — 에이전트 입구와 웹 입구(`/v1/web/trips/{id}/reports`)가 **같은 것**을 쓴다."""
        duplicate = _already(store, trip_id, request.request_id)
        if duplicate:
            return duplicate
        desk = _desk(store)
        at = _seoul(request.at) or datetime.now(KST)
        if request.type == "delay":
            if request.minutes is None:
                raise _error(422, "validation_error", "delay needs minutes")
            outcome = desk.report_delay(trip_id=trip_id, at=at, minutes=request.minutes,
                                        message=request.message, request_id=request.request_id)
        elif request.type == "closed":
            outcome = desk.report_closed(trip_id=trip_id, at=at, message=request.message,
                                         request_id=request.request_id)
        else:
            if not request.products:
                raise _error(422, "validation_error", "stock_out needs products")
            outcome = desk.ask_nearby_store(trip_id=trip_id, at=at, products=request.products,
                                            message=request.message, request_id=request.request_id)
        return _outcome(outcome)

    @router.post("/v1/trips/{trip_id}/messages")
    def message(trip_id: UUID, request: MessageIn, http: Request,
                principal: Principal = Depends(require_scope("trip:write"))):
        """고객 자유 문장 → **Case → 분류 → 추출 → 여행 창구 → Case 닫기**(v11 §5 경로).

        ★처리 규칙은 `trip_messages.handle_trip_message` 한 곳에 있다 — 시나리오 모드도
          같은 것을 쓴다(한 규칙이 두 벌로 갈라지지 않게).
        """
        from app.domains.travel_ops.components.conversation.trip_messages import TripNotFound, handle_trip_message

        store = TripStore(principal.tenant_id)
        try:
            result = handle_trip_message(
                tenant=principal.tenant_id, trip_id=trip_id, request_id=request.request_id,
                message=request.message, at=_seoul(request.at) or datetime.now(KST),
                classifier=_lazy("classifier", classifier_factory),
                chat=_lazy("chat", chat_factory), desk=_desk(store), actor_id=principal.key_id,
                policy_search=_lazy("policy", policy_search_factory),
                place_source=_lazy("place", place_factory), selected_item_id=request.item_id,
                location=request.location, controller=http.app.state.runtime.controller,
                resume_case_id=request.case_id, resume_token=request.resume_token,
                expected_version=request.expected_version)
        except TripNotFound:
            raise _error(404, "not_found", "resource not found") from None
        except TeamUnavailable as exc:
            raise HTTPException(503, unavailable_payload(exc)) from exc
        except ControllerError as exc:
            raise _error(401, "invalid_resume_token", str(exc)) from None
        except StateConflict:
            raise _error(409, "state_conflict", "업무 상태가 바뀌었거나 답변할 업무를 지정해야 합니다") from None
        except ValueError as exc:
            raise _error(422, "invalid_customer_input", str(exc)) from None
        _log_turn(principal.tenant_id, trip_id, request.message, result)
        result.pop("answer_web", None)          # 웹 전용 판 — 에이전트는 목록이 든 문장(`answer`)을 읽는다
        return result

    @router.post("/v1/trips/{trip_id}/items/{item_id}/alternate")
    def alternate(trip_id: UUID, item_id: UUID, request: AlternateIn,
                  principal: Principal = Depends(require_scope("trip:write"))):
        return _alternate_result(TripStore(principal.tenant_id), trip_id, item_id, request)

    def _alternate_result(store: TripStore, trip_id: UUID, item_id: UUID, request: AlternateIn) -> dict[str, Any]:
        """다른 안으로 바꾸기 한 건 — 에이전트 입구와 웹 입구(`/v1/web/trips/{id}/items/{item}/alternate`)가 같은 것을 쓴다."""
        duplicate = _already(store, trip_id, request.request_id)
        if duplicate:
            return duplicate
        return _outcome(_desk(store).swap_alternate(
            trip_id=trip_id, item_id=item_id, base_version=request.base_version,
            choice=request.choice, message=request.message, request_id=request.request_id))

    @router.post("/v1/trips/{trip_id}/rollback")
    def rollback(trip_id: UUID, request: RollbackIn,
                 principal: Principal = Depends(require_scope("trip:write"))):
        store = TripStore(principal.tenant_id)
        duplicate = _already(store, trip_id, request.request_id)
        if duplicate:
            return duplicate
        return _outcome(_desk(store).rollback(
            trip_id=trip_id, base_version=request.base_version, to_version=request.to_version,
            message=request.message, request_id=request.request_id))

    @router.get("/plan/{trip_id}")
    def plan(trip_id: UUID, t: str = Query(...), format: str | None = Query(None), download: bool = Query(False)):
        """★로그인 없는 링크. 토큰이 틀리면 **있는지도 말하지 않는다**(404).

        ★`[2026-09-22]` 여행이 **어느 테넌트 것인지 먼저 찾아** 그 테넌트로 토큰을 맞춘다. 전에는
          설정된 테넌트 하나로만 맞춰서, 다른 테넌트의 여행(시나리오 모드의 전용 테넌트)은 통지에
          링크가 실려 나가도 **404** 였다 — 화면에서 링크를 눌러 보고 찾았다.
        ★이 한 줄만 테넌트 조건 없이 읽는다(`CLAUDE.md` §1 의 예외). 이 경로에서는 **링크가 곧
          자격**이고, 여기서 얻는 것은 테넌트 문자열 하나뿐이다. 그 뒤 모든 조회는 그 테넌트로 묶고,
          토큰이 틀리면 여행이 있든 없든 404 다.
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM trips WHERE trip_id=%s", (trip_id,))
            row = cur.fetchone()
        tenant = row[0] if row else settings_module.get_settings().tenant_id
        if not hmac.compare_digest(t, plan_token(tenant, trip_id)):
            raise _error(404, "not_found", "resource not found")
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id)
            view = _trip_view(conn, store, trip_id)
        view.pop("customer_id", None)          # ★링크를 받은 사람에게 내부 id 를 보이지 않는다
        if format == "json":
            return JSONResponse(view)
        if download:
            # ★`[2026-10-04 사용자 결정]` 계획서 **내려받기** — 게스트 데이터는 보존 시간 뒤 지워지니 파일로 가져갈 수 있게 한다. 이 HTML 은 외부 파일 없이 혼자 열린다(밖으로 나가는 것은 지도 링크뿐)
            name = "".join(ch for ch in str(view.get("title") or "plan") if ch not in _FILENAME_BAD).strip()[:60] or "plan"
            return HTMLResponse(_render_plan(view), headers={
                "Content-Disposition": f"attachment; filename=\"triPilot-plan.html\"; filename*=UTF-8''{quote('triPilot-' + name + '.html')}",
                "Cache-Control": "no-store"})
        return HTMLResponse(_render_plan(view))

    @router.get("/booking-change/{booking_id}")
    def booking_change(booking_id: UUID, t: str = Query(...), format: str | None = Query(None)):
        """업체 예약 **변경 링크**(v11 §4-C · §12 DoD-16·17).

        ★계획서 링크와 같은 모양이다 — 로그인 없음 · 토큰이 틀리면 **있는지도 말하지 않는다**(404)
          · 예약이 **어느 테넌트 것인지 먼저 찾아** 그 테넌트로 토큰을 맞춘다.
        ★**아무것도 쓰지 않는다.** 그래서 승인도 scope 도 없다 — 업체 예약을 바꾸는 것은 고객이
          업체 쪽에서 하고, 우리는 무엇을·어떤 대안으로·얼마 차이로 바꿔야 하는지만 보인다.
        ★이 한 줄만 테넌트 조건 없이 읽는다(`CLAUDE.md` §1 의 예외 — 계획서 링크와 같은 이유).
          이 경로에서는 **링크가 곧 자격**이고 여기서 얻는 것은 테넌트 문자열 하나뿐이다.
        """
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT tenant_id FROM bookings WHERE booking_id=%s", (booking_id,))
            row = cur.fetchone()
        tenant = row[0] if row else settings_module.get_settings().tenant_id
        if not hmac.compare_digest(t, change_token(tenant, booking_id)):
            raise _error(404, "not_found", "resource not found")
        with get_connection() as conn:
            view = change_view(conn, tenant_id=tenant, booking_id=booking_id)
        if view is None:
            raise _error(404, "not_found", "resource not found")
        if format == "json":
            return JSONResponse(view)
        return HTMLResponse(render_change(view))

    # ── 보류 제안(「먼저 물어봐줘」 · 변경 안 할 일정) — D-020 ─────────────────
    def _proposal_view(row: dict[str, Any]) -> dict[str, Any]:
        return {"proposal_id": str(row["proposal_id"]), "item_id": str(row["item_id"]),
                "base_version": row["base_version"], "reason": row["reason"],
                "protected_by": row["protected_by"], "safety": row["safety"], "status": row["status"],
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "chosen_key": row["chosen_key"], "causes": row["cause_json"],
                "options": [{"key": o["key"], "rank": o.get("rank"),
                             "name": o.get("option_label") or o.get("name"),
                             "starts_at": o.get("starts_at"),
                             # ★`[2026-09-29 ui 세션 지적]` 조건 풀기 안의 설명(「08:30으로 늦추면」 · 「다음 일정(…) 근처」)
                             "note": o.get("note"),
                             # ★`[2026-09-29]` 관광공사 목록 후보처럼 영업시간·가격을 모르는 안은 경고를 같이 보인다
                             "warnings": list(o.get("warnings") or [])} for o in (row["options_json"] or [])]}

    def _proposals(tenant: str, trip_id: UUID, customer_id: UUID | None = None) -> dict[str, Any]:
        from app.domains.travel_ops.components.planning.pending import PendingStore

        with get_connection() as conn:
            _trip_or_404(conn, TripStore(tenant), trip_id, customer_id)
            rows = PendingStore(tenant).list(conn, trip_id)
        return {"trip_id": str(trip_id), "proposals": [_proposal_view(r) for r in rows]}

    def _stay_recheck(store: TripStore, trip_id: UUID, proposal_id: UUID, key: str,
                      customer_id: UUID | None) -> dict[str, Any] | None:
        """고른 안이 숙소 「다른 후보」의 마이리얼트립 숙소면 그 날짜 1박 값을 다시 본다(`booking/offers/recheck`). 그 밖에는 None.
        읽기만 한다(잠그지 않는다) — 고르기 자체의 판정(기준 판 · 열린 제안)은 뒤의 트랜잭션이 다시 한다."""
        from app.domains.travel_ops.components.booking.offers.recheck import recheck_stay
        from app.domains.travel_ops.components.planning.pending import PendingStore

        with get_connection() as conn:
            proposal = PendingStore(store.tenant_id).get(conn, trip_id, proposal_id)
            if proposal is None or proposal["status"] != "open" or proposal["reason"] != "requested_options":
                return None
            try:
                trip, items = store.latest(conn, trip_id)
            except KeyError:
                return None
        if customer_id is not None and trip["customer_id"] != customer_id:
            return None
        item = next((i for i in items if i.item_id == proposal["item_id"]), None)
        option = next((o for o in proposal["options_json"] or [] if o.get("key") == key), None)
        if item is None or item.kind != "lodging" or option is None or not str(key).startswith(("myrealtrip:", "google:")):
            return None
        check_out = (item.ends_at or item.starts_at + timedelta(days=1))
        sources = _lazy("travel_sources", _travel_sources)
        keyword = item.detail.get("area") or (item.place or {}).get("district") or (item.place or {}).get("name")
        return recheck_stay(getattr(sources, "travel_search", None), option,
                            check_in=_seoul(item.starts_at).date().isoformat(), check_out=_seoul(check_out).date().isoformat(),
                            adults=int(trip.get("party_size") or 1), google=getattr(sources, "stay_google", None), keyword=keyword)

    def _choose(tenant: str, trip_id: UUID, proposal_id: UUID, key: str | None, by: str,
                customer_id: UUID | None = None) -> dict[str, Any]:
        """★한 트랜잭션 — 실패하면 아무것도 안 바뀐다. 먼저 고른 쪽이 이기고 나중 쪽은 409."""
        from app.domains.travel_ops.components.planning.pending import PendingStore, ProposalRefused, choose

        store = TripStore(tenant)
        # ★`[2026-10-13]` 숙소 후보는 고르기 **전에** 1박 값을 다시 본다(잠그기 전 — 바깥 검색을 잠근 채 부르지 않는다). 객실이 없으면 바꾸지 않는다
        price_check = _stay_recheck(store, trip_id, proposal_id, key, customer_id) if key else None
        if price_check and price_check.get("status") == "gone":
            raise _error(409, "option_gone", "그 날짜에 남은 객실이 없어요 — 일정은 그대로 두었어요. 다른 후보를 고르거나 「바꿔 줘」라고 해 주세요",
                         detail={"price_check": price_check})
        try:
            with get_connection() as conn, conn.transaction():
                _trip_or_404(conn, store, trip_id, customer_id)
                places = {str(p["place_id"]): p for p in store.places(conn, trip_id)}
                result = choose(conn=conn, store=store, pending=PendingStore(tenant), trip_id=trip_id,
                                proposal_id=proposal_id, key=key, by=by, places_by_id=places,
                                check=_check(), price_check=price_check)
            # ★`[2026-10-02 결함 인계 #2·#5]` 못 고르는 상태라 **닫은** 제안(`expired` 끝난 일정 · `superseded` 낡은 기준 버전)은 위 트랜잭션이 커밋된 **뒤에**
            #   409 로 알린다 — 예외로 올리면 닫은 기록이 되돌아가 같은 제안이 계속 열려 있었다
            if result.get("status") in ("expired", "superseded"):
                raise ProposalRefused("expired" if result["status"] == "expired" else "stale",
                                      {k: v for k, v in result.items() if k != "status"})
            return result
        except ProposalRefused as refused:
            status = {"not_found": 404, "already_decided": 409, "stale": 409, "expired": 409}.get(refused.code, 422)
            # ★상세는 `detail` 아래에 둔다 — 거절 상세에 `status`·`message` 가 들어 있어 펼치면 인자와 부딪힌다
            raise _error(status, refused.code, "안을 고르지 못했다 — 아무것도 바뀌지 않았다",
                         detail={k: (v if isinstance(v, (int, float, str, bool, type(None), list, dict))
                                     else str(v)) for k, v in refused.detail.items()}) from None

    @router.get("/v1/trips/{trip_id}/proposals")
    def proposals(trip_id: UUID, principal: Principal = Depends(require_scope("trip:read"))):
        return _proposals(principal.tenant_id, trip_id)

    @router.post("/v1/trips/{trip_id}/proposals/{proposal_id}/choose")
    def choose_proposal(trip_id: UUID, proposal_id: UUID, request: ChooseIn,
                        principal: Principal = Depends(require_scope("trip:write"))):
        return _choose(principal.tenant_id, trip_id, proposal_id, request.key, by=principal.key_id)

    # ── 웹(고객 브라우저) — 사용자 식별 키 `X-User-Key` ────────────────────
    #   ★서버용 scope 키를 브라우저에 넣지 않는다. 이 키는 **그 사용자 본인의 여행**만 연다(D-020 · 025).
    #   ★`[2026-10-04 사용자 결정 — D-CS-011]` 브라우저는 **HttpOnly 쿠키 세션**, 에이전트(MCP)·옛 호출자는 키 헤더 — 둘 다 여기서 가른다
    #     (`web_cookie.authenticate`: 둘이 같이 오면 400, 쿠키로 인증된 쓰기는 Origin + CSRF 토큰).
    def _web_customer(http: Request) -> tuple[str, UUID]:
        from app.domains.travel_ops.modules.web_account.web_cookie import authenticate

        who = authenticate(http)
        return who.tenant_id, who.customer_id

    def _web_customer_by_cookie(http: Request) -> tuple[str, UUID]:
        """★`[2026-10-06]` **쿠키 세션으로만** — 사용자 키 · 에이전트 키로 온 요청은 403. 자동 변경을 켜는 권한(항로 지킴이)이 쓴다."""
        from app.domains.travel_ops.modules.web_account.web_cookie import authenticate

        who = authenticate(http)
        if who.via != "cookie":
            raise _error(403, "cookie_session_required",
                         "항로 지킴이는 로그인한 브라우저에서만 켜고 끌 수 있다 — 키로는 바꾸지 않는다")
        return who.tenant_id, who.customer_id

    # ── 계획 읽기 (2026-09-27, 설계서 program/plan/A-COP_고객계획_읽기_설계_2026-09-26.md) ──────────────
    #   ★고객 id 는 키에서 — 몸통으로 받지 않는다(`/v1/web/trips` 와 같은 경계). 읽기는 뒤에서 돈다(사진 한 장 ~45초).
    @router.post("/v1/web/trip-intakes", status_code=202)
    async def web_intake(http: Request, background: BackgroundTasks, text: str = Form(""),
                         files: list[UploadFile] = File(default_factory=list),
                         turnstile_token: str = Form(""),
                         who: tuple[str, UUID] = Depends(_web_customer)):
        """글(붙여 넣은 일정 · 채팅처럼 쓴 계획)과 파일(사진 · PDF · docx · xlsx)을 받는다. 곧바로 접수 id 를 돌려준다.
        ★`[2026-09-28]` 사람 확인(폼 `turnstile_token`) → 횟수 세기(`intake`) 뒤에 받는다(`web_guard.py`)."""
        from app.domains.travel_ops.components.intake.pipeline import IntakeRejected, open_intake, process

        tenant, customer = who
        human = _human(turnstile_token, http)
        _count("intake", tenant, customer, http)
        blobs = [(f.filename or "file", await f.read()) for f in files]
        try:
            with get_connection() as conn:
                intake_id = open_intake(conn, tenant_id=tenant, customer_id=customer, text=text, files=blobs)
        except IntakeRejected as exc:
            raise _error(422, exc.code, exc.message) from None
        if not text.strip() and not blobs:
            # ★`[2026-09-30 사용자 결정]` 빈 접수는 읽을 것이 없다 — 뒤에서 읽지 않고 곧바로 확인 화면(`review`)이다
            return {"intake_id": str(intake_id), "status": "review", "stage": "review", "human_check": human}
        offset = 1 if text.strip() else 0
        chat = _lazy("chat", chat_factory)
        _ctx = _PlaceCtx()
        _kakao_raw = _lazy("kakao", kakao_factory)
        background.add_task(process, get_connection, tenant_id=tenant, intake_id=intake_id,
                            blobs={offset + i: data for i, (_, data) in enumerate(blobs)},
                            see=getattr(chat, "see", None),
                            chat=chat if hasattr(chat, "json") else None,
                            tour=_lazy("place", place_factory),
                            kakao=_kakao_raw,
                            dining=_dining_lookup(_ctx))
        return {"intake_id": str(intake_id), "status": "reading", "stage": "received", "human_check": human}

    @router.get("/v1/web/test-scenarios")
    def web_test_scenarios():
        """공개된 준비 계획 원문 목록만 읽는다. 고객 정보·조회 결과·경보를 반환하지 않는다."""
        from app.domains.travel_ops.components.intake.test_plans import catalog

        return catalog(settings_module.get_settings().tenant_id)

    @router.get("/v1/web/trip-intakes/{intake_id}")
    def web_intake_view(intake_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        """진행 단계 · 원본별 줄 번호 글 · 읽은 항목(값마다 근거) · 확인 필요. ★남의 접수는 404."""
        from app.domains.travel_ops.components.intake.pipeline import view

        tenant, customer = who
        with get_connection() as conn:
            found = view(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id)
        if found is None:
            raise _error(404, "not_found", "resource not found")
        return found

    @router.get("/v1/web/trip-intakes/{intake_id}/route-shapes")
    def web_intake_route_shapes(intake_id: UUID, detail: bool = False, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-04]` 접수 **확인 화면**의 지도에 그릴 경로선 — 이동마다 GeoJSON LineString(등록 여행용 `/trips/{id}/route-shapes` 와 같은 모양,
        `item_id` 만 없다). `from_item_id`·`to_item_id` 는 확인 화면 `review.items[].id`. **저장된 검사만 읽는다**(없으면 이동을 다시 계산하지 않고
        빈 목록) — 우리 도로 그래프로 계산하고 외부 길찾기는 부르지 않는다. ★남의 접수는 404."""
        from app.domains.travel_ops.components.intake import review as review_module

        tenant, customer = who
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, revision FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s",
                        (tenant, intake_id, customer))
            row = cur.fetchone()
            if row is None:
                raise _error(404, "not_found", "resource not found")
            status, revision = row
            stored = review_module.load(conn, tenant, intake_id, revision) if status in ("review", "confirmed") else None
        from app.domains.travel_ops.components.team_hooks import mobility_shapes
        return {"intake_id": str(intake_id), "revision": revision, "detail": detail,
                "shapes": mobility_shapes.review(stored, detail=detail) if stored is not None else [],
                "attribution": "경로선: 지도 데이터 © OpenStreetMap contributors (ODbL)"}

    @router.get("/v1/web/trip-intakes/{intake_id}/moves/{pair}/options")
    def web_intake_move_options(intake_id: UUID, pair: str, http: Request, revision: int | None = Query(default=None, ge=1),
                                who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-07 사용자 결정 — 이동수단 고르기]` 확인 화면의 이동 한 구간(`{pair}` = `{from}~{to}` · 확인 화면 `items[].id` 두 개)을 **지하철 · 버스 · 택시 · 걸음 수단마다**
        계산한 후보 — 줄 순서 고정, 못 만든 수단은 줄이 없고, 시간 안에 못 끝낸 수단은 `fits: null` + `why_not`. 계산은 구간을 열 때 한 건씩 부른다(네 수단 동시 · 느린 구간은 수 초).
        `fits` = 다음 일정 시작 안에 닿고(`slack_min ≥ 0`) 시간표로 확인한 것. 이 기능이 꺼져 있거나 그 구간이 대상이 아니면 404 `not_available`(화면은 고르기 박스를 두지 않는다).
        읽기 전용 — 아무것도 저장하지 않는다. ★남의 접수 404 · 낡은 판(`revision`) 409. 계약: `wiki/records/plans/2026-10-05_이동수단_선택_서버계약안.md`."""
        from app.domains.travel_ops.components.intake import move_options, pipeline

        tenant, customer = who
        from_id, _, to_id = pair.partition("~")
        _count("place_search", tenant, customer, http)
        try:
            with get_connection() as conn:
                return {"intake_id": str(intake_id), **move_options.read(
                    conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id, revision=revision,
                    from_id=from_id, to_id=to_id)}
        except move_options.NotAvailable:
            raise _error(404, "not_available", "이 구간은 수단을 고를 수 없다") from None
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except pipeline.IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None

    @router.post("/v1/web/trip-intakes/{intake_id}/moves/{pair}/mode")
    def web_intake_move_mode(intake_id: UUID, pair: str, request: IntakeModeIn, http: Request,
                             who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-07]` 이동 한 구간의 수단을 고른다 → `{revision, review}`(새 판 · 그 구간이 고른 수단으로 다시 계산된 확인 결과 전체 · `moves[i].mode_choice` · `recommended_mode`).
        서버가 **고른 수단으로 다시 계산해 닿는지 확인한 뒤에만** 받는다(화면이 보낸 값을 믿지 않는다) — 그래서 「다시 제출」이 필요 없다. `mode` = `recommended` 는 되돌리기.
        ★낡은 판 409 `stale_revision` · 안 닿는 수단 · 계산 못 한 수단 · 알 수 없는 수단 422 `mode_not_fit`(`why` = 서버가 완성한 문장) · 남의 접수 · 없는 구간 404.
        고른 구간은 앞뒤 일정이 바뀌어도 다시 시도한다 — 닿으면 유지(`kept`) · 안 닿으면 추천으로 복귀(`dropped` + `why`)."""
        from app.domains.travel_ops.components.intake import move_options, pipeline

        tenant, customer = who
        from_id, _, to_id = pair.partition("~")
        _count("place_search", tenant, customer, http)
        try:
            with get_connection() as conn:
                return move_options.choose(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                           revision=request.revision, from_id=from_id, to_id=to_id, mode=request.mode)
        except move_options.ModeNotFit as exc:
            raise _error(422, "mode_not_fit", exc.why, why=exc.why) from None
        except move_options.NotAvailable:
            raise _error(404, "not_available", "이 구간은 수단을 고를 수 없다") from None
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except pipeline.IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None

    @router.get("/v1/web/trip-intakes/{intake_id}/events")
    async def web_intake_events(intake_id: UUID, http: Request, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-02 사용자 지시]` 접수 **읽기 진행**을 SSE 로 — 뒤에서 도는 읽기(사진 글자 읽기 · 모델 읽기)가 어디까지 왔는지.

        `accepted{state}` → 단계가 바뀔 때마다 `stage{state}` · 조용하면 `beat` → 끝나면(`review` · `confirmed` · `fatal`) `result{state}`.
        `state` = `{status, stage, stage_label, revision, fatal_code, quiet_seconds}`. ★`[2026-10-02]` 그 사이에 **내용 이벤트**가 흐른다(`intake/stream.py`):
        `line`(읽힌 원문 줄) · `item`(찾은 일정 — 시각 → 장소가 채워질 때마다 같은 id 로 다시) · `check`(검사 줄) · `move`(장소 사이 이동) · `progress`(`{phase: places|hours|moves, done, total, current:{id, title}}` — 「3/14 · 광장시장 운영시간 확인 중」) · `done`(검사 끝).
        ★`[2026-10-03]` 검사 진행은 계산이 **끝나는 대로** 나간다(일정마다 `item` · `check` 를 이동 계산 전에, 이동은 구간마다 `move`) — 전에는 검사가 끝난 뒤 한꺼번에 나갔다. 중간 진행은 프로세스 안 보관소(`intake/progress.py`)를 거친다.
        이벤트는 **상태의 복사본**이라 같은 키가 다시 와도 덮으면 되고, 다시 연결하면 지금까지의 상태가 처음부터 온다. 정본은 `GET /v1/web/trip-intakes/{id}` 다.
        뒤에서 읽던 일꾼이 죽어(서버 재시작) 갱신이 `intake_stalled_seconds` 넘게 멈추면 `error{code: stalled, retryable}` 로 끝낸다 —
        영원히 「읽는 중」으로 두지 않는다. 남의 접수 · 없는 접수는 다른 조회와 같은 404. 사용자당 열린 연결이 상한이면 429."""
        from starlette.concurrency import run_in_threadpool
        from starlette.responses import StreamingResponse

        from app.domains.travel_ops.modules.live_progress import op_stream
        from app.domains.travel_ops.components.intake.pipeline import STAGES as INTAKE_STAGES

        tenant, customer = who

        def read() -> dict[str, Any] | None:
            with get_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT status, stage, revision, fatal_code, extract(epoch FROM (now() - updated_at)) "
                            "FROM trip_intakes WHERE tenant_id=%s AND intake_id=%s AND customer_id=%s",
                            (tenant, intake_id, customer))
                row = cur.fetchone()
            if row is None:
                return None
            status, stage, revision, fatal_code, quiet = row
            return {"status": status, "stage": stage, "stage_label": INTAKE_STAGES.get(stage, stage),
                    "revision": revision, "fatal_code": fatal_code, "quiet_seconds": round(float(quiet), 1)}

        if await run_in_threadpool(read) is None:
            raise _error(404, "not_found", "resource not found")
        cfg = op_stream.limits("intake")
        if not op_stream.acquire(tenant, customer, cap=int(cfg["max_per_user"])):
            error = _error(429, "too_many_streams", "열어 둔 실시간 연결이 너무 많다 — 다른 화면을 닫고 다시 시도한다")
            error.headers = {"Retry-After": "5"}
            raise error
        guard = settings_module.get_guardrails()
        from app.domains.travel_ops.components.intake.stream import Feed, sse_chunks

        feed = Feed(tenant, customer, intake_id)

        def content(state: dict[str, Any]) -> list[str]:
            with get_connection() as conn:
                return sse_chunks(feed.poll(conn, state))

        async def extra(state: dict[str, Any]) -> list[str]:
            return await run_in_threadpool(content, state)

        async def flow():
            try:
                async for chunk in op_stream.watch(
                        read, op="intake", cfg=cfg, is_disconnected=http.is_disconnected,
                        done=lambda s: s["status"] in ("review", "confirmed", "fatal"),
                        stage_of=lambda s: s["stage"], label_of_state=lambda s: s["stage_label"],
                        quiet_for=lambda s: s["quiet_seconds"],
                        stalled_after=float(guard.get("travel.op_stream.intake_stalled_seconds")),
                        poll_seconds=float(guard.get("travel.op_stream.intake_poll_seconds")),
                        extra=extra):
                    yield chunk
            finally:
                op_stream.release(tenant, customer)

        return StreamingResponse(flow(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/v1/web/trip-intakes/{intake_id}/edits")
    def web_intake_edit(intake_id: UUID, request: IntakeEditIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """확인 화면에서 고친 값 → 새 판. ★낡은 판(다른 탭에서 먼저 고침)은 409 — 조용히 덮지 않는다."""
        from app.domains.travel_ops.components.intake.pipeline import IntakeConflict, IntakeRejected, edit, view

        tenant, customer = who
        try:
            _ctx = _PlaceCtx()
            _kakao_raw = _lazy("kakao", kakao_factory)
            with get_connection() as conn:
                edit(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id, revision=request.revision,
                     edits=[e.model_dump() for e in request.edits],
                     tour=_lazy("place", place_factory),
                     kakao=_kakao_raw,
                     dining=_dining_lookup(_ctx))
                return view(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        except IntakeRejected as exc:
            raise _error(422, exc.code, exc.message) from None

    @router.post("/v1/web/trip-intakes/{intake_id}/restore")
    def web_intake_restore(intake_id: UUID, request: IntakeRestoreIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """완료된 원판을 새 판으로 전체 복원한다. 현재 잠금·예약·판 충돌은 부분 저장 없이 거절한다."""
        from app.domains.travel_ops.components.intake.pipeline import IntakeConflict, restore

        tenant, customer = who
        try:
            with get_connection() as conn:
                return restore(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                               revision=request.revision, restore_revision=request.restore_revision,
                               item=(request.item.source_id, request.item.index) if request.item else None)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None

    # ── 확인 화면 수정 화면: 대체 후보 · 장소 검색 · 사진 · 전체 자동 추천 · 재검증 (2026-10-02, 계획 확인 시나리오 목업) ─────────
    def _review_call(intake_id: UUID, customer: UUID, tenant: str, revision: int | None, use):
        """현재 판의 검사를 읽어 `use(conn, found)` 를 부른다. ★남의 접수 404 · 낡은 판 409 · 아직 읽는 중이면 409 — 다른 입구와 같다."""
        from app.domains.travel_ops.components.intake.pipeline import IntakeConflict, IntakeRejected, current_review

        try:
            with get_connection() as conn:
                _, found = current_review(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                          revision=revision)
                return use(conn, found)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        except IntakeRejected as exc:
            raise _error(422, exc.code, exc.message) from None

    def _review_item(found: dict[str, Any], source_id: UUID, index: int) -> dict[str, Any]:
        item = next((i for i in found["items"] if i["source_id"] == str(source_id) and i["index"] == index), None)
        if item is None:
            raise _error(404, "item_not_found", "그 일정을 찾지 못했다")
        return item

    @router.get("/v1/web/trip-intakes/{intake_id}/candidates")
    def web_intake_candidates(intake_id: UUID, http: Request, source_id: UUID = Query(),
                              index: int = Query(ge=0, le=500), revision: int | None = Query(default=None, ge=1),
                              who: tuple[str, UUID] = Depends(_web_customer)):
        """이 일정의 **다른 안 셋**(목업의 후보 A·B·C) — 같은 날 앞뒤 일정에 가까운 순, 후보마다 그 일정의 날짜·시각에 맞는지 검사 줄을 붙인다.
        이름이 모호한 곳은 같은 이름의 다른 지점, 아니면 같은 종류의 다른 곳. 고르면 `POST …/edits` 의 `items[n].place` 로 이 `place` 객체를 그대로 보낸다.
        읽기 전용 — 아무것도 저장하지 않는다(카카오 값은 약관상 저장하지 않는다)."""
        from app.domains.travel_ops.components.intake import candidates

        tenant, customer = who

        def use(conn, found):
            item = _review_item(found, source_id, index)
            _count("place_search", tenant, customer, http)
            return {"revision": found["revision"], **candidates.alternatives(
                conn, tenant_id=tenant, review=found, item=item, kakao=_lazy("kakao", kakao_factory),
                chat=_lazy("chat", chat_factory))}      # ★`[2026-10-07]` 이유 문장은 모델이 확인된 값으로 쓴다(저장 · 검사 · 실패하면 틀 문장)

        return _review_call(intake_id, customer, tenant, revision, use)

    @router.get("/v1/web/trip-intakes/{intake_id}/place-search")
    def web_intake_place_search(intake_id: UUID, http: Request, q: str = Query(min_length=2, max_length=60),
                                source_id: UUID = Query(), index: int = Query(ge=0, le=500),
                                revision: int | None = Query(default=None, ge=1),
                                who: tuple[str, UUID] = Depends(_web_customer)):
        """수정 화면의 **장소 검색** — 이름·분류로 관광공사 목록 · 요식 원장 · 카카오를 함께 찾고 앞뒤 일정에서 가까운 순으로 보인다.
        결과마다 그 일정의 날짜·시각에 맞는지 검사 줄이 붙는다. 읽기 전용."""
        from app.domains.travel_ops.components.intake import candidates

        tenant, customer = who
        if len(" ".join(q.split())) < 2:                      # 공백만 보내면 모든 장소가 맞는 검색이 된다
            raise _error(422, "query_too_short", "검색어는 공백을 뺀 두 글자 이상이어야 한다")

        def use(conn, found):
            item = _review_item(found, source_id, index)
            _count("place_search", tenant, customer, http)
            return {"revision": found["revision"], **candidates.search(
                conn, tenant_id=tenant, review=found, item=item, query=q, kakao=_lazy("kakao", kakao_factory))}

        return _review_call(intake_id, customer, tenant, revision, use)

    @router.get("/v1/web/places/photos")
    def web_place_photos(http: Request, ref: str = Query(min_length=3, max_length=80),
                         who: tuple[str, UUID] = Depends(_web_customer)):
        """장소에 **등록된 사진** 주소(관광공사 `tour:<번호>`만). ★저장하지 않는다 — 부를 때마다 받아 그대로 넘기고 출처 표시(`source_note`)를 붙인다.
        사진이 없거나 못 받으면 `photos: []` 와 이유(`reason`)다 — 지어내지 않는다. ★`ref` 는 후보가 주는 값 그대로다(`place:<uuid>` 도 온다 — 42자) — 길이가 모자라 422 가
        나던 것을 80자로 늘렸다(2026-10-03 실제 화면). 관광공사가 아닌 `ref` 는 `no_photo_source` 로 답한다."""
        from app.domains.travel_ops.components.intake import candidates

        tenant, customer = who
        _count("place_search", tenant, customer, http)
        return candidates.photos(_lazy("place", place_factory), ref)

    @router.post("/v1/web/trip-intakes/{intake_id}/autofix")
    def web_intake_autofix(intake_id: UUID, request: IntakeAutofixIn, http: Request,
                           who: tuple[str, UUID] = Depends(_web_customer)):
        """**전체 자동 추천** — 확인이 필요한 일정을 운영시간·휴무·앞뒤 이동까지 검증한 대체 일정(장소+시각)으로 한 번에 바꾼다(`intake/autofix.py`).
        고정한 일정은 건드리지 않는다. 바꿀 것이 있으면 `edits` 와 같은 길로 새 판이 되고(`applied: true`), 없으면 판을 만들지 않고 이유(`kept`)만 돌려준다.
        응답 = `{applied, revision, changed:[{from, to, reason}], kept:[{id, reason}], view}` — `view` 는 `GET …/{id}` 와 같은 모양(새 검사 포함).
        ★`dry_run: true` — **저장하지 않고** 바뀔 모습만: `applied: false` · `dry_run: true` · `view.preview: true` · `view.revision` 은 적용하면 생길 판 번호(실제 적용과 같은 길로 만들어 읽고 되돌린다)."""
        from app.domains.travel_ops.components.intake import pipeline

        tenant, customer = who
        _count("place_search", tenant, customer, http)
        try:
            with get_connection() as conn:
                done = pipeline.autofix(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                        revision=request.revision, tour=_lazy("place", place_factory),
                                        kakao=_lazy("kakao", kakao_factory), dry_run=request.dry_run,
                                        dining=_dining_lookup())
                # 미리 보기는 바뀔 모습(`preview`)을 이미 들고 있다 — 지금 판을 다시 읽어 덮지 않는다
                return {**done, "view": done.get("view") or pipeline.view(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id)}
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except pipeline.IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        except pipeline.IntakeRejected as exc:
            raise _error(422, exc.code, exc.message) from None

    @router.post("/v1/web/trip-intakes/{intake_id}/revalidate")
    def web_intake_revalidate(intake_id: UUID, request: IntakeRevisionIn,
                              who: tuple[str, UUID] = Depends(_web_customer)):
        """**재검증** — 새 판 없이 같은 판의 검사(운영시간·휴무·이동)를 처음부터 다시 계산해 저장된 검사를 새 값으로 바꾼다. 응답은 `GET …/{id}` 와 같은 모양이고
        `review.ready` 가 참이면 「여행 등록」을 켠다. 등록 판정(`confirm`)과 같은 판정기를 쓴다."""
        from app.domains.travel_ops.components.intake import pipeline

        tenant, customer = who
        try:
            with get_connection() as conn:
                return pipeline.revalidate(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                           revision=request.revision)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except pipeline.IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None

    def _intake_survey(tenant: str, intake_id: UUID, given: dict[str, Any] | None) -> dict[str, Any] | None:
        """★`[2026-10-06]` 등록에 실을 설문 = 로딩 중 질문으로 접수에 모아 둔 답 + 등록 요청이 직접 준 설문(요청이 준 값이 이긴다). 둘 다 없으면 None."""
        from app.domains.travel_ops.components.intake import survey_answers

        with get_connection() as conn:
            # ★저장 때 해석한 값(`stored_resolved`)으로 읽는다 — 질문 묶음이 바뀐 뒤에도 옛 답이 새 매핑으로 잘못 읽히지 않는다
            return survey_answers.merged_survey(survey_answers.stored(conn, tenant_id=tenant, intake_id=intake_id), given,
                                                survey_answers.stored_resolved(conn, tenant_id=tenant, intake_id=intake_id))

    @router.post("/v1/web/trip-intakes/{intake_id}/survey")
    def web_intake_survey(intake_id: UUID, request: IntakeSurveyIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-06 사용자 지시]` 로딩 중 질문의 답을 **한 문항씩 바로** 저장한다 → `{ok, answered, questions_version}`(지금까지 답한 모든 문항 번호 · 이 답을 해석한 질문 묶음 버전).

        - 질문은 `GET /v1/web/trip-intakes/{id}` 의 `questions[]`. 답 = `{문항 id: 선택지 id}` — 모르는 문항 · 선택지는 422 `invalid_answers`(하나라도 틀리면 **아무것도 저장하지 않는다**).
        - 부분 답 · 멱등이다. ★**같은 문항을 다시 보내면 덮어쓴다**(마지막 값이 이긴다) — 화면이 앞 질문으로 돌아가 고칠 수 있다.
        - `on_disruption`(replace · ask_first)과 `pace`(relaxed · moderate · packed)도 받는다 — 항로 지킴이 카드 · 계획 담기 화면의 값을 접수에 실어 두기 위해서다(질문 목록에는 없다).
        - ★`[2026-10-06 uiux 요청]` 서버가 그때의 질문 묶음 버전 · 문구 · 라벨 · 슬롯 · 해석한 값을 **답 이력**(`trip_intake_survey_answers`)에 남기고, 등록 때는 **저장 때 해석한 값**으로 설문을 만든다.
          웹은 선택지 번호만 보내고 뜻(구조화 값)을 모른다 — 문항 번호와 슬롯은 다른 칸이고 선택지 ↔ 값 매핑은 서버가 소유한다.
        - 답은 접수에 모아 두었다가 **등록(`confirm` · `plan`)할 때 설문에 합쳐진다.** 등록 요청이 직접 준 `survey` 가 이긴다. 이미 등록된 접수는 409 `intake_confirmed`. 남의 접수는 404.
        """
        from app.domains.travel_ops.components.intake import survey_answers

        tenant, customer = who
        try:
            answers = survey_answers.check(request.answers)
        except survey_answers.InvalidAnswers as invalid:
            raise _error(422, "invalid_answers", "모르는 문항이거나 선택지가 아니다 — 아무것도 저장하지 않았다", problems=invalid.problems) from None
        try:
            with get_connection() as conn, conn.transaction():
                answered = survey_answers.save(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id, answers=answers)
        except survey_answers.IntakeClosed:
            raise _error(409, "intake_confirmed", "이미 등록한 접수라 답을 더할 수 없다") from None
        if answered is None:
            raise _error(404, "not_found", "resource not found")
        return {"ok": True, "answered": answered, "questions_version": survey_answers.QUESTION_SET_VERSION}

    @router.post("/v1/web/trip-intakes/{intake_id}/confirm")
    def web_intake_confirm(intake_id: UUID, request: IntakeConfirmIn, http: Request,
                           who: tuple[str, UUID] = Depends(_web_customer)):
        """「등록하고 관리 시작」. ★서버가 **다시 조립하고 다시 판정**한 뒤 `_create_trip` 한 곳으로 등록한다.

        - `request_id` = `intake:{접수}:r{판}` — 탭 두 개에서 같은 확인을 눌러도 여행은 하나다.
        - 필수값이 비었으면 422 `intake_incomplete` + 문제 목록. 판정기가 막으면 그 422 를 그대로 돌려준다.
        """
        from app.domains.travel_ops.components.intake import reopen
        from app.domains.travel_ops.components.intake.pipeline import IntakeConflict, draft, mark_confirmed
        from app.domains.travel_ops.components.intake import registered_moves, review as review_module

        tenant, customer = who
        try:
            with get_connection() as conn:
                status, existing_trip, built = draft(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                                    revision=request.revision)
                reopened = reopen.session(conn, tenant_id=tenant, intake_id=intake_id)
                # 수정 접수는 「완료」 뒤에도 `_confirm_reopened` 가 같은 판을 돌려준다 — 보통 접수만 여기서 끝낸다
                if reopened is None and status == "confirmed" and existing_trip:
                    store = TripStore(tenant)
                    _trip_or_404(conn, store, existing_trip, customer)
                    return {"intake_id": str(intake_id), "status": "confirmed",
                            "trip": {**_trip_view(conn, store, existing_trip), "created": False}}
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        if reopened is not None:
            return _confirm_reopened(tenant, customer, intake_id, request, http, reopened, status, built)
        if built.problems:
            raise _error(422, "intake_incomplete", "등록 전에 채워야 할 값이 있습니다",
                         problems=[p.as_dict() for p in built.problems])
        _count("confirm", tenant, customer, http)
        with get_connection() as conn:
            checked = review_module.load(conn, tenant, intake_id, request.revision)
        body = {**registered_moves.with_review_moves(built.body, checked), "customer_id": str(customer)}
        survey = _intake_survey(tenant, intake_id, request.survey)
        if survey is not None:
            body["constraints"] = {**body.get("constraints", {}), "survey": survey}
        try:
            create = CreateTrip.model_validate(body)
        except ValidationError as exc:
            raise _error(422, "validation_error", "읽은 값으로 만든 등록 몸통이 계약과 다르다",
                         problems=[{"field": ".".join(str(x) for x in e["loc"]), "reason": e["msg"]}
                                   for e in exc.errors()]) from None
        trip = _create_trip(tenant, create)
        with get_connection() as conn:
            mark_confirmed(conn, tenant_id=tenant, intake_id=intake_id, trip_id=UUID(str(trip["trip_id"])))
        return {"intake_id": str(intake_id), "status": "confirmed", "trip": trip}

    def _confirm_reopened(tenant: str, customer: UUID, intake_id: UUID, request: IntakeConfirmIn, http: Request,
                          reopened: dict[str, Any], status: str, built: Any) -> dict[str, Any]:
        """★`[2026-10-13 사용자 결정]` 수정 접수의 「완료」 — 새 여행이 아니라 **같은 여행의 새 판**(계약 「등록한 여행 고치기」).

        쓰기는 채팅 변경과 같다(`TripDesk._write` — 판 · 변경 이력 · 알림 하나, 낡은 판이면 409). 판정도 채팅 변경과 같이 **이번 변경으로 새로 생긴 위반**만 막는다.
        - `request_id` = `intake:{접수}:r{판}` — 두 번 눌러도 판은 하나. 바뀐 것이 없으면 판을 쓰지 않는다(`changed: false`)
        - 지난 일정(끝 ≤ 지금)을 바꾸거나 빼거나 지난 때에 더하면 422 `item_in_past`
        """
        from app.domains.travel_ops.components.actions.itinerary_actions import introduced_violations
        from app.domains.travel_ops.components.intake import reopen
        from app.domains.travel_ops.components.intake.pipeline import mark_confirmed
        from app.domains.travel_ops.components.itinerary.itinerary import StaleItinerary, fill_weather_sensitive
        from app.domains.travel_ops.components.itinerary.itinerary_changes import with_request

        if request.survey is not None:
            raise _error(422, "trip_field_locked", "등록한 여행의 설문은 여기서 바꾸지 않아요")
        store = TripStore(tenant)
        trip_id, base_version = UUID(reopened["trip_id"]), int(reopened["version"])
        request_id = f"intake:{intake_id}:r{request.revision}"

        def done(version: int, changed: bool) -> dict[str, Any]:
            with get_connection() as conn:
                mark_confirmed(conn, tenant_id=tenant, intake_id=intake_id, trip_id=trip_id)
                return {"intake_id": str(intake_id), "status": "confirmed", "trip": _trip_view(conn, store, trip_id),
                        "version": version, "changed": changed}

        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
            applied = store.version_for_request(conn, trip_id, request_id)
        if applied is not None:
            return done(applied, True)                 # 이미 쓴 판 — 같은 「완료」를 다시 눌렀다
        if status == "confirmed":
            return done(base_version, False)           # 이미 「완료」했고 바뀐 것이 없어 판을 안 쓴 접수
        # 고정 일정(지난 일정 등)이 남아 있으면 앞으로의 일정을 다 빼도 된다 — 「읽은 일정이 없어요」로 막지 않는다
        problems = [p for p in built.problems if not (p.code == "no_items" and reopened["frozen_item_ids"])]
        if problems:
            raise _error(422, "intake_incomplete", "등록 전에 채워야 할 값이 있습니다",
                         problems=[p.as_dict() for p in problems])
        with get_connection() as conn:
            trip, base_items = store.latest(conn, trip_id)
            if trip["version"] != base_version:
                raise _error(409, "stale_itinerary", "그 사이 일정이 바뀌었어요 — 일정 수정을 다시 열어 주세요",
                             version=trip["version"])
            plan = reopen.prepare(conn, tenant_id=tenant, intake_id=intake_id, revision=request.revision,
                                  base_items=base_items, frozen_ids=set(reopened["frozen_item_ids"]),
                                  now=datetime.now(KST))
        if plan.past:
            raise _error(422, "item_in_past", "지난 일정은 바꿀 수 없어요: " + ", ".join(plan.past), items=plan.past)
        if plan.unchanged:
            return done(base_version, False)
        _count("confirm", tenant, customer, http)
        if any(spec["attributes"].get("dining_place_uid") for spec in plan.places.values()):
            from app.domains.travel_ops.components.team_hooks import dining_ledger

            dining_ledger.require_link_core_place()
        summary = plan.summary()
        cause = with_request({"category": "customer_request", "type": "edit", "summary": summary,
                              "intake_id": str(intake_id)}, request_id)
        with get_connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    for item in plan.items:
                        spec = plan.places.get(item.item_id)
                        if spec is None:
                            continue
                        found = None
                        if spec.get("place_id"):
                            # 접수가 아는 행 — 공용 행이거나 이 여행의 행일 때만 그대로 쓴다(다른 여행 전용 행을 가리키지 않게, 029)
                            cur.execute("SELECT place_id FROM places WHERE tenant_id=%s AND place_id=%s "
                                        "AND (trip_scope IS NULL OR trip_scope=%s)", (tenant, spec["place_id"], trip_id))
                            row = cur.fetchone()
                            found = row[0] if row else None
                        item.place_id = found or _store_place(
                            conn, cur, tenant, trip_id, name=spec["name"], kind=spec["kind"], lat=spec["lat"],
                            lon=spec["lon"], weather_sensitive=spec["weather_sensitive"], attributes=spec["attributes"])
                fill_weather_sensitive(conn, tenant)           # 등록과 같다 — 방금 넣은 장소의 실내·야외 모름을 확실한 분류로 채운다
                placed = store.places_by_id(conn, [i.place_id for i in plan.items if i.item_id in plan.places])
                for item in plan.items:
                    if item.item_id in plan.places:
                        item.place = placed.get(item.place_id)
                violations = introduced_violations(trip, base_items, plan.items)
                if violations:
                    raise _error(422, "itinerary_infeasible",
                                 "이 일정은 그대로 수행할 수 없습니다: " + " / ".join(v.reason for v in violations),
                                 violations=[v.as_dict() for v in violations])
                try:
                    version = store.append_version(conn, trip_id=trip_id, base_version=base_version, items=plan.items,
                                                   reason="customer_request", causes=[cause])
                except StaleItinerary:
                    now_trip, _ = store.latest(conn, trip_id)
                    raise _error(409, "stale_itinerary", "그 사이 일정이 바뀌었어요 — 일정 수정을 다시 열어 주세요",
                                 version=now_trip["version"]) from None
                store.enqueue_notice(conn, trip_id=trip_id, version=version, payload={
                    "text": summary + ".", "language": "ko", "causes": [cause],
                    "changed": {"edit": {"changed": plan.changed, "added": plan.added, "removed": plan.removed}},
                    "other_options": [], "replay": False, "version": version})
        return done(version, True)

    @router.post("/v1/web/trip-intakes/{intake_id}/plan")
    def web_intake_plan(intake_id: UUID, request: IntakePlanIn, http: Request,
                        who: tuple[str, UUID] = Depends(_web_customer)):
        """「일정 짜 줘」 → 일정 생성기(`planner.plan_trip`) → **판정을 통과한 초안**을 `_create_trip` 한 곳으로 등록.

        - 선호 문장은 고객이 올린 **원문 그대로**다. 읽은 항목(고정 일정)은 일정 생성기가 받지 않는다 — 화면이 그렇게 말한다.
        - `request_id` = `intake:{접수}:plan:r{판}` — 두 번 눌러도 모델을 다시 부르지 않고 같은 여행을 돌려준다.
        - 생성기가 못 짜면 422(그 이유와 완화 조건) — 지어낸 일정을 등록하지 않는다.
        - ★`[2026-10-02 사용자 지시]` `Accept: text/event-stream` 이면 **실시간 진행(SSE)** — `accepted` → `stage`(planning · checking ·
          registering) · `beat` … → `result`(아래 JSON 과 같은 본문) | `error`(못 짠 이유·완화 조건은 `error` 몸통에 그대로). 이미 등록된 판은 곧바로
          `accepted` → `result`. 스트림이 열리기 **전**의 거절(404 · 409 · 422 날짜 밖 · 429)은 보통의 HTTP 오류다.
        """
        from app.domains.travel_ops.components.planning import planner as planner_module
        from app.domains.travel_ops.components.intake import reopen
        from app.domains.travel_ops.components.intake.pipeline import IntakeConflict, draft, mark_confirmed

        tenant, customer = who
        try:
            with get_connection() as conn:
                _, _, built = draft(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                    revision=request.revision)
                reopened = reopen.session(conn, tenant_id=tenant, intake_id=intake_id)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        if reopened is not None:
            # ★`[2026-10-13]` 일정 짜기는 새 여행을 만든다 — 등록한 여행을 고치는 수정 접수에서는 하지 않는다
            raise _error(409, "intake_reopened", "등록한 여행을 고치는 중이에요 — 일정 짜기는 새 여행에서 해 주세요")
        store = TripStore(tenant)
        request_id = f"intake:{intake_id}:plan:r{request.revision}"
        key = idempotency_key(tenant_id=tenant, request_id=request_id, action_type="trip.create",
                              business_subject=str(customer))
        with get_connection() as conn:
            existing = store.by_request_key(conn, key)
            if existing is not None:
                done = {"intake_id": str(intake_id), "status": "confirmed", "trip": {
                    **_trip_view(conn, store, existing[0]), "created": False}}
                if _wants_stream(http):
                    return _sse_response(tenant, customer, op="plan", http=http, work=None, instant=done)
                return done
        _count("plan", tenant, customer, http)      # ★같은 판 되풀이는 위에서 끝나 세지 않는다
        survey = _intake_survey(tenant, intake_id, request.survey)
        ask = planner_module.PlanRequest(
            city="서울", start_date=request.start_date, days=request.days, party_size=request.party_size,
            preferences=str(built.plan.get("preferences") or ""), title=built.body["title"], locale="ko",
            constraints={**(built.body.get("constraints") or {}), **({"survey": survey} if survey is not None else {}),
                         **({"avoid_districts": list(request.recovery.avoid_districts), "lighter_day": request.recovery.lighter_day}
                            if request.recovery is not None else {})})
        keep = request.keep_read_items and bool(built.body["items"])
        if keep:
            # ★읽은 일정이 요청한 날짜 밖이면 끼울 수 없다 — 조용히 버리지 않고 거절한다
            span = {(request.start_date + timedelta(days=i)).isoformat() for i in range(request.days)}
            outside = [it["title"] for it in built.body["items"] if it["starts_at"][:10] not in span]
            if outside:
                raise _error(422, "read_items_outside_days",
                             "읽은 일정 중 고른 날짜 밖의 것이 있다 — 첫날·일수를 맞추거나 「새로 짜기」로 하세요",
                             items=outside)
        def plan_and_register(progress: Callable[[str], None] | None = None) -> dict[str, Any]:
            """생성 → (읽은 일정과 합치기) → 등록. JSON 입구와 SSE 입구가 **같은 것**을 쓴다. `progress` 는 단계 알림(없으면 안 한다)."""
            say = progress or (lambda name: None)
            say("planning")
            from app.domains.travel_ops.components.team_hooks import dining_ledger

            dining_ledger.require_planner_factory()
            try:
                with get_connection() as conn:
                    outcome = planner_module.plan_trip(
                        conn=conn, tenant_id=tenant, request=ask, chat=_lazy("chat", chat_factory),
                        tour_api=_lazy("place", place_factory),
                        exclude_names=[p["name"] for p in built.body["places"]] if keep else (),
                        # ★`[2026-10-01]` 이름이 달라도 같은 곳(경복궁 건청궁 ↔ 경복궁)은 빼는 데 쓴다 — 고객이 쓴 활동 장소의 좌표
                        exclude_sites=[{"name": p["name"], "latitude": p["lat"], "longitude": p["lon"],
                                        "attributes": p.get("attributes") or {}}
                                       for p in built.body["places"] if p.get("kind") == "activity"] if keep else (),
                        # ★`[2026-10-05 병합]` 식사 후보 · 판정은 요식 원장으로(팀 방식 — 관광공사를 실시간으로 부르지 않는다)
                        ledger=_planner_ledger(conn))
            except planner_module.PlanRefused as refused:
                raise _plan_refused(refused) from None
            draft, merged = outcome.draft, []
            if keep:
                say("checking")
                fixed_places = [{**p, "key": f"fixed-{p['key']}"} for p in built.body["places"]]
                fixed_items = [{**it, "place": f"fixed-{it['place']}" if it.get("place") else None}
                               for it in built.body["items"]]
                draft, merged = planner_module.plan_around(outcome, fixed_items=fixed_items, fixed_places=fixed_places)
            say("registering")
            body = draft.as_create_body(request_id=request_id, customer_id=customer)
            trip = _create_trip(tenant, CreateTrip.model_validate(body))
            with get_connection() as conn:
                mark_confirmed(conn, tenant_id=tenant, intake_id=intake_id, trip_id=UUID(str(trip["trip_id"])))
            return {"intake_id": str(intake_id), "status": "confirmed", "trip": trip,
                    "planner": {"coverage": outcome.coverage, "checks": outcome.checks,
                                "kept_read_items": keep, "merge": merged}}

        if _wants_stream(http):
            return _sse_response(tenant, customer, op="plan", http=http,
                                 work=lambda progress, defer: plan_and_register(progress.stage))
        return plan_and_register()

    @router.post("/v1/web/session", status_code=201)
    def web_session(http: Request, x_turnstile_token: str | None = Header(default=None),
                    body: dict[str, Any] | None = Body(default=None)):
        """첫 방문 — 사용자와 키를 만든다. ★키 원문은 **이번에만** 돌려준다. 사용자에게 보관하게 한다.
        ★키 없이 열린 유일한 쓰기 경로라 **주소마다 한 시간에 몇 개**로 막는다(`web.session.per_ip_hour`).
        ★`[2026-09-28]` 사람 확인(헤더 `X-Turnstile-Token` 또는 몸통 `turnstile_token`)을 먼저 한다 — 가입 폭주의 입구가
          여기다. 세기는 DB(`web_usage`)에서 — 전에는 프로세스 메모리라 재시작하면 풀렸다."""
        from app.domains.travel_ops.modules.web_account.web_session import issue

        tenant = settings_module.get_settings().tenant_id
        human = _human(x_turnstile_token or (body or {}).get("turnstile_token"), http)
        try:
            web_guard.count_session(tenant, ip=_ip(http))
        except web_guard.UsageRefused as refused:
            error = _error(429, "too_many_sessions", "새 키를 너무 많이 받았다 — 잠시 뒤에 다시 하거나 가진 키를 넣는다",
                           retry_after_seconds=refused.retry_after)
            error.headers = {"Retry-After": str(refused.retry_after)}
            raise error from None
        with get_connection() as conn, conn.transaction():
            customer, raw = issue(conn, tenant_id=tenant)
        return {"customer_id": str(customer), "user_key": raw, "human_check": human,
                "notice": "이 키를 따로 잘 보관해 주세요. 다시 보여 드리지 않아요 — 다른 기기에서 이어 쓸 때 필요합니다."}

    @router.post("/v1/web/session/rotate")
    def web_rotate(who: tuple[str, UUID] = Depends(_web_customer)):
        """키를 새로 받는다 — **옛 키는 바로 무효.** 키가 샜다고 의심되면 이것으로 끊는다."""
        from app.domains.travel_ops.modules.web_account.web_session import rotate

        tenant, customer = who
        with get_connection() as conn, conn.transaction():
            raw = rotate(conn, tenant_id=tenant, customer_id=customer)
        return {"customer_id": str(customer), "user_key": raw,
                "notice": "새 키예요. 옛 키는 더 이상 쓸 수 없어요 — 따로 잘 보관해 주세요."}

    @router.get("/v1/web/trips")
    def web_trips(who: tuple[str, UUID] = Depends(_web_customer)):
        tenant, customer = who
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT trip_id, title, latest_version, created_at FROM trips "
                        "WHERE tenant_id=%s AND customer_id=%s ORDER BY created_at DESC", (tenant, customer))
            rows = cur.fetchall()
        return {"trips": [{"trip_id": str(r[0]), "title": r[1], "version": r[2],
                           "created_at": r[3].isoformat()} for r in rows]}

    @router.post("/v1/web/trips", status_code=201)
    def web_create(http: Request, body: dict[str, Any] = Body(...), who: tuple[str, UUID] = Depends(_web_customer)):
        """★고객은 **자기 이름으로만** 등록한다 — 몸통에 `customer_id` 를 받지 않는다."""
        tenant, customer = who
        if "customer_id" in body:
            raise _error(422, "customer_id_not_allowed", "웹에서는 customer_id 를 보내지 않는다 — 키가 정한다")
        try:
            request = CreateTrip.model_validate({**body, "customer_id": str(customer)})
        except ValidationError as exc:
            raise _error(422, "validation_error", "등록 몸통이 계약과 다르다",
                         problems=[{"field": ".".join(str(x) for x in e["loc"]), "reason": e["msg"]}
                                   for e in exc.errors()]) from None
        _count("trip_create", tenant, customer, http)
        return _create_trip(tenant, request)

    @router.post("/v1/web/trips/{trip_id}/delete")
    def web_trip_delete(trip_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        """여행 **즉시 완전 삭제**(D-CS-011 · 요청서 「구현 지침」). 남의 여행 · 없는 여행 · 이미 지운 여행은 모두 같은 404 — 웹은 200 · 404 를 둘 다 「지운 것」으로 읽는다."""
        tenant, customer = who
        with get_connection() as conn, conn.transaction():
            counts = trip_delete.delete_trip(conn, tenant_id=tenant, customer_id=customer, trip_id=trip_id)
        if counts is None:
            raise _error(404, "not_found", "resource not found")
        return {"trip_id": str(trip_id), "status": "deleted"}

    @router.get("/v1/web/trips/{trip_id}")
    def web_detail(trip_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
            return _trip_view(conn, store, trip_id)

    @router.get("/v1/web/trips/{trip_id}/proposals")
    def web_proposals(trip_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        tenant, customer = who
        return _proposals(tenant, trip_id, customer)

    @router.post("/v1/web/trips/{trip_id}/proposals/{proposal_id}/choose")
    def web_choose(trip_id: UUID, proposal_id: UUID, request: ChooseIn,
                   who: tuple[str, UUID] = Depends(_web_customer)):
        tenant, customer = who
        return _choose(tenant, trip_id, proposal_id, request.key, by=f"web:{customer}", customer_id=customer)

    @router.post("/v1/web/trips/{trip_id}/reports")
    def web_report(trip_id: UUID, request: ReportIn, http: Request, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-02]` 웹(사용자 키)용 신고 — 지연 · 휴무 · 품절. 에이전트 입구(`/v1/trips/{id}/reports`)와 **같은 처리**이고
        그 사용자 본인의 여행만 연다. 개인 AI(MCP)가 구조화된 신고를 보내는 길이다(모델을 안 거친다). 남용 방어는 채팅과 같은 `message` 로 센다."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
        _count("message", tenant, customer, http)
        return _report_result(store, trip_id, request)

    @router.post("/v1/web/trips/{trip_id}/items/{item_id}/alternate")
    def web_alternate(trip_id: UUID, item_id: UUID, request: AlternateIn, http: Request,
                      who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-02]` 웹(사용자 키)용 「다른 안으로」 — 에이전트 입구와 **같은 처리**, 그 사용자 본인의 여행만."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
        _count("message", tenant, customer, http)
        return _alternate_result(store, trip_id, item_id, request)

    @router.post("/v1/web/trips/{trip_id}/rollback")
    def web_rollback(trip_id: UUID, request: RollbackIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-09-29]` 웹 「되돌리기」 버튼 — 자동으로 바꾼 일정(설문에서 자동을 고른 고객)을 옛 판으로.
        에이전트 입구(`/v1/trips/{id}/rollback`)와 같은 계산(`TripDesk.rollback`). ★그 사용자 본인의 여행만."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
        duplicate = _already(store, trip_id, request.request_id)
        if duplicate:
            return duplicate
        return _outcome(_desk(store).rollback(
            trip_id=trip_id, base_version=request.base_version, to_version=request.to_version,
            message=request.message, request_id=request.request_id))

    @router.post("/v1/web/trips/{trip_id}/reopen")
    def web_trip_reopen(trip_id: UUID, request: ReopenIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-13 사용자 결정]` 「일정 수정」 — 등록한 여행을 확인 화면 접수로 **다시 연다**(`components/intake/reopen.py`).
        고치기는 기존 `/v1/web/trip-intakes/{intake_id}/*` 그대로, 「완료」는 `/confirm` 이 같은 여행의 새 판을 쓴다. 응답 = 접수 조회 모양 + `reopen`.
        같은 판으로 열린 수정 접수가 있으면 그것을 돌려준다(고치던 값이 남는다)."""
        from app.domains.travel_ops.components.intake import reopen
        from app.domains.travel_ops.components.intake.pipeline import view

        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
            trip, items = store.latest(conn, trip_id)
            if trip["version"] != request.version:
                raise _error(409, "stale_itinerary", "그 사이 일정이 바뀌었어요 — 새로 불러와 주세요", version=trip["version"])
            intake_id, _ = reopen.open_for_trip(conn, tenant_id=tenant, customer_id=customer, trip=trip, items=items,
                                                now=datetime.now(KST))
            return view(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id)

    @router.post("/v1/web/trip-intakes/{intake_id}/cancel")
    def web_intake_cancel(intake_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-13 사용자 지시]` 수정 모드의 「취소」 — 열린 수정 접수를 닫는다(`reopen.cancel`). 고친 값은 여행에 쓰지 않고, 다음 「일정 수정」은 새 접수다."""
        from app.domains.travel_ops.components.intake import reopen
        from app.domains.travel_ops.components.intake.pipeline import IntakeConflict

        tenant, customer = who
        try:
            with get_connection() as conn:
                reopen.cancel(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        return {"intake_id": str(intake_id), "status": "closed"}

    @router.post("/v1/web/trips/{trip_id}/guardian")
    def web_guardian(trip_id: UUID, request: GuardianIn, who: tuple[str, UUID] = Depends(_web_customer_by_cookie)):
        """★`[2026-10-06 사용자 결정]` **항로 지킴이**(일정이 꼬이면 알아서 대체안을 적용하는 모드) 켜기 · 끄기 → `{enabled, since, via}`.

        - 켜기와 끄기는 **같은 입구**다. 켜짐 = 설문 15번 `on_disruption=replace` · 꺼짐 = `ask_first` 를 **명시**한다 — 둘 다 「직접 고른 것」으로 기록된다(`survey_answered`).
        - **로그인한 쿠키 세션**으로만 부른다 — 사용자 키 · 에이전트 키 · 계획서 링크 토큰으로는 안 된다(403 `cookie_session_required`).
          자동 변경은 일정을 실제로 바꾸는 권한이라서다. 디스코드 · 텔레그램 알림에서는 링크로 웹을 열고 **웹이 확인한 뒤** 이 입구를 부른다.
        - 본인 여행이 아니거나 없으면 404. 같은 값을 다시 보내면 아무것도 바꾸지 않고 현재 값을 돌려준다.
        - `since`·`via` 는 **마지막으로 바뀐** 시각 · 곳이다(`trip_guardian_changes`, 추가만 하는 기록).
        """
        tenant, customer = who
        with get_connection() as conn, conn.transaction():
            result = guardian_module.set_enabled(conn, tenant_id=tenant, trip_id=trip_id,
                                                 customer_id=customer, enabled=request.enabled, via=request.via)
        if result is None:
            raise _error(404, "not_found", "resource not found")
        return result

    @router.post("/v1/web/trips/{trip_id}/safety/resume")
    def web_safety_resume(trip_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-06 사용자 결정]` 재난으로 정지된 일정을 **다시 시작**한다 → `{resumed, safety}`(`resumed` = 닫은 정지 수, 정지가 없으면 0 — 오류가 아니다).

        다시 시작은 **사용자가 정한다** — 서버는 사용자가 이미 안전한 곳에 있는지 모른다. 공식 해제가 오면 「해제됐어요」 알림이 나가지만 정지는 이 요청이 풀 때까지 그대로다.
        ★`[결정 2026-10-06 사용자]` 그날 정지도 **자정에 풀리지 않는다** — 이 요청이 풀어야 한다. 풀면 `recovery`(재난 뒤 상황 꾸러미)가 같이 온다. 다시 시작해도 **같은 사건으로 다시 정지하지 않는다**(사건 지문 — `trip_safety_pauses.event_key`). 본인 여행만(남의 것 404)."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
        with get_connection() as conn, conn.transaction():
            closed = safety_pause.SafetyPauses(tenant).resume(conn, trip_id=trip_id, via="web", by=customer)
        with get_connection() as conn:
            current = safety_pause.view(conn, tenant_id=tenant, trip_id=trip_id)
        with get_connection() as conn:
            brief = safety_recovery.build_brief(conn, store=store, tenant_id=tenant, trip_id=trip_id) if closed else None
        return {"resumed": closed, "safety": current, "recovery": brief}

    @router.get("/v1/web/trips/{trip_id}/safety/recovery")
    def web_safety_recovery(trip_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[결정 2026-10-06 사용자]` 재난 뒤 **상황 꾸러미** → `{recovery}`(다시 시작한 정지가 없거나 72시간이 지나면 `null`). 읽기 전용.

        사실(출처가 있는 것)과 **모르는 것**을 따로 싣고, 남은 일정 항목마다 영향 판정(확정 · 불명 · 없음 — 모르면 불명)과 이유를 붙인다. 고르는 것은 `POST` 다."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
            brief = safety_recovery.build_brief(conn, store=store, tenant_id=tenant, trip_id=trip_id)
        return {"recovery": brief}

    @router.post("/v1/web/trips/{trip_id}/safety/recovery")
    def web_safety_recovery_choose(trip_id: UUID, request: RecoveryChoiceIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """재난 뒤 다시 시작할 때 **사용자가 고른 것**을 기록한다 → `{recorded, choice, proposals}`.

        · `keep` — 기록만. 일정은 안 바뀐다
        · `replace_affected` — 영향 **확정** 항목마다 대신 갈 곳을 **제안**한다(`proposals` — 기존 「선택이 필요해요」 제안이고, 고르면 바뀌고 안 고르면 그대로)
        · `replan_all` — 기록만. 개인 AI 가 `tripilot_get_recovery_brief` 로 이 상황을 읽고 다시 짠다
        남의 여행 · 다시 시작한 적 없는 정지는 404 다. `lighter_day` 는 우리가 밀도를 임의로 낮추지 않고 **기록 · 제약 전달**만 한다."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
            try:
                saved = safety_recovery.record_choice(conn, tenant_id=tenant, trip_id=trip_id, customer_id=customer, pause_id=request.pause_id,
                                                      choice=request.choice, lighter_day=request.lighter_day, answers=request.answers)
            except LookupError:
                raise _error(404, "not_found", "resource not found") from None
            brief = safety_recovery.build_brief(conn, store=store, tenant_id=tenant, trip_id=trip_id)
        proposals: list[dict[str, Any]] = []
        if request.choice == "replace_affected" and brief is not None and brief["pause_id"] == str(request.pause_id):
            proposals = safety_recovery.suggest_replacements(_desk(store), store, tenant_id=tenant, trip_id=trip_id, brief=brief, conn_factory=get_connection)
        return {"recorded": saved["recorded"], "choice": request.choice, "lighter_day": request.lighter_day, "proposals": proposals}

    @router.post("/v1/web/trips/{trip_id}/messages")
    def web_message(trip_id: UUID, request: WebMessageIn, http: Request, background: BackgroundTasks,
                    who: tuple[str, UUID] = Depends(_web_customer)):
        """「에이전트에게 변경 요청」 — 자유 문장. 에이전트 API 와 **같은 처리**를 탄다.

        ★`[2026-10-02 사용자 지시]` `Accept: text/event-stream` 으로 부르면 **실시간 진행(SSE)** 으로 답한다 — `accepted` → `stage` ·
        `beat` … → `result`(아래 JSON 과 같은 본문) | `error`. 서버·모델이 멈춰도 사용자가 상태를 알게 한다(`op_stream.py`).
        그렇지 않으면 전처럼 JSON 한 번이다. 스트림이 열리기 **전**의 거절(404 · 429 …)은 두 경우 모두 보통의 HTTP 오류다."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
        _count("message", tenant, customer, http)
        if _wants_stream(http):
            return _sse_response(
                tenant, customer, op="message", http=http,
                work=lambda progress, defer: _web_message_result(
                    tenant, customer, store, trip_id, request, defer=defer, progress=progress.stage,
                    controller=http.app.state.runtime.controller))
        return _web_message_result(tenant, customer, store, trip_id, request, defer=background.add_task,
                                   controller=http.app.state.runtime.controller)

    def _web_message_result(tenant: str, customer: UUID, store: TripStore, trip_id: UUID, request: MessageIn, *,
                            defer: Callable[..., None], progress: Callable[[str], None] | None = None,
                            controller: Any = None) -> dict[str, Any]:
        """채팅 한 번의 처리 — JSON 입구와 SSE 입구가 **같은 것**을 쓴다(한 규칙이 두 벌로 갈라지지 않게)."""
        from app.domains.travel_ops.components.conversation.trip_messages import TripNotFound, handle_trip_message

        from app.domains.travel_ops.instances._shared.itinerary_team import ANSWERS

        try:
            result = handle_trip_message(
                tenant=tenant, trip_id=trip_id, request_id=request.request_id, message=request.message,
                at=_seoul(request.at) or datetime.now(KST), classifier=_lazy("classifier", classifier_factory),
                chat=_lazy("chat", chat_factory), desk=_desk(store), actor_id=f"web:{customer}",
                policy_search=_lazy("policy", policy_search_factory),
                place_source=_lazy("place", place_factory),
                # ★`[2026-09-29]` 사실 질문은 분류(모델)를 기다리지 않고 답한다 — 분류·완료 기록은 응답 뒤에서
                defer=defer, selected_item_id=request.item_id, location=request.location, progress=progress,
                controller=controller, resume_case_id=request.case_id, resume_token=request.resume_token,
                expected_version=request.expected_version, summary_day=getattr(request, "summary_day", None))
        except TripNotFound:
            raise _error(404, "not_found", "resource not found") from None
        except TeamUnavailable as exc:
            raise HTTPException(503, unavailable_payload(exc)) from exc
        except ControllerError as exc:
            raise _error(401, "invalid_resume_token", str(exc)) from None
        except StateConflict:
            raise _error(409, "state_conflict", "업무 상태가 바뀌었거나 답변할 업무를 지정해야 합니다") from None
        except ValueError as exc:
            raise _error(422, "invalid_customer_input", str(exc)) from None
        # ★`[2026-09-27]` 「바꾸지 않아도 되는 결과」는 사람에게 넘길 일이 아니라 답이다 — 대화 경로와 **같은 문장표**
        #   (`itinerary_team.ANSWERS`)를 웹에도 싣는다. 웹이 문장을 따로 지어내지 않게.
        if not result.get("answer") and result.get("status") in ANSWERS:
            result["answer"] = ANSWERS[result["status"]]
        _log_turn(tenant, trip_id, request.message, result)      # 대화 기록은 목록이 든 문장 그대로(글만 읽는 쪽)
        if result.get("answer_web"):
            result["answer"] = result["answer_web"]
        result.pop("answer_web", None)
        return _web_view(tenant, result)

    def _wants_stream(http: Request) -> bool:
        """`Accept: text/event-stream` — 웹이 실시간 진행(SSE)을 원한다."""
        return "text/event-stream" in (http.headers.get("accept") or "").lower()

    def _sse_response(tenant: str, customer: UUID, *, op: str, http: Request, work: Callable[..., dict[str, Any]],
                      instant: dict[str, Any] | None = None):
        """오래 걸리는 웹 일을 SSE 로 — `work(progress, defer)` 는 **스레드에서** 돌고 본문을 돌려준다(`op_stream.run`).
        `instant` 가 있으면 일 없이 곧바로 `accepted` → `result` 만 흘린다(이미 끝난 요청 — 클라이언트가 한 길로 읽게).
        사용자당 열린 실시간 작업이 상한이면 429. ★열린 연결을 세는 것이지 일꾼 수가 아니다 — 끊긴 일꾼은 끝까지 돌고 사라진다."""
        from starlette.responses import StreamingResponse

        from app.domains.travel_ops.modules.live_progress import op_stream

        cfg = op_stream.limits(op)
        if not op_stream.acquire(tenant, customer, cap=int(cfg["max_per_user"])):
            error = _error(429, "too_many_streams", "열어 둔 실시간 연결이 너무 많다 — 다른 화면을 닫고 다시 시도한다")
            error.headers = {"Retry-After": "5"}
            raise error
        job = (lambda progress, defer: instant) if instant is not None else work

        async def flow():
            try:
                async for chunk in op_stream.run(job, op=op, is_disconnected=http.is_disconnected, cfg=cfg):
                    yield chunk
            finally:
                op_stream.release(tenant, customer)       # ★끊겨도 상한이 새지 않게

        return StreamingResponse(flow(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def _web_view(tenant: str, result: dict[str, Any]) -> dict[str, Any]:
        """★`[2026-09-29 사용자 지시]` 근거(`basis` — 원래 모양 그대로)와 해석 결과(`decision`)는 **개발 모드**
        (`web.dev_mode = on`)일 때만 싣는다. 근거 목록은 `basis_sources: [{source}]` — 규정 조각 id(t_doc_… · #c…) ·
        예약 조건 · 사실 답의 조회 출처. 끄면 세 칸을 뺀다. 고객 문장(`answer`)에는 늘 근거 id 가 없다."""
        from app.domains.travel_ops.modules.web_account import web_guard

        if web_guard.values(tenant).get("web.dev_mode") != "on":
            return {k: v for k, v in result.items() if k not in ("basis", "decision", "basis_sources")}
        basis = result.get("basis") or {}
        sources = list(basis.get("sources") or []) if isinstance(basis, dict) else []
        lookup = (basis.get("lookups") or {}) if isinstance(basis, dict) else {}
        if isinstance(lookup, dict) and lookup.get("source"):
            sources.append(str(lookup["source"]))
        return {**result, "basis_sources": [{"source": s} for s in sources]}

    def _log_turn(tenant: str, trip_id: UUID, message: str, result: dict[str, Any]) -> None:
        """★`[2026-09-29]` 대화 기록(034) — 결정 단위가 앞 대화로 「그 식당」「거기」를 푼다. 같은 요청의 되풀이는 적지 않는다."""
        from app.domains.travel_ops.components.conversation import chat_log

        if result.get("status") == "duplicate":
            return
        with get_connection() as conn:
            chat_log.record(conn, tenant_id=tenant, trip_id=trip_id, customer_text=message,
                            answer=result.get("answer"), case_id=result.get("case_id"))

    @router.get("/v1/web/trips/{trip_id}/chat")
    def web_chat(trip_id: UUID, limit: int = Query(40, ge=1, le=200),
                 who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-09-29]` 이 여행의 대화 기록(오래된 것부터) — 웹이 다른 기기에서도 이어 보게. 고객 문장은 가린 값이다."""
        from app.domains.travel_ops.components.conversation import chat_log

        tenant, customer = who
        with get_connection() as conn:
            _trip_or_404(conn, TripStore(tenant), trip_id, customer)
            return {"trip_id": str(trip_id), "turns": chat_log.recent(conn, tenant_id=tenant, trip_id=trip_id,
                                                                     limit=limit)}

    @router.get("/v1/trips/{trip_id}/chat")
    def agent_chat(trip_id: UUID, limit: int = Query(40, ge=1, le=200), customer_id: UUID | None = Query(None),
                   principal: Principal = Depends(require_scope("trip:read"))):
        from app.domains.travel_ops.components.conversation import chat_log

        with get_connection() as conn:
            _trip_or_404(conn, TripStore(principal.tenant_id), trip_id, customer_id)
            return {"trip_id": str(trip_id), "turns": chat_log.recent(conn, tenant_id=principal.tenant_id,
                                                                     trip_id=trip_id, limit=limit)}

    @router.post("/v1/web/warmup")
    def web_warmup(http: Request, background: BackgroundTasks, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-09-29]` 모델 예열 — 화면이 여행·채팅 칸을 열 때 부른다(식은 모델의 첫 채팅이 34초 걸렸다).
        이미 올라가 있으면 아무것도 안 하고, 1분 안 되풀이는 한 번으로, 실제로 부를 때만 남용 방어로 센다(`model_warmup.py`)."""
        from app.domains.travel_ops.modules.live_progress import model_warmup

        tenant, customer = who
        # ★`[2026-10-13]` 규정 검색의 임베딩 모델도 함께 깨운다(`retriever.warm_embedding` — 식은 채로 두면 규정·날씨 질문이 모두 오류였다)
        from app.infrastructure.rag.retriever import warm_embedding

        background.add_task(warm_embedding)
        return model_warmup.warmup(
            _lazy("chat", chat_factory), count=lambda: _count("warmup", tenant, customer, http),
            defer=background.add_task,
            dedupe_seconds=float(settings_module.get_guardrails().get("web_guard.warmup.dedupe_seconds")))

    @router.post("/v1/web/map-load")
    def web_map_load(who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-09-29 사용자 지시]` **구글 지도를 불러와도 되는가** — 화면이 구글 지도를 부르기 **전에** 한 번 묻는다.

        ☆왜 — 구글 지도는 고객 브라우저가 구글을 직접 부른다(지도 한 번 = 요금 단위 `google_maps_dynamic_maps` 1건,
          무료 월 10,000). 서버의 호출 예산(`call_budget`, DB 에서 모든 프로세스가 같이 센다)을 안 지나서 한도를 넘어도 몰랐다.
        ★여기서 한 칸을 확보하고(하루 = 무료 ÷ 32, 월 = 무료 − 하루 — 다른 구글 요금 단위와 같은 규칙), 못 하면
          `allowed: false` — 화면은 구글을 부르지 않고 무료 지도로 보인다(`fallback`).
        ★`[2026-09-29 사용자 결정]` 지도 종류는 운영 설정 `web.map_provider`(osm · google, 기본 osm)가 정한다 — osm 이면
          구글 한도를 세지 않고 `allowed: false, reason: "setting"`. google 이면 한 칸을 확보하고, 못 하면 `reason: "cap"`.
        """
        from app.domains.travel_ops.modules.web_account import web_guard

        tenant, _ = who
        provider = web_guard.values(tenant).get("web.map_provider", "osm")
        if provider != "google":
            return {"provider": provider, "allowed": False, "reason": "setting", "meter": MAP_METER,
                    "used": None, "cap": None, "fallback": "free_map"}
        budget = map_budget()
        allowed = budget.try_reserve(MAP_METER)
        return {"provider": "google", "allowed": allowed, "reason": None if allowed else "cap", "meter": MAP_METER,
                "used": budget.used(MAP_METER), "cap": budget.caps.get(MAP_METER),
                "fallback": None if allowed else "free_map"}

    # ── 고객 연락처 (2026-10-01 사용자 지시 — ui 세션 전달) ─────────────────────────────────────────────
    #   복구 이메일 · 디스코드 웹훅. ★웹훅은 비밀값이자 서버가 나중에 POST 하는 주소(SSRF)라 받을 때 엄격히 검사하고 암호화해서만 저장하며
    #   응답에는 마스킹만 돌려준다(`customer_profile.py` 머리). 남의 키로는 남의 값이 안 보인다(키 → 고객).
    @router.get("/v1/web/profile")
    def web_profile(who: tuple[str, UUID] = Depends(_web_customer)):
        from app.domains.travel_ops.components.customer import profile as customer_profile

        tenant, customer = who
        with get_connection() as conn:
            return _profile_body(customer_profile.read(conn, tenant, customer))

    def _profile_body(view: Any) -> dict[str, Any]:
        """고객 연락처 응답 — ★`discord_connect` 는 조회 · 갱신 · 시험 **어느 응답에나** 싣는다(웹이 응답마다 같은 모양으로 읽는다 — 저장 직후 단추가 사라지지 않게)."""
        from app.domains.travel_ops.modules.web_account import discord_connect
        from app.domains.travel_ops.modules.web_account import telegram_connect

        # ★`[2026-10-05]` 텔레그램 칸도 같은 자리에서 — 어느 응답에나 빠짐없이(웹이 응답마다 같은 모양으로 읽는다). 대화 번호는 어디에도 안 준다
        return {**view.as_dict(), "discord_connect": discord_connect.public_state(), "telegram_connect": telegram_connect.public_state(),
                "telegram": view.telegram_dict(), "notice_channel": view.notice_channel}

    def _require_consent(tenant: str, customer: UUID, code: str) -> None:
        """게이트가 켜져 있으면 이 선택 동의(`alert_channel` 등)가 있어야 한다 — 없으면 403 `consent_required`(항목 `code` 를 싣는다). 게이트가 꺼져 있으면(기본) 아무것도 안 막는다."""
        from app.domains.travel_ops.components.customer import consents

        try:
            with get_connection() as conn:
                consents.require(conn, tenant, customer, code)
        except consents.ConsentError as refused:
            raise _error(refused.status, refused.code, refused.message, **refused.extra) from None

    @router.put("/v1/web/profile")
    def web_profile_update(body: dict[str, Any] = Body(...), who: tuple[str, UUID] = Depends(_web_customer)):
        """부분 갱신 — 칸이 없으면 안 건드리고 null(또는 공백만)이면 지운다. 모르는 칸 · 틀린 값은 422(받은 값을 되돌려 싣지 않는다)."""
        from app.domains.travel_ops.components.customer import profile as customer_profile

        tenant, customer = who
        if str(body.get("discord_webhook_url") or "").strip() or body.get("notice_channel"):
            _require_consent(tenant, customer, "alert_channel")                # 알림 채널을 **저장하는** 일은 동의가 있어야 한다(지우기는 늘 된다)
        try:
            with get_connection() as conn:
                return _profile_body(customer_profile.update(conn, tenant, customer, body))
        except customer_profile.ProfileError as refused:
            raise _error(refused.status, refused.code, refused.message) from None

    # ★`[2026-10-05 ui 세션 요청서 「디스코드 연결 버튼」]` 웹훅을 **붙여넣지 않고** 디스코드 창에서 서버·채널을 고르면 디스코드가 웹훅 주소를 서버에 돌려준다(`discord_connect.py`).
    @router.post("/v1/web/profile/discord/connect/start")
    def web_profile_discord_connect_start(who: tuple[str, UUID] = Depends(_web_customer)):
        """`{authorize_url}` — 웹이 그 주소(`https://discord.com…`)로 브라우저를 보낸다. 디스코드 앱 설정이 없으면 404. 사용자당 한 시간 한도(429 `too_many_requests`)."""
        from app.domains.travel_ops.modules.web_account import discord_connect
        from app.domains.travel_ops.modules.web_account import web_guard

        tenant, customer = who
        if not discord_connect.available():
            raise _error(404, "not_found", "디스코드 연결을 쓸 수 없다 — 붙여넣기로 연결한다")
        _require_consent(tenant, customer, "alert_channel")
        try:
            discord_connect.count_start(tenant, customer)
        except web_guard.UsageRefused as refused:
            error = _error(429, "too_many_requests", "디스코드 연결을 너무 자주 시도했다 — 잠시 뒤에 다시 한다", retry_after_seconds=refused.retry_after)
            error.headers = {"Retry-After": str(refused.retry_after)}
            raise error from None
        with get_connection() as conn, conn.transaction():
            state = discord_connect.begin(conn, tenant_id=tenant, customer_id=customer)
        return JSONResponse({"authorize_url": discord_connect.authorize_url(state)}, headers={"Cache-Control": "no-store"})

    @router.get("/v1/web/profile/discord/connect/callback")
    def web_profile_discord_connect_callback(code: str | None = Query(default=None), state: str | None = Query(default=None),
                                             error: str | None = Query(default=None)):
        """디스코드가 고객 브라우저를 돌려보내는 곳(인증 없음 — 다른 사이트에서 오는 이동이라 쿠키에 기대지 않고 `state` 가 사용자를 정한다).
        어떤 실패든 **웹으로 302 + `?discord=connected|cancelled|expired|failed`** 다(브라우저 이동이라 JSON 오류를 못 읽는다)."""
        from fastapi.responses import RedirectResponse

        from app.domains.travel_ops.modules.web_account import discord_connect

        origin = discord_connect.web_origin()
        if not origin:
            raise _error(503, "web_origin_not_configured", "연결 뒤 돌아갈 웹 주소가 설정되지 않았다")
        with get_connection() as conn:
            result = discord_connect.finish(conn, tenant_id=settings_module.get_settings().tenant_id, state=state, code=code, error=error)
        return RedirectResponse(f"{origin}/mypage?discord={result}", status_code=302,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    # ── 텔레그램으로 알림 받기 (2026-10-05 사용자 지시 — ui 세션 요청서) ──────────────────────────────────
    #   고객이 「텔레그램으로 연결」 → 일회용 코드가 든 링크 → 텔레그램에서 「시작」 → 텔레그램이 아래 웹훅으로 `/start <코드>` 를 보냄 → 대화 번호를 그 사용자에 묶는다(`telegram_connect.py`). 알림만 한다.
    @router.post("/v1/web/profile/telegram/connect/start")
    def web_profile_telegram_connect_start(who: tuple[str, UUID] = Depends(_web_customer)):
        """`{link, expires_at}` — 웹이 그 링크(`https://t.me/<봇>?start=<코드>`)를 연다. 봇 설정이 없으면 404 `not_found` · 사용자당 한 시간 한도(429 `too_many_requests`)."""
        from app.domains.travel_ops.modules.web_account import telegram_connect
        from app.domains.travel_ops.modules.web_account import web_guard

        tenant, customer = who
        if not telegram_connect.available():
            raise _error(404, "not_found", "텔레그램 연결을 쓸 수 없다")
        _require_consent(tenant, customer, "alert_channel")
        try:
            telegram_connect.count_start(tenant, customer)
        except web_guard.UsageRefused as refused:
            error = _error(429, "too_many_requests", "텔레그램 연결을 너무 자주 시도했다 — 잠시 뒤에 다시 한다", retry_after_seconds=refused.retry_after)
            error.headers = {"Retry-After": str(refused.retry_after)}
            raise error from None
        with get_connection() as conn, conn.transaction():
            link = telegram_connect.begin(conn, tenant_id=tenant, customer_id=customer)
        return JSONResponse(link, headers={"Cache-Control": "no-store"})

    @router.post("/v1/web/profile/telegram/test")
    def web_profile_telegram_test(who: tuple[str, UUID] = Depends(_web_customer)):
        """연결된 대화로 시험 메시지 한 줄 — 고객이 누를 때만. `{result: ok|blocked|rate_limited|failed, profile}`. 연결이 없으면 409 `no_telegram` · 너무 자주면 429 `too_soon`."""
        from app.domains.travel_ops.components.customer import profile as customer_profile
        from app.domains.travel_ops.modules.web_account import telegram_connect

        tenant, customer = who
        interval = float(settings_module.get_guardrails().get("travel.profile.test_interval_seconds"))
        try:
            with get_connection() as conn:
                result, view = telegram_connect.send_test(conn, tenant, customer, min_interval_seconds=interval)
        except customer_profile.ProfileError as refused:
            error = _error(refused.status, refused.code, refused.message)
            if refused.retry_after:
                error.headers = {"Retry-After": str(refused.retry_after)}
            raise error from None
        return {"result": result, "profile": _profile_body(view)}

    @router.delete("/v1/web/profile/telegram")
    def web_profile_telegram_disconnect(who: tuple[str, UUID] = Depends(_web_customer)):
        """연결 풀기 — 대화 번호를 지운다. 알림 받는 곳이 텔레그램이었으면 디스코드가 있으면 디스코드로 · 없으면 없음. → `{profile}`."""
        from app.domains.travel_ops.modules.web_account import telegram_connect

        tenant, customer = who
        with get_connection() as conn:
            return {"profile": _profile_body(telegram_connect.disconnect(conn, tenant, customer))}

    @router.post("/v1/telegram/webhook")
    async def telegram_webhook(http: Request):
        """텔레그램이 업데이트를 보내는 곳(인증은 **비밀 헤더**). 헤더가 서버 비밀값과 다르면 401 — 아무것도 안 바뀐다. 맞으면 처리하고 **늘 빠르게 200**(텔레그램은 2xx 가 아니면 같은 업데이트를 다시 보낸다)."""
        from starlette.concurrency import run_in_threadpool

        from app.domains.travel_ops.modules.web_account import telegram_connect

        if not telegram_connect.verify_secret(http.headers.get("X-Telegram-Bot-Api-Secret-Token")):
            raise _error(401, "unauthenticated", "인증하지 못했다")
        try:
            update = await http.json()
        except Exception:                                    # noqa: BLE001 — 텔레그램은 항상 올바른 JSON 을 보낸다
            raise _error(400, "invalid_json", "본문이 JSON 이 아니다") from None
        tenant = settings_module.get_settings().tenant_id

        def work() -> None:
            with get_connection() as conn:
                telegram_connect.handle_update(conn, tenant_id=tenant, update=update, send=telegram_connect.real_send)

        await run_in_threadpool(work)
        return {"ok": True}

    @router.post("/v1/web/profile/discord/test")
    def web_profile_discord_test(who: tuple[str, UUID] = Depends(_web_customer)):
        """저장된 웹훅으로 시험 메시지 한 줄 — 고객이 누를 때만. `{result: ok|invalid|rate_limited|failed, profile: {…}}`.
        마지막 시도에서 `travel.profile.test_interval_seconds` 안이면 429 `too_soon`."""
        from app.domains.travel_ops.components.customer import profile as customer_profile

        tenant, customer = who
        interval = float(settings_module.get_guardrails().get("travel.profile.test_interval_seconds"))
        try:
            with get_connection() as conn:
                result, view = customer_profile.send_test(conn, tenant, customer, min_interval_seconds=interval)
        except customer_profile.ProfileError as refused:
            error = _error(refused.status, refused.code, refused.message)
            if refused.retry_after:
                error.headers = {"Retry-After": str(refused.retry_after)}
            raise error from None
        return {"result": result, "profile": _profile_body(view)}

    @router.get("/v1/web/trips/{trip_id}/events")
    async def web_trip_events(trip_id: UUID, http: Request, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-09-30 사용자 승인 — ui 세션 전달]` **변경 초인종** — 이 여행이 바뀌었다는 신호만 흘린다(`text/event-stream`).

        `event: ready` `{trip_id, version}` 한 번 → 바뀔 때마다 `event: trip.changed` `{trip_id, kinds, version}`
        (`kinds` = itinerary · notice · proposal 중 웹이 다시 읽을 것의 힌트) · 조용하면 20초마다 `: ping`. 내용 · 사용자 키는 싣지 않는다.
        웹은 받으면 지금 있는 조회로 다시 읽는다. 놓친 신호를 되풀이하지 않는다 — 재연결하면 전부 다시 읽는다.
        남의 여행 · 없는 여행은 다른 조회와 같은 404, 사용자당 열린 연결이 상한이면 429. 한 연결은 `max_seconds` 뒤에 닫힌다.
        """
        from starlette.concurrency import run_in_threadpool
        from starlette.responses import StreamingResponse

        from app.domains.travel_ops.components.watch import trip_events

        tenant, customer = who

        def check() -> None:
            with get_connection() as conn:
                _trip_or_404(conn, TripStore(tenant), trip_id, customer)

        await run_in_threadpool(check)
        cfg = trip_events.limits()
        if not trip_events.acquire(tenant, customer, cap=int(cfg["max_per_user"])):
            error = _error(429, "too_many_streams", "열어 둔 실시간 연결이 너무 많다 — 다른 화면을 닫고 다시 시도한다")
            error.headers = {"Retry-After": "5"}
            raise error

        async def flow():
            try:
                async for chunk in trip_events.stream(tenant_id=tenant, trip_id=trip_id, connect=get_connection,
                                                      is_disconnected=http.is_disconnected, cfg=cfg):
                    yield chunk
            finally:
                trip_events.release(tenant, customer)      # ★끊겨도 상한이 새지 않게

        return StreamingResponse(flow(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.get("/v1/web/trips/{trip_id}/notices")
    def web_notices(trip_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        """화면 위쪽 알림 — 그 여행에 나간 알림 전부. `type` 으로 가른다:
        guidance(하루 시작·다음 일정·이동) · proposal_request(선택 요청) · safety_alert · change_notice."""
        tenant, customer = who
        with get_connection() as conn:
            _trip_or_404(conn, TripStore(tenant), trip_id, customer)
            with conn.cursor() as cur:
                cur.execute("SELECT dedupe_key, payload_json, status, available_at FROM outbox "
                            "WHERE tenant_id=%s AND topic='trip.notice' AND dedupe_key LIKE %s "
                            "ORDER BY available_at, dedupe_key", (tenant, f"{trip_id}:%"))
                rows = cur.fetchall()
        return {"notices": [{"key": key.split(":", 1)[1], "type": payload.get("type") or "change_notice",
                             "kind": payload.get("kind"), "text": payload.get("text"),
                             "version": payload.get("version"), "proposal_id": payload.get("proposal_id"),
                             "options": payload.get("options"), "delivery": status,
                             # ★`[2026-09-29]` 자동 변경의 되돌리기 · 「바꿀까요?」 표시 — 화면이 버튼을 그린다(ui 세션 요청)
                             "rollback": payload.get("rollback"), "consent": bool(payload.get("consent")),
                             "consent_key": payload.get("consent_key"),
                             # ★`[2026-10-06]` 항로 지킴이 몫 — `{changed: true}`(켜 둔 사용자에게 자동으로 바꿨다는 표시) · `{offer: {label, via, path, url}}`(꺼 둔 사용자에게 켜기 단추)
                             "guardian": payload.get("guardian"),
                             # ★`[2026-10-06]` 재난 안전 알림의 안내(공식 안내 · 가까운 대피 장소 · 「일정 다시 시작」) — 안전 알림(`safety_pause` · `safety_release`)에만 있다
                             "safety": payload.get("guidance") if payload.get("reason") in ("safety_pause", "safety_release") else None,
                             "at": at.isoformat()} for key, payload, status, at in rows]}

    # ── 개인 AI 입구(MCP)의 읽기 도구 둘 — 일정 위험 점검 · 이동 판정 `[2026-10-06 사용자 요청]` ──────────────
    #   ★MCP 는 새 규칙을 만들지 않는다 — 이 두 입구가 규칙이고 MCP 도구(`mcp_server.py`)는 그대로 부르는 얇은 어댑터다. 둘 다 **읽기**(아무것도 쓰지 않는다 · 쓰기 스위치와 무관).
    slots: dict[str, Any] = {}

    @contextlib.contextmanager
    def _slot(name: str):
        """같은 입구를 **동시에** 처리하는 수의 상한(`travel.mcp.max_concurrent.<이름>`) — 넘으면 기다리지 않고 503 `busy`(5초 뒤 다시). 하루 횟수 제한과 별개로 연결 · 스레드를 지킨다
        (캐시 읽기는 호출마다 새 DB 연결을 여러 번 열고 항목마다 스레드를 만든다)."""
        import threading

        from app.core.settings import get_guardrails

        if name not in slots:
            slots[name] = threading.BoundedSemaphore(max(1, int(get_guardrails().get(f"travel.mcp.max_concurrent.{name}"))))
        if not slots[name].acquire(blocking=False):
            error = _error(503, "busy", "지금 같은 요청이 몰려 있다 — 잠시 뒤 다시 한다", retry_after_seconds=5)
            error.headers = {"Retry-After": "5"}
            raise error
        try:
            yield
        finally:
            slots[name].release()

    def _owned_trip_items(tenant: str, trip_id: UUID, customer: UUID):
        """본인 여행의 최신 판과 항목 — 한 번만 읽는다(남의 것 · 없는 것은 같은 404)."""
        with get_connection() as conn:
            try:
                trip, items = TripStore(tenant).latest(conn, trip_id)
            except KeyError:
                raise _error(404, "not_found", "resource not found") from None
        if trip["customer_id"] != customer:
            raise _error(404, "not_found", "resource not found")
        return trip, items

    @router.get("/v1/web/trips/{trip_id}/risks")
    def web_trip_risks(trip_id: UUID, http: Request, item_id: UUID | None = Query(default=None), within_hours: float | None = Query(default=None, gt=0, le=48),
                       fresh: bool = Query(default=False), at: datetime | None = Query(default=None), who: tuple[str, UUID] = Depends(_web_customer)):
        """**일정 위험 점검** — 내 여행의 곧 시작할 일정(기본 지금부터 `travel.mcp.risk_check.horizon_hours` 시간 안 · 진행 중 포함)마다 외부 정보 6종
        (날씨 예보 · 기상 특보 · 재난문자 · 교통 통제 · 대기질 · 지진)이 문제를 가리키나. 항목마다 `problem`(원인) · `clear` · `unknown`(확인 불가 — 못 확인한 종류 목록).

        - ★기본(`fresh=false`)은 **감시가 모아 둔 공유 응답 캐시만 읽는다** — 바깥에 새로 묻지 않아 하루 한도(감시 · 채팅 몫)를 안 쓴다. 캐시에 없는 종류는 `unknown`(지어내지 않는다).
        - `fresh=true` 는 캐시에 없는 것을 새로 부르되 **낮은 우선순위**다(소스의 하루 · 이번 달 한도의 `fresh_share` 까지만 쓴다) · 한 번에 `fresh_max_items` 개까지 · 횟수는 `risk_check`(키 · 주소 · 서비스) 로 센다(429 `usage_limit` · 503 `service_daily_cap`).
        - `item_id` 로 한 항목만 · `within_hours`(최대 48) · `at`(기준 시각 — 시험 · 재생용, 없으면 지금).
        - 본인 여행만(남의 것 · 없는 것 404). 읽기 전용이다."""
        from app.core.settings import get_guardrails
        from app.domains.travel_ops.components.watch.risk_report import build_report

        tenant, customer = who
        guard = get_guardrails()
        factory = fresh_check_factory if fresh else cached_check_factory
        if factory is None:
            raise _error(503, "risk_check_unavailable", "일정 위험 점검 소스가 이 서버에 조립되어 있지 않다")
        if at is not None and not 2000 <= at.year <= 2100:
            raise _error(422, "invalid_request", "at 은 2000~2100년 사이여야 한다")
        trip, items = _owned_trip_items(tenant, trip_id, customer)
        if item_id is not None and not any(i.item_id == item_id for i in items):
            raise _error(404, "not_found", "resource not found")
        _count("risk_check" if fresh else "risk_read", tenant, customer, http)          # 캐시만 읽는 호출도 센다 — 바깥 한도는 안 쓰지만 서버 조회 부하는 쓴다
        horizon = float(within_hours if within_hours is not None else guard.get("travel.mcp.risk_check.horizon_hours"))
        limit = int(guard.get("travel.mcp.risk_check.fresh_max_items" if fresh else "travel.mcp.risk_check.max_items"))
        with _slot("risk_check"):                                                        # 동시에 몰려도 연결 · 스레드를 다 쓰지 못한다
            return build_report(trip_id=trip_id, version=int(trip["version"]), items=items, check=_lazy("risk_" + ("fresh" if fresh else "cached"), factory),
                                at=_seoul(at) or datetime.now(KST), horizon_hours=horizon, max_items=limit, item_id=item_id, mode="fresh" if fresh else "cached",
                                snapshot_hours=float(guard.get("travel.mcp.risk_check.snapshot_hours")))

    @router.post("/v1/web/moves/judge")
    def web_move_judge(request: MoveJudgeIn, http: Request, who: tuple[str, UUID] = Depends(_web_customer)):
        """**이동 판정** — 두 장소 → 출발 시각 · 경로(노선) · 소요 · 근거 등급(`timetable` 시간표 판정 · `estimate` 직선 어림 · `none` 근거 없음) · 확인 시각.

        - 장소는 이름 · 좌표(`lat` · `lon`) 또는 내 여행의 일정 항목(`item_id` + `trip_id` — 본인 여행만). 서울 밖은 판정하지 않는다(`out_of_scope`).
        - 시각은 `depart_at` 이나 `arrive_by` 중 **하나**(둘 다 주면 422, 둘 다 없으면 지금 출발). ★판정기가 꺼져 있거나 못 하면 그 사실을 그대로 돌려준다(`status` = `unavailable` · `no_route`) — 경로 · 시각을 지어내지 않는다.
        - 이 서버의 시간표 · 도로 그래프와 자체 길찾기 서버(GraphHopper)만 쓴다(바깥 **유료** 소스를 부르지 않는다). 횟수는 `move_judge` 로 센다. 읽기 전용이다."""
        from app.domains.travel_ops.components.itinerary import move_judge

        tenant, customer = who
        if request.depart_at is not None and request.arrive_by is not None:
            raise _error(422, "invalid_request", "depart_at 과 arrive_by 는 둘 중 하나만 줄 수 있다")
        trip, items = None, []
        if request.trip_id is not None:
            trip, items = _owned_trip_items(tenant, request.trip_id, customer)

        def end(side: MoveEndIn, key: str) -> dict[str, Any]:
            try:
                if side.item_id is not None:
                    if trip is None:
                        raise _error(422, "trip_id_required", "일정 항목(item_id)으로 가리키려면 trip_id 가 필요하다")
                    found = next((i for i in items if i.item_id == side.item_id), None)
                    if found is None:
                        raise _error(404, "not_found", "resource not found")
                    if found.place is None or found.place.get("latitude") is None:
                        raise _error(422, "invalid_place", "그 항목은 장소(좌표)가 없다 — 이동 판정을 할 수 없다")
                    return move_judge.as_place({"name": found.place.get("name") or found.title, "lat": found.place["latitude"],
                                                "lon": found.place["longitude"]}, key=key)
                return move_judge.as_place({"name": side.name, "lat": side.lat, "lon": side.lon}, key=key)
            except move_judge.InvalidPlace as exc:
                raise _error(422, "invalid_place", f"{'출발' if key == 'origin' else '도착'} 장소를 읽지 못했다 — {exc}") from None

        for moment in (request.depart_at, request.arrive_by):
            if moment is not None and not 2000 <= moment.year <= 2100:
                raise _error(422, "invalid_request", "시각은 2000~2100년 사이여야 한다")
        origin, dest = end(request.origin, "origin"), end(request.destination, "destination")
        _count("move_judge", tenant, customer, http)
        with _slot("move_judge"):                                                        # 판정기는 계산이 무겁다 — 동시 처리 상한
            return move_judge.judge(origin=origin, dest=dest, now=datetime.now(KST), depart_at=_seoul(request.depart_at), arrive_by=_seoul(request.arrive_by),
                                    party_size=(trip or {}).get("party_size"), constraints=(trip or {}).get("constraints"))

    @router.get("/v1/web/trips/{trip_id}/route-shapes")
    def web_route_shapes(trip_id: UUID, detail: bool = False, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-10-04]` 지도에 그릴 **경로선** — 이동 항목마다 GeoJSON LineString. 우리 도로 그래프(지도 원본 OSM)로 직접 계산해 내린다 —
        외부 길찾기 API 를 부르지 않는다(`mobility/route_shape.py`). 그 사용자 본인의 여행만. 못 그린 구간은 직선 + `grade=근거없음` + `note`.
        지도 원본 출처 표기(ODbL)를 `attribution` 으로 같이 준다 — 화면은 선을 그릴 때 보여야 한다."""
        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
            _trip, items = store.latest(conn, trip_id)
            from app.domains.travel_ops.components.intake import registered_moves
            checked = registered_moves.load_trip_review(conn, tenant_id=tenant, customer_id=customer, trip_id=trip_id)
            items = registered_moves.recover_for_routes(items, checked)
        from app.domains.travel_ops.components.team_hooks import mobility_shapes
        return {"trip_id": str(trip_id), "detail": detail, "shapes": mobility_shapes.items(items, detail=detail),
                "attribution": "경로선: 지도 데이터 © OpenStreetMap contributors (ODbL)"}

    return router


__all__ = ["build_trip_router", "change_token", "change_url", "plan_token", "plan_url"]
