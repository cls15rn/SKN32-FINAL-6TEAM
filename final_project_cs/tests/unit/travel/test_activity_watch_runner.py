# -*- coding: utf-8 -*-
"""`activity/watch_runner.py` — 활동 재난문자 감시 한 회차의 껍질.

DB·네트워크 없이 본다. 3시간·5분 규칙의 SQL 쪽은 `tests/integration/db/test_activity_watch_db.py`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.travel_ops.activity import watch as watch_mod
from app.modules.travel_ops.activity.watch import TravelWatcher
from app.modules.travel_ops.activity.watch_runner import run_activity_disaster


class _Disaster:
    name = "disaster_msg"

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def near(self, lat, lng, *, within):
        self.calls.append((lat, lng, within))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Sources:
    def __init__(self, disaster):
        self.disaster = disaster


def _target(**over):
    base = {"activity_id": "a1", "name": "경복궁", "activity_time": datetime.now(UTC) + timedelta(hours=1),
            "latitude": 37.57, "longitude": 126.98, "last_seen": None}
    return {**base, **over}


def _patch(monkeypatch, targets, seen=None):
    seen = seen if seen is not None else {}

    def due(self, limit=5, *, within_hours, min_gap_minutes):
        seen.update(limit=limit, within_hours=within_hours, min_gap_minutes=min_gap_minutes)
        return targets

    monkeypatch.setattr(TravelWatcher, "due_activities", due)
    monkeypatch.setattr(TravelWatcher, "_record", lambda self, **kw: None)
    return seen


def _run(sources, **kw):
    return run_activity_disaster(connection_factory=lambda: None, tenant_id="t", sources=sources, **kw)


def test_default_rule_is_three_hours_and_five_minutes(monkeypatch):
    seen = _patch(monkeypatch, [])
    _run(_Sources(_Disaster({"for_region": []})))
    assert seen["within_hours"] == watch_mod.ACTIVITY_WINDOW_HOURS == 3.0
    assert seen["min_gap_minutes"] == watch_mod.ACTIVITY_GAP_MINUTES == 5.0


def test_rule_can_be_swapped_by_arguments(monkeypatch):
    seen = _patch(monkeypatch, [])
    _run(_Sources(_Disaster({"for_region": []})), within_hours=1, min_gap_minutes=2)
    assert (seen["within_hours"], seen["min_gap_minutes"]) == (1.0, 2.0)


def test_checked_target_asks_disaster_source_with_its_coordinates_and_time(monkeypatch):
    when = datetime.now(UTC) + timedelta(hours=2)
    _patch(monkeypatch, [_target(activity_time=when)])
    source = _Disaster({"for_region": [{"serial": "1", "step": "안전안내"}]})

    out = _run(_Sources(source))

    assert source.calls == [(37.57, 126.98, when)]
    assert out == {"checked": 1, "changes": 0, "unknown": 0, "no_source": 0, "fatal": 0}


def test_source_answering_none_is_unknown_not_clear(monkeypatch):
    _patch(monkeypatch, [_target()])
    out = _run(_Sources(_Disaster(None)))
    assert out["unknown"] == 1 and out["changes"] == 0 and out["fatal"] == 0


def test_missing_coordinates_are_unknown(monkeypatch):
    _patch(monkeypatch, [_target(latitude=None)])
    source = _Disaster({"for_region": []})
    out = _run(_Sources(source))
    assert out["unknown"] == 1 and source.calls == []


def test_no_source_is_counted_not_silently_skipped(monkeypatch):
    _patch(monkeypatch, [_target()])
    out = _run(_Sources(None))
    assert out["no_source"] == 1 and out["checked"] == 0


def test_source_exception_is_counted_as_fatal_and_does_not_raise(monkeypatch):
    """한 회차가 죽어도 다른 되잡기 작업을 막지 않는다 — 대신 fatal 로 세어 sweepers 가 exit 1 로 알린다."""
    _patch(monkeypatch, [_target()])
    out = _run(_Sources(_Disaster(RuntimeError("api down"))))
    assert out["fatal"] == 1 and out["checked"] == 0
