# -*- coding: utf-8 -*-
"""X2 ② — queries.jsonl 을 MOTIS 에 넣고 우리 판정과 댄다 (실험 · exp-mobility).

MOTIS 는 먼저 떠 있어야 한다(x2_motis.ps1). 두 판을 같이 본다.
  A  http://localhost:8080  — X1 GTFS 그대로
  B  http://localhost:8081  — 종착 추정 정차(timepoint=0 · 17,447)를 뺀 판   ← X1 주의 (5)
질의 셋(케이스·후보 단위)
  AB    「도착 목표 시각까지」(arriveBy) — 목표 = 케이스 arrive_by(있으면 우리 @ 를 뺀 시각도 따로) /
        없으면 우리 예정 도착(+1분 · 분 단위 올림 흡수) / 우리가 불가면 운행일 끝(28:00)
        → 같은 경로로 가장 늦게 떠나는 편. 그 출발 ≥ 케이스 출발 시각이면 MOTIS 「성립」
  FWD   출발 시각부터(같은 경로의 가장 이른 도착) — 도착 시각 차
  LAST  운행일 끝(28:00)까지 도착 — 같은 경로의 마지막 출발 = 막차
「같은 경로」 = MOTIS 여정의 탑승 구간(노선 · 타는 역 · 내리는 역)이 케이스 legs 와 같다.
경로 응답은 저장하지 않는다 — 시각(분)·환승 수·노선열 요약만 compare.csv 에 남긴다(저장소 밖).
"""
import argparse, csv, datetime as dt, json, statistics, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

DAY_END = 28 * 60          # 운행일 끝 04:00
GAP_MAX = 180              # 판정기 service_window.gap_max_min 과 같은 값
REJOIN_AT = {("02호선", "성수"), ("06호선", "응암")}
REJOIN_GAP = 3             # 분
TOL = 1                    # 분 — MOTIS 응답은 분 단위(X1 주의 4)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LAT = []                   # 질의당 응답 시간(ms)


def iso(date, minute):
    t = dt.datetime.fromisoformat(date) + dt.timedelta(minutes=minute) - dt.timedelta(hours=9)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_min(date, s):
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None) + dt.timedelta(hours=9)
    return int((t - dt.datetime.fromisoformat(date)).total_seconds() // 60)


def plan(base, date, frm, to, minute, arrive_by=False, n=6, cursor=None):
    p = {"fromPlace": frm, "toPlace": to, "time": iso(date, minute), "numItineraries": n}
    if arrive_by: p["arriveBy"] = "true"
    if cursor: p["pageCursor"] = cursor
    t0 = time.perf_counter()
    try:
        r = json.load(OPENER.open(base + "/api/v1/plan?" + urllib.parse.urlencode(p), timeout=60))
    except urllib.error.HTTPError as e:
        return [], None, e.read().decode("utf-8", "replace")[:200]
    LAT.append((time.perf_counter() - t0) * 1000)
    its = []
    for it in r.get("itineraries", []):
        tl = [l for l in it["legs"] if l["mode"] != "WALK"]
        # 순환·관문역(2호선 성수 · 6호선 응암)에서 같은 노선을 3분 안에 이어 탄 것은 한 구간으로 본다 — X1 의 편은 행선지가
        # 바뀌는 역에서 끊긴 것이 있어 MOTIS 가 「02호선 A→성수 / 02호선 성수→B」로 낸다(같은 열차가 이어 달리는 것).
        # 그 밖의 역(구로 · 신도림 지선 등)에서 같은 노선끼리 갈아탄 것은 진짜 환승이라 합치지 않는다.
        path, rejoin, prev_end = [], 0, None
        for l in tl:
            seg = (l.get("routeShortName"), l["from"]["name"], l["to"]["name"])
            st = to_min(date, l["startTime"])
            cont = (path and path[-1][0] == seg[0] and path[-1][2] == seg[1] and (seg[0], seg[1]) in REJOIN_AT
                    and prev_end is not None and st - prev_end <= REJOIN_GAP)
            prev_end = to_min(date, l["endTime"])
            if cont:
                path[-1] = (seg[0], path[-1][1], seg[2]); rejoin += 1
            else:
                path.append(seg)
        its.append({
            "dep": to_min(date, it["startTime"]), "arr": to_min(date, it["endTime"]), "tr": it["transfers"] - rejoin,
            "path": path, "rejoin": rejoin,
            "legs": [(to_min(date, l["startTime"]), to_min(date, l["endTime"])) for l in tl],
            "head": [l.get("headsign") for l in tl],
        })
    cur = r.get("previousPageCursor") if arrive_by else r.get("nextPageCursor")
    return its, cur, None


def same(it, legs):
    return it["path"] == [tuple(l) for l in legs]


def search(base, q, minute, arrive_by, pages=4, free=False):
    """같은 경로 여정을 찾는다(없으면 다음 쪽으로 최대 pages 번). free 면 첫 쪽 전부."""
    cur, seen = None, []
    for _ in range(pages):
        its, cur, err = plan(base, q["date"], q["from_stop"], q["to_stop"], minute, arrive_by, cursor=cur)
        if err: return None, seen, err
        seen += its
        if free: break
        hit = [i for i in its if same(i, q["legs"])]
        if hit:
            return (max(hit, key=lambda i: i["dep"]) if arrive_by else min(hit, key=lambda i: (i["arr"], -i["dep"]))), seen, None
        if not cur: break
    return None, seen, None


XFER = {}                  # (from_stop, to_stop) → 분 — GTFS transfers.txt (X1 의 213쌍)


def load_xfer(path):
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            # MOTIS 는 초를 분으로 내리고 최소 2분을 둔다(임의 400쌍 질의의 도보 구간 길이로 확인 · 2026-10-05)
            XFER[("x1_" + r["from_stop_id"], "x1_" + r["to_stop_id"])] = max(2, int(r["min_transfer_time"]) // 60)


def leg_q(q, i, lk):
    l = q["legs"][i]
    return dict(q, legs=[l], from_stop=lk[i][0], to_stop=lk[i][1])


def chain(base, q, minute, arrive_by):
    """구간열을 **그 노선 그대로** MOTIS 에 한 구간씩 통과시킨다(환승 시간은 GTFS transfers.txt · 표에 없으면 MOTIS 기본 2분).
    MOTIS 의 자유 탐색은 도착이 같은 다른 환승역을 골라 「같은 경로」가 답에 안 나올 수 있어서다.
    돌려줌: (첫 구간 출발, 끝 구간 도착, 표 밖 환승 수) 또는 None."""
    lk, n, off = q["leg_stops"], len(q["legs"]), 0
    order = range(n - 1, -1, -1) if arrive_by else range(n)
    t, first, last = minute, None, None
    for i in order:
        hit, _, err = search(base, leg_q(q, i, lk), t, arrive_by)
        if not hit: return None
        if arrive_by:
            if last is None: last = hit["arr"]
            first = hit["dep"]
            if i > 0:
                x = XFER.get((lk[i - 1][1], lk[i][0])); off += x is None
                t = hit["dep"] - (x if x is not None else 2)
        else:
            if first is None: first = hit["dep"]
            last = hit["arr"]
            if i < n - 1:
                x = XFER.get((lk[i][1], lk[i + 1][0])); off += x is None
                t = hit["arr"] + (x if x is not None else 2)
    return {"dep": first, "arr": last, "off_table": off}


def rejoin_of(hit):
    return hit.get("rejoin", 0) if hit else 0


def fmt(m):
    return "" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def one(q, A, B):
    o = {k: q.get(k) for k in ("id", "bundle", "unit", "cand", "date", "kind", "why", "ours_verdict", "ours_code",
                                 "ours_internal", "ours_reason")}
    o["path"] = " → ".join(f"{l[0]} {l[1]}→{l[2]}" for l in q.get("legs") or [])
    o["depart"] = fmt(q.get("depart_min")); o["arrive_by"] = fmt(q.get("arrive_by_min"))
    o["ours_arrive"] = fmt(q.get("ours_arrive_min")); o["ours_last"] = fmt(q.get("ours_last_depart_min"))
    o["express_zone"] = int(any(l[0] == "09호선" for l in q.get("legs") or []) or "EXPRESS" in q["id"])
    if q["kind"] != "비교":
        return o
    dep = q["depart_min"]
    if q["unit"] == "leg":
        for tag, base in (("", A), ("_noest", B)):
            hit, seen, err = search(base, q, dep, False)
            o["m_board" + tag] = fmt(hit["dep"]) if hit else ""
            o["m_arrive" + tag] = fmt(hit["arr"]) if hit else ""
            if hit:
                o["d_board" + tag] = hit["dep"] - q["ours_board_min"]
                o["d_arrive" + tag] = hit["arr"] - q["ours_arrive_min"]
            else:
                o["m_note" + tag] = err or ("같은 경로 없음 · MOTIS 첫 여정 " + (" / ".join(f"{p[0]} {p[1]}→{p[2]}" for p in seen[0]["path"]) if seen else "0개"))
        o["ours_board"] = fmt(q["ours_board_min"])
        return o
    if q.get("free"):                       # multi 케이스: MOTIS 자유 탐색의 가장 이른 도착 ↔ 우리 후보 중 가장 이른 도착
        _, seen, err = search(A, q, dep, False, free=True)
        ok = [i for i in seen if i["dep"] - dep <= GAP_MAX and i["dep"] < DAY_END]
        best = min(ok, key=lambda i: (i["arr"], i["tr"])) if ok else None
        o["m_verdict"] = "feasible" if best else "infeasible"
        if best:
            o["m_arrive"] = fmt(best["arr"]); o["m_depart"] = fmt(best["dep"]); o["m_transfers"] = best["tr"]
            o["m_path"] = " → ".join(f"{p[0]} {p[1]}→{p[2]}" for p in best["path"])
            o["m_same_path"] = int(same(best, q["legs"]))
            if q.get("ours_arrive_min") is not None: o["d_arrive"] = best["arr"] - q["ours_arrive_min"]
        return o
    # ── AB: 도착 목표 시각까지 ─────────────────────────────────────────────
    ours_ok = q["ours_verdict"] == "feasible"
    if q.get("arrive_by_min") is not None:
        target, tkind = q["arrive_by_min"], "arrive_by"
    elif ours_ok and q.get("ours_arrive_min") is not None:
        target, tkind = q["ours_arrive_min"] + TOL, "우리 예정 도착+1"
    else:
        target, tkind = DAY_END, "운행일 끝"
    o["ab_target"] = fmt(target); o["ab_target_kind"] = tkind
    for tag, base in (("", A), ("_noest", B)):
        hit, seen, err = chain(base, q, target, True), [], None
        o["ab_depart" + tag] = fmt(hit["dep"]) if hit else ""
        o["ab_arrive" + tag] = fmt(hit["arr"]) if hit else ""
        o["m_verdict" + tag] = "feasible" if hit and hit["dep"] >= dep else "infeasible"
        if hit and hit["off_table"] and not tag: o["m_off_table_xfer"] = hit["off_table"]
        # arrive_by 가 있으면 우리 @(여유 폭)를 뺀 목표로도 한 번 — 판정 층(@)을 걷어낸 비교
        if tkind == "arrive_by" and q.get("ours_margin_min") is not None:
            h2 = chain(base, q, target - q["ours_margin_min"], True)
            o["ab_depart_at" + tag] = fmt(h2["dep"]) if h2 else ""
            o["m_verdict_at" + tag] = "feasible" if h2 and h2["dep"] >= dep else "infeasible"
        # ── FWD: 출발 시각부터 — 도착 시각 차 ────────────────────────────
        f = chain(base, q, dep, False)
        _, fseen, _ = search(base, q, dep, False, free=True) if not tag else (None, [], None)
        if f:
            o["m_depart" + tag] = fmt(f["dep"]); o["m_arrive" + tag] = fmt(f["arr"])
            if ours_ok and q.get("ours_arrive_min") is not None:
                o["d_arrive" + tag] = f["arr"] - q["ours_arrive_min"]
        if fseen and not tag:
            fb = min(fseen, key=lambda i: (i["arr"], i["tr"]))
            o["m_free_arrive"] = fmt(fb["arr"]); o["m_free_path"] = " → ".join(f"{p[0]} {p[1]}→{p[2]}" for p in fb["path"])
        # ── LAST: 막차 ───────────────────────────────────────────────────
        #   도착 목표가 있는 케이스의 우리 「늦어도 출발」은 목표−@ 로 역산한 값이다 → 같은 목표로 역산한 MOTIS 값과 댄다
        l = h2 if tkind == "arrive_by" and q.get("ours_margin_min") is not None else chain(base, q, DAY_END, True)
        o["m_last" + tag] = fmt(l["dep"]) if l else ""
        if l and q.get("ours_last_depart_min") is not None:
            o["d_last" + tag] = l["dep"] - q["ours_last_depart_min"]
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\final_project\exp\x2_compare")
    ap.add_argument("--a", default="http://127.0.0.1:8080")   # localhost 는 윈도우에서 ::1 을 먼저 두드려 질의마다 약 2초를 잃는다
    ap.add_argument("--b", default="http://127.0.0.1:8081")
    ap.add_argument("--transfers", default=r"C:\final_project\exp\x1_gtfs\out\gtfs\transfers.txt")
    a = ap.parse_args()
    out = Path(a.out)
    load_xfer(a.transfers)
    qs = [json.loads(x) for x in (out / "queries.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    plan(a.a, "2026-10-14", "x1_L2_222", "x1_L2_239", 540)      # 첫 질의(적재 뒤 느림)는 따로 잰다
    first_ms = LAT.pop() if LAT else None
    plan(a.b, "2026-10-14", "x1_L2_222", "x1_L2_239", 540); LAT.clear()
    rows = [one(q, a.a, a.b) for q in qs]
    cols = []
    for r in rows:
        for k in r:
            if k not in cols: cols.append(k)
    with open(out / "compare.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    lat = sorted(LAT)
    timing = {"n_queries": len(lat), "first_ms": round(first_ms, 1) if first_ms else None,
              "median_ms": round(statistics.median(lat), 1), "p95_ms": round(lat[int(len(lat) * 0.95)], 1),
              "max_ms": round(lat[-1], 1), "total_s": round(sum(lat) / 1000, 2)}
    (out / "timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"비교 단위 {len(rows)}줄 → {out / 'compare.csv'}")
    print("응답 시간:", json.dumps(timing, ensure_ascii=False))


if __name__ == "__main__":
    main()
