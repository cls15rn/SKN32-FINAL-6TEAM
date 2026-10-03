# -*- coding: utf-8 -*-
"""여행 API — 일정 등록 · 조회 · 고객 신고 · 재요청(다른 안 · 되돌림) · 계획서 링크.

★**왜 `app/modules/` 에 있나** — presentation 은 도메인을 import 하지 못한다
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
import hashlib
import hmac
import html
import json
from typing import Any, Callable, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import (APIRouter, BackgroundTasks, Body, Depends, File, Form, Header, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core import settings as settings_module
from app.core.idempotency import idempotency_key

from app.infrastructure.db.session import get_connection
from app.presentation.security import Principal, require_scope

from .activity.csv_places import CsvPlaceLookup as _CsvPlaceLookup
from .itinerary import Item, TripStore
from .trip_desk import TripDesk

# CSV 로드는 서버 기동 시 한 번만 — intake rate limit 폴백용
_csv_places = _CsvPlaceLookup()


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


class _CsvFallbackTour:
    """CSV 전용 장소 조회 — CSV에 없으면 None(not_found).

    activity_total_data.csv(1,586개 서울 액티비티)만 사용한다.
    외부 API(tour_api · kakao)를 호출하지 않으므로 rate limit · 403 오류가 없다.
    CSV에 없는 장소는 not_found로 처리한다.
    ctx 가 있으면 확정 좌표를 다음 조회의 near 힌트로 넘긴다.
    """

    def __init__(self, real: Any, csv_lookup: _CsvPlaceLookup,
                 ctx: _PlaceCtx | None = None) -> None:
        self._real = real
        self._csv = csv_lookup
        self._ctx = ctx
        self.misses: dict[str, int] = {}
        self.had_deferred: bool = False

    def set_current_date(self, date_str: str | None) -> None:
        if self._ctx is not None:
            self._ctx.current_datetime = date_str

    def find(self, place_name: str, *, area_code: str | None = None, **kw: Any) -> dict[str, Any] | None:
        near = self._ctx.get_near() if self._ctx else None
        before = self._csv.misses.get("deferred_no_near", 0)
        result = self._csv.find(place_name, near=near)
        if result is None:
            if self._csv.misses.get("deferred_no_near", 0) > before:
                self.had_deferred = True
            else:
                self.misses["not_found"] = self.misses.get("not_found", 0) + 1
            return None
        if self._ctx is not None:
            try:
                self._ctx.add(float(result["latitude"]), float(result["longitude"]))
            except (KeyError, TypeError, ValueError):
                pass
        return result


class _KakaoNearHint:
    """Kakao 검색에 확정된 장소 좌표를 near 힌트로 자동 주입하는 래퍼."""

    def __init__(self, kakao: Any, ctx: _PlaceCtx) -> None:
        self._kakao = kakao
        self._ctx = ctx

    def search(self, query: str, *, near: tuple[float, float] | None = None, **kw: Any) -> Any:
        return self._kakao.search(query, near=near or self._ctx.get_near(), **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._kakao, name)

#: ★대상 도시는 서울 하나다(v11 §1). 시간대 없이 온 시각은 서울 시각으로 읽는다.
KST = ZoneInfo("Asia/Seoul")

CheckFactory = Callable[[], Callable[..., dict[str, Any]]]


class PlaceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    lat: float
    lon: float
    weather_sensitive: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)


#: ★외부 서비스에서 받은 장소 — 공용 장소 표에 쌓지 않고 **그 여행 전용 행**으로 넣는다(마이그레이션 029).
#:  `places[].attributes.source` 로 가린다. 일정 생성기의 관광공사 후보도 이 값을 단다(`planner.py`).
EXTERNAL_PLACE_SOURCES = frozenset({"tour_api", "kakao", "google_places"})


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


class IntakeConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    #: ★`[2026-09-28]` 여행 시작 설문(`TripSurvey`, 판 `2026-09-24.v1`) — 선택. 등록 몸통의 `constraints.survey` 로
    #:  실어 `_create_trip` 이 검사한다(틀리면 422 `invalid_survey`). 전에는 이 흐름에 설문을 실을 곳이 없었다
    survey: dict[str, Any] | None = None


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


class MessageIn(BaseModel):
    """고객 **자유 문장** — 에이전트가 옮기지 않고 그대로 보낸다."""
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    at: datetime | None = None


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


# ★`status` 를 **위치 전용**(`/`)으로 받는다 — `**extra` 에 상세로 `status` 가 들어오면(예: 아직 등록할 수 없는 접수의 현재 상태
#   `IntakeConflict(..., status=...)`) 같은 이름이 둘이라 `TypeError: got multiple values for argument 'status'` 로 409 가 서버 오류(500)가 됐다.
def _error(status: int, code: str, message: str, /, **extra: Any) -> HTTPException:
    return HTTPException(status, {"error": {"code": code, "message": message, **extra}})


def _seoul(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=KST) if moment.tzinfo is None else moment


# ── 계획서 링크 ──────────────────────────────────────────────────
# ★`[2026-09-20]` 구현은 `plan_link.py` 로 옮겼다 — 통지·안내를 만드는 쪽이 FastAPI 를 끌고 오지
#   않고 링크를 붙일 수 있게. 여기서 다시 내보내므로 부르는 쪽은 안 바뀐다.
from .change_link import change_token, change_url, change_view, render_change  # noqa: E402
from .itinerary_checks import Part, check_itinerary, parts_from_items
from .density import measure_density
from .plan_link import plan_token, plan_url        # noqa: E402  (자리를 지켜 읽기 쉽게 둔다)
from .route_uses import route_problems  # noqa: E402
from .survey import apply_survey  # noqa: E402
from .trip_facts import booking_fact  # noqa: E402


# ── 보기 ────────────────────────────────────────────────────────
def _item_view(item: Item) -> dict[str, Any]:
    return {"item_id": str(item.item_id), "seq": item.seq, "kind": item.kind,
            "title": item.title, "place": (item.place or {}).get("name"),
            "starts_at": item.starts_at.isoformat(),
            "ends_at": item.ends_at.isoformat() if item.ends_at else None,
            "changed": item.replaces_item_id is not None,
            # ★`[2026-09-30]` 식당 가격 비교(`same_or_lower` · `higher` · `unknown` · `won`) — 원래 식당 대비.
            #   구글 금액은 내려 주지 않는다(저장 금지 — 비교 결과만 기록돼 있다). 비교하지 않았으면 None
            "price_compare": item.detail.get("price_compare"),
            "other_options": [{"key": a["key"], "name": a.get("option_label") or a["name"],
                               "price_compare": a.get("price_compare")}
                              for a in item.detail.get("alternates") or []],
            "customer_pinned": bool(item.detail.get("customer_pinned")),
            # ★`[2026-09-27]` 웹 지도 핀 · 예약 표시. 좌표는 그 고객 자신의 여행 장소다(다른 고객에게 가지 않는다)
            "lat": (item.place or {}).get("latitude"), "lon": (item.place or {}).get("longitude"),
            # ★`[2026-09-28]` 채팅의 예약 답과 같은 판정(`trip_facts.booking_fact`) — 전에는 `detail.reserved` 를 안 봤다
            "booked": booking_fact(item)[0] == "있음"}


_CAUSE_FIELDS = ("category", "type", "kind", "summary", "message", "to_version", "mode")


def _trip_view(conn, store: TripStore, trip_id: UUID) -> dict[str, Any]:
    trip, items = store.latest(conn, trip_id)
    history = [{"version": row["version"], "reason": row["reason"],
                "causes": [{k: cause.get(k) for k in _CAUSE_FIELDS if cause.get(k) is not None}
                           for cause in (row["causes"] or [])],
                "at": row["created_at"].isoformat()}
               for row in store.versions(conn, trip_id)]
    return {"trip_id": str(trip["trip_id"]), "customer_id": str(trip["customer_id"]),
            "title": trip["title"], "locale": trip["locale"], "party_size": trip["party_size"],
            "version": trip["version"], "items": [_item_view(item) for item in items],
            "history": history, "plan_url": plan_url(store.tenant_id, trip["trip_id"]),
            **measure_density(parts_from_items(items), trip.get("constraints") or {})}


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


def build_trip_router(*, check_factory: CheckFactory | None = None,
                      classifier_factory: Callable[[], Any] | None = None,
                      chat_factory: Callable[[], Any] | None = None,
                      place_factory: Callable[[], Any] | None = None,
                      kakao_factory: Callable[[], Any] | None = None,
                      policy_search_factory: Callable[[], Any] | None = None) -> APIRouter:
    """★점검기·분류기·추출용 LLM 은 **처음 쓸 때** 만든다 — 앱 기동이 기다리지 않게."""
    router = APIRouter()
    cache: dict[str, Any] = {}

    def _lazy(name: str, factory: Callable[[], Any] | None):
        if factory is None:
            return None
        if name not in cache:
            cache[name] = factory()
        return cache[name]

    def _check():
        return _lazy("check", check_factory)

    def _desk(store: TripStore) -> TripDesk:
        return TripDesk(store=store, connection_factory=get_connection, check=_check())

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
        violations = check_itinerary(
            [Part(seq=it.seq, kind=it.kind, title=it.title, starts_at=_seoul(it.starts_at),
                  ends_at=_seoul(it.ends_at),
                  place=_place_view(it.place, request.places), route=request.routes.get(str(it.route)),
                  detail=it.detail)
             for it in request.items],
            constraints=request.constraints, party_size=request.party_size)
        if violations:
            raise _error(422, "itinerary_infeasible",
                         "이 일정은 그대로 수행할 수 없습니다: "
                         + " / ".join(v.reason for v in violations),
                         violations=[v.as_dict() for v in violations])
        key = idempotency_key(tenant_id=tenant, request_id=request.request_id,
                              action_type="trip.create", business_subject=str(request.customer_id))
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
                    trip_id = _insert(conn, store, request, key, body_sha)
                    created = True
            view = _trip_view(conn, store, trip_id)
        return {**view, "created": created}

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
                # ★장소는 테넌트 안에서 (이름, 종류)로 하나다(UNIQUE). 이미 있으면 **그것을 쓴다.**
                #   ☆2026-09-14 — 처음엔 무조건 INSERT 해서, 두 번째 여행이 경복궁을 적자
                #     500 이 났다(개발 서버에서 발견. 시험은 여행마다 테넌트를 새로 만들어
                #     못 잡았다). 보낸 속성은 **빈 칸만 채운다** — 카탈로그 값을 고객 한 명의
                #     제출이 덮어쓰지 않게(`EXCLUDED || places` 는 오른쪽이 이긴다).
                if str(place.attributes.get("source") or "") in EXTERNAL_PLACE_SOURCES:
                    # ☆같은 여행 안에서 같은 장소를 두 번 적을 수 있다 — 그 여행 행을 다시 쓴다
                    cur.execute(
                        "INSERT INTO places (tenant_id,name,kind,latitude,longitude,weather_sensitive,"
                        "attributes,trip_scope) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (tenant_id, trip_scope, name, kind) WHERE trip_scope IS NOT NULL "
                        "DO UPDATE SET attributes = EXCLUDED.attributes || places.attributes "
                        "RETURNING place_id",
                        (tenant, place.name, place.kind, place.lat, place.lon, place.weather_sensitive,
                         json.dumps(place.attributes, ensure_ascii=False), trip_id))
                else:
                    cur.execute(
                        "INSERT INTO places (tenant_id,name,kind,latitude,longitude,weather_sensitive,"
                        "attributes) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (tenant_id, name, kind) WHERE trip_scope IS NULL DO UPDATE "
                        "SET attributes = EXCLUDED.attributes || places.attributes "
                        "RETURNING place_id",
                        (tenant, place.name, place.kind, place.lat, place.lon, place.weather_sensitive,
                         json.dumps(place.attributes, ensure_ascii=False)))
                ids[place.key] = cur.fetchone()[0]
        items = [Item(item_id=uuid4(), seq=it.seq, kind=it.kind, title=it.title,
                      place_id=ids.get(it.place) if it.place else None,
                      starts_at=_seoul(it.starts_at), ends_at=_seoul(it.ends_at),
                      detail={**it.detail,
                              **({"route": it.route, "route_def": request.routes[it.route]}
                                 if it.route else {})})
                 for it in request.items]
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
        from . import planner as planner_module

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
        try:
            with get_connection() as conn:
                outcome = planner_module.plan_trip(conn=conn, tenant_id=tenant, request=ask,
                                                   chat=_lazy("chat", chat_factory),
                                                   tour_api=_lazy("place", place_factory))
        except planner_module.PlanRefused as refused:
            raise _error(422, refused.code, refused.message, **refused.detail) from None
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
        store = TripStore(principal.tenant_id)
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
    def message(trip_id: UUID, request: MessageIn,
                principal: Principal = Depends(require_scope("trip:write"))):
        """고객 자유 문장 → **Case → 분류 → 추출 → 여행 창구 → Case 닫기**(v11 §5 경로).

        ★처리 규칙은 `trip_messages.handle_trip_message` 한 곳에 있다 — 시나리오 모드도
          같은 것을 쓴다(한 규칙이 두 벌로 갈라지지 않게).
        """
        from .trip_messages import TripNotFound, handle_trip_message

        store = TripStore(principal.tenant_id)
        try:
            return handle_trip_message(
                tenant=principal.tenant_id, trip_id=trip_id, request_id=request.request_id,
                message=request.message, at=_seoul(request.at) or datetime.now(KST),
                classifier=_lazy("classifier", classifier_factory),
                chat=_lazy("chat", chat_factory), desk=_desk(store), actor_id=principal.key_id,
                policy_search=_lazy("policy", policy_search_factory),
                place_source=_lazy("place", place_factory))
        except TripNotFound:
            raise _error(404, "not_found", "resource not found") from None

    @router.post("/v1/trips/{trip_id}/items/{item_id}/alternate")
    def alternate(trip_id: UUID, item_id: UUID, request: AlternateIn,
                  principal: Principal = Depends(require_scope("trip:write"))):
        store = TripStore(principal.tenant_id)
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
    def plan(trip_id: UUID, t: str = Query(...), format: str | None = Query(None)):
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
                             "starts_at": o.get("starts_at")} for o in (row["options_json"] or [])]}

    def _proposals(tenant: str, trip_id: UUID, customer_id: UUID | None = None) -> dict[str, Any]:
        from .pending import PendingStore

        with get_connection() as conn:
            _trip_or_404(conn, TripStore(tenant), trip_id, customer_id)
            rows = PendingStore(tenant).list(conn, trip_id)
        return {"trip_id": str(trip_id), "proposals": [_proposal_view(r) for r in rows]}

    def _choose(tenant: str, trip_id: UUID, proposal_id: UUID, key: str | None, by: str,
                customer_id: UUID | None = None) -> dict[str, Any]:
        """★한 트랜잭션 — 실패하면 아무것도 안 바뀐다. 먼저 고른 쪽이 이기고 나중 쪽은 409."""
        from .pending import PendingStore, ProposalRefused, choose

        store = TripStore(tenant)
        try:
            with get_connection() as conn, conn.transaction():
                _trip_or_404(conn, store, trip_id, customer_id)
                places = {str(p["place_id"]): p for p in store.places(conn, trip_id)}
                return choose(conn=conn, store=store, pending=PendingStore(tenant), trip_id=trip_id,
                              proposal_id=proposal_id, key=key, by=by, places_by_id=places,
                              check=_check())
        except ProposalRefused as refused:
            status = {"not_found": 404, "already_decided": 409, "stale": 409}.get(refused.code, 422)
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
    def _web_customer(x_user_key: str | None = Header(default=None)) -> tuple[str, UUID]:
        from .web_session import resolve

        tenant = settings_module.get_settings().tenant_id
        with get_connection() as conn, conn.transaction():
            customer = resolve(conn, tenant_id=tenant, raw=x_user_key)
        if customer is None:
            raise _error(401, "unauthenticated", "사용자 키가 없거나 맞지 않는다")
        return tenant, customer

    # ── 계획 읽기 (2026-09-27, 설계서 program/plan/A-COP_고객계획_읽기_설계_2026-09-26.md) ──────────────
    #   ★고객 id 는 키에서 — 몸통으로 받지 않는다(`/v1/web/trips` 와 같은 경계). 읽기는 뒤에서 돈다(사진 한 장 ~45초).
    @router.post("/v1/web/trip-intakes", status_code=202)
    async def web_intake(background: BackgroundTasks, text: str = Form(""),
                         files: list[UploadFile] = File(default_factory=list),
                         who: tuple[str, UUID] = Depends(_web_customer)):
        """글(붙여 넣은 일정 · 채팅처럼 쓴 계획)과 파일(사진 · PDF · docx · xlsx)을 받는다. 곧바로 접수 id 를 돌려준다."""
        from .intake.pipeline import IntakeRejected, open_intake, process

        tenant, customer = who
        blobs = [(f.filename or "file", await f.read()) for f in files]
        try:
            with get_connection() as conn:
                intake_id = open_intake(conn, tenant_id=tenant, customer_id=customer, text=text, files=blobs)
        except IntakeRejected as exc:
            raise _error(422, exc.code, exc.message) from None
        offset = 1 if text.strip() else 0
        chat = _lazy("chat", chat_factory)
        _ctx = _PlaceCtx()
        _kakao_raw = _lazy("kakao", kakao_factory)
        background.add_task(process, get_connection, tenant_id=tenant, intake_id=intake_id,
                            blobs={offset + i: data for i, (_, data) in enumerate(blobs)},
                            see=getattr(chat, "see", None),
                            chat=chat if hasattr(chat, "json") else None,
                            tour=_CsvFallbackTour(_lazy("place", place_factory), _csv_places, _ctx),
                            kakao=_KakaoNearHint(_kakao_raw, _ctx) if _kakao_raw is not None else None)
        return {"intake_id": str(intake_id), "status": "reading", "stage": "received"}

    @router.get("/v1/web/trip-intakes/{intake_id}")
    def web_intake_view(intake_id: UUID, who: tuple[str, UUID] = Depends(_web_customer)):
        """진행 단계 · 원본별 줄 번호 글 · 읽은 항목(값마다 근거) · 확인 필요. ★남의 접수는 404."""
        from .intake.pipeline import view

        tenant, customer = who
        with get_connection() as conn:
            found = view(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id)
        if found is None:
            raise _error(404, "not_found", "resource not found")
        return found

    @router.post("/v1/web/trip-intakes/{intake_id}/edits")
    def web_intake_edit(intake_id: UUID, request: IntakeEditIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """확인 화면에서 고친 값 → 새 판. ★낡은 판(다른 탭에서 먼저 고침)은 409 — 조용히 덮지 않는다."""
        from .intake.pipeline import IntakeConflict, IntakeRejected, edit, view

        tenant, customer = who
        try:
            _ctx = _PlaceCtx()
            _kakao_raw = _lazy("kakao", kakao_factory)
            with get_connection() as conn:
                edit(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id, revision=request.revision,
                     edits=[e.model_dump() for e in request.edits],
                     tour=_CsvFallbackTour(_lazy("place", place_factory), _csv_places, _ctx),
                     kakao=_KakaoNearHint(_kakao_raw, _ctx) if _kakao_raw is not None else None)
                return view(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        except IntakeRejected as exc:
            raise _error(422, exc.code, exc.message) from None

    @router.post("/v1/web/trip-intakes/{intake_id}/confirm")
    def web_intake_confirm(intake_id: UUID, request: IntakeConfirmIn,
                           who: tuple[str, UUID] = Depends(_web_customer)):
        """「등록하고 관리 시작」. ★서버가 **다시 조립하고 다시 판정**한 뒤 `_create_trip` 한 곳으로 등록한다.

        - `request_id` = `intake:{접수}:r{판}` — 탭 두 개에서 같은 확인을 눌러도 여행은 하나다.
        - 필수값이 비었으면 422 `intake_incomplete` + 문제 목록. 판정기가 막으면 그 422 를 그대로 돌려준다.
        """
        from .intake.pipeline import IntakeConflict, draft, mark_confirmed

        tenant, customer = who
        try:
            with get_connection() as conn:
                _, _, built = draft(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                    revision=request.revision)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        if built.problems:
            raise _error(422, "intake_incomplete", "등록 전에 채워야 할 값이 있습니다",
                         problems=[p.as_dict() for p in built.problems])
        body = {**built.body, "customer_id": str(customer)}
        if request.survey is not None:
            body["constraints"] = {**body.get("constraints", {}), "survey": request.survey}
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

    @router.post("/v1/web/trip-intakes/{intake_id}/plan")
    def web_intake_plan(intake_id: UUID, request: IntakePlanIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """「일정 짜 줘」 → 일정 생성기(`planner.plan_trip`) → **판정을 통과한 초안**을 `_create_trip` 한 곳으로 등록.

        - 선호 문장은 고객이 올린 **원문 그대로**다. 읽은 항목(고정 일정)은 일정 생성기가 받지 않는다 — 화면이 그렇게 말한다.
        - `request_id` = `intake:{접수}:plan:r{판}` — 두 번 눌러도 모델을 다시 부르지 않고 같은 여행을 돌려준다.
        - 생성기가 못 짜면 422(그 이유와 완화 조건) — 지어낸 일정을 등록하지 않는다.
        """
        from . import planner as planner_module
        from .intake.pipeline import IntakeConflict, draft, mark_confirmed

        tenant, customer = who
        try:
            with get_connection() as conn:
                _, _, built = draft(conn, tenant_id=tenant, customer_id=customer, intake_id=intake_id,
                                    revision=request.revision)
        except LookupError:
            raise _error(404, "not_found", "resource not found") from None
        except IntakeConflict as exc:
            raise _error(409, exc.code, exc.message, **exc.detail) from None
        store = TripStore(tenant)
        request_id = f"intake:{intake_id}:plan:r{request.revision}"
        key = idempotency_key(tenant_id=tenant, request_id=request_id, action_type="trip.create",
                              business_subject=str(customer))
        with get_connection() as conn:
            existing = store.by_request_key(conn, key)
            if existing is not None:
                return {"intake_id": str(intake_id), "status": "confirmed", "trip": {
                    **_trip_view(conn, store, existing[0]), "created": False}}
        ask = planner_module.PlanRequest(
            city="서울", start_date=request.start_date, days=request.days, party_size=request.party_size,
            preferences=str(built.plan.get("preferences") or ""), title=built.body["title"], locale="ko",
            constraints={"survey": request.survey} if request.survey is not None else {})
        keep = request.keep_read_items and bool(built.body["items"])
        if keep:
            # ★읽은 일정이 요청한 날짜 밖이면 끼울 수 없다 — 조용히 버리지 않고 거절한다
            span = {(request.start_date + timedelta(days=i)).isoformat() for i in range(request.days)}
            outside = [it["title"] for it in built.body["items"] if it["starts_at"][:10] not in span]
            if outside:
                raise _error(422, "read_items_outside_days",
                             "읽은 일정 중 고른 날짜 밖의 것이 있다 — 첫날·일수를 맞추거나 「새로 짜기」로 하세요",
                             items=outside)
        try:
            with get_connection() as conn:
                outcome = planner_module.plan_trip(
                    conn=conn, tenant_id=tenant, request=ask, chat=_lazy("chat", chat_factory),
                    tour_api=_lazy("place", place_factory),
                    exclude_names=[p["name"] for p in built.body["places"]] if keep else ())
        except planner_module.PlanRefused as refused:
            raise _error(422, refused.code, refused.message, **refused.detail) from None
        draft, merged = outcome.draft, []
        if keep:
            fixed_places = [{**p, "key": f"fixed-{p['key']}"} for p in built.body["places"]]
            fixed_items = [{**it, "place": f"fixed-{it['place']}" if it.get("place") else None}
                           for it in built.body["items"]]
            draft, merged = planner_module.plan_around(outcome, fixed_items=fixed_items, fixed_places=fixed_places)
        body = draft.as_create_body(request_id=request_id, customer_id=customer)
        trip = _create_trip(tenant, CreateTrip.model_validate(body))
        with get_connection() as conn:
            mark_confirmed(conn, tenant_id=tenant, intake_id=intake_id, trip_id=UUID(str(trip["trip_id"])))
        return {"intake_id": str(intake_id), "status": "confirmed", "trip": trip,
                "planner": {"coverage": outcome.coverage, "checks": outcome.checks,
                            "kept_read_items": keep, "merge": merged}}

    @router.post("/v1/web/session", status_code=201)
    def web_session(http: Request):
        """첫 방문 — 사용자와 키를 만든다. ★키 원문은 **이번에만** 돌려준다. 사용자에게 보관하게 한다.
        ★키 없이 열린 유일한 쓰기 경로라 **주소마다 한 시간에 몇 개**로 막는다(`security.web_session_issue_per_hour`)."""
        from .web_session import issue, issue_wait

        wait = issue_wait(http.client.host if http.client else "unknown")
        if wait:
            error = _error(429, "too_many_sessions", "새 키를 너무 많이 받았다 — 잠시 뒤에 다시 하거나 가진 키를 넣는다",
                           retry_after_seconds=int(wait))
            error.headers = {"Retry-After": str(int(wait))}
            raise error
        tenant = settings_module.get_settings().tenant_id
        with get_connection() as conn, conn.transaction():
            customer, raw = issue(conn, tenant_id=tenant)
        return {"customer_id": str(customer), "user_key": raw,
                "notice": "이 키를 따로 잘 보관해 주세요. 다시 보여 드리지 않아요 — 다른 기기에서 이어 쓸 때 필요합니다."}

    @router.post("/v1/web/session/rotate")
    def web_rotate(who: tuple[str, UUID] = Depends(_web_customer)):
        """키를 새로 받는다 — **옛 키는 바로 무효.** 키가 샜다고 의심되면 이것으로 끊는다."""
        from .web_session import rotate

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
    def web_create(body: dict[str, Any] = Body(...), who: tuple[str, UUID] = Depends(_web_customer)):
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
        return _create_trip(tenant, request)

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

    @router.post("/v1/web/trips/{trip_id}/messages")
    def web_message(trip_id: UUID, request: MessageIn, who: tuple[str, UUID] = Depends(_web_customer)):
        """「에이전트에게 변경 요청」 — 자유 문장. 에이전트 API 와 **같은 처리**를 탄다."""
        from .trip_messages import TripNotFound, handle_trip_message

        tenant, customer = who
        store = TripStore(tenant)
        with get_connection() as conn:
            _trip_or_404(conn, store, trip_id, customer)
        from .itinerary_team import ANSWERS

        try:
            result = handle_trip_message(
                tenant=tenant, trip_id=trip_id, request_id=request.request_id, message=request.message,
                at=_seoul(request.at) or datetime.now(KST), classifier=_lazy("classifier", classifier_factory),
                chat=_lazy("chat", chat_factory), desk=_desk(store), actor_id=f"web:{customer}",
                policy_search=_lazy("policy", policy_search_factory),
                place_source=_lazy("place", place_factory))
        except TripNotFound:
            raise _error(404, "not_found", "resource not found") from None
        # ★`[2026-09-27]` 「바꾸지 않아도 되는 결과」는 사람에게 넘길 일이 아니라 답이다 — 대화 경로와 **같은 문장표**
        #   (`itinerary_team.ANSWERS`)를 웹에도 싣는다. 웹이 문장을 따로 지어내지 않게.
        if not result.get("answer") and result.get("status") in ANSWERS:
            result["answer"] = ANSWERS[result["status"]]
        return result

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
                             "at": at.isoformat()} for key, payload, status, at in rows]}

    @router.post("/v1/web/warmup")
    def web_warmup(background: BackgroundTasks, who: tuple[str, UUID] = Depends(_web_customer)):
        """★`[2026-09-29]` 모델 예열 — 화면이 여행·채팅 칸을 열 때 부른다(식은 모델의 첫 채팅이 34초 걸렸다).
        이미 올라가 있으면 아무것도 안 하고, 1분 안 되풀이는 한 번으로 줄인다(`model_warmup.py`).

        ★develop 판은 **남용 방어 없이** 잇는다(`count=lambda: None`) — 웹 남용 방어(`web_guard`)가 아직 develop 에 없다.
          남용 방어는 사용자 결정(2026-09-28)으로 어차피 꺼져 있어 달라지는 것은 「사용량 기록이 안 남는다」 하나다.
          전체 동기화 때 role-manager 판(`count=lambda: _count("warmup", ...)`)으로 덮는다."""
        from . import model_warmup

        return model_warmup.warmup(
            _lazy("chat", chat_factory), count=lambda: None, defer=background.add_task,
            dedupe_seconds=float(settings_module.get_guardrails().get("web_guard.warmup.dedupe_seconds")))

    return router


__all__ = ["build_trip_router", "change_token", "change_url", "plan_token", "plan_url"]
