# -*- coding: utf-8 -*-
"""장소·일정 정보 중 일부가 비어 있어도 Team이 안전하게 답하는지 검증한다.

Activity 가 장소·일정 JSON의 **필수값이 빠졌을 때** 어떻게 반응하는지.

★★**"없다"와 "모른다"를 같게 다루지 않는다.** `read.booking`·`read.place`는
  스키마를 강제하지 않는다 — DB 조회가 그냥 `None`을 주거나, 일부 필드가
  빠진 dict를 줄 수 있다. 이 파일은 그럴 때 Team이:
    - 판정에 **꼭 필요한** 값(예약 자체)이 없으면 **확정 답을 만들지 않고
      escalate** 하는지. 시각·규정은 capability 마다 다르다 — 성립 판정(`check_feasible`)은
      시각이 없으면 「정보 부족」, 규정은 아예 안 쓴다. 취소·변경은 둘 다 필요해서 그대로 escalate
    - **없어도 성립 판정 자체는 막지 않는** 값(장소 상세, 인원 정보)이
      없으면 "모름"을 달고도 계속 진행하는지
  를 검증한다. 코드: `app/modules/travel_ops/activity.py` `execute()`·
  `_check_feasible()`.
"""
from __future__ import annotations

import pytest

from app.modules.travel_ops.activity import ActivityTeam

from .helpers import FakeTools, in_hours, pack, task

ALLOWED = ActivityTeam.manifest.allowed_tools


def _task(capability: str = "activity.check_feasible"):
    context = pack("activity", scope=["activity"])
    return task("activity", capability, context, ALLOWED)


def _show(label: str, result) -> None:
    """★`pytest -s`로 실행해야 보인다."""
    print(f"\n--- {label} ---")
    print("outcome      :", result.outcome)
    print("failure_code :", result.failure_code)
    print("answer       :", result.answer)
    print("warnings     :", result.warnings)
    print("decisions    :", result.decisions)


#: 완전한 일정(booking) JSON — 여기서 하나씩 필드를 빼거나 비운다. (party_size: 신청인원, capacity: 정원)
FULL_BOOKING = {"booking_id": "b1", "place_id": "p1", "starts_at": in_hours(30),
                "party_size": 2, "capacity": 4}
#: 완전한 장소(place) JSON.
FULL_PLACE = {"place_id": "p1", "name": "경복궁", "weather_sensitive": False,
              "latitude": 37.5, "longitude": 127.0}
FULL_POLICY = [{"cancel_deadline_hours": 24}]


def _values(*, booking=FULL_BOOKING, policy=FULL_POLICY, place=FULL_PLACE):
    return {"read.booking": booking, "read.policy": policy,
            "read.place": place, "read.weather": None}


# ── 필수값 — 없으면 확정 답을 안 만든다 ──────────────────────────

@pytest.mark.asyncio
async def test_missing_booking_json_escalates_instead_of_guessing():
    """일정 JSON 자체가 없다(`read.booking` → `None`)."""
    result = await ActivityTeam(FakeTools(_values(booking=None))).execute(_task())
    _show("booking 없음", result)

    assert result.outcome == "escalated"
    assert result.failure_code == "unknown_예약 내역"


def _assert_time_unknown(result):
    """성립 판정은 시각을 모르면 사람에게 넘기지 않고 「정보 부족」으로 답한다(2026-10-02)."""
    assert result.outcome == "completed"
    decision = result.decisions[0]
    assert decision["status"] == "insufficient_info" and decision["feasible"] is False
    assert decision["reason"] == "time_unknown" and decision["failure_code"] == "time_unknown"
    assert "alternatives" not in decision               # ★정보 부족이면 대체 장소를 찾지 않는다
    assert "성립합니다" not in result.answer


@pytest.mark.asyncio
async def test_booking_json_missing_starts_at_is_insufficient_info():
    """일정 JSON은 있지만 **시각이 빠졌다** — 언제인지 모르면 성립을 단정하지 않는다."""
    broken = {k: v for k, v in FULL_BOOKING.items() if k != "starts_at"}
    result = await ActivityTeam(FakeTools(_values(booking=broken))).execute(_task())
    _show("starts_at 누락", result)

    _assert_time_unknown(result)


@pytest.mark.asyncio
async def test_booking_json_with_unparseable_starts_at_is_insufficient_info():
    """시각 필드는 있는데 **타입이 틀렸다**(문자열 "모름" 등) — `datetime`이 아니면 모름."""
    broken = {**FULL_BOOKING, "starts_at": "모름"}
    result = await ActivityTeam(FakeTools(_values(booking=broken))).execute(_task())
    _show("starts_at 타입 오류", result)

    _assert_time_unknown(result)


@pytest.mark.asyncio
async def test_unknown_time_does_not_hide_a_party_over_capacity():
    """시각을 몰라도 정원 초과는 시각 없이 확인된 불가다 — 「정보 부족」으로 덮지 않는다."""
    broken = {k: v for k, v in FULL_BOOKING.items() if k != "starts_at"}
    broken.update(party_size=9, capacity=4)
    result = await ActivityTeam(FakeTools(_values(booking=broken))).execute(_task())

    assert result.decisions[0]["status"] == "problem"
    assert result.decisions[0]["reason"] == "party_over_capacity"


@pytest.mark.asyncio
async def test_unknown_time_does_not_read_the_place():
    """시각 없이는 운영시간·재난·기상을 잴 수 없다 — 장소를 읽지 않는다."""
    broken = {k: v for k, v in FULL_BOOKING.items() if k != "starts_at"}
    tools = FakeTools(_values(booking=broken))
    await ActivityTeam(tools).execute(_task())

    assert "read.place" not in [name for name, _ in tools.calls]


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["activity.check_cancelable", "activity.propose_change"])
async def test_other_capabilities_still_escalate_without_a_time(capability):
    """★바뀐 것은 성립 판정뿐이다. 취소·변경은 시각이 있어야 계산되므로 그대로 멈춘다."""
    broken = {k: v for k, v in FULL_BOOKING.items() if k != "starts_at"}
    result = await ActivityTeam(FakeTools(_values(booking=broken))).execute(_task(capability))

    assert result.outcome == "escalated" and result.failure_code == "unknown_예약 시각"


@pytest.mark.asyncio
async def test_missing_policy_does_not_block_the_feasibility_check():
    """규정 JSON이 없다(`read.policy` → `[]`) — 성립 판정은 규정을 쓰지 않으므로 계속 답한다."""
    result = await ActivityTeam(FakeTools(_values(policy=[]))).execute(_task())
    _show("policy 없음", result)

    assert result.outcome == "completed"
    assert result.decisions[0]["status"] == "ok" and result.decisions[0]["feasible"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["activity.check_cancelable", "activity.propose_change"])
async def test_missing_policy_still_escalates_the_other_capabilities(capability):
    """규정이 필요한 취소·변경은 규정 없이 판정을 만들지 않는다."""
    result = await ActivityTeam(FakeTools(_values(policy=[]))).execute(_task(capability))

    assert result.outcome == "escalated" and result.failure_code == "unknown_취소·환급 규정"


# ── 있으면 좋지만 없어도 판정은 계속되는 값 ──────────────────────

@pytest.mark.asyncio
async def test_missing_place_json_returns_feasible_false():
    """장소 JSON이 없다(`read.place` → `None`) — **escalate 하지 않지만 `feasible: False`**.

    결함 1 수정(2026-09-21): 고치기 전엔 「성립합니다 … 판정하지 않았습니다」라는
    앞뒤 모순된 답을 만들었다. 장소 운영 여부를 알 수 없으면 성립을 단정할 수 없다.
    """
    result = await ActivityTeam(FakeTools(_values(place=None))).execute(_task())
    _show("place 없음", result)

    assert result.outcome == "completed"          # ★escalate 하지 않는다 = 사람한테 안 넘기고 Team이 계속 답한다
    assert result.decisions[0]["feasible"] is False   # ★결함 1 수정 — 성립을 단정하지 않는다
    assert result.decisions[0]["place_confirmed"] is False
    assert "장소·운영 정보를 확인하지 못했다" in result.warnings
    assert "판정하지 않았습니다" in result.answer


@pytest.mark.asyncio
async def test_empty_place_json_counts_as_confirmed_but_carries_nothing():
    """★경계 케이스 — `place`가 **빈 dict `{}`**면 `None`과 다르게 취급된다.

    `_check_feasible`이 `place is not None`만 보고 `place_confirmed`를
    정하기 때문에, "필드가 하나도 없는 장소 정보"도 "확인됨"으로 찍힌다.
    이 테스트는 옳고 그름을 판정하지 않는다 — **지금 실제로 이렇게 동작한다**
    는 사실을 고정해 둔다. 스키마 검증을 더 엄격히 할지는 별도 결정이다.
    """
    result = await ActivityTeam(FakeTools(_values(place={}))).execute(_task())
    _show("place가 빈 dict", result)

    assert result.outcome == "completed"
    assert result.decisions[0]["place_confirmed"] is True
    assert "operating" not in result.decisions[0]   # 운영 원문은 당연히 없다


@pytest.mark.asyncio
async def test_missing_party_and_capacity_skips_the_capacity_check_safely():
    """인원·정원 필드가 둘 다 없어도 죽지 않고, 정원 초과 판정을 그냥 건너뛴다."""
    booking = {k: v for k, v in FULL_BOOKING.items()
               if k not in ("party_size", "capacity")}
    result = await ActivityTeam(FakeTools(_values(booking=booking))).execute(_task())
    _show("party_size·capacity 누락", result)

    assert result.outcome == "completed"
    assert result.decisions[0]["feasible"] is True
    assert "reason" not in result.decisions[0]      # party_over_capacity 갈래를 안 탔다


@pytest.mark.asyncio
async def test_booking_without_place_id_asks_read_place_for_none():
    """★`place_id`가 빠진 일정도 `read.place`는 **인자 그대로** 부른다(`None`).

    `place_id=None`을 조용히 다른 값으로 대체하지 않는다는 것을, 실제로
    무슨 인자로 도구를 불렀는지 확인해서 고정한다.
    """
    booking = {k: v for k, v in FULL_BOOKING.items() if k != "place_id"}
    tools = FakeTools(_values(booking=booking, place=None))
    result = await ActivityTeam(tools).execute(_task())
    _show("place_id 누락", result)

    arguments = dict(tools.calls)["read.place"]
    assert arguments["place_id"] is None
    assert result.outcome == "completed"
    assert result.decisions[0]["place_confirmed"] is False


# ── 결함 1: 장소 정보 없어도 성립으로 답하던 버그 ────────────────────────

@pytest.mark.asyncio
async def test_missing_place_answer_does_not_say_feasible():
    """answer가 '성립합니다'로 시작하지 않는다 — feasible:False인데 성립이라 하면 모순."""
    result = await ActivityTeam(FakeTools(_values(place=None))).execute(_task())

    assert "성립합니다" not in result.answer


@pytest.mark.asyncio
async def test_missing_place_answer_contains_판정_not_possible():
    """answer에 '판정하지 않았습니다' 또는 그에 준하는 문구가 있어야 한다."""
    result = await ActivityTeam(FakeTools(_values(place=None))).execute(_task())

    assert "판정하지 않았습니다" in result.answer


@pytest.mark.asyncio
async def test_present_place_is_still_feasible_with_good_data():
    """★회귀 가드 — 장소가 있는 정상 경우는 여전히 feasible:True다.

    `place is not None and ...` 조건이 정상 케이스를 잘못 막아선 안 된다.
    """
    result = await ActivityTeam(FakeTools(_values())).execute(_task())

    assert result.decisions[0]["feasible"] is True
    assert result.decisions[0]["place_confirmed"] is True
    assert "성립합니다" in result.answer


# ── 결함 2: 시작 시각이 지난 예약도 성립으로 답하던 버그 ──────────────────

@pytest.mark.asyncio
async def test_past_booking_returns_feasible_false():
    """이미 시작된 예약(`starts_at`이 2시간 전) — `feasible: False`로 답해야 한다.

    결함: 고치기 전엔 「성립합니다(-2.0시간)」라고 틀린 답을 만들었다.
    수정: `_check_feasible()`에서 `remaining < 0`이면 즉시 already_started로 반환.
    """
    booking = {**FULL_BOOKING, "starts_at": in_hours(-2)}
    result = await ActivityTeam(FakeTools(_values(booking=booking))).execute(_task())
    _show("starts_at 2시간 전", result)

    assert result.outcome == "completed"
    assert result.decisions[0]["feasible"] is False
    assert result.decisions[0]["reason"] == "already_started"


@pytest.mark.asyncio
async def test_past_booking_hours_elapsed_is_positive_and_approximate():
    """`hours_elapsed`는 **양수** — 경과 시간이다(-remaining)."""
    booking = {**FULL_BOOKING, "starts_at": in_hours(-2)}
    result = await ActivityTeam(FakeTools(_values(booking=booking))).execute(_task())

    elapsed = result.decisions[0]["hours_elapsed"]
    assert elapsed > 0, "경과 시간은 양수여야 한다"
    assert abs(elapsed - 2.0) < 0.1, f"약 2시간이어야 하는데 {elapsed}가 나왔다"


@pytest.mark.asyncio
async def test_past_booking_answer_mentions_elapsed():
    """answer 문구에 '이미 시작됐거나 종료된' 문구가 들어 있어야 한다."""
    booking = {**FULL_BOOKING, "starts_at": in_hours(-2)}
    result = await ActivityTeam(FakeTools(_values(booking=booking))).execute(_task())

    assert "이미 시작됐거나 종료된" in result.answer


@pytest.mark.asyncio
async def test_future_booking_is_still_feasible():
    """★회귀 가드 — 미래 예약(`starts_at`이 30시간 뒤)은 feasible이 True다.

    `remaining < 0` 분기가 미래 예약까지 잡아선 안 된다.
    """
    result = await ActivityTeam(FakeTools(_values())).execute(_task())
    _show("starts_at 30시간 후 (회귀 가드)", result)

    assert result.outcome == "completed"
    assert result.decisions[0]["feasible"] is True


@pytest.mark.asyncio
async def test_past_booking_does_not_call_read_place():
    """시작 전 검사에서 immediately 반환하므로 `read.place`를 부르지 않는다.

    already_started 경로에서 장소 조회를 낭비하지 않는다는 것을 고정한다.
    """
    booking = {**FULL_BOOKING, "starts_at": in_hours(-2)}
    tools = FakeTools(_values(booking=booking))
    await ActivityTeam(tools).execute(_task())

    called_names = [name for name, _ in tools.calls]
    assert "read.place" not in called_names
