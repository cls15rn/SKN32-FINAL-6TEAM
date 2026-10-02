# -*- coding: utf-8 -*-
"""서울교통공사 지하철알림정보 — 감시 루프의 **지하철 경로 사건** 소스(`affecting()` · `unsupported()`).

출처(확정 · 2026-09-29 활용신청 승인 · 2026-09-30 실제 호출):
    공공데이터포털 「서울교통공사_지하철알림정보」(15144070)
    GET https://apis.data.go.kr/B553766/ntce/getNtceList
        serviceKey · dataType=JSON · pageNo · numOfRows · srchStartNoftOcrnYmd · srchEndNoftOcrnYmd
    → {"response": {"header": {"resultCode": "00"}, "body": {"totalCount", "items": {"item": [...]}}}}
      item 칸: noftTtl(제목) · noftCn(내용) · noftOcrnDt(발생 일시) · lineNmLst(호선 목록) ·
               stnSctnCdLst(역 코드 목록 · 「전구간」) · upbdnbSe(상하행) · noftSeCd · nonstopYn ·
               xcseSitnBgngDt·xcseSitnEndDt(계획 공지에만)
    키 = 공공데이터포털 공통 키(`ACOP_DATA_GO_KR_KEY`). 하루 10,000회 · 1분 갱신 → 1분 캐시면 1,440회.
    근거 문서: 이동 담당 「지하철·버스 사고 공지 출처 v1」(2026-09-30) §2·§4·§7.

★**종류는 제목으로 가른다**(표본 87건 · 확정). `nonstopYn=Y` 가 무정차·운행 중단·시위 예고·종료 알림에 두루
  붙어서 쓸 수 없다. 규칙(문서 §4-1 R0~R8):
    R0 같은 제목이 60초 안에 여러 줄(채널별 복사) → 칸이 가장 많이 찬 줄 하나
    R1 테스트·고객센터·민원 → 버림
    R2 끝 알림(종료·정상운행·재개·조치완료 · 또는 종료 일시 칸) → 같은 호선의 열린 사건을 닫는다
       (역 코드가 겹치는 것 → 코드 없음·「전구간」이면 그 호선 전부)
    R4 무정차 → 그 역에 서지 않는다(`skip_station`)
    R5 운행 중단·중지 → 그 구간(역 코드 둘 = 양 끝 · 하나 = 그 역)
    R3 시간표 변경(noftSeCd 7·8 · 운행계획 등)·R6 시위 사전 안내(역 모름)·R7 지연·R8 나머지 → **판정 조건 아님** —
       「확인 못 한 공지」로 센다(조용히 버리지 않는다)
  ☆문서와 다른 한 곳: 제목에 「무정차」가 있고 시작·종료 일시 칸이 찬 **계획 무정차**(noftSeCd 8 · 불꽃축제 등)는
    R3 로 버리지 않고 그 시간대 무정차로 읽는다 — 시각이 칸으로 오는 유일한 사전 정보다.
★**알림은 늦게 온다**(언론 대조 10건 · 추정): 조치 시작 → 알림 가운데값 5~6분(0~17) · 조치 끝 → 끝 알림 7분(3~10).
  그래서 「지금 이 역이 무정차 중」으로만 쓰고 끝 시각을 추정하지 않는다(끝 알림이 오면 닫는다).
★**못 보는 대상은 `unsupported()`** — 9호선·공항철도·경의중앙·수인분당 등 1~8호선 밖, 1~8호선이라도 서울교통공사가
  운영하지 않는 역(코레일 구간 · 표본에서 노량진·용산 시위 무정차가 API 에 없었다), 우리 역표에 없는 역.
  「사건 없음」과 다르다 — 감시 루프가 센다.
★**못 읽으면 `None`** — 감시 루프가 그 이동 항목을 치명으로 남긴다(결정 15). 「사건 없음」으로 바꾸지 않는다.
★역 코드 → 표기(`2호선:이대`)는 **이동 계산기 역표**(`station_coords.json` 의 station_cd · operator)로 바꾼다 —
  표본 역 코드 33종 전부 1:1(확정). 표를 못 받으면(이동 계산기 꺼짐) 지하철 대상은 전부 `unsupported()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from .base import TravelSource
from .route_events_chain import COVERED_LINES

ENDPOINT = "https://apis.data.go.kr/B553766/ntce/getNtceList"
KST = ZoneInfo("Asia/Seoul")
#: 1분 캐시 — 원천 갱신이 1분(설명문)이고 하루 한도 10,000 의 14%
CACHE_TTL_SECONDS = 60
#: 어제·오늘 발생 알림을 받는다 — 자정을 넘는 사건(밤 무정차 → 새벽 끝 알림)을 잇기 위해서
LOOKBACK_DAYS = 1
ROWS = 100
#: 끝 알림 없이 열린 채 이만큼 지나면 더 「지금 무정차 중」으로 보지 않는다 — **우리가 고른 값(추정)**.
#: 표본 실시간 사건 최장 3시간 54분 · 계획 공사 9시간 23분 · 무정차·운행 중단 16/16 에 끝 알림이 왔다.
OPEN_MAX_HOURS = 24
DEDUP_SECONDS = 60
#: 계획 사건을 미리 보는 폭(분) — 감시 루프의 내다보는 폭 90분 + 이동 소요. **우리가 고른 값**
LOOKAHEAD_MINUTES = 120

#: 지하철 노선 중 이 출처가 보는 것 — uses 표기(1~8호선). 그 안에서도 서울교통공사 운영 역만(표 만들 때 가린다 · route_events_chain)

_IRRELEVANT = ("테스트", "고객센터", "민원")
_END = ("종료", "정상운행", "운행재개", "재개", "조치완료", "중룟")
_SKIP = ("무정차",)
_SUSPEND = ("운행중단", "운행 중단", "운행중지", "운행 중지")
_TIMETABLE = ("시간표", "운행계획", "연장운행", "임시열차", "운행 조정", "운행조정", "준법")
_DELAY = ("지연", "고장", "장애", "정전")
_SECTION = re.compile(r"([가-힣0-9]+?)역?\s*[~∼-]\s*([가-힣0-9]+?)역")


def _has(text: str, words: Iterable[str]) -> bool:
    return any(w in text for w in words)


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=KST)


def _codes(raw: Any) -> list[str]:
    """`0150, 0426` · `0241` · `전구간` → 코드 목록. 「전구간」은 빈 목록이 아니라 `["*"]`."""
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if part == "전구간":
            out.append("*")
        elif part:
            out.append(part)
    return out


def _lines(raw: Any) -> list[str]:
    return [p.strip() for p in str(raw or "").split(",") if p.strip()]


def _filled(item: dict[str, Any]) -> int:
    return sum(1 for v in item.values() if v not in (None, ""))


def items_of(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """봉투 → item 목록. 하나면 dict 로, 없으면 빈 문자열로 오는 것까지 받는다. 모양이 다르면 None."""
    body = ((payload or {}).get("response") or {}).get("body")
    if not isinstance(body, dict):
        return None
    items = body.get("items")
    if items in (None, "", []):
        return []
    if not isinstance(items, dict):
        return None
    item = items.get("item")
    if item is None:
        return []
    if isinstance(item, dict):
        return [item]
    return item if isinstance(item, list) else None


@dataclass
class Incident:
    kind: str                    # skip | suspend
    line: str                    # 알림의 호선 이름(「2호선」)
    codes: list[str]             # 역 코드 · 「*」 = 전구간
    title: str
    started_at: datetime
    ends_at: datetime | None = None      # 계획 공지의 종료 칸 · 끝 알림이 오면 그 시각
    closed: bool = False
    direction: str | None = None
    section_names: tuple[str, str] | None = None    # 운행 중단인데 코드가 하나뿐일 때 제목의 「A역~B역」

    def active_at(self, at: datetime) -> bool:
        # ★계획 사건(시작 일시가 칸으로 온 것)은 시작 LOOKAHEAD_MINUTES 전부터 걸린 것으로 본다 — 감시 루프는 「지금」으로
        #   묻지만 일정은 앞으로 90분 안의 것이다. 계산기의 역 무정차 조건에는 시간대가 없어 그 사이는 보수적으로 막힌다.
        if at + timedelta(minutes=LOOKAHEAD_MINUTES) < self.started_at:
            return False
        if self.ends_at is not None and at > self.ends_at:
            return False
        return not self.stale_at(at)

    def stale_at(self, at: datetime) -> bool:
        """끝 알림 없이 OPEN_MAX_HOURS 를 넘겼다 — 복구를 확인한 것이 아니라 **더 믿지 않는 것**이다(세어 둔다)."""
        return self.ends_at is None and at > self.started_at + timedelta(hours=OPEN_MAX_HOURS)


@dataclass
class Reading:
    """알림 묶음을 읽은 결과 — 사건 · 짝 없는 끝 알림 · 판정 조건이 아닌 공지 수(종류별)."""
    incidents: list[Incident] = field(default_factory=list)
    unpaired_end: int = 0
    not_conditions: dict[str, int] = field(default_factory=dict)

    def count(self, why: str) -> None:
        self.not_conditions[why] = self.not_conditions.get(why, 0) + 1


def read_items(items: list[dict[str, Any]]) -> Reading:
    """R0~R8 을 시각 순으로 적용한다(문서 §4-1 · 표본 87 → 무정차·운행 중단 18/18 · 끝 15/15)."""
    rows = []
    for item in items:
        at = _dt(item.get("noftOcrnDt"))
        if at is None:
            continue
        rows.append((at, item))
    rows.sort(key=lambda r: r[0])
    # R0 — 같은 제목 60초 안 → 칸이 가장 많이 찬 줄
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for at, item in rows:
        title = str(item.get("noftTtl") or "")
        dup = next((i for i, (a, x) in enumerate(kept)
                    if str(x.get("noftTtl") or "") == title and abs((at - a).total_seconds()) <= DEDUP_SECONDS), None)
        if dup is None:
            kept.append((at, item))
        elif _filled(item) > _filled(kept[dup][1]):
            kept[dup] = (kept[dup][0], item)
    out = Reading()
    for at, item in kept:
        title = str(item.get("noftTtl") or "")
        lines, codes = _lines(item.get("lineNmLst")), _codes(item.get("stnSctnCdLst"))
        begin, end = _dt(item.get("xcseSitnBgngDt")), _dt(item.get("xcseSitnEndDt"))
        if _has(title, _IRRELEVANT):                                    # R1
            out.count("irrelevant")
            continue
        # ☆GPT 84 #1 — 시작·종료 일시가 둘 다 찬 **계획 공지**는 끝 알림이 아니다(무정차든 운행 중단이든 그 시간대의 사건).
        planned = begin is not None and end is not None and not _has(title, _END) \
            and (_has(title, _SKIP) or _has(title, _SUSPEND))
        if not planned and (_has(title, _END) or end is not None):       # R2
            closing_at = end or at
            hit = False
            for inc in out.incidents:
                # ☆GPT 84 #2 — 아직 시작 안 한 계획 사건은 지금 온 끝 알림이 닫지 않는다
                if inc.closed or inc.line not in lines or inc.started_at > at:
                    continue
                # ☆GPT 84 #2 — 무정차는 역 하나가 사건 하나다. 끝 알림에 적힌 역만 닫는다(A·B 무정차에 A 정상운행이 오면
                #   B 는 열린 채) · 코드 없음·「전구간」이면 그 호선 전부. 운행 중단은 구간이라 겹치면 통째로 닫는다.
                if "*" in codes or not codes or "*" in inc.codes or set(codes) & set(inc.codes):
                    inc.closed, inc.ends_at, hit = True, closing_at, True
            if not hit:
                out.unpaired_end += 1
            continue
        if _has(title, _SKIP):                                          # R4
            if not lines or not codes:
                out.count("skip_without_target")
                continue
            for line in lines:
                for code in codes:                                       # 역 하나 = 사건 하나(끝 알림이 역마다 닫는다)
                    out.incidents.append(Incident("skip", line, [code], title, begin if planned else at,
                                                  end if planned else None, direction=item.get("upbdnbSe")))
            continue
        if not planned and (str(item.get("noftSeCd") or "") in ("7", "8") or _has(title, _TIMETABLE)):   # R3
            out.count("timetable")
            continue
        if _has(title, _SUSPEND):                                       # R5
            if not lines:
                out.count("suspend_without_line")
                continue
            names = None
            if len([c for c in codes if c != "*"]) < 2:
                m = _SECTION.search(title)
                names = (m.group(1), m.group(2)) if m else None
            if not codes and names is None:
                out.count("suspend_without_target")
                continue
            for line in lines:
                out.incidents.append(Incident("suspend", line, (codes or ["*"]) if not names else codes, title,
                                              begin if planned else at, end if planned else None,
                                              direction=item.get("upbdnbSe"), section_names=names))
            continue
        if "시위" in title and ("사전" in title or not codes):           # R6
            out.count("protest_notice")
            continue
        if _has(title, _DELAY):                                         # R7
            out.count("delay")
            continue
        out.count("other")                                              # R8
    return out


#: 역 코드 → (uses 표기 「2호선:이대」, 이 출처가 보는 역인가). 이동 계산기 역표에서 만든다(`mobility.wiring`).
StationTable = dict[str, tuple[str, bool]]


class SeoulMetroAlerts(TravelSource):
    """지하철 경로 사건. `affecting(targets)` → {대상: 사건} · 못 읽으면 None. `unsupported(targets)` → 못 보는 대상."""

    name = "seoulmetro_alert"
    cache_ttl_seconds = CACHE_TTL_SECONDS

    def __init__(self, *, service_key: str, stations: Callable[[], StationTable | None],
                 now: Callable[[], datetime] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._key = service_key
        self._stations = stations
        self._now = now or (lambda: datetime.now(KST))
        self.last_stale = 0

    # ── 읽기 ────────────────────────────────────────────────────
    @staticmethod
    def _body_error(payload: dict[str, Any]) -> str | None:
        header = ((payload or {}).get("response") or {}).get("header")
        if isinstance(header, dict):
            code = str(header.get("resultCode", ""))
            if code not in ("00", "0", "0000"):
                return f"{code} {header.get('resultMsg', '')}".strip()
            return None
        return TravelSource._body_error(payload)

    def reading(self) -> Reading | None:
        today = self._now().astimezone(KST).date()
        params = {"serviceKey": self._key, "dataType": "JSON", "pageNo": 1, "numOfRows": ROWS,
                  "srchStartNoftOcrnYmd": (today - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
                  "srchEndNoftOcrnYmd": today.strftime("%Y%m%d")}
        payload = self._fetch_json(ENDPOINT, params)
        if payload is None:
            return None
        items = items_of(payload)
        if items is None:
            self._miss("unexpected_shape", str(payload)[:120])
            return None
        total = (((payload.get("response") or {}).get("body") or {}).get("totalCount"))
        if isinstance(total, int) and total > ROWS:
            # ★이틀에 100건 넘게 온 적은 없다(2026 9개월 87건). 넘으면 잘린 것 — 모름으로 낸다(지어내지 않는다)
            self._miss("truncated", f"totalCount {total} > {ROWS}")
            return None
        return read_items(items)

    # ── 감시 루프 쪽 ────────────────────────────────────────────
    @staticmethod
    def _subway(target: str) -> bool:
        head = target.partition(":")[0]
        return head not in ("버스", "도로") and bool(head)

    def unsupported(self, targets: list[str]) -> list[str]:
        table = self._stations()
        if not table:
            return [t for t in targets if self._subway(t)]
        seen = {target: covered for target, covered in table.values()}
        return [t for t in targets if self._subway(t)
                and (t.partition(":")[0] not in COVERED_LINES or not seen.get(t, False))]

    def affecting(self, targets: list[str], at: datetime | None = None
                  ) -> dict[str, dict[str, Any]] | None:
        subway = [t for t in targets if self._subway(t)]
        if not subway:
            return {}
        table = self._stations()
        if not table:
            return {}              # 표가 없으면 전부 unsupported() — 사건을 지어내지 않는다
        reading = self.reading()
        if reading is None:
            return None
        moment = at or self._now()
        moment = moment if moment.tzinfo else moment.replace(tzinfo=KST)
        found: dict[str, dict[str, Any]] = {}
        self.last_stale = sum(1 for inc in reading.incidents if inc.stale_at(moment))
        if self.last_stale:
            self.misses["stale_open_incident"] += self.last_stale     # 끝 알림 없이 오래 열린 사건 — 복구 확인이 아니다
        for inc in reading.incidents:
            if not inc.active_at(moment):
                continue
            for target in subway:
                hit = self._hit(inc, target, table)
                if hit is None:
                    continue
                event = self.stamp({**hit, "title": inc.title, "direction": inc.direction,
                                    "started_at": inc.started_at.isoformat(),
                                    "ends_at": inc.ends_at.isoformat() if inc.ends_at else None,
                                    "grade": "확정"}, source=self.name)
                prev = found.get(target)
                if prev is None:
                    found[target] = event
                    continue
                # ☆GPT 84 #3 — 한 대상에 사건이 둘이면 둘 다 싣는다(대상마다 사건 하나라는 계약 안에서). 운행 중단이
                #   앞에 서고(구간 차단), 그 역 무정차는 `station_skipped` 로, 다른 구간의 운행 중단은 `sections` 에 더한다.
                skip_now, skip_prev = not event.get("suspended"), not prev.get("suspended")
                if skip_now and skip_prev:
                    continue
                main, extra = (event, prev) if skip_prev else (prev, event)
                if skip_now or skip_prev:
                    main["station_skipped"] = True
                else:
                    main["sections"] = list(main.get("sections") or [main.get("section")]) + [extra.get("section")]
                found[target] = main
        return found

    @staticmethod
    def _hit(inc: Incident, target: str, table: StationTable) -> dict[str, Any] | None:
        line = target.partition(":")[0]
        if line != inc.line:
            return None
        names = {code: table[code][0] for code in inc.codes if code in table}
        if inc.kind == "skip":
            if target not in names.values():
                return None
            station = target.partition(":")[2]
            return {"effect": "skip_station", "summary": f"{line} {station} 무정차 통과 중(서울교통공사 알림)"}
        # 운행 중단 — ★그 노선을 쓰는 대상이면 전부 알린다. uses 에는 타고 내리는 역만 있어 구간을 **지나가는** 경로를
        #   대상으로 못 가린다 — 지나가는지는 이동 계산기가 구간 차단(edge_closed)으로 다시 본다(변환 `wiring`).
        #   ★효과 이름은 skip_station — 재계획(replan.route_candidates)이 「이 대상 못 씀」으로 알아듣는 것이 이것뿐이다.
        section = None
        if "*" not in inc.codes:
            ends = [names[c].partition(":")[2] for c in inc.codes if c in names]
            if inc.section_names:
                section = list(inc.section_names)
            elif len(ends) >= 2:
                section = [ends[0], ends[-1]]
            elif len(ends) == 1:
                section = [ends[0], ends[0]]
            # ☆GPT 84 #9 — 코드가 우리 역표에 없으면 구간을 모른다. 「사건 없음」으로 두지 않고 그 노선 전 구간으로 본다(보수적)
        where = f"{section[0]}~{section[1]} 구간" if section and section[0] != section[1] else \
            (f"{section[0]}" if section else "전 구간")
        return {"effect": "skip_station", "suspended": True, "section": section,
                "summary": f"{line} {where} 운행 중단(서울교통공사 알림)"}


__all__ = ["ENDPOINT", "Incident", "Reading", "SeoulMetroAlerts", "items_of", "read_items"]
