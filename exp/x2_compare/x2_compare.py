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


def chain(base, q, minute, arrive_by, xfer=None):
    """구간열을 **그 노선 그대로** 한 구간씩 MOTIS 에 넣고 **이 스크립트가 잇는다**(MOTIS 구간 탐색 + 바깥 연결기 —
    MOTIS 의 전체 경로 판정이 아니다). 환승 시간은 MOTIS 가 쓰는 값(transfers.txt 를 분으로 내림 · 최소 2분 · 표에 없으면 2분),
    xfer 를 주면 그 값(우리 판정이 쓴 분)으로.
    돌려줌 {st, dep, arr, off_table, rejoin} — st: ok / exhausted(4쪽 안에 같은 경로 없음 — 「편이 없다」가 아님) / error."""
    lk, n, off, rj = q["leg_stops"], len(q["legs"]), 0, 0
    order = range(n - 1, -1, -1) if arrive_by else range(n)
    t, first, last = minute, None, None
    for i in order:
        hit, _, err = search(base, leg_q(q, i, lk), t, arrive_by)
        if err: return {"st": "error", "note": err}
        if not hit: return {"st": "exhausted"}
        rj += hit.get("rejoin", 0)
        k = (i - 1) if arrive_by else i
        x = None
        if 0 <= k < n - 1:
            x = xfer[k] if xfer and xfer[k] is not None else XFER.get((lk[k][1], lk[k + 1][0]))
            if x is None: x, off = 2, off + 1
        if arrive_by:
            if last is None: last = hit["arr"]
            first = hit["dep"]
            if i > 0: t = hit["dep"] - x
        else:
            if first is None: first = hit["dep"]
            last = hit["arr"]
            if i < n - 1: t = hit["arr"] + x
    return {"st": "ok", "dep": first, "arr": last, "off_table": off, "rejoin": rj}


def ok(h):
    return bool(h) and h.get("st") == "ok"


def in_day(h, dep):
    """그 운행일 안에서 탈 수 있나 — 케이스 출발 뒤 GAP_MAX 분 안에 떠나고 운행일 끝(28:00) 전."""
    return ok(h) and dep <= h["dep"] < DAY_END and h["dep"] - dep <= GAP_MAX


def xfer_min(q):
    lk = q["leg_stops"]
    return [XFER.get((lk[i][1], lk[i + 1][0]), 2) for i in range(len(lk) - 1)]


def fmt(m):
    return "" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def one(q, A, B, G):
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
            if hit and not (hit["dep"] < DAY_END):       # 다음 날 편은 이 운행일의 답이 아니다(GPT 대조 4번)
                o["m_note" + tag] = f"운행일 안에 없음(다음 날 편 {fmt(hit['dep'])})"; hit = None
            elif not hit:
                o["m_note" + tag] = err or "같은 경로가 응답 4쪽 안에 없음"
            o["m_board" + tag] = fmt(hit["dep"]) if hit else ""
            o["m_arrive" + tag] = fmt(hit["arr"]) if hit else ""
            if hit:
                o["d_board" + tag] = hit["dep"] - q["ours_board_min"]
                o["d_arrive" + tag] = hit["arr"] - q["ours_arrive_min"]
                if not tag: o["m_rejoin"] = hit.get("rejoin", 0)
        g = G.leg(q["date"], q["from_stop"], q["to_stop"], dep * 60) if G else None
        if g:
            o["g_board"] = fmt(g[0] // 60); o["g_arrive"] = fmt(g[1] // 60); o["g_arrive_sec"] = g[1] % 60
        o["ours_board"] = fmt(q["ours_board_min"])
        return o
    if q.get("free"):                       # multi 케이스: MOTIS **자유 탐색**(첫 쪽 6개) ↔ 우리 후보 중 가장 이른 도착
        _, seen, err = search(A, q, dep, False, free=True)
        cand = [i for i in seen if dep <= i["dep"] < DAY_END and i["dep"] - dep <= GAP_MAX]
        best = min(cand, key=lambda i: (i["arr"], i["tr"])) if cand else None
        o["m_status"] = "feasible" if best else ("query_error" if err else "search_exhausted")
        o["m_verdict"] = "feasible" if best else "infeasible"
        if best:
            o["m_arrive"] = fmt(best["arr"]); o["m_depart"] = fmt(best["dep"]); o["m_transfers"] = best["tr"]
            o["m_path"] = " → ".join(f"{p[0]} {p[1]}→{p[2]}" for p in best["path"])
            o["m_same_path"] = int(same(best, q["legs"])); o["m_rejoin"] = best.get("rejoin", 0)
            if q.get("ours_arrive_min") is not None: o["d_arrive"] = best["arr"] - q["ours_arrive_min"]
        return o
    ours_ok = q["ours_verdict"] == "feasible"
    by, at = q.get("arrive_by_min"), q.get("ours_margin_min")
    xm = xfer_min(q)
    for tag, base in (("", A), ("_noest", B)):
        # ── FWD: 출발 시각부터 같은 경로 — 존재(성립/불가)와 도착 시각. 두 엔진에 같은 조건(GPT 대조 3번):
        #    출발 뒤 GAP_MAX 분 안 · 운행일 안. 도착 목표가 있으면 그 시각까지 닿아야 성립.
        f = chain(base, q, dep, False)
        if in_day(f, dep):
            st = "feasible" if (by is None or f["arr"] <= by) else "late_for_target"
        elif ok(f):
            st = "no_service_in_day"                 # 같은 경로가 있긴 하나 그 운행일 밖(다음 날 편)·공백 상한 밖
        else:
            st = "query_error" if f["st"] == "error" else "search_exhausted"
        if st in ("no_service_in_day", "search_exhausted") and G and not tag:
            g = G.chain(q["date"], q["leg_stops"], dep, xm)      # MOTIS 밖에서 같은 GTFS 를 직접 읽어 확인
            if g is None or not (g["dep"] // 60 < DAY_END and g["dep"] // 60 - dep <= GAP_MAX):
                st = "no_service_confirmed"
            o["g_depart"] = fmt(g["dep"] // 60) if g else ""; o["g_arrive"] = fmt(g["arr"] // 60) if g else ""
        o["m_status" + tag] = st
        o["m_verdict" + tag] = "feasible" if st == "feasible" else ("infeasible" if st in ("no_service_confirmed", "no_service_in_day", "late_for_target") else "unknown")
        if in_day(f, dep):
            o["m_depart" + tag] = fmt(f["dep"]); o["m_arrive" + tag] = fmt(f["arr"])
            if ours_ok and q.get("ours_arrive_min") is not None:
                o["d_arrive" + tag] = f["arr"] - q["ours_arrive_min"]
            if not tag:
                o["m_rejoin"] = f["rejoin"]
                if f["off_table"]: o["m_off_table_xfer"] = f["off_table"]
        if not tag:
            # 같은 GTFS 를 초 단위로 직접 읽은 값(정합성 · GPT 대조 2·7번)
            if G and in_day(f, dep):
                g = G.chain(q["date"], q["leg_stops"], dep, xm)
                if g: o["g_depart"] = fmt(g["dep"] // 60); o["g_arrive"] = fmt(g["arr"] // 60); o["g_arrive_sec"] = g["arr"] % 60
            # 환승 시간만 우리 값으로 맞춘 재실행(GPT 대조 6번) — 환승이 있는 경로만
            if len(q["legs"]) > 1 and all(x is not None for x in q.get("ours_xfer") or [None]):
                fo = chain(base, q, dep, False, xfer=q["ours_xfer"])
                if in_day(fo, dep):
                    o["m_arrive_ox"] = fmt(fo["arr"])
                    if ours_ok and q.get("ours_arrive_min") is not None: o["d_arrive_ox"] = fo["arr"] - q["ours_arrive_min"]
            _, fseen, _ = search(base, q, dep, False, free=True)
            fseen = [i for i in fseen if dep <= i["dep"] < DAY_END]
            if fseen:
                fb = min(fseen, key=lambda i: (i["arr"], i["tr"]))
                o["m_free_arrive"] = fmt(fb["arr"]); o["m_free_path"] = " → ".join(f"{p[0]} {p[1]}→{p[2]}" for p in fb["path"])
        # ── 마지막 출발 셋(GPT 대조 4번) ─────────────────────────────────
        #   L1 운행일 마지막 출발: 28:00 까지 도착하는 가장 늦은 출발(이 GTFS 의 마지막 시각은 25:14:30 — 잘리는 편 없음)
        #   L2 목표까지 닿는 마지막 출발(arrive_by) · L3 우리 @ 까지 뺀 목표로(arrive_by − @)
        l1 = chain(base, q, DAY_END, True)
        o["m_last_day" + tag] = fmt(l1["dep"]) if ok(l1) else ""
        if not tag and ok(l1): o["m_rejoin_last"] = l1["rejoin"]
        l3 = None
        if by is not None:
            l2 = chain(base, q, by, True)
            o["m_last_by" + tag] = fmt(l2["dep"]) if ok(l2) else ""
            if at is not None:
                l3 = chain(base, q, by - at, True)
                o["m_last_by_at" + tag] = fmt(l3["dep"]) if ok(l3) else ""
                o["m_verdict_at" + tag] = "feasible" if ok(l3) and l3["dep"] >= dep else "infeasible"
        # 우리 「늦어도 출발」과 같은 뜻의 것끼리: 목표가 있으면 L3, 없으면 L1
        l = l3 if (by is not None and at is not None) else (l1 if by is None else None)
        o["last_kind"] = "L3 목표−@" if (by is not None and at is not None) else ("L1 운행일" if by is None else "")
        o["m_last" + tag] = fmt(l["dep"]) if ok(l) else ""
        if ok(l) and q.get("ours_last_depart_min") is not None:
            o["d_last" + tag] = l["dep"] - q["ours_last_depart_min"]
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\final_project\exp\x2_compare")
    ap.add_argument("--a", default="http://127.0.0.1:8080")   # localhost 는 윈도우에서 ::1 을 먼저 두드려 질의마다 약 2초를 잃는다
    ap.add_argument("--b", default="http://127.0.0.1:8081")
    ap.add_argument("--gtfs", default=r"C:\final_project\exp\x1_gtfs\out\gtfs_subway_x1.zip", help="직접 계산용(빈 값이면 건너뜀)")
    ap.add_argument("--transfers", default=r"C:\final_project\exp\x1_gtfs\out\gtfs\transfers.txt")
    a = ap.parse_args()
    out = Path(a.out)
    load_xfer(a.transfers)
    qs = [json.loads(x) for x in (out / "queries.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    plan(a.a, "2026-10-14", "x1_L2_222", "x1_L2_239", 540)      # 첫 질의(적재 뒤 느림)는 따로 잰다
    first_ms = LAT.pop() if LAT else None
    plan(a.b, "2026-10-14", "x1_L2_222", "x1_L2_239", 540); LAT.clear()
    G = None
    if a.gtfs:
        from x2_gtfs import Gtfs
        G = Gtfs(a.gtfs)
    rows = []
    for q in qs:
        n0 = len(LAT)
        r = one(q, a.a, a.b, G)
        if q["kind"] == "비교":
            r["n_queries"] = len(LAT) - n0; r["unit_ms"] = round(sum(LAT[n0:]), 1)   # 이 단위가 쓴 MOTIS 질의 시간 합
        rows.append(r)
    cols = []
    for r in rows:
        for k in r:
            if k not in cols: cols.append(k)
    with open(out / "compare.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    lat = sorted(LAT)
    cu = sorted(r["unit_ms"] for r in rows if r.get("unit") == "case" and r.get("unit_ms") is not None)
    err = sum(1 for r in rows if "error" in str(r.get("m_status", "")) or "error" in str(r.get("m_status_noest", "")))
    timing = {"case_n": len(cu), "case_median_ms": round(statistics.median(cu), 1), "case_p95_ms": round(cu[int(len(cu) * 0.95)], 1),
              "case_max_ms": round(cu[-1], 1), "case_queries_median": statistics.median(r["n_queries"] for r in rows if r.get("unit") == "case" and r.get("n_queries") is not None),
              "query_errors": err, "n_queries": len(lat), "first_ms": round(first_ms, 1) if first_ms else None,
              "median_ms": round(statistics.median(lat), 1), "p95_ms": round(lat[int(len(lat) * 0.95)], 1),
              "max_ms": round(lat[-1], 1), "total_s": round(sum(lat) / 1000, 2)}
    (out / "timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"비교 단위 {len(rows)}줄 → {out / 'compare.csv'}")
    print("응답 시간:", json.dumps(timing, ensure_ascii=False))


if __name__ == "__main__":
    main()
