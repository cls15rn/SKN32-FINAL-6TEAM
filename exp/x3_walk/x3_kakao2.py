# -*- coding: utf-8 -*-
"""X3 — 검증 표본(카카오 대조 2차). 1차 60쌍(x3_kakao.py)을 보고 규칙을 골랐다는 지적(GPT 대조)을 받아, **규칙·지표를 먼저 고정하고** 새로 뽑는다.

  모집단   정류장↔역 출구 전수에서 잰 쌍 중 이름에 「(가상)」이 없는 것(8,291) — 1차에서 본 60쌍은 뺀다.
  값       판정기가 실제로 쓸 거리 = used_m(낙관 표시면 길 기준과 직선 × 1.4 중 큰 쪽).
  A 표본   모집단 전체에서 **쌍 단순 무작위 60**(가중 없음 — 비율이 곧 모집단 추정).
  B 표본   「길 기준 분 − 직선 × 1.4 분 ≥ 5」인 쌍에서 **쌍 단순 무작위 30**(역으로 거르지 않는다).
  규칙     ① 길 그대로 ② 직선의 2배 넘으면 직선 × 1.4 ③ 직선 × 1.4 보다 5분 이상 길면 직선 × 1.4 ④ 직선 × 1.4 만 — 이 넷만, 여기 적은 그대로.
  지표     카카오 「최단거리」 ±25% 안 · 분 차이 ±1 안 · 카카오보다 2분 이상 짧게 · 5분 이상 길게(분 = 거리 ÷ 1.04 m/s 올림 — 실제 보행 시간이 아니다).
씨앗 20261007.  pick → kakao_sample2.json(각 줄에 "kakao" 를 채운다) → table → x3_kakao_table2.json
"""
import argparse, csv, json, math, random, statistics as st

SPEED = 1.04
def hav(a, b, c, d):
    r = 6371008.8; p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))
def mn(m): return math.ceil(m / SPEED / 60 - 1e-9)

RULES = {"길 그대로": lambda o: o["used"],
         "직선의 2배 넘으면 직선×1.4": lambda o: (o["est"] if o["used"] > 2 * o["straight"] else o["used"]),
         "5분 이상 길면 직선×1.4": lambda o: (o["est"] if mn(o["used"]) - mn(o["est"]) >= 5 else o["used"]),
         "직선×1.4 만": lambda o: o["est"]}

def load(a):
    rows = [r for r in csv.DictReader(open(f"{a.out}/x3_stop_exit_all_base.csv", encoding="utf-8-sig")) if r["graph_m"] and "가상" not in r["stop"]]
    return rows

def pick(a):
    P = a.repo.rstrip("/\\") + "/datasets/mobility/processed/mobility/"
    stops = {}
    for line in open(P + "bus_stops_v1.jsonl", encoding="utf-8"):
        r = json.loads(line); stops.setdefault(r["station_id"], (r["station_nm"], r["lat"], r["lng"]))
    ex = json.load(open(P + "station_exits_v1.json", encoding="utf-8"))["exits"]
    seen = {(o["stop"], o["station"], o["straight"]) for o in json.load(open(f"{a.out}/kakao_sample.json", encoding="utf-8"))}
    pop = [r for r in load(a) if (r["stop"], r["station"], int(r["straight_m"])) not in seen]
    tail = [r for r in pop if int(r["min_used"]) - int(r["min_est"]) >= 5]
    random.seed(20261007)
    A = random.sample(pop, 60)
    ida = {id(r) for r in A}
    B = random.sample([r for r in tail if id(r) not in ida], 30)
    out = []
    for grp, rs in (("A", A), ("B", B)):
        for r in rs:
            nm, la, lo = stops[r["stop_id"]]
            c = [e for e in ex[r["station"]] if e.get("ref") == (r["exit"] or None)] or ex[r["station"]]
            e = min(c, key=lambda e: abs(hav(la, lo, e["lat"], e["lng"]) - int(r["straight_m"])))
            out.append({"grp": grp, "stop": nm, "station": r["station"], "exit": r["exit"], "slat": la, "slng": lo, "elat": e["lat"], "elng": e["lng"],
                        "straight": int(r["straight_m"]), "est": int(r["est_m"]), "graph": int(r["graph_m"]), "used": int(r["used_m"]),
                        "optimistic": r["optimistic"] == "True"})
    json.dump(out, open(f"{a.out}/kakao_sample2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print(json.dumps({"population": len(pop), "tail": len(tail), "A_tail": sum(mn(o["used"]) - mn(o["est"]) >= 5 for o in out if o["grp"] == "A"),
                      "A_over2x": sum(o["used"] > 2 * o["straight"] for o in out if o["grp"] == "A")}, ensure_ascii=False))
    print(json.dumps([[i, f'{o["slat"]},{o["slng"]}', f'{o["elat"]},{o["elng"]}'] for i, o in enumerate(out)], separators=(",", ":")))

def table(a):
    S = json.load(open(f"{a.out}/kakao_sample2.json", encoding="utf-8"))
    res = {}
    for grp, name in (("A", "A · 전체에서 무작위 60"), ("B", "B · 5분 이상 긴 쌍에서 무작위 30")):
        rows = [o for o in S if o["grp"] == grp and o.get("kakao")]
        d = {"n": len(rows), "rules": {}}
        val = {nm: [f(o) for o in rows] for nm, f in RULES.items()}
        for nm, vs in val.items():
            r = [v / o["kakao"] for v, o in zip(vs, rows)]
            dm = [mn(v) - mn(o["kakao"]) for v, o in zip(vs, rows)]
            d["rules"][nm] = {"within_25pct": sum(.75 <= x <= 1.25 for x in r), "median": round(st.median(r), 2), "short": sum(x < .75 for x in r),
                              "long": sum(x > 1.25 for x in r), "min_within1": sum(abs(x) <= 1 for x in dm), "min_short2": sum(x <= -2 for x in dm),
                              "min_long2": sum(x >= 2 for x in dm), "min_long5": sum(x >= 5 for x in dm)}
        ok = lambda v, o: .75 <= v / o["kakao"] <= 1.25          # noqa: E731
        a0, a2 = val["길 그대로"], val["직선의 2배 넘으면 직선×1.4"]
        d["cross_길그대로_vs_2배"] = {"둘 다 안": sum(ok(x, o) and ok(y, o) for x, y, o in zip(a0, a2, rows)),
                                  "길 그대로만 안": sum(ok(x, o) and not ok(y, o) for x, y, o in zip(a0, a2, rows)),
                                  "2배 규칙만 안": sum(not ok(x, o) and ok(y, o) for x, y, o in zip(a0, a2, rows)),
                                  "둘 다 밖": sum(not ok(x, o) and not ok(y, o) for x, y, o in zip(a0, a2, rows))}
        tgt = [o for o in rows if o["used"] > 2 * o["straight"]]
        dd = sorted(mn(o["est"]) - mn(o["kakao"]) for o in tgt)
        d["over2x_targets"] = {"n": len(tgt), "되돌린 값이 카카오보다 2분↑ 짧게": sum(x <= -2 for x in dd), "5분↑ 짧게": sum(x <= -5 for x in dd),
                               "±1분": sum(abs(x) <= 1 for x in dd), "분 차이(되돌린 값 − 카카오) 분포": dd,
                               "길 그대로일 때 5분↑ 길게": sum(mn(o["used"]) - mn(o["kakao"]) >= 5 for o in tgt),
                               "길 그대로가 ±25% 안(진짜 우회로 보이는 것)": sum(ok(o["used"], o) for o in tgt)}
        d["optimistic"] = sum(o["optimistic"] for o in rows)
        res[name] = d
    json.dump(res, open(f"{a.out}/x3_kakao_table2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=("pick", "table")); ap.add_argument("--repo", default="."); ap.add_argument("--out", required=True)
    a = ap.parse_args(); (pick if a.cmd == "pick" else table)(a)
