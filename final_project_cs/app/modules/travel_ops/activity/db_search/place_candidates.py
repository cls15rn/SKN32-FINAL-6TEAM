# -*- coding: utf-8 -*-
"""대체 장소 후보 풀 조회 — `read.place_candidates` 의 DB 쪽 구현.

`place_catalog`(011·017)에서 원래 장소 행(origin)과 후보 행들을 읽어
`alternatives.py` 가 읽는 CSV·TourAPI 컬럼명 모양으로 돌려준다.
적재는 `scripts/load_place_catalog_csv.py` 가 한다(지금은 TourAPI 805건).

반환 모양(`ReadToolbox.place_candidates` 계약)::

    {"origin":     {"contentid", "title", "contenttypeid",
                    "lclsSystm1", "lclsSystm2", "lclsSystm3",
                    "sigungucode", "brand", "mapx", "mapy",
                    "closed_days", "business_hours"},
     "candidates": [<origin 과 같은 모양의 행>, ...],
     "source": "place_catalog:tour_api+oliveyoung+...", "confirmed_at": "..."}

★`[2026-10-02]` 후보 풀은 원래 장소 좌표 기준 **최대 반경(`RADIUS_MAX_KM`)의 바운딩 박스**
  안의 행까지만 좁힌다(거리 계산 1단계 — 사각형이라 인덱스 범위 비교로 끝난다). 정확한
  거리(하버사인)와 반경 넓히기는 `alternatives.py` 가 한다. 분류로는 좁히지 않는다 —
  이동 중요는 대분류까지 풀 수 있어야 한다. 좌표가 없는 행은 범위 비교에서 빠진다.
★원래 장소를 카탈로그에서 못 찾으면 `None`(모름) — 유사도를 잴 기준이 없다.
★원래 장소 좌표가 없으면 박스를 그릴 수 없어 후보를 읽지 않고 빈 목록을 준다 —
  `alternatives.rank_alternatives` 가 `origin_no_coordinates` 로 답한다.
★좌표는 컬럼(`latitude`·`longitude`) 값을 쓴다. 적재 때 서울 밖 자리표시 좌표는
  NULL 로 넣었으므로 `raw_json` 의 원본 `mapx`·`mapy` 를 되살리지 않는다.
★`confirmed_at` 은 풀에 든 행 중 **가장 오래된** `fetched_at` 이다 — 우리가
  받은 시각이지 공급자가 확인한 시각이 아니다. 가장 낡은 값을 댄다.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from ..alternatives import RADIUS_MAX_KM, bounding_box

#: 후보 풀에 넣는 출처 전부. `scripts/load_place_catalog_csv.py --source` 로 적재한 값과 같아야 한다.
#: ★브랜드 매장(올리브영·다이소·아트박스·무신사)도 같은 카탈로그의 한 출처다 —
#:  `contentid` 가 TourAPI 는 숫자, 브랜드는 OY/DS/AB/MS 접두어라 출처가 달라도 겹치지 않는다.
#:  아직 적재하지 않은 출처는 행이 없을 뿐이라 넣어 둬도 해가 없다.
DEFAULT_SOURCES = ("tour_api", "oliveyoung", "daiso", "artbox", "musinsa")

_COLUMNS = ("content_id", "content_type_id", "title", "latitude", "longitude",
            "large_class_code", "raw_json", "fetched_at")
_SELECT = ("SELECT content_id, content_type_id, title, latitude, longitude, "
           "large_class_code, raw_json, fetched_at FROM place_catalog "
           "WHERE tenant_id=%s AND source = ANY(%s) ")

ORIGIN_SQL = _SELECT + "AND content_id=%s ORDER BY source LIMIT 1"
POOL_SQL = (_SELECT + "AND content_id <> %s "
            "AND latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s "
            "ORDER BY content_id")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """`place_catalog` 한 행 → `alternatives.py` 가 읽는 CSV 컬럼 모양."""
    raw = record.get("raw_json") or {}
    lon, lat = record.get("longitude"), record.get("latitude")
    return {
        "contentid": _text(record.get("content_id")),
        "title": _text(record.get("title")) or _text(raw.get("title")),
        "contenttypeid": _text(record.get("content_type_id")) or _text(raw.get("contenttypeid")),
        "lclsSystm1": _text(record.get("large_class_code")) or _text(raw.get("lclsSystm1")),
        "lclsSystm2": _text(raw.get("lclsSystm2")),
        "lclsSystm3": _text(raw.get("lclsSystm3")),
        "sigungucode": _text(raw.get("sigungucode")),
        "brand": _text(raw.get("brand")),
        "mapx": None if lon is None else str(lon),
        "mapy": None if lat is None else str(lat),
        "closed_days": _text(raw.get("closed_days")),
        "business_hours": _text(raw.get("business_hours")),
    }


def _oldest(records: list[dict[str, Any]]) -> str | None:
    stamps = [r["fetched_at"] for r in records if isinstance(r.get("fetched_at"), datetime)]
    return min(stamps).isoformat() if stamps else None


def find_place_candidates(connection_factory: Callable[[], Any], tenant_id: str,
                          content_id: str | None, *,
                          sources: tuple[str, ...] = DEFAULT_SOURCES) -> dict[str, Any] | None:
    """원래 장소 `content_id` 로 후보 풀을 읽는다. 원래 장소를 모르면 `None`."""
    content_id = _text(content_id)
    if content_id is None:
        return None
    with connection_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(ORIGIN_SQL, (tenant_id, list(sources), content_id))
            row = cur.fetchone()
            if row is None:
                return None
            origin_record = dict(zip(_COLUMNS, row))
            origin = to_candidate(origin_record)
            lat, lng = origin_record.get("latitude"), origin_record.get("longitude")
            records: list[dict[str, Any]] = []
            if lat is not None and lng is not None:
                cur.execute(POOL_SQL, (tenant_id, list(sources), content_id,
                                       *bounding_box(float(lat), float(lng), RADIUS_MAX_KM)))
                records = [dict(zip(_COLUMNS, r)) for r in cur.fetchall()]
    return {"origin": origin,
            "candidates": [to_candidate(r) for r in records],
            "source": "place_catalog:" + "+".join(sources),
            "confirmed_at": _oldest([origin_record, *records])}
