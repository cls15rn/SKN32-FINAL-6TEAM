# -*- coding: utf-8 -*-
"""고객이 말한 이름 → `place_catalog` 에서 장소 **하나** 찾기 (`read.place_lookup` 의 1단계).

반환 `status`:
  - `found`      — 이름이 정확히 한 곳을 가리킨다
  - `ambiguous`  — 둘 이상이다(후보 이름 몇 개를 같이 낸다 — 고객이 고른다)
  - `not_found`  — 없다

★이름 비교는 둘을 **순서대로** 본다. ① 띄어쓰기·기호만 무시한 **완전 일치**가 정확히 한 곳이면 그곳.
  ② 아니면 지점 접미어·괄호까지 뗀 일치(`intake.places.normalize`)가 한 곳이면 그곳. 둘 이상이면 `ambiguous` —
  「다이소」처럼 지점이 수백 곳인 이름을 한 곳으로 찍지 않는다.
★후보 선별은 SQL(포함 검색)이 하고 정확 비교는 파이썬이 한다. SQL 은 띄어쓰기를 뗀 제목이 질의를 **포함**하는
  행만 가져오므로, 괄호가 **가운데** 끼어 정규화로만 같아지는 제목(「A(x)B」 = 「AB」)은 놓친다. 그 경우는 `not_found`
  쪽으로 기운다 — 없는 곳을 있다고 하지 않는 방향이다.
★카카오 같은 바깥 값은 여기 없다. 우리 카탈로그의 행만 읽는다.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable

from app.modules.travel_ops.intake.places import normalize

from .place_candidates import DEFAULT_SOURCES

#: ambiguous 일 때 같이 내는 후보 이름 수(고객에게 보여 줄 만큼만)
SHOW_LIMIT = 5
#: SQL 이 가져오는 행 수 상한 — 「다이소」 같은 이름이 수백 곳을 끌어오지 않게
FETCH_LIMIT = 200

_COLUMNS = ("content_id", "content_type_id", "title", "latitude", "longitude", "source")
SQL = ("SELECT content_id, content_type_id, title, latitude, longitude, source FROM place_catalog "
       "WHERE tenant_id=%s AND source = ANY(%s) "
       # ★지우는 문자가 `_loose` 와 같아야 한다(공백·`·`·`-`·`_`·`/`) — 다르면 SQL 이 제목을 놓친다
       "AND regexp_replace(title, '[[:space:]·_/-]+', '', 'g') ILIKE %s "
       "ORDER BY content_id LIMIT %s")


def _loose(text: str) -> str:
    """띄어쓰기·기호만 지운 비교 키. 지점 접미어·괄호는 **떼지 않는다**."""
    return re.sub(r"[\s·\-_/]+", "", unicodedata.normalize("NFKC", text or "")).lower()


def _like(pattern: str) -> str:
    escaped = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _hit(row: dict[str, Any]) -> dict[str, Any]:
    return {"content_id": row["content_id"], "content_type_id": row["content_type_id"],
            "matched_title": row["title"], "latitude": row["latitude"], "longitude": row["longitude"],
            "source": row["source"]}


def find_place_by_name(connection_factory: Callable[[], Any], tenant_id: str, name: str | None, *,
                       sources: tuple[str, ...] = DEFAULT_SOURCES) -> dict[str, Any]:
    """이름으로 카탈로그에서 찾는다. 항상 `status` 가 있는 dict 를 준다(모름이 아니라 **없음**을 말할 수 있다)."""
    query = (name or "").strip()
    key = _loose(query)
    if len(key) < 2:
        return {"status": "not_found", "via": "place_catalog", "reason": "name_too_short"}
    with connection_factory() as conn, conn.cursor() as cur:
        cur.execute(SQL, (tenant_id, list(sources), _like(key), FETCH_LIMIT))
        rows = [dict(zip(_COLUMNS, r)) for r in cur.fetchall()]

    exact = [r for r in rows if _loose(r["title"]) == key]
    pool = exact or [r for r in rows if normalize(r["title"]) == normalize(query)]
    if not pool:
        return {"status": "not_found", "via": "place_catalog"}
    if len(pool) == 1:
        return {"status": "found", "via": "place_catalog", **_hit(pool[0])}
    return {"status": "ambiguous", "via": "place_catalog", "match_count": len(pool),
            "candidates": [{"content_id": r["content_id"], "title": r["title"]} for r in pool[:SHOW_LIMIT]]}
