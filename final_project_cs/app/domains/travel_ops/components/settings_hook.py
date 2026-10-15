# -*- coding: utf-8 -*-
"""운영자가 화면에서 바꾼 값을 읽는 **끼움 자리**. `[2026-10-06]` D-CS-013

★왜 있나. 전에는 대화 부품이 웹 남용 방어 모듈의 설정 표를 직접 읽었다(`web_guard.values`).
  채팅 동작 하나가 「끌 수 있는 기능」의 저장소 모양에 묶여 있었던 셈이다.

지금은 이렇게 돈다.

    부품            "이 값 운영자가 바꿨나?"  →  여기(`operator_setting`)
    설정 화면 기능  "내가 그 표를 들고 있다"  →  조립 때 `register()`

  아무도 꽂지 않으면 `None` 을 돌려준다 — 부르는 쪽이 가드레일 기본값을 쓴다.
  **값을 지어내지 않는다.** 표를 못 읽는 상황과 「조정값이 없다」를 같은 `None` 으로 돌려주는 것은
  의도한 것이다. 둘 다 「기본값으로 간다」가 정답이고, 그 사실은 부르는 쪽 주석이 적는다.
"""
from __future__ import annotations

from typing import Any, Callable

#: (tenant_id, 이름) -> 값 또는 None
Reader = Callable[[str, str], Any]

_reader: Reader | None = None


def register(reader: Reader) -> None:
    """조립 때 한 번. 다시 부르면 교체한다(재조립 대비)."""
    global _reader
    _reader = reader


def clear() -> None:
    global _reader
    _reader = None


def is_registered() -> bool:
    return _reader is not None


def operator_setting(tenant_id: str, name: str) -> Any:
    if _reader is None:
        return None
    try:
        return _reader(tenant_id, name)
    except Exception:                                   # noqa: BLE001 — 표를 못 읽으면 기본값으로 간다
        return None


GOOGLE_PRICE = "travel.dining.google_price_enabled"


def google_price_enabled(tenant_id: str) -> bool:
    """식당 대체 후보의 구글 가격 조회를 켰나. `[2026-10-13 사용자 지시]` 유료 결제 뒤 관리 콘솔에서 켠다.

    운영자가 화면에서 바꾼 값이 먼저, 없거나 못 읽으면 가드레일(`app.core.settings.google_price_enabled`, 기본 꺼짐).
    ★참/거짓이 아닌 값은 바꾼 값으로 보지 않는다 — 켜는 것은 명시한 경우뿐이다.
    """
    value = operator_setting(tenant_id, GOOGLE_PRICE)
    if isinstance(value, bool):
        return value
    from app.core.settings import google_price_enabled as guardrail_default

    return guardrail_default()


TAXI_CALL_PRODUCT = "travel.mobility.taxi_call_product_enabled"


def taxi_call_product_enabled(tenant_id: str) -> bool:
    """확인 화면에서 택시 호출 상품을 고르게 하나. `[2026-10-14 사용자 지시 · 핫픽스]` 택시 요금 계산 확인 뒤 관리 콘솔에서 켠다.

    운영자가 화면에서 바꾼 값이 먼저, 없거나 못 읽으면 가드레일(`app.core.settings.taxi_call_product_enabled`, 기본 꺼짐).
    ★참/거짓이 아닌 값은 바꾼 값으로 보지 않는다 — 켜는 것은 명시한 경우뿐이다.
    """
    value = operator_setting(tenant_id, TAXI_CALL_PRODUCT)
    if isinstance(value, bool):
        return value
    from app.core.settings import taxi_call_product_enabled as guardrail_default

    return guardrail_default()


TRIP_NOW_LABEL = "travel.trip_screen.now_label_enabled"


def trip_now_label_enabled(tenant_id: str) -> bool:
    """여행 화면에 「지금 HH:MM」 시각 꼬리표를 보이나. `[2026-10-15 사용자 결정 — ST4F-494]` 시험 기간에 켜 두고 배포 때 관리 콘솔에서 끈다.

    운영자가 화면에서 바꾼 값이 먼저, 없거나 못 읽으면 가드레일(`app.core.settings.trip_now_label_enabled`, 기본 켜짐).
    ★참/거짓이 아닌 값은 바꾼 값으로 보지 않는다. 읽는 곳은 여행 조회 응답의 `now_label`(`trip_api._trip_view`) 하나다.
    """
    value = operator_setting(tenant_id, TRIP_NOW_LABEL)
    if isinstance(value, bool):
        return value
    from app.core.settings import trip_now_label_enabled as guardrail_default

    return guardrail_default()


ACTIVITY_LIVE_CHECK = "travel.activity_judge.live_status_batch_enabled"


def activity_live_check_enabled(tenant_id: str) -> bool:
    """활동 장소 임시휴무 · 폐업 새벽 웹 확인을 돌리나. `[2026-10-14 사용자 지시]` 유료 웹 검색이라 관리 콘솔에서 켤 때만.

    운영자가 화면에서 바꾼 값이 먼저, 없거나 못 읽으면 가드레일(`app.core.settings.activity_live_check_enabled`, 기본 꺼짐).
    ★참/거짓이 아닌 값은 바꾼 값으로 보지 않는다 — 켜는 것은 명시한 경우뿐이다.
    """
    value = operator_setting(tenant_id, ACTIVITY_LIVE_CHECK)
    if isinstance(value, bool):
        return value
    from app.core.settings import activity_live_check_enabled as guardrail_default

    return guardrail_default()
