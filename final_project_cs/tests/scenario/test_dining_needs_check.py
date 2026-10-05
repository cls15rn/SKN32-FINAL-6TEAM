"""원래 식당의 영업을 모를 때 채팅도 일정을 유지한 완료 응답을 준다."""
from types import SimpleNamespace
from uuid import uuid4

from app.infrastructure.db.session import get_connection
from app.modules.travel_ops.case_engine import cleanup_tenant
from app.modules.travel_ops.trip_desk import TripDesk
from app.modules.travel_ops.trip_messages import handle_trip_message

from .test_case_version_day import REPORTS, _at, _classifier, _seed


def test_dining_unknown_is_answered_without_escalation_or_a_new_itinerary(monkeypatch):
    tenant = "dining_unknown_" + uuid4().hex[:10]
    store, _, trip_id = _seed(tenant)
    desk = TripDesk(store=store, connection_factory=get_connection)
    monkeypatch.setattr(desk, "_dining_states", lambda slots: {
        s["place_id"]: {"linked": True, "open_at_slot": None} for s in slots})
    report = REPORTS["delay"]
    try:
        arguments = {"tenant": tenant, "trip_id": trip_id, "request_id": "unknown-hours",
                     "message": report["message"], "at": _at(report["at"]), "classifier": _classifier,
                     "chat": SimpleNamespace(json=lambda *_: {"type": "delay", "minutes": report["minutes"]}),
                     "desk": desk, "actor_id": "test"}
        outcome = handle_trip_message(**arguments)
        assert outcome["status"] == "needs_check" and outcome["case_status"] == "resolved"
        assert outcome["answer"] == outcome["outcome"]["message"]
        assert "확인" in outcome["answer"] and "바꾸지" in outcome["answer"]
        with get_connection() as conn:
            assert store.latest(conn, trip_id)[0]["version"] == 1
        repeated = handle_trip_message(**arguments)
        assert repeated["status"] == "duplicate" and repeated["case_status"] == "resolved"
        assert outcome["answer"] in repeated["answer"]
    finally:
        cleanup_tenant(tenant)
