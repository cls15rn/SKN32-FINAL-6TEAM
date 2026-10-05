# -*- coding: utf-8 -*-
"""X2 ① — 회귀 199건의 legs 를 MOTIS 질의 목록으로 바꾼다 (실험 · exp-mobility · 발표 코드 읽기만).

입력
  · 회귀 14파일  final_project_cs/tests/unit/travel/mobility/*_legs_v1.json   (199건)
  · 우리 판정    <out>/ours/<묶음>.json   — run_ours.ps1 이 `verify_time --json` 으로 만든 것(기준 판)
                 synthetic 은 실 시간표로 다시 돌린 synthetic_real.json 을 쓴다(MOTIS 는 실 시간표만 싣는다)
  · 역↔stop_id   x1_gtfs/out/stop_lookup.csv   (MOTIS 안에서는 x1_ + stop_id)
출력  <out>/queries.jsonl — 한 줄 = 비교 단위 하나
  unit = case  : 케이스 199건. kind 로 비교 대상/비교 밖을 가른다
  unit = cand  : multi 케이스의 후보 각각(지하철만으로 된 후보)
  unit = leg   : 우리 판정이 성립으로 통과시킨 지하철 구간 하나(같은 노선 직행 대조용)
시각은 전부 「운행일 분」(04:00 전은 +24h — 판정기 timeutil 과 같은 자).
"""
import argparse, csv, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CASES_DIR = REPO / "final_project_cs" / "tests" / "unit" / "travel" / "mobility"
BUNDLES = ["synthetic", "real", "issue", "alt", "bus", "mixed", "multi", "car", "bike", "judgment",
           "night", "bus_profile", "bus_noprof", "destfill"]
LOAD_FIRST, LOAD_DAYS = "2026-09-01", 90      # MOTIS config.yml 과 같은 값


def to_min(hhmm):
    h, m = hhmm.split(":")[:2]
    v = int(h) * 60 + int(m)
    return v + 1440 if v < 240 else v           # 04:00 전 = 그 운행일의 연장


def in_window(date):
    import datetime as dt
    d0 = dt.date.fromisoformat(LOAD_FIRST)
    d = dt.date.fromisoformat(date)
    return d0 <= d and (d - d0).days + 1 < LOAD_DAYS     # 운행일 다음 날 새벽까지 실려 있어야 한다


def load_lookup(path):
    m = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            m[(r["line"], r["stop_name"])] = "x1_" + r["stop_id"]
    return m


def is_subway(leg):
    return leg.get("mode", "subway") == "subway" and "line" in leg


def stop_of(lk, line, name):
    return lk.get((line, name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\final_project\exp\x2_compare")
    ap.add_argument("--lookup", default=r"C:\final_project\exp\x1_gtfs\out\stop_lookup.csv")
    a = ap.parse_args()
    out = Path(a.out)
    lk = load_lookup(a.lookup)
    rows, tally = [], {}

    def bump(k):
        tally[k] = tally.get(k, 0) + 1

    def path_units(base, legs, legres, depart_min, ours, unit):
        """지하철만으로 된 구간열 하나 → 끝에서 끝 질의 1 + 구간별 직행 질의 n."""
        stops, miss = [], []
        for l in legs:
            s, t = stop_of(lk, l["line"], l["from"]), stop_of(lk, l["line"], l["to"])
            if not s: miss.append(f'{l["line"]} {l["from"]}')
            if not t: miss.append(f'{l["line"]} {l["to"]}')
            stops.append((s, t))
        legres = [x for x in (legres or []) if not str(x.get("label", "")).startswith("환승")]   # 판정 결과에는 「환승 ○○」 줄이 끼어 있다
        q = dict(base, unit=unit, legs=[[l["line"], l["from"], l["to"]] for l in legs],
                 depart_min=depart_min, **ours)
        if miss:
            q.update(kind="비교밖", why="역 없음(GTFS 에 그 노선·역이 없다): " + " · ".join(miss))
            rows.append(q); bump(f"{unit}:비교밖-역없음"); return
        q.update(kind="비교", from_stop=stops[0][0], to_stop=stops[-1][1], leg_stops=stops)
        rows.append(q); bump(f"{unit}:비교")
        for i, (l, (s, t)) in enumerate(zip(legs, stops)):
            lr = legres[i] if legres and i < len(legres) else None
            if not lr or lr.get("verdict") != "feasible" or lr.get("depart_min") is None:
                continue
            ready = lr["depart_min"] - (lr.get("wait_min") or 0)
            rows.append(dict(base, unit="leg", kind="비교", leg_idx=i, parent=unit, legs=[[l["line"], l["from"], l["to"]]],
                             from_stop=s, to_stop=t, depart_min=int(round(ready)),
                             ours_board_min=lr["depart_min"], ours_arrive_min=lr["arrive_min"]))
            bump("leg:비교")

    for b in BUNDLES:
        cases = json.loads((CASES_DIR / f"{b}_legs_v1.json").read_text(encoding="utf-8"))["cases"]
        res_file = out / "ours" / ("synthetic_real.json" if b == "synthetic" else f"{b}.json")
        res = {r["id"]: r for r in json.loads(res_file.read_text(encoding="utf-8"))}
        for c in cases:
            r = res[c["id"]]
            o = r.get("out") or {}
            base = dict(id=c["id"], bundle=b, date=c["date"])
            ours = dict(ours_verdict=o.get("verdict"), ours_code=o.get("code"), ours_internal=r.get("verdict"),
                        ours_arrive_min=r.get("arrive_min"), ours_last_depart_min=r.get("last_feasible_depart_min"),
                        ours_margin_min=r.get("margin_min"), ours_reason=(r.get("reason") or "")[:160],
                        arrive_by_min=to_min(c["arrive_by"]) if c.get("arrive_by") else None)
            dep = to_min(c["depart_at"])
            if not in_window(c["date"]):
                rows.append(dict(base, unit="case", kind="비교밖", why="적재 기간 밖", **ours)); bump("case:비교밖-기간"); continue
            if c.get("disruptions"):
                rows.append(dict(base, unit="case", kind="비교밖", why="이슈(무정차·중단) — MOTIS 에 없는 입력(판정 층)", **ours))
                bump("case:비교밖-이슈"); continue
            if "multi" in c:
                cands = r.get("candidates") or []
                sub = [k for k in cands if k.get("legs") and all(is_subway(l) for l in k["legs"])]
                if c["multi"].get("mixed") and not sub:
                    rows.append(dict(base, unit="case", kind="비교밖", why="혼합(버스 포함) 후보만 — GTFS 에 버스 없음", **ours))
                    bump("case:비교밖-버스"); continue
                if not sub:
                    rows.append(dict(base, unit="case", kind="비교밖", why="지하철 후보 없음", **ours)); bump("case:비교밖-후보없음"); continue
                # 케이스 단위: 후보 중 성립이면서 도착이 가장 이른 것(없으면 첫 후보)을 대표로 삼아 MOTIS 자유 탐색과 댄다
                ok = [k for k in sub if k.get("verdict") == "feasible" and k.get("arrive_min") is not None]
                rep = min(ok, key=lambda k: k["arrive_min"]) if ok else sub[0]
                s, t = stop_of(lk, rep["legs"][0]["line"], rep["legs"][0]["from"]), stop_of(lk, rep["legs"][-1]["line"], rep["legs"][-1]["to"])
                # 우리 쪽 성립/불가도 **지하철 후보만** 본다(버스 후보로 성립한 케이스를 MOTIS 지하철 판과 대면 층이 섞인다)
                oc = dict(ours, ours_arrive_min=rep.get("arrive_min") if ok else None,
                          ours_verdict_all=ours["ours_verdict"], ours_verdict="feasible" if ok else "infeasible")
                rows.append(dict(base, unit="case", kind="비교" if s and t else "비교밖", multi=True, free=True,
                                 from_stop=s, to_stop=t, depart_min=dep,
                                 legs=[[l["line"], l["from"], l["to"]] for l in rep["legs"]], **oc))
                bump("case:비교(multi)")
                for k in sub:
                    oo = dict(ours_verdict="feasible" if k.get("verdict") == "feasible" else "infeasible",
                              ours_code=None, ours_internal=k.get("verdict"), ours_arrive_min=k.get("arrive_min"),
                              ours_last_depart_min=None, ours_margin_min=ours["ours_margin_min"],
                              ours_reason=(k.get("reason") or "")[:160], arrive_by_min=ours["arrive_by_min"])
                    path_units(dict(base, cand=k.get("n"), criteria=k.get("criteria")), k["legs"], k.get("legs_result"),
                               dep, oo, "cand")
                continue
            legs = c["legs"]
            if not all(is_subway(l) for l in legs):
                modes = sorted({l.get("mode", "subway") for l in legs})
                rows.append(dict(base, unit="case", kind="비교밖", why="GTFS 에 없는 수단: " + "+".join(modes), **ours))
                bump("case:비교밖-수단")
                # 섞인 케이스 안의 지하철 구간은 구간 단위로만 댄다
                lrs = [x for x in (r.get("legs") or []) if not str(x.get("label", "")).startswith("환승")]
                for i, l in enumerate(legs):
                    lr = lrs[i] if i < len(lrs) else None
                    if is_subway(l) and lr and lr.get("verdict") == "feasible" and lr.get("depart_min") is not None:
                        s, t = stop_of(lk, l["line"], l["from"]), stop_of(lk, l["line"], l["to"])
                        if s and t:
                            rows.append(dict(base, unit="leg", kind="비교", leg_idx=i, parent="case-섞임",
                                             legs=[[l["line"], l["from"], l["to"]]], from_stop=s, to_stop=t,
                                             depart_min=int(round(lr["depart_min"] - (lr.get("wait_min") or 0))),
                                             ours_board_min=lr["depart_min"], ours_arrive_min=lr["arrive_min"]))
                            bump("leg:비교")
                continue
            path_units(base, legs, r.get("legs"), dep, ours, "case")

    out.mkdir(parents=True, exist_ok=True)
    with open(out / "queries.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for q in rows:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    n_case = sum(1 for q in rows if q["unit"] == "case")
    print(f"케이스 {n_case}건 → 비교 {sum(1 for q in rows if q['unit']=='case' and q['kind']=='비교')} · "
          f"비교 밖 {sum(1 for q in rows if q['unit']=='case' and q['kind']!='비교')}")
    for k in sorted(tally):
        print(f"  {k}: {tally[k]}")
    print(f"→ {out / 'queries.jsonl'} ({len(rows)}줄)")
    if n_case != 199:
        print(f"★ 케이스 수가 199 가 아니다: {n_case}"); sys.exit(1)


if __name__ == "__main__":
    main()
