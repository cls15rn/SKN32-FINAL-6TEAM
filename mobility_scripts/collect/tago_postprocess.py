# mobility_scripts/collect/tago_postprocess.py — TAGO 시간표 수집 결과 후처리 (수집 스크립트 v1 산출물용)
# 실행: 저장소 루트에서  python mobility_scripts/collect/tago_postprocess.py [--fetched-at 2026-09-09]
#   ① tago_station_ids.json 에서 매칭 0개 역명 → tago_unmatched.json (표기 보정표 재료)
#   ② tago_timetable.jsonl 중복 제거 + 행마다 fetched_at·source 부여 → processed/mobility/tago_timetable_v1.jsonl
#   ③ tago_run_meta.json — 소스·수집 시각·역/조합/행 수·빈 조합(완료했지만 행이 0인 역ID|요일|방향)
# fetched_at 은 행 단위 실제 호출 시각이 없으므로 "수집일" 정밀도다(precision=day). Evidence.observed_at 에 그대로 쓴다.
import sys, json, argparse
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
SOURCE = "data.go.kr/1613000/SubwayInfo"
ID_MAP = RAW_MOBILITY / "tago_station_ids.json"
TIMETABLE = RAW_MOBILITY / "tago_timetable.jsonl"
DONE = RAW_MOBILITY / "tago_timetable_done.json"
UNMATCHED = RAW_MOBILITY / "tago_unmatched.json"
META = RAW_MOBILITY / "tago_run_meta.json"
OUT_DIR = PROCESSED / "mobility"
OUT = OUT_DIR / "tago_timetable_v1.jsonl"

ap = argparse.ArgumentParser()
ap.add_argument("--fetched-at", help="수집일 YYYY-MM-DD (기본: jsonl 파일 수정 시각의 날짜)")
args = ap.parse_args()

# ① 미매칭 역명
id_map = json.loads(ID_MAP.read_text(encoding="utf-8"))
unmatched = sorted(nm for nm, its in id_map.items() if not its)
UNMATCHED.write_text(json.dumps(unmatched, ensure_ascii=False, indent=1), encoding="utf-8")
targets = {it["subwayStationId"]: {"name": nm, "route": it.get("subwayRouteName")}
           for nm, its in id_map.items() for it in its}
print(f"① 역명 {len(id_map)}개 중 미매칭 {len(unmatched)}개 → {UNMATCHED.name} / TAGO 역 ID {len(targets)}개")

# ② 중복 제거 + fetched_at·source
if args.fetched_at:
    fetched_at = args.fetched_at
else:
    fetched_at = datetime.fromtimestamp(TIMETABLE.stat().st_mtime, KST).strftime("%Y-%m-%d")
OUT_DIR.mkdir(parents=True, exist_ok=True)
seen, rows, dup, combos = set(), 0, 0, set()
with TIMETABLE.open(encoding="utf-8") as f, OUT.open("w", encoding="utf-8") as g:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        key = (r["station_id"], r["daily_type"], r["updown"], r.get("dep_time"), r.get("arr_time"),
               r.get("end_station_id"), r.get("route_id"))
        if key in seen:
            dup += 1; continue
        seen.add(key)
        combos.add("|".join((r["station_id"], r["daily_type"], r["updown"])))
        r["route_nm"] = targets.get(r["station_id"], {}).get("route")
        r["fetched_at"] = fetched_at
        r["fetched_at_precision"] = "day"
        r["source"] = SOURCE
        g.write(json.dumps(r, ensure_ascii=False) + "\n"); rows += 1
print(f"② 행 {rows}개 기록 (중복 제거 {dup}개) → {OUT}")

# ③ 메타
done = set(json.loads(DONE.read_text(encoding="utf-8"))) if DONE.exists() else set()
empty = sorted(done - combos)
per_route = {}
for sid in {c.split("|")[0] for c in combos}:
    per_route[targets.get(sid, {}).get("route") or "?"] = per_route.get(targets.get(sid, {}).get("route") or "?", 0) + 1
meta = {
    "source": SOURCE,
    "ops": {"search": "GetKwrdFndSubwaySttnList", "timetable": "GetSubwaySttnAcctoSchdulList"},
    "fetched_at": fetched_at, "fetched_at_precision": "day",
    "postprocessed_at": datetime.now(KST).isoformat(timespec="seconds"),
    "station_names": len(id_map), "unmatched_names": len(unmatched), "station_ids": len(targets),
    "combos_done": len(done), "combos_with_rows": len(combos), "empty_combos": empty,
    "rows": rows, "duplicates_removed": dup,
    "stations_with_rows_by_route": dict(sorted(per_route.items())),
    "output": str(OUT),
}
META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"③ 완료 조합 {len(done)}개, 행 있는 조합 {len(combos)}개, 빈 조합 {len(empty)}개 → {META.name}")
if len(done) < len(targets) * 6:
    print(f"   ※ 수집 미완료: 조합 {len(targets) * 6}개 중 {len(done)}개 완료. 수집 끝난 뒤 다시 실행.", file=sys.stderr)
