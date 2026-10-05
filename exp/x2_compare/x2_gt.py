# -*- coding: utf-8 -*-
"""X2 ④ — 실측(티머니 태그 여정) 중 지하철 구간: MOTIS 쪽 도착 · 우리 예정 도착 · 실측 도착 세 값.

입력  <out>/gt/field_legs_v1.json — 평가 지표 방(28)이 만든 실측 26여정(개인 이동 기록 · git 에 올리지 않는다)
단위  ① 지하철만으로 된 여정(출발 = 첫 승차 태그 · 실측 도착 = 마지막 하차 태그)
      ② 버스+지하철 여정의 지하철 구간(출발 = 그 구간 승차 태그 · 실측 도착 = 그 구간 하차 태그)
읽는 법
  · 태그는 **개찰구** 시각이다. 두 엔진의 도착은 **승강장의 열차** 시각이다 → 실측에는 개찰구↔승강장 걷는 시간과
    열차 대기가 들어 있다. 세 값의 차는 「누가 맞나」가 아니라 「개찰구 사이 실제 걸린 시간에 각 값이 얼마나 가까운가」다.
  · 시간표는 2026-10-01 수집 판이고 실측은 9월이다(판이 다를 수 있다).
  · 표본은 한 사람의 출퇴근 노선(3·7호선 남부터미널↔남성 · 7호선 내방→남성 · 2호선 1)이다.
MOTIS 는 떠 있어야 한다(x2_run.ps1 이 띄운 동안 부른다).
"""
import argparse, csv, json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import x2_compare as C
from x2_build import load_lookup, to_min


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\final_project\exp\x2_compare")
    ap.add_argument("--lookup", default=r"C:\final_project\exp\x1_gtfs\out\stop_lookup.csv")
    ap.add_argument("--transfers", default=r"C:\final_project\exp\x1_gtfs\out\gtfs\transfers.txt")
    ap.add_argument("--a", default="http://127.0.0.1:8080")
    a = ap.parse_args()
    out = Path(a.out)
    src = out / "gt" / "field_legs_v1.json"
    cases = json.loads(src.read_text(encoding="utf-8"))["cases"]
    lk = load_lookup(a.lookup)
    C.load_xfer(a.transfers)
    units = []
    for c in cases:
        legs, f = c["legs"], c["field"]
        sub = [l.get("mode", "subway") == "subway" and "line" in l for l in legs]
        if all(sub):
            units.append(dict(id=c["id"], kind="여정", date=c["date"], depart_at=f["actual_first_board"], legs=legs,
                              truth=f["actual_last_alight"]))
        elif any(sub) and len(f["legs_truth"]) == len(legs):
            for l, t, s in zip(legs, f["legs_truth"], sub):
                if s:
                    units.append(dict(id=c["id"], kind="구간", date=c["date"], depart_at=t["board"], legs=[l], truth=t["alight"]))
    # 우리 판정 — 기준 판 판정기를 같은 출발 시각으로(읽기만 · 자료는 저장소 기준 판)
    probe = {"cases": [dict(id=f"GT-{i}", date=u["date"], depart_at=u["depart_at"], legs=u["legs"]) for i, u in enumerate(units)]}
    with tempfile.TemporaryDirectory() as td:
        pj, rj = Path(td) / "probe.json", Path(td) / "res.json"
        pj.write_text(json.dumps(probe, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, DATA_DIR=str(REPO / "datasets" / "mobility" / "processed"), PYTHONUTF8="1")
        subprocess.run([sys.executable, "-m", "app.modules.travel_ops.mobility.engine.verify_time", "--cases", str(pj), "--json", str(rj)],
                       cwd=REPO / "final_project_cs", env=env, stdout=subprocess.DEVNULL, check=False)
        res = {r["id"]: r for r in json.loads(rj.read_text(encoding="utf-8"))}
    rows = []
    for i, u in enumerate(units):
        r = res[f"GT-{i}"]
        stops = [[lk[(l["line"], l["from"])], lk[(l["line"], l["to"])]] for l in u["legs"]]
        dep = to_min(u["depart_at"])
        q = dict(date=u["date"], legs=[[l["line"], l["from"], l["to"]] for l in u["legs"]], leg_stops=stops,
                 from_stop=stops[0][0], to_stop=stops[-1][1])
        m = C.chain(a.a, q, dep, False)
        truth = to_min(u["truth"])
        ours = r.get("arrive_min") if (r.get("out") or {}).get("verdict") == "feasible" else None
        worst = (ours + r["margin_min"]) if ours is not None and r.get("margin_min") is not None else None
        ma = m["arr"] if C.in_day(m, dep) else None
        rows.append({"id": u["id"], "단위": u["kind"], "날짜": u["date"], "경로": " → ".join(f"{l['line']} {l['from']}→{l['to']}" for l in u["legs"]),
                     "출발(승차 태그)": u["depart_at"], "실측 도착(하차 태그)": u["truth"], "우리 예정": C.fmt(ours), "우리 예정+@": C.fmt(worst),
                     "MOTIS 쪽": C.fmt(ma), "우리−실측": "" if ours is None else ours - truth, "MOTIS−실측": "" if ma is None else ma - truth,
                     "실측 ≤ 우리+@": "" if worst is None else int(truth <= worst)})
    with open(out / "gt_compare.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"실측 여정 {len(cases)} 중 지하철 단위 {len(rows)} (여정 {sum(1 for r in rows if r['단위']=='여정')} · 구간 {sum(1 for r in rows if r['단위']=='구간')}) · 나머지는 버스만")
    print("| " + " | ".join(rows[0]) + " |\n|" + "---|" * len(rows[0]))
    for r in rows:
        print("| " + " | ".join(str(v) for v in r.values()) + " |")
    do = [r["우리−실측"] for r in rows if r["우리−실측"] != ""]; dm = [r["MOTIS−실측"] for r in rows if r["MOTIS−실측"] != ""]
    med = lambda x: sorted(x)[len(x) // 2]
    print(f"\n우리−실측(분): {sorted(do)} · 절대값 중앙 {med([abs(x) for x in do])} · ±3분 안 {sum(1 for x in do if abs(x) <= 3)}/{len(do)}")
    print(f"MOTIS−실측(분): {sorted(dm)} · 절대값 중앙 {med([abs(x) for x in dm])} · ±3분 안 {sum(1 for x in dm if abs(x) <= 3)}/{len(dm)}")
    print(f"실측 도착 ≤ 우리 예정+@: {sum(r['실측 ≤ 우리+@'] for r in rows if r['실측 ≤ 우리+@'] != '')}/{sum(1 for r in rows if r['실측 ≤ 우리+@'] != '')} · 실측이 우리 예정보다 늦음 {sum(1 for x in do if x < 0)}/{len(do)} · MOTIS 쪽보다 늦음 {sum(1 for x in dm if x < 0)}/{len(dm)}")


if __name__ == "__main__":
    main()
