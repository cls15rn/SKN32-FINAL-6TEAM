#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""지하철 시간표(역별 출발 행) → GTFS  (X1 · 실험 · 2026-10-04)

입력(기준 커밋 ae88ba1 의 저장소 파일 — 읽기만):
  datasets/mobility/processed/mobility/timetable_v1.jsonl.gz      역별 출발 행 453,668
  datasets/mobility/processed/mobility/line_station_order_v1.json 노선별 역 순서·간선
  datasets/mobility/processed/mobility/station_coords.json        역 좌표
  datasets/mobility/processed/mobility/transfer_walk_v1.json      환승 거리 213쌍
  final_project_cs/app/modules/travel_ops/mobility/engine/rules/holidays_2026_2027.json  공휴일(판정기와 같은 표)
출력(저장소 밖 — 기본 C:\\final_project\\exp\\x1_gtfs\\out):
  gtfs/  agency · stops · routes · trips · stop_times · calendar · calendar_dates · transfers · feed_info
  gtfs_subway_x1.zip      위 폴더를 묶은 것(MOTIS 입력)
  row_map.csv.gz          원천 행 번호 → trip_id · stop_sequence (보존 검사·X2 용)
  orphan_rows.csv         편으로 못 묶은 홑행(GTFS 에 못 넣음 — 이유 포함)
  est_stops.csv           우리가 더한 종착역 도착(원천에 없는 유일한 정차)
  stop_lookup.csv         노선·역명 ↔ stop_id
  build_report.json       숫자 요약

원칙: 출발 시각은 원천 그대로 · 버스 없음 · 원천에 없는 정차는 「종착역 도착」 하나뿐(따로 표시 · timepoint=0).

  python exp/x1_gtfs/build_gtfs.py                 # 기본 경로
  python exp/x1_gtfs/build_gtfs.py --out D:\\x     # 산출 자리 바꾸기
"""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import io
import json
import math
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chain  # noqa: E402

REPO = next((p for p in HERE.parents if (p / "datasets" / "mobility").is_dir()), HERE.parents[1])
M = REPO / "datasets" / "mobility" / "processed" / "mobility"
RULES = REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules"

# 기준 판(X0 §2 · 커밋 ae88ba1) — git blob 해시
BASELINE = {
    "timetable_v1.jsonl.gz": "a27f80ac08fa719057c7f80eaf7a27de3c1c30dc",
    "line_station_order_v1.json": "e1c261edb816c4ed4d3d56ced9b3e1a5fd493f6a",
    "transfer_walk_v1.json": "742bc0ac625cf8b40d19f0c7945423adbb028de4",
    "station_coords.json": "02ae095f22f880a69ed0ebd17881b8f763359522",
}

LINE_CODE = {
    "01호선": "L1", "02호선": "L2", "03호선": "L3", "04호선": "L4", "05호선": "L5", "06호선": "L6", "07호선": "L7",
    "08호선": "L8", "09호선": "L9", "GTX-A": "GTXA", "경강선": "GG", "경의선": "GJ", "경춘선": "GC", "공항철도": "AREX",
    "김포도시철도": "GIMPO", "서해선": "SH", "수인분당선": "SB", "신림선": "SL", "신분당선": "SBD", "용인경전철": "YI",
    "우이신설경전철": "UI", "의정부경전철": "UJB", "인천2호선": "I2", "인천선": "I1",
}
DAY_CODE = {"weekday": "W", "saturday": "S", "holiday": "H"}

# 환승 거리표의 노선·역 이름을 시간표 이름으로 — 표가 옛 이름·운영사 이름을 쓴 12쌍
TRANSFER_LINE_ALIAS = {("수서", "국철"): "수인분당선", ("석계", "경원선"): "01호선"}
TRANSFER_STATION_ALIAS = {("서울역", "GTX-A"): "서울", ("총신대입구", "07호선"): "이수", ("이수", "04호선"): "총신대입구"}

EST_SKIP_MAX = 3      # 행선지가 이만큼 역 안에 있고 사이 역에 이 열차 행이 없으면(통과) 종착 도착을 더한다


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def hms(sec):
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description="지하철 시간표 → GTFS (X1 실험)")
    ap.add_argument("--timetable", default=str(M / "timetable_v1.jsonl.gz"))
    ap.add_argument("--order", default=str(M / "line_station_order_v1.json"))
    ap.add_argument("--coords", default=str(M / "station_coords.json"))
    ap.add_argument("--transfer", default=str(M / "transfer_walk_v1.json"))
    ap.add_argument("--holidays", default=str(RULES / "holidays_2026_2027.json"))
    ap.add_argument("--coord-fill", default=str(HERE / "stop_coord_fill.csv"))
    ap.add_argument("--out", default=str(REPO.parent / "exp" / "x1_gtfs" / "out"))
    ap.add_argument("--allow-other", action="store_true", help="기준 판(blob 해시)이 달라도 계속한다")
    a = ap.parse_args()

    out = Path(a.out)
    if REPO in out.resolve().parents or out.resolve() == REPO:
        sys.exit(f"산출 자리가 저장소 안이다: {out} — 저장소 밖을 준다(--out)")
    gt = out / "gtfs"
    gt.mkdir(parents=True, exist_ok=True)

    # ── 입력 · 기준 판 확인 ──
    raw = {}
    for key, path in (("timetable_v1.jsonl.gz", a.timetable), ("line_station_order_v1.json", a.order),
                      ("station_coords.json", a.coords), ("transfer_walk_v1.json", a.transfer)):
        raw[key] = Path(path).read_bytes()
        sha = blob_sha(raw[key])
        ok = sha == BASELINE[key]
        print(f"  입력 {key}: {sha[:12]} {'= 기준 판' if ok else '≠ 기준 판 ' + BASELINE[key][:12]}")
        if not ok and not a.allow_other:
            sys.exit("기준 판과 다른 입력이다 — 기준 커밋 ae88ba1 의 파일로 돌리거나 --allow-other 를 준다")
    order = json.loads(raw["line_station_order_v1.json"].decode("utf-8"))
    coords = json.loads(raw["station_coords.json"].decode("utf-8"))["stations"]
    transfer = json.loads(raw["transfer_walk_v1.json"].decode("utf-8"))
    hol = json.loads(Path(a.holidays).read_text(encoding="utf-8"))
    rows = [json.loads(ln) for ln in gzip.open(io.BytesIO(raw["timetable_v1.jsonl.gz"]), "rt", encoding="utf-8") if ln.strip()]
    N = len(rows)
    fetched = sorted({r.get("fetched_at") for r in rows})
    print(f"  원천 행 {N:,} · 수집일 {fetched}")

    # ── 열차 잇기 ──
    R = chain.build(rows, order)
    succ, pred, T, D, nets = R["succ"], R["pred"], R["T"], R["dest"], R["nets"]
    delta, nxt, term_row, conflict = R["delta"], R["nxt"], R["term_row"], R["conflict"]

    def edge_sec(ln, s, n, d):
        m = (delta.get((ln, s, n, d)) if d else None) or delta.get((ln, s, n))
        return m[0] if m else None

    # ── 정류장 ──
    fill = {}
    if Path(a.coord_fill).exists():
        for r in csv.DictReader(open(a.coord_fill, encoding="utf-8-sig")):
            fill[r["station_key"]] = (float(r["lat"]), float(r["lng"]), r["grade"])
    stop_id, stops, no_coord = {}, [], []
    used = {(r["line"], r["station_nm"]) for r in rows}
    for ln, L in order["lines"].items():
        for s in L["stations"]:
            key = (ln, s["station_nm"])
            sid = f"{LINE_CODE[ln]}_{s['fr_code']}"
            c = coords.get(s["station_key"]) or {}
            lat, lng, grade = c.get("lat"), c.get("lng"), "확정"
            if lat is None and s["station_key"] in fill:
                lat, lng, grade = fill[s["station_key"]]
            if lat is None:
                if key in used:
                    no_coord.append(s["station_key"])
                continue
            stop_id[key] = sid
            stops.append({"stop_id": sid, "stop_name": s["station_nm"], "stop_desc": ln, "stop_lat": lat, "stop_lon": lng,
                          "line": ln, "fr_code": s["fr_code"], "station_cd": s.get("station_cd") or "",
                          "station_nm_en": s.get("station_nm_en") or "", "coord_grade": grade})
    if no_coord:
        sys.exit("좌표 없는 역(시간표에 있음): " + " · ".join(no_coord) + " — stop_coord_fill.csv 에 적는다")

    # ── 사슬 → 편 ──
    has_sat = {ln for ln in order["lines"] if any(r["line"] == ln and r["day_type"] == "saturday" for r in rows)}

    def service(ln, day):
        if day == "weekday":
            return "WD"
        if day == "saturday":
            return "SAT"
        return "SUNHOL" if ln in has_sat else "SATSUNHOL"

    trips, st_rows, row_map, orphans, est = [], [], {}, [], []
    heads = [i for i in range(N) if i not in pred]
    heads.sort(key=lambda i: (rows[i]["line"], rows[i]["day_type"], T[i], nets[rows[i]["line"]].order.get(rows[i]["station_nm"], 0),
                              rows[i]["dir"], rows[i].get("dest_nm") or "", i))
    seq_no = collections.Counter()
    stat = collections.defaultdict(collections.Counter)
    for h in heads:
        ch = [h]
        while ch[-1] in succ:
            ch.append(succ[ch[-1]])
        ln, day = rows[h]["line"], rows[h]["day_type"]
        net, j = nets[ln], ch[-1]
        s = rows[j]["station_nm"]
        # 종착 도착(원천에 없는 정차) — 다음 역이 행선지거나 노선 끝이면 그 열차는 거기 닿는다
        extra, end_kind = None, "조각"
        n1 = nxt.get(j)
        if j in term_row and len(ch) > 1:
            end_kind = "종착행"                              # 종착역 도착 행이 원천에 있다
        elif n1 is not None and (n1 == D[j] or len(net.adj[n1]) == 1):
            sec = edge_sec(ln, s, n1, D[j])
            if sec is not None and (ln, n1) in stop_id:
                extra, end_kind = (n1, T[j] + sec, "종착직전", 1), "종착추정"
        elif D[j] and D[j] != s:
            p = net.path(s, D[j], EST_SKIP_MAX)
            if p and p[-1] == D[j] and len(p) >= 2:
                acc, prev, ok = 0, s, True
                for n in p:
                    sec = edge_sec(ln, prev, n, D[j])
                    if sec is None:
                        ok = False
                        break
                    acc, prev = acc + sec, n
                if ok and (ln, D[j]) in stop_id:
                    extra, end_kind = (D[j], T[j] + acc, "통과후종착", len(p)), "종착추정(통과)"
        if len(ch) == 1 and extra is None:
            r = rows[h]
            why = ("행선지 없음" if not r.get("dest_nm") else "행선지-방향 어긋남" if h in conflict else "이을 다음 역 행 없음")
            orphans.append({"row_no": h + 1, "line": ln, "station_nm": r["station_nm"], "day_type": day, "dep_time": r["dep_time"],
                            "dir": r["dir"], "dest_nm": r.get("dest_nm") or "", "why": why})
            stat[ln]["홑행(제외)"] += 1
            continue
        code = f"{LINE_CODE[ln]}{DAY_CODE[day]}"
        seq_no[code] += 1
        tid = f"{code}{seq_no[code]:05d}"
        names = collections.Counter(rows[i]["dest_nm"] for i in ch if D[i] and i not in conflict and rows[i].get("dest_nm"))
        last_stop = extra[0] if extra else rows[j]["station_nm"]
        head_src = names.most_common(1)[0][0] if names else None
        loop_dest = bool(head_src) and net.loop
        headsign = head_src if (head_src and (end_kind != "조각" or True)) else last_stop
        if not head_src or (end_kind.startswith("종착") and not loop_dest and
                            (order.get("dest_alias", {}).get(ln, {}).get(head_src, head_src) != last_stop)):
            headsign = last_stop                            # 원천 행선지가 없거나(어긋남) 실제 끝 역과 다르면 끝 역 이름
        trips.append({"route_id": LINE_CODE[ln], "service_id": service(ln, day), "trip_id": tid, "trip_headsign": headsign,
                      "direction_id": 0 if rows[h]["dir"] == "U" else 1})
        for k, i in enumerate(ch, 1):
            st_rows.append((tid, rows[i]["dep_time"], rows[i]["dep_time"], stop_id[(ln, rows[i]["station_nm"])], k, 1))
            row_map[i] = (tid, k)
        if extra:
            t = hms(int(extra[1]))
            st_rows.append((tid, t, t, stop_id[(ln, extra[0])], len(ch) + 1, 0))
            est.append({"trip_id": tid, "line": ln, "day_type": day, "stop_name": extra[0], "arrival_time": t, "kind": extra[2],
                        "from_station": s, "from_dep": rows[j]["dep_time"], "hops": extra[3]})
        stat[ln]["편"] += 1
        stat[ln]["편:" + end_kind] += 1
        stat[ln]["행"] += len(ch)
        stat[ln]["잇기:" + "/".join(sorted({R["how"][i] for i in ch[:-1]} & {"skip"})) or "잇기"] += 0
    for i, hw in R["how"].items():
        stat[rows[i]["line"]]["이음:" + hw] += 1

    # ── 운행일 ──
    years = sorted(int(y) for y in hol.get("years") or {int(k[:4]) for k in hol["holidays"]})
    start, end = f"{years[0]}0101", f"{years[-1]}1231"
    cal = [("WD", 1, 1, 1, 1, 1, 0, 0), ("SAT", 0, 0, 0, 0, 0, 1, 0), ("SUNHOL", 0, 0, 0, 0, 0, 0, 1), ("SATSUNHOL", 0, 0, 0, 0, 0, 1, 1)]
    cal_dates = []
    for iso in sorted(hol["holidays"]):
        d = datetime.date.fromisoformat(iso)
        ymd = d.strftime("%Y%m%d")
        if d.weekday() < 5:
            cal_dates += [("WD", ymd, 2), ("SUNHOL", ymd, 1), ("SATSUNHOL", ymd, 1)]
        elif d.weekday() == 5:
            cal_dates += [("SAT", ymd, 2), ("SUNHOL", ymd, 1)]

    # ── 환승 ──
    by_name = collections.defaultdict(dict)
    for (ln, nm), sid in stop_id.items():
        by_name[nm][ln] = sid
    tr, tr_alias, tr_fail = [], [], []
    for key, p in transfer["pairs"].items():
        nm, fl, tl = p["station_nm"], p["from_line"], p["to_line"]
        fl2, tl2 = TRANSFER_LINE_ALIAS.get((nm, fl), fl), TRANSFER_LINE_ALIAS.get((nm, tl), tl)
        fn, tn = TRANSFER_STATION_ALIAS.get((nm, fl2), nm), TRANSFER_STATION_ALIAS.get((nm, tl2), nm)
        a_, b_ = by_name.get(fn, {}).get(fl2), by_name.get(tn, {}).get(tl2)
        if not a_ or not b_ or p.get("walk_min") is None:
            tr_fail.append(key)
            continue
        if (fl2, tl2, fn, tn) != (fl, tl, nm, nm):
            tr_alias.append(key)
        tr.append((a_, b_, 2, int(math.ceil(p["walk_min"] * 60 - 1e-9))))

    # ── 쓰기 ──
    def w(name, header, data):
        with open(gt / name, "w", encoding="utf-8", newline="") as f:
            cw = csv.writer(f)
            cw.writerow(header)
            cw.writerows(data)

    w("agency.txt", ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang"],
      [["X1", "수도권 지하철(이동 모듈 실험 X1)", "https://github.com/cls15rn/SKN32-FINAL-6TEAM", "Asia/Seoul", "ko"]])
    w("stops.txt", ["stop_id", "stop_name", "stop_desc", "stop_lat", "stop_lon"],
      [[s["stop_id"], s["stop_name"], s["stop_desc"], s["stop_lat"], s["stop_lon"]] for s in stops])
    w("routes.txt", ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
      [[LINE_CODE[ln], "X1", ln, "", 1] for ln in order["lines"] if stat[ln]["편"]])
    w("trips.txt", ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id"],
      [[t[k] for k in ("route_id", "service_id", "trip_id", "trip_headsign", "direction_id")] for t in trips])
    w("stop_times.txt", ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "timepoint"], st_rows)
    w("calendar.txt", ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
      [list(c) + [start, end] for c in cal])
    w("calendar_dates.txt", ["service_id", "date", "exception_type"], cal_dates)
    w("transfers.txt", ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"], tr)
    w("feed_info.txt", ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date", "feed_version"],
      [["6팀 이동 모듈 실험 X1", "https://github.com/cls15rn/SKN32-FINAL-6TEAM", "ko", start, end,
        f"ae88ba1/{BASELINE['timetable_v1.jsonl.gz'][:8]}/fetched{fetched[-1]}"]])

    with zipfile.ZipFile(out / "gtfs_subway_x1.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(gt.glob("*.txt")):
            z.write(p, p.name)
    with gzip.open(out / "row_map.csv.gz", "wt", encoding="utf-8", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["row_no", "trip_id", "stop_sequence"])
        for i in range(N):
            if i in row_map:
                cw.writerow([i + 1, row_map[i][0], row_map[i][1]])

    def wd(name, data, header):
        with open(out / name, "w", encoding="utf-8-sig", newline="") as f:
            cw = csv.DictWriter(f, fieldnames=header)
            cw.writeheader()
            cw.writerows(data)

    wd("orphan_rows.csv", orphans, ["row_no", "line", "station_nm", "day_type", "dep_time", "dir", "dest_nm", "why"])
    wd("est_stops.csv", est, ["trip_id", "line", "day_type", "stop_name", "arrival_time", "kind", "from_station", "from_dep", "hops"])
    wd("stop_lookup.csv", stops, ["line", "stop_name", "stop_id", "fr_code", "station_cd", "station_nm_en", "stop_lat", "stop_lon",
                                  "coord_grade", "stop_desc"])

    rep = {
        "built_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline_commit": "ae88ba1", "input_blobs": {k: blob_sha(v) for k, v in raw.items()}, "fetched_at": fetched,
        "source_rows": N, "rows_in_trips": len(row_map), "orphan_rows": len(orphans), "trips": len(trips),
        "stop_times": len(st_rows), "est_terminal_stops": len(est),
        "est_by_kind": dict(collections.Counter(e["kind"] for e in est)),
        "trip_end": dict(sum((collections.Counter({k[2:]: v for k, v in c.items() if k.startswith("편:")}) for c in stat.values()),
                             collections.Counter())),
        "links": dict(collections.Counter(R["how"].values())), "dest_conflict_rows": len(conflict),
        "stops": len(stops), "stops_coord_filled": [s["line"] + "|" + s["stop_name"] for s in stops if s["coord_grade"] != "확정"],
        "transfers": len(tr), "transfers_by_alias": tr_alias, "transfers_unmapped": tr_fail,
        "services": {"WD": "평일(공휴일 제외)", "SAT": "토요일 표가 따로 있는 노선의 토요일(공휴일 제외)",
                     "SUNHOL": "토요일 표가 따로 있는 노선의 일요일·공휴일", "SATSUNHOL": "그 밖 노선의 토·일·공휴일"},
        "saturday_table_lines": sorted(has_sat), "calendar_range": [start, end], "holidays": len(hol["holidays"]),
        "per_line": {ln: {k: v for k, v in sorted(c.items()) if v} for ln, c in sorted(stat.items())},
    }
    (out / "build_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  편 {len(trips):,} · stop_times {len(st_rows):,} (원천 행 {len(row_map):,} + 종착 추정 {len(est):,}) · 홑행 제외 {len(orphans):,}")
    print(f"  끝 유형 {rep['trip_end']}")
    print(f"  정류장 {len(stops)} · 환승 {len(tr)}(이름 맞춤 {len(tr_alias)} · 못 맞춤 {len(tr_fail)}) · 달력 {start}~{end}")
    print(f"  → {out}")


if __name__ == "__main__":
    main()
