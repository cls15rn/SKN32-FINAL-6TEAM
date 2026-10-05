# -*- coding: utf-8 -*-
"""X3 — 걸음 그래프(road_graph_v2) 검수: 직선 × 1.4 와 길 기준 거리 대조.

  ① 독립 계산기(scipy) ↔ 발표 코드 길찾기(팀장 graph_router) 같은 값인가
  ② 도보 20구간(지하철 출구 → 관광지 · 카카오 도보 최단거리 ±25%)
  ③ 정류장 ↔ 역 출구 전수(「역 앞」 500 m 안 · 혼합 후보·환승 판정이 쓰는 쌍) — 직선 × 1.4 대비 분포
  ④ 회귀 MIX-01~07 · 실측 태그 여정의 환승 도보(판정기가 고른 정류장 행·출구 그대로)
  ⑤ 장소 ↔ 역 출구(도보 20구간 도착지 · 예시 일정 장소)

실행(저장소 맨 위에서):
  python exp/x3_walk/x3_run.py --repo . --out C:\\final_project\\exp\\x3_walk\\out ^
      --walk20 C:\\final_project\\graph\\gh_walk20_result.csv ^
      --samples C:\\final_project\\data\\travel\\raw\\mobility\\osm\\osm_walk_samples_20.json ^
      --field C:\\final_project\\exp\\x2_compare\\gt\\field_legs_v1.json
★ 판정기를 올릴 때 자료 폴더는 **저장소의 datasets/mobility/processed** 로 직접 준다(저장소 .env 의 DATA_DIR 은 안 읽는다 — X2 오류).
★ 실측 태그 여정(개인 동선)이 들어간 표는 --out(저장소 밖)에만 쓴다. 저장소에는 요약 숫자만.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics as st
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from x3_graph import WalkGraph, hav  # noqa: E402

DETOUR, SPEED = 1.4, 1.04          # 규칙 값(시작할 때 규칙 파일과 같은지 확인한다)


def boot_engine(repo):
    """엔진 패키지만 올린다 — 상위 패키지 __init__ 은 코어 의존(openai·pgvector)을 끌어온다."""
    import types
    base = os.path.join(repo, "final_project_cs")
    for name in ("app", "app.modules", "app.modules.travel_ops", "app.modules.travel_ops.mobility"):
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.__path__ = [os.path.join(base, *name.split("."))]
            sys.modules[name] = m
    if base not in sys.path:
        sys.path.insert(0, base)
    from app.modules.travel_ops.mobility.engine import runtime
    data_dir = os.path.abspath(os.path.join(repo, "datasets", "mobility", "processed"))
    rt = runtime.build_verifier(data_dir=data_dir, quiet=True, local_router=True)
    return rt, rt._v, data_dir


def minutes(m):
    return math.ceil(m / SPEED / 60 - 1e-9)


def pct(xs, p):
    return float(np.percentile(xs, p)) if len(xs) else None


def dist_summary(rows, key="ratio"):
    xs = [r[key] for r in rows if r.get(key) is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "p10": round(pct(xs, 10), 2), "p25": round(pct(xs, 25), 2), "median": round(pct(xs, 50), 2),
            "p75": round(pct(xs, 75), 2), "p90": round(pct(xs, 90), 2), "max": round(max(xs), 2), "min": round(min(xs), 2)}


def measure(S, g, lat, lon, straight):
    r = g.to_point(S, lat, lon)
    est = straight * DETOUR
    if "error" in r:
        return {"error": r["error"], "straight_m": round(straight), "est_m": round(est)}
    used = max(r["dist_m"], est) if r["optimistic"] else r["dist_m"]
    return {"straight_m": round(straight), "est_m": round(est), "graph_m": round(r["dist_m"]), "used_m": round(used),
            "optimistic": r["optimistic"], "snap_m": r["snap_m"],
            "ratio": round(r["dist_m"] / est, 3) if est > 0 else None,
            "ratio_used": round(used / est, 3) if est > 0 else None,
            "x_straight": round(r["dist_m"] / straight, 2) if straight > 0 else None,
            "min_est": minutes(est), "min_graph": minutes(r["dist_m"]), "min_used": minutes(used)}


def band(rows, key="ratio"):
    ok = [r for r in rows if r.get(key) is not None]
    n = len(ok)
    if not n:
        return {}
    dm = [r["min_used"] - r["min_est"] for r in ok]
    return {"n": n,
            "graph_shorter_25pct": sum(r[key] < 0.75 for r in ok),
            "within_25pct": sum(0.75 <= r[key] <= 1.25 for r in ok),
            "graph_longer_25pct": sum(r[key] > 1.25 for r in ok),
            "graph_over_2x_straight": sum((r["x_straight"] or 0) > 2.0 for r in ok),
            "graph_over_3x_straight": sum((r["x_straight"] or 0) > 3.0 for r in ok),
            "optimistic": sum(bool(r["optimistic"]) for r in ok),
            "min_same": sum(d == 0 for d in dm), "min_plus1": sum(d == 1 for d in dm), "min_plus2to4": sum(2 <= d <= 4 for d in dm),
            "min_plus5": sum(d >= 5 for d in dm), "min_minus1": sum(d == -1 for d in dm), "min_minus2": sum(d <= -2 for d in dm)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--graph", default=None, help="걸음 그래프 폴더(기본: 저장소의 road_graph_v2)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--walk20", required=True)
    ap.add_argument("--samples", required=True)
    ap.add_argument("--field", default=None)
    ap.add_argument("--near-m", type=float, default=None)
    ap.add_argument("--no-engine", action="store_true", help="판정기 없이(①④ 생략) — 변형 그래프를 잴 때")
    ap.add_argument("--tag", default="v2")
    ap.add_argument("--area", action="store_true", help="실험판의 광장 간선을 쓴다")
    ap.add_argument("--no-snap-area", action="store_true", help="광장 간선에는 붙이지 않는다(지나가기만)")
    ap.add_argument("--snap-exclude", default="", help="붙이지 않을 gr 값(쉼표) — 1 지하 · 2 고가")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    repo = os.path.abspath(a.repo)
    proc = os.path.join(repo, "datasets", "mobility", "processed", "mobility")
    gdir = a.graph or os.path.join(proc, "road_graph_v2")
    S = {"tag": a.tag, "graph_dir": os.path.basename(os.path.normpath(gdir)), "ran_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    snap_ex = [int(x) for x in a.snap_exclude.split(",") if x]
    g = WalkGraph(gdir, area=a.area, snap_exclude=snap_ex, snap_area=not a.no_snap_area)
    S["variant"] = {"area": a.area, "snap_exclude": snap_ex, "snap_area": not a.no_snap_area}
    S["graph"] = dict(g.stats, osm_data_at=g.manifest.get("osm_data_at"), src_md5=g.manifest.get("src_md5"),
                      version=g.manifest.get("version"))
    try:
        import resource
        S["graph"]["rss_mb_after_load"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    except ImportError:
        pass

    rt = v = router = None
    if not a.no_engine:
        t0 = time.time()
        rt, v, data_dir = boot_engine(repo)
        S["engine"] = {"data_dir": data_dir, "rules_version": rt.rules_version, "load_s": round(time.time() - t0, 1)}
        R = v.R["transfer"]["stop_station_walk"]
        assert R["detour_factor"]["value"] == DETOUR and v.R["measured_baseline"]["kakao_walk_speed_mps"]["value"] == SPEED, "규칙 값이 다르다"
        router = v.bike_router.router
        assert router.foot_ok, "걸음 그래프(v2)가 아니다"
        S["engine"]["walk_limit_m"] = v._walk_limit({})
        S["engine"]["near_m"] = v.rv("alternatives", "정류장_반경_m")
    near_m = a.near_m or (S.get("engine") or {}).get("near_m") or 500

    def team(lat1, lon1, lat2, lon2):
        try:
            p = router.route((lon1, lat1), (lon2, lat2), "foot")["paths"][0]
            return {"dist_m": p["distance"], "optimistic": p["quality"]["optimistic"]}
        except Exception as e:  # RouterDown
            return {"error": str(e).split(":")[0]}

    # ── ② 도보 20구간 ─────────────────────────────────────────────────────────
    samples = {r["id"]: r for r in json.load(open(a.samples, encoding="utf-8"))}
    w20 = []
    for row in csv.DictReader(open(a.walk20, encoding="utf-8-sig")):
        s = samples[row["id"]]
        straight = hav(s["slat"], s["slng"], s["dlat"], s["dlng"])
        m = measure(g.from_point(s["slat"], s["slng"]), g, s["dlat"], s["dlng"], straight)
        kk = float(row["kakao_m"])
        rec = {"id": row["id"], "start": row["start"], "dest": row["dest"], "kakao_m": kk, "gh_m": float(row["gh_foot_m"]), **m}
        for nm, val in (("graph", m.get("graph_m")), ("used", m.get("used_m")), ("est", m.get("est_m")), ("gh", rec["gh_m"])):
            rec[f"{nm}/kakao"] = round(val / kk, 2) if val else None
        if router is not None:
            tr = team(s["slat"], s["slng"], s["dlat"], s["dlng"])
            rec["team_m"] = tr.get("dist_m")
        w20.append(rec)

    def n25(key):
        xs = [r[key] for r in w20 if r[key] is not None]
        return {"within_25pct": sum(0.75 <= x <= 1.25 for x in xs), "n": len(xs), "median": round(st.median(xs), 2),
                "short": sum(x < 0.75 for x in xs), "long": sum(x > 1.25 for x in xs), "max": max(xs), "min": min(xs)}
    S["walk20"] = {k: n25(f"{k}/kakao") for k in ("graph", "used", "gh", "est")}
    S["walk20"]["deficit4"] = {r["id"]: {"graph/kakao": r["graph/kakao"], "gh/kakao": r["gh/kakao"], "graph_m": r.get("graph_m"),
                                         "kakao_m": r["kakao_m"]} for r in w20 if r["id"] in ("S07", "S02", "S10", "S17")}
    S["walk20"]["rows"] = w20

    # ── ③ 정류장 ↔ 역 출구 전수 ───────────────────────────────────────────────
    exits_doc = json.load(open(os.path.join(proc, "station_exits_v1.json"), encoding="utf-8"))
    ex_rows = []
    for nm, lst in exits_doc["exits"].items():
        for e in lst:
            ex_rows.append((nm, e.get("ref"), e["lat"], e["lng"]))
    from scipy.spatial import cKDTree
    kx = math.cos(math.radians(37.55)) * 111320.0
    ex_xy = np.array([(r[3] * kx, r[2] * 110540.0) for r in ex_rows])
    ex_tree = cKDTree(ex_xy)
    stops = {}
    with open(os.path.join(proc, "bus_stops_v1.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("lat") is None:
                continue
            stops.setdefault(r["station_id"], (r["station_nm"], r["lat"], r["lng"]))
    pairs, n_stop_near, errs = [], 0, {}
    t0 = time.time()
    for sid, (snm, la, lo) in stops.items():
        idx = ex_tree.query_ball_point((lo * kx, la * 110540.0), near_m + 15)
        best = {}
        for i in idx:
            nm, ref, ela, elo = ex_rows[i]
            d = hav(la, lo, ela, elo)
            if d <= near_m and (nm not in best or d < best[nm][0]):
                best[nm] = (d, ref, ela, elo)
        if not best:
            continue
        n_stop_near += 1
        Sp = g.from_point(la, lo, limit_m=5000.0)
        for nm, (d, ref, ela, elo) in best.items():
            m = measure(Sp, g, ela, elo, d)
            m.update(stop_id=sid, stop=snm, station=nm, exit=ref)
            if "error" in m:
                errs[m["error"]] = errs.get(m["error"], 0) + 1
            pairs.append(m)
    ok = [p for p in pairs if "error" not in p]
    S["stop_exit_all"] = {"near_m": near_m, "stops_total": len(stops), "stops_with_station": n_stop_near, "pairs": len(pairs),
                          "measured": len(ok), "errors": errs, "elapsed_s": round(time.time() - t0, 1),
                          "ratio_graph_over_est": dist_summary(ok, "ratio"), "ratio_used_over_est": dist_summary(ok, "ratio_used"),
                          "graph_over_straight": dist_summary(ok, "x_straight"), "bands": band(ok),
                          "by_straight": {}}
    for lo_m, hi_m in ((0, 50), (50, 100), (100, 200), (200, 350), (350, 500)):
        sub = [p for p in ok if lo_m <= p["straight_m"] < hi_m or (hi_m == 500 and p["straight_m"] == 500)]
        S["stop_exit_all"]["by_straight"][f"{lo_m}-{hi_m}"] = dict(dist_summary(sub, "ratio"), **{k: band(sub).get(k) for k in
                                                                    ("graph_longer_25pct", "graph_shorter_25pct", "min_plus2to4", "min_plus5")})
    worst = sorted(ok, key=lambda p: -(p["graph_m"] - p["est_m"]))[:40]
    S["stop_exit_all"]["worst40"] = [{k: p[k] for k in ("stop", "station", "exit", "straight_m", "est_m", "graph_m", "x_straight",
                                                         "min_est", "min_graph", "optimistic")} for p in worst]
    with open(out / f"x3_stop_exit_all_{a.tag}.csv", "w", newline="", encoding="utf-8-sig") as f:
        cols = ["stop_id", "stop", "station", "exit", "straight_m", "est_m", "graph_m", "used_m", "optimistic", "ratio", "x_straight",
                "min_est", "min_graph", "min_used", "error"]
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(pairs)

    # ── ① 독립 계산기 ↔ 발표 코드 길찾기 ──────────────────────────────────────
    if router is not None:
        rng = np.random.default_rng(20261005)
        pick = [pairs[i] for i in rng.choice(len(pairs), size=min(300, len(pairs)), replace=False)]
        diffs, mism = [], []
        coord = {sid: (la, lo) for sid, (_n, la, lo) in stops.items()}
        exc = {(nm, ref): (la, lo) for nm, ref, la, lo in ex_rows}
        for p in pick:
            la, lo = coord[p["stop_id"]]
            # 같은 역·같은 번호 출구가 여럿일 수 있어 직선이 맞는 것을 고른다
            cands = [(ela, elo) for nm, ref, ela, elo in ex_rows if nm == p["station"] and ref == p["exit"]]
            ela, elo = min(cands, key=lambda c: abs(hav(la, lo, c[0], c[1]) - p["straight_m"]))
            tr = team(la, lo, ela, elo)
            mine = g.route(la, lo, ela, elo)
            if "error" in tr or "error" in mine:
                mism.append({"stop": p["stop"], "station": p["station"], "team": tr.get("error") or round(tr["dist_m"]),
                             "scipy": mine.get("error") or round(mine["dist_m"])})
                continue
            d = mine["dist_m"] - tr["dist_m"]
            diffs.append(d)
            if abs(d) > 2.0:
                mism.append({"stop": p["stop"], "station": p["station"], "team": round(tr["dist_m"], 1), "scipy": mine["dist_m"]})
        w20d = [r["graph_m"] - r["team_m"] for r in w20 if r.get("team_m") is not None and r.get("graph_m") is not None]
        S["cross_check"] = {"pairs": len(pick), "both_measured": len(diffs), "within_2m": sum(abs(d) <= 2.0 for d in diffs),
                            "within_1pct": None, "max_abs_diff_m": round(max(map(abs, diffs)), 1) if diffs else None,
                            "mismatch": mism[:30], "walk20_within_2m": sum(abs(d) <= 2.0 for d in w20d), "walk20_n": len(w20d)}

    # ── ④ 회귀 MIX · 실측 태그 여정의 환승 도보 ────────────────────────────────
    if v is not None:
        def transfers(cases, label):
            rows = []
            for c in cases:
                legs = c["legs"]
                for i in range(1, len(legs)):
                    p, q = legs[i - 1], legs[i]
                    pb, qb = p.get("mode") == "bus", q.get("mode") == "bus"
                    rec = {"set": label, "case": c["id"], "kind": None}
                    try:
                        if pb and qb:
                            ra, rb = v._bus_stop_row(p, "to"), v._bus_stop_row(q, "from")
                            rec["kind"] = "버스↔버스"
                            if not ra or not rb:
                                rec["error"] = "정류장 행 없음"
                            elif ra.get("station_id") == rb.get("station_id"):
                                rec.update(a=ra["station_nm"], b=rb["station_nm"], straight_m=0, est_m=0, graph_m=0, same_stop=True)
                            else:
                                d = hav(ra["lat"], ra["lng"], rb["lat"], rb["lng"])
                                rec.update(a=ra["station_nm"], b=rb["station_nm"],
                                           **measure(g.from_point(ra["lat"], ra["lng"]), g, rb["lat"], rb["lng"], d))
                        elif pb != qb:
                            bus_leg, which, sub = (p, "to", q) if pb else (q, "from", p)
                            st_nm = sub["from"] if pb else sub["to"]
                            row = v._bus_stop_row(bus_leg, which)
                            rec["kind"] = "버스↔지하철"
                            if not row:
                                rec["error"] = "정류장 행 없음"
                            else:
                                near = v.ex.nearest(st_nm, row["lat"], row["lng"], sub.get("line")) if v.ex else None
                                if near:
                                    d, e = near
                                    tla, tlo, where = e["lat"], e["lng"], f"{st_nm} {e.get('ref')}번 출구"
                                else:
                                    pt = v.sc.get(sub.get("line"), st_nm)
                                    tla, tlo, where = pt["lat"], pt["lng"], f"{st_nm}(역 좌표)"
                                    d = hav(row["lat"], row["lng"], tla, tlo)
                                rec.update(a=row["station_nm"], b=where, **measure(g.from_point(row["lat"], row["lng"]), g, tla, tlo, d))
                                ss = v._stop_station_walk(p, q, {})
                                rec["engine_verdict"], rec["engine_walk_min"] = ss["verdict"], ss["walk_min"]
                                rec["engine_straight_m"] = round(ss["dist_m"]) if ss.get("dist_m") is not None else None
                        else:
                            continue            # 지하철↔지하철은 역 안 환승 거리표(이 방 밖)
                    except Exception as e:      # noqa: BLE001
                        rec["error"] = f"{type(e).__name__}: {e}"
                    rows.append(rec)
            return rows

        mix = json.load(open(os.path.join(repo, "final_project_cs", "tests", "unit", "travel", "mobility", "mixed_legs_v1.json"),
                             encoding="utf-8"))["cases"]
        from app.modules.travel_ops.mobility.engine.verify_time_cli import check_expect
        miss_all = []
        for c in mix:
            miss, _skipped = check_expect(c, rt.verify_case(c))      # 회귀·명령줄과 같은 대조 함수
            miss_all += [list(map(str, m)) for m in miss]
        S["mix_expect"] = {"cases": len(mix), "mismatch": miss_all}
        if S["mix_expect"]["mismatch"]:
            raise SystemExit(f"회귀 기대와 다르다 — 멈춘다: {S['mix_expect']['mismatch']}")
        S["mix_transfers"] = transfers(mix, "MIX")
        if a.field:
            fc = json.load(open(a.field, encoding="utf-8"))["cases"]
            ft = transfers(fc, "FIELD")
            uniq = {}
            for r in ft:
                uniq.setdefault((r["kind"], r.get("a"), r.get("b")), dict(r, cases=0))["cases"] += 1
            with open(out / "x3_field_transfers.json", "w", encoding="utf-8") as f:      # 개인 동선 — 저장소 밖
                json.dump(list(uniq.values()), f, ensure_ascii=False, indent=1)
            okf = [r for r in uniq.values() if r.get("graph_m") is not None and not r.get("same_stop")]
            S["field_transfers"] = {"cases": len(fc), "transfers": len(ft), "unique_pairs": len(uniq),
                                    "same_stop": sum(bool(r.get("same_stop")) for r in uniq.values()),
                                    "errors": sum("error" in r for r in uniq.values()),
                                    "measured": len(okf), "bands": band(okf) if okf else {},
                                    "rows_no_names": [{k: r.get(k) for k in ("kind", "cases", "straight_m", "est_m", "graph_m",
                                                                              "min_est", "min_graph", "optimistic")} for r in okf]}

    # ── ⑤ 장소 ↔ 역 출구 ─────────────────────────────────────────────────────
    places = [(s["dest"], s["dlat"], s["dlng"]) for s in samples.values()]
    ex_in = os.path.join(repo, "final_project_cs", "tests", "unit", "travel", "mobility", "plan_example_in_v1.json")
    if os.path.exists(ex_in):
        places += [(p["name"], p["lat"], p["lon"]) for p in json.load(open(ex_in, encoding="utf-8"))["places"]]
    prow = []
    for nm, la, lo in places:
        idx = ex_tree.query_ball_point((lo * kx, la * 110540.0), 815)
        best = {}
        for i in idx:
            snm, ref, ela, elo = ex_rows[i]
            d = hav(la, lo, ela, elo)
            if d <= 800 and (snm not in best or d < best[snm][0]):
                best[snm] = (d, ref, ela, elo)
        Sp = g.from_point(la, lo)
        for snm, (d, ref, ela, elo) in sorted(best.items(), key=lambda kv: kv[1][0])[:4]:
            m = measure(Sp, g, ela, elo, d)
            m.update(place=nm, station=snm, exit=ref)
            prow.append(m)
    okp = [p for p in prow if "error" not in p]
    S["place_exit"] = {"places": len(places), "pairs": len(prow), "measured": len(okp), "ratio_graph_over_est": dist_summary(okp, "ratio"),
                       "bands": band(okp), "rows": [{k: p.get(k) for k in ("place", "station", "exit", "straight_m", "est_m", "graph_m",
                                                                            "used_m", "x_straight", "min_est", "min_used", "optimistic",
                                                                            "error")} for p in prow]}

    with open(out / f"x3_summary_{a.tag}.json", "w", encoding="utf-8") as f:
        json.dump(S, f, ensure_ascii=False, indent=1)
    brief = {k: S[k] for k in S if k not in ("walk20", "stop_exit_all", "place_exit", "mix_transfers", "field_transfers")}
    brief["walk20"] = {k: S["walk20"][k] for k in ("graph", "used", "gh", "est", "deficit4")}
    brief["stop_exit_all"] = {k: val for k, val in S["stop_exit_all"].items() if k != "worst40"}
    brief["place_exit"] = {k: val for k, val in S["place_exit"].items() if k != "rows"}
    if "field_transfers" in S:
        brief["field_transfers"] = {k: val for k, val in S["field_transfers"].items() if k != "rows_no_names"}
    print(json.dumps(brief, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
