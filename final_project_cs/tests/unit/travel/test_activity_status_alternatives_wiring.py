# -*- coding: utf-8 -*-
"""4단계 대체 장소를 `check_feasible`에 연결한 경로 — `read.place_candidates` 계약.

wiki/teams/activity.md 「구현 현황 — 작업자 B」 「아직 안 한 것」:
  - Team(`execute()`)에 연결 — 후보 풀 조회 도구는 계약(`read.place_candidates`)만
    먼저 두고, 실제 DB 조회는 작업자 A 몫이다. 여기서는 FakeTools 로 넣는다.
  - 고른 후보의 ① 재검증(v11 §5) — 도구를 더 부르지 않고 이미 읽은 값으로 한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.travel_ops.activity import ActivityTeam

from .helpers import FakeTools, pack, task

UTC = timezone.utc
ALLOWED = ActivityTeam.manifest.allowed_tools


def _upcoming(when: datetime) -> datetime:
    """요일·시각은 그대로 두고 주 단위로 미래로 민다(취소 기한과 안 겹치게 이틀 뒤부터)."""
    floor = datetime.now(UTC) + timedelta(days=2)
    while when < floor:
        when += timedelta(weeks=1)
    return when


TUESDAY = _upcoming(datetime(2026, 9, 22, 6, tzinfo=UTC))    # 화요일 15시 KST
SATURDAY = _upcoming(datetime(2026, 10, 3, 6, tzinfo=UTC))


def _row(cid, *, title, x, y, closed="연중무휴", l1="HS", sgg="23", hours="09:00~18:00"):
    return {"contentid": cid, "title": title, "contenttypeid": "12",
            "lclsSystm1": l1, "lclsSystm2": "HS01", "lclsSystm3": "HS010100",
            "sigungucode": sgg, "mapx": str(x), "mapy": str(y),
            "closed_days": closed, "business_hours": hours}


ORIGIN = _row("126508", title="경복궁", x=126.9770, y=37.5796, closed="매주 화요일")
NEAR = _row("c1", title="가까운 곳", x=126.9800, y=37.5800)
FAR = _row("c2", title="먼 곳", x=127.0500, y=37.6000)
CLOSED_TUE = _row("c3", title="화요일 휴무", x=126.9771, y=37.5797, closed="매주 화요일")
UNKNOWN = _row("c4", title="휴무 모름", x=126.9772, y=37.5797, closed="홈페이지 참조", hours="점포별 상이")


def _pool(*candidates, origin=ORIGIN):
    return {"origin": origin, "candidates": list(candidates),
            "source": "place_catalog", "confirmed_at": "2026-09-26T00:00:00+00:00"}


def _values(*, starts_at=TUESDAY, pool=None, disaster=None, content_id="126508",
            weather_sensitive=False):
    place = {"place_id": "p1", "name": "경복궁", "weather_sensitive": weather_sensitive,
             "latitude": 37.5796, "longitude": 126.9770,
             "source_content_id": content_id, "source_content_type_id": "12",
             "operating": {"usetime_text": "09:00~18:00", "restdate_text": "매주 화요일 휴무",
                           "source": "tour_api", "confirmed_at": "2026-09-20T12:00:00+00:00"}}
    return {
        "read.booking": {"booking_id": "b1", "place_id": "p1", "starts_at": starts_at,
                         "party_size": 2, "capacity": 4},
        "read.policy": [{"cancel_deadline_hours": 24}],
        "read.place": place,
        "read.weather": {"matched_hour": "15:00", "precipitation_probability": 10,
                         "wind_speed_kmh": 5, "source": "kma", "confirmed_at": "x"},
        "read.disaster": disaster,
        "read.place_candidates": pool,
    }


def _task(state=None):
    return task("activity", "activity.check_feasible",
                pack("activity", scope=["activity"], state=state), ALLOWED)


async def _run(values, state=None):
    tools = FakeTools(values)
    result = await ActivityTeam(tools).execute(_task(state))
    return result, [name for name, _ in tools.calls]


# ══════════════════════════════════════════════════════════════════
# 계약
# ══════════════════════════════════════════════════════════════════

def test_the_tool_is_declared():
    """★Contract-first — 도구는 선언돼 있다(실구현은 `db_search/place_candidates.py`)."""
    assert "read.place_candidates" in ALLOWED


# ══════════════════════════════════════════════════════════════════
# 문제있음일 때만 찾는다
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_closure_problem_ranks_alternatives_and_lists_them():
    result, calls = await _run(_values(pool=_pool(FAR, NEAR)))

    decision = result.decisions[0]
    assert decision["status"] == "problem"
    assert calls[-1] == "read.place_candidates"
    found = decision["alternatives"]
    assert found["status"] == "ranked"
    assert [a["contentid"] for a in found["alternatives"]] == ["c1", "c2"]
    assert all(a["revalidated"] for a in found["alternatives"])
    assert "대체 장소 후보:\n- 가까운 곳: 0.3km · " in result.answer   # 장소마다 추천 이유 한 줄
    assert "정원은 확인하지 않았습니다" in result.answer
    assert "tool:activity:read.place_candidates" in [e.evidence_id for e in result.evidence]


@pytest.mark.asyncio
async def test_the_pool_lookup_is_keyed_by_the_origin_content_id():
    tools = FakeTools(_values(pool=_pool(NEAR)))
    await ActivityTeam(tools).execute(_task())
    assert ("read.place_candidates", {"content_id": "126508"}) in tools.calls


@pytest.mark.asyncio
async def test_ok_does_not_look_for_alternatives():
    result, calls = await _run(_values(starts_at=SATURDAY, pool=_pool(NEAR)))
    assert result.decisions[0]["status"] == "ok"
    assert "read.place_candidates" not in calls
    assert "alternatives" not in result.decisions[0]


@pytest.mark.asyncio
async def test_insufficient_info_does_not_look_for_alternatives():
    values = _values(pool=_pool(NEAR))
    values["read.place"] = None
    result, calls = await _run(values)
    assert result.decisions[0]["status"] == "insufficient_info"
    assert "read.place_candidates" not in calls


# ══════════════════════════════════════════════════════════════════
# 모름은 모름으로
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_unimplemented_pool_says_it_could_not_look():
    """★지금 실제 도구가 이 갈래다 — 「대안 없음」이 아니라 「조회 못 함」."""
    result, _ = await _run(_values(pool=None))
    found = result.decisions[0]["alternatives"]
    assert found == {"status": "unknown", "reason": "pool_unavailable"}
    assert "조회하지 못했습니다" in result.answer
    assert "대체 장소 후보 풀을 받지 못했다" in result.warnings
    assert result.decisions[0]["feasible"] is False      # 판정은 그대로


@pytest.mark.asyncio
async def test_no_content_id_skips_the_lookup():
    result, calls = await _run(_values(pool=_pool(NEAR), content_id=None))
    assert "read.place_candidates" not in calls
    assert result.decisions[0]["alternatives"]["reason"] == "no_content_id"


@pytest.mark.asyncio
async def test_empty_match_is_not_confused_with_unknown():
    other = _row("c9", title="다른 갈래", x=126.98, y=37.58, l1="NA", sgg="99")
    result, _ = await _run(_values(pool=_pool(other)))
    found = result.decisions[0]["alternatives"]
    assert found["status"] == "ranked" and found["alternatives"] == []
    assert "근처(반경 10km)에 조건에 맞는 장소가 없습니다" in result.answer


# ══════════════════════════════════════════════════════════════════
# ① 재검증 (v11 §5)
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_candidate_closed_on_the_same_weekday_is_dropped():
    result, _ = await _run(_values(pool=_pool(CLOSED_TUE, FAR)))
    ids = [a["contentid"] for a in result.decisions[0]["alternatives"]["alternatives"]]
    assert ids == ["c2"]


@pytest.mark.asyncio
async def test_unconfirmed_candidate_is_kept_but_not_announced():
    """★휴무를 모르는 후보는 decisions 에 남기되 고객 안내문에는 싣지 않는다."""
    result, _ = await _run(_values(pool=_pool(UNKNOWN)))
    [only] = result.decisions[0]["alternatives"]["alternatives"]
    assert only["contentid"] == "c4" and only["revalidated"] is False
    assert "휴무 모름" not in result.answer
    assert "운영 여부를 확인하지 못해 안내하지 않았습니다" in result.answer


@pytest.mark.asyncio
async def test_disaster_block_withholds_alternatives_without_a_lookup():
    """★재난문자는 전국 목록이라(`disaster_msg.near()`가 좌표를 안 쓴다)
    어느 후보로 옮겨도 같은 판정이다 — 후보 풀을 부르지도 않는다."""
    disaster = {"for_region": [{"step": "위급재난", "kind": "지진"}],
                "source": "safetydata", "confirmed_at": "x"}
    result, calls = await _run(_values(starts_at=SATURDAY, pool=_pool(NEAR),
                                       disaster=disaster))
    assert result.decisions[0]["status"] == "problem"
    assert result.decisions[0]["alternatives"] == {"status": "withheld",
                                                   "reason": "disaster_blocks"}
    assert "read.place_candidates" not in calls


# ══════════════════════════════════════════════════════════════════
# 선호도
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_preference_is_read_from_current_state():
    # ★폴백 순서는 2026-10-01 팀 합의(활동 우선: 소분류 → 중분류, 타입은 대분류와 한 묶음)를 따른다.
    #   2026-10-03 부터 10곳이 찰 때까지 넓히므로 열을 둔다(모자라면 가장 느슨한 단계까지 간다).
    others = [{**NEAR, "contentid": f"o{i}", "lclsSystm3": "HS019900"} for i in range(10)]
    result, _ = await _run(_values(pool=_pool(*others)),
                           state={"activity_preference": "activity"})
    found = result.decisions[0]["alternatives"]
    assert found["preference"] == "activity"
    assert found["dropped_fields"] == ["lclsSystm3"]
    assert any("lclsSystm3" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_unknown_preference_is_not_guessed():
    result, _ = await _run(_values(pool=_pool(NEAR)),
                           state={"activity_preference": "cheap"})
    assert result.decisions[0]["alternatives"]["preference"] is None
    assert any("알 수 없는 선호도" in w for w in result.warnings)


# ══════════════════════════════════════════════════════════════════
# 예산
# ══════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_longest_path_fits_the_tool_budget():
    """예약·규정·장소·기상·재난·후보 = 6 ≤ `max_steps`(지금 12). 넘치면 ToolBudgetExceeded."""
    result, calls = await _run(_values(pool=_pool(NEAR), weather_sensitive=True))
    assert len(calls) == 6 <= ActivityTeam.manifest.max_steps
    assert result.decisions[0]["alternatives"]["status"] == "ranked"
