"""실제 원장 SQL 판정이 대체 후보의 영업 필터까지 전달되는지 검증한다."""
from datetime import datetime
from uuid import uuid4

from app.modules.travel_ops.dining.ledger import dining_states
from app.modules.travel_ops.replan import dining_candidates

from tests.integration.dining.test_dining_judgment import add_hours


def test_ledger_open_closed_and_unknown_override_core_hours(conn, place):
    start = datetime.fromisoformat("2026-09-21T12:00:00+09:00")
    tenant = f"opening-test-{uuid4().hex}"
    core_ids = [str(uuid4()) for _ in range(3)]
    uids = [place() for _ in range(3)]
    add_hours(conn, uids[0], 1, [(660, 1320, None)])
    add_hours(conn, uids[1], 1, [(960, 1320, None)])
    candidates = [{"place_id": core_id, "name": f"식당-{i}", "kind": "dining",
                   "latitude": 37.5, "longitude": 127,
                   "attributes": {} if i == 0 else {"hours": ["11:00", "22:00"]}}
                  for i, core_id in enumerate(core_ids)]
    try:
        for core_id, uid in zip(core_ids, uids):
            conn.execute("INSERT INTO dining.dn_core_place_link "
                         "(tenant_id, core_place_id, place_uid, linked_by) VALUES (%s,%s,%s,'test')",
                         (tenant, core_id, uid))
        result = dining_candidates(
            original={"place_id": str(uuid4()), "latitude": 37.5, "longitude": 127}, places=candidates,
            arrival=start, minutes=50, constraints={}, radius_m=700, next_start=None,
            state_lookup=lambda slots: dining_states(conn, tenant, slots))
        assert [c.key for c in result if not c.rejected] == [core_ids[0]]
        assert all(c.rejected for c in result[1:])
    finally:
        conn.execute("DELETE FROM dining.dn_core_place_link WHERE tenant_id=%s", (tenant,))
