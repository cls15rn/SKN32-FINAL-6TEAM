# -*- coding: utf-8 -*-
"""이동 계산기 점검(2026-09-29) — 설정·적재·정책 수치·자료 확인 수정 회귀(#24·#25·#32·#48·#49).

실제 시간표(약 195MB) 없이, 임시 폴더에 **아주 작은 가공 자료**(역 4개 · 열차 몇 편 · 버스 1노선)를 만들어
`build_verifier` 적재 경로를 통째로 돈다.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.modules.travel_ops.mobility.engine import guardrails as G
from app.modules.travel_ops.mobility.engine import paths
from app.modules.travel_ops.mobility.engine.paths import RULES_DIR

ENGINE = Path(paths.__file__).resolve().parent
CS_ROOT = ENGINE.parents[4]


def _write_mini_data(root: Path, built_at="2026-09-20T00:00:00+09:00"):
    m = root / "travel" / "processed" / "mobility"
    m.mkdir(parents=True)
    rows = []
    for k, st in enumerate(["A", "B", "C"]):
        for h in range(6, 23):
            rows.append({"line": "01호선", "station_nm": st, "day_type": "weekday", "dep_time": f"{h:02d}:{2 * k:02d}:00",
                         "dir": "D", "dest_nm": "D", "fetched_at": built_at})
    (m / "timetable_v1.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    line = {"stations": [{"station_nm": n, "fr_order": i} for i, n in enumerate("ABCD")],
            "edges": [{"a": a, "b": b, "grade": "확정", "travel_min": 2.0} for a, b in zip("ABC", "BCD")],
            "dir_label": {"reliable": True}, "is_loop": False}
    (m / "line_station_order_v1.json").write_text(json.dumps({"built_at": built_at, "dest_alias": {},
                                                              "lines": {"01호선": line}}), encoding="utf-8")
    (m / "transfer_walk_v1.json").write_text(json.dumps({"built_at": built_at, "pairs": {}, "stations": {}}), encoding="utf-8")
    (m / "bus_route_v1.jsonl").write_text(json.dumps({"route_id": "R1", "route_nm": "101", "route_type_nm": "간선",
                                                      "term_min": 10, "first_time": "05:00", "last_time": "23:00",
                                                      "service_days": "daily"}), encoding="utf-8")
    (m / "bus_stops_v1.jsonl").write_text("\n".join(json.dumps({"route_id": "R1", "seq": i, "station_nm": f"정{i}",
                                                               "lat": 37.5 + i / 1000, "lng": 127.0, "sect_dist_m": 400})
                                                   for i in range(1, 4)), encoding="utf-8")
    coords = {f"01호선|{n}": {"station_key": f"01호선|{n}", "line": "01호선", "station_nm": n,
                              "lat": 37.5 + i / 100, "lng": 127.0} for i, n in enumerate("ABCD")}
    (m / "station_coords.json").write_text(json.dumps({"stations": coords, "built_at": built_at}), encoding="utf-8")
    (m / "timetable_v1_meta.json").write_text(json.dumps({"built_at": built_at}), encoding="utf-8")
    return m


@pytest.fixture
def mini(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SOURCE", paths.SOURCE)          # 시험 뒤 되돌린다
    before = (paths.SOURCE, paths.DATA_DIR)
    _write_mini_data(tmp_path)
    yield tmp_path
    paths._layout(before[1], before[0])


# ── #48 import 때 .env 를 읽지 않는다 ───────────────────────────────
def test_48_paths_module_does_not_load_dotenv_at_import():
    tree = ast.parse((ENGINE / "paths.py").read_text(encoding="utf-8"))
    top_calls = [n for n in tree.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and getattr(n.value.func, "id", "") == "load_dotenv"]
    top_imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "dotenv"]
    assert not top_calls and not top_imports, "모듈 맨 위에서 dotenv 를 부르지 않는다"


def test_48_fresh_import_is_unset_and_leaves_environ_alone():
    env = {k: v for k, v in os.environ.items() if k != "DATA_DIR"}
    code = ("import os; before = dict(os.environ); "
            "import app.modules.travel_ops.mobility.engine.paths as p; "
            "print(p.SOURCE, dict(os.environ) == before)")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=CS_ROOT, env=env, capture_output=True, text=True)
    assert out.stdout.split() == ["unset", "True"], out.stderr


def test_48_cli_processed_reads_env_only_when_nobody_configured(tmp_path):
    """점검 스크립트 길 — 아무도 안 정했으면 DATA_DIR 을 따르고, 서버가 끈 뒤에는 새지 않는다."""
    env = dict(os.environ, DATA_DIR=str(tmp_path))
    code = ("import app.modules.travel_ops.mobility.engine.paths as p; a = p.cli_processed(); s1 = p.SOURCE; "
            "p.disable(); b = p.cli_processed(); print(a, s1, b, p.SOURCE, sep='|')")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=CS_ROOT, env=env, capture_output=True, text=True)
    a, s1, b, s2 = out.stdout.strip().split("|")
    assert Path(a) == tmp_path / "travel" / "processed" and s1 == "cli_env", out.stderr
    assert Path(b) == paths.UNSET_DIR / "travel" / "processed" and s2 == "disabled"


def test_48_configure_refuses_empty_and_sets_source(mini):
    with pytest.raises(ValueError):
        paths.configure("")
    paths.configure(mini)
    assert paths.SOURCE == "settings" and paths.PROCESSED == mini / "travel" / "processed"


# ── #49 정책 수치는 guardrails.yaml 한 곳 ───────────────────────────
MOVED = [("buffer", "by_stage", "planning"), ("buffer", "by_stage", "in_progress"),
         ("limits", "transfers", "default"), ("limits", "walk_m", "infant_or_luggage"),
         ("staleness", "timetable_warn_days")]


def test_49_rules_json_holds_pointers_not_numbers():
    R = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
    for path in MOVED:
        node = R
        for k in path:
            node = node[k]
        assert node["value"] is None and node["value_from"].startswith("guardrails:mobility."), path


def test_49_verifier_reads_values_from_guardrails(tmp_path):
    from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier
    R = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
    v = Verifier(Timetable(), None, R, set())
    assert v.rv("buffer", "by_stage", "planning") == 10 and v.rv("limits", "walk_m", "default") == 1200
    alt = tmp_path / "g.yaml"
    alt.write_text("mobility:\n  buffer:\n    by_stage:\n      planning: 25\n", encoding="utf-8")
    try:
        G.use(alt)
        assert v.rv("buffer", "by_stage", "planning") == 25, "guardrails 값을 바꾸면 엔진이 따른다"
        with pytest.raises(G.GuardrailMissing):
            v.rv("limits", "walk_m", "default")              # 조용히 옛 값을 쓰지 않는다
    finally:
        G.use(G.DEFAULT_PATH)


# ── #24 · #25 · #32 적재 · 자료 확인 · 노후 · 다시 올리기 ─────────────
def test_24_build_verifier_on_mini_data(mini):
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    rt = build_verifier(quiet=True, data_dir=mini, seoul_key="")
    assert rt.stats["timetable_stations"] == 3 and rt.stats["data_dir_source"] == "settings"
    assert rt.stats["bike_live"] is False and "bike_router" not in rt.stats, "빈 값은 끔 — 환경변수로 새지 않는다 · 자전거 라우터 칸은 없다(99)"
    r = rt.verify_case({"id": "t", "date": "2026-10-07", "depart_at": "10:00", "legs": [
        {"line": "01호선", "from": "A", "to": "C"}], "no_alternatives": True})
    assert r.verdict == "feasible", r.reason


def test_24_missing_data_names_the_folder(tmp_path, monkeypatch):
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    before = (paths.SOURCE, paths.DATA_DIR)
    try:
        with pytest.raises(RuntimeError, match="자료 폴더"):
            build_verifier(quiet=True, data_dir=tmp_path, seoul_key="")
    finally:
        paths._layout(before[1], before[0])


def test_25_manifest_detects_changed_file(mini):
    from app.modules.travel_ops.mobility.engine import datacheck
    paths.configure(mini)
    assert datacheck.check()["ok"]
    datacheck.write_manifest()
    assert datacheck.check()["ok"], "명세를 쓴 직후는 맞다"
    tt = mini / "travel" / "processed" / "mobility" / "timetable_v1.jsonl"
    tt.write_text(tt.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    bad = datacheck.check()
    assert not bad["ok"] and bad["mismatched"][0]["file"] == "timetable"


def test_32_stale_timetable_is_flagged(tmp_path):
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    before = (paths.SOURCE, paths.DATA_DIR)
    _write_mini_data(tmp_path, built_at="2026-01-01T00:00:00+09:00")
    try:
        rt = build_verifier(quiet=True, data_dir=tmp_path, seoul_key="")
        assert rt.timetable_stale and rt.stats["timetable_age_days"] > 30
    finally:
        paths._layout(before[1], before[0])


def _iso_days_ago(n):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=n)).isoformat(timespec="seconds")


@pytest.mark.parametrize("built_ago, tago_ago, seoul_ago, stale", [
    (1, 40, 40, True),      # 89: 옛 원자료를 어제 다시 빌드 — 수집이 40일 전이면 오래됐다(앞 판은 built_at 을 봐 신선으로 냈다)
    (40, 2, 2, False),      # 빌드는 오래됐어도 원천을 이틀 전에 받았으면 신선
    (1, 2, 40, True),       # 원천 중 가장 오래된 것이 기준(서울 보충분만 낡아도 경고)
])
def test_89_staleness_uses_collection_date_not_build_time(tmp_path, built_ago, tago_ago, seoul_ago, stale):
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    before = (paths.SOURCE, paths.DATA_DIR)
    m = _write_mini_data(tmp_path, built_at=_iso_days_ago(built_ago))
    (m / "timetable_v1_meta.json").write_text(json.dumps({
        "built_at": _iso_days_ago(built_ago),
        "tago_fetched_at": _iso_days_ago(tago_ago)[:10], "seoul_fetched_at": _iso_days_ago(seoul_ago)[:10]}), encoding="utf-8")
    try:
        rt = build_verifier(quiet=True, data_dir=tmp_path, seoul_key="")
        assert rt.timetable_stale is stale
        assert rt.stats["timetable_age_basis"] == "meta_fetched"
        assert rt.timetable_built_at.startswith("built:"), "판 표시(built_at)는 그대로 — 나이 기준만 바뀐다"
    finally:
        paths._layout(before[1], before[0])


def test_89_staleness_falls_back_to_row_fetched_at(tmp_path):
    """meta 에 수집일이 없으면 행 fetched_at — 만든 시각(built_at)으로 신선하다고 하지 않는다."""
    from app.modules.travel_ops.mobility.engine.runtime import build_verifier
    before = (paths.SOURCE, paths.DATA_DIR)
    m = _write_mini_data(tmp_path, built_at="2026-01-01T00:00:00+09:00")          # 행 fetched_at = 1/1
    (m / "timetable_v1_meta.json").write_text(json.dumps({"built_at": _iso_days_ago(1)}), encoding="utf-8")
    try:
        rt = build_verifier(quiet=True, data_dir=tmp_path, seoul_key="")
        assert rt.timetable_stale and rt.stats["timetable_age_basis"] == "row_fetched"
    finally:
        paths._layout(before[1], before[0])


def test_32_get_verifier_reloads_when_source_changes(mini, monkeypatch):
    from app.modules.travel_ops.mobility.engine import runtime as RT
    monkeypatch.setattr(RT, "_SINGLETON", None)
    first = RT.get_verifier(quiet=True, data_dir=mini, seoul_key="")
    assert RT.get_verifier() is first, "바뀐 것이 없으면 같은 판"
    tt = mini / "travel" / "processed" / "mobility" / "timetable_v1.jsonl"
    time.sleep(0.05)
    tt.write_text(tt.read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(tt, (time.time() + 5, time.time() + 5))
    second = RT.get_verifier()
    assert second is not first, "시간표 파일이 바뀌면 다시 올린다"
    monkeypatch.setattr(RT, "_SINGLETON", None)


# ── #63 시험용 축소 시간표를 압축해 둔다 — 로더가 .gz 도 읽는다 ─────────────
def test_63_timetable_loads_gzip_same_as_plain(tmp_path):
    import gzip
    from app.modules.travel_ops.mobility.engine.verify_time import Timetable
    rows = [{"line": "01호선", "station_nm": "A", "day_type": "weekday", "dep_time": "09:00:00", "dir": "down",
             "dest_nm": "D", "fetched_at": "2026-09-09"},
            {"line": "01호선", "station_nm": "B", "day_type": "weekday", "dep_time": None, "dir": "down",
             "dest_nm": "D", "fetched_at": "2026-09-09"}]
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    (tmp_path / "t.jsonl").write_text(text, encoding="utf-8")
    with gzip.open(tmp_path / "t.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(text)
    a, b = Timetable.load(tmp_path / "t.jsonl"), Timetable.load(tmp_path / "t.jsonl.gz")
    assert (a.rows, a.skipped_no_dep, a.stations, dict(a.by_key)) == (b.rows, b.skipped_no_dep, b.stations, dict(b.by_key))
    assert b.rows == 1 and b.skipped_no_dep == 1


def test_63_committed_mini_timetable_is_gzip_and_readable():
    from app.modules.travel_ops.mobility.engine.verify_time import Timetable
    here = Path(__file__).resolve().parent
    assert not (here / "mini_timetable_v2.jsonl").exists(), "평문 20MB 판은 압축본으로 바뀌었다"
    tt = Timetable.load(here / "mini_timetable_v2.jsonl.gz", wanted={("05호선", "여의도")})
    assert tt.rows > 0 and tt.fetched_at == "2026-09-09"
