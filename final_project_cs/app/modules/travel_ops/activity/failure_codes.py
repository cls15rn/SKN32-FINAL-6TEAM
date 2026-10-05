# -*- coding: utf-8 -*-
"""Activity Team 실패·예외 코드 — 한 곳에 모은다.

★**무엇이 이미 있고 무엇이 새것인가.**
  - 사람에게 넘기는(escalate) 실패는 코어가 이미 `failure_code` 와 함께 이벤트로 남긴다
    (`app/application/controller.py` 의 `GUARDRAIL_ESCALATED`). 분류 실패도 코어가 남긴다
    (`classification.py`). 여기서 다시 만들지 않는다.
  - 코어가 못 보는 것 — 사람에게 넘기지 않고 **정상 응답으로 끝나는 실패**(휴무·재난·장소 모름·후보 조회 실패)와
    **도구(API·DB) 예외** — 에만 이 코드를 쓴다. 결과의 `decisions[].failure_code` 와 로그(`acop.activity.failure`)에 같은 값이 나간다.

★코드는 소문자 스네이크다. 새 코드를 더하면 `DESCRIPTIONS` 에도 한 줄 적는다(시험이 둘이 맞는지 본다).
"""
from __future__ import annotations

from typing import Final

#: 로그 이름. 어디에 쓸지(파일·수집기)는 운영의 logging 설정이 정한다 — 경로를 코드에 박지 않는다.
LOGGER_NAME: Final = "acop.activity.failure"

CLOSED_WEEKDAY: Final = "closed_weekday"
DISASTER_BLOCKS: Final = "disaster_blocks"
ALREADY_STARTED: Final = "already_started"
PARTY_OVER_CAPACITY: Final = "party_over_capacity"
PLACE_UNKNOWN: Final = "place_unknown"
TIME_UNKNOWN: Final = "time_unknown"
PLACE_NOT_FOUND: Final = "place_not_found"
PLACE_AMBIGUOUS: Final = "place_ambiguous"
PLACE_EXISTS_UNREGISTERED: Final = "place_exists_unregistered"
PLACE_LOOKUP_BLOCKED: Final = "place_lookup_blocked"
ALTERNATIVES_WITHHELD: Final = "alternatives_withheld"
ALTERNATIVES_NO_CONTENT_ID: Final = "alternatives_no_content_id"
ALTERNATIVES_NO_COORDINATES: Final = "alternatives_no_coordinates"
ALTERNATIVES_POOL_UNAVAILABLE: Final = "alternatives_pool_unavailable"
ALTERNATIVES_NONE: Final = "alternatives_none"
ALTERNATIVES_UNCONFIRMED: Final = "alternatives_unconfirmed"
TOOL_ERROR: Final = "tool_error"

DESCRIPTIONS: Final[dict[str, str]] = {
    CLOSED_WEEKDAY: "요청 요일이 정기휴무 요일이다(판정: 불가)",
    DISASTER_BLOCKS: "위급재난이 확인돼 성립하지 않는다(판정: 불가)",
    ALREADY_STARTED: "이미 시작됐거나 끝난 활동이다",
    PARTY_OVER_CAPACITY: "신청 인원이 정원을 넘는다",
    PLACE_UNKNOWN: "예약의 장소·운영 정보를 읽지 못했다(판정: 정보 부족)",
    TIME_UNKNOWN: "예약 시각을 읽지 못해 성립 여부를 판정하지 않았다(판정: 정보 부족)",
    PLACE_NOT_FOUND: "고객이 말한 장소 이름을 카탈로그·TourAPI 에서 하나로 특정하지 못했다(고객에게 되묻는다)",
    PLACE_AMBIGUOUS: "고객이 말한 장소 이름이 카탈로그의 둘 이상을 가리킨다(고객에게 고르게 한다)",
    PLACE_EXISTS_UNREGISTERED: "장소는 실재하지만 우리 카탈로그에 없다(저장하지 않고 고객에게 되묻는다)",
    PLACE_LOOKUP_BLOCKED: "장소 조회가 막혀 있는지 없는지 확인하지 못했다(없음이 아니다 — 사람에게 넘긴다)",
    ALTERNATIVES_WITHHELD: "위급재난이라 대체 장소를 안내하지 않았다",
    ALTERNATIVES_NO_CONTENT_ID: "원래 장소의 식별자가 없어 대체 후보를 찾지 못했다",
    ALTERNATIVES_NO_COORDINATES: "원래 장소의 좌표가 없어 근처 대체 후보를 잴 수 없었다",
    ALTERNATIVES_POOL_UNAVAILABLE: "대체 후보 풀을 읽지 못했다(원래 장소가 카탈로그에 없거나 조회 실패)",
    ALTERNATIVES_NONE: "최대 반경(10km) 안에 조건에 맞는 대체 후보가 없다",
    ALTERNATIVES_UNCONFIRMED: "대체 후보는 있으나 운영 여부를 확인한 곳이 없어 안내하지 않았다",
    TOOL_ERROR: "읽기 도구(API·DB)가 예외를 냈다 — 삼키지 않고 다시 던졌다",
}
