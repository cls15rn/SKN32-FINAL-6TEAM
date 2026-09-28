# mobility_scripts/collect/exit_pairs_v1.py — 하차 칸 수기 대상 (도착역 · 출구) 쌍 집계 (46번 방 층1 · 2026-09-25)
#
# 층2(하차 칸 수기)는 **우리 후보가 실제로 고르는 쌍만** 적는다. 이 스크립트가 그 목록을 만든다.
#   입력 ① POI 목록 — 팀 use case POI(확정 뒤) · 25 실측 목적지 · 데모 동선. CSV 또는 JSON
#          CSV 컬럼: poi_id,name,lat,lng[,set]   JSON: [{"poi_id","name","lat","lng","set"?}, ...]
#        ② (선택) 도착 구간 목록 — {"cases":[{"id", "legs":[{"line","from","to"}...], "dest":{"lat","lng"}}]}
#          마지막 지하철 leg 의 (노선, 앞 역, 도착역) 을 그대로 쓴다(방향이 정해져 있다).
#   역 좌표 34 station_coords.json · 출구 34 station_exits_v1.json(OSM · 추정) · 방향 line_station_order_v1.json
#
# 규칙
#   · POI 마다 반경 --radius m 안 역(역 좌표 기준)을 전부 후보 도착역으로 본다. 반경은 **수집 범위**(줄 수 상한)일 뿐
#     판정 값이 아니다 — 판정의 도보 한도는 규칙 파일이 정한다.
#   · 출구 = 그 역 출구 중 POI 에 가장 가까운 것(exits.py nearest 와 같은 규칙 · 직선거리). 출구 좌표가 없는 역은
#     출구 「?」 로 남기고 수기 때 안내도로 정한다.
#   · 방향 = 그 역에 들어오는 **앞 역**(prev). POI 모드에서는 역의 노선마다 이웃 전부를 방향으로 편다(어느 쪽에서 올지 모른다).
#   · 수기 템플릿의 칸·문·출처 URL·확인 시각은 **비워 둔다** — 안내도를 보고 사람이 채운다(규칙 24 · 캡처 저장 안 함).
#   · 표에 없는 쌍은 값을 안 낸다. 「계단 근접 칸」으로 대체하지 않는다.
#
# 출력: processed/mobility/exit_pairs_v1.json · exit_car_todo_v1.csv(수기 템플릿) · exit_pairs_v1_report.md
# 실행: python mobility_scripts/collect/exit_pairs_v1.py --poi <poi.csv> [--cases <cases.json> ...] [--radius 800]
import argparse, csv, json, math, sys, collections
from datetime import datetime, timezone, timedelta
from pathlib import Path
from _paths import PROCESSED

KST = timezone(timedelta(hours=9))
OUT_DIR = PROCESSED / "mobility"


def meters(lat1, lng1, lat2, lng2):          # mobility/engine/geo.py 와 같은 평면 근사
    dy = (lat2 - lat1) * 111_320
    dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def load_poi(p):
    p = Path(p)
    if p.suffix.lower() == ".json":
        rows = json.loads(p.read_text(encoding="utf-8"))
    else:
        rows = list(csv.DictReader(p.read_text(encoding="utf-8-sig").splitlines()))
    out = []
    for r in rows:
        try:
            out.append({"poi_id": str(r["poi_id"]), "name": r.get("name", ""), "set": r.get("set") or p.stem,
                        "lat": float(r["lat"]), "lng": float(r["lng"])})
        except (KeyError, ValueError, TypeError) as e:
            sys.exit(f"POI 행 형식 오류({p.name}): {r} — {e}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poi", action="append", default=[], help="POI CSV/JSON (여러 번 가능)")
    ap.add_argument("--cases", action="append", default=[], help="도착 구간 JSON (여러 번 가능)")
    ap.add_argument("--radius", type=float, default=800.0, help="POI→역 수집 반경 m (판정 값 아님)")
    ap.add_argument("--out-prefix", default="exit_pairs_v1")
    a = ap.parse_args()
    if not a.poi and not a.cases:
        sys.exit("입력이 없다: --poi 또는 --cases")

    sc = json.loads((OUT_DIR / "station_coords.json").read_text(encoding="utf-8"))
    ex = json.loads((OUT_DIR / "station_exits_v1.json").read_text(encoding="utf-8"))
    lo = json.loads((OUT_DIR / "line_station_order_v1.json").read_text(encoding="utf-8"))
    exits = ex["exits"]

    st_pos, st_lines = {}, collections.defaultdict(set)
    for v in sc["stations"].values():
        st_pos.setdefault(v["station_nm"], (v["lat"], v["lng"]))
        st_lines[v["station_nm"]].add(v["line"])
    adj = {}
    for ln, L in lo["lines"].items():
        g = collections.defaultdict(set)
        for e in L["edges"]:
            g[e["a"]].add(e["b"]); g[e["b"]].add(e["a"])
        adj[ln] = g

    def nearest_exit(st, lat, lng):
        best = None
        for e in exits.get(st, []):
            d = meters(lat, lng, e["lat"], e["lng"])
            if best is None or d < best[0]:
                best = (d, e)
        return best

    # (역, 노선, 앞역, 출구) → 근거 목록
    pairs = collections.defaultdict(lambda: {"from": [], "exit_desc": None, "exit_attrib": None, "exit_to_poi_m": []})
    no_station, no_exit = [], set()

    def add(st, line, prev, lat, lng, ref_from):
        ne = nearest_exit(st, lat, lng)
        if ne is None:
            no_exit.add(st); ref, desc, attrib, dm = "?", None, None, None
        else:
            dm, e = ne; ref, desc, attrib = e.get("ref") or "?", e.get("desc"), e.get("attrib")
        k = (st, line, prev, ref)
        p = pairs[k]
        p["from"].append(ref_from); p["exit_desc"] = desc; p["exit_attrib"] = attrib
        if dm is not None:
            p["exit_to_poi_m"].append(round(dm))

    n_poi = 0
    for f in a.poi:
        for poi in load_poi(f):
            n_poi += 1
            near = [(meters(poi["lat"], poi["lng"], *xy), st) for st, xy in st_pos.items()]
            near = sorted((d, st) for d, st in near if d <= a.radius)
            if not near:
                no_station.append(poi["poi_id"]); continue
            for d, st in near:
                for ln in sorted(st_lines[st]):
                    nbs = sorted(adj.get(ln, {}).get(st, ())) or [None]
                    for prev in nbs:
                        add(st, ln, prev, poi["lat"], poi["lng"],
                            {"kind": "poi", "set": poi["set"], "poi_id": poi["poi_id"], "name": poi["name"],
                             "station_m": round(d)})

    n_case = 0
    for f in a.cases:
        doc = json.loads(Path(f).read_text(encoding="utf-8"))
        for c in doc.get("cases", doc if isinstance(doc, list) else []):
            dest = c.get("dest") or {}
            sub = [l for l in c.get("legs", []) if l.get("line") in adj]
            if not sub or "lat" not in dest:
                continue
            n_case += 1
            last = sub[-1]; ln, st = last["line"], last["to"]
            # 앞 역 = 마지막 leg 경로에서 도착역 바로 앞. 이웃 중 from 쪽으로 가는 첫 칸(트리·최단).
            # 순환선(2호선)에서 양쪽 거리가 비슷하면 최단이 실제 경로와 다를 수 있다 — 케이스에 path 가 있으면 그걸 써야 한다(W3 에서)
            g = adj[ln]; prev = None
            if last["from"] in g.get(st, ()):
                prev = last["from"]
            else:
                q = collections.deque([(st, None)]); seen = {st}
                while q and prev is None:
                    u, first = q.popleft()
                    for w in g.get(u, ()):
                        if w in seen:
                            continue
                        seen.add(w); f1 = first or w
                        if w == last["from"]:
                            prev = f1; break
                        q.append((w, f1))
            add(st, ln, prev, dest["lat"], dest["lng"],
                {"kind": "case", "file": Path(f).name, "case_id": c.get("id")})

    now = datetime.now(KST).isoformat(timespec="seconds")
    rows = []
    for (st, ln, prev, ref), p in sorted(pairs.items(), key=lambda x: (x[0][0], x[0][1], str(x[0][2]), x[0][3])):
        rows.append({"station_nm": st, "line": ln, "prev_nm": prev, "exit_ref": ref, "exit_desc": p["exit_desc"],
                     "exit_attrib": p["exit_attrib"], "exit_to_poi_m_min": min(p["exit_to_poi_m"], default=None),
                     "n_refs": len(p["from"]), "from": p["from"]})
    doc = {"schema": "exit_pairs_v1", "built_at": now, "radius_m": a.radius,
           "inputs": {"poi": [Path(x).name for x in a.poi], "cases": [Path(x).name for x in a.cases]},
           "exit_source": ex.get("source_id"), "exit_grade": ex.get("grade"),
           "stats": {"poi": n_poi, "cases": n_case, "pairs": len(rows),
                     "stations": len({r["station_nm"] for r in rows}),
                     "station_exit": len({(r["station_nm"], r["exit_ref"]) for r in rows}),
                     "poi_no_station": len(no_station), "stations_no_exit": sorted(no_exit)},
           "poi_no_station": no_station, "pairs": rows}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{a.out_prefix}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    # 수기 템플릿 — 값 칸은 비워 둔다. utf-8-sig 로 써야 엑셀에서 한글이 안 깨진다
    todo = OUT_DIR / "exit_car_todo_v1.csv"
    with todo.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["station_nm", "line", "prev_nm", "exit_ref", "exit_desc", "n_refs", "car", "door",
                    "source_url", "checked_at", "note"])
        for r in rows:
            w.writerow([r["station_nm"], r["line"], r["prev_nm"] or "", r["exit_ref"], r["exit_desc"] or "",
                        r["n_refs"], "", "", "", "", ""])

    by_line = collections.Counter(r["line"] for r in rows)
    L = ["# 하차 칸 수기 대상 쌍 (exit_pairs_v1)", "",
         f"생성 {now} · 반경 {a.radius:.0f} m(수집 범위) · POI {n_poi} · 도착 구간 {n_case} · 출구 소스 {ex.get('source_id')}"
         f"(등급 {ex.get('grade')})", "",
         f"수기 줄(역·노선·앞역·출구) **{len(rows)}** · 역 {doc['stats']['stations']} · (역·출구) {doc['stats']['station_exit']}",
         f"역을 못 찾은 POI {len(no_station)} · 출구 좌표 없는 역 {sorted(no_exit)}", "",
         "| 노선 | 줄 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in by_line.most_common()]
    L += ["", "수기 템플릿 `exit_car_todo_v1.csv` — car·door·source_url·checked_at 을 안내도(KRIC 역별 이용안내도)를 보고 채운다. "
          "캡처는 저장하지 않는다(규칙 24). 채운 뒤 `exit_car_v1.json` 빌드는 층2 에서."]
    (OUT_DIR / f"{a.out_prefix}_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[exit_pairs_v1] POI {n_poi} · 구간 {n_case} → 줄 {len(rows)} · 역 {doc['stats']['stations']} · 출구 없음 {len(no_exit)}")


if __name__ == "__main__":
    main()
