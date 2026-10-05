# -*- coding: utf-8 -*-
"""서울 열린데이터광장 **실시간 지하철 도착정보**(OA-12764) — 「실시간 지하철 인증키」 전용 `[2026-10-05]`.

    GET http://swopenapi.seoul.go.kr/api/subway/{KEY}/json/realtimeStationArrival/{시작}/{끝}/{역명}
    → {errorMessage:{status:200, code:"INFO-000", message:"정상 처리되었습니다.", total:N},
       realtimeArrivalList:[{subwayId · updnLine · trainLineNm · arvlMsg2 · arvlMsg3 · arvlCd · barvlDt · recptnDt · btrainSttus · statnNm …}]}

★이 키는 서울 열린데이터광장 **일반 인증키와 별개**다(따릉이용 `ACOP_SEOUL_OPENAPI_KEY` 로는 이 서비스가 안 열린다). 하루 **1,000건**(활용사례
  등록 심사 뒤 해제 · 공식 안내 2026-10-04). 제공처가 **http 만** 받는다 — 키가 주소 경로에 들어가고 평문으로 나간다. 그래서 키를 오류 기록에
  싣지 않는다(`_miss` 를 덮어 가린다 · `bike.py` 의 `BikeLive` 와 같은 이유).
★이 소스가 주는 것은 **지금 값**이다 — 미래 시각의 도착은 모른다. 시간표(이동 계산기)를 대신하지 않는다: 지금 이 역에 어느 열차가 몇 분 뒤 오는가를
  **같이 놓고 보는** 용도다(시간표 오차 · 운행 이상의 단서). 판정에 넣지 않았다.
★`INFO-200 「해당하는 데이터가 없습니다」` 는 **오류가 아니라 빈 결과**다 — 운행 시간 밖이거나 그 역에 열차가 없다(실측 2026-10-05 01시: 인증은
  통과하고 이 코드가 왔다). 인증 실패 · 호출 한도 · 잘못된 요청(`INFO-100`·`ERROR-xxx`)만 못 읽음(`None`)이다.
★역명은 **「역」 접미사 없이** 넣는다(공식 안내). 호선 필터는 응답의 `subwayId` 로 우리가 한다.
★`[미확인]` 응답 칸의 실제 값(메시지 모양 · barvlDt 단위)은 운행 시간대에 확인한다 — 아래 정규화는 공식 안내의 칸 이름만 믿는다.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .base import TravelSource

BASE = "http://swopenapi.seoul.go.kr/api/subway"
KST = ZoneInfo("Asia/Seoul")
#: 한 번에 받는 최대 건수(역 하나의 상·하행 도착 몇 대면 충분하다)
PAGE_SIZE = 20
#: 응답 `subwayId` → 호선 표기(우리 `uses` 의 「N호선」과 같은 이름만). 모르는 번호는 그대로 둔다
SUBWAY_ID = {"1001": "1호선", "1002": "2호선", "1003": "3호선", "1004": "4호선", "1005": "5호선", "1006": "6호선",
             "1007": "7호선", "1008": "8호선", "1009": "9호선", "1063": "경의중앙선", "1065": "공항철도", "1067": "경춘선",
             "1075": "수인분당선", "1077": "신분당선", "1092": "우이신설선", "1032": "GTX-A"}
#: 「없음」으로 읽는 코드(빈 결과) — 인증·한도 오류와 갈라야 한다
EMPTY_CODES = {"INFO-200"}


def clean_station(name: str) -> str:
    """공식 안내: 역명은 「역」 접미사 없이."""
    name = str(name or "").strip()
    return name[:-1] if name.endswith("역") else name


class SeoulSubwayArrival(TravelSource):
    name = "seoul_subway_arrival"

    def __init__(self, *, service_key: str, now=None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = service_key
        self._now = now or (lambda: datetime.now(KST))

    def _miss(self, reason: str, detail: str = "") -> None:
        """★키가 주소 경로에 들어간다 — 기록에 새지 않게 가린다."""
        if self._key and detail:
            detail = detail.replace(self._key, "***").replace(urllib.parse.quote(self._key, safe=""), "***")
        super()._miss(reason, detail)

    def _get(self, url: str, params: dict[str, Any]) -> httpx.Response:  # type: ignore[override]
        return httpx.get(url, timeout=self._timeout)

    @staticmethod
    def _body_error(payload: dict[str, Any]) -> str | None:
        """`INFO-200`(빈 결과)은 오류가 아니다. 그 밖의 INFO/ERROR 코드는 문구를 돌려준다."""
        info = payload.get("errorMessage") if isinstance(payload.get("errorMessage"), dict) else payload
        code = str(info.get("code") or "")
        if code and code != "INFO-000" and code not in EMPTY_CODES and (code.startswith("ERROR") or code.startswith("INFO")):
            return f'{code} {info.get("message", "")}'.strip()
        return TravelSource._body_error(payload)

    def arrivals(self, station: str, *, line: str | None = None) -> dict[str, Any] | None:
        """그 역의 **지금 도착 예정 열차**(상·하행). 못 읽으면 `None`, 열차가 없으면 `trains: []`(실패가 아니라 빈 결과).

        `line` 을 주면(「2호선」) 그 호선만 남긴다. 값은 정규화 — 원값(`raw`)은 싣지 않는다(키·주소가 안 섞이게)."""
        if not self._key:
            self._miss("no_service_key")
            return None
        name = clean_station(station)
        if not name:
            self._miss("empty_station")
            return None
        url = f"{BASE}/{self._key}/json/realtimeStationArrival/0/{PAGE_SIZE}/{urllib.parse.quote(name)}"
        payload = self._fetch_json(url, {})
        if payload is None:
            return None
        rows = payload.get("realtimeArrivalList")
        if rows is None:                              # INFO-200 — 빈 결과
            rows = []
        trains = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            line_name = SUBWAY_ID.get(str(row.get("subwayId") or ""), str(row.get("subwayId") or ""))
            if line and line_name != line:
                continue
            seconds = row.get("barvlDt")
            trains.append({
                "line": line_name, "direction": row.get("updnLine"), "route_label": row.get("trainLineNm"),
                "message": row.get("arvlMsg2"), "position": row.get("arvlMsg3"), "arrival_code": row.get("arvlCd"),
                "eta_seconds": int(seconds) if str(seconds or "").isdigit() else None,
                "received_at": row.get("recptnDt"), "train_status": row.get("btrainSttus")})
        return self.stamp({"station": name, "line": line, "trains": trains, "kind": "subway_arrival"},
                          source=self.name)


__all__ = ["SUBWAY_ID", "SeoulSubwayArrival", "clean_station"]
