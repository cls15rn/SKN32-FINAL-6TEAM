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

★라우터(GraphHopper)는 **항상 끈다**(`gh_url="none"`) — 기기에 라우터가 있어도 안 잡는다(재현성). alt 묶음의 `expect_taxi` 4건은
  CLI `--allow-router-down` 과 같이 SKIP 으로 세고 통과시킨다. 라우터 값은 종전대로 ps1/CLI 로 GH 있는 기기에서 본다.
  자동차는 합성 경로 픽스처(`car_routes_fixture_v1.json`) · 자전거는 GH 요약 픽스처(`bike_gh_fixture_v1.json`).

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

# ── 14묶음 — 파일 + CLI 인자(scratch\_69\s7_resolve_run.ps1 (8) 구간과 같은 조합) ──────────────
BUNDLES = {
    "synthetic":   {"file": "synthetic_legs_v1.json", "timetable": "mini_timetable_v2.jsonl.gz"},   # #63(9/29) 압축 판 · Timetable.load 가 .gz 읽음
    "real":        {"file": "real_legs_v1.json"},
    "issue":       {"file": "issue_legs_v1.json"},
    "alt":         {"file": "alt_legs_v1.json", "allow_router_down": True},
    "bus":         {"file": "bus_legs_v1.json"},
    "mixed":       {"file": "mixed_legs_v1.json"},
    "multi":       {"file": "multi_legs_v1.json"},
    "car":         {"file": "car_legs_v1.json", "gh_url": "fixture:car_routes_fixture_v1.json"},
    "bike":        {"file": "bike_legs_v1.json", "bike_fixture": "bike_gh_fixture_v1.json"},
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
N_ALL, N_GATE = 191, 21          # 정본 숫자(80 기준 회귀 191 = 87 의 186 + 80 R-BRANCH-01~05 · 게이트 21) — 케이스를 더하면 여기도 올린다. 검사는 test_gate_list_is_consistent 에서


# ── 판정기: 적재 조건(시간표·라우터·자전거 픽스처·버스 프로파일)이 같은 케이스는 한 판정기를 나눠 쓴다 ────────
#   묶음마다 따로 올리면 197 MB 시간표를 묶음 수만큼 훑는다(게이트 10묶음 ≈ 100 초). 조건이 같으면 wanted(케이스가
#   쓰는 노선·역 집합)만 합집합이 되고 판정은 같다 — CLI `--case <id>` 가 케이스 하나만으로 적재해도 같은 값이 나오는 것과
#   같은 이유. 게이트는 판정기 2개(실데이터 1 · 합성 1), 전체층은 5개(기본 · 합성 · 자동차 픽스처 · 자전거 픽스처 · 프로파일 없음).
_CACHE = {}
LOAD_KEYS = ("timetable", "gh_url", "bike_fixture", "bus_profile")


def _processed():
    # #48(9/29 · 팀장): paths 는 import 때 .env 를 안 읽는다 — 시험은 cli_processed() 로 저장소 맨 위 .env 의 DATA_DIR 을 받는다
    #   (환경변수 DATA_DIR 이 있으면 그것이 이김 · 서버가 configure/disable 한 뒤에는 건드리지 않음).
    from app.modules.travel_ops.mobility.engine.paths import cli_processed
    return cli_processed() / "mobility"


def _skip_if_missing(bundle):
    need = NEED_SYNTH if bundle == "synthetic" else NEED_REAL
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
    # ★ 라우터(GraphHopper)는 pytest 에서 **항상 끈다**(GPT 대조 ①). 안 끄면 .env 의 MOBILITY_GH_URL 이나 규칙의 주소로
    #   살아 있는 라우터를 잡아 기기마다 결과가 달라진다. 라우터 값(alt 의 expect_taxi 등)은 종전대로 ps1/CLI 로 GH 있는
    #   기기에서 본다. 자동차 픽스처(fixture:)만 예외.
    gh = b.get("gh_url") or "none"
    if gh.startswith("fixture:"):
        gh = "fixture:" + str(HERE / gh[len("fixture:"):])
    return SimpleNamespace(
        cases=str(HERE / b["file"]), case=None,
        timetable=fx("timetable"), order=None, transfer_walk=None, bus_route=None, bus_stops=None,
        station_coords=None, station_exits=None, bike_stations=None,
        bike_fixture=fx("bike_fixture"), bike_live="none", bike_record=None,
        bus_profile=b.get("bus_profile"), congestion=None,
        rules=str(RULES_DIR / "rules_v0.3.json"), holidays=str(RULES_DIR / "holidays_2026_2027.json"),
        graph_dir=None, gh_url=gh, allow_router_down=bool(b.get("allow_router_down")),
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
    miss, skipped = check_expect(case, r, BUNDLES[bundle].get("allow_router_down", False))
    assert not miss, (f"[{case_id}] {case.get('note', '')[:120]}\n"
                      + "\n".join(f"  MISS 기대 {e} → 실제 {g}" for _i, e, g in miss)
                      + (f"\n  (라우터 없음 SKIP: {[a for _i, a in skipped]})" if skipped else ""))


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
