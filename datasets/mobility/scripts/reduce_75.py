# -*- coding: utf-8 -*-
"""75번 방 ④ 줄인 판 만들기 — 정본 `DATA_DIR\\travel\\processed\\mobility`(읽기만) → 저장소 `datasets/mobility/processed/mobility/`.

자리(9/29 팀장 폴더 배정 · 서버 뜰 때까지 임시): 이 스크립트는 `datasets/mobility/scripts/` 에 있고, 산출 기본값은
`datasets/mobility/processed/mobility/` 다. 그 폴더는 팀 `.gitignore` 가 막으므로 **`git add -f`** 로 추적한다(README 참조).

원칙(75 첫 메시지 · 92차):
  · 정본 무변경 — 여기서는 읽기만 한다
  · 열 제거는 판정기 읽기 코드로 확인한 열만(`verify_time.Timetable.load` L132~156 · 8열)
  · **행 삭제 금지** — `--drop-no-dep` 는 기본 off(후보 · 회귀 171 확인 뒤에만)
  · gz 는 마지막 수단 — 줄인 뒤에도 50 MB 를 넘는 파일만 `.gz` 를 **옆에** 만든다(둘 다 두고 MANIFEST 에 표시 ·
    git 에 무엇을 올릴지는 73 뒤 팀장 판 읽기 코드(#63 .gz 읽기)에 맞춰 정한다)

실행(노트북 PowerShell · 표준 라이브러리만 · 46만 행이라 1~2분):
    python datasets/mobility/scripts/reduce_75.py            (저장소 루트에서 · 정본 위치는 .env DATA_DIR)
옵션:
    --src  <DATA_DIR>\\travel\\processed\\mobility            (기본 · .env 의 DATA_DIR)
    --dst  <repo>\\datasets\\mobility\\processed\\mobility     (기본 · 있으면 안 지우고 멈춘다 · --clean 이면 비우고 다시)
    (기본 = 9/30 채택안) 50 MB 넘는 파일은 .gz 로 교체(판정기 `paths.timetable_file` 이 .gz 우선) · 시간표 출발없음 행(18,411 · 시·종착역 표기)과
    9호선 혼잡도 급행 행(2,432 · congestion.py 가 local 만 씀)을 뺀다 — 회귀 게이트·전체층·pytest 가 정본 판과 같은 값(p4 · 9/30)
    --no-gz / --keep-no-dep / --keep-express   각각 끈다(비교 시험용)
산출:
    <dst>\\**                       줄인 판(A 그대로 · B 열 제거) + 보고서 md
    <dst>\\MANIFEST_git_v1.json      파일 · 크기 · sha256 · 행수 · 원자료 · 확인 시각 · 전/후
    <dst>\\..\\size_table_75.md       전/후 크기표(datasets/mobility/processed/)
"""
import argparse
import gzip
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]                       # datasets/mobility/scripts → 저장소 루트
DEFAULT_DST = REPO_ROOT / "datasets" / "mobility" / "processed" / "mobility"


def default_src():
    """정본 위치 — 저장소 맨 위 .env 의 DATA_DIR(없으면 None → 인자 필수)."""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass
    d = os.environ.get("DATA_DIR")
    return str(Path(d) / "travel" / "processed" / "mobility") if d else None


GZ_LINE = 50 * 1024 * 1024          # GitHub 경고선(파일당). 100 MB 는 거부선

# ── 시간표에서 판정기가 읽는 열(verify_time.py Timetable.load · 2026-09-29 확인) ──
TIMETABLE_KEEP = ["line", "station_nm", "day_type", "dep_time", "dir", "dest_nm", "dest_inferred", "fetched_at"]
#  line/station_nm  : 키 (L140) · fetched_at : 첫 행에서 읽어 evidence observed_at 에 씀(L144 · L1439 — 어느 행이 첫 행이 될지
#  모르므로 전 행에 둔다) · dep_time : to_min (L147) · day_type/dir/dest_nm/dest_inferred : Dep (L151)

# ── 분류표 · 원자료 · 재생성 스크립트(datasets/mobility/scripts/… · 82 자리) ──
A = "A(그대로)"
B = "B(열 제거)"
FILES = [
    # path(상대 · /), 분류, 원자료, 재생성 스크립트, 읽는 코드
    ("timetable_v1.jsonl", B, "서울 열린데이터광장 OA-101(seoul_timetable_*.jsonl) + 국토부 TAGO(tago_timetable.jsonl) · raw\\mobility\\",
     "datasets/mobility/scripts/build_timetable_v1.py → fill_timetable_dest_v1.py(28 · dest_inferred)", "engine/verify_time.py Timetable.load · runtime.py"),
    ("timetable_v1_meta.json", A, "build_timetable_v1.py 가 시간표와 같이 씀", "datasets/mobility/scripts/build_timetable_v1.py", "engine/runtime.py(built_at)"),
    ("line_station_order_v1.json", A, "국가철도공단 표준데이터 FR_CODE + 시간표 관측 + 서울교통공사 역간거리 CSV(54)",
     "datasets/mobility/scripts/build_line_station_order_v1.py", "engine/line_order.py · candidates.py"),
    ("transfer_walk_v1.json", A, "서울교통공사_환승역거리 소요시간 정보_20251231.csv", "datasets/mobility/scripts/build_transfer_walk_v1.py", "engine/transfer_walk.py"),
    ("bus_route_v1.jsonl", A, "서울시 버스 정보 API(seoul_bus_all_routes.json · 캐시)", "datasets/mobility/scripts/seoul_bus_find_routes.py → build_bus_all_v1.py(45)", "engine/bus.py"),
    ("bus_stops_v1.jsonl", A, "서울시 버스 정보 API(seoul_bus_all_stops.json · 캐시)", "datasets/mobility/scripts/build_bus_all_v1.py", "engine/bus.py · geo.py"),
    ("station_coords.json", A, "국가철도공단 전체_도시철도역사정보_20260630.xlsx + OA-15442 stations_all.json + datasets/mobility/scripts/station_coord_fix.json · station_nm_en_fix.json",
     "datasets/mobility/scripts/station_coords_build.py(57)", "engine/geo.py"),
    ("station_exits_v1.json", A, "OSM 지하철 출구(raw\\mobility\\osm\\osm_subway_entrances_sudogwon_raw.json)", "datasets/mobility/scripts/build_station_exits_v1.py", "engine/exits.py"),
    ("bike_stations_v1.jsonl", A, "서울 OA-21235 master + OA-15493 bikeList + OA-13252 xlsx(raw\\mobility\\bike\\)", "datasets/mobility/scripts/build_bike_stations_v1.py", "engine/bike.py"),
    ("bus_seg_profile_v1.jsonl.gz", A, "서울 OA-21217 노선별 구간 운행시간 zip 9(raw\\mobility\\bus_speed\\ · 8.7M 행)", "datasets/mobility/scripts/build_bus_seg_profile_v1.py", "engine/bus_profile.py"),
    ("congestion_v1.jsonl", A, "서울교통공사_지하철혼잡도정보_20260630.csv", "datasets/mobility/scripts/congestion_build.py", "engine/congestion.py"),
    ("congestion_line9_v1.jsonl", A, "서울 OA-22197 9호선 혼잡도 xlsx(raw\\mobility\\congestion_line9\\)", "datasets/mobility/scripts/congestion_build.py --line9 (25)", "engine/congestion.py"),
    ("transfer_car_v1.json", A, "국토부 15151816 + 서울교통공사 15098252(raw\\mobility\\car_position\\)", "(재생성 스크립트 저장소 밖 — 표시 영구 off · 백업 _backup\\removed_root_20260930\\ · 82 결정 6)", "engine/options.py TransferCar(display off)"),
    ("graph/topis_class_factor_v1.json", A, "TOPIS 속도 xlsx(raw\\mobility\\topis\\)", "datasets/mobility/scripts/graph_03b_profile.py(18 · cwd 실행)", "engine/car.py CarGraph"),
    ("graph/topis_link_profile_v1.jsonl.gz", A, "TOPIS 속도 xlsx 2025-09~2026-05", "datasets/mobility/scripts/graph_03b_profile.py(18 · cwd 실행)", "engine/car.py CarGraph"),
    ("graph/osm_way_seg_topis_link_v1.csv", A, "OSM pbf + TOPIS 링크 형상 매칭", "datasets/mobility/scripts/graph_02b_match.py(18 · cwd 실행)", "engine/car.py CarGraph"),
    ("graph/osm_way_geom_v1.csv", A, "OSM pbf", "datasets/mobility/scripts/graph_01_geom.py(18 · cwd 실행)", "engine/car.py CarGraph"),
    ("graph/daytype_calendar_v1.csv", A, "holidays KR + 정정(34)", "datasets/mobility/scripts/graph_03b_profile.py(18 · cwd 실행)", "engine/car.py CarGraph"),
    # 101(2026-10-05 · 합치기) 서울 차도·자전거·**걸음** 그래프 v2(팀장 build_road_graph_v2.py · v1 을 대신한다 — v1 3파일은 내렸다 ·
    #   이미 gz · md5 는 road_graph_v2/MANIFEST.json 과 대조). 택시·자전거·걷기 길찾기(engine/graph_router.py)가 읽는다.
    ("road_graph_v2/nodes.jsonl.gz", A, "Geofabrik south-korea-latest.osm.pbf(raw\\mobility\\osm\\ · OSM 2026-09-18 · md5 b4aac996… · ODbL)",
     "datasets/mobility/scripts/build_road_graph_v2.py(팀장 · build_road_graph_v1.py 를 불러 쓴다)", "engine/graph_router.py — 서버 없는 파이썬 길찾기"),
    ("road_graph_v2/edges.jsonl.gz", A, "같은 pbf", "datasets/mobility/scripts/build_road_graph_v2.py", "engine/graph_router.py"),
    ("road_graph_v2/region_v1.geojson", A, "같은 pbf 의 행정경계 relation(서울+인접 8+영종구+공항고속도로 회랑 · v1 과 같은 범위)", "datasets/mobility/scripts/build_road_graph_v2.py", "범위 판단(스크립트)"),
    # 101 — OSM 선로 길이로 낸 역간 거리 추정(팀장 #21 · 공표 역간거리가 없는 간선의 요금 거리)
    ("rail_edge_track_v1.jsonl.gz", A, "같은 pbf 의 railway=subway|rail|light_rail 선로 + line_station_order_v1.json + station_coords.json",
     "datasets/mobility/scripts/build_rail_edge_distance_v1.py(팀장)", "engine/options.py(fare.subway.distance_estimate · 추정)"),
    # 54-2(2026-10-05) — 공표 역간거리 표(국가철도공단 CSV 17 + 김포골드라인). ★엔진이 아직 읽지 않는다(요금 거리 원천 순서는 다음 합치기 방)
    ("station_gap_v1.jsonl", A, "공공데이터포털 국가철도공단 역간거리 CSV 17개(raw\\mobility\\station_gap\\) + 김포골드라인 운영사 누적 km",
     "datasets/mobility/scripts/build_station_gap_v1.py(54-2)", "(아직 없음 — 다음 합치기 방에서 engine/options.py 가 읽게 한다)"),
]
# 정본 폴더의 자체 MANIFEST(md5) 와 대조할 폴더
SUB_MANIFESTS = {"road_graph_v2": "road_graph_v2/MANIFEST.json"}
# 부속 보고서(작은 md · 파일별 커버리지·등급 근거) — 코드는 안 읽는다
REPORTS = ["timetable_v1_coverage.md", "timetable_v1_destfill_report.md", "line_station_order_v1_report.md",
           "transfer_walk_v1_report.md", "bus_route_v1_report.md", "station_coords_report.md",
           "bike_stations_v1_report.md", "bus_seg_profile_v1_report.md", "congestion_v1_report.md",
           "congestion_line9_v1_report.md", "transfer_car_v1_report.md", "graph/README.md",
           "road_graph_v2/README.md", "road_graph_v2/MANIFEST.json", "road_graph_v2/build_report.json"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def md5sum(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reduce_congestion_line9(src: Path, dst: Path, drop_express: bool):
    """9호선 혼잡도 — 열 그대로. --drop-express 면 service=express 행을 뺀다(congestion.py loader 가 local 만 쓴다)."""
    n_in = n_out = n_express = 0
    with open(src, encoding="utf-8") as fi, open(dst, "w", encoding="utf-8", newline="\n") as fo:
        for raw in fi:
            s = raw.strip()
            if not s:
                continue
            n_in += 1
            r = json.loads(s)
            if r.get("service") not in (None, "local"):
                n_express += 1
                if drop_express:
                    continue
            fo.write(raw if raw.endswith("\n") else raw + "\n")
            n_out += 1
    return {"rows_in": n_in, "rows_out": n_out, "rows_express": n_express,
            "rows_dropped_express": (n_express if drop_express else 0)}


def count_rows(p: Path) -> int:
    op = gzip.open if p.suffix == ".gz" else open
    n = 0
    with op(p, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def fmt(n):
    return f"{n / 1048576:,.1f} MB" if n >= 1048576 else f"{n / 1024:,.1f} KB"


def reduce_timetable(src: Path, dst: Path, drop_no_dep: bool):
    """열 8개만 남긴다. 행 순서·행 수 그대로(--drop-no-dep 아니면). 줄 끝 \\n · ensure_ascii=False · 구분자 최소."""
    keep = TIMETABLE_KEEP
    seen_keys = {}
    n_in = n_out = n_no_dep = 0
    dropped_cols = set()
    with open(src, encoding="utf-8") as fi, open(dst, "w", encoding="utf-8", newline="\n") as fo:
        for raw in fi:
            s = raw.strip()
            if not s:
                continue
            r = json.loads(s)
            n_in += 1
            for k in r:
                seen_keys[k] = seen_keys.get(k, 0) + 1
                if k not in keep:
                    dropped_cols.add(k)
            # 판정기 to_min 과 같은 뜻: None · '' · '000000' · '0' = 출발 없음(시·종착역 표기)
            dep = r.get("dep_time")
            no_dep = dep is None or str(dep).strip() in ("", "000000", "0")
            if no_dep:
                n_no_dep += 1
                if drop_no_dep:
                    continue
            o = {k: r.get(k) for k in keep}
            fo.write(json.dumps(o, ensure_ascii=False, separators=(",", ":")) + "\n")
            n_out += 1
    return {"rows_in": n_in, "rows_out": n_out, "rows_no_dep": n_no_dep,
            "rows_dropped_no_dep": (n_no_dep if drop_no_dep else 0),
            "columns_kept": keep, "columns_dropped": sorted(dropped_cols),
            "columns_seen": seen_keys}


def gzip_file(p: Path) -> Path:
    g = p.with_name(p.name + ".gz")
    with open(p, "rb") as fi, gzip.open(g, "wb", compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=default_src(), help="정본 processed/mobility (기본 .env DATA_DIR)")
    ap.add_argument("--dst", default=str(DEFAULT_DST))
    ap.add_argument("--clean", action="store_true")
    # 9/30 채택안이 기본(p4 검증: 회귀 게이트 22 · 전체층 156 · pytest 게이트 210+1 · 전체층 185 — 정본 판과 같은 값)
    ap.add_argument("--no-gz", dest="gz", action="store_false", help="50 MB 넘는 파일도 텍스트로 둔다(기본: .gz 로 교체)")
    ap.add_argument("--gz", dest="gz", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--keep-no-dep", dest="drop_no_dep", action="store_false", help="출발없음 행을 남긴다(기본: 뺀다)")
    ap.add_argument("--keep-express", dest="drop_express", action="store_false", help="9호선 급행 행을 남긴다(기본: 뺀다)")
    ap.add_argument("--drop-no-dep", dest="drop_no_dep", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--drop-express", dest="drop_express", action="store_true", help=argparse.SUPPRESS)
    ap.set_defaults(gz=True, drop_no_dep=True, drop_express=True)
    a = ap.parse_args()
    if not a.src:
        print("! --src 를 주거나 .env 에 DATA_DIR 을 넣어라"); sys.exit(2)
    src, dst = Path(a.src).resolve(), Path(a.dst).resolve()
    # 9/30 사고(p3): DATA_DIR 이 저장소 datasets/ 를 가리킨 채 돌아 정본을 못 찾고 --clean 이 git 폴더를 비웠다 — 먼저 멈춘다
    if not (src / "timetable_v1.jsonl").exists():
        print(f"! 정본 시간표가 없다: {src / 'timetable_v1.jsonl'} — --src 로 드라이브 정본 processed\\mobility 를 준다"); sys.exit(2)
    if (REPO_ROOT / "datasets") in src.parents:
        print(f"! --src 가 저장소 datasets/ 안이다({src}) — 정본(드라이브 DATA_DIR)을 준다"); sys.exit(2)
    if dst.exists() and any(dst.iterdir()):
        if not a.clean:
            print(f"! {dst} 가 비어 있지 않다 — --clean 을 주면 비우고 다시 만든다")
            sys.exit(2)
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    entries = []
    tot_before = tot_after = 0

    for rel, cls, raw_src, regen, reader in FILES:
        sp, dp = src / rel, dst / rel
        if not sp.exists():
            print(f"  ! 없음: {rel}")
            entries.append({"path": rel, "class": cls, "missing": True})
            continue
        dp.parent.mkdir(parents=True, exist_ok=True)
        e = {"path": rel, "class": cls, "source_raw": raw_src, "regen_script": regen, "read_by": reader,
             "src_bytes": sp.stat().st_size, "src_sha256": sha256(sp), "checked_at": now}
        if rel == "timetable_v1.jsonl":
            e["reduce"] = reduce_timetable(sp, dp, a.drop_no_dep)
        elif rel == "congestion_line9_v1.jsonl":
            e["reduce"] = reduce_congestion_line9(sp, dp, a.drop_express)
        else:
            shutil.copyfile(sp, dp)
        e["text_bytes"] = dp.stat().st_size
        e["text_sha256"] = sha256(dp)
        if rel.endswith((".jsonl", ".jsonl.gz", ".csv")):
            e["rows"] = count_rows(dp) - (1 if rel.endswith(".csv") else 0)   # csv 는 머리글 제외
        e["gz"] = None
        if e["text_bytes"] > GZ_LINE:
            e["over_50mb"] = True
        if e["text_bytes"] > GZ_LINE and a.gz:
            g = gzip_file(dp)
            dp.unlink()                                   # 텍스트 판은 남기지 않는다(git 에는 .gz 만)
            e["gz"] = {"path": rel + ".gz", "bytes": g.stat().st_size,
                       "reason": f"줄인 뒤에도 {fmt(e['text_bytes'])} > 50 MB(GitHub 경고선) → .gz 로 교체 · 판정기 paths.timetable_file 이 .gz 우선"}
            e["path"] = rel + ".gz"
            dp = g
        e["bytes"] = dp.stat().st_size
        e["sha256"] = sha256(dp)
        e["md5"] = md5sum(dp)
        tot_before += e["src_bytes"]
        tot_after += e["bytes"]
        print(f"  {cls:8s} {e['path']:42s} {fmt(e['src_bytes']):>10s} → {fmt(e['bytes']):>10s}"
              + (f"  (텍스트 {fmt(e['text_bytes'])} → gz)" if e["gz"] else "") + (f"  rows={e['rows']:,}" if "rows" in e else ""))
        entries.append(e)

    # 정본 폴더 자체 MANIFEST(76 등)와 md5 대조
    sub_checks = []
    for sub, mrel in SUB_MANIFESTS.items():
        mp = src / mrel
        if not mp.exists():
            continue
        doc = json.loads(mp.read_text(encoding="utf-8"))
        for fn, info in (doc.get("files") or {}).items():
            e = next((x for x in entries if x.get("path") == f"{sub}/{fn}"), None)
            ok = bool(e) and e.get("md5") == info.get("md5")
            sub_checks.append({"path": f"{sub}/{fn}", "expected_md5": info.get("md5"), "got_md5": e.get("md5") if e else None,
                               "expected_rows": info.get("rows"), "got_rows": e.get("rows") if e else None, "ok": ok})
            print(f"  md5 {sub}/{fn}: {'OK' if ok else '!! MISMATCH'} ({info.get('md5')})")

    reports = []
    for rel in REPORTS:
        sp, dp = src / rel, dst / rel
        if sp.exists():
            dp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sp, dp)
            reports.append({"path": rel, "bytes": dp.stat().st_size, "sha256": sha256(dp)})

    manifest = {
        "manifest_version": "git_v1", "generated_at": now, "room": 75,
        "src_dir": str(src), "dst_dir": str(dst),
        "rule": {"columns_only_verified_by_loader": True,
                 "row_deletion": {"drop_no_dep": a.drop_no_dep, "drop_express": a.drop_express, "policy": "only when regression values are identical"},
                 "gz_threshold_bytes": GZ_LINE, "gz_policy": "files still > 50 MB after column reduction are replaced by .gz (--gz); loader reads .gz first"},
        "totals": {"src_bytes": tot_before, "dst_bytes": tot_after},
        "files": entries, "reports": reports, "sub_manifest_checks": sub_checks,
    }
    (dst / "MANIFEST_git_v1.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# 75 크기표 전/후 — {now}", "", f"정본 합계(올리는 파일만) {fmt(tot_before)} → 줄인 판 {fmt(tot_after)}", "",
             "| 파일 | 분류 | 전 | 후 | 행 |", "|---|---|---:|---:|---:|"]
    for e in entries:
        if e.get("missing"):
            lines.append(f"| `{e['path']}` | {e['class']} | 없음 | | |")
            continue
        rows = f"{e['rows']:,}" if isinstance(e.get("rows"), int) else ""
        after = fmt(e["bytes"]) + (f"(텍스트 {fmt(e['text_bytes'])})" if e.get("gz") else "")
        lines.append(f"| `{e['path']}` | {e['class']} | {fmt(e['src_bytes'])} | {after} | {rows} |")
    for c in sub_checks:
        lines.append(f"- md5 대조 `{c['path']}`: {'일치' if c['ok'] else '불일치'} (기대 {c['expected_md5']} · 행 {c['expected_rows']})")
    tt = next((e for e in entries if e["path"].startswith("timetable_v1.jsonl")), None)
    if tt and "reduce" in tt:
        r = tt["reduce"]
        lines += ["", f"시간표: 행 {r['rows_in']:,} → {r['rows_out']:,}(출발없음 {r['rows_no_dep']:,}행 · 뺀 행 {r['rows_dropped_no_dep']:,}) · "
                  f"남긴 열 {r['columns_kept']} · 뺀 열 {r['columns_dropped']}"]
    c9 = next((e for e in entries if e["path"] == "congestion_line9_v1.jsonl"), None)
    if c9 and "reduce" in c9:
        r = c9["reduce"]
        lines += [f"9호선 혼잡도: 행 {r['rows_in']:,} → {r['rows_out']:,}(급행 {r['rows_express']:,}행 · 뺀 행 {r['rows_dropped_express']:,})"]
    (dst.parent / "size_table_75.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n합계 {fmt(tot_before)} → {fmt(tot_after)}")
    print(f"MANIFEST: {dst / 'MANIFEST_git_v1.json'} · 크기표: {dst.parent / 'size_table_75.md'}")


if __name__ == "__main__":
    main()
