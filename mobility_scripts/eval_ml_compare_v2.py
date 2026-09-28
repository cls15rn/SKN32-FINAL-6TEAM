#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ML 대조 실험 v2 — v1 의 한계 셋을 정면으로 건드린다.

v1(eval_ml_compare.py) 결론: 시간표를 안 보는 분류기는 근거없음을 전부 확정답으로 바꾼다(376/376).
v1 §5 가 적어 둔 한계:  ① 근거없음이 한 구간(9999)뿐  ② 하이퍼파라미터 안 찾음  ③ 「모른다」를 낼 구조가 없음.

v2 는 셋을 각각 실험으로 바꾼다.
  EXP-08  근거없음 구간을 9종으로 늘린다 (final_project_cs/tests/unit/travel/mobility/ml/unknown_seeds_v1.json) — v1 특징 그대로
  EXP-09  + 「소스 존재」 특징 6개 (역이 좌표표에 있나 · 버스 노선이 수집됐나 …) — 시간표 값은 여전히 안 본다
  EXP-10  + class_weight=balanced (근거없음 4% 불균형 보정)
  EXP-11  + HGB 하이퍼파라미터 격자 탐색 (GroupKFold 안에서)
  회귀 85건 외부 시험은 전부 같이 낸다.

되돌리는 조건(v1 §4)의 실측이다: 「모른다」를 낼 수 있는 구조가 생기면 근거없음→성립이 0 이 되는가.
"""
import argparse, collections, json, sys, time, pickle, itertools
from datetime import date as _date
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mobility_scripts.eval_ml_compare import (CLASSES, KO, norm_verdict, featurize, score,        # noqa: E402
                                     flips_in_series)
from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier                                       # noqa: E402
from mobility_scripts.selfcheck_mobility import build_probes, expand_seeds                          # noqa: E402
from app.modules.travel_ops.mobility.engine.line_order import LineOrder                                         # noqa: E402
from app.modules.travel_ops.mobility.engine.transfer_walk import TransferWalk                                   # noqa: E402
from app.modules.travel_ops.mobility.engine.bus import BusRoutes                                                # noqa: E402
from app.modules.travel_ops.mobility.engine.geo import StationCoords                                            # noqa: E402


# ── 「소스 존재」 특징 — 값이 아니라 존재 여부만 본다 ────────────────────
def coverage_features(case, sc, bus):
    """시간표·정류장 '값'은 여전히 안 본다. 참조 표에 그 키가 '있는가'만 본다.
    이게 v1 §5 가 말한 '「소스가 없다」를 봐야 나온다' 의 최소 구현이다."""
    legs = case["legs"]
    n_stn_missing = n_route_missing = n_stop_missing = 0
    for l in legs:
        if l.get("mode") == "bus":
            r = bus.by_nm.get(str(l.get("route"))) if bus else None
            if r is None:
                n_route_missing += 1; n_stop_missing += 2
            else:
                names = {s.get("station_nm") for s in bus.stops.get(r.route_id, [])}
                n_stop_missing += int(l.get("from") not in names) + int(l.get("to") not in names)
        else:
            for nm in (l.get("from"), l.get("to")):
                if sc is None or sc.get(l.get("line"), nm) is None:
                    n_stn_missing += 1
    return {
        "cov_stn_missing": float(n_stn_missing),
        "cov_route_missing": float(n_route_missing),
        "cov_stop_missing": float(n_stop_missing),
        "cov_any_missing": float((n_stn_missing + n_route_missing + n_stop_missing) > 0),
        "cov_all_known": float((n_stn_missing + n_route_missing + n_stop_missing) == 0),
        "cov_missing_ratio": (n_stn_missing + n_route_missing + n_stop_missing) / max(1, 2 * len(legs)),
    }


def hgb(seed, cw=None, **kw):
    from sklearn.ensemble import HistGradientBoostingClassifier
    p = dict(max_iter=300, learning_rate=0.08, max_leaf_nodes=31, random_state=seed)
    p.update(kw)
    return HistGradientBoostingClassifier(class_weight=cw, **p)


def run_cv(X, yi, y, groups, rows, folds, seeds, make, label):
    from sklearn.model_selection import GroupKFold
    dec = lambda a: np.array([CLASSES[int(i)] for i in a])
    out = []
    fails = []
    for seed in seeds:
        rng = np.random.RandomState(seed)
        perm = {g: i for i, g in enumerate(rng.permutation(np.unique(groups)))}
        g2 = np.array([perm[g] for g in groups])
        for fi, (tr, te) in enumerate(GroupKFold(n_splits=folds).split(X, yi, g2)):
            m = make(seed)
            t1 = time.time(); m.fit(X[tr], yi[tr]); fit_s = time.time() - t1
            pred = dec(m.predict(X[te]))
            s = score(y[te], pred)
            fl, ns = flips_in_series([(rows[i]["probe"]["_route"], rows[i]["probe"]["_dir"],
                                       rows[i]["probe"]["_dtag"], rows[i]["probe"]["_depart_min"], pv)
                                      for i, pv in zip(te, pred)])
            s.update({"seed": seed, "fold": fi, "flip_series": fl, "n_series": ns, "fit_s": fit_s})
            out.append(s)
            if seed == seeds[0]:
                for i, pv in zip(te, pred):
                    if pv != y[i]:
                        p = rows[i]["probe"]
                        fails.append({"probe": p["id"], "src": p["_src"], "A": y[i], "C": pv})
    return out, fails


def agg(lst):
    def ms(k):
        a = np.array([x[k] for x in lst], dtype=float); return [float(a.mean()), float(a.std())]
    seed0 = [x for x in lst if x["seed"] == lst[0]["seed"]]
    return {"acc": ms("acc"), "macro_f1": ms("macro_f1"), "unknown_recall": ms("unknown_recall"),
            "flip_series": ms("flip_series"), "fit_s": ms("fit_s"),
            "unknown_to_feasible_seed0": int(sum(x["unknown_to_feasible"] for x in seed0)),
            "unknown_to_definite_seed0": int(sum(x["unknown_to_definite"] for x in seed0)),
            "n_unknown": int(sum(x["n_unknown"] for x in seed0))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", default=["final_project_cs/tests/unit/travel/mobility/*.json", "final_project_cs/tests/unit/travel/mobility/ml/unknown_seeds_v1.json"])
    ap.add_argument("--reg", nargs="+", default=["final_project_cs/tests/unit/travel/mobility/*.json"])
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds-rng", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--base-date", default="2026-09-14")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    from mobility_scripts.collect._paths import PROCESSED
    M = PROCESSED / "mobility"
    out = Path(args.out) if args.out else (M / "ml_compare_v2"); out.mkdir(parents=True, exist_ok=True)
    holidays = set(json.loads((REPO / "final_project_cs/app/modules/travel_ops/mobility/engine/rules/holidays_2026_2027.json").read_text(encoding="utf-8"))["holidays"])
    rules = json.loads((REPO / "final_project_cs/app/modules/travel_ops/mobility/engine/rules/rules_v0.3.json").read_text(encoding="utf-8"))

    routes, dates, probes = build_probes(args.seeds, holidays, args.step, 300, 1680, _date.fromisoformat(args.base_date))
    print(f"구간 {len(routes)}종 → 탐침 {len(probes):,}건")
    wanted = {(l["line"], nm) for p in probes for l in p["legs"] if l.get("line") for nm in (l["from"], l["to"])}
    reg_cases = []
    for p in expand_seeds(args.reg):
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        for c in (doc.get("cases") if isinstance(doc, dict) else doc):
            if c.get("expect"):
                c["_src"] = Path(p).name; reg_cases.append(c)
    wanted |= {(l["line"], nm) for c in reg_cases for l in c["legs"] if l.get("line") for nm in (l["from"], l["to"])}

    tt = Timetable.load(str(M / "timetable_v1.jsonl"), wanted)
    lo = LineOrder.load(str(M / "line_station_order_v1.json"))
    tw = TransferWalk.load(str(M / "transfer_walk_v1.json"), rules["measured_baseline"]["kakao_walk_speed_mps"]["value"])
    bus = BusRoutes.load(str(M / "bus_route_v1.jsonl"), str(M / "bus_stops_v1.jsonl"))
    sc = StationCoords.load(str(M / "station_coords.json"))
    v = Verifier(tt, lo, rules, holidays, tw, bus, sc)

    rows = []
    for i, p in enumerate(probes):
        case = {k: v2 for k, v2 in p.items() if not k.startswith("_")}
        r = v.verify_case(case)
        rows.append({"probe": p, "verdict": norm_verdict(r.verdict), "grade": r.grade})
        if (i + 1) % 2000 == 0: print(f"  A {i+1:,}/{len(probes):,}")
    y = np.array([r["verdict"] for r in rows]); ENC = {c: i for i, c in enumerate(CLASSES)}
    yi = np.array([ENC[t] for t in y]); groups = np.array([r["probe"]["_route"] for r in rows])
    unk_routes = sorted({r["probe"]["_route"] for r in rows if r["verdict"] == "unknown"})
    print(f"A 판정 {dict(collections.Counter(y))} · 근거없음 구간 {len(unk_routes)}종")

    cases = [{k: v2 for k, v2 in r["probe"].items() if not k.startswith("_")} for r in rows]
    f1 = [featurize(c, holidays, sc, bus) for c in cases]
    names1 = list(f1[0].keys())
    X1 = np.array([[f[n] for n in names1] for f in f1], dtype=float)
    f2 = [coverage_features(c, sc, bus) for c in cases]
    names2 = names1 + list(f2[0].keys())
    X2 = np.hstack([X1, np.array([[f[n] for n in f2[0].keys()] for f in f2], dtype=float)])

    S = {"n_probes": len(rows), "n_routes": len(routes), "unknown_routes": len(unk_routes),
         "verdict_dist": {str(k): int(n) for k, n in collections.Counter(y).items()}, "exp": {}, "fails": {}}

    # EXP-08 v1 특징 · 근거없음 9종
    r, f = run_cv(X1, yi, y, groups, rows, args.folds, args.seeds_rng, lambda s: hgb(s), "EXP-08")
    S["exp"]["EXP-08 v1특징·근거없음9종"] = agg(r); S["fails"]["EXP-08"] = f; print("EXP-08", S["exp"]["EXP-08 v1특징·근거없음9종"]["acc"], S["exp"]["EXP-08 v1특징·근거없음9종"]["unknown_recall"])
    # EXP-09 + coverage
    r, f = run_cv(X2, yi, y, groups, rows, args.folds, args.seeds_rng, lambda s: hgb(s), "EXP-09")
    S["exp"]["EXP-09 +소스존재특징"] = agg(r); S["fails"]["EXP-09"] = f; print("EXP-09", S["exp"]["EXP-09 +소스존재특징"]["acc"], S["exp"]["EXP-09 +소스존재특징"]["unknown_recall"])
    # EXP-10 + balanced
    r, f = run_cv(X2, yi, y, groups, rows, args.folds, args.seeds_rng, lambda s: hgb(s, cw="balanced"), "EXP-10")
    S["exp"]["EXP-10 +class_weight"] = agg(r); S["fails"]["EXP-10"] = f; print("EXP-10", S["exp"]["EXP-10 +class_weight"]["acc"], S["exp"]["EXP-10 +class_weight"]["unknown_recall"])
    # EXP-11 grid
    grid = list(itertools.product([0.03, 0.08, 0.15], [15, 31, 63], [150, 300, 500]))
    best = None; grid_res = []
    for lr, leaf, it in grid:
        r, _ = run_cv(X2, yi, y, groups, rows, args.folds, [args.seeds_rng[0]],
                      lambda s, lr=lr, leaf=leaf, it=it: hgb(s, cw="balanced", learning_rate=lr, max_leaf_nodes=leaf, max_iter=it), "grid")
        a = agg(r); a.update({"learning_rate": lr, "max_leaf_nodes": leaf, "max_iter": it}); grid_res.append(a)
        key = (a["macro_f1"][0], -a["unknown_to_feasible_seed0"])
        if best is None or key > best[0]: best = (key, (lr, leaf, it))
    lr, leaf, it = best[1]
    r, f = run_cv(X2, yi, y, groups, rows, args.folds, args.seeds_rng,
                  lambda s: hgb(s, cw="balanced", learning_rate=lr, max_leaf_nodes=leaf, max_iter=it), "EXP-11")
    S["exp"][f"EXP-11 격자탐색 best lr={lr} leaf={leaf} iter={it}"] = agg(r); S["fails"]["EXP-11"] = f
    S["grid"] = grid_res
    print("EXP-11", best[1], S["exp"][f"EXP-11 격자탐색 best lr={lr} leaf={leaf} iter={it}"]["acc"])

    # 회귀 85 외부 시험 — 각 설정을 탐침 전체로 학습
    dec = lambda a: np.array([CLASSES[int(i)] for i in a])
    yr = np.array([norm_verdict(c["expect"]) for c in reg_cases])
    Xr1 = np.array([[featurize(c, holidays, sc, bus)[n] for n in names1] for c in reg_cases], dtype=float)
    Xr2 = np.hstack([Xr1, np.array([[coverage_features(c, sc, bus)[n] for n in f2[0].keys()] for c in reg_cases], dtype=float)])
    S["regression"] = {"n": len(reg_cases), "A": {"acc": 1.0, "match": len(reg_cases)}, "C": {}, "fails": {}}
    for label, X, Xr, mk in [("EXP-08", X1, Xr1, lambda s: hgb(s)), ("EXP-09", X2, Xr2, lambda s: hgb(s)),
                             ("EXP-10", X2, Xr2, lambda s: hgb(s, cw="balanced")),
                             ("EXP-11", X2, Xr2, lambda s: hgb(s, cw="balanced", learning_rate=lr, max_leaf_nodes=leaf, max_iter=it))]:
        m = mk(args.seeds_rng[0]); m.fit(X, yi); pred = dec(m.predict(Xr))
        s = score(yr, pred); s["match"] = int(round(s["acc"] * len(yr))); S["regression"]["C"][label] = s
        S["regression"]["fails"][label] = [{"id": c["id"], "expect": t, "C": p, "note": (c.get("note") or "")[:50]}
                                           for c, t, p in zip(reg_cases, yr, pred) if t != p]
        if label == "EXP-11":
            pickle.dump({"model": m, "features": names2, "classes": CLASSES, "trained_on": f"probes {len(rows)} step {args.step} +coverage"},
                        open(out / "model_hgb_v2.pkl", "wb"))
    S["features_v2"] = names2
    (out / "summary.json").write_text(json.dumps(S, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(out / "report.md", S)
    print(f"→ {out}")


def write_md(path, S):
    L = ["# ML 대조 실험 v2 — 한계 셋을 실험으로\n",
         f"탐침 {S['n_probes']:,}건 · 구간 {S['n_routes']}종 · 근거없음 구간 {S['unknown_routes']}종 · A 판정 {S['verdict_dist']}\n",
         "## 1. 탐침 — 구간 단위 GroupKFold 5 × 시드 5 (EXP-11 은 격자탐색 후 시드 5)\n",
         "| 실험 | 정확도 | macro-F1 | 근거없음 재현율 | 근거없음→성립 (시드0) | 근거없음→확정답 | FLIP/폴드 |",
         "|---|---|---|---|---|---|---|"]
    for k, r in S["exp"].items():
        L.append(f"| {k} | {r['acc'][0]:.3f}±{r['acc'][1]:.3f} | {r['macro_f1'][0]:.3f} | {r['unknown_recall'][0]:.3f} | "
                 f"**{r['unknown_to_feasible_seed0']}/{r['n_unknown']}** | {r['unknown_to_definite_seed0']}/{r['n_unknown']} | {r['flip_series'][0]:.1f} |")
    L.append("| A 현행 | 1.000 | — | 1.000 | 0 | 0 | 0 |\n")
    R = S["regression"]
    L += [f"## 2. 회귀 {R['n']}건 외부 시험\n", "| 경로 | 일치 | 근거없음→성립 | 근거없음→확정답 |", "|---|---|---|---|",
          f"| A 현행 | {R['n']}/{R['n']} | 0/2 | 0/2 |"]
    for k, s in R["C"].items():
        L.append(f"| C {k} | {s['match']}/{R['n']} | {s['unknown_to_feasible']}/{s['n_unknown']} | {s['unknown_to_definite']}/{s['n_unknown']} |")
    L.append("\n### EXP-11 이 회귀에서 틀린 것\n")
    for f in R["fails"].get("EXP-11", []):
        L.append(f"- `{f['id']}` 기대 {KO.get(f['expect'], f['expect'])} → C {KO.get(f['C'], f['C'])} — {f['note']}")
    L.append("\n## 3. 격자 탐색 (시드 0 · GroupKFold 5) 상위 5\n")
    L.append("| lr | leaf | iter | 정확도 | macro-F1 | 근거없음→성립 |"); L.append("|---|---|---|---|---|---|")
    for g in sorted(S["grid"], key=lambda g: (-g["macro_f1"][0], g["unknown_to_feasible_seed0"]))[:5]:
        L.append(f"| {g['learning_rate']} | {g['max_leaf_nodes']} | {g['max_iter']} | {g['acc'][0]:.3f} | {g['macro_f1'][0]:.3f} | {g['unknown_to_feasible_seed0']} |")
    L.append("\n## 4. 소스 존재 특징 6개\n")
    L.append(", ".join(f"`{n}`" for n in S["features_v2"] if n.startswith("cov_")))
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
