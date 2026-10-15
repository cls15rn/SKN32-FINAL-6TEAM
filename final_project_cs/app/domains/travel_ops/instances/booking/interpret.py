# -*- coding: utf-8 -*-
"""모델이 낸 항공 해석을 **검증**한다 — 모델은 문장을 구조로 옮기고(도시 이름 → 공항 코드 포함), 쓸 수 있는지는 서버가 정한다. `[2026-10-08]`

- 프롬프트: `prompts/flight/interpret.v5.md` (키 `flight.interpret`). 모델 호출은 한 번이다.
- `[2026-10-09]` 출발 시간대(`depart_times`: dawn · morning · afternoon · evening)를 받는다. 시각 경계는 서버(`TIME_BANDS`)가 정한다 —
  모델은 고객이 말한 시간대 이름만 옮기고, 근거 조각(`depart_times_text`)이 문장에 없으면 비운다(날짜와 같은 방식).
- 모양이 틀리면 `InterpretationInvalid` — 고쳐 맞추지 않는다. 공항 코드는 영문 대문자 3글자만 받는다.
- 무엇이 빠졌는지는 `needs()` 가 값으로 다시 센다 — 지난 출발일 · 출발일보다 앞선 귀국일 · 출발지와 같은 도착지는 없는 값으로 본다.
- `[2026-10-14]` 예약 모듈 확장 — `mode`(flight · train · bus). 항공만 출발 · 도착이 공항 코드이고, 기차 · 버스는 역 · 터미널 **이름**(규칙 사전 값)이다.
  `domestic` 은 항공에서만 필수(어느 도구를 부를지), 기차 · 버스는 국내뿐이라 True 로 둔다. `grade` 는 열차 등급(KTX · SRT …) · 버스 등급(우등 · 프리미엄 …) 바람.
- `[2026-10-15]` `mode="tour"`(시티투어버스 `city_tour` · 한강 유람선 `cruise` · 렌터카 `rental` · 한강버스 `river_bus`): 도착이 없고 날짜만 필수(`needs`).
  유람선은 선착장(여의도 · 잠실)을 말하면 `origin` 에 담는다.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domains.travel_ops.instances._shared.interpret_dates import (
    CALENDAR_DAYS, ground_dates, keep_quoted, model_context, settle_dates)

REQUIRED = ("origin", "destination", "depart_date", "adults")
LABELS = {"origin": "출발지", "destination": "도착지", "depart_date": "출발 날짜", "adults": "성인 인원",
          "return_date": "돌아오는 날짜"}
_CODE = r"^[A-Z]{3}$"
#: 출발 시간대 — (이름, 한국어, 시작 시각 포함, 끝 시각 제외). 출발 공항 현지 시각 기준. 우리가 고른 값(2026-10-09, 사용자와 정함)
TIME_BANDS = (("dawn", "새벽", "00:00", "07:00"), ("morning", "오전", "07:00", "12:00"),
              ("afternoon", "오후", "12:00", "18:00"), ("evening", "저녁", "18:00", "24:00"))
Band = Literal["dawn", "morning", "afternoon", "evening"]
Mode = Literal["flight", "train", "bus", "tour"]   # `[10-15]` tour = 시티투어버스 같은 상품형(출발 · 도착 없음)


class InterpretationInvalid(ValueError):
    """모델 출력이 약속한 모양이 아니다."""


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task: Literal["search", "status", "unclear"]
    mode: Mode = "flight"
    origin: str | None = Field(default=None, max_length=40)
    destination: str | None = Field(default=None, max_length=40)
    depart_date: date | None = None
    return_date: date | None = None
    adults: int | None = Field(default=None, ge=1, le=99)   # `[10-15]` 상품형 단체(한강버스 10~30명) — 항공 · 기차 · 버스는 아래 parse 에서 9 까지
    children: int | None = Field(default=None, ge=0, le=9)
    infants: int | None = Field(default=None, ge=0, le=9)
    cabin: Literal["ECONOMY", "BUSINESS", "FIRST"] | None = None
    depart_date_text: str | None = None
    return_date_text: str | None = None
    direct_only: bool | None = None
    depart_times: list[Band] | None = None
    depart_times_text: str | None = None
    #: `[2026-10-11]` 오는 편 출발 시간대 · 1인 가격 상한(원)과 그 기준 · 반영 못 하는 요청(문장 조각)
    return_times: list[Band] | None = None
    return_times_text: str | None = None
    max_price: int | None = Field(default=None, ge=1)
    max_price_basis: Literal["round_trip", "one_way"] | None = None
    other_requests: list[str] = Field(default_factory=list)
    domestic: bool | None = None
    #: `[2026-10-14]` 기차 · 버스 — 열차 등급 · 버스 등급 바람(그대로 문장 조각), 버스 종류(express · suburbs)
    grade: str | None = Field(default=None, max_length=20)
    kind: Literal["express", "suburbs", "city_tour", "cruise", "rental", "river_bus"] | None = None
    missing: list[str] = Field(default_factory=list)
    question: str | None = None


def parse(raw: Any) -> Interpretation:
    if not isinstance(raw, dict):
        raise InterpretationInvalid(f"not an object: {type(raw).__name__}")
    cleaned = {key: (value.strip() or None) if isinstance(value, str) else value for key, value in raw.items()}
    for key in ("depart_times", "return_times"):
        if cleaned.get(key) == []:
            cleaned[key] = None
    price = cleaned.get("max_price")
    if isinstance(price, (int, float)) and not isinstance(price, bool) and 0 < price < 1000:
        cleaned["max_price"] = int(price * 10000)      # 「왕복 50 안 넘게」 — 항공 가격의 맨숫자는 만 원 단위
    if not isinstance(cleaned.get("other_requests"), list):
        cleaned["other_requests"] = []
    try:
        found = Interpretation.model_validate(cleaned)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        raise InterpretationInvalid(f"invalid fields: {fields}") from exc
    if found.mode != "tour" and found.adults is not None and found.adults > 9:
        raise InterpretationInvalid("invalid fields: ['adults']")    # 항공 · 기차 · 버스는 전처럼 9명까지(예약 한 번의 한도)
    if found.mode == "flight":
        bad = [name for name in ("origin", "destination") if getattr(found, name) and not re.fullmatch(_CODE, getattr(found, name))]
        if bad:
            raise InterpretationInvalid(f"invalid fields: {bad}")       # 항공은 공항 코드(영문 대문자 3글자)만
        if found.task == "search" and found.origin and found.destination and found.domestic is None:
            # ★공항 둘을 알면서 국내 · 국제를 안 정했다 — 어느 도구를 부를지 서버가 추측하지 않는다
            raise InterpretationInvalid("invalid fields: ['domestic']")
    elif found.domestic is None:
        found = found.model_copy(update={"domestic": True})            # 기차 · 버스는 국내뿐
    return found


def needs(found: Interpretation, *, today: date) -> list[str]:
    """이 해석으로 검색하려면 아직 받아야 하는 값의 이름들(순서 고정). 귀국일이 틀렸으면 `return_date` 를 더한다."""
    if found.task != "search":
        return []
    values = found.model_dump()
    if found.depart_date is not None and found.depart_date < today:
        values["depart_date"] = None
    if found.origin and found.destination and found.origin == found.destination:
        values["destination"] = None
    required = ("depart_date",) if found.mode == "tour" else REQUIRED   # `[10-15]` 상품형은 출발 · 도착이 없고 인원은 링크한 화면에서
    missing = [name for name in required if values.get(name) in (None, "")]
    if found.return_date is not None and values["depart_date"] is not None and found.return_date < found.depart_date:
        missing.append("return_date")
    return missing


def ask(found: Interpretation, missing: list[str]) -> str:
    """되묻는 문장. 모델이 센 빠진 값이 서버가 센 것과 **같을 때만** 모델 문장을 쓰고, 다르면 빠진 값의 이름을 나열한다.

    값은 읽었는데 서버가 쓸 수 없다고 본 것(지난 날짜 · 같은 공항 · 뒤집힌 날짜)은 **읽은 값을 같이 보여 준다** —
    ☆2026-10-08 14:58 playdata, 「11월 6일」을 모델이 2023-11-06 으로 옮겼고 서버가 지난 날짜로 거른 뒤 「출발 날짜를 알려 달라」고만 물었다.
      고객은 날짜를 말했는데 다시 묻는 꼴이었다.
    """
    values = found.model_dump(mode="json")
    rejected = [f"{LABELS[name]} {values[name]}" for name in missing if values.get(name) not in (None, "")]
    if rejected:
        return (f"{' · '.join(rejected)}(으)로 읽었는데 지난 날짜이거나 앞뒤가 맞지 않습니다. "
                f"{' · '.join(LABELS[name] for name in missing)}을(를) 다시 알려 주세요.")
    if found.question and set(found.missing) == set(missing):
        return found.question
    return f"{' · '.join(LABELS[name] for name in missing)}을(를) 알려 주시면 이어서 찾아 드리겠습니다."

#: 날짜 값 → 그 값의 근거로 모델이 옮겨 적은 문장 조각 칸
GROUNDS = {"depart_date": "depart_date_text", "return_date": "return_date_text", "depart_times": "depart_times_text",
           "return_times": "return_times_text"}


def ground(found: Interpretation, text: str, *, has_trip: bool,
           trip: dict[str, Any] | None = None) -> tuple[Interpretation, list[str]]:
    """날짜마다 모델이 적은 근거 조각이 **고객 문장에 실제로 있는지** 본다. 없으면 그 날짜를 비운다(없는 값으로 다시 묻는다).

    ☆2026-10-08 15:26 playdata — 「서울에서 3박 할 숙소 추천해줘」에 모델이 체크인을 오늘(2026-10-08)로 지어냈다.
      프롬프트에 「지어내지 말라」가 있었는데도 그랬다. 그래서 값 대신 **근거를 받아 서버가 문장과 맞춰 본다** —
      뜻을 서버가 해석하는 것이 아니라, 모델이 인용한 글자가 문장에 있는지만 본다(공백 무시).
    `[2026-10-10]` 확인 규칙은 `_shared/interpret_dates.ground_dates` 로 옮겼다(숙소 · 항공 공용). 모델의 `missing` 은 더 보지 않는다 —
      맞는 날짜를 missing 에 적어 지워지던 것이 17:55 · 17:58 측정의 숙소 오답 대부분이었다.
    """
    found, cleared = ground_dates(found, text, grounds=GROUNDS, start="depart_date", end="return_date", has_trip=has_trip, trip=trip)
    return found.model_copy(update={"other_requests": keep_quoted(found.other_requests, text)}), cleared


#: 국내 출발 공항 → 문장에 있어야 하는 이름(하나라도). 서울은 인천 · 김포 둘 다로 본다
_KOREAN_AIRPORTS = {
    "ICN": ("인천", "서울", "ICN", "Incheon", "Seoul"), "GMP": ("김포", "서울", "GMP", "Gimpo", "Seoul"),
    "PUS": ("부산", "김해", "PUS", "Busan", "Gimhae"), "CJU": ("제주", "CJU", "Jeju"), "CJJ": ("청주", "CJJ", "Cheongju"),
    "TAE": ("대구", "TAE", "Daegu"), "KWJ": ("광주", "KWJ", "Gwangju"), "MWX": ("무안", "MWX", "Muan"),
    "RSU": ("여수", "RSU", "Yeosu"), "USN": ("울산", "USN", "Ulsan"), "YNY": ("양양", "YNY", "Yangyang"),
    "KPO": ("포항", "KPO", "Pohang"), "HIN": ("사천", "진주", "HIN"), "KUV": ("군산", "KUV"), "WJU": ("원주", "WJU"),
}
#: 시간대 이름 → 근거에서 그 시간대를 가리키는 말
_BAND_WORDS = {"dawn": ("새벽", "첫 비행기", "첫비행기"), "morning": ("오전", "아침"), "afternoon": ("오후", "점심", "낮"),
               "evening": ("저녁", "밤", "퇴근")}
_NEGATION = ("싫", "빼", "말고", "제외", "아니", "피해")
#: 머무는 박 수 — 「하룻밤만 자고 올거야」 「2박 하고」. 돌아오는 날 = 가는 날 + 박 수
_STAY = re.compile(r"(\d+)\s*박|하룻\s*밤|하루\s*(?:밤|자고)|이틀\s*밤|이틀\s*자고")


def settle_years(found: Interpretation, *, today: date, text: str = "") -> tuple[Interpretation, list[str]]:
    """날 · 연도를 근거 조각과 맞추고(`_shared/interpret_dates.settle_dates`), 가는 편 시간대를 근거와 맞춘다. (해석, 고친 칸)

    ☆2026-10-11 16:21 playdata 「가는날은 새벽에 출발해도 괜찮으니까 … 오는날에는 … 오후 출발」 — 근거는 가는 날 부분만 적고 값은
      [dawn, afternoon](오는 편 오후가 섞임)으로 냈다. 「새벽도 괜찮다」는 제한이 아니다.
    - 근거에 「~도 괜찮 · 상관없」이 있고 빼는 말이 없으면 시간 제한이 아니다 → 비운다.
    - 빼는 말이 없고 근거에 시간대 말이 있으면, 근거에 나온 시간대만 남긴다(근거에 없는 시간대는 다른 구절에서 섞인 것).
    - 출발지: 국내 공항인데 문장에 그 이름이 없으면 비운다(되묻는다 — 사용자 결정 2026-10-11).
    - 돌아오는 날이 비었고 문장에 머무는 박 수(「하룻밤만 자고」 「2박」)가 하나만 있으면 가는 날 + 박 수로 채운다. 「편도」가 있으면 안 채운다.
      ☆2026-10-11 16:50 playdata 사용자 문장 U-F02 「하룻밤만 자고 올거야 … 내일 갔으면」 — 돌아오는 날을 비워 편도로 찾았다.
    """
    found, fixed = settle_dates(found, grounds=GROUNDS, today=today, text=text)
    if text and found.origin in _KOREAN_AIRPORTS:
        squeezed = "".join(text.split()).casefold()
        if not any(name.casefold() in squeezed for name in _KOREAN_AIRPORTS[found.origin]):
            # `[2026-10-11 사용자 결정]` 출발지를 말하지 않았으면 되묻는다 — ☆16:13 · 16:21 「일본」 「홍콩」 「영국」 문장에 출발지가 없는데
            #   모델이 인천(ICN)을 채웠다. 국내 공항만 이름으로 확인한다(해외 출발 공항은 이름 표가 없어 그대로 둔다)
            found, fixed = found.model_copy(update={"origin": None}), [*fixed, "origin"]
    stays = _STAY.findall(text) if text else []
    if found.return_date is None and found.depart_date is not None and len(stays) == 1 and "편도" not in text:
        match = _STAY.search(text)
        nights = int(match.group(1)) if match.group(1) else (2 if "이틀" in match.group(0) else 1)
        found = found.model_copy(update={"return_date": found.depart_date + timedelta(days=nights), "return_date_text": match.group(0)})
        fixed = [*fixed, "return_date"]
    for field in ("depart_times", "return_times"):
        quote, times = getattr(found, field + "_text") or "", getattr(found, field)
        if not times or not quote or any(word in quote for word in _NEGATION):
            continue
        if "도 괜찮" in quote or "도괜찮" in quote or "상관없" in quote:
            found, fixed = found.model_copy(update={field: None}), [*fixed, field]
            continue
        said = [band for band in times if any(word in quote for word in _BAND_WORDS[band])]
        if said and said != times:
            found, fixed = found.model_copy(update={field: said}), [*fixed, field]
    if found.return_times and found.return_date is None:
        found, fixed = found.model_copy(update={"return_times": None}), [*fixed, "return_times"]   # 편도에 오는 편 시간대는 쓸 데가 없다
    return found, fixed


__all__ = ["CALENDAR_DAYS", "model_context", "settle_years", "TIME_BANDS", "GROUNDS", "Interpretation", "InterpretationInvalid", "LABELS", "REQUIRED", "ask", "ground", "needs", "parse"]
