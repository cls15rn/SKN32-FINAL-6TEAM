# -*- coding: utf-8 -*-
"""서울교통공사 지하철알림정보 — 역 **무정차 통과** 감시 `[2026-10-04]` (문제목록 #36).

공공데이터포털 15144070. 실측(2026-10-04, 공통 키를 `unquote` 해서):

    GET https://apis.data.go.kr/B553766/ntce/getNtceList?serviceKey=…&dataType=JSON&pageNo=1&numOfRows=100
    → HTTP 200 · {header:{resultCode:"00"}, body:{totalCount:1314, items:{item:[…]}}} — **최신순**(2016-11 부터 쌓임)
      item 칸: noftTtl(알림 제목) · noftCn(내용) · noftOcrnDt(발생 일시 · KST 표기) · lineNmLst(호선) · stnSctnCdLst(역·구간 코드)
               noftSeCd(알림 구분 코드) · nonstopYn(무정차 여부) · xcseSitnBgngDt/EndDt(이례 상황 시작·종료) · upbdnbSe(상·하·내·외선)
    개발계정 하루 10,000건 · 매 1분 갱신 · 공공누리 1유형(출처 표시).

★이 소스가 답하는 것은 **「그 역에 지금 무정차 통과 중인가」 하나다.** 지연·운행 재개·시간표 변경은 경로를 끊지 않아 안 다룬다.

★왜 제목 글자를 읽나(= 구조화된 칸이 모자란다). 시작·종료를 가르는 칸이 없다 — `nonstopYn` 은 **종료 알림에도 Y** 였다
  (「여의나루역 무정차 통과 종료」). `xcseSitnBgngDt/EndDt` 는 실측 거의 비어 있다. 그래서 같은 호선·같은 역을 말하는 **가장 최근 알림**의
  제목으로 정한다: 「무정차」가 있고 종료어(종료·해제·재개·정상·복구·완료·해소)가 없으면 진행 중, 종료어가 있으면 끝.
★역은 대상의 역 이름이 알림 제목·내용에 **「이름 + 역」으로 (앞이 한글이 아닌 자리에서)** 나올 때 같은 역으로 본다 — 「대방역」이
  「신대방역」에 걸리지 않게. **한계(과거 1,314건으로 확인)**: 제목에서 역을 못 읽는 알림이 무정차 138건 중 42건(「혜화, 한성대입구역」처럼
  역 이름을 나열하거나 「서울역 열차 무정차」처럼 호선이 없는 것 · 집회 안내문)이다 — 나열형은 **마지막 역만** 잡는다. 그래서 이 소스의 「사건 없음」은
  「알림에서 못 찾음」이지 「문제 없음」이 아니다(증거 등급은 추정).
★끝 알림이 안 오는 경우가 있다(시작 14건이 끝 알림 없이 남았다). 그래서 **발생 뒤 3시간이 지난 알림은 진행 중으로 보지 않는다**(UTIC 와 같은 기준).
★못 읽으면(`None`) 감시 루프가 그 이동 항목을 치명으로 남긴다(결정 15) — 「사건 없음」으로 넘기지 않는다.
★키 함정: 포털 키는 Encoding 형태다. `Settings.public_data_key()` 가 `unquote` 해서 준다(한 번 더 인코딩하면 403 「등록되지 않은 서비스키」).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .base import TravelSource

ENDPOINT = "https://apis.data.go.kr/B553766/ntce/getNtceList"
KST = ZoneInfo("Asia/Seoul")
#: 끝 알림이 안 오는 경우가 있어 진행 중으로 보는 최대 시간(시간) — UTIC `OPEN_ENDED_HOURS` 와 같다
ACTIVE_HOURS = 3
#: 한 번에 읽는 최신 알림 수. 최근 몇 시간만 보므로 한 쪽이면 충분하다(실측 하루 발생 몇 건 안팎)
PAGE_SIZE = 100
END_WORDS = ("종료", "해제", "재개", "정상", "복구", "완료", "해소")
#: 서울교통공사 1~8호선만 이 소스의 대상이다(9호선·공항철도·경의선 등은 알림이 없다)
SUPPORTED_LINES = frozenset(f"{n}호선" for n in range(1, 9))


def _parse_time(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text)).replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


def _clean(name: str) -> str:
    """역 이름 비교용 — 끝의 「역」·공백을 뗀다."""
    name = str(name or "").strip()
    return name[:-1] if name.endswith("역") else name


def mentions_station(text: str, station: str) -> bool:
    """글에 「{역}역」이 **앞이 한글이 아닌 자리에서** 나오는가 — 「대방역」이 「신대방역」에 걸리지 않게."""
    name = re.escape(_clean(station))
    return bool(name) and re.search(rf"(?<![가-힣]){name}역", text or "") is not None


def is_nonstop_start(title: str) -> bool:
    """제목이 무정차 통과의 **시작·진행 중** 알림인가."""
    return "무정차" in title and not any(word in title for word in END_WORDS)


class SubwayNotices(TravelSource):
    name = "subway_notice"

    def __init__(self, *, service_key: str, now=None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = service_key
        self._now = now or (lambda: datetime.now(KST))

    @staticmethod
    def _body_error(payload: dict[str, Any]) -> str | None:
        header = (payload.get("response") or payload).get("header")
        if isinstance(header, dict):
            code = str(header.get("resultCode", ""))
            if code and code not in ("00", "0", "0000"):
                return f'{code} {header.get("resultMsg", "")}'.strip()
        return TravelSource._body_error(payload)

    def records(self) -> list[dict[str, Any]] | None:
        """최신 알림 한 쪽(최신순). 못 읽으면 `None` — 이유는 `misses` 에 남는다."""
        if not self._key:
            self._miss("no_service_key")
            return None
        payload = self._fetch_json(ENDPOINT, {"serviceKey": self._key, "dataType": "JSON",
                                              "pageNo": "1", "numOfRows": str(PAGE_SIZE)})
        if payload is None:
            return None
        body = (payload.get("response") or payload).get("body")
        if not isinstance(body, dict):
            self._miss("unexpected_envelope", str(list(payload))[:120])
            return None
        items = body.get("items")
        rows = items.get("item") if isinstance(items, dict) else None
        if rows is None:                              # 알림이 하나도 없으면 items 가 비어 온다 — 실패가 아니라 빈 결과
            rows = []
        if isinstance(rows, dict):
            rows = [rows]
        return [row for row in rows if isinstance(row, dict)]

    def latest_about(self, records: list[dict[str, Any]], line: str, station: str,
                     at: datetime) -> dict[str, Any] | None:
        """`at` 이전 `ACTIVE_HOURS` 안에서 **그 호선·그 역을 말하는 가장 최근 알림**. 없으면 None."""
        floor = at - timedelta(hours=ACTIVE_HOURS)
        best, best_time = None, None
        for row in records:
            lines = {part.strip() for part in str(row.get("lineNmLst") or "").split(",")}
            if line not in lines:
                continue
            when = _parse_time(row.get("noftOcrnDt"))
            if when is None or when > at or when < floor:
                continue
            text = f'{row.get("noftTtl") or ""} {row.get("noftCn") or ""}'
            if not mentions_station(text, station):
                continue
            if best_time is None or when > best_time:
                best, best_time = row, when
        return best


class SubwayRouteEvents:
    """감시 루프의 **경로 사건** 소스 — `UticRouteEvents` 와 같은 모양(`unsupported()` · `affecting()`).

    답하는 대상은 `N호선:역`(1~8호선)뿐이다. 그 역에 지금 무정차 통과 알림이 진행 중이면 `skip_station`.
    9호선·공항철도·경의선 같은 다른 노선과 버스·도로는 이 소스가 모른다 → `unsupported()`(「확인 못 한 대상」으로 센다).
    """

    def __init__(self, notices: SubwayNotices) -> None:
        self.notices = notices
        self.name = "subway_notice_route_events"

    @staticmethod
    def _split(target: str) -> tuple[str, str] | None:
        head, sep, rest = str(target).partition(":")
        if sep and head in SUPPORTED_LINES and rest and rest != "*":
            return head, rest
        return None

    def unsupported(self, targets: list[str]) -> list[str]:
        return [target for target in targets if self._split(target) is None]

    def affecting(self, targets: list[str], at: datetime | None = None) -> dict[str, dict[str, Any]] | None:
        mine = [(t, self._split(t)) for t in targets if self._split(t) is not None]
        if not mine:
            return {}
        records = self.notices.records()
        if records is None:
            return None
        moment = at or self.notices._now()
        moment = moment if moment.tzinfo else moment.replace(tzinfo=KST)
        found: dict[str, dict[str, Any]] = {}
        for target, (line, station) in mine:
            row = self.notices.latest_about(records, line, station, moment)
            if row is None or not is_nonstop_start(str(row.get("noftTtl") or "")):
                continue
            found[target] = self.notices.stamp({
                "effect": "skip_station", "reason": "무정차 통과",
                # ★문구는 공급자 제목으로만 만든다 — 없는 이유를 붙이지 않는다
                "summary": f"{line} {_clean(station)}역 무정차 통과(서울교통공사 지하철알림정보)",
                "title": row.get("noftTtl"), "notice_at": row.get("noftOcrnDt"),
                "direction": row.get("upbdnbSe"), "ends_at": None}, source="subway_notice")
        return found


class CompositeRouteEvents:
    """여러 경로 사건 소스를 하나로 — 도로(UTIC) + 지하철(알림정보).

    ★`unsupported` 는 **어느 소스도 모르는** 대상만이다. `affecting` 은 각 소스가 자기 대상만 묻고 합친다 —
      어느 한 소스라도 **자기 대상을 못 읽으면 전체가 `None`**(결정 15: 못 읽은 것을 「사건 없음」으로 넘기지 않는다).
    """

    def __init__(self, parts: list[Any]) -> None:
        self.parts = [part for part in parts if part is not None]
        self.name = "+".join(getattr(part, "name", "route_events") for part in self.parts)

    def unsupported(self, targets: list[str]) -> list[str]:
        left = list(targets)
        for part in self.parts:
            theirs = set(part.unsupported(left)) if hasattr(part, "unsupported") else set(left)
            left = [target for target in left if target in theirs]
        return left

    def affecting(self, targets: list[str], at: datetime | None = None) -> dict[str, dict[str, Any]] | None:
        found: dict[str, dict[str, Any]] = {}
        for part in self.parts:
            theirs = set(targets) - set(part.unsupported(targets) if hasattr(part, "unsupported") else [])
            if not theirs:
                continue
            got = part.affecting(sorted(theirs, key=targets.index), at) if at is not None else part.affecting(
                sorted(theirs, key=targets.index))
            if got is None:
                return None
            found.update(got)
        return found


class AlternateRouteEvents:
    """**같은 대상**을 1차·2차 두 소스가 맡을 때(도로: UTIC + ITS) — 결정 15 의 「1차가 안 되면 대체로」 `[2026-10-05]`.

    ★둘 다 물어 **합친다**(합집합 · 1차가 겹치면 1차 값). 둘은 서로 다른 사건을 가진다(UTIC = 시내 사고·집회 · ITS = 도시고속도로·국도).
    ★한쪽만 못 읽으면 읽은 쪽 값으로 답한다 — 그 사실을 `degraded`(못 읽은 소스 이름)에 남긴다(조용히 넘기지 않는다 · 소스의 `misses` 에도 이유가 있다).
      읽은 쪽이 비어 있으면 「사건 없음」은 **그 소스 기준**이다.
    ★둘 다 못 읽으면 `None` — 치명(결정 15).
    """

    def __init__(self, primary: Any, secondary: Any) -> None:
        self.primary, self.secondary = primary, secondary
        self.name = f"{getattr(primary, 'name', 'primary')}|{getattr(secondary, 'name', 'secondary')}"
        self.degraded: list[str] = []

    def unsupported(self, targets: list[str]) -> list[str]:
        mine = set(self.primary.unsupported(targets)) & set(self.secondary.unsupported(targets))
        return [target for target in targets if target in mine]

    @staticmethod
    def _ask(part: Any, targets: list[str], at: datetime | None) -> dict[str, dict[str, Any]] | None:
        return part.affecting(targets) if at is None else part.affecting(targets, at)

    def affecting(self, targets: list[str], at: datetime | None = None) -> dict[str, dict[str, Any]] | None:
        got_primary = self._ask(self.primary, targets, at)
        got_secondary = self._ask(self.secondary, targets, at)
        self.degraded = [getattr(part, "name", "source") for part, got in
                         ((self.primary, got_primary), (self.secondary, got_secondary)) if got is None]
        if got_primary is None and got_secondary is None:
            return None
        return {**(got_secondary or {}), **(got_primary or {})}


__all__ = ["ACTIVE_HOURS", "AlternateRouteEvents", "CompositeRouteEvents", "ENDPOINT", "SubwayNotices", "SubwayRouteEvents",
           "is_nonstop_start", "mentions_station"]
