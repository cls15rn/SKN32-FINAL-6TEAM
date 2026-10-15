# -*- coding: utf-8 -*-
"""항공 문장을 **규칙으로** 구조로 옮긴다 — 모델 없이. `[2026-10-14 팀장 결정]` 예약(항공 · 기차 · 버스)은 칸이 정해진 영역이라
LLM 판단이 아니라 채우기다 → 「예약 모듈」(규칙 기반). 숙소는 취향 · 자유 표현이 있어 에이전트(LLM)로 남는다.

내보내는 모양은 모델이 내던 것과 **같다**(`interpret.Interpretation` 의 원재료 dict, 날짜 근거 조각 `*_text` 포함) — 그래서 뒤의
서버 검증(`parse → ground → settle_years → needs`)을 그대로 탄다. 뽑지 못한 칸은 비워 둔다(지어내지 않는다 → 되묻기).

규칙(우리가 고른 것 — 정답 문장 50개 `scripts/interpret_cases.json` 으로 잰다, `scripts/eval_interpret.py --rules`):
- 공항: 이름 표(`AIRPORTS`, 한국어 · 영어 · 코드). 「X에서 Y」 「X→Y」 「X-Y」 「X 출발」 「Y 가는 · 행 · 도착 · 로」 「from X to Y」.
  두 곳이면 앞이 출발, 뒤가 도착. 한 곳이면 「출발 · 에서」가 붙었을 때만 출발, 아니면 도착. 나라 이름은 대표 공항이 하나일 때만(태국 → BKK,
  영국 → LHR, 홍콩 → HKG) — 일본 · 미국 · 유럽은 비운다.
- 날짜: 「M월 D일」 「M/D」 「D일」(앞 날짜의 달) 「오늘 · 내일 · 모레 · 낼모레 · 글피」 「다음 달 D일」 「내년 M월 D일」 「(다다음 · 다음 · 이번)주 X요일」
  「N일 · N주 · N달 뒤」 「이번 여행」(여행 첫날). 첫 날짜 = 가는 날, 둘째 = 오는 날; 「귀국 · 돌아 · 오는 · 올 때 · 까지」가 붙으면 오는 날.
  「편도」면 오는 날 없음. 「D일로 바꿔」면 가는 날을 그 날로. 연도 · 요일 · 박 수 맞추기는 서버 검증(`settle_years`)이 한다.
- 인원: 「N명」 「N인」 「성인 · 어른 N」 「혼자 · 1인」 「아이 · 어린이 · 애 N」 「유아 N」 「for N」. 「A명 아니 B명」은 B.
- 좌석: 비즈니스 · 퍼스트(일등석) · 이코노미 — 빼는 말(말고 · 싫 · 힘들)이 붙은 것은 제외.
- 직항: 「직항」 → True, 「경유도 괜찮 · 경유 가능」 → False.
- 시간대: 절(쉼표 · 마침표 · 「~고」)마다 새벽 · 오전(아침) · 오후(점심 · 낮) · 저녁(밤) · 「N시」. 「오는 날 · 올 때 · 귀국」 절은 오는 편, 「가는 날 · 출발」 절은 가는 편.
  빼는 말이 있으면 나머지 시간대, 「~도 괜찮 · 상관없」이면 제한 없음. 「갈 때랑 똑같이」면 가는 편과 같게.
- 가격: 「N만 원」 「N만원 이하 · 까지」 「왕복으로 N」(만 원 단위) — 「왕복」이 붙으면 round_trip, 「편도」면 one_way.
- `[2026-10-14]` 교통수단(`mode`): 기차 · 열차 · KTX · SRT · ITX · 무궁화 · 새마을 → train, 고속버스 · 시외버스 · 버스 → bus, 없으면 flight.
  `[2026-10-15]` 공항철도 · AREX · 직통열차도 train(서울역 ↔ 인천공항이면 `offers/arex.py` 직통 시간표로 간다).
  「X 말고 Y」면 Y. 기차 · 버스의 출발 · 도착은 역 · 터미널 **이름**(`offers/transit.STATIONS` 사전 + 「역 · 터미널」 붙은 말) — 공항 표를 안 본다.
  열차 등급(KTX · SRT · ITX · 무궁화 · 새마을) · 버스 등급(프리미엄 · 우등 · 일반)은 `grade`, 「시외 · 고속」은 `kind`.
- 할 일: 「확정 · 예약 상태 · 발권」 → status, 「항공(권) 말고 · 비행기 말고」 → unclear, 그 밖 → search.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any

from app.domains.travel_ops.components.booking.offers.transit import STATIONS

#: 공항 · 도시 이름 → 코드. 긴 이름을 먼저 맞춘다(「김포공항」 「인천공항」). 우리가 고른 표 — 자주 가는 곳 위주
AIRPORTS: dict[str, str] = {
    # 국내
    "인천공항": "ICN", "인천": "ICN", "서울": "ICN", "incheon": "ICN", "seoul": "ICN",
    "김포공항": "GMP", "김포": "GMP", "gimpo": "GMP",
    "김해": "PUS", "부산": "PUS", "busan": "PUS", "gimhae": "PUS",
    "제주도": "CJU", "제주": "CJU", "jeju": "CJU",
    "청주": "CJJ", "대구": "TAE", "광주": "KWJ", "무안": "MWX", "여수": "RSU", "울산": "USN", "양양": "YNY", "포항": "KPO",
    "사천": "HIN", "진주": "HIN", "군산": "KUV", "원주": "WJU",
    # 일본
    "나리타": "NRT", "narita": "NRT", "하네다": "HND", "haneda": "HND", "도쿄": "NRT", "동경": "NRT", "tokyo": "NRT",
    "간사이": "KIX", "오사카": "KIX", "osaka": "KIX", "kansai": "KIX", "후쿠오카": "FUK", "fukuoka": "FUK",
    "신치토세": "CTS", "삿포로": "CTS", "sapporo": "CTS", "나고야": "NGO", "nagoya": "NGO", "오키나와": "OKA", "okinawa": "OKA",
    # 동남아 · 중화권
    "방콕": "BKK", "bangkok": "BKK", "태국": "BKK", "다낭": "DAD", "danang": "DAD", "하노이": "HAN", "hanoi": "HAN",
    "호치민": "SGN", "나트랑": "CXR", "푸꾸옥": "PQC", "타이베이": "TPE", "타이페이": "TPE", "대만": "TPE", "taipei": "TPE",
    "홍콩": "HKG", "hongkong": "HKG", "hong kong": "HKG", "싱가포르": "SIN", "싱가폴": "SIN", "singapore": "SIN",
    "세부": "CEB", "마닐라": "MNL", "보라카이": "MPH", "쿠알라룸푸르": "KUL", "코타키나발루": "BKI", "발리": "DPS", "덴파사르": "DPS", "bali": "DPS",
    "자카르타": "CGK", "푸켓": "HKT", "푸껫": "HKT", "치앙마이": "CNX", "상하이": "PVG", "상해": "PVG", "베이징": "PEK", "북경": "PEK",
    "괌": "GUM", "guam": "GUM", "사이판": "SPN", "울란바토르": "UBN", "블라디보스토크": "VVO",
    # 미주 · 유럽 · 대양주
    "뉴욕": "JFK", "newyork": "JFK", "new york": "JFK", "로스앤젤레스": "LAX", "la": "LAX", "los angeles": "LAX", "샌프란시스코": "SFO",
    "시카고": "ORD", "하와이": "HNL", "호놀룰루": "HNL", "밴쿠버": "YVR", "토론토": "YYZ",
    "런던": "LHR", "영국": "LHR", "london": "LHR", "파리": "CDG", "paris": "CDG", "프랑크푸르트": "FRA", "뮌헨": "MUC", "로마": "FCO",
    "바르셀로나": "BCN", "마드리드": "MAD", "암스테르담": "AMS", "프라하": "PRG", "비엔나": "VIE", "빈": "VIE", "취리히": "ZRH",
    "이스탄불": "IST", "두바이": "DXB", "시드니": "SYD", "오클랜드": "AKL",
}
_KOREAN = {"ICN", "GMP", "PUS", "CJU", "CJJ", "TAE", "KWJ", "MWX", "RSU", "USN", "YNY", "KPO", "HIN", "KUV", "WJU"}
#: 나라 · 지역 이름인데 공항이 여럿이라 비우는 것(되묻는다)
_VAGUE = ("일본", "미국", "유럽", "중국", "베트남", "필리핀", "호주", "캐나다", "프랑스", "독일", "이탈리아", "스페인", "동남아")
_CODE = re.compile(r"\b([A-Z]{3})\b")
_WORD = re.compile(r"[가-힣]+|[A-Za-z]+")

_NEG = ("말고", "싫", "제외", "빼", "아니", "피해", "힘들", "안 ")
_BANDS = {"dawn": ("새벽", "첫 비행기", "첫비행기", "첫차"), "morning": ("오전", "아침"), "afternoon": ("오후", "점심", "낮"),
          "evening": ("저녁", "밤", "퇴근")}
_ALL_BANDS = ["dawn", "morning", "afternoon", "evening"]
_HOUR = re.compile(r"(\d{1,2})\s*시")
_RETURN_WORDS = ("오는날", "오는 날", "올때", "올 때", "귀국", "돌아", "복귀", "오는 비행기", "오는비행기", "올 거", "올거", "오는 편", "오는편")
_CLAUSE = re.compile(r"[.。!?\n]|,\s*|\s(?:그리고|근데|그런데|그래서)\s")

_NUM_WORD = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4, "다섯": 5, "여섯": 6}
_PEOPLE = re.compile(r"(?<![\d시])(?<!시\s)(\d+)\s*(?:명|인|분)")     # 「3시 30분」의 「30분」은 인원이 아니다(검토 10-14)
_ADULTS = re.compile(r"(?:성인|어른|대인)\s*(\d+|하나|한|둘|두|셋|세|넷|네)")
_CHILDREN = re.compile(r"(?:아이|어린이|애|소아|아동)\s*(\d+|하나|한|둘|두|셋|세|넷|네)")
_INFANTS = re.compile(r"(?:유아|아기)\s*(\d+|하나|한|둘|두)")
_ENGLISH_PEOPLE = re.compile(r"\bfor\s+(\d+)\b")
_CORRECT_PEOPLE = re.compile(r"(\d+)\s*명\s*아니\s*(\d+)\s*명")

_PRICE = re.compile(r"(\d+)\s*만\s*원?|(\d{5,7})\s*원")
_PRICE_BARE = re.compile(r"(?:왕복|편도)\s*(?:으로|기준|은|는)?\s*(\d{1,3})(?!\d)(?!\s*(?:일|월|명|시|박|인|분))")

_MONTH_DAY = re.compile(r"(?:(?P<y>(?:19|20)\d{2})\s*[년.-]?\s*)?(?P<pre>내년|내후년|올해)?\s*(?P<m>\d{1,2})\s*(?:월|/|\.|-)\s*(?P<d>\d{1,2})\s*일?(?!\d)")
_NEXT_MONTH_DAY = re.compile(r"(다음\s*달|담달|다음달)\s*(\d{1,2})\s*일")
_DAY_ONLY = re.compile(r"(?<![\d/월])(\d{1,2})\s*일(?!\s*(?:뒤|후|동안|간|정도|쯤|째))")
_RELATIVE = {"오늘": 0, "내일": 1, "모레": 2, "낼모레": 2, "글피": 3, "내일모레": 2}
_WEEK = re.compile(r"(다다음\s*주|다음\s*주|담주|이번\s*주)\s*([월화수목금토일])요일")
_BARE_WEEKDAY = re.compile(r"(?<![주\s])\s*([월화수목금토일])요일|(?<=^)([월화수목금토일])요일")
_OFFSET = re.compile(r"(\d+|일|이|삼|사|오|한|두|세|네)\s*(주일|일|주|달|개월)\s*(뒤|후|있다가|지나서)")
_KOREAN_NUMBER = {"일": 1, "한": 1, "이": 2, "두": 2, "삼": 3, "세": 3, "사": 4, "네": 4, "오": 5}
#: 받는 칸이 없는 바람 — 답 끝에 「아직 반영하지 못한 것」으로 돌려준다(문장 조각 그대로)
_WISH = re.compile(r"창가\s*좌석|통로\s*좌석|좌석\s*지정|수하물[^,.。]{0,10}|마일리지[^,.。]{0,8}|저가\s*항공\s*말고|분위기[^,.。]{0,12}|편의성[^,.。]{0,10}|가격\s*차이[^,.。]{0,10}|최대한\s*저렴하게|싸게")
_CORRECTED = re.compile(r"(\d{1,2})\s*일\s*로\s*(?:바꿔|바꾸|변경|옮겨|해\s*줘|할게|하자)")
_ENGLISH_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{1,2})\b", re.I)
_MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ---------------------------------------------------------------- 공항
def _ascii_letter(ch: str) -> bool:
    return bool(ch) and ch.isascii() and ch.isalpha()


def _places(text: str, table: dict[str, str] | None = None) -> list[tuple[int, int, str, str]]:
    """문장에 나온 공항 · 도시(또는 `table` 의 역 · 터미널)를 (시작, 끝, 값, 이름) 으로, 긴 이름 우선 · 겹치지 않게."""
    table = AIRPORTS if table is None else table
    low = text.casefold()
    found: list[tuple[int, int, str, str]] = []
    taken: list[tuple[int, int]] = []
    for name in sorted(table, key=len, reverse=True):
        start = 0
        while True:
            index = low.find(name, start)
            if index < 0:
                break
            end = index + len(name)
            if name.isascii() and (_ascii_letter(low[index - 1:index]) or _ascii_letter(low[end:end + 1])):
                start = end
                continue                                   # 「la」가 「plan」 안에서 잡히지 않게(한글 조사 「incheon에서」는 경계로 본다)
            if not any(s < end and index < e for s, e in taken):
                found.append((index, end, table[name], name))
                taken.append((index, end))
            start = end
    for match in (_CODE.finditer(text) if table is AIRPORTS else ()):
        if match.group(1) in set(AIRPORTS.values()) and not any(s < match.end() and match.start() < e for s, e in taken):
            found.append((match.start(), match.end(), match.group(1), match.group(1)))
            taken.append((match.start(), match.end()))
    return sorted(found)


#: 도시 이름 → 도시, 공항 코드 → 도시 — 「도쿄 하네다」처럼 붙어 있을 때 공항 쪽을 쓰기 위해
_CITY = {"도쿄": "도쿄", "동경": "도쿄", "tokyo": "도쿄", "서울": "서울", "seoul": "서울", "오사카": "오사카", "osaka": "오사카", "부산": "부산"}
_CITY_OF = {"NRT": "도쿄", "HND": "도쿄", "ICN": "서울", "GMP": "서울", "KIX": "오사카", "PUS": "부산"}
_ORIGIN_AFTER = ("에서", "공항에서", "출발", "공항에 도착", "공항에서")
_DEST_AFTER = ("로", "으로", "행", "가는", "까지", "도착", "에 가", "에가", "을", "를", " 가")


def _endpoints(text: str, table: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """출발 · 도착을 표지로 가른다 — 「X에서 · X 출발 · X공항에 도착(해서 떠남) · from X」는 출발, 「Y로 · Y행 · Y 가는 · to Y」는 도착.
    표지가 없으면 순서(앞 = 출발, 뒤 = 도착). 도시 + 공항이 붙어 있으면(「도쿄 하네다」) 공항 쪽을 쓴다."""
    places = _places(text, table)
    merged: list[tuple[int, int, str, str]] = []
    for place in places:
        if merged and place[0] - merged[-1][1] <= 1 and _CITY.get(merged[-1][3]) is not None and _CITY.get(merged[-1][3]) == _CITY_OF.get(place[2]) and merged[-1][2] != place[2]:
            merged[-1] = place                          # 「도쿄 하네다」 → 하네다(도시 + 그 도시의 공항)
            continue
        merged.append(place)                            # 같은 공항을 두 번 말해도(「인천공항에서 … 인천공항으로」) 먼저 정해진 쪽이 남는다
    if not merged:
        return None, None
    low = text.casefold()
    origin = destination = None
    free: list[str] = []
    for start, end, code, _ in merged:
        after, before = text[end:end + 7], low[max(0, start - 6):start]
        if "from " in before or after.startswith(_ORIGIN_AFTER) or after.startswith("공항") and ("에서" in after or "도착" in after or "출발" in after):
            origin = origin or code
        elif "to " in before or after.startswith(_DEST_AFTER) or after.startswith("공항") and any(after.startswith("공항" + d) for d in _DEST_AFTER):
            destination = destination or code
        else:
            free.append(code)
    for code in free:
        if origin is None and (destination is not None or len(merged) >= 2) and code != destination:
            origin = code
        elif destination is None and code != origin:
            destination = code
    return origin, destination


def _airports(text: str) -> tuple[str | None, str | None]:
    return _endpoints(text, AIRPORTS)


# ---------------------------------------------------------------- 교통수단 (2026-10-14 기차 · 버스)
#: 고객 말 → 역 · 터미널 사전 값(이름 그대로). 「서울역」 「부산 터미널」처럼 붙은 말도 같은 곳
_STATION_TABLE: dict[str, str] = {name: name for name in STATIONS}
_STATION_TABLE.update({f"{name}역": name for name in STATIONS if not name.endswith("역")})
_STATION_TABLE.update({f"{name} 터미널": name for name in STATIONS} | {f"{name}터미널": name for name in STATIONS})
_MODE_WORDS = {"train": ("기차", "열차", "ktx", "srt", "itx", "무궁화", "새마을", "train", "공항철도", "arex", "직통열차"),
               "bus": ("고속버스", "시외버스", "고속 버스", "시외 버스", "공항버스", "공항 버스", "리무진", "버스", "bus"),
               "flight": ("비행기", "항공권", "항공편", "항공", "flight", "플라이트"),
               # `[2026-10-15]` 상품형 — 시티투어버스(「투어버스」의 「버스」보다 앞에서 잡혀 버스가 아니라 이것)
               "tour": ("시티투어", "시티 투어", "투어버스", "투어 버스", "타이거버스", "노랑풍선",
                        "유람선", "크루즈", "이크루즈", "이랜드크루즈",
                        "렌터카", "렌트카", "차 빌려", "차를 빌려", "한강버스", "한강 버스")}
#: 공항리무진을 가리키는 말
_LIMOUSINE = ("리무진", "공항버스", "공항 버스")
#: 상품형 종류를 가리키는 말 — 앞에서부터(없으면 시티투어)
_CRUISE_WORDS = ("유람선", "크루즈")
_TOUR_KINDS = (("river_bus", ("한강버스", "한강 버스")), ("cruise", _CRUISE_WORDS),
               ("rental", ("렌터카", "렌트카", "차 빌려", "차를 빌려")))
#: 유람선 선착장 — 말하면 그 선착장 상품만
_PIERS = ("여의도", "잠실")
#: `[2026-10-15]` 공항버스 · 리무진은 버스로 읽는다(서울 ↔ 인천공항이면 booking 이 공항리무진 안내로 답한다) — 시내버스 · 셔틀만 뺀다
_NOT_BUS = ("시내버스", "셔틀")
#: 교통수단 뒤에 붙는 빼는 말 — 「기차 말고 · 빼고 · 제외하고 · 아니고 · 안 타고 · 타지 말고」 (팀장 점검 LB-06 「제외」 추가, 10-15)
_MODE_NEG = ("말고", "빼고", "말구", "아니고", "아니라", "제외", "빼구", "안 타", "안타", "타지 말", "타지 않", "싫", "피해")
_GRADES = ("KTX", "SRT", "ITX", "AREX", "무궁화", "새마을", "프리미엄", "우등", "일반")


def _mode(text: str) -> str | None:
    """문장이 말하는 교통수단. 「X 말고 Y」면 X 는 뺀다. 말하지 않았으면 None(→ 항공)."""
    low = text.casefold()
    said: list[tuple[int, str]] = []
    for mode, words in _MODE_WORDS.items():
        for word in words:
            start = 0
            while (index := low.find(word, start)) >= 0:
                start = index + len(word)
                if mode == "bus" and any(low[max(0, index - 4):index + 6].find(no) >= 0 for no in _NOT_BUS):
                    continue
                if word.isascii() and (_ascii_letter(low[index - 1:index]) or _ascii_letter(low[start:start + 1])):
                    continue                                 # 「ktx로」 「srt를」 — 한글 조사는 단어 경계(검토 10-14)
                tail = low[start:start + 8]
                if any(no in tail for no in _MODE_NEG):
                    continue                                 # 「기차 말고 비행기」 「기차 제외하고 버스」 — 기차는 뺀다
                said.append((index, mode))
    if not said:
        return None
    return sorted(said)[0][1]


def _kind(text: str) -> str | None:
    low = text.casefold()
    if "시외" in low:
        return "suburbs"
    if "고속" in low:
        return "express"
    return None


def _grade(text: str) -> str | None:
    found = [grade for grade in _GRADES if grade.casefold() in text.casefold()]
    for grade in found:
        tail = text.casefold()[text.casefold().find(grade.casefold()) + len(grade):][:6]
        if any(no in tail for no in _NEG):
            continue
        return grade
    return None


# ---------------------------------------------------------------- 날짜
def _dates(text: str, *, today: date, trip: dict[str, Any] | None) -> list[tuple[int, date, str]]:
    """문장의 날짜 언급을 (위치, 값, 근거) 로 순서대로. 값은 「올해 기준」이고 연도 · 요일 맞추기는 서버 검증이 한다."""
    out: list[tuple[int, date, str]] = []
    taken: list[tuple[int, int]] = []

    def put(start: int, end: int, value: date | None, quote: str) -> None:
        if value is None or any(s < end and start < e for s, e in taken):
            return
        out.append((start, value, quote))
        taken.append((start, end))

    for m in _NEXT_MONTH_DAY.finditer(text):
        month = today.month % 12 + 1
        year = today.year + (1 if month == 1 else 0)
        put(m.start(), m.end(), _safe(year, month, int(m.group(2))), m.group(0))
    for m in _MONTH_DAY.finditer(text):
        year = int(m.group("y")) if m.group("y") else today.year + (1 if m.group("pre") == "내년" else 2 if m.group("pre") == "내후년" else 0)
        put(m.start(), m.end(), _safe(year, int(m.group("m")), int(m.group("d"))), m.group(0).strip())
    for m in _ENGLISH_DATE.finditer(text):
        put(m.start(), m.end(), _safe(today.year, _MONTHS_EN.index(m.group(1)[:3].lower()) + 1, int(m.group(2))), m.group(0))
    for m in _WEEK.finditer(text):
        head, weekday = m.group(1).replace(" ", ""), "월화수목금토일".index(m.group(2))
        weeks = 2 if head.startswith("다다음") else 1 if head in ("다음주", "담주") else 0
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7 * weeks)
        put(m.start(), m.end(), monday + timedelta(days=weekday), m.group(0))
    for m in _BARE_WEEKDAY.finditer(text):
        # `[2026-10-14]` 「토요일에 가서 일요일 저녁에」 — 주(週) 말 없이 요일만: 오늘 이후 가장 가까운 그 요일(오늘이 그 요일이면 오늘)
        weekday = "월화수목금토일".index(m.group(1))
        put(m.start(), m.end(), today + timedelta(days=(weekday - today.weekday()) % 7), m.group(0))
    for m in _OFFSET.finditer(text):
        raw, unit = m.group(1), m.group(2)
        count = int(raw) if raw.isdigit() else _KOREAN_NUMBER.get(raw, 1)
        if unit in ("일",):
            value = today + timedelta(days=count)
        elif unit in ("주", "주일"):
            value = today + timedelta(days=7 * (1 if unit == "주일" and raw == "일" else count))
        else:
            month = today.month - 1 + count
            value = _safe(today.year + month // 12, month % 12 + 1, min(today.day, 28))
        put(m.start(), m.end(), value, m.group(0))
    for word, days in sorted(_RELATIVE.items(), key=lambda kv: -len(kv[0])):
        start = 0
        while True:
            index = text.find(word, start)
            if index < 0:
                break
            put(index, index + len(word), today + timedelta(days=days), word)
            start = index + len(word)
    for m in _DAY_ONLY.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        earlier = [value for start, value, _ in sorted(out) if start < m.start()]
        base = earlier[-1] if earlier else today
        put(m.start(), m.end(), _safe(base.year, base.month, int(m.group(1))), m.group(0))
    if trip and ("이번 여행" in text or "이 여행" in text) and not out and trip.get("first_day"):
        try:
            out.append((text.index("여행"), date.fromisoformat(str(trip["first_day"])), "trip"))
        except (ValueError, TypeError):
            pass
    return sorted(out)


def _is_return(text: str, position: int) -> bool:
    window = text[max(0, position - 12):position + 14]
    return any(word in window for word in _RETURN_WORDS) or text[position:position + 12].find("까지") >= 0


# ---------------------------------------------------------------- 시간대
_NEG_BAND = ("말고", "싫", "제외", "빼", "피해", "힘들", "안 ")


def _bands_in(clause: str) -> tuple[list[str], list[str]]:
    """절에서 말한 시간대와, 그중 바로 뒤(8글자)에 빼는 말이 붙은 시간대."""
    said: list[str] = []
    negated: list[str] = []
    for band, words in _BANDS.items():
        for word in words:
            index = clause.find(word)
            if index < 0 or (word == "밤" and clause[max(0, index - 2):index] in ("하룻", "하루", "룻")):
                continue                                # 「하룻밤」은 시간대가 아니다
            said.append(band)
            if any(neg in clause[index + len(word):index + len(word) + 12] for neg in _NEG_BAND):
                negated.append(band)
            break
    for m in _HOUR.finditer(clause):
        hour = int(m.group(1))
        before = clause[max(0, m.start() - 4):m.start()]
        if hour < 12 and any(word in before for word in ("오후", "저녁", "밤", "낮")):
            hour += 12                                  # 「저녁 7시」 「밤 11시」 — 그 시각의 시간대만(검토 10-14: 전에는 「오후」만 봐 오전이 섞였다)
        elif hour < 12 and "오후" in clause and not any(word in clause for word in ("오전", "아침", "새벽")):
            hour += 12
        band = "dawn" if hour < 7 else "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        if band not in said:
            said.append(band)
    return [b for b in _ALL_BANDS if b in said], [b for b in _ALL_BANDS if b in negated]


_DEPART_WORDS = ("가는날", "가는 날", "갈때", "갈 때", "가는 편", "가는편", "출국")


def _split_depart_return(clauses: list[str]) -> list[str]:
    """한 절에 「출발」과 오는 말(귀국 · 돌아 …)이 같이 있으면 「출발」 **뒤**에서 두 절로 가른다 — 「오전 출발하고 오후 귀국」 「오후에 출발하는 걸로
    저녁에 돌아올게」(팀장 점검 LB-07, 10-15). 「출발」 앞의 시간대는 가는 편, 뒤는 오는 편. 쉼표 · 「그리고」로는 이미 갈라져 있다."""
    out: list[str] = []
    for clause in clauses:
        at = clause.find("출발")
        if at >= 0 and any(word in clause for word in _RETURN_WORDS) and not any(word in clause for word in _DEPART_WORDS):
            head, tail = clause[:at + 2], clause[at + 2:]
            out += [part for part in (head, tail) if part.strip()]
        else:
            out.append(clause)
    return out


def _times(text: str) -> tuple[list[str] | None, str | None, list[str] | None, str | None]:
    """절마다: 「가는 날 · 갈 때」가 있으면 가는 편, 「오는 날 · 올 때 · 귀국」이 있으면 오는 편, 둘 다 없으면 아직 비어 있는 가는 편.
    「갈 때랑 똑같이」는 오는 편 = 가는 편. 「오는 날은 상관없어」는 오는 편 제한 없음."""
    depart: tuple[list[str] | None, str | None] = (None, None)
    back: tuple[list[str] | None, str | None] = (None, None)
    for clause in _split_depart_return([c.strip() for c in _CLAUSE.split(text) if c and c.strip()]):
        bands, negated = _bands_in(clause)
        is_return = any(word in clause for word in _RETURN_WORDS)
        # 「출발」은 오는 말(귀국 · 돌아)이 같은 절에 없을 때만 가는 편 표지 — 「오전 출발하고 오후 귀국」(팀장 점검 LB-07, 10-15)
        is_depart = any(word in clause for word in _DEPART_WORDS) or ("출발" in clause and not is_return)
        is_return = is_return and not is_depart
        if not bands:
            if ("똑같이" in clause or "같게" in clause or "동일" in clause) and depart[1] is not None and back == (None, None):
                back = (depart[0], clause)
            if is_return and ("상관없" in clause or "아무 때" in clause or "아무때" in clause):
                back = (None, clause)
            continue
        if "도 괜찮" in clause or "도괜찮" in clause or "상관없" in clause:
            chosen: list[str] | None = None
        elif negated:
            kept = [band for band in bands if band not in negated]
            chosen = kept or [band for band in _ALL_BANDS if band not in negated] or None
        else:
            chosen = bands
        if is_return:
            if back == (None, None):
                back = (chosen, clause)
        elif depart[1] is None:
            depart = (chosen, clause)
    return depart[0], depart[1], back[0], back[1]


# ---------------------------------------------------------------- 인원 · 좌석 · 가격
def _people(text: str) -> tuple[int | None, int | None, int | None]:
    adults = children = infants = None
    corrected = _CORRECT_PEOPLE.search(text)
    if m := _ADULTS.search(text):
        adults = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORD.get(m.group(1))
    if m := _CHILDREN.search(text):
        children = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORD.get(m.group(1))
    if m := _INFANTS.search(text):
        infants = int(m.group(1)) if m.group(1).isdigit() else _NUM_WORD.get(m.group(1))
    if adults is None:
        if corrected:
            adults = int(corrected.group(2))
        elif "혼자" in text or re.search(r"\b1\s*인\b|한\s*명|나\s*혼자|alone|solo", text):
            adults = 1
        elif m := (_PEOPLE.search(text) or _ENGLISH_PEOPLE.search(text.casefold())):
            total = int(m.group(1))
            adults = total - (children or 0) - (infants or 0) if children or infants else total
            if adults <= 0:
                adults = None
    return adults, children, infants


def _cabin(text: str) -> str | None:
    for word, cabin in (("퍼스트", "FIRST"), ("일등석", "FIRST"), ("비즈니스", "BUSINESS"), ("비지니스", "BUSINESS"), ("이코노미", "ECONOMY")):
        if word in text:
            tail = text[text.index(word):text.index(word) + 14]
            if not any(neg in tail for neg in _NEG):
                return cabin
    return None


def _direct(text: str) -> bool | None:
    if re.search(r"경유\s*(?:도|은|는)?\s*(?:괜찮|상관없|가능|돼|되)", text):
        return False
    if "직항" in text:
        return True
    return None


def _price(text: str) -> tuple[int | None, str | None]:
    basis = "round_trip" if "왕복" in text else "one_way" if "편도" in text else None
    if m := _PRICE.search(text):
        value = int(m.group(1)) * 10000 if m.group(1) else int(m.group(2))
        return value, basis
    if m := _PRICE_BARE.search(text):
        return int(m.group(1)) * 10000, basis
    return None, None


def _task(text: str) -> str:
    if re.search(r"(항공권|비행기|항공|티켓|기차|열차|버스)\s*(은|는)?\s*말고\s*(숙소|호텔|식당|맛집|렌터카)", text):
        return "unclear"
    if any(word in text for word in ("확정", "예약 상태", "예약상태", "발권", "예약됐", "예약 됐", "예약 확인")) and "찾" not in text:
        return "status"
    return "search"


# ---------------------------------------------------------------- 전체
def interpret(text: str, *, today: date, trip: dict[str, Any] | None = None) -> dict[str, Any]:
    """문장 → 모델이 내던 모양의 dict. 뒤의 `interpret.parse · ground · settle_years · needs` 를 그대로 탄다."""
    task = _task(text)
    if task != "search":
        return {"task": task, "other_requests": [], "missing": [], "question": None}
    mode = _mode(text) or "flight"
    tour_kind = None
    if mode == "tour":
        tour_kind = next((kind for kind, words in _TOUR_KINDS if any(word in text for word in words)), "city_tour")
        origin = next((pier for pier in _PIERS if pier in text), None) if tour_kind == "cruise" else None
        destination = None
    else:
        origin, destination = _airports(text) if mode == "flight" else _endpoints(text, _STATION_TABLE)
        if mode == "bus" and any(word in text for word in _LIMOUSINE) and (origin or destination):
            # `[10-15]` 「인천공항에서 잠실 가는 리무진」 — 반대쪽이 역 · 터미널 사전에 없는 동네면 서울로(공항리무진은 서울 노선 · 동네는 팀이 노선 정보로 좁힌다)
            if origin and origin.startswith("인천공항") and destination is None:
                destination = "서울"
            elif destination and destination.startswith("인천공항") and origin is None:
                origin = "서울"
    if origin and destination and origin == destination:
        destination = None
    dates = _dates(text, today=today, trip=trip)
    one_way = bool(re.search(r"편도(?!\s*(?:기준|가격|요금|\d))", text))
    depart = back = None
    for position, value, quote in dates:
        if depart is not None and value == depart[0]:
            continue                                        # 같은 날을 두 번 말함(「내일 … 내일」)
        if _is_return(text, position) and depart is not None:
            if back is None:
                back = (value, quote)
        elif depart is None:
            depart = (value, quote)
        elif back is None and not one_way:
            back = (value, quote)
    if corrected := _CORRECTED.search(text):
        if depart is not None:
            moved = _safe(depart[0].year, depart[0].month, int(corrected.group(1)))
            if moved:
                depart = (moved, corrected.group(0))
    if one_way:
        back = None
    adults, children, infants = _people(text)
    if adults is None and trip and depart and depart[1] == "trip" and isinstance(trip.get("party_size"), int):
        adults = trip["party_size"]                         # 「이번 여행」 — 인원은 여행 인원
    others = [m.group(0).strip() for m in _WISH.finditer(text)][:5]
    depart_times, depart_quote, return_times, return_quote = _times(text)
    price, basis = _price(text)
    domestic = None
    if mode != "flight":
        domestic = True
    elif origin and destination:
        domestic = origin in _KOREAN and destination in _KOREAN
    return {
        "task": "search", "mode": mode, "origin": origin, "destination": destination,
        "grade": _grade(text) if mode in ("train", "bus") else None,
        "kind": _kind(text) if mode == "bus" else tour_kind,
        "depart_date": depart[0].isoformat() if depart else None, "depart_date_text": depart[1] if depart else None,
        "return_date": back[0].isoformat() if back else None, "return_date_text": back[1] if back else None,
        "adults": adults, "children": children, "infants": infants,
        "cabin": _cabin(text) if mode == "flight" else None, "direct_only": _direct(text) if mode == "flight" else None,
        "depart_times": depart_times, "depart_times_text": depart_quote,
        "return_times": return_times, "return_times_text": return_quote,
        "max_price": price, "max_price_basis": basis if price else None, "domestic": domestic,
        "other_requests": others, "missing": [], "question": None,
    }


__all__ = ["AIRPORTS", "STATIONS", "interpret"]
