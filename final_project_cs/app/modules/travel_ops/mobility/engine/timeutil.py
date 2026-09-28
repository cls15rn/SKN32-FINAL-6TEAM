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
    if t is None or t is False:
        return None
    if isinstance(t, bool):
        return None
    if isinstance(t, int):
        return t
    if isinstance(t, float):
        return int(t)
    s = str(t).strip()
    if not s:
        return None
    m = _HHMM_RE.match(s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        d = re.sub(r"\D", "", s)
        if len(d) not in (4, 6):
            return None
        if int(d) == 0:               # '000000' = 출발 없음(시·종착역)
            return None
        h, mi = int(d[:2]), int(d[2:4])
    if mi > 59:
        return None
    v = h * 60 + mi
    if v > SERVICE_DAY_MAX_MIN:       # 31:00 같은 값은 표기 오류로 본다
        return None
    return v


def to_service_min(t):
    """**사람이 준 시각**을 운행일 축으로. '00:30' → 1470(=24:30).

    시간표는 이미 정규화돼 있지만 요청 시각·도착 필요 시각은 벽시계 표기로 온다.
    같은 자를 안 쓰면 '00:30 까지 도착' 이 시간표의 24:50 열차보다 이르게 정렬돼
    막차 판정이 통째로 뒤집힌다.
    04 시 이전만 올린다 — 06:00 을 30:00 으로 올리지 않는다.
    """
    v = to_min(t)
    if v is None:
        return None
    if v < SERVICE_DAY_START_MIN:
        v += MIN_DAY
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


def day_type_of(d, holidays):
    """date → 시간표 요일축. holiday_collect.py 의 day_type_of 와 같은 규칙이다.

    d: datetime.date · holidays: {'2026-10-03', ...}
    반환: 'weekday' / 'holiday'
    ※ 신정지선 토요일 예외는 규칙(rules)의 몫이라 여기서 다루지 않는다.
    """
    if d.weekday() >= 5 or d.isoformat() in holidays:
        return "holiday"
    return "weekday"
