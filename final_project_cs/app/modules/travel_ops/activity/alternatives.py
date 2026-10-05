# -*- coding: utf-8 -*-
"""대체 장소 후보 — 「문제있음」 판정 뒤에만 부른다(wiki/teams/activity.md
「대안 생성 규칙」 ①~④).

★**계산만 한다. LLM 을 부르지 않는다.** 후보 풀(카탈로그 행)을 받아
  거르고 줄 세울 뿐이다 — 풀을 어디서 읽어 오는지(DB조회·기타지도API)는
  이 모듈의 경계 밖이다(작업자 A 범위).

★**여기서 고른 후보도 판정이 아니다.** 통지 전에 `check_feasible`(①검증
  규칙)을 다시 통과해야 한다(v11 §5). 이 모듈의 ① 가용성 필터는 **명백히
  닫힌 곳을 미리 빼는 것**일 뿐이다.

| 단계 | 함수 | 하는 일 |
|---|---|---|
| ① 가용성 | `closed_on`·`open_at` | 휴무일(`closed_days`)과 운영시간(`business_hours`)을 **가장 먼저** 본다 — 명백히 닫힌 곳만 뺀다 |
| ② 유사도 | `similar` | 원래 장소와 같은 갈래(관광타입·대/중/소분류)만 남긴다 |
| ②' 반경 | `distance_km`·`search_steps` | 원래 장소에서 반경 1km 안부터 1km씩 넓힌다(최대 10km) |
| ③ 선호도 | `FALLBACK_DROPS` | 모자라면 정해진 순서로 분류 필드를 빼고 다시 거른다 |
| ④ 순위 | `rank_alternatives` | 점수 정렬(반경 1km 안의 같은 브랜드 먼저, 거리·분류 가까움) → 같은 브랜드 체인은 가장 가까운 1곳(`one_per_brand`) → 화면 3곳 + 「더보기」 10곳까지. 장소마다 추천 이유 한 줄(`reason_line`) |

★`[2026-10-02]` **근처는 시군구가 아니라 반경(km)으로 잰다.** 시군구로 재면 같은 구의 먼 곳이
  옆 구의 가까운 곳보다 앞서고(경계 문제), 구마다 넓이가 달라 넓히는 기준이 일정하지 않다.
  거리는 두 단계로 잰다 — DB 조회가 `bounding_box()` 사각형(최대 반경)으로 먼저 거르고
  (`db_search/place_candidates.py`), 남은 후보만 여기서 하버사인으로 정확히 잰다.
★반경은 「더보기」까지 채울 `KEEP`(10)곳이 남을 때까지 넓히고(`[2026-10-03]` 3곳 → 10곳), `RADIUS_MAX_KM` 에서 멈춘다(무한 확장 방지).
  그래도 0건이면 「근처에 조건에 맞는 장소가 없다」다.

★브랜드 매장(올리브영·다이소·아트박스·무신사)은 같은 브랜드가 가장 비슷한 대체다.
  다만 **반경 1km 안의 같은 브랜드만** 앞세운다 — 그보다 먼 같은 브랜드보다
  더 가까운 다른 매장이 있으면 그쪽이 낫다(거리도 무시 못 한다).
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

#: ② 유사도에 쓰는 필드. 전부 TourAPI 원본 필드명이다(CSV 컬럼과 같다).
#:  ★`[2026-10-02]` `sigungucode` 는 뺐다 — 근처 여부는 아래 반경으로 잰다.
SIMILARITY_FIELDS = ("contenttypeid", "lclsSystm1", "lclsSystm2", "lclsSystm3")

#: ②' 반경 — 1km 에서 시작해 1km 씩, 최대 10km 까지 넓힌다.
RADIUS_START_KM = 1.0
RADIUS_STEP_KM = 1.0
RADIUS_MAX_KM = 10.0
#: 화면에 보이는 수 / 「더보기」까지 보관하는 수.
#:  ★`KEEP` 곳이 남으면 반경·조건을 더 넓히지 않는다 — 화면 3곳만 채우고 멈추면 「더보기」가 비어서다.
SHOW = 3
KEEP = 10
#: 같은 브랜드를 앞세우는 거리 — 이보다 먼 같은 브랜드는 거리로만 겨룬다.
BRAND_NEARBY_KM = RADIUS_START_KM
#: 바운딩 박스용 — 서울(북위 약 37.5°) 기준 1km 의 위도·경도 폭.
LAT_DEG_PER_KM = 0.009
LON_DEG_PER_KM = 0.0113

#: ③ 선호도별 폴백 — 모자라면 **앞에서부터 한 단계씩** 뺀다(2026-10-01 팀 합의, 2026-10-02 반경으로 교체).
#:  - 이동 1순위: 거리 우선 — 반경마다 ①소분류 ②중분류 ③대분류(+타입)를 먼저 빼 보고, 그래도 모자라면 반경을 넓힌다.
#:  - 활동 1순위: 대분류(+타입) 고정 — 분류 단계마다 반경을 1→10km 넓혀 보고, 그래도 모자라면 ①소분류 ②중분류를 뺀다.
#:  ★타입(`contenttypeid`)은 **대분류와 한 묶음**이다. 대분류와 거의 1:1 이라(관광타입 38=쇼핑, 15=행사,
#:   14=문화시설) 따로 풀 이유가 없고, 풀어야 한다면 대분류와 함께 **맨 마지막**에 푼다.
FALLBACK_DROPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "mobility": (("lclsSystm3",), ("lclsSystm2",), ("lclsSystm1", "contenttypeid")),
    "activity": (("lclsSystm3",), ("lclsSystm2",)),
}

#: 선호도 `None` 일 때 **「더보기」에만** 푸는 조건(2026-10-03). 화면 3곳은 정확한 분류만 쓴다.
MORE_ONLY_DROPS = ("lclsSystm3",)

_WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")
_WEEKLY = re.compile(r"매주\s*([^/<(※]*)")
_RANGE = re.compile(r"([월화수목금토일])요일\s*~\s*([월화수목금토일])요일")
_SINGLE = re.compile(r"([월화수목금토일])요일")
#: 원문이 「모른다」고 말하는 표현. 여기 걸리면 열었다고도 닫았다고도 안 한다.
_UNKNOWN_MARKERS = ("상이", "참조", "확인", "문의")


def closed_on(closed_days: str | None, at: datetime) -> bool | None:
    """`at` 요일이 원문의 정기휴무 요일인가. 모르면 `None`.

    ★`_weekday_closure_match`와 같은 원칙 — **"매주 <요일>" 하나만** 본다.
      "단, 공휴일과 겹치면 개방" 같은 예외 조건, 1월 1일·설·추석 같은 날짜
      휴무는 반영하지 않는다. `~` 범위("매주 토요일~일요일")와 "주말"만
      펴서 읽는다 — 그 이상은 파싱하지 않는다.

    | 원문 | 결과 |
    |---|---|
    | 비었음 · "점포별 상이" · "홈페이지 참조" | `None` (모름) |
    | "연중무휴" · "매주 월요일"(요청이 토요일) | `False` |
    | "매주 월요일"(요청이 월요일) · "주말"(요청이 일요일) | `True` |
    """
    text = (closed_days or "").strip()
    if not text or any(marker in text for marker in _UNKNOWN_MARKERS):
        return None
    when = at if at.tzinfo else at.replace(tzinfo=UTC)
    closed: set[int] = set()
    if "주말" in text:
        closed |= {5, 6}
    for weekly in _WEEKLY.findall(text):
        for start, end in _RANGE.findall(weekly):
            i, j = _WEEKDAYS.index(start), _WEEKDAYS.index(end)
            span = range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)]
            closed |= set(span)
        closed |= {_WEEKDAYS.index(d) for d in _SINGLE.findall(weekly)}
    return when.weekday() in closed


KST = timezone(timedelta(hours=9))
_TIME_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*[~\-–]\s*(\d{1,2}):(\d{2})")
_ALWAYS_OPEN = re.compile(r"상시|24\s*시간")
#: 이 말이 있으면 원문을 해석하지 않는다(모름). 층·시설·계절·휴게처럼 시간표 하나로 못 읽는 표현이다.
_HOURS_UNSURE = ("상이", "참조", "확인", "문의", "별도", "변동", "브레이크", "휴게", "[", "<br", "하계", "동계",
                 "성수기", "비수기", "라스트", "예약")
_DAY_TOKENS = {"평일": {0, 1, 2, 3, 4}, "주말": {5, 6}, "매일": set(range(7))}
_DAY_CHARS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_HOLIDAY = re.compile(r"공휴일|휴일")
_DAY_RANGE = re.compile(r"([월화수목금토일])(?:요일)?\s*[~\-]\s*([월화수목금토일])(?:요일)?")


def _hours_segment_days(label: str) -> set[int] | None:
    """구간 앞뒤 글자에서 적용 요일을 읽는다. 라벨이 없으면 매일. 못 읽는 글자가 남으면 `None`."""
    rest = _HOLIDAY.sub("", label)
    days: set[int] = set()
    for first, last in _DAY_RANGE.findall(rest):          # 「월~금」 「금~월」
        i, j = _DAY_CHARS[first], _DAY_CHARS[last]
        days |= set(range(i, j + 1)) if i <= j else {*range(i, 7), *range(0, j + 1)}
    rest = _DAY_RANGE.sub("", rest)
    for token, covered in _DAY_TOKENS.items():
        if token in rest:
            days |= covered
            rest = rest.replace(token, "")
    rest = rest.replace("요일", "")
    for char, index in _DAY_CHARS.items():
        if char in rest:
            days.add(index)
            rest = rest.replace(char, "")
    if re.search(r"[가-힣A-Za-z0-9]", rest):
        return None
    if not days:
        return set(range(7)) if not _HOLIDAY.search(label) else set()
    return days


def open_at(business_hours: str | None, at: datetime) -> bool | None:
    """`at` 시각에 열려 있나. 확실히 읽을 수 있을 때만 답하고 그 밖은 `None`(모름).

    ★`closed_on` 과 같은 원칙 — **명백히 닫힌 곳(False)만** 후보에서 뺀다. 모르면 남기고 표시한다.
    읽는 것: 「상시 개방」·「24시간」, 「HH:MM~HH:MM」(여러 개 · 자정 넘김 · 24:00 끝), 「/」로 나뉜 구간 앞의
      요일 라벨(평일·주말·월~일·매일). 입장마감 같은 괄호 설명은 뗀다.
    안 읽는 것(`None`): 비었음, 「점포별 상이」·「브레이크」·계절·층별처럼 `_HOURS_UNSURE` 에 걸리는 말,
      시간이 아닌 글자가 라벨에 남는 구간, `at` 요일에 맞는 구간이 없을 때, 공휴일 구간의 시간이 다를 때.
    ★시각은 한국 시간(KST)으로 읽는다. 시간대 없는 `at` 도 KST 로 본다 — 원문이 한국 현지 시각이다.
    """
    raw = (business_hours or "").strip()
    if not raw or any(marker in raw for marker in _HOURS_UNSURE):
        return None
    text = re.sub(r"\(.*?\)", "", raw).strip()
    ranges_found = _TIME_RANGE.search(text) is not None
    if _ALWAYS_OPEN.search(text):
        return True if not ranges_found else None
    if not ranges_found:
        return None

    local = at.astimezone(KST) if at.tzinfo else at
    now_min, weekday = local.hour * 60 + local.minute, local.weekday()

    matched: list[tuple[int, int]] = []
    holiday: set[tuple[int, int]] = set()
    for segment in re.split(r"[/;\n]", text):
        segment = segment.strip()
        if not segment:
            continue
        spans = [(int(a) * 60 + int(b), int(c) * 60 + int(d)) for a, b, c, d in _TIME_RANGE.findall(segment)]
        if not spans:
            return None
        days = _hours_segment_days(_TIME_RANGE.sub("", segment))
        if days is None:
            return None
        if _HOLIDAY.search(segment):
            holiday.update(spans)
        if weekday in days:
            matched.extend(spans)
    if not matched:
        return None
    if holiday and not holiday <= set(matched):
        return None      # ★공휴일 시간이 따로 있으면 오늘이 공휴일인지 몰라 단정하지 않는다
    for start, end in matched:
        inside = (start <= now_min < end) if start < end else (now_min >= start or now_min < end)
        if inside:
            return True
    return False


def _norm(value: Any) -> str:
    return str(value or "").strip()


def similar(origin: dict[str, Any], pool: list[dict[str, Any]],
            fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """`fields`가 원래 장소와 **모두** 같은 후보만. 원래 장소 쪽 값이 비면 그
    필드는 비교하지 않는다(모르는 값끼리 "같다"고 읽지 않는다)."""
    keys = [f for f in fields if _norm(origin.get(f))]
    return [c for c in pool if all(_norm(c.get(f)) == _norm(origin.get(f)) for f in keys)]


def preference_from_survey(survey: dict[str, Any] | None) -> str | None:
    """설문 `priority`(앞이 더 중요) 중 먼저 나오는 `activity`·`mobility` 를 선호도로. 없으면 `None`.

    ★`food` 는 이 팀 몫이 아니라 건너뛴다. `None`(무응답)이면 `rank_alternatives` 가 폴백을 쓰지 않는다.
    """
    for area in (survey or {}).get("priority") or []:
        if area in FALLBACK_DROPS:
            return area
    return None


def same_brand_nearby(origin: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """원래 장소가 브랜드 매장이고, 후보가 **같은 브랜드이면서 반경 `BRAND_NEARBY_KM` 안**인가.

    ★브랜드나 거리를 모르면 `False` — 모르는 값끼리 「같다」고 읽지 않는다(`similar` 와 같은 원칙).
    """
    brand = _norm(origin.get("brand"))
    if not brand or _norm(candidate.get("brand")) != brand:
        return False
    d = distance_km(origin, candidate)
    return d is not None and d <= BRAND_NEARBY_KM


def bounding_box(lat: float, lng: float, km: float) -> tuple[float, float, float, float]:
    """반경 `km` 원을 감싸는 사각형 `(위도 최소, 위도 최대, 경도 최소, 경도 최대)`. DB 1차 거르기용.

    ★사각형은 원보다 넓다(모서리) — 정확한 거리는 `distance_km` 가 다시 잰다.
    """
    dlat, dlng = km * LAT_DEG_PER_KM, km * LON_DEG_PER_KM
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng


def search_steps(preference: str | None) -> list[tuple[float, tuple[str, ...]]]:
    """찾는 순서 — `(반경 km, 그때까지 뺀 분류 필드)` 목록. 앞에서부터 `KEEP` 곳이 찰 때까지 시도한다.

    이동 중요는 반경이 바깥 고리(가까운 곳에서 분류를 먼저 푼다), 활동 중요는 분류가 바깥 고리
    (같은 분류에서 반경을 먼저 넓힌다). 선호도 `None` 이면 분류는 풀지 않고 반경만 넓힌다.
    ★마지막 단계가 가장 느슨하다(앞 단계 결과를 모두 포함한다).
    """
    levels: list[tuple[str, ...]] = [()]
    for group in FALLBACK_DROPS[preference] if preference else ():
        levels.append(levels[-1] + group)
    if preference == "mobility":
        return [(r, dropped) for r in radii() for dropped in levels]
    return [(r, dropped) for dropped in levels for r in radii()]


def radii() -> list[float]:
    """반경 단계 — 1km 부터 1km 씩 10km 까지."""
    count = int(round((RADIUS_MAX_KM - RADIUS_START_KM) / RADIUS_STEP_KM)) + 1
    return [RADIUS_START_KM + i * RADIUS_STEP_KM for i in range(count)]


def distance_km(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """`mapx`(경도)·`mapy`(위도) 직선거리(haversine). 좌표가 없으면 `None`."""
    try:
        lon1, lat1 = float(a["mapx"]), float(a["mapy"])
        lon2, lat2 = float(b["mapx"]), float(b["mapy"])
    except (KeyError, TypeError, ValueError):
        return None
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def category_level(origin: dict[str, Any], candidate: dict[str, Any]) -> int:
    """원래 장소와 분류가 얼마나 가까운가 — 0 같은 소분류 · 1 같은 중분류 · 2 같은 대분류(+타입) · 3 다름.

    ★원래 장소 쪽 값을 모르면 그 단계는 「같다」고 치지 않는다(`similar` 와 같은 원칙).
    """
    def same(*fields: str) -> bool:
        return all(_norm(origin.get(f)) and _norm(candidate.get(f)) == _norm(origin.get(f)) for f in fields)

    if same("lclsSystm1", "lclsSystm2", "lclsSystm3"):
        return 0
    if same("lclsSystm1", "lclsSystm2"):
        return 1
    if same("lclsSystm1"):
        return 2
    return 3


def one_per_brand(candidates: list[dict[str, Any]], distance: dict[int, float]) -> list[dict[str, Any]]:
    """다양성 — 같은 브랜드 체인은 **가장 가까운 1곳만** 남긴다. 브랜드가 없는 곳은 그대로 둔다."""
    nearest: dict[str, dict[str, Any]] = {}
    for c in candidates:
        brand = _norm(c.get("brand"))
        if brand and (brand not in nearest or distance[id(c)] < distance[id(nearest[brand])]):
            nearest[brand] = c
    return [c for c in candidates if not _norm(c.get("brand")) or nearest[_norm(c.get("brand"))] is c]


_LEVEL_TEXT = ("같은 소분류", "같은 중분류", "같은 대분류", "분류는 다름")
_AVAILABILITY_TEXT = {"open_weekday": "휴무일 아님", "open_at_time": "그 시각 영업 중",
                      "unconfirmed": "영업 여부 미확인"}


def reason_line(origin: dict[str, Any], candidate: dict[str, Any], km: float, availability: str) -> str:
    """추천 이유 한 줄 — 거리 · 분류 가까움 · (같은 브랜드) · 영업 확인. 지어내지 않고 잰 값만 쓴다."""
    parts = [f"{km:.1f}km", _LEVEL_TEXT[category_level(origin, candidate)]]
    brand = _norm(origin.get("brand"))
    if brand and _norm(candidate.get("brand")) == brand:
        parts.append(f"같은 브랜드({brand})")
    parts.append(_AVAILABILITY_TEXT[availability])
    return " · ".join(parts)


def rank_alternatives(origin: dict[str, Any], pool: list[dict[str, Any]], at: datetime,
                      *, preference: str | None = None, exclude_ids: Any = (),
                      limit: int = SHOW, keep: int = KEEP) -> dict[str, Any]:
    """①~④를 차례로 적용해 화면용 `limit`(3)곳과 「더보기」용 `keep`(10)곳까지를 낸다.

    `preference`: `"mobility"`(이동 중요) · `"activity"`(활동 중요) · `None`(설문 무응답).
    ★`None`이면 ③을 **아예 안 쓴다** — 우선순위를 임의로 가정하지 않고
      분류 4개 필드 전부로만 거른다. 반경 넓히기(②')는 선호도와 무관하게 한다.
      다만 10곳이 안 차면 **「더보기」에만** 소분류를 푼 후보를 채운다(`MORE_ONLY_DROPS`, ⑤).
    `exclude_ids`: 이미 일정에 있는 곳의 `contentid` — 하드 필터로 뺀다.

    거르는 순서(결과가 많을 때): 하드 필터(휴무일·영업시간 외·이미 일정에 있는 곳) → 점수 정렬
    (거리·분류 가까움) → 다양성(같은 브랜드 체인은 가장 가까운 1곳) → 상위 N 자르기.

    반환값의 `dropped_fields`·`radius_km`·`availability`는 어느 조건을 풀었는지·무엇을
    모르는지를 숨기지 않으려고 둔다. 원래 장소 좌표를 모르면 근처를 잴 수 없어
    `reason: "origin_no_coordinates"` 로 빈 결과를 낸다(「근처에 없음」과 다르다).
    """
    if preference is not None and preference not in FALLBACK_DROPS:
        raise ValueError(f"unknown preference: {preference!r}")

    if distance_km(origin, origin) is None:
        return {"preference": preference, "reason": "origin_no_coordinates",
                "matched_fields": list(SIMILARITY_FIELDS), "dropped_fields": [],
                "more_dropped_fields": [], "radius_km": None, "max_radius_km": RADIUS_MAX_KM,
                "total_matched": 0, "alternatives": [], "more_alternatives": []}

    origin_id = _norm(origin.get("contentid"))
    excluded = {_norm(x) for x in exclude_ids or ()} - {""}
    # ① 하드 필터 — **가장 먼저.** 휴무 요일(`closed_on`)·운영시간 밖(`open_at`)·이미 일정에 있는 곳을 뺀다.
    #    모름(None)은 남기되 표시한다. 좌표가 없거나 최대 반경 밖인 곳도 여기서 뺀다(근처인지 모른다).
    open_pool: list[tuple[dict[str, Any], bool | None, bool | None, float]] = []
    for candidate in pool:
        cid = _norm(candidate.get("contentid"))
        if (origin_id and cid == origin_id) or cid in excluded:
            continue
        d = distance_km(origin, candidate)
        if d is None or d > RADIUS_MAX_KM:
            continue
        closed = closed_on(candidate.get("closed_days"), at)
        if closed is True:
            continue
        in_hours = open_at(candidate.get("business_hours"), at)
        if in_hours is False:
            continue
        open_pool.append((candidate, closed, in_hours, d))
    candidates = [c for c, _, _, _ in open_pool]
    closed_state = {id(c): closed for c, closed, _, _ in open_pool}
    hours_state = {id(c): in_hours for c, _, in_hours, _ in open_pool}
    distance = {id(c): d for c, _, _, d in open_pool}

    def confirmed(c: dict[str, Any]) -> bool:
        return closed_state[id(c)] is False or hours_state[id(c)] is True

    # ②·②'·③ 유사도 + 반경 + 폴백 — `search_steps` 순서대로, 브랜드를 하나씩만 셌을 때 `keep` 곳이
    #    차면 멈춘다. 끝까지 모자라면 마지막(가장 느슨한) 단계 결과를 그대로 낸다.
    #    ★앞 단계에서 찾은 곳은 **계속 남긴다** — 조건을 풀면 반경이 1km 부터 다시 시작하는데, 그때
    #      앞 단계(더 엄격한 분류)에서 10km 안에 찾은 곳이 빠지면 안 된다. 푼다는 것은 더하는 것이다.
    found: dict[int, dict[str, Any]] = {}
    matched: list[dict[str, Any]] = []
    radius, dropped = RADIUS_START_KM, ()
    for radius, dropped in search_steps(preference):
        fields = tuple(f for f in SIMILARITY_FIELDS if f not in dropped)
        for c in similar(origin, candidates, fields):
            if distance[id(c)] <= radius:
                found.setdefault(id(c), c)
        matched = one_per_brand(list(found.values()), distance)
        if len(matched) >= max(keep, limit):
            break

    # ④ 점수 정렬 — 영업 확인된 곳 → 반경 1km 안의 같은 브랜드 → 선호도에 따라
    #    이동 중요: 거리 → 분류 가까움 / 그 밖: 분류 가까움 → 거리.
    def sort_key(c: dict[str, Any]) -> tuple:
        level, d = category_level(origin, c), distance[id(c)]
        tail = (d, level) if preference == "mobility" else (level, d)
        return (not confirmed(c), not same_brand_nearby(origin, c), *tail)

    def availability(c: dict[str, Any]) -> str:
        # ★가장 강한 확인만 말한다: 휴무 요일이 아님 → `open_weekday`, 그것은 모르지만 운영시간 안 → `open_at_time`
        return ("open_weekday" if closed_state[id(c)] is False
                else "open_at_time" if hours_state[id(c)] is True else "unconfirmed")

    def item(rank: int, c: dict[str, Any]) -> dict[str, Any]:
        state = availability(c)
        return {"rank": rank,
                "contentid": c.get("contentid"),
                "title": c.get("title"),
                "distance_km": round(distance[id(c)], 2),
                "availability": state,
                "reason": reason_line(origin, c, distance[id(c)], state),
                "closed_days": c.get("closed_days") or None,
                "business_hours": c.get("business_hours") or None}

    strict = sorted(matched, key=sort_key)
    shown, more = strict[:limit], strict[limit:max(keep, limit)]

    # ⑤ 선호도 `None` 의 「더보기」 채우기 — 화면은 정확한 분류(4개 필드 모두 같음)만 쓰고,
    #    10곳이 안 찼으면 **더보기에 한해서만** 소분류를 풀어 반경 1→10km 로 더 찾는다(2026-10-03).
    #    ★화면 칸은 비어도 느슨한 후보로 채우지 않는다 — 선호를 짐작하지 않는다는 규칙은 화면에서 지킨다.
    more_dropped: list[str] = []
    if preference is None and len(strict) < max(keep, limit):
        more_dropped = list(MORE_ONLY_DROPS)
        fields = tuple(f for f in SIMILARITY_FIELDS if f not in MORE_ONLY_DROPS)
        strict_ids = {id(c) for c in strict}
        strict_brands = {_norm(c.get("brand")) for c in strict} - {""}
        room = max(keep, limit) - len(strict)
        extra_found: dict[int, dict[str, Any]] = {}
        extra: list[dict[str, Any]] = []
        for r in radii():
            for c in similar(origin, candidates, fields):
                if distance[id(c)] <= r and id(c) not in strict_ids \
                        and _norm(c.get("brand")) not in strict_brands:
                    extra_found.setdefault(id(c), c)
            extra = one_per_brand(list(extra_found.values()), distance)
            if len(extra) >= room:
                break
        more = more + sorted(extra, key=sort_key)[:room]

    ranked = [item(i + 1, c) for i, c in enumerate(shown + more)]
    return {
        "preference": preference,
        "reason": None if matched else "none_within_max_radius",
        "matched_fields": [f for f in SIMILARITY_FIELDS if f not in dropped],
        "dropped_fields": list(dropped),
        "more_dropped_fields": more_dropped,      # 「더보기」에만 푼 조건
        "radius_km": radius,
        "max_radius_km": RADIUS_MAX_KM,
        "total_matched": len(matched),
        "alternatives": ranked[:len(shown)],      # 화면 노출
        "more_alternatives": ranked[len(shown):],  # 「더보기」
    }
