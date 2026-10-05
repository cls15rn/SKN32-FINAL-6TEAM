# -*- coding: utf-8 -*-
"""X3 — 정류장 ↔ 역 출구 표본을 카카오맵 도보 「최단거리」와 대조(두 번째 잣대 — 정답이 아니다).

  pick   전수 결과(x3_stop_exit_all_base.csv · _BG12.csv)에서 표본 60쌍을 뽑아 kakao_sample.json 에 쓴다.
         무작위 40(직선 0–100 · 100–200 · 200–350 · 350–500 m 에서 10씩) + 긴 쪽 20(길 기준이 직선 × 1.4 보다 5분 이상 긴 쌍 · 역마다 하나).
         두 번에 나눠 뽑았다(씨앗 20261005 로 12 + 10, 20261006 으로 28 + 10) — 그 순서 그대로 다시 만든다.
  table  kakao_sample.json 의 각 줄에 "kakao"(m)를 채운 뒤 돌린다 → 규칙별 집계.

카카오 값은 지도 화면(map.kakao.com/link/by/walk/…)에서 「최단거리」 숫자만 읽어 적는다 — API 아님 · 경로 저장 안 함 · 이 파일은 저장소 밖.
"""
import argparse
import csv
import json
import math
import random
import statistics as st

SPEED = 1.04


def hav(a, b, c, d):
    r = 6371008.8
    p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def mn(m):
    return math.ceil(m / SPEED / 60 - 1e-9)


def pick(a):
    P = a.repo.rstrip("/\\") + "/datasets/mobility/processed/mobility/"
    stops = {}
    for line in open(P + "bus_stops_v1.jsonl", encoding="utf-8"):
        r = json.loads(line)
        stops.setdefault(r["station_id"], (r["station_nm"], r["lat"], r["lng"]))
    ex = json.load(open(P + "station_exits_v1.json", encoding="utf-8"))["exits"]
    base = {(r["stop_id"], r["station"]): r for r in csv.DictReader(open(f"{a.out}/x3_stop_exit_all_base.csv", encoding="utf-8-sig"))}
    bg = {(r["stop_id"], r["station"]): r for r in csv.DictReader(open(f"{a.out}/x3_stop_exit_all_BG12.csv", encoding="utf-8-sig"))}
    ok = [k for k, r in base.items() if r["graph_m"] and "가상" not in r["stop"]]
    bands = ((0, 100), (100, 200), (200, 350), (350, 501))

    def row(grp, k):
        r = base[k]
        nm, la, lo = stops[k[0]]
        c = [e for e in ex[k[1]] if e.get("ref") == (r["exit"] or None)] or ex[k[1]]
        e = min(c, key=lambda e: abs(hav(la, lo, e["lat"], e["lng"]) - int(r["straight_m"])))
        return {"grp": grp, "stop": nm, "station": k[1], "exit": r["exit"], "slat": la, "slng": lo, "elat": e["lat"], "elng": e["lng"],
                "straight": int(r["straight_m"]), "est": int(r["est_m"]), "v2": int(r["graph_m"]),
                "x3": int(bg[k]["graph_m"]) if bg[k]["graph_m"] else None}

    out = []
    # 1차
    random.seed(20261005)
    pk = []
    for lo, hi in bands:
        c = [k for k in ok if lo <= int(base[k]["straight_m"]) < hi]
        pk += [("무작위", k) for k in random.sample(c, 3)]
    tail = [k for k in ok if int(base[k]["min_used"]) - int(base[k]["min_est"]) >= 5]
    seen = set()
    random.shuffle(tail)
    for k in tail:
        if base[k]["station"] in seen:
            continue
        seen.add(base[k]["station"])
        pk.append(("긴쪽", k))
        if len(seen) == 10:
            break
    out += [row(g, k) for g, k in pk]
    # 2차
    have = {(o["stop"], o["station"], o["straight"]) for o in out}
    random.seed(20261006)
    pk = []
    for lo, hi in bands:
        c = [k for k in ok if lo <= int(base[k]["straight_m"]) < hi and (base[k]["stop"], k[1], int(base[k]["straight_m"])) not in have]
        pk += [("무작위", k) for k in random.sample(c, 7)]
    tail = [k for k in ok if int(base[k]["min_used"]) - int(base[k]["min_est"]) >= 5]
    seen = {o["station"] for o in out if o["grp"] == "긴쪽"}
    random.shuffle(tail)
    n = 0
    for k in tail:
        if base[k]["station"] in seen:
            continue
        seen.add(base[k]["station"])
        pk.append(("긴쪽", k))
        n += 1
        if n == 10:
            break
    out += [row(g, k) for g, k in pk]
    json.dump(out, open(f"{a.out}/kakao_sample.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    for i, o in enumerate(out):
        print(i, o["grp"], o["stop"], o["station"], f'https://map.kakao.com/link/by/walk/s,{o["slat"]},{o["slng"]}/e,{o["elat"]},{o["elng"]}')


RULES = {"길 그대로": lambda o, g: g,
         "직선의 2배 넘으면 직선×1.4": lambda o, g: (o["est"] if g > 2 * o["straight"] else g),
         "직선의 3배 넘으면 직선×1.4": lambda o, g: (o["est"] if g > 3 * o["straight"] else g),
         "직선×1.4 보다 5분 이상 길면 직선×1.4": lambda o, g: (o["est"] if mn(g) - mn(o["est"]) >= 5 else g),
         "직선×1.4 만": lambda o, g: o["est"]}


def table(a):
    S = json.load(open(f"{a.out}/kakao_sample.json", encoding="utf-8"))
    base = [r for r in csv.DictReader(open(f"{a.out}/x3_stop_exit_all_base.csv", encoding="utf-8-sig")) if r["graph_m"]]
    N = len(base)
    ntail = sum(int(r["min_used"]) - int(r["min_est"]) >= 5 for r in base)
    res = {"population": N, "tail": ntail, "sample": len(S), "by_group": {}, "rules": {}}

    def line(rows, key):
        r = [o[key] / o["kakao"] for o in rows]
        dm = [mn(o[key]) - mn(o["kakao"]) for o in rows]
        return {"n": len(r), "within_25pct": sum(.75 <= x <= 1.25 for x in r), "median": round(st.median(r), 2),
                "short": sum(x < .75 for x in r), "long": sum(x > 1.25 for x in r), "max": round(max(r), 2), "min": round(min(r), 2),
                "min_within1": sum(abs(d) <= 1 for d in dm), "min_short2": sum(d <= -2 for d in dm), "min_long2": sum(d >= 2 for d in dm),
                "min_long5": sum(d >= 5 for d in dm)}
    groups = {"무작위 40": [o for o in S if o["grp"] == "무작위"], "긴 쪽 20": [o for o in S if o["grp"] == "긴쪽"]}
    for lo, hi in ((0, 100), (100, 200), (200, 350), (350, 501)):
        groups[f"무작위 · 직선 {lo}–{min(hi, 500)} m"] = [o for o in S if o["grp"] == "무작위" and lo <= o["straight"] < hi]
    for nm, rows in groups.items():
        res["by_group"][nm] = {k: line(rows, k) for k in ("est", "v2", "x3")}
    # 규칙 — 보통 층(무작위 중 긴 쪽이 아닌 것) · 긴 쪽 층(표본의 긴 쪽 전부)으로 나눠 전수 비율로 가중
    for gk in ("v2", "x3"):
        is_tail = lambda o: mn(o["v2"]) - mn(o["est"]) >= 5       # noqa: E731
        layers = (("보통", [o for o in S if o["grp"] == "무작위" and not is_tail(o)], 1 - ntail / N),
                  ("긴쪽", [o for o in S if is_tail(o)], ntail / N))
        for nm, f in RULES.items():
            d = {}
            for lab, rows, w in layers:
                d[lab] = {"n": len(rows), "within_25pct": sum(.75 <= f(o, o[gk]) / o["kakao"] <= 1.25 for o in rows),
                          "min_within1": sum(abs(mn(f(o, o[gk])) - mn(o["kakao"])) <= 1 for o in rows),
                          "min_short2": sum(mn(f(o, o[gk])) - mn(o["kakao"]) <= -2 for o in rows),
                          "min_long5": sum(mn(f(o, o[gk])) - mn(o["kakao"]) >= 5 for o in rows), "weight": round(w, 4)}
            d["weighted_within_25pct"] = round(sum(d[x]["within_25pct"] / d[x]["n"] * d[x]["weight"] for x in ("보통", "긴쪽")), 3)
            d["weighted_min_within1"] = round(sum(d[x]["min_within1"] / d[x]["n"] * d[x]["weight"] for x in ("보통", "긴쪽")), 3)
            res["rules"][f"{gk} · {nm}"] = d
    json.dump(res, open(f"{a.out}/x3_kakao_table.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("pick", "table"))
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    (pick if a.cmd == "pick" else table)(a)
