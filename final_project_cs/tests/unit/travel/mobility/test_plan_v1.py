# final_project_cs/tests/unit/travel/mobility/test_plan_v1.py — 32번 방 · 값 내놓기(plan) 시험 · 23번 방 options[] 후보·이유(plan-v2) 추가
# 실행: 저장소 루트에서
#   $env:PYTHONPATH = "final_project_cs"
#   python final_project_cs/tests/unit/travel/mobility/test_plan_v1.py          # 단위 + (시간표가 있으면) 예시 골든 대조
#   python -m pytest tests/unit/travel/mobility/test_plan_v1.py
# 실패하면 종료코드 1.
#
# 축
#   U  단위 — 운행일(45 계약)·시각 표기·uses 표기(9/24)·party 매핑. 데이터 없이 돈다
#   G  골든 — 예시 입력 → 예시 출력(basis 제외)이 바이트 단위로 같다. 시간표가 바뀌면 여기서 걸린다(의도)
#   K  계약 — 코어로 나가는 키가 기존 칸뿐(새 키 0) · 금지 키 0 · ends−starts = 계획 수단 eta_min
#   T  시각 — ends + @ ≤ 다음 항목 시작 · starts 에서 다시 판정하면 성립(역산 시각이 참인지)
#   C  코어 판정 — 팀 itinerary_checks.check_itinerary 가 위반 0 (등록에서 안 걸린다)
#   O  (23) options — id 수단 태그 · 이유(축별 사실 · 순위 없음) · walk_m·fare_krw · uses 자가 검사(팀 route_uses)
#      · 후보별 출발 칸 없음(결정 1 — 더 이른 출발 후보는 봉투 left_out) · 장소 기준 버스 · 환승 칸(표시 · 기본 off)
#   F  (54) 요금 — 규칙 fare 절만 · 지하철 운임거리 괄호(하한=상한 요금일 때만) · 버스 유형별 단일 · 조조 · 통합환승 합성
#      · 티머니 실측 태그 대조 · 모르면 뺀다 · 재계획이 대중교통 옵션을 요금으로 안 떨어뜨린다
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

from app.modules.travel_ops.mobility.engine.plan import (  # noqa: E402
    iso_of, label_of, line_name, party_of, plan, service_day, station_name, uses_of)

KST = timezone(timedelta(hours=9))
IN = HERE / "plan_example_in_v1.json"
GOLD = HERE / "plan_example_out_v1.json"
GOLD_ALL = HERE / "plan_example_out_all_v1.json"   # 23 — 버스 포함(자전거는 실시간 조회라 뺀다)
MODES = ["subway", "walk"]           # 골든은 지하철·도보로 뽑았다(노트북 버스 데이터가 옛 판 · 41 뒤 재추출)
MODES_ALL = ["subway", "walk", "bus"]

ITEM_KEYS = {"seq", "kind", "title", "place", "starts_at", "ends_at", "route", "detail"}   # trip_api.ItemIn
ROUTE_KEYS = {"from", "to", "planned", "options"}                                        # 스펙 v1.3
OPTION_KEYS = {"id", "label", "eta_min", "uses"}                                         # 스펙 v1.3 — 필수
OPTION_OPT = {"walk_m", "fare_krw"}                         # 23 · 스펙 v1.4 — 팀 route_def 기존 칸(모르면 뺀다)
ID_PREFIX = ("subway", "bus", "walk", "bike")
FORBIDDEN = {"last_feasible_depart", "last_feasible_depart_min", "grade", "verdict", "code", "reason",
             "relief", "slack_min", "margin_min", "buffer_min", "p90_eta_min", "p95_eta_min",
             "average_eta_min", "recommend_depart_at", "basis", "modes", "legs",
             "depart_at", "start_min", "left_out", "transfer_car", "exit_car"}   # 23 — 후보별 출발 없음 · 표시 필드 기본 off


# ── U 단위 ────────────────────────────────────────────────────────────────
def test_service_day():
    d, m = service_day(datetime(2026, 9, 30, 0, 30, tzinfo=KST))
    assert (d, m) == (date(2026, 9, 29), 1470), "04:00 전은 전날 운행일의 24:xx (45 계약)"
    d, m = service_day(datetime(2026, 9, 30, 4, 0, tzinfo=KST))
    assert (d, m) == (date(2026, 9, 30), 240), "04:00 은 그날 운행일"
    d, m = service_day(datetime(2026, 9, 30, 3, 59, tzinfo=KST))
    assert (d, m) == (date(2026, 9, 29), 1679)


def test_iso_of():
    assert iso_of(date(2026, 9, 29), 1470) == "2026-09-30T00:30:00+09:00", "24:30 은 다음 날 벽시계"
    assert iso_of(date(2026, 9, 29), 558) == "2026-09-29T09:18:00+09:00"
    for m in (240, 700, 1439, 1440, 1500, 1679):
        dt = datetime.fromisoformat(iso_of(date(2026, 9, 29), m))
        assert service_day(dt) == (date(2026, 9, 29), m), f"왕복 {m}"


def test_names():
    assert line_name("02호선") == "2호선"
    assert line_name("09호선") == "9호선"
    assert line_name("경의선") == "경의중앙선"
    assert line_name("공항철도") == "공항철도"
    assert line_name("인천2호선") == "인천2호선", "숫자 두 자리 + 호선 만 줄인다"
    assert line_name("인천선") == "인천1호선" and line_name("우이신설경전철") == "우이신설선"
    assert line_name("김포도시철도") == "김포골드라인"
    assert station_name("잠실역") == "잠실"
    assert station_name("경복궁(정부서울청사)") == "경복궁"
    assert station_name("서울역") == "서울역"
    assert station_name("역삼") == "역삼", "「역」으로 끝나지 않으면 그대로"


def test_uses():
    legs = [{"line": "03호선", "from": "경복궁", "to": "을지로3가"},
            {"line": "02호선", "from": "을지로3가", "to": "성수"}]
    assert uses_of(legs) == ["3호선:경복궁", "3호선:을지로3가", "2호선:을지로3가", "2호선:성수"], \
        "탄·갈아탄·내린 역만 · 환승역은 두 노선 각각"
    assert uses_of([{"mode": "bus", "route": "2224", "from": "a", "to": "b"}]) == ["버스:2224"]
    assert uses_of([{"mode": "bike", "from": "a", "to": "b"}]) == []
    assert label_of(legs) == "3호선 경복궁→을지로3가 → 2호선 을지로3가→성수"


def test_party():
    assert party_of(2, {}) == {"size": 2}
    assert party_of(None, {"mobility_ease": "needs_rest"}) == {"fatigue_high": True}
    assert party_of(1, {"mobility_ease": "ok"}) == {"size": 1}


# ── 데이터가 필요한 축 ──────────────────────────────────────────────────────
_RT = None


def _runtime():
    global _RT
    if _RT is None:
        from app.modules.travel_ops.mobility.engine.runtime import build_verifier
        try:
            _RT = build_verifier(quiet=True)
        except RuntimeError as e:          # 시간표가 없는 기기 — 데이터 축은 SKIP
            _RT = e
    return None if isinstance(_RT, Exception) else _RT


def _skip_if_no_data():
    if _runtime() is None:
        try:
            import pytest
            pytest.skip("시간표 없음(DATA_DIR) — 데이터 축 SKIP")
        except ImportError:
            raise _Skip()


class _Skip(Exception):
    pass


def _run(trace=None):
    doc = json.loads(IN.read_text(encoding="utf-8"))
    return doc, plan(doc["places"], doc["items"], doc.get("party_size"), doc.get("constraints"),
                     runtime=_runtime(), modes=MODES, trace=trace)


def _walk_keys(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k, path
            yield from _walk_keys(v, f"{path}.{k}")
    elif isinstance(x, list):
        for v in x:
            yield from _walk_keys(v, path)


def test_golden():
    _skip_if_no_data()
    _, got = _run()
    got.pop("basis")
    want = json.loads(GOLD.read_text(encoding="utf-8"))
    assert got == want, "예시 출력이 골든과 다르다 — 시간표·규칙이 바뀌었으면 골든을 다시 뽑고 이유를 적는다"


def test_contract_keys():
    _skip_if_no_data()
    _, got = _run()
    for it in got["items"]:
        assert set(it) <= ITEM_KEYS, f"ItemIn 에 없는 키: {set(it) - ITEM_KEYS}"
    for key, r in got["routes"].items():
        assert set(r) == ROUTE_KEYS, f"{key}: {set(r) ^ ROUTE_KEYS}"
        assert r["planned"] in {o["id"] for o in r["options"]}
        for o in r["options"]:
            assert OPTION_KEYS <= set(o) <= OPTION_KEYS | OPTION_OPT, f"{key}/{o.get('id')}: {set(o) ^ OPTION_KEYS}"
            assert isinstance(o["eta_min"], int) and o["eta_min"] > 0
    bad = [(k, p) for k, p in _walk_keys({"items": got["items"], "routes": got["routes"]}) if k in FORBIDDEN]
    assert not bad, f"코어로 나가면 안 되는 키: {bad}"
    mob = [it for it in got["items"] if it["kind"] == "mobility"]
    for it in mob:
        assert it["route"] in got["routes"]
        for k in ("starts_at", "ends_at"):
            assert it[k].endswith(":00+09:00"), f"시각 표기 {it[k]}"
        r = got["routes"][it["route"]]
        eta = next(o["eta_min"] for o in r["options"] if o["id"] == r["planned"])
        s, e = datetime.fromisoformat(it["starts_at"]), datetime.fromisoformat(it["ends_at"])
        assert (e - s) == timedelta(minutes=eta), "ends − starts = 계획 수단 eta_min"
    assert [it["seq"] for it in got["items"]] == list(range(1, len(got["items"]) + 1))


def test_times_hold():
    _skip_if_no_data()
    tr = []
    doc, got = _run(trace=tr)
    items = got["items"]
    for i, it in enumerate(items):
        if it["kind"] != "mobility":
            continue
        nxt = datetime.fromisoformat(items[i + 1]["starts_at"])
        t = next(x for x in tr if x["route"] == it["route"])
        p = next(o for o in t["options"] if o["id"] == t["planned"])
        assert p["slack_min"] >= 0, "slack 음수 = 늦는다"
        e = datetime.fromisoformat(it["ends_at"])
        assert e + timedelta(minutes=p["margin_min"] + p["slack_min"]) == nxt, \
            "starts = 목표 − (eta + @) − slack  (slack ≥ 0)"
        assert s_ok(it, items[i - 1]), "앞 항목이 끝나기 전에 떠나라는 값이면 예시가 틀렸다(예시에서는 없어야 한다)"


def s_ok(mob, prev):
    s = datetime.fromisoformat(mob["starts_at"])
    pe = datetime.fromisoformat(prev.get("ends_at") or prev["starts_at"])
    return s >= pe


def test_core_itinerary_checks():
    _skip_if_no_data()
    from app.modules.travel_ops.itinerary_checks import Part, check_itinerary
    doc, got = _run()
    places = {p["key"]: p for p in doc["places"]}
    parts = [Part(seq=it["seq"], kind=it["kind"], title=it["title"],
                  starts_at=datetime.fromisoformat(it["starts_at"]),
                  ends_at=datetime.fromisoformat(it["ends_at"]) if it.get("ends_at") else None,
                  place=places.get(it.get("place")), route=got["routes"].get(it.get("route")))
             for it in got["items"]]
    v = check_itinerary(parts, constraints=doc.get("constraints"), party_size=doc.get("party_size"))
    assert not v, f"코어 등록 판정 위반: {[x.as_dict() for x in v]}"


def test_skip_no_station():
    _skip_if_no_data()
    places = [{"key": "g", "name": "경복궁", "kind": "activity", "lat": 37.5796, "lon": 126.977},
              {"key": "j", "name": "제주", "kind": "activity", "lat": 33.5, "lon": 126.5}]
    items = [{"seq": 1, "kind": "activity", "title": "A", "place": "g",
              "starts_at": "2026-09-29T10:00:00+09:00", "ends_at": "2026-09-29T11:00:00+09:00"},
             {"seq": 2, "kind": "activity", "title": "B", "place": "j", "starts_at": "2026-09-29T15:00:00+09:00"}]
    got = plan(places, items, 1, {}, runtime=_runtime(), modes=MODES)
    assert [it["kind"] for it in got["items"]] == ["activity", "activity"], "모르면 이동 항목을 안 만든다"
    assert got["routes"] == {}
    assert got["skipped"] and got["skipped"][0]["code"] == "no_data"


def test_night_service_day():
    """자정 넘는 도착 목표 — 운행일 9/29 의 24:30. 이동 항목 시각은 벽시계(+1일)로 나간다."""
    _skip_if_no_data()
    places = [{"key": "g", "name": "경복궁", "kind": "activity", "lat": 37.5796, "lon": 126.977},
              {"key": "s", "name": "성수", "kind": "activity", "lat": 37.5445, "lon": 127.056}]
    items = [{"seq": 1, "kind": "activity", "title": "A", "place": "g",
              "starts_at": "2026-09-29T22:30:00+09:00", "ends_at": "2026-09-29T23:30:00+09:00"},
             {"seq": 2, "kind": "activity", "title": "B", "place": "s", "starts_at": "2026-09-30T00:30:00+09:00"}]
    tr = []
    got = plan(places, items, 1, {}, runtime=_runtime(), modes=MODES, trace=tr)
    assert tr and tr[0]["date"] == "2026-09-29" and tr[0]["arrive_by_min"] == 1470, "case.date = 운행일"
    mob = [it for it in got["items"] if it["kind"] == "mobility"]
    assert len(mob) == 1
    s, e = datetime.fromisoformat(mob[0]["starts_at"]), datetime.fromisoformat(mob[0]["ends_at"])
    assert s < e <= datetime(2026, 9, 30, 0, 30, tzinfo=KST)


_GS = [{"key": "g", "name": "경복궁", "kind": "activity", "lat": 37.5796, "lon": 126.977},
       {"key": "s", "name": "성수", "kind": "activity", "lat": 37.5445, "lon": 127.056}]


def _two(t0, t1, t2):
    return [{"seq": 1, "kind": "activity", "title": "A", "place": "g", "starts_at": t0, "ends_at": t1},
            {"seq": 2, "kind": "activity", "title": "B", "place": "s", "starts_at": t2}]


def test_0400_boundary():
    """자체 대조 #1 — 도착 목표 04:00 에서 역 도보를 빼면 전 운행일(27:5x)이다. 정수 분을 그대로 넘기면
    판정기가 240 미만을 +24h 로 읽어 그날 밤 막차(20시간 뒤)를 내던 결함. 이제는 목표보다 늦은 출발이 나오면 안 된다."""
    _skip_if_no_data()
    got = plan(_GS, _two("2026-09-28T22:00:00+09:00", "2026-09-28T23:00:00+09:00", "2026-09-29T04:00:00+09:00"),
               1, {}, runtime=_runtime(), modes=MODES)
    mob = [it for it in got["items"] if it["kind"] == "mobility"]
    assert len(mob) == 1, got["skipped"]
    s, e = datetime.fromisoformat(mob[0]["starts_at"]), datetime.fromisoformat(mob[0]["ends_at"])
    assert datetime(2026, 9, 28, 23, 0, tzinfo=KST) <= s < e <= datetime(2026, 9, 29, 4, 0, tzinfo=KST), \
        f"전 운행일 막차(24:xx)로 가야 한다 — 받은 값 {mob[0]}"
    assert any(it["kind"] == "mobility" for it in got["items"]) or got["skipped"]


def test_not_before_prev_end():
    """자체 대조 #2 — 앞 항목이 끝나기 전에 떠나라는 값은 코어 등록이 overlap 으로 거절한다 → 싣지 않고 skipped."""
    _skip_if_no_data()
    got = plan(_GS, _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T11:10:00+09:00"),
               1, {}, runtime=_runtime(), modes=MODES)
    assert not any(it["kind"] == "mobility" for it in got["items"])
    assert got["skipped"] and got["skipped"][0]["code"] == "arrive_late"


def test_keep_input_move_when_skipped():
    """자체 대조 #6 — 우리가 못 만든 구간의 입력 이동 항목은 그대로 남는다. 만든 구간의 것은 우리 값으로 바뀐다."""
    _skip_if_no_data()
    tight = _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T11:10:00+09:00")
    tight.append({"seq": 3, "kind": "mobility", "title": "택시", "route": "taxi_g_s",
                  "starts_at": "2026-09-29T11:00:00+09:00", "ends_at": "2026-09-29T11:10:00+09:00"})
    got = plan(_GS, tight, 1, {}, runtime=_runtime(), modes=MODES)
    assert [it.get("route") for it in got["items"] if it["kind"] == "mobility"] == ["taxi_g_s"]
    loose = _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T13:00:00+09:00")
    loose.append(dict(tight[-1]))
    got = plan(_GS, loose, 1, {}, runtime=_runtime(), modes=MODES)
    assert [it.get("route") for it in got["items"] if it["kind"] == "mobility"] == ["g_to_s"]


def _recheck(got, tr, doc_items, party_size=None, constraints=None, stage="planning"):
    """출력 기준으로 다시 본다(GPT 2차 #2) — 출력 options 와 trace 후보 ID 집합이 같고, 출력 eta_min 이 판정기를
    **따로** 불러 낸 소요(+장소·정류장 도보)와 같고, 도착 목표는 원본 다음 일정 starts_at 에서 가져온다.
    재판정에는 원래 party·first_visit·stage 를 그대로 넘긴다."""
    v = _runtime()._v
    constraints = dict(constraints or {})
    party = party_of(party_size, constraints)
    fv = constraints.get("first_visit", True)
    stays = sorted((x for x in doc_items if x.get("kind") != "mobility"),
                   key=lambda x: datetime.fromisoformat(x["starts_at"]))
    n = 0
    items = got["items"]
    for i, it in enumerate(items):
        if it["kind"] != "mobility":
            continue
        t = next(x for x in tr if x["route"] == it["route"])
        out_opts = {o["id"]: o for o in got["routes"][it["route"]]["options"]}
        assert set(out_opts) == {o["id"] for o in t["options"]}, "출력 options 와 판정한 후보가 다르다"
        nxt = next(x for x in stays if x["title"] == items[i + 1]["title"])   # 원본 다음 일정
        sdate = date.fromisoformat(t["date"])
        d, m = service_day(datetime.fromisoformat(nxt["starts_at"]))
        arrive_by = m + (d - sdate).days * 1440
        d, m = service_day(datetime.fromisoformat(it["starts_at"]))
        start_min = m + (d - sdate).days * 1440
        for o in t["options"]:
            eta_out = out_opts[o["id"]]["eta_min"]
            ck = o.get("check")
            if ck is None:                          # 도보 — 출발 + 소요 + 버퍼 ≤ 목표
                assert eta_out == o["eta_min"]
                assert start_min + eta_out + v.rv("buffer", "by_stage", stage) <= arrive_by
                continue
            dep = start_min + ck["walk_place_in"] - ck["off"] + ck["walk_stop_in"]
            by_station = arrive_by - ck["walk_place_out"] - ck["off"]
            r = v.verify_case({"id": f"recheck/{it['route']}/{o['id']}", "date": ck["date"], "legs": ck["legs"],
                               "depart_at": dep, "arrive_by": by_station - ck["walk_stop_out"],
                               "stage": stage, "party": party, "first_visit": fv, "no_alternatives": True})
            assert r.out["verdict"] == "feasible" and r.out.get("slack_min", -1) >= 0, \
                f"{it['route']}/{o['id']} 가 출력 출발 시각에서 성립하지 않는다: {r.out}"
            eta_indep = (ck["walk_place_in"] + ck["walk_stop_in"] + r.out["eta_min"]
                         + ck["walk_stop_out"] + ck["walk_place_out"])
            assert eta_out == eta_indep, f"{it['route']}/{o['id']}: 출력 eta {eta_out} ≠ 따로 잰 {eta_indep}"
            n += 1
    return n


def test_independent_recheck():
    """GPT #5 — 출력된 starts_at 에서 판정기를 **따로** 불러 성립을 본다(trace 등식에 기대지 않는다).
    GPT #1 — options 에 남은 후보 전부가 그 한 출발 시각에서 성립해야 한다. 자정 가까운 경복궁→성수는
    후보마다 마지막 성립 출발이 다르다(23:42 · 23:32) — 더 일찍 떠나야 하는 후보는 options 에 남으면 안 된다."""
    _skip_if_no_data()
    tr = []
    doc, got = _run(trace=tr)
    n = _recheck(got, tr, doc["items"], doc.get("party_size"), doc.get("constraints"))
    tr = []
    night = _two("2026-09-29T22:30:00+09:00", "2026-09-29T23:30:00+09:00", "2026-09-30T00:30:00+09:00")
    got = plan(_GS, night, 1, {}, runtime=_runtime(), modes=MODES, trace=tr)
    n += _recheck(got, tr, night, 1, {})
    assert n >= 4


def test_route_key_reserved():
    """GPT #2 — 남겨 둔 입력 이동 항목·호출 쪽 routes 의 키를 새 경로가 덮지 않는다."""
    _skip_if_no_data()
    items = _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T13:00:00+09:00")
    items.append({"seq": 9, "kind": "activity", "title": "C", "place": "s",
                  "starts_at": "2026-09-29T15:00:00+09:00"})
    items.append({"seq": 10, "kind": "mobility", "title": "옛 이동", "route": "g_to_s",
                  "starts_at": "2026-09-29T14:00:00+09:00", "ends_at": "2026-09-29T14:10:00+09:00"})
    got = plan(_GS, items, 1, {}, runtime=_runtime(), modes=MODES, routes={"g_to_s_2": {}})
    assert "g_to_s" not in got["routes"] and "g_to_s_2" not in got["routes"], list(got["routes"])
    keys = [it.get("route") for it in got["items"] if it["kind"] == "mobility"]
    assert "g_to_s" in keys, "같은 장소 사이의 입력 이동 항목은 남는다"


def test_recheck_catches_tampering():
    """_recheck 자신의 실패 장면 — 출력 eta 를 바꾸거나 후보를 끼워 넣으면 잡아야 한다(GPT 2차 #2 재현)."""
    _skip_if_no_data()
    import copy
    tr = []
    doc, got = _run(trace=tr)
    key = next(it["route"] for it in got["items"] if it["kind"] == "mobility" and it["route"] != "dinner_to_lotte_mart")
    for tamper in ("eta", "extra"):
        g = copy.deepcopy(got)
        opts = g["routes"][key]["options"]
        if tamper == "eta":
            opts[0]["eta_min"] = 999
        else:
            opts.append({"id": "fake", "label": "가짜", "eta_min": 1, "uses": []})
        try:
            _recheck(g, tr, doc["items"], doc.get("party_size"), doc.get("constraints"))
        except AssertionError:
            continue
        raise AssertionError(f"_recheck 가 {tamper} 변조를 못 잡았다")


def test_cli_passes_routes():
    """GPT 2차 #1 — CLI(plan_doc)도 입력의 기존 routes 키를 새 키로 안 쓴다 · 함수 호출과 결과가 같다."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine.plan import plan_doc
    doc = {"places": _GS, "items": _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00",
                                        "2026-09-29T13:00:00+09:00"),
           "party_size": 1, "constraints": {}, "routes": {"g_to_s": {"from": "옛", "to": "옛", "planned": "x",
                                                                      "options": []}}}
    by_cli = plan_doc(doc, runtime=_runtime(), modes=MODES)
    by_fn = plan(doc["places"], doc["items"], 1, {}, runtime=_runtime(), modes=MODES, routes=doc["routes"])
    assert "g_to_s" not in by_cli["routes"] and list(by_cli["routes"]) == ["g_to_s_2"], list(by_cli["routes"])
    for r in (by_cli, by_fn):
        r.pop("basis")
    assert by_cli == by_fn


def test_not_before_seconds():
    """GPT #3 — 앞 항목이 09:47:30 에 끝나면 09:47 출발은 겹친다. 하한은 분 올림."""
    _skip_if_no_data()
    got = plan(_GS, _two("2026-09-29T09:00:00+09:00", "2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00"),
               1, {}, runtime=_runtime(), modes=MODES)
    mob = [it for it in got["items"] if it["kind"] == "mobility"]
    assert mob, got["skipped"]
    s = datetime.fromisoformat(mob[0]["starts_at"])
    end_sec = (s + timedelta(seconds=30)).isoformat()          # 이동 출발 30초 뒤에 앞 항목이 끝나게
    got = plan(_GS, _two("2026-09-29T09:00:00+09:00", end_sec, "2026-09-29T11:00:00+09:00"),
               1, {}, runtime=_runtime(), modes=MODES)
    for it in got["items"]:
        if it["kind"] == "mobility":
            assert datetime.fromisoformat(it["starts_at"]) >= datetime.fromisoformat(end_sec), it


def test_leading_and_only_moves_kept():
    """GPT #4 — 첫 비이동 항목보다 앞선 입력 이동 · 이동 항목만 있는 입력은 사라지지 않는다."""
    _skip_if_no_data()
    mv = {"seq": 1, "kind": "mobility", "title": "공항 → 호텔", "route": "airport",
          "starts_at": "2026-09-29T08:00:00+09:00", "ends_at": "2026-09-29T08:50:00+09:00"}
    act = {"seq": 2, "kind": "activity", "title": "A", "place": "g", "starts_at": "2026-09-29T09:00:00+09:00"}
    got = plan(_GS, [mv, act], 1, {}, runtime=_runtime(), modes=MODES)
    assert [it["title"] for it in got["items"]] == ["공항 → 호텔", "A"]
    got = plan(_GS, [mv], 1, {}, runtime=_runtime(), modes=MODES)
    assert [it["title"] for it in got["items"]] == ["공항 → 호텔"]


# ── O (23) options 후보·이유 ────────────────────────────────────────────────
_NT = [{"key": "ns", "name": "남산타워", "kind": "activity", "lat": 37.5512, "lon": 126.9882},
       {"key": "it", "name": "이태원", "kind": "activity", "lat": 37.5345, "lon": 126.9946}]


def _ns_it(**kw):
    """남산타워 → 이태원 15:00 — 버스 405 가 계획 수단, 지하철이 같은 출발로 같이 실린다(9/29 화 · 실데이터)."""
    items = [{"seq": 1, "kind": "activity", "title": "A", "place": "ns",
              "starts_at": "2026-09-29T11:00:00+09:00", "ends_at": "2026-09-29T12:00:00+09:00"},
             {"seq": 2, "kind": "activity", "title": "B", "place": "it", "starts_at": "2026-09-29T15:00:00+09:00"}]
    return items, plan(_NT, items, 1, {}, runtime=_runtime(), **kw)


def test_golden_all_modes():
    """버스 포함 예시(27 손일 「버스 포함 예시 재추출」) — 자전거는 실시간 조회라 뺀다."""
    _skip_if_no_data()
    doc = json.loads(IN.read_text(encoding="utf-8"))
    got = plan(doc["places"], doc["items"], doc.get("party_size"), doc.get("constraints"),
               runtime=_runtime(), modes=MODES_ALL)
    got.pop("basis")
    want = json.loads(GOLD_ALL.read_text(encoding="utf-8"))
    assert got == want, "버스 포함 예시가 골든과 다르다 — 시간표·버스 프로파일·규칙이 바뀌었으면 다시 뽑고 이유를 적는다"


def test_option_ids_mode_tag():
    """◆선호 — 수단 태그는 id 접두(지하철/버스 구분) · route 안에서 유일 · 버스는 노선번호."""
    _skip_if_no_data()
    for got in (_run()[1], _ns_it(modes=MODES_ALL)[1]):
        for key, r in got["routes"].items():
            ids = [o["id"] for o in r["options"]]
            assert len(ids) == len(set(ids)), f"{key}: id 중복 {ids}"
            for o in r["options"]:
                assert o["id"].split("_")[0] in ID_PREFIX, o["id"]
                if o["id"].startswith("bus_"):          # id 로 노선번호를 복원하지 않는다(GPT 23 #9) — 접두만 본다
                    assert len(o["uses"]) == 1 and o["uses"][0].startswith("버스:"), o
                    assert o["id"] == f"bus_{o['uses'][0][3:]}" or o["id"].startswith(f"bus_{o['uses'][0][3:]}_"), o
                if o["id"].startswith("subway"):
                    assert o["uses"] and not any(u.startswith("버스:") for u in o["uses"]), o
    _, got = _ns_it(modes=MODES_ALL)
    r = got["routes"]["ns_to_it"]
    assert r["planned"] == "bus_405" and {o["id"] for o in r["options"]} == {"bus_405", "subway_1"}, r


def test_uses_self_check():
    """41 넘김 — 모든 uses 가 팀 route_uses.problem() 을 통과. 실패 장면: 표기가 틀린 후보는 싣지 않고 left_out(uses_format)."""
    _skip_if_no_data()
    from app.modules.travel_ops.route_uses import problem
    from app.modules.travel_ops.mobility.engine import plan as P
    for got in (_run()[1], _ns_it(modes=MODES_ALL)[1]):
        bad = [(u, problem(u)) for r in got["routes"].values() for o in r["options"] for u in o["uses"] if problem(u)]
        assert not bad, bad
    orig = P.uses_of
    try:
        P.uses_of = lambda legs: ["02호선:잠실역"] if legs else []
        got = plan(_GS, _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T13:30:00+09:00"),
                   1, {}, runtime=_runtime(), modes=MODES)
    finally:
        P.uses_of = orig
    assert not any(it["kind"] == "mobility" for it in got["items"]), "표기가 틀린 후보를 실었다"
    assert got["skipped"] and got["skipped"][0]["code"] == "no_data" and "uses" in got["skipped"][0]["reason"]


def test_line_names_contract():
    """경의선 외 노선명 — 시간표의 모든 노선 이름이 계약 표기(route_uses)로 바뀐다."""
    _skip_if_no_data()
    from app.modules.travel_ops.route_uses import problem
    lines = list(_runtime()._v.lo.doc["lines"])
    bad = [(ln, line_name(ln), problem(f"{line_name(ln)}:가")) for ln in lines if problem(f"{line_name(ln)}:가")]
    assert not bad, f"계약 밖 노선명: {bad}"
    assert len(lines) >= 20


def test_reasons_axis():
    """이유 = 축별 사실(실린 후보끼리) · 순위 말 없음 · 후보 하나면 비교 문구 없음."""
    _skip_if_no_data()
    _, got = _run()
    r = got["routes"]["gyeongbokgung_to_seongsu"]["options"]
    assert "소요 가장 짧음" in r[0]["label"] and "환승 가장 적음(1회)" in r[0]["label"], r[0]["label"]
    assert r[1]["label"].endswith("— 도보 거리 가장 짧음"), r[1]["label"]   # 전부 walk_m 이 있으면 거리로(GPT 23 #7)
    for rr in got["routes"].values():
        for o in rr["options"]:
            assert not any(w in o["label"] for w in ("추천", "순위", "1위", "최선", "best")), o["label"]
            if len(rr["options"]) == 1:
                assert "가장" not in o["label"], o["label"]


def test_reasons_unit():
    """혼잡 이유 — 극심 경고가 있는 후보 옆에서만 「혼잡 자료상 극심 구간 없음」, 혼잡 자료가 없는 후보(버스)는 말하지 않는다 · 9호선 단서."""
    from app.modules.travel_ops.mobility.engine.options import CAVEAT_LINE9, add_reasons
    base = {"eta_min": 30, "_transfers": 1, "_walk_min": 5, "_severe": [], "_covered": False, "_legs": []}
    opts = [dict(base, _route="A", _severe=["2호선 사당 출발 열차가 극심 혼잡(144.6%)이다"], _covered=True,
                 _legs=[{"line": "02호선"}]),
            dict(base, _route="B", _covered=True, _legs=[{"line": "09호선"}]),
            dict(base, _route="C", _legs=[{"mode": "bus"}])]
    add_reasons(opts)
    assert "극심 혼잡 구간 있음" in opts[0]["label"] and "극심 구간 없음" not in opts[0]["label"]
    assert "확인한 승차역 혼잡 자료에서 극심 없음" in opts[1]["label"] and CAVEAT_LINE9 in opts[1]["label"]
    assert opts[2]["label"] == "C", "혼잡 자료가 없는 후보에 붐빔 비교를 붙였다"
    one = [dict(base, _route="X")]
    add_reasons(one)
    assert one[0]["label"] == "X"


def test_walk_m_and_fare():
    """walk_m = 장소↔역(직선×우회) + 환승 거리표 m · 거리표 밖 환승이면 키를 뺀다 · 요금은 규칙 근거가 있을 때만(54 — 지하철 1,550)."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine.options import transfer_walk_m
    v = _runtime()._v
    _, got = _run()
    w = got["routes"]["dinner_to_lotte_mart"]["options"][0]
    assert w["id"] == "walk" and w["fare_krw"] == 0 and w["walk_m"] > 0
    from app.modules.travel_ops.mobility.engine.options import fare_of
    for rr in got["routes"].values():
        for o in rr["options"]:
            if o["id"].startswith("subway"):
                assert o.get("fare_krw") == 1550, f"도심 10km 안 지하철은 1,550(규칙 fare) — {o}"
            assert isinstance(o.get("fare_krw", 0), int)
    assert fare_of(v, [{"line": "09호선", "from": "노량진", "to": "신논현"}], [_FLR("09호선 노량진→신논현", 600)]) is None, \
        "거리 모르는 노선(9호선)은 요금을 뺀다"
    legs = [{"line": "03호선", "from": "경복궁", "to": "을지로3가"}, {"line": "02호선", "from": "을지로3가", "to": "성수"}]
    d = v.tw.lookup("을지로3가", "03호선", "02호선").distance_m
    assert transfer_walk_m(v, legs) == d
    assert transfer_walk_m(v, [{"line": "09호선", "from": "여의도", "to": "신논현"},
                               {"line": "신분당선", "from": "신논현", "to": "강남"}]) is None, "거리표 밖 환승은 모른다"
    assert transfer_walk_m(v, [{"mode": "bus", "route": "1", "from": "a", "to": "b"}]) is None


def test_no_per_option_departure():
    """결정 1 — 후보별 출발 칸 없음. 더 이른 출발이 필요한 후보는 options 에 없고 봉투 left_out 에 이유와 함께."""
    _skip_if_no_data()
    tr = []
    night = _two("2026-09-29T22:30:00+09:00", "2026-09-29T23:30:00+09:00", "2026-09-30T00:30:00+09:00")
    got = plan(_GS, night, 1, {}, runtime=_runtime(), modes=MODES, trace=tr)
    lo = got["left_out"]["g_to_s"]
    assert any(x["code"] == "earlier_departure" for x in lo), lo
    labels = {o["label"].split(" — ")[0] for o in got["routes"]["g_to_s"]["options"]}
    assert not labels & {x["label"] for x in lo}, "뺀 후보가 options 에 남았다"
    starts = {o["start_min"] for o in tr[0]["options"] if o["id"] != "walk"}
    assert len(starts) == 1, f"options 의 출발이 여럿이다: {starts}"
    assert set(got) == {"items", "routes", "skipped", "left_out", "basis"}


def test_bus_from_place_recheck():
    """결정 4 — 버스는 장소에서 바로 찾는다(이중 도보 없음). 출력 출발에서 판정기를 따로 불러 성립 · eta 일치."""
    _skip_if_no_data()
    tr = []
    items, got = _ns_it(modes=MODES_ALL, trace=tr)
    n = _recheck(got, tr, items, 1, {})
    assert n == 2, n
    chk = next(o["check"] for o in tr[0]["options"] if o["id"] == "bus_405")
    assert chk["walk_stop_in"] == 0 and chk["walk_stop_out"] == 0, "장소→정류장 한 번만 걷는다"


def test_transfer_car_display():
    """◆칸 — 기본 off(키 없음) · 켜면 46 조회 규칙(실제 역열 · 정확 일치). 방향이 반대면 다른 키가 나온다."""
    _skip_if_no_data()
    doc = json.loads(IN.read_text(encoding="utf-8"))
    off = plan(doc["places"], doc["items"], 2, doc.get("constraints"), runtime=_runtime(), modes=MODES)
    assert not any("transfer_car" in o for r in off["routes"].values() for o in r["options"])
    from app.modules.travel_ops.mobility.engine.options import TransferCar
    tc = TransferCar.load()
    if tc is None:
        return                                              # 46 산출이 없는 기기 — 표시 축만 SKIP
    E = json.loads((Path(__import__("app.modules.travel_ops.mobility.engine.paths", fromlist=["PROCESSED"]).PROCESSED)
                    / "mobility" / "transfer_car_v1.json").read_text(encoding="utf-8"))["entries"]
    on = plan(_GS, _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T13:30:00+09:00"),
              1, {}, runtime=_runtime(), modes=MODES, display=True)
    o = next(x for x in on["routes"]["g_to_s"]["options"] if x["uses"][1] == "3호선:을지로3가")
    # ★ 키 문자열의 셋째 칸은 「다음역」(열차가 환승역 다음에 설 역)이다 — 직전 역이 아니다. 필드로 찾는다(46 §3).
    def pick(st, ln, prev, to_ln, to_next):
        return next(e for e in E.values() if (e["station_nm"], e["line"], e["prev_nm"], e["to_line"], e["to_next_nm"])
                    == (st, ln, prev, to_ln, to_next))["positions"][0]["car_door"]
    want = pick("을지로3가", "03호선", "종로3가", "02호선", "을지로4가")
    assert o["transfer_car"] == [{"station": "을지로3가", "from_line": "3호선", "to_line": "2호선", "car_door": want}], o
    back = plan(list(reversed(_GS)), [{"seq": 1, "kind": "activity", "title": "B", "place": "s",
                                       "starts_at": "2026-09-29T10:00:00+09:00", "ends_at": "2026-09-29T11:00:00+09:00"},
                                      {"seq": 2, "kind": "activity", "title": "A", "place": "g",
                                       "starts_at": "2026-09-29T13:30:00+09:00"}],
                1, {}, runtime=_runtime(), modes=MODES, display=True)
    ob = next(x for x in back["routes"]["s_to_g"]["options"] if "2호선:을지로3가" in x["uses"])
    want_b = pick("을지로3가", "02호선", "을지로4가", "03호선", "종로3가")
    assert want != want_b
    assert ob.get("transfer_car") == [{"station": "을지로3가", "from_line": "2호선", "to_line": "3호선",
                                       "car_door": want_b}], ob
    assert tc.lookup("03호선", ["안국", "을지로3가"], "02호선", ["을지로3가", "을지로4가"]) is None, "폴백 금지 — 직전 역이 다르면 없다"


# ── GPT 대조 1차(23) 반영 ─────────────────────────────────────────────────
def test_make_id_same_route():
    """GPT 23 #9 — 같은 노선의 다른 승하차 두 후보 → bus_405 · bus_405_2."""
    from app.modules.travel_ops.mobility.engine.options import make_id
    taken = set()
    a = make_id([{"mode": "bus", "route": "405", "from": "a", "to": "b"}], taken)
    taken.add(a)
    b = make_id([{"mode": "bus", "route": "405", "from": "c", "to": "d"}], taken)
    assert (a, b) == ("bus_405", "bus_405_2")


class _D:
    def __init__(self, m, d="U", dest="x"):
        self.min, self.dir, self.dest = m, d, dest


class _Verd:
    def __init__(self, path):
        self.path = path


class _LR:
    def __init__(self, label, dep, arr, verdict="feasible"):
        self.label, self.depart_min, self.arrive_min, self.verdict = label, dep, arr, verdict


class _FakeV:
    """ridden_path 합성 시험용 — 판정기의 candidates()·travel_min_on_path() 모양만 흉내 낸다(간선 1개 = 2분)."""
    def __init__(self, deps):
        self.deps = deps
        self.lo = self

    def candidates(self, line, a, b, day_type):
        return [(d, _Verd(p), None) for d, p in self.deps], {}, False

    def travel_min_on_path(self, line, path, target):
        return 2 * path.index(target) if target in path else None


def test_ridden_path_synthetic():
    """GPT 23 #4 — 같은 분 다른 경로 → None · 다른 종착이지만 목적지까지 같은 역열 → 경로 · 다음 편(혼잡) → 그 분 · 맞는 편성 없음 → None."""
    from app.modules.travel_ops.mobility.engine.options import ridden_path
    leg = {"line": "02호선", "from": "A", "to": "C"}
    loop = _FakeV([(_D(10), ["A", "B", "C"]), (_D(10, "D"), ["A", "Y", "C"])])
    assert ridden_path(loop, leg, _LR("02호선 A→C", 10, 14), "weekday") is None
    branch = _FakeV([(_D(10, dest="X"), ["A", "B", "C", "X"]), (_D(10, dest="Z"), ["A", "B", "C", "Z"])])
    assert ridden_path(branch, leg, _LR("02호선 A→C", 10, 14), "weekday") == ["A", "B", "C"]
    nxt = _FakeV([(_D(10), ["A", "B", "C"]), (_D(13), ["A", "Q", "C"])])
    assert ridden_path(nxt, leg, _LR("02호선 A→C", 13, 17), "weekday") == ["A", "Q", "C"]
    assert ridden_path(nxt, leg, _LR("02호선 A→C", 13, 99), "weekday") is None


def test_ride_results_duplicate_label():
    """GPT 23 #5 — 같은 표기의 구간이 두 번이면 순서로 짝짓는다(첫 결과를 두 번 쓰지 않는다)."""
    from app.modules.travel_ops.mobility.engine.options import ride_results
    legs = [{"line": "02호선", "from": "A", "to": "B"}, {"line": "03호선", "from": "B", "to": "C"},
            {"line": "02호선", "from": "A", "to": "B"}]
    lrs = [_LR("02호선 A→B", 10, 12), _LR("환승 B", None, None), _LR("03호선 B→C", 15, 20),
           _LR("환승 C", None, None), _LR("02호선 A→B", 30, 32)]
    got = ride_results(legs, lrs)
    assert [x.depart_min for x in got] == [10, 15, 30]
    assert ride_results(legs, lrs[:3]) is None
    extra = [_LR("02호선 A→B", 10, 12), _LR("04호선 X→Y", 12, 13), _LR("03호선 B→C", 15, 20)]
    assert ride_results(legs[:2], extra) is None, "예상 밖 승차 결과를 건너뛰어 맞췄다(2차 #5)"


def test_transfer_car_collision():
    """GPT 23 #6 — 조회 키가 같은데 칸이 다르면 키를 뺀다(행 순서로 값이 바뀌면 안 된다) · 같으면 그대로."""
    from app.modules.travel_ops.mobility.engine.options import TransferCar
    base = {"station_nm": "S", "line": "L1", "prev_nm": "P", "to_line": "L2", "to_station_nm": "S", "to_next_nm": "N"}
    e1 = dict(base, next_nm="Q1", positions=[{"car_door": "1-1"}])
    e2 = dict(base, next_nm="Q2", positions=[{"car_door": "9-4"}])
    for order in ([e1, e2], [e2, e1]):
        tc = TransferCar({"entries": {str(i): e for i, e in enumerate(order)}})
        assert tc.lookup("L1", ["P", "S"], "L2", ["S", "N"]) is None
    same = TransferCar({"entries": {"a": e1, "b": dict(e2, positions=[{"car_door": "1-1"}])}})
    assert same.lookup("L1", ["P", "S"], "L2", ["S", "N"]) == [{"car_door": "1-1"}]


def test_left_out_rechecked():
    """GPT 23 #3 — 「더 일찍 떠나야 한다」로 뺀 후보는 계획 출발에서 판정기로 다시 봐도 성립하지 않는다(확인된 말만 한다)."""
    _skip_if_no_data()
    v = _runtime()._v
    n = 0
    for items, modes in ((_two("2026-09-29T22:30:00+09:00", "2026-09-29T23:30:00+09:00", "2026-09-30T00:30:00+09:00"), MODES),
                         (_two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T15:00:00+09:00"), MODES_ALL)):
        tr = []
        plan(_GS, items, 1, {}, runtime=_runtime(), modes=modes, trace=tr)
        for t in tr:
            for e in t["left_checks"]:
                if e["code"] != "earlier_departure" or e["check"] is None:
                    continue
                ck, start = e["check"], t["start_min"]
                r = v.verify_case({"id": "lo", "date": ck["date"], "legs": ck["legs"], "stage": "planning",
                                   "depart_at": start + ck["walk_place_in"] - ck["off"] + ck["walk_stop_in"],
                                   "arrive_by": ck["by_station"] - ck["walk_stop_out"], "party": {"size": 1},
                                   "first_visit": True, "no_alternatives": True})
                assert (r.out or {}).get("verdict") != "feasible", f"성립하는데 뺐다: {ck['legs']}"
                n += 1
    assert n >= 1


def test_bus_cap_after_eligibility():
    """GPT 23 #2 — 버스 상한은 자격 검사(앞 일정·uses) 뒤에 자른다. 도보 짧은 버스 셋이 앞 일정과 겹쳐도 넷째가 실린다."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine import plan as P
    rt = _runtime()
    pl = P.Planner(rt, modes=["bus"])
    arrive = datetime(2026, 9, 29, 15, 0, tzinfo=KST)
    sdate, by = P.service_day(arrive)
    real = pl._bus_direct(_NT[0], _NT[1], arrive, sdate, by, {}, True, "cap", pl._walk_limit({}))
    assert real, "버스 후보가 없다"
    doc = json.loads(IN.read_text(encoding="utf-8"))
    pp = {p["key"]: p for p in doc["places"]}
    a2 = datetime(2026, 9, 29, 19, 30, tzinfo=KST)
    s2, b2 = P.service_day(a2)
    many = pl._bus_direct(pp["dinner"], pp["lotte_mart"], a2, s2, b2, {}, True, "cap2", pl._walk_limit({}))
    bmax = rt._v.R["candidates"]["버스_직행_최대"]["value"]
    assert len(many) > bmax, "후보 단계에서 상한으로 자르면 자격 검사 뒤 보충이 안 된다 — 상한은 leg() 에서만"
    o = real[0]
    # 가짜 셋: 도보는 짧고, 역산 출발이 앞 일정과 겹치며, 판정기로 다시 봐도 모르는 노선(재판정 불통과)
    bad = dict(o["_check"], legs=[dict(o["_legs"][0], route="없는노선9")])
    fakes = [dict(o, _walk_m=10.0 + i, _start=o["_start"] - 30, _route=f"가짜{i}", _n=500 + i, _check=bad)
             for i in range(3)]
    fakes.append(dict(o, _walk_m=999.0, _route="진짜", _n=600))
    orig = P.Planner._bus_direct
    try:
        P.Planner._bus_direct = lambda self, *a, **k: [dict(x) for x in fakes]
        nb = datetime.combine(sdate, datetime.min.time(), tzinfo=KST) + timedelta(minutes=o["_start"] - 20)
        got, why = pl.leg(_NT[0], _NT[1], arrive, {}, True, "cap", not_before_dt=nb)
    finally:
        P.Planner._bus_direct = orig
    assert got is not None, why
    route = got[0]
    assert [x["label"].split(" — ")[0] for x in route["options"]] == ["진짜"], route
    assert {e["code"] for e in got[4]} == {"not_confirmed"}, got[4]


def test_recheck_revives():
    """GPT 23 2차 #1·#3 — 역산 출발이 앞 일정과 겹쳐도(더 이르게 나와도) 계획 출발에서 다시 보면 성립하는 후보는 살린다.
    살린 후보의 eta·편성·요금은 재판정 결과를 따른다(출력 eta = 독립 재판정 eta)."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine import plan as P
    rt = _runtime()
    pl = P.Planner(rt, modes=["bus"])
    arrive = datetime(2026, 9, 29, 15, 0, tzinfo=KST)
    sdate, by = P.service_day(arrive)
    o = pl._bus_direct(_NT[0], _NT[1], arrive, sdate, by, {}, True, "rv", pl._walk_limit({}))[0]
    early = dict(o, _start=o["_start"] - 30, eta_min=o["eta_min"] + 7, _route="살릴 후보", _n=700, _lr=None)
    orig = P.Planner._bus_direct
    tr = []
    try:
        P.Planner._bus_direct = lambda self, *a, **k: [dict(o), dict(early)]
        pl.trace = tr
        nb = datetime.combine(sdate, datetime.min.time(), tzinfo=KST) + timedelta(minutes=o["_start"] - 20)
        got, why = pl.leg(_NT[0], _NT[1], arrive, {}, True, "rv", not_before_dt=nb)
    finally:
        P.Planner._bus_direct = orig
    assert got is not None, why
    labels = {x["label"].split(" — ")[0]: x for x in got[0]["options"]}
    assert "살릴 후보" in labels, got
    assert labels["살릴 후보"]["eta_min"] == o["eta_min"], "살린 후보의 eta 가 재판정 값이 아니다"
    assert tr[0]["start_min"] == o["_start"]
    assert all(x["start_min"] == o["_start"] for x in tr[0]["options"])


def test_core_routes_strips_display():
    """GPT 23 #10 — display 로 뽑은 결과도 core_routes() 를 거치면 계약 칸만 남는다(= display 끈 결과)."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine.plan import core_routes
    items = _two("2026-09-29T10:00:00+09:00", "2026-09-29T11:00:00+09:00", "2026-09-29T13:30:00+09:00")
    on = plan(_GS, items, 1, {}, runtime=_runtime(), modes=MODES, display=True)
    off = plan(_GS, items, 1, {}, runtime=_runtime(), modes=MODES)
    assert any("transfer_car" in o for r in on["routes"].values() for o in r["options"]), "display 가 표시 필드를 안 냈다"
    assert core_routes(on["routes"]) == off["routes"]
    for r in core_routes(on["routes"]).values():
        for o in r["options"]:
            assert OPTION_KEYS <= set(o) <= OPTION_KEYS | OPTION_OPT


def test_replan_fare_known_locked():
    """54 — GPT 23 #8 잠금을 뒤집었다: 지하철·버스 옵션에 요금이 실려 코어 재계획(route_candidates)이 「요금을 몰라」로
    떨어뜨리지 않는다. 405 무정차 → 지하철이 대안으로 뽑히고 추가 비용 = 1,550 − 1,500 = 50(티머니 실측 환승 +50 과 같은 값)."""
    _skip_if_no_data()
    from app.modules.travel_ops.replan import choose, route_candidates
    items, got = _ns_it(modes=MODES_ALL)
    route = got["routes"]["ns_to_it"]
    fares = {o["id"]: o.get("fare_krw") for o in route["options"]}
    assert fares.get("bus_405") == 1500 and fares.get("subway_1") == 1550, fares
    mob = next(it for it in got["items"] if it["kind"] == "mobility")
    cands = route_candidates(route=route, depart=datetime.fromisoformat(mob["starts_at"]),
                             planned_arrival=datetime.fromisoformat(mob["ends_at"]),
                             next_start=datetime.fromisoformat(items[1]["starts_at"]),
                             events={"버스:405": {"effect": "skip_station", "summary": "405 무정차"}})
    sub = next(c for c in cands if c.key.startswith("subway"))
    assert not any("요금을 몰라" in r for r in sub.rejected), sub.rejected
    assert sub.extra_cost_krw == 50, sub.extra_cost_krw
    best, _alts, _rej = choose(cands)
    assert best is not None and best.key == sub.key, "요금이 들어왔으니 지하철이 대안으로 뽑혀야 한다"
    walk_route = {"from": "a", "to": "b", "planned": "walk",
                  "options": [{"id": "walk", "label": "도보", "eta_min": 5, "walk_m": 300, "fare_krw": 0, "uses": []}]}
    w = route_candidates(route=walk_route, depart=datetime(2026, 9, 29, 10, 0, tzinfo=KST),
                         planned_arrival=datetime(2026, 9, 29, 10, 5, tzinfo=KST),
                         next_start=datetime(2026, 9, 29, 10, 30, tzinfo=KST), events={})
    assert not w[0].rejected, w[0].rejected
    # 요금을 모르는 옵션은 여전히 떨어진다(키를 빼는 쪽이 맞는지 — 재계획 쪽 규칙은 그대로)
    unk = dict(route, options=[{k: v for k, v in o.items() if k != "fare_krw"} if o["id"].startswith("subway") else o
                               for o in route["options"]])
    c2 = route_candidates(route=unk, depart=datetime.fromisoformat(mob["starts_at"]),
                          planned_arrival=datetime.fromisoformat(mob["ends_at"]),
                          next_start=datetime.fromisoformat(items[1]["starts_at"]), events={})
    assert any("요금을 몰라" in r for c in c2 if c.key.startswith("subway") for r in c.rejected)


class _FakeCg:
    def __init__(self, hit):
        self.hit, self.by_key = hit, {1: 1}

    def __bool__(self):
        return True

    def lookup(self, line, station, dir, day_type, date, minute, levels, is_holiday=False):
        return self.hit


def test_congestion_checked_needs_cell():
    """GPT 23 #1 — 「혼잡 자료상 극심 구간 없음」은 **실제 탄 편성의 셀**(노선·역·방향·요일·30분)이 있을 때만.
    셀 없음 · 방향이 하나로 안 모임 · 지하철 밖 구간 → False."""
    from app.modules.travel_ops.mobility.engine.options import congestion_checked
    leg = {"line": "09호선", "from": "A", "to": "C"}
    lr = [_LR("09호선 A→C", 10, 14)]
    one = _FakeV([(_D(10, "U"), ["A", "B", "C"])])
    one.R, one.holidays = {"congestion": {"levels": {}}}, set()
    one.cg_data = _FakeCg((55.0, "보통", {}))
    assert congestion_checked(one, [leg], lr, date(2026, 9, 29), "weekday") is True
    one.cg_data = _FakeCg(None)
    assert congestion_checked(one, [leg], lr, date(2026, 9, 29), "weekday") is False, "셀이 없는데 확인했다고 했다"
    two = _FakeV([(_D(10, "U"), ["A", "B", "C"]), (_D(10, "D"), ["A", "B", "C"])])
    two.R, two.holidays, two.cg_data = one.R, set(), _FakeCg((55.0, "보통", {}))
    assert congestion_checked(two, [leg], lr, date(2026, 9, 29), "weekday") is False
    one.cg_data = _FakeCg((140.0, "극심", {}))
    assert congestion_checked(one, [leg], lr, date(2026, 9, 29), "weekday") is False, "극심 셀인데 극심 없음이라 했다"
    one.cg_data = _FakeCg((55.0, "보통", {}))
    assert congestion_checked(one, [{"mode": "bus", "route": "1", "from": "a", "to": "b"}], lr,
                              date(2026, 9, 29), "weekday") is False


def test_recheck_at_offsets():
    """GPT 23 2차 #3 — 재판정 성공 분기의 축 변환: 04:00 목표(역 도착이 전 운행일로 넘어감 · off −1440)와 자정 넘김.
    trace 의 후보를 그 출발에서 _recheck_at 으로 다시 보면 같은 출발·같은 eta 가 나와야 한다."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine import plan as P
    rt = _runtime()
    for items in (_two("2026-09-28T22:00:00+09:00", "2026-09-28T23:00:00+09:00", "2026-09-29T04:00:00+09:00"),
                  _two("2026-09-29T22:30:00+09:00", "2026-09-29T23:30:00+09:00", "2026-09-30T00:30:00+09:00")):
        tr = []
        plan(_GS, items, 1, {}, runtime=rt, modes=MODES, trace=tr)
        pl = P.Planner(rt, modes=MODES)
        t = tr[0]
        for x in t["options"]:
            if x["check"] is None:
                continue
            o = {"_check": x["check"], "_legs": x["check"]["legs"], "eta_min": -1, "_start": -1}
            got, why = pl._recheck_at(o, x["start_min"], t["arrive_by_min"], {"size": 1}, True, "off")
            assert got is not None, (why, x)
            assert got["_start"] == x["start_min"] and got["eta_min"] == x["eta_min"], (got["eta_min"], x)
        assert any(x["check"] and x["check"]["off"] == -1440 for x in t["options"]) or items[1]["starts_at"].endswith("00:30:00+09:00")


def test_bus_cap_zero_keeps_planned():
    """GPT 23 2차 #6 — 버스 상한이 0 이어도 계획 버스는 남는다(계획 버스 유지 후 남은 자리를 도보 순)."""
    _skip_if_no_data()
    R = _runtime()._v.R["candidates"]["버스_직행_최대"]
    old = R["value"]
    try:
        R["value"] = 0
        _, got = _ns_it(modes=MODES_ALL)
    finally:
        R["value"] = old
    r = got["routes"]["ns_to_it"]
    assert r["planned"] == "bus_405" and "bus_405" in {o["id"] for o in r["options"]}, r


# ── F 요금(54) ──────────────────────────────────────────────────────────────
class _FLR:
    """요금 시험용 구간 결과 — label·depart_min·wait_min 만."""
    def __init__(self, label, dep, wait=None, ride=None):
        self.label, self.depart_min, self.wait_min, self.ride_min = label, dep, wait, ride
        self.arrive_min, self.verdict = None, "feasible"


def _rules():
    from app.modules.travel_ops.mobility.engine.paths import RULES_DIR
    return json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))


def test_fare_rules_have_basis():
    """규칙 fare 절 — 값마다 grade·근거 · 확인 시각·출처 URL · 판정기 버전은 그대로(판정 무변경)."""
    R = _rules()
    F = R["fare"]
    # 54 는 판정 무변경이라 v0.9 를 유지했다 · 55(동명이역 · 판정 변경)가 v0.9.1 로 올렸다 — 두 판 번호가 같은 판을 가리키는지만 본다
    assert R["rules_version"] in ("v0.9", "v0.9.1") and R["source_id"] == f"mobility_rules@{R['rules_version']}", "판 번호 두 곳 일치"
    assert F["확인"]["checked_at"].startswith("2026-09-25") and all(u.startswith("https://") for u in F["확인"]["sources"])
    n = 0
    for sec in ("subway", "bus", "transfer"):
        for k, x in F[sec].items():
            assert {"value", "grade", "근거"} <= set(x), f"{sec}.{k}"
            assert x["grade"] in {"확정", "추정", "근거없음"}, f"{sec}.{k}"
            n += 1
    assert n >= 14


def test_fare_no_hardcode():
    """하드코딩 금지(27 규칙 14) — options.py 에 요금 숫자가 없다. 규칙 값을 바꾸면 결과가 따라 바뀐다."""
    import re
    from app.modules.travel_ops.mobility.engine import options as O
    src = Path(O.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(1550|1500|1400|1200|2500|3000|10000|5000|8000|50000|390)\b", src), "요금 값이 코드에 있다"
    F = _rules()["fare"]
    F["subway"]["base"]["value"]["won"] = 1650
    assert O.subway_fare_at(F, 5000, 600) == 1650


def test_fare_distance_steps():
    """거리 추가운임 경계 — 10km 까지 기본 · 5km 마다 100(올림) · 50km 넘으면 8km 마다 100 · 조조는 기본운임만 20%."""
    from app.modules.travel_ops.mobility.engine.options import subway_fare_at
    F = _rules()["fare"]
    want = {0: 1550, 10000: 1550, 10001: 1650, 15000: 1650, 15001: 1750, 50000: 2350, 50001: 2450,
            58000: 2450, 58001: 2550}
    for m, w in want.items():
        assert subway_fare_at(F, m, 600) == w, (m, subway_fare_at(F, m, 600), w)
    assert subway_fare_at(F, 5000, 389) == 1240, "06:29 승차는 조조 20%"
    assert subway_fare_at(F, 5000, 390) == 1550, "06:30 승차는 조조 아님(「06:30까지」 — 분 단위 390 미만)"
    assert subway_fare_at(F, 12000, 350) == 1340, "조조는 기본운임에만 — 거리 추가 100 은 그대로"


def test_fare_transfer_tmoney():
    """통합환승 합성 — 티머니 실측 태그(25 · 9/08~10 퇴근): 버스 1,500 → 지하철 +50 · 버스→버스 0.
    창 초과 · 광역·심야 · 거리 모름 · 6회 승차 → None."""
    from app.modules.travel_ops.mobility.engine.options import transfer_fare
    F = _rules()["fare"]
    bus = lambda typ, m, b, a: {"kind": typ, "base": F["bus"]["by_type"]["value"].get(typ), "m": m, "board": b, "alight": a}
    sub = lambda m, b, a: {"kind": "subway", "base": F["subway"]["base"]["value"]["won"], "m": m, "board": b, "alight": a}
    assert transfer_fare(F, [bus("지선", 4000, 1099, 1116), sub(2000, 1119, 1125)]) == 1550, "T10 버스→지하철 +50"
    assert transfer_fare(F, [bus("간선", 3000, 1162, 1173), bus("지선", 5000, 1176, 1201)]) == 1500, "T07 버스→버스 0"
    assert transfer_fare(F, [bus("지선", 6000, 600, 620), sub(6000, 630, 650)]) == 1650, "합산 12km → +100"
    assert transfer_fare(F, [bus("지선", 4000, 600, 620), sub(2000, 651, 660)]) is None, "낮 31분 → 환승 아님(값 안 냄)"
    assert transfer_fare(F, [bus("지선", 4000, 1300, 1320), sub(2000, 1370, 1380)]) == 1550, "21시 뒤 60분 창"
    assert transfer_fare(F, [bus("광역", 4000, 600, 620), sub(2000, 630, 640)]) is None, "광역 섞인 환승은 규칙 밖"
    assert transfer_fare(F, [bus("심야", 4000, 1500, 1520), bus("지선", 2000, 1530, 1540)]) is None
    assert transfer_fare(F, [bus("지선", None, 600, 620), sub(2000, 630, 640)]) is None, "버스 거리 모름"
    assert transfer_fare(F, [bus("지선", 30000, 600, 640), bus("간선", 26000, 650, 690)]) == 2500, \
        "GPT 54 #3 — 환승은 10km 넘으면 끝까지 5km 단계(56km → +1,000 · 지하철 단독 8km 단계 아님)"
    assert transfer_fare(F, [bus("지선", 100000, 600, 700), bus("간선", 100000, 710, 810)]) == 3000, \
        "GPT 54 #3 — 개별 요금 합(1,500 + 1,500)을 넘지 않는다"
    six = [bus("지선", 1000, 600 + 10 * i, 605 + 10 * i) for i in range(6)]
    assert transfer_fare(F, six[:5]) == 1500 and transfer_fare(F, six) is None, "5회 승차까지"


class _FBus:
    def __init__(self, typ, term):
        self.route_type_nm, self.term_min = typ, term


class _FFareV:
    """bus_fare 합성용 — R(규칙)·bus.route() 모양만."""
    def __init__(self, typ, term=10):
        self.R = _rules()
        self.bus = self
        self._r = _FBus(typ, term) if typ else None

    def route(self, name):
        return self._r


def test_fare_bus_single():
    """버스 한 번 — 유형별 단일요금 · 공항·투어·모르는 노선 → 없음 · 조조는 승차 창 [정류장 도착, +배차] 이 06:30 한쪽일 때만 · 심야 조조 없음."""
    from app.modules.travel_ops.mobility.engine.options import fare_of
    leg = [{"mode": "bus", "route": "405", "from": "a", "to": "b"}]
    lab = "버스 405 a→b"
    ok = lambda typ, dep, wait=3, term=10: fare_of(_FFareV(typ, term), leg, [_FLR(lab, dep, wait)])
    assert ok("간선", 600) == 1500 and ok("지선", 600) == 1500 and ok("순환", 600) == 1400
    assert ok("마을", 600) == 1200 and ok("광역", 600) == 3000 and ok("심야", 100 + 1440) == 2500
    assert ok("공항", 600) is None and ok("투어", 600) is None and ok(None, 600) is None
    assert ok("간선", 370, wait=3, term=10) == 1200, "도착 06:07 + 배차 10 < 06:30 → 조조"
    assert ok("간선", 390, wait=3, term=10) is None, "도착 06:27 — 승차가 06:30 앞뒤 어느 쪽인지 모른다"
    assert ok("간선", 400, wait=0, term=10) == 1500
    assert ok("심야", 300, wait=3, term=30) == 2500, "심야는 조조 대상 아님"
    assert fare_of(_FFareV("간선"), leg, [_FLR("버스 999 a→b", 600, 3)]) is None, "구간 결과가 안 맞으면 모른다"


def test_fare_mixed_is_unknown():
    """버스가 섞인 환승 — 버스 운임거리 근거가 없어(fare.transfer.bus_distance 근거없음) 값을 안 낸다."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine.options import fare_of
    v = _runtime()._v
    legs = [{"mode": "bus", "route": "405", "from": "a", "to": "b"}, {"line": "06호선", "from": "삼각지", "to": "이태원"}]
    lrs = [_FLR("버스 405 a→b", 600, 3), _FLR("06호선 삼각지→이태원", 620)]
    assert fare_of(v, legs, lrs) is None
    assert fare_of(v, legs[:1] * 2, lrs[:1] * 2) is None, "버스→버스도 거리 모름"


def test_fare_subway_bracket():
    """지하철 운임거리 괄호 — 카드는 승하차역만 안다 → 최단거리 요금. 하한 = 상한 요금일 때만 값.
    티머니 T16·T19(남부터미널→남성 1,550) 대조 · 최단보다 긴 후보도 같은 요금 · 거리 모르는 노선은 없음 · 경계에 걸리면 없음."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine import options as O
    v = _runtime()._v
    net = O.fare_net(v)
    f = lambda legs, dep=600: O.fare_of(v, legs, [_FLR(O.leg_txt(x), dep) for x in legs])
    t16 = [{"line": "03호선", "from": "남부터미널", "to": "고속터미널"}, {"line": "07호선", "from": "고속터미널", "to": "남성"}]
    assert f(t16, 1332) == 1550, "T16 22:12 남부터미널→남성 태그 1,550"
    short = [{"line": "03호선", "from": "경복궁", "to": "을지로3가"}, {"line": "02호선", "from": "을지로3가", "to": "성수"}]
    long_ = [{"line": "03호선", "from": "경복궁", "to": "충무로"}, {"line": "04호선", "from": "충무로", "to": "동대문역사문화공원"},
             {"line": "02호선", "from": "동대문역사문화공원", "to": "성수"}]
    assert net.ridden_m(long_) > 10000 >= net.upper_m(long_), "돌아가는 후보도 운임은 최단 기준"
    assert f(long_) == f(short) == 1550
    assert net.lower_m(short) <= net.upper_m(short)
    assert f([{"line": "09호선", "from": "노량진", "to": "신논현"}]) is None, "9호선 거리 없음"
    lb, ub = net.lower_m([{"line": "05호선", "from": "김포공항", "to": "광화문"}]), net.upper_m([{"line": "05호선", "from": "김포공항", "to": "광화문"}])
    assert O.subway_fare_at(v.R["fare"], lb, 600) != O.subway_fare_at(v.R["fare"], ub, 600)
    assert f([{"line": "05호선", "from": "김포공항", "to": "광화문"}]) is None, "괄호가 요금 경계를 가로지르면 뺀다"
    assert f(short, 350) == 1240, "조조"
    assert f(short, 395) is None, "GPT 54 #4 — 06:35 열차면 개찰이 06:30 전일 수 있다(창이 가로지름) → 뺀다"
    assert f(short, 420) == 1550
    via_sb = [{"line": "02호선", "from": "교대", "to": "강남"}, {"line": "신분당선", "from": "강남", "to": "양재"},
              {"line": "03호선", "from": "양재", "to": "매봉"}]
    assert net.ridden_m(via_sb) is None and net.upper_m(via_sb) is not None
    assert f(via_sb) is None, "GPT 54 #1 — 거리 모르는 노선(신분당선 · 별도운임)을 실제로 타면 다른 경로 상한으로 대신하지 않는다"
    assert O.fare_of(v, short, None) is None, "첫 승차 시각을 모르면 뺀다(조조를 가를 수 없다)"


def test_fare_ub_join_same_name_only():
    """상한 그래프의 환승은 서울교통공사 환승역거리 표의 쌍만 — 역명·좌표로 이으면 `경의선|양평` 좌표 결함(5호선 좌표)
    때문에 다른 역(경의중앙 양평 ↔ 5호선 양평)이 이어져 상한이 가짜로 짧아진다(54 발견)."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine import options as O
    v = _runtime()._v
    net = O.fare_net(v)
    a, b = ("경의선", "양평"), ("05호선", "양평")
    if a in net.lb and b in net.lb:
        assert b not in net.ub.get(a, {}), "양평(경의중앙) ↔ 양평(5호선) 은 다른 역이다"
    joined = {(x, y) for x in net.ub for y in net.ub[x] if x[0] != y[0]}
    pairs = {((r["from_line"], r["station_nm"]), (r["to_line"], r["station_nm"])) for r in v.tw.pairs.values()}
    assert joined and joined <= pairs | {(y, x) for x, y in pairs}


def test_fare_lb_unknown_zero():
    """GPT 54 #2 — 하한 그래프에서 거리 모르는 간선은 0(좌표 직선은 공표 거리보다 길 수 있어 하한이 아니다 —
    확정 간선 270개 중 96개가 직선 > 공표). 거리 확정 간선은 공표 값 그대로."""
    _skip_if_no_data()
    from app.modules.travel_ops.mobility.engine import options as O
    v = _runtime()._v
    net = O.fare_net(v)
    k = n = 0
    for ln, doc in v.lo.doc["lines"].items():
        for e in doc["edges"]:
            w = net.lb[(ln, e["a"])][(ln, e["b"])]
            if e.get("distance_m") is None:
                assert w == 0, (ln, e["a"], e["b"], w)
                k += 1
            else:
                assert w in (0, int(e["distance_m"])), (ln, e["a"], e["b"])
                n += 1
    assert k >= 400 and n >= 250


def test_fare_early_bird_gate():
    """GPT 54 #4 — 조조 기준은 개찰 태그. 태그 창 [열차 − 15, 열차] 이 06:30(390) 을 가로지르면 모른다."""
    from app.modules.travel_ops.mobility.engine.options import early_bird
    W = _rules()["fare"]["subway"]["early_bird_gate_window"]["value"]
    assert early_bird(389, 390, W) is True
    assert early_bird(390, 390, W) is None and early_bird(390 + W - 1, 390, W) is None
    assert early_bird(390 + W, 390, W) is False


if __name__ == "__main__":
    fails, skips, n = 0, 0, 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            n += 1
            try:
                fn()
                print(f"  ok   {name}")
            except _Skip:
                skips += 1
                print(f"  SKIP {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name}: {e}")
            except Exception as e:                  # 예외도 실패로 센다 — 한 시험이 터져 뒤 시험이 안 도는 일이 없게
                fails += 1
                print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"[plan] {n - fails - skips}/{n} 통과 · SKIP {skips} · 실패 {fails}")
    sys.exit(1 if fails else 0)
