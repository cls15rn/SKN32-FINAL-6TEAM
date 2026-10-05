# modules/mobility/timeutil.py — 이동 모듈의 시각 자(尺). 하나만 쓴다.
#
# ★ 시각은 **분 단위 정수**로 다룬다. datetime·time 으로 바꾸지 않는다.
#   시간표 dep_time 에 '24:50:00'·'27:59:00' 이 들어온다. 자정을 넘긴 열차를
#   운행일 기준으로 24 시 이후로 적어 둔 값이고, 이게 맞는 표기다.
#   - datetime.strptime(t, "%H:%M:%S") 는 ValueError 로 죽는다.
#   - time(24, 50) 도 만들 수 없다. 파이썬 시각형에는 24 시가 없다.
#   - 24:50 을 00:50 으로 되돌리면 그 행이 하루의 가장 이른 시각이 되어
#     min() 이 첫차로, max() 가 막차로 잡는다. 2026-09-10 C4 에서 실제로 그랬다
#     (2호선 강변 평일 첫차 00:05 / 막차 23:53 → 실제 05:36 / 24:50).
#   분 단위 정수는 24:50=1490, 27:30=1650 을 그대로 담고 비교·뺄셈이 그냥 된다.
#   혼잡도 슬롯(24:30)·버스 막차(27:30)도 같은 자를 쓴다.
#
# 운행일(service day)
#   하루의 경계는 자정이 아니라 **04:00** 이다. 04 시 이전 시각은 전날 운행분의
#   연장이므로 +24 시간 한 값으로 읽는다. 지하철 첫차가 05 시대라 04 시를 경계로 둔다.
#   raw 소스를 정규화하는 자리는 build_timetable_v1.py 의 hhmmss() 이고,
#   여기서는 **이미 정규화된 시간표 값**과 **사람이 준 시각**을 같은 축으로 맞춘다.
import re

from .errors import CaseInputError

MIN_DAY = 24 * 60                 # 1440
SERVICE_DAY_START_MIN = 4 * 60    # 04:00 — 운행일 경계
SERVICE_DAY_MAX_MIN = 30 * 60     # 30:00 — 이보다 큰 값은 시각으로 보지 않는다

_HHMM_RE = re.compile(r"^\s*(\d{1,2})\s*:\s*(\d{2})(?:\s*:\s*(\d{2}))?\s*$")


def to_min(t):
    """시각 표기 → 분 단위 정수. 24 시 이상 표기를 **그대로 살린다**.

    받는 것: 'HH:MM:SS' · 'HH:MM' · 'HHMMSS' · 'HHMM' · int(이미 분) · None
    돌려주는 것: 분(int) 또는 None(값 없음·시각 아님)

    초는 버린다(내림). 시간표의 초는 판정에 쓰지 않고 버퍼가 흡수한다.
    '000000'·'0' 은 '출발 없음'이지 0 시가 아니므로 None 이다 — 시·종착역의 표기다.
    """
    v, _sec = _parse(t)
    return v


def to_min_ceil(t):
    """**사람이 준 출발 시각** → 분. 초가 있으면 **다음 분으로 올린다**.

    ☆`[2026-09-29 문제목록 #12]` to_min 은 초를 버린다 — 시간표에는 맞지만 요청 시각에 쓰면
      10:00:59 에 떠나는 사람이 이미 떠난 10:00 열차를 탄다고 판정됐다. 출발은 늦게 잡는 쪽이 안전하다.
    """
    v, sec = _parse(t)
    if v is None:
        return None
    if sec:
        v += 1
        if v > SERVICE_DAY_MAX_MIN:     # 30:00:01 → 1801 은 허용 최대(30:00) 밖 — 시각이 아니다(73 후속)
            return None
    return v


def _parse(t):
    """(분, 초) — 값이 아니면 (None, 0).

    ☆`[2026-09-29 문제목록 #17]` 앞 판은 음수 분(-1 → -1)과 '10:00:99'(초 99 → 600)를 그대로 받았다.
      시각이 아닌 값은 None 이다.
    """
    if t is None or t is False or isinstance(t, bool):
        return None, 0
    if isinstance(t, int):
        return (t, 0) if t >= 0 else (None, 0)
    if isinstance(t, float):
        return (int(t), 0) if t >= 0 else (None, 0)
    s = str(t).strip()
    if not s:
        return None, 0
    m = _HHMM_RE.match(s)
    if m:
        h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    else:
        d = re.sub(r"\D", "", s)
        if len(d) not in (4, 6):
            return None, 0
        if int(d) == 0:               # '000000' = 출발 없음(시·종착역)
            return None, 0
        h, mi, sec = int(d[:2]), int(d[2:4]), int(d[4:6] or 0)
    if mi > 59 or sec > 59:
        return None, 0
    v = h * 60 + mi
    if v > SERVICE_DAY_MAX_MIN:       # 31:00 같은 값은 표기 오류로 본다
        return None, 0
    return v, sec


def to_service_min(t, ceil_seconds=False):
    """**사람이 준 시각**을 운행일 축으로. '00:30' → 1470(=24:30).

    시간표는 이미 정규화돼 있지만 요청 시각·도착 필요 시각은 벽시계 표기로 온다.
    같은 자를 안 쓰면 '00:30 까지 도착' 이 시간표의 24:50 열차보다 이르게 정렬돼
    막차 판정이 통째로 뒤집힌다.
    04 시 이전만 올린다 — 06:00 을 30:00 으로 올리지 않는다.

    ceil_seconds=True 이면 초를 다음 분으로 올린다 — **출발 시각**에 쓴다(#12).
    도착 기한(arrive_by)은 내림이 안전한 쪽이라 기본값(내림)을 쓴다.
    """
    # ☆`[2026-09-29 73 후속 · 팀장 #12 뒤따름]` 운행일을 **올림 전 값**으로 먼저 정하고, 그 다음에 초를 올린다.
    #   앞 판(to_min_ceil 먼저)은 03:59:01~59 가 240(그날 04:00)이 됐다 — 맞는 값은 1680(전날 28:00) · 24시간 어긋남.
    #   올린 뒤 30:00 을 넘으면(30:00:01 → 1801) 시각이 아니다(None) — _parse 의 범위 검사를 올림 뒤에도 한 번 더.
    v, sec = _parse(t)
    if v is None:
        return None
    if v < SERVICE_DAY_START_MIN:
        v += MIN_DAY
    if ceil_seconds and sec:
        v += 1
        if v > SERVICE_DAY_MAX_MIN:
            return None
    return v


def fmt_min(m, seconds=False):
    """분 → '24:50'. 24 시 이상을 그대로 보여 준다(판정 근거로 남길 표기)."""
    if m is None:
        return None
    m = int(m)
    sign = "-" if m < 0 else ""
    m = abs(m)
    return f"{sign}{m // 60:02d}:{m % 60:02d}" + (":00" if seconds else "")


def fmt_wall(m):
    """분 → 벽시계 표기 '00:50 (+1일)'. 고객에게 보여줄 때만 쓴다."""
    if m is None:
        return None
    m = int(m)
    day = m // MIN_DAY
    w = m % MIN_DAY
    tail = f" (+{day}일)" if day else ""
    return f"{w // 60:02d}:{w % 60:02d}{tail}"


def is_next_day(m):
    return m is not None and m >= MIN_DAY


def normalize_raw(t):
    """raw 소스('000500') → 운행일 분. 수집 스크립트용 — 판정기는 쓰지 않는다.

    build_timetable_v1.py 의 hhmmss() 와 같은 규칙이다. 시간표를 거치지 않고
    소스를 직접 읽는 자리가 생기면 그 규칙을 다시 쓰지 말고 이 함수를 부른다.
    """
    v = to_min(t)
    if v is None:
        return None
    if v < SERVICE_DAY_START_MIN:
        v += MIN_DAY
    return v


class CalendarOutOfRange(CaseInputError):
    """공휴일 표가 덮지 않는 해의 날짜 — 평일·휴일을 **짐작하지 않는다**(결정 15: 대체 출처가 없으면 치명)."""


class HolidayCalendar(set):
    """공휴일 날짜 집합 + **이 표가 덮는 해**. set 처럼 `in` 으로 쓴다.

    ☆`[2026-09-29 문제목록 #5]` 앞 판은 표(2026·2027) 밖의 2028-03-01(삼일절)을 평일로 판정했다.
      덮는 해를 알면 day_type_of 가 그 밖의 날짜를 거절한다. 덮는 해를 모르는 옛 집합(set)은 종전대로 본다.
    """

    def __init__(self, days=(), years=()):
        super().__init__(days)
        self.years = frozenset(int(y) for y in years)

    @classmethod
    def from_doc(cls, doc):
        days = doc["holidays"]
        years = doc.get("years") or sorted({int(k[:4]) for k in days})
        return cls(days, years)


def day_type_of(d, holidays):
    """date → 시간표 요일축. holiday_collect.py 의 day_type_of 와 같은 규칙이다.

    d: datetime.date · holidays: HolidayCalendar(덮는 해를 안다) 또는 {'2026-10-03', ...}
    반환: 'weekday' / 'holiday'
    ※ 신정지선 토요일 예외는 규칙(rules)의 몫이라 여기서 다루지 않는다.
    ★ 표가 덮지 않는 해면 CalendarOutOfRange — 주말은 표 없이도 휴일이지만, 평일 공휴일을 놓치므로
      주말도 같이 거절한다(판정 하나만 되고 옆 날짜가 안 되는 모양을 만들지 않는다).
    """
    years = getattr(holidays, "years", None)
    if years and d.year not in years:
        raise CalendarOutOfRange(f"공휴일 표가 {d.year}년을 덮지 않는다(덮는 해 {sorted(years)}) — "
                                 f"{d.isoformat()} 의 요일축을 정할 수 없다")
    if d.weekday() >= 5 or d.isoformat() in holidays:
        return "holiday"
    return "weekday"
