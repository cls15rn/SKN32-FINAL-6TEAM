# -*- coding: utf-8 -*-
"""국토교통부 ITS 국가교통정보센터 — 돌발상황정보 어댑터.

실측(2026-09-14, 발급 키):

    GET https://openapi.its.go.kr:9443/eventInfo
        ?apiKey=…&type=all&eventType=all&minX=…&maxX=…&minY=…&maxY=…&getType=json
    → {"header":{"resultCode":0,"resultMsg":"SUCCESS"},"body":{"totalCount":122,"items":[…]}}
    틀린 키 → HTTP 401 {"header":{"resultCode":4005,"resultMsg":"유효하지 않은 인증키…"}}

  서울 범위 122건: 도로 종류 고속도로 101(강변북로·올림픽대로 등 **도시고속도로 포함**)·
  시군도 10·국도 6·지방도 2 / 돌발 종류 공사 116·사고 3·기타돌발 3.
  ★「ITS 는 고속도로·국도만」이라고 판단했었는데 **틀렸다** — 시내 도로(삼일대로
    남산1호터널, 언주로 등)가 실제로 온다.

★★「통제」 낱말로 가르지 않는다. 메시지 108/122 건에 `[공사/통제]` 태그가 붙어 있다.
  메시지가 `<종류>::도로::시작::끝::차로::[태그] 본문` 모양이라, 가르는 기준을 **차로와
  태그**에서 뽑는다. `lanesBlockType` 필드는 122건 전부 비어 있었다(실측).

이상(일정 변경 대상)으로 세는 것:
    - 교통사고
    - 차로 칸이 「전체」·「양방향」 — 길이 막힌다 (실측 예: 도림천로 진행방향 전체차로)
    - 태그 [집회]·[행사]·[시위]·[마라톤] 또는 세부 「이벤트/홍보」
      (실측 예: 강남순환로 `[집회]` — ITS 에도 집회가 **일부** 들어온다)
  나머지 부분 차로 공사는 **주의**로만 둔다 — 한 차로 공사로 일정을 바꾸지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

from .base import TravelSource

ENDPOINT = "https://openapi.its.go.kr:9443/eventInfo"
KST = ZoneInfo("Asia/Seoul")

#: 장소에서 이 거리 안의 돌발만 본다(미터). ★우리가 고른 값이다 — 측정 아님.
DEFAULT_RADIUS_M = 1000
#: ★`[2026-10-03 사용자 — 「소스마다 한 번 받아 두고 항목은 사본에서 비교」]` 서울 전체를 덮는 상자(경도 126.70~127.30 · 위도 37.35~37.80 — 서울 경계 경도 126.76~127.19 · 위도 37.41~37.72 에 여유).
#:  전에는 **장소마다 반경 상자**로 요청해(요청 열쇠가 장소마다 달라 캐시도 못 나눴다) 항목 수만큼 요청이 늘었고, 하루 한도(1,000)가 9/30 · 10/2 에 실제로 찼다(4001).
#:  서울 범위 한 번 조회는 122건이라(2026-09-14 실측 — 위 머리말) 부담이 작다. 거리로 거르는 것은 전과 같이 **받은 뒤 여기서** 한다.
#:  서울 밖 장소(상자를 벗어나는 반경)는 전처럼 그 장소의 반경 상자로 묻는다.
SEOUL_BOX = (126.70, 127.30, 37.35, 37.80)               # (minX, maxX, minY, maxY)
#: 끝 시각이 없는 돌발(사고 등)을 「그 시각에도 이어진다」고 볼 한도(시간).
#:  ★우리가 고른 값이다. 지금 난 사고가 내일 일정까지 이어진다고 보지 않는다.
OPEN_ENDED_HOURS = 3

GATHERING_TAGS = frozenset({"집회", "행사", "시위", "마라톤"})
_TAG = re.compile(r"\[([^\]]+)\]")


def _parse_time(value: Any) -> datetime | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 12:
        return None
    try:
        return datetime.strptime(digits[:14].ljust(14, "0"), "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def parse_message(message: str) -> dict[str, Any]:
    """`<공사>::삼일대로::남산1호터널북측::남산한옥마을::4차로::[공사/통제] …` 를 편다."""
    parts = [part.strip() for part in str(message or "").split("::")]
    tags: set[str] = set()
    for group in _TAG.findall(message or ""):
        tags.update(tag.strip() for tag in group.split("/"))
    return {"lanes": parts[4] if len(parts) > 4 else "",
            "section": " → ".join(p for p in parts[2:4] if p) if len(parts) > 3 else "",
            "tags": sorted(tags)}


def classify(item: dict[str, Any]) -> tuple[str, str]:
    """(`disruption` | `advisory`, 이유)."""
    parsed = parse_message(item.get("message", ""))
    lanes = parsed["lanes"] + " " + str(item.get("lanesBlocked") or "")
    if item.get("eventType") == "교통사고":
        return "disruption", "교통사고"
    if "전체" in lanes or "양방향" in lanes:
        return "disruption", "전체 차로 통제"
    gathering = GATHERING_TAGS & set(parsed["tags"])
    if gathering or item.get("eventDetailType") == "이벤트/홍보":
        return "disruption", "집회·행사 " + ",".join(sorted(gathering) or ["이벤트/홍보"])
    return "advisory", "부분 차로 " + (item.get("eventType") or "돌발")


class ItsTrafficEvents(TravelSource):
    name = "its"

    def __init__(self, *, service_key: str, now=None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = service_key
        self._now = now or (lambda: datetime.now(KST))

    @staticmethod
    def _body_error(payload: dict[str, Any]) -> str | None:
        """ITS 는 `header.resultCode` 가 **숫자 0** 일 때만 성공이다."""
        header = payload.get("header")
        if isinstance(header, dict):
            code = header.get("resultCode")
            return None if str(code) == "0" else f'{code} {header.get("resultMsg", "")}'.strip()
        return TravelSource._body_error(payload)

    def records(self) -> list[dict[str, Any]] | None:
        """서울 상자 전체의 돌발 목록(캐시를 나눠 쓴다). 못 읽으면 `None` — `UticIncidents.records()` 와 같은 모양(2026-10-05 · 경로 사건 2차 소스용)."""
        payload = self._fetch_json(ENDPOINT, {
            "apiKey": self._key, "type": "all", "eventType": "all",
            "minX": SEOUL_BOX[0], "maxX": SEOUL_BOX[1], "minY": SEOUL_BOX[2], "maxY": SEOUL_BOX[3], "getType": "json"})
        if payload is None:
            return None
        body = payload.get("body")
        items = body.get("items") if isinstance(body, dict) else None
        if items in (None, ""):
            items = []
        if not isinstance(items, list):
            self._miss("unexpected_shape", type(items).__name__)
            return None
        return [item for item in items if isinstance(item, dict)]

    def active_at(self, item: dict[str, Any], at: datetime) -> bool:
        """그 시각에 걸리는 돌발인가 — 시작 전 · 끝난 뒤 · 끝 모르는 먼 미래는 아니다."""
        start, end = _parse_time(item.get("startDate")), _parse_time(item.get("endDate"))
        if start is not None and at < start:
            return False
        if end is not None and at > end:
            return False
        return not (end is None and at > self._now() + timedelta(hours=OPEN_ENDED_HOURS))

    def near(self, *, latitude: float, longitude: float, at: datetime,
             radius_m: int = DEFAULT_RADIUS_M) -> dict[str, Any] | None:
        """장소 반경 안에서 `at` 시각에 걸리는 돌발. 못 읽었으면 `None`."""
        at = at if at.tzinfo else at.replace(tzinfo=KST)
        dlat = radius_m / 111_000
        dlon = radius_m / (111_000 * max(math.cos(math.radians(latitude)), 0.01))
        place_box = (longitude - dlon, longitude + dlon, latitude - dlat, latitude + dlat)
        in_seoul = (SEOUL_BOX[0] <= place_box[0] and place_box[1] <= SEOUL_BOX[1]
                    and SEOUL_BOX[2] <= place_box[2] and place_box[3] <= SEOUL_BOX[3])
        min_x, max_x, min_y, max_y = SEOUL_BOX if in_seoul else tuple(round(v, 6) for v in place_box)
        payload = self._fetch_json(ENDPOINT, {
            "apiKey": self._key, "type": "all", "eventType": "all",
            "minX": min_x, "maxX": max_x, "minY": min_y, "maxY": max_y, "getType": "json"})
        if payload is None:
            return None
        body = payload.get("body")
        items = body.get("items") if isinstance(body, dict) else None
        if items in (None, ""):
            items = []                        # ★빈 목록 = 그 반경에 돌발 없음(아는 사실)
        if not isinstance(items, list):
            self._miss("unexpected_shape", type(items).__name__)
            return None

        now = self._now()
        disruptions, advisories = [], []
        for item in items:
            try:
                x, y = float(item.get("coordX")), float(item.get("coordY"))
            except (TypeError, ValueError):
                self._miss("bad_coordinates", repr(item.get("coordX")))
                continue
            distance = _distance_m(latitude, longitude, y, x)
            if distance > radius_m:
                continue
            start, end = _parse_time(item.get("startDate")), _parse_time(item.get("endDate"))
            if start is not None and at < start:
                continue                      # 그 시각엔 아직 시작 전
            if end is not None and at > end:
                continue                      # 그 시각엔 이미 끝남
            if end is None and at > now + timedelta(hours=OPEN_ENDED_HOURS):
                continue                      # ★끝 시각 모르는 돌발을 먼 미래까지 늘리지 않는다
            level, reason = classify(item)
            parsed = parse_message(item.get("message", ""))
            entry = {"reason": reason, "event_type": item.get("eventType"),
                     "road": item.get("roadName"), "road_type": item.get("type"),
                     "section": parsed["section"], "lanes": parsed["lanes"],
                     "tags": parsed["tags"], "distance_m": round(distance),
                     "starts_at": start.isoformat() if start else None,
                     "ends_at": end.isoformat() if end else None,
                     "message": str(item.get("message") or "").strip()}
            (disruptions if level == "disruption" else advisories).append(entry)
        return self.stamp({"radius_m": radius_m, "at": at.isoformat(),
                           "total_in_box": len(items), "box": "seoul" if in_seoul else "place", "for_place": disruptions,
                           "advisories": advisories, "kind": "traffic_events"},
                          source=self.name)


class ItsRouteEvents:
    """감시 루프의 **경로 사건** 2차 소스 — `UticRouteEvents` 와 같은 모양(`unsupported()` · `affecting()`) `[2026-10-05]`.

    UTIC 가 멈추면(키가 IP 에 묶여 거절 · 한도 · 장애) 경로 사건이 곧바로 치명이 되던 자리를 ITS 가 받는다 — 결정 15 의 「1차가 안 되면 대체로」.
    ★답하는 대상은 **도로(`도로:이름`)뿐**이다. 도로 이름은 응답의 `roadName` 또는 메시지(`<종류>::도로::…`)에서 맞춘다.
    ★ITS 는 고속도로·도시고속도로가 강하고 시내 사고·집회는 UTIC 가 본체다(머리말) — 그래서 UTIC 를 **대신하지 않고 함께** 묻는다(합집합).
    """

    ROAD = "도로:"

    def __init__(self, source: ItsTrafficEvents) -> None:
        self.source = source
        self.name = "its_route_events"

    def unsupported(self, targets: list[str]) -> list[str]:
        return [target for target in targets if not target.startswith(self.ROAD)]

    def affecting(self, targets: list[str], at: datetime | None = None) -> dict[str, dict[str, Any]] | None:
        roads = [t for t in targets if t.startswith(self.ROAD)]
        if not roads:
            return {}
        records = self.source.records()
        if records is None:
            return None
        moment = at or self.source._now()
        moment = moment if moment.tzinfo else moment.replace(tzinfo=KST)
        found: dict[str, dict[str, Any]] = {}
        for target in roads:
            road = target[len(self.ROAD):]
            for item in records:
                if road not in str(item.get("roadName") or "") and road not in str(item.get("message") or ""):
                    continue
                level, reason = classify(item)
                if level != "disruption" or not self.source.active_at(item, moment):
                    continue
                end = _parse_time(item.get("endDate"))
                found[target] = self.source.stamp({
                    "effect": "road_control", "reason": reason,
                    "summary": f"{road} {reason}(국토교통부 ITS 돌발정보)",
                    "title": str(item.get("message") or "").strip()[:120], "incident_id": item.get("eventId") or item.get("linkId"),
                    "ends_at": end.isoformat() if end else None}, source="its")
                break
        return found


__all__ = ["DEFAULT_RADIUS_M", "ENDPOINT", "ItsRouteEvents", "ItsTrafficEvents", "SEOUL_BOX", "classify", "parse_message"]
