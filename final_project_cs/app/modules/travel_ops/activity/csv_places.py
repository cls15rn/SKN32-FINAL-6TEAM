# -*- coding: utf-8 -*-
"""activity CSV 기반 장소 조회 — 관광공사 rate limit · 카카오 403 우회 로컬 폴백.

intake/places.py 의 _tour() 계약(find + misses)을 구현한다.
외부 API 없이 activity_total_data.csv 에서 찾는다.

정확일치(정규화 기준) → 접두사 단독일치 순으로 찾는다.
places.py NOT_THERE 집합과 같은 실패 키를 쓴다.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from typing import Any

_CSV_PATH = Path(__file__).parent / "data_processing" / "activity_total_data.csv"
_BRANCH = re.compile(r"\s*(본점|직영점|\S+점)$")
_COMPOUND_SEP = re.compile(r"[&·,+]")

# lclsSystm2 코드 → 실내외 추정 (신분류체계 정의서 기준)
# 야외(True): 자연·역사유적·수상/항공레저·농산어촌체험·도시공원·골목길/둘레길
_OUTDOOR_LCLSSYSTM2 = frozenset({
    "EX03",                                    # 농·산·어촌 체험
    "HS01", "HS02", "HS03", "HS04",            # 역사유적지·유물·종교성지·안보
    "LS02", "LS03",                            # 수상·항공 레저스포츠
    "NA01", "NA02", "NA03", "NA04", "NA05",    # 자연관광 전체
    "VE03", "VE04",                            # 도시공원·골목길/문화거리/둘레길
})
# 실내(False): 쇼핑·공연시설·전시시설·교육시설·웰니스·공예체험
_INDOOR_LCLSSYSTM2 = frozenset({
    "EX02", "EX05",                            # 공예체험, 웰니스(온천/스파/찜질방)
    "SH01", "SH02", "SH04", "SH05",           # 백화점·쇼핑몰·면세점·전문매장
    "SH06", "SH07",                            # 시장·기타쇼핑
    "VE06", "VE07", "VE08", "VE09", "VE12",   # 공연장·전시관·연회장·교육시설·기타
})
# 혼합(None): EV(이벤트), LS01·04(육상/복합레저), EX01·04·06·07,
#             VE01·02·05·10(랜드마크/테마파크/복합/스포츠시설) 등


def weather_sensitive_from_lclssystm2(code: str | None) -> bool | None:
    """lclsSystm2 분류 코드로 실외(True) / 실내(False) 여부를 추정한다.
    코드가 없거나 혼재 유형이면 None을 반환한다.
    """
    if not code:
        return None
    upper = code.upper()
    if upper in _OUTDOOR_LCLSSYSTM2:
        return True
    if upper in _INDOOR_LCLSSYSTM2:
        return False
    return None


def _normalize(text: str) -> str:
    """places.py normalize()와 동일 — 동작을 반드시 맞춰야 한다."""
    text = unicodedata.normalize("NFKC", text or "").strip()
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", "", text)
    text = _BRANCH.sub("", text)
    return re.sub(r"[\s·\-_/]+", "", text).lower()


def _to_float(value: Any) -> float | None:
    try:
        v = float(value)
        return v if v else None
    except (TypeError, ValueError):
        return None


def _distance_to(row: dict[str, str], near: tuple[float, float]) -> float:
    """`near`(위도, 경도)까지의 제곱거리 — 순서만 비교하므로 제곱근은 안 구한다. 좌표가 없으면 무한대."""
    lat = _to_float(row.get("mapy"))
    lon = _to_float(row.get("mapx"))
    if lat is None or lon is None:
        return float("inf")
    return (lat - near[0]) ** 2 + (lon - near[1]) ** 2


class CsvPlaceLookup:
    """activity_total_data.csv 기반 장소 조회 — intake 의 tour= 자리에 주입한다."""

    name = "csv_activity"

    def __init__(self, csv_path: Path = _CSV_PATH) -> None:
        self.misses: dict[str, int] = {}
        self._rows: list[dict[str, str]] = []
        self._by_norm: dict[str, dict[str, str]] = {}
        self._by_base: dict[str, list[dict[str, str]]] = {}
        self._by_content_id: dict[str, dict[str, str]] = {}
        self._load(csv_path)

    def _load(self, path: Path) -> None:
        if not path.exists():
            return
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                title = (row.get("title") or "").strip()
                if not title:
                    continue
                self._rows.append(row)
                key = _normalize(title)
                if key not in self._by_norm:
                    self._by_norm[key] = row
                self._by_base.setdefault(key, []).append(row)
                # 복합명("A&B", "A·B") → 첫 부분도 별도 키로 등록
                # 예) "롯데월드타워&롯데월드몰" → "롯데월드타워"도 검색 가능
                if _COMPOUND_SEP.search(title):
                    first = _COMPOUND_SEP.split(title)[0].strip()
                    first_key = _normalize(first)
                    if first_key and first_key not in self._by_norm:
                        self._by_norm[first_key] = row
                content_id = str(row.get("contentid") or "").strip()
                if content_id and content_id not in self._by_content_id:
                    self._by_content_id[content_id] = row

    def find(self, place_name: str, *, area_code: str | None = None,
             near: tuple[float, float] | None = None, **_kwargs: Any) -> dict[str, Any] | None:
        """이름으로 장소 하나를 찾는다. 없으면 None.

        near=(위도, 경도) 를 주면 접두사 다중 일치 시 해당 좌표에 가장 가까운 것을 고른다.
        """
        if not place_name or not place_name.strip():
            self.misses["no_place_name"] = self.misses.get("no_place_name", 0) + 1
            return None

        key = _normalize(place_name.strip())

        # ① 정확 일치 — near 있으면 가장 가까운 것, near 없고 후보가 여럿이면 미룬다
        same_key = self._by_base.get(key, [])
        if not same_key:
            row = None
        elif len(same_key) == 1:
            row = same_key[0]
        elif near is not None:
            row = min(same_key, key=lambda r: _distance_to(r, near))
        else:
            # 후보가 여럿인데 near 없음 → 임의 선택 대신 미룸(파이프라인이 재시도)
            self.misses["deferred_no_near"] = self.misses.get("deferred_no_near", 0) + 1
            return None

        # ② 접두사 일치 — near 좌표가 있으면 가장 가까운 것,
        #    near 없고 후보가 여럿이면 미룬다(strategy ① 와 동일 원칙)
        if row is None:
            prefix = [r for r in self._rows if _normalize(r.get("title", "")).startswith(key)]
            if len(prefix) == 1:
                row = prefix[0]
            elif len(prefix) > 1:
                if near is not None:
                    row = min(prefix, key=lambda r: _distance_to(r, near))
                else:
                    self.misses["deferred_no_near"] = self.misses.get("deferred_no_near", 0) + 1
                    return None

        # ③ 이름+지역 분리 — "다이소 용산" → 기본명 "다이소"로 풀을 만들고
        #    나머지 토큰("용산")이 제목·주소에 포함된 것만 추린다.
        #    branch 정규화로 사라진 위치 정보를 원문 title/addr1 로 복원한다.
        if row is None and " " in place_name.strip():
            orig_words = place_name.strip().split()
            for split_at in range(len(orig_words) - 1, 0, -1):
                base_key = _normalize(" ".join(orig_words[:split_at]))
                location = " ".join(orig_words[split_at:]).lower()
                pool = [r for r in self._rows
                        if _normalize(r.get("title", "")).startswith(base_key)]
                with_loc = [r for r in pool
                            if location in (r.get("title") or "").lower()
                            or location in (r.get("addr1") or "").lower()]
                if not with_loc:
                    continue
                if len(with_loc) == 1:
                    row = with_loc[0]
                elif near is not None:
                    row = min(with_loc, key=lambda r: _distance_to(r, near))
                else:
                    row = with_loc[0]
                break

        if row is None:
            self.misses["not_found"] = self.misses.get("not_found", 0) + 1
            return None

        lat = _to_float(row.get("mapy"))
        lon = _to_float(row.get("mapx"))
        if lat is None or lon is None:
            self.misses["no_coordinates"] = self.misses.get("no_coordinates", 0) + 1
            return None

        addr = (row.get("addr1") or "").strip()
        if not addr.startswith("서울"):
            self.misses["not_found"] = self.misses.get("not_found", 0) + 1
            return None

        return {
            "content_id": str(row.get("contentid") or ""),
            "content_type_id": str(row.get("contenttypeid") or ""),
            "large_class_code": str(row.get("lclsSystm1") or "") or None,
            "large_class_name": None,
            "matched_title": str(row.get("title") or ""),
            "latitude": lat,
            "longitude": lon,
            "address": addr,
        }

    def find_by_content_id(self, content_id: str) -> dict[str, str] | None:
        """contentid로 CSV 원본 행을 반환한다. 없으면 None."""
        if not content_id:
            return None
        return self._by_content_id.get(str(content_id).strip())
