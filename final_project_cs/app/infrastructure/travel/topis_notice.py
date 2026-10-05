# -*- coding: utf-8 -*-
"""TOPIS 버스 공지 — 감시 루프의 **버스 경로 사건** 소스(정류소 무정차 · 임시우회 예고).

출처(2026-09-29 표본 30건 · 이동 담당 「지하철·버스 사고 공지 출처 v1」 §1·§4-2):
    서울시 TOPIS 누리집 공지판이 부르는 내부 JSON — **공식 API 가 아니다**(공공데이터포털 15157266 → 열린데이터광장
    OA-22861 은 「종료된 서비스」). 키 없음.
    POST https://topis.seoul.go.kr/notice/selectNoticeList.do
         pageIndex · recordPerPage=10 · blbdDivCd=02 · bdwrDivCd=(빈값=전체) · tabGubun=A · category=sTtl · boardSearch=
    → {"rows": [{bdwrSeq, bdwrDivCd(0201 통제안내 · 0202 버스안내 …), bdwrTtlNm, bdwrCts(HTML), createDate, apndFileNm …}],
       "paginationInfo": {...}}
    robots.txt 는 /visit/ 와 /map/openTotalMap.do 만 막는다(2026-10-01 확인) · 이용 조건 공시 없음.

★**미리 알리는 공지다**(표본 무정차 7건 모두 시행 약 21시간~5.4일 전 게시). 그래서 **운행일 첫 호출(새벽) 때 한 번**
  받아 그날 하루 쓴다(하루 3쪽 · 30건). 감시 루프가 일정 90분 전에 물으면 「그 시간대 그 정류장 무정차」가 걸린다.
  ★**감시 범위는 최신 30건**이다 — 게시가 그보다 오래된 장기 공지(몇 주 전 예고)는 못 본다(표본: 30건 ≈ 3주).
★**사고성(예고 없는) 무정차·우회는 이 출처로 못 잡는다** — 사용자 결정(2026-10-01): 감지 못한 무정차는 받아들인다.
★읽는 것 두 가지(제목·본문):
    무정차 → 정류장 번호(ARS 5자리 · 옛 표기 01-126) + 시간대 → `버스:<노선>` 에 `skip_station` + `stops`·`windows`
             (노선은 공지에 없을 때가 많다 — 그 정류장을 지나는 노선을 **이동 계산기 정류장 표**로 찾는다)
    우회   → 노선 번호 + 시간대 → `버스:<노선>` 에 `road_control` + `detour`(소요를 모른다 — 재계획이 그 노선 후보를 빼고,
             이동 계산기는 그 노선 버스 구간을 판단불가로 본다)
  시간대·대상을 못 읽은 공지(첨부 PDF 에만 있음 · 「매주 일요일」 같은 반복)는 버리지 않고 「확인 못 한 공지」로 센다.
★**못 읽으면 `affecting()` 이 None** — 지하철 알림·UTIC 와 같다(팀장 9/30 「소스를 못 읽으면 None」). 감시 루프가 버스를 쓰는
  이동 항목을 치명(`fatal` · 재검토 필요)으로 세고, 다음 틱에 다시 본다. 새벽 영업 확인(`dawn_check.py`)이 구글을 못 불렀을
  때와 같은 처리다 — 기록하지 않고 세며, 창 안에서 다시 부른다. 서비스는 멈추지 않는다(치명은 그 항목을 사람에게 넘길 뿐).
  ★비공식 출처를 두드리지 않게 실패하면 `RETRY_MINUTES` 동안은 다시 부르지 않고 그동안 None 을 그대로 낸다.
★**이동 계산기 정류장 표가 없으면**(계산기 꺼짐) 부르지 않는다 — 버스 대상은 `unsupported()`(새벽 확인의 `disabled` 자리).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import html
import re
import threading
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from .base import TravelSource

ENDPOINT = "https://topis.seoul.go.kr/notice/selectNoticeList.do"
KST = ZoneInfo("Asia/Seoul")
PAGES = 3
PER_PAGE = 10
SERVICE_DAY_START = time(4, 0)
#: 내다보는 폭 — 감시 루프는 「지금」으로 묻지만 일정은 앞으로 90분 안의 것이다(`trip_watch.DEFAULT_LOOKAHEAD`) + 이동 소요.
#: 이 폭 안에 시작하는 무정차·우회도 **시간대와 함께** 낸다 — 걸리는지는 이동 계산기가 실제 승하차 시각으로 본다(우리가 고른 값)
LOOKAHEAD_MINUTES = 180
#: 실패하면 이만큼 뒤에 다시 부른다(비공식 출처를 자주 두드리지 않는다 · 감시 틱보다 길게 · 우리가 고른 값)
RETRY_MINUTES = 10
BUS_DIVS = ("0201", "0202")            # 통제안내 · 버스안내 (0203 정책 · 0204 기타는 안 본다)

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<\s*(br|/p|/div|/li)\s*/?>", re.I)
#: 정류장 번호 — 5자리(옛 표기 01-126). ☆GPT 84 #4 — 뒤에 단위가 붙은 숫자(「23285명」·「10000원」·「12000m」)는 번호가 아니다
_ARS = re.compile(r"(?<![\d.-])(\d{2})-?(\d{3})(?![\d]|\s?(?:명|원|개|대|회|건|km|m\b|M\b))")
_STOP_LINE = ("정류",)
#: 정류장 줄 뒤에 이어지는 줄이 여기 걸리면 정류장 목록이 끝난 것으로 본다(인원·일시·장소 줄의 숫자를 줍지 않게)
_NOT_STOP_LINE = ("일시", "일 시", "인원", "장소", "장 소", "행진", "집회)", "통제구간", "행사명", "문의")
_DATE = r"(?:'|20)?(?:(\d{2})\.\s?)?(\d{1,2})\.\s?(\d{1,2})\.?\s*(?:\([^)]{1,3}\))?"
_TIME = r"(\d{1,2})(?::(\d{2})|시(?:\s*(\d{1,2})분)?)"
_RANGE = re.compile(_DATE + r"\s*" + _TIME + r"\s*[~∼]\s*(?:" + _DATE + r"\s*)?" + _TIME)
_ROUTE_LINE = ("시내버스", "마을버스", "공항버스", "직행버스", "광역버스", "대상노선", "우회노선", "노선")
_ROUTE_TOKEN = re.compile(r"[NM]?[가-힣]{0,4}\d{1,4}(?:-\d{1,2})?[AB]?")


def text_of(body: Any) -> str:
    """본문 HTML → 줄글. 줄바꿈 태그는 줄로, 나머지 태그는 지운다."""
    s = _BREAK.sub("\n", str(body or ""))
    return html.unescape(_TAG.sub("", s)).replace("\r", "")


def _hm(h: str, m: str | None, m2: str | None) -> tuple[time, int] | None:
    """(시각, 날 더하기). ☆GPT 84 #4 — 24:00 은 23:59 가 아니라 **다음 날 00:00** 이다."""
    hour, minute = int(h), int(m or m2 or 0)
    if hour == 24 and minute == 0:
        return time(0, 0), 1
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour, minute), 0


def windows_of(text: str, posted: date) -> list[tuple[datetime, datetime]]:
    """「2026.9.19.(토) 04:50~05:50」·「'26.9.24.(목) 13:00 ~ 14:00」·「2026.10.3.(토) 07:00 ~ 10.4.(일) 22:00」 → 시간대.
    해가 없으면 게시일의 해(게시일보다 이르면 다음 해). 끝 날짜에 해가 없고 시작보다 이르면 다음 해(12.31 ~ 1.1 · GPT 84 #4).
    끝 시각이 시작보다 이르면 다음 날. 통제일시·통제기간·무정차 줄이 있으면 그 줄만.
    ★날짜와 시각이 **다른 줄**에 있거나 「매주 일요일」 같은 반복은 못 읽는다 — 그 공지는 「확인 못 한 공지」로 센다."""
    lines = text.split("\n")
    prefer = [ln for ln in lines if "통제일시" in ln or "통제기간" in ln or "무정차" in ln]
    pools = [prefer, lines] if prefer else [lines]
    for pool in pools:
        out = []
        for ln in pool:
            for m in _RANGE.finditer(ln):
                y1, mo1, d1, h1, mi1, mm1, y2, mo2, d2, h2, mi2, mm2 = m.groups()
                try:
                    year = 2000 + int(y1) if y1 else posted.year
                    start_day = date(year, int(mo1), int(d1))
                    if not y1 and start_day < posted - timedelta(days=31):
                        start_day = date(year + 1, int(mo1), int(d1))
                    end_day = start_day
                    if mo2 and d2:
                        end_day = date(2000 + int(y2) if y2 else start_day.year, int(mo2), int(d2))
                        if not y2 and end_day < start_day:
                            end_day = date(start_day.year + 1, int(mo2), int(d2))
                except ValueError:
                    continue
                t1, t2 = _hm(h1, mi1, mm1), _hm(h2, mi2, mm2)
                if t1 is None or t2 is None:
                    continue
                a = datetime.combine(start_day, t1[0], KST) + timedelta(days=t1[1])
                b = datetime.combine(end_day, t2[0], KST) + timedelta(days=t2[1])
                if b <= a:
                    b += timedelta(days=1)
                out.append((a, b))
        if out:
            return out
    return []


def stops_of(text: str) -> list[str]:
    """정류장 번호(ARS 5자리). ☆GPT 84 #4 — **정류장 줄에서만** 찾는다: 「정류」가 있는 줄, 그리고 그 뒤로 이어지는 줄
    (번호만 늘어놓은 줄 · 「세종문화회관(01-126) : 401, 406」 같은 줄). 일시·인원·장소 줄이 나오거나 빈 줄이면 끊는다.
    앞 판은 「무정차」 낱말 뒤 전부를 봐서 「예상 인원 23285명」을 정류장으로 주웠다."""
    seen, out, on = set(), [], False
    for ln in text.split("\n"):
        if any(k in ln for k in _STOP_LINE):
            on = True
        elif not ln.strip() or any(k in ln for k in _NOT_STOP_LINE):
            on = False
        if not on:
            continue
        for a, b in _ARS.findall(ln):
            ars = a + b
            if ars not in seen:
                seen.add(ars)
                out.append(ars)
    return out


def routes_of(text: str, known: Callable[[str], bool]) -> list[str]:
    """노선 번호 — 노선 낱말이 있는 줄의 토막 중 **우리 노선 표에 있는 것만**(날짜·거리 숫자가 섞이지 않게)."""
    out = []
    for ln in text.split("\n"):
        if not any(k in ln for k in _ROUTE_LINE):
            continue
        for tok in _ROUTE_TOKEN.findall(ln.replace("번", " ")):
            if known(tok) and tok not in out:
                out.append(tok)
    return out


@dataclass
class Notice:
    seq: str
    title: str
    kind: str                                   # stop_skip | detour
    windows: list[tuple[datetime, datetime]]
    stops: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)

    def windows_within(self, at: datetime, until: datetime) -> list[tuple[datetime, datetime]]:
        """[at, until] 과 겹치는 시간대 전부 — 지금 걸린 것 + 내다보는 폭 안에 시작하는 것."""
        return [w for w in self.windows if w[0] <= until and w[1] >= at]


@dataclass
class NoticeReading:
    notices: list[Notice] = field(default_factory=list)
    not_read: list[dict[str, str]] = field(default_factory=list)     # 「확인 못 한 공지」 — 제목 · 왜


def read_rows(rows: list[dict[str, Any]], *, stop_known: Callable[[str], bool],
              route_known: Callable[[str], bool]) -> NoticeReading:
    out = NoticeReading()
    for row in rows:
        if str(row.get("bdwrDivCd") or "") not in BUS_DIVS:
            continue
        title = str(row.get("bdwrTtlNm") or "")
        body = text_of(row.get("bdwrCts"))
        whole = title + "\n" + body
        skip, detour = "무정차" in whole, "우회" in whole
        if not (skip or detour):
            continue                           # 도로 통제만 — 경찰청 UTIC 「도로:」 영역
        try:
            posted = datetime.fromisoformat(str(row.get("createDate"))).date()
        except ValueError:
            posted = date.today()
        wins = windows_of(body, posted)
        seq = str(row.get("bdwrSeq") or "")
        if not wins:
            out.not_read.append({"seq": seq, "title": title, "why": "시간대를 못 읽음"})
            continue
        if skip:
            stops = [s for s in stops_of(body) if stop_known(s)]
            if stops:
                out.notices.append(Notice(seq, title, "stop_skip", wins, stops=stops))
            else:
                out.not_read.append({"seq": seq, "title": title, "why": "무정차 정류장을 못 읽음(첨부만 · 우리 표 밖)"})
        if detour:
            routes = routes_of(body, route_known)
            if routes:
                out.notices.append(Notice(seq, title, "detour", wins, routes=routes))
            elif not skip:
                out.not_read.append({"seq": seq, "title": title, "why": "우회 노선을 못 읽음(첨부만 · 「N개 노선」)"})
    return out


@dataclass
class StopTable:
    """이동 계산기 정류장 표에서 만든 것 — ARS → 그 정류장을 지나는 노선 · 우리 노선 표."""
    routes_by_ars: dict[str, set[str]]
    routes: set[str]


class TopisNotices(TravelSource):
    """버스 경로 사건(예고 공지). `affecting()` → {대상: 사건} · 못 읽으면 None. 표가 없거나 우리 노선 밖이면 `unsupported()`."""

    name = "topis_notice"

    def __init__(self, *, stops: Callable[[], StopTable | None], now: Callable[[], datetime] | None = None,
                 post: Callable[[str, dict[str, Any]], httpx.Response] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._stops = stops
        self._now = now or (lambda: datetime.now(KST))
        self._post = post or self._http_post
        # ☆GPT 84 #8 — (운행일, 읽은 결과, 받은 시각)을 **한 묶음**으로 바꾼다. 따로따로 바꾸면 동시에 들어온 호출이
        #   반쯤 바뀐 상태(날짜만 새것 · 시각 없음)를 본다. 받는 동안은 잠가서 같은 날 두 번 부르지 않는다.
        self._state: tuple[date, NoticeReading | None, datetime] | None = None
        self._lock = threading.Lock()
        self.last_failed = False

    def _http_post(self, url: str, form: dict[str, Any]) -> httpx.Response:
        proxy = getattr(self, "_proxy", None)
        if proxy:
            return httpx.post(url, data=form, timeout=self._timeout, proxy=proxy)
        return httpx.post(url, data=form, timeout=self._timeout)

    def _service_day(self, moment: datetime) -> date:
        local = moment.astimezone(KST)
        return (local - timedelta(days=1)).date() if local.time() < SERVICE_DAY_START else local.date()

    def _rows(self) -> list[dict[str, Any]] | None:
        rows: list[dict[str, Any]] = []
        for page in range(1, PAGES + 1):
            if not self._allow():
                return None
            form = {"pageIndex": page, "recordPerPage": PER_PAGE, "blbdDivCd": "02", "bdwrDivCd": "",
                    "tabGubun": "A", "category": "sTtl", "boardSearch": ""}
            try:
                response = self._post(ENDPOINT, form)
            except httpx.HTTPError as exc:
                self._miss("transport_error", f"{type(exc).__name__}: {exc}")
                return None
            if response.status_code != 200:
                self._miss(f"http_{response.status_code}", response.text[:200])
                return None
            try:
                payload = response.json()
            except ValueError:
                self._miss("not_json", response.text[:200])        # ★누리집 개편 등 — 모양이 바뀌면 여기 걸린다
                return None
            got = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(got, list):
                self._miss("unexpected_shape", str(payload)[:200])
                return None
            rows += got
        return rows

    def reading(self) -> NoticeReading | None:
        """운행일마다 한 번 받는다(그날 첫 호출 · 보통 새벽). 실패하면 None — RETRY_MINUTES 뒤에 다시 부른다."""
        table = self._stops()
        if table is None:
            return None
        now = self._now()
        day = self._service_day(now)

        def fresh(state):
            return state is not None and state[0] == day and (
                state[1] is not None or now - state[2] < timedelta(minutes=RETRY_MINUTES))
        state = self._state
        if fresh(state):
            return state[1]
        with self._lock:
            state = self._state
            if fresh(state):                       # 기다리는 사이 다른 호출이 받아 왔다
                return state[1]
            rows = self._rows()
            reading = None if rows is None else read_rows(
                rows, stop_known=lambda a: a in table.routes_by_ars, route_known=lambda r: r in table.routes)
            if reading is not None and reading.not_read:
                # ☆GPT 84 #9 — 읽었지만 해석 못 한 공지를 세어 둔다(「사건 없음」과 다르다 · 운영 화면이 misses 를 본다)
                self.misses["notice_not_read"] += len(reading.not_read)
            self._state = (day, reading, now)
            self.last_failed = reading is None
            return reading

    @staticmethod
    def _bus(target: str) -> bool:
        return target.startswith("버스:")

    def unsupported(self, targets: list[str]) -> list[str]:
        # ☆`[2026-10-05 합치기 · GPT 1]` **버스가 아닌 대상은 전부 못 본다**고 답한다. 앞 판은 버스 대상만 넘겨받는 합치기
        #   (CombinedRouteEvents)를 전제로 버스 대상만 걸렀다 — 팀장 합치기(CompositeRouteEvents)는 모든 대상을 넘기고 「돌려주지 않은
        #   대상 = 이 소스가 본다」로 읽으므로, 그대로면 도로·지하철 소스가 없을 때 그 대상의 「확인 못 한 대상」 표시가 사라진다.
        #   단독으로 꽂힐 때(다른 소스 키가 없을 때)도 같다. UticRouteEvents·SubwayRouteEvents 와 같은 계약이다.
        table = self._stops()
        if table is None:
            return list(targets)
        return [t for t in targets if not self._bus(t) or t.partition(":")[2] not in table.routes]

    def affecting(self, targets: list[str], at: datetime | None = None
                  ) -> dict[str, dict[str, Any]] | None:
        bus = [t for t in targets if self._bus(t)]
        if not bus:
            return {}
        table = self._stops()
        if table is None:
            return {}                   # ★표가 없다 → 부르지 않는다. unsupported() 가 버스 대상을 「확인 못 함」으로 센다
        reading = self.reading()
        if reading is None:
            return None                 # ★못 읽음 → 치명(감시 루프) · 「사건 없음」이 아니다
        moment = at or self._now()
        moment = moment if moment.tzinfo else moment.replace(tzinfo=KST)
        found: dict[str, dict[str, Any]] = {}
        until = moment + timedelta(minutes=LOOKAHEAD_MINUTES)
        for target in bus:
            route = target.partition(":")[2]
            detours, rows, titles = [], [], []
            for n in reading.notices:
                wins = n.windows_within(moment, until)
                if not wins:
                    continue
                if n.kind == "detour" and route in n.routes:
                    detours.append((n, wins))
                elif n.kind == "stop_skip":
                    # ☆GPT 84 #3 — 정류장마다 **제 시간대**를 들고 간다(공지 여럿이 겹치면 시간대가 다르다)
                    mine = [s for s in n.stops if route in table.routes_by_ars.get(s, ())]
                    rows += [{"ars": s, "start": w[0].isoformat(), "end": w[1].isoformat()} for s in mine for w in wins]
                    if mine:
                        titles.append(n.title)
            if detours:
                # 우회가 걸리면 그 노선은 소요를 모른다 — 정류장 무정차보다 앞선다(노선 구간 전체가 판단불가)
                n, wins = detours[0]
                found[target] = self._event(n, (min(w[0] for _, ws in detours for w in ws),
                                                max(w[1] for _, ws in detours for w in ws)),
                                            effect="road_control", detour=True,
                                            summary=f"버스 {route} 임시우회({n.title})")
            elif rows:
                stops = sorted({r["ars"] for r in rows})
                found[target] = {"effect": "skip_station", "stops": stops, "stop_windows": rows,
                                 "summary": f"버스 {route} 정류장 {', '.join(stops)} 무정차({' · '.join(titles)})",
                                 "title": titles[0], "window_start": min(r["start"] for r in rows),
                                 "window_end": max(r["end"] for r in rows), "grade": "추정",
                                 "confirmed_at": self._fetched_at().isoformat(), "source": self.name}
        return found

    def _fetched_at(self) -> datetime:
        state = self._state
        return state[2] if state else self._now()

    def _event(self, n: Notice, win: tuple[datetime, datetime], **value: Any) -> dict[str, Any]:
        return {**value, "title": n.title, "notice_seq": n.seq,
                "window_start": win[0].isoformat(), "window_end": win[1].isoformat(),
                "grade": "추정",       # 예고 공지 — 현장에서 실제로 그랬는지는 모른다
                "confirmed_at": self._fetched_at().isoformat(), "source": self.name}


__all__ = ["ENDPOINT", "Notice", "NoticeReading", "StopTable", "TopisNotices", "read_rows", "routes_of",
           "stops_of", "text_of", "windows_of"]
