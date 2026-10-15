# -*- coding: utf-8 -*-
"""인천국제공항공사 공항버스(리무진) 정보 — 지역별 노선 · 요금 · 승차 위치 · **공항 출발** 시간표. `[2026-10-15]` 예약 모듈(공항리무진).

주소(공공데이터포털 15095045 상세의 요청주소 그대로): `GET https://apis.data.go.kr/B551177/BusInformation/getBusInfo`
  ?serviceKey · numOfRows · pageNo · area(1 서울 · 2 경기 · 3 인천 · 4 강원 · 5 충청 · 6 경상 · 7 전라) · type=json

실측(2026-10-15 10:41, playdata, `scripts/probe_airport_bus.py`): 서울 0.54초 · `00 NORMAL SERVICE.` · 43줄 중 시간표 칸 있는 줄 37.
칸: adultfare · area · busclass · busnumber · cpname · routeinfo · t1endfirst · t1endlast · t1ridelo · t1wdayt · t1wt ·
t2endfirst · t2endlast · t2ridelo · t2wdayt · t2wt · toawfirst · toawlast. 시간표 칸은 「0540, 0610, 0630, …」(HHMM, 쉼표).

★시간표(`t1wdayt` 등)는 **공항에서 출발하는 시각**으로 읽는다 — 6009번: T2 시간표 05:20 부터 · T1 05:40 부터(20분 차) · `toawfirst` 05:20 이
  T2 첫 시각과 같다(이름 · 숫자로 본 추정 — 공급자 설명에는 「T1 평일시간표」라고만 있다). 서울 정류장별 출발 시각은 이 API 에 없다.
  그래서 서울 → 공항 쪽에는 시각을 만들지 않는다(공항 출발 시각을 서울 출발처럼 쓰지 않는다).
★버스번호가 비고 시간표도 없는 줄(서울 6줄)은 그대로 넘기고, 쓰는 쪽이 거른다.
★정적 시간표라 응답을 6시간 캐시한다(`cache_ttl_seconds`) — 하루 한도 표(`settings.source_rate_limits`)에는 넣지 않았다(공용 파일).
"""
from __future__ import annotations

from typing import Any

from .base import TravelSource

URL = "https://apis.data.go.kr/B551177/BusInformation/getBusInfo"
AREA = {"서울": 1, "경기": 2, "인천": 3, "강원": 4, "충청": 5, "경상": 6, "전라": 7}
#: 터미널 → (평일 칸, 주말 칸, 승차 위치 칸, 첫차 칸, 막차 칸)
_FIELDS = {"T1": ("t1wdayt", "t1wt", "t1ridelo", "t1endfirst", "t1endlast"),
           "T2": ("t2wdayt", "t2wt", "t2ridelo", "t2endfirst", "t2endlast")}


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _clock(value: Any) -> str | None:
    """「0540」 → 「05:40」. 모양이 다르면 None."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 3:
        digits = "0" + digits
    if len(digits) != 4 or int(digits[:2]) > 24 or int(digits[2:]) > 59:
        return None
    return f"{digits[:2]}:{digits[2:]}"


def _clocks(value: Any) -> list[str]:
    return sorted({clock for part in str(value or "").split(",") if (clock := _clock(part))})


def _int(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


class AirportBusInfo(TravelSource):
    """공항버스 노선 목록(지역별). 한 번 부르면 그 지역 전체가 온다."""

    name = "incheon_airport_bus"
    cache_ttl_seconds = 6 * 3600

    def __init__(self, *, service_key: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = service_key

    @staticmethod
    def _rows(payload: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
        response = payload.get("response") or {}
        code = str((response.get("header") or {}).get("resultCode") or "")
        items = (response.get("body") or {}).get("items")
        rows = items.get("item") if isinstance(items, dict) else items if isinstance(items, list) else []
        rows = [rows] if isinstance(rows, dict) else [row for row in rows or [] if isinstance(row, dict)]
        return code, rows

    def routes(self, area: str = "서울") -> list[dict[str, Any]] | None:
        """그 지역 노선들. 못 부르면 None(모름), 노선이 없으면 []."""
        if not self._key:
            self._miss("no_service_key")
            return None
        if area not in AREA:
            self._miss("unknown_area", area)
            return []
        payload = self._fetch_json(URL, {"serviceKey": self._key, "numOfRows": "200", "pageNo": "1", "area": str(AREA[area]), "type": "json"})
        if payload is None:
            return None
        code, rows = self._rows(payload)
        if code not in ("00", "0000"):
            self._miss("result_code", code)
            return None
        found = []
        for row in rows:
            terminals = {}
            for terminal, (weekday, weekend, ride, first, last) in _FIELDS.items():
                terminals[terminal] = {"weekday": _clocks(row.get(weekday)), "weekend": _clocks(row.get(weekend)),
                                       "ride": _text(row.get(ride)), "first": _clock(row.get(first)), "last": _clock(row.get(last))}
            found.append(self.stamp({
                "number": _text(row.get("busnumber")), "route": _text(row.get("routeinfo")), "grade": _text(row.get("busclass")),
                "company": _text(row.get("cpname")), "price": _int(row.get("adultfare")), "area": area,
                "to_airport_first": _clock(row.get("toawfirst")), "to_airport_last": _clock(row.get("toawlast")),
                "terminals": terminals}, source=self.name))
        return found


__all__ = ["AREA", "URL", "AirportBusInfo"]
