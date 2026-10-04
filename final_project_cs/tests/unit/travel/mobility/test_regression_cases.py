# -*- coding: utf-8 -*-
"""이동 모듈 회귀(171 → 78 에서 177) 를 pytest 로 — 71번 방(2026-09-29).

★무엇을 보나: 케이스 파일(`*_legs_v1.json` · 14묶음 · 177건(78))의 **입력 칸**을 판정기(`verify_time.Verifier`)에 넣고
  결과를 **기대 칸(`expect…`)** 과 대조한다. 대조 함수는 CLI `--check-expect` 와 같은 `verify_time.check_expect` 다 —
  비교 칸·문구가 두 군데 생기지 않는다. 케이스 본문도 여기 없다 — 파일을 그대로 읽는다.
  칸 설명·묶음별 뜻·깨졌을 때 읽는 순서는 이 폴더 `README.md`.

★두 층(conftest.py):
  · **게이트**(기본 실행) — `regression_gate_v1.json` 의 21건. 「주요 기능마다 하나 · 깨지면 그 기능이 죽은 것」.
    시험 id 는 케이스 id(예: `test_gate[R-LAST-01]`) · 실패 메시지 첫 줄에 기능 이름과 「깨지면 무엇」이 찍힌다.
  · **전체**(`-m "mobility_full and not live"`) — 나머지 150건. 방을 닫을 때 우리가 돈다.

★데이터: 실데이터 묶음은 `DATA_DIR/travel/processed/mobility/` 가 있어야 한다(git 밖 · 드라이브 zip). 없으면
  **skip(사유 "data not present")** — 코드가 깨진 게 아니라 데이터가 없는 것. 합성 묶음(`synthetic`)은 시간표만
  저장소 안 `mini_timetable_v2.jsonl.gz` 로 바꾸고 역 순서표·환승표는 여전히 DATA_DIR 에서 읽는다(0단계 문서 §5).
  수집(collection) 단계에서는 DATA_DIR 을 건드리지 않는다(27번 규칙 26) — 데이터 없는 CI 에서 수집 오류 0.

★도로 경로(택시·자동차) — 99(2026-10-04) 부터 경로 서버가 없다. 묶음마다 `road_graph` 로 정한다:
  · alt = `auto`(저장소 안 차도 그래프 `road_graph_v1` · 파이썬 라우터) — `expect_taxi` 4건의 도착·요금을 **실제로 대조한다**
    (앞 판은 서버 없는 기기에서 SKIP 으로 세고 넘어갔다). 차도 그래프·도로 소요 자료가 없으면 묶음째 skip.
  · car = `fixture:car_routes_fixture_v1.json`(합성 경로 대역) — 도로급 커버 경고·골목 과반·라우터 못 닿음처럼 실제 그래프로는
    만들 수 없는 상황을 잠근다. 실제 경로 계산 결과가 아니다.
  · 그 밖 = `none` — 택시 대안은 근거없음으로 나온다(이 묶음들은 택시 값을 대조하지 않는다 · 적재 13초·0.2 GB 를 안 쓴다).
    명령줄(`python -m …verify_time --check-expect`)은 기본이 켠 채(auto)다 — 켜도 이 묶음들의 기대는 같다(95 · 99 확인).
  자전거 승차 소요는 근거없음이다(자전거 경로 계산 없음) — 자전거 픽스처는 없다.

실행(final_project_cs 에서):
  python -m pytest tests/unit/travel/mobility -q                      # 게이트(+가벼운 단위)
  python -m pytest tests/unit/travel/mobility -m "mobility_full and not live" -q     # 전체(팀 pytest.ini 의 not live 를 유지)
  python -m pytest tests/unit/travel/mobility/test_regression_cases.py -q -k "R-LOOP-01"   # 게이트 한 건
  python -m pytest tests/unit/travel/mobility/test_regression_cases.py -q -m mobility_full -k "BIKE-01"   # 전체층 한 건(-m 없으면 deselect 돼 exit 5)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))          # final_project_cs — 다른 시험 파일과 같은 방식

#: alt 묶음(택시 값 대조)이 더 읽는 파일 — 차도 그래프 + 도로 소요 자료. 없으면 skip(99).
NEED_ROAD = ("road_graph_v1/edges.jsonl.gz", "graph/topis_class_factor_v1.json")

# ── 14묶음 — 파일 + CLI 인자(scratch\_69\s7_resolve_run.ps1 (8) 구간과 같은 조합) ──────────────
BUNDLES = {
    "synthetic":   {"file": "synthetic_legs_v1.json", "timetable": "mini_timetable_v2.jsonl.gz"},   # #63(9/29) 압축 판 · Timetable.load 가 .gz 읽음
    "real":        {"file": "real_legs_v1.json"},
    "issue":       {"file": "issue_legs_v1.json"},
    "alt":         {"file": "alt_legs_v1.json", "road_graph": "auto", "need": NEED_ROAD},
    "bus":         {"file": "bus_legs_v1.json"},
    "mixed":       {"file": "mixed_legs_v1.json"},
    "multi":       {"file": "multi_legs_v1.json"},
    "car":         {"file": "car_legs_v1.json", "road_graph": "fixture:car_routes_fixture_v1.json"},
    "bike":        {"file": "bike_legs_v1.json"},
    "judgment":    {"file": "judgment_legs_v1.json"},
    "night":       {"file": "night_legs_v1.json"},
    "bus_profile": {"file": "bus_profile_legs_v1.json"},
    "bus_noprof":  {"file": "bus_noprof_legs_v1.json", "bus_profile": "none"},
    "destfill":    {"file": "destfill_legs_v1.json"},
}
FILE2BUNDLE = {b["file"]: name for name, b in BUNDLES.items()}

#: 실데이터 묶음이 반드시 읽는 파일(없으면 skip). 합성 묶음은 시간표 대신 역 순서표·환승표만.
NEED_REAL = ("timetable_v1.jsonl", "line_station_order_v1.json", "transfer_walk_v1.json",
             "bus_route_v1.jsonl", "bus_stops_v1.jsonl", "station_coords.json")
NEED_SYNTH = ("line_station_order_v1.json", "transfer_walk_v1.json")


def _read_cases(file):
    doc = json.loads((HERE / file).read_text(encoding="utf-8"))
    return doc["cases"] if isinstance(doc, dict) else doc


# ── 수집 단계: 케이스 파일(저장소 안)만 읽는다 ─────────────────────────────────────────────
GATE = json.loads((HERE / "regression_gate_v1.json").read_text(encoding="utf-8"))["gate"]
GATE_KEYS = {(g["file"], g["id"]) for g in GATE}
ALL = [(file, c["id"]) for file in (b["file"] for b in BUNDLES.values()) for c in _read_cases(file)]
FULL = [k for k in ALL if k not in GATE_KEYS]
N_ALL, N_GATE = 199, 21          # 정본 숫자(92 기준 회귀 199 = 80 의 191 + 92 R-EXPRESS-01~08 · 게이트 21) — 케이스를 더하면 여기도 올린다. 검사는 test_gate_list_is_consistent 에서


# ── 판정기: 적재 조건(시간표·도로 경로·버스 프로파일)이 같은 케이스는 한 판정기를 나눠 쓴다 ────────
#   묶음마다 따로 올리면 197 MB 시간표를 묶음 수만큼 훑는다(게이트 10묶음 ≈ 100 초). 조건이 같으면 wanted(케이스가
#   쓰는 노선·역 집합)만 합집합이 되고 판정은 같다 — CLI `--case <id>` 가 케이스 하나만으로 적재해도 같은 값이 나오는 것과
#   같은 이유. 게이트는 판정기 2개(실데이터 1 · 합성 1), 전체층은 5개(기본 · 합성 · 자동차 픽스처 · 택시 대안(차도 그래프) · 프로파일 없음).
_CACHE = {}
LOAD_KEYS = ("timetable", "road_graph", "bus_profile")


def _processed():
    # #48(9/29 · 팀장): paths 는 import 때 .env 를 안 읽는다 — 시험은 cli_processed() 로 저장소 맨 위 .env 의 DATA_DIR 을 받는다
    #   (환경변수 DATA_DIR 이 있으면 그것이 이김 · 서버가 configure/disable 한 뒤에는 건드리지 않음).
    from app.modules.travel_ops.mobility.engine.paths import cli_processed
    return cli_processed() / "mobility"


def _skip_if_missing(bundle):
    need = (NEED_SYNTH if bundle == "synthetic" else NEED_REAL) + tuple(BUNDLES[bundle].get("need") or ())
    p = _processed()
    # 75(9/30): 저장소 데이터의 실 시간표는 `.gz` — 판정기와 같은 규칙(paths.timetable_file · .gz 우선)으로 있는지 본다
    from app.modules.travel_ops.mobility.engine.paths import timetable_file
    missing = [n for n in need if not ((timetable_file(p) if n == "timetable_v1.jsonl" else p / n).exists())]
    if missing:
        pytest.skip(f"data not present: {p} — {missing}")


def _load_cfg(bundle):
    b = BUNDLES[bundle]
    return tuple(b.get(k) for k in LOAD_KEYS)


def _args(bundle):
    b = BUNDLES[bundle]
    from app.modules.travel_ops.mobility.engine.verify_time import RULES_DIR
    fx = lambda k: str(HERE / b[k]) if b.get(k) else None            # noqa: E731
    # ★ 도로 경로는 묶음이 정한다(머리말) — 안 적은 묶음은 끈다(none). 환경변수 MOBILITY_ROAD_GRAPH 는 보지 않는다(재현성).
    road = b.get("road_graph") or "none"
    if road.startswith("fixture:"):
        road = "fixture:" + str(HERE / road[len("fixture:"):])
    return SimpleNamespace(
        cases=str(HERE / b["file"]), case=None,
        timetable=fx("timetable"), order=None, transfer_walk=None, bus_route=None, bus_stops=None,
        station_coords=None, station_exits=None, bike_stations=None,
        bike_live="none",
        bus_profile=b.get("bus_profile"), congestion=None,
        rules=str(RULES_DIR / "rules_v0.3.json"), holidays=str(RULES_DIR / "holidays_2026_2027.json"),
        graph_dir=None, road_graph=road,
        check_expect=True, verbose=False, json=None)


def _verifier(bundle, layer_ids):
    """이 묶음과 적재 조건이 같은 묶음들의(이 층) 케이스를 합쳐 판정기 하나를 만든다."""
    cfg = _load_cfg(bundle)
    key = (cfg, id(layer_ids))
    if key not in _CACHE:
        from app.modules.travel_ops.mobility.engine.verify_time import build_verifier_for_cases
        cases = []
        for name, b in BUNDLES.items():
            if _load_cfg(name) == cfg and b["file"] in layer_ids:
                cases += [c for c in _read_cases(b["file"]) if c["id"] in layer_ids[b["file"]]]
        v, _ctx = build_verifier_for_cases(_args(bundle), cases)
        _CACHE[key] = v
    return _CACHE[key]


def _run(file, case_id, layer_ids):
    """케이스 하나를 CLI 와 같은 방식으로 판정·대조한다. MISS 가 있으면 실패."""
    from app.modules.travel_ops.mobility.engine.verify_time import check_expect
    bundle = FILE2BUNDLE[file]
    _skip_if_missing(bundle)
    v = _verifier(bundle, layer_ids)
    case = next(c for c in _read_cases(file) if c["id"] == case_id)
    r = v.verify_case(case)
    miss, _skipped = check_expect(case, r)
    assert not miss, (f"[{case_id}] {case.get('note', '')[:120]}\n"
                      + "\n".join(f"  MISS 기대 {e} → 실제 {g}" for _i, e, g in miss))


def _by_file(keys):
    out = {}
    for f, i in keys:
        out.setdefault(f, set()).add(i)
    return out


_GATE_IDS = _by_file(GATE_KEYS)
_FULL_IDS = _by_file(FULL)


@pytest.mark.parametrize("g", GATE, ids=[g["id"] for g in GATE])
def test_gate(g):
    """게이트 — 주요 기능 하나 = 케이스 하나. 실패 메시지 첫 줄이 「깨지면 무엇이 잘못된 것」이다."""
    try:
        _run(g["file"], g["id"], _GATE_IDS)
    except AssertionError as e:
        raise AssertionError(f"[기능] {g['feature']} — 깨지면: {g['broken']}\n{e}") from None


@pytest.mark.mobility_full
@pytest.mark.parametrize("file,case_id", FULL, ids=[f"{FILE2BUNDLE[f]}-{i}" for f, i in FULL])
def test_full(file, case_id):
    """전체층 — 게이트에 없는 나머지 150건. `-m mobility_full` 로만 돈다."""
    _run(file, case_id, _FULL_IDS)


def test_gate_list_is_consistent():
    """게이트 목록·케이스 파일의 정합성(데이터 없이 돈다) — GPT 대조 ⑤: 중복 id · 포함 관계 · 두 층의 합/교집합."""
    assert len(ALL) == N_ALL, len(ALL)
    assert len(ALL) == len(set(ALL)), "케이스 id 가 파일 안에서 겹친다 — _run 이 첫 케이스만 고른다"
    assert len(GATE) == len(GATE_KEYS) == N_GATE, len(GATE)
    for g in GATE:
        assert (g["file"], g["id"]) in set(ALL), g
        assert g["feature"] and g["broken"], g
    assert GATE_KEYS.isdisjoint(FULL) and len(GATE) + len(FULL) == N_ALL
