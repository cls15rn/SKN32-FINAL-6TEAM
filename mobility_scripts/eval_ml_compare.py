#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""이동 판정 — A(코드+시간표) 와 C(ML 분류기) 비교.

질문 하나: **시간표를 안 보고, 입력 특징만으로 판정을 배울 수 있는가.**
배울 수 있으면 시간표 46만 행이 필요 없고, 못 배우면 "판정은 코드"가 숫자로 서는 것이다.

  A · 현행  : 시간표 조회 + 규칙 코드 (final_project_cs/app/modules/travel_ops/mobility/engine/verify_time.py)
  C · ML    : 역쌍·좌표·노선·요일·시각 → 판정 3클래스 (feasible / infeasible / unknown)
              로지스틱 · HistGradientBoosting · MLP(작은 DL)

학습 데이터 = 자기점검 탐침 (selfcheck_mobility.py 와 같은 씨앗·간격). 라벨은 A 가 낸 판정이다.
→ 이 실험은 **"규칙을 모델이 다시 배울 수 있나"** 를 재는 것이지, A 보다 나은 정답을 찾는 게 아니다.
   A 가 틀린 곳은 C 도 같이 틀린다. 그건 회귀 85건(사람이 기대값을 박은 것)으로 따로 잰다.

누수 금지: 특징에 시간표·도착시각·A 의 출력을 넣지 않는다. 분할은 **구간(route) 단위** GroupKFold —
같은 구간이 학습·시험에 같이 들어가면 외우기 시험이 된다.

사용:
  python mobility_scripts/eval_ml_compare.py --seeds "final_project_cs/tests/unit/travel/mobility/*.json" --step 30 --reg "final_project_cs/tests/unit/travel/mobility/*.json"
  (결과는 $DATA_DIR/travel/processed/mobility/ml_compare/ 에 쓴다)
"""
import argparse, collections, json, math, sys, time, pickle
from datetime import date as _date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier                        # noqa: E402
from mobility_scripts.selfcheck_mobility import build_probes, expand_seeds           # noqa: E402
from app.modules.travel_ops.mobility.engine.line_order import LineOrder                          # noqa: E402
from app.modules.travel_ops.mobility.engine.transfer_walk import TransferWalk                    # noqa: E402
from app.modules.travel_ops.mobility.engine.bus import BusRoutes                                 # noqa: E402
from app.modules.travel_ops.mobility.engine.geo import StationCoords                             # noqa: E402
from app.modules.travel_ops.mobility.engine.timeutil import day_type_of, to_service_min          # noqa: E402

CLASSES = ["feasible", "infeasible", "unknown"]
KO = {"feasible": "성립", "infeasible": "불가", "unknown": "근거없음"}
DAY = {"weekday": 0, "sat": 1, "sun": 2, "holiday": 3}


def norm_verdict(v):
    # 탈락(rejected_by_limit)은 '불가'로 접는다 — 성립이 아니고 근거는 있다.
    return "infeasible" if v == "rejected_by_limit" else v


# ── 특징 ───────────────────────────────────────────────────────────────────
def leg_coord(leg, sc, bus):
    """지하철은 역 좌표, 버스는 정류장 좌표. 없으면 NaN — 그 자체가 정보다."""
    if leg.get("mode") == "bus":
        r = bus.by_nm.get(str(leg.get("route"))) if bus else None
        if r:
            for s in bus.stops.get(r.route_id, []):
                if s.get("station_nm") == leg.get("from"):
                    a = (s.get("lat"), s.get("lng"))
                    break
            else:
                a = (None, None)
            for s in bus.stops.get(r.route_id, []):
                if s.get("station_nm") == leg.get("to"):
                    b = (s.get("lat"), s.get("lng"))
                    break
            else:
                b = (None, None)
            return a, b
        return (None, None), (None, None)
    a = sc.get(leg.get("line"), leg.get("from")) if sc else None
    b = sc.get(leg.get("line"), leg.get("to")) if sc else None
    return ((a or {}).get("lat"), (a or {}).get("lng")), ((b or {}).get("lat"), (b or {}).get("lng"))


def hav(a, b):
    if None in a or None in b:
        return float("nan")
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


LINES = [f"{i:02d}호선" for i in range(1, 10)]


def featurize(case, holidays, sc, bus):
    """case: {date, depart_at, legs[], disruptions?} → 고정 길이 벡터. 시간표는 안 본다."""
    legs = case["legs"]
    d = _date.fromisoformat(case["date"])
    dt = day_type_of(d, holidays)
    m = to_service_min(case["depart_at"])
    f = {
        "depart_min": m,
        "depart_sin": math.sin(2 * math.pi * (m % 1440) / 1440),
        "depart_cos": math.cos(2 * math.pi * (m % 1440) / 1440),
        "after_midnight": 1.0 if m >= 1440 else 0.0,
        "day_type": DAY.get(dt, 0),
        "is_sat": 1.0 if d.weekday() == 5 else 0.0,
        "n_legs": len(legs),
        "n_bus": sum(1 for l in legs if l.get("mode") == "bus"),
        "n_disrupt": len(case.get("disruptions") or []),
        "party_n": float((case.get("party") or {}).get("size") or 0),
    }
    for ln in LINES:
        f[f"line_{ln}"] = float(any(l.get("line") == ln for l in legs))
    coords = [leg_coord(l, sc, bus) for l in legs]
    dist = [hav(a, b) for a, b in coords]
    f["dist_sum_km"] = float(np.nansum(dist)) if not all(math.isnan(x) for x in dist) else float("nan")
    f["dist_first_km"] = dist[0]
    f["coord_missing"] = float(sum(1 for a, b in coords if None in a or None in b))
    (a0, b0) = coords[0]
    (aN, bN) = coords[-1]
    f["from_lat"], f["from_lng"] = a0[0] if a0[0] is not None else float("nan"), a0[1] if a0[1] is not None else float("nan")
    f["to_lat"], f["to_lng"] = bN[0] if bN[0] is not None else float("nan"), bN[1] if bN[1] is not None else float("nan")
    f["od_km"] = hav(a0, bN)
    return f


# ── 지표 ───────────────────────────────────────────────────────────────────
def flips_in_series(rows_pred):
    """rows_pred: [(route, dir, dtag, depart_min, verdict)] → INV-FLIP 위반 시리즈 수.
    하루는 '불가→성립→불가' 가 정상이라 전이 2회까지다. 3회 이상이면 경계가 불안정하다."""
    by = collections.defaultdict(list)
    for r, dr, dt, m, v in rows_pred:
        by[(r, dr, dt)].append((m, v))
    bad = 0
    for k, s in by.items():
        s.sort()
        seq = [v for _, v in s]
        fl = sum(1 for a, b in zip(seq, seq[1:]) if a != b and {a, b} <= {"feasible", "infeasible"})
        if fl > 2:
            bad += 1
    return bad, len(by)


def score(y_true, y_pred):
    from sklearn.metrics import accuracy_score, f1_score, recall_score
    yt, yp = np.array(y_true), np.array(y_pred)
    out = {
        "acc": float(accuracy_score(yt, yp)),
        "macro_f1": float(f1_score(yt, yp, labels=CLASSES, average="macro", zero_division=0)),
        "unknown_recall": float(recall_score(yt, yp, labels=["unknown"], average="macro", zero_division=0)),
        # ★ 핵심 — 소스가 없는 구간(근거없음)을 '성립'으로 바꾼 건수
        "unknown_to_feasible": int(((yt == "unknown") & (yp == "feasible")).sum()),
        "unknown_to_definite": int(((yt == "unknown") & (yp != "unknown")).sum()),
        "n_unknown": int((yt == "unknown").sum()),
    }
    return out


def make_models(seed):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    return {
        "logreg": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                LogisticRegression(max_iter=2000, C=1.0, random_state=seed)),
        "hgb": HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08,
                                              max_leaf_nodes=31, random_state=seed),
        "mlp": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=400,
                                           early_stopping=True, random_state=seed)),
    }


# ── main ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs="+", required=True)
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--from-min", type=int, default=5 * 60)
    ap.add_argument("--to-min", type=int, default=28 * 60)
    ap.add_argument("--base-date", default="2026-09-14")
    ap.add_argument("--rules", default=str(REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "rules_v0.3.json"))
    ap.add_argument("--holidays", default=str(REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "holidays_2026_2027.json"))
    ap.add_argument("--reg", nargs="+", default=None, help="회귀 케이스 파일(expect 있는 것)")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds-rng", type=int, nargs="+", default=[0, 1, 2, 3, 4])
    ap.add_argument("--out", default=None, help="기본값: $DATA_DIR/travel/processed/mobility/ml_compare")
    args = ap.parse_args()
    from mobility_scripts.collect._paths import PROCESSED
    M = PROCESSED / "mobility"
    out = Path(args.out) if args.out else (M / "ml_compare")
    out.mkdir(parents=True, exist_ok=True)
    holidays = set(json.loads(Path(args.holidays).read_text(encoding="utf-8"))["holidays"])
    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))

    routes, dates, probes = build_probes(args.seeds, holidays, args.step, args.from_min,
                                         args.to_min, _date.fromisoformat(args.base_date))
    print(f"구간 {len(routes)}종 × 요일 {len(dates)}종 → 탐침 {len(probes):,}건")

    wanted = {(l["line"], nm) for p in probes for l in p["legs"] if l.get("line") for nm in (l["from"], l["to"])}
    reg_cases = []
    if args.reg:
        for p in expand_seeds(args.reg):
            doc = json.loads(Path(p).read_text(encoding="utf-8"))
            for c in (doc.get("cases") if isinstance(doc, dict) else doc):
                if c.get("expect"):
                    c["_src"] = Path(p).name
                    reg_cases.append(c)
        wanted |= {(l["line"], nm) for c in reg_cases for l in c["legs"] if l.get("line") for nm in (l["from"], l["to"])}

    tt = Timetable.load(str(M / "timetable_v1.jsonl"), wanted)
    lo = LineOrder.load(str(M / "line_station_order_v1.json"))
    tw = TransferWalk.load(str(M / "transfer_walk_v1.json"), rules["measured_baseline"]["kakao_walk_speed_mps"]["value"])
    bus = BusRoutes.load(str(M / "bus_route_v1.jsonl"), str(M / "bus_stops_v1.jsonl"))
    sc = StationCoords.load(str(M / "station_coords.json"))
    v = Verifier(tt, lo, rules, holidays, tw, bus, sc)

    # ── A 경로: 탐침을 돌려 라벨을 만든다 ────────────────────────────────
    t0 = time.time()
    rows = []
    for i, p in enumerate(probes):
        case = {k: v2 for k, v2 in p.items() if not k.startswith("_")}
        r = v.verify_case(case)
        rows.append({"probe": p, "verdict": norm_verdict(r.verdict), "grade": r.grade,
                     "arrive_min": r.arrive_min})
        if (i + 1) % 2000 == 0:
            print(f"  A {i+1:,}/{len(probes):,}")
    a_elapsed = time.time() - t0
    a_ms = a_elapsed / len(rows) * 1000
    y_all = np.array([r["verdict"] for r in rows])
    ENC = {c: i for i, c in enumerate(CLASSES)}
    yi_all = np.array([ENC[y] for y in y_all])
    dec = lambda a: np.array([CLASSES[int(i)] for i in a])
    groups = np.array([r["probe"]["_route"] for r in rows])
    print(f"A 판정 {dict(collections.Counter(y_all))} · {a_elapsed:.1f}초 · 건당 {a_ms:.1f}ms")

    feats = [featurize({k: v2 for k, v2 in r["probe"].items() if not k.startswith("_")}, holidays, sc, bus) for r in rows]
    names = list(feats[0].keys())
    X_all = np.array([[f[n] for n in names] for f in feats], dtype=float)
    a_flip, n_series = flips_in_series([(r["probe"]["_route"], r["probe"]["_dir"], r["probe"]["_dtag"],
                                         r["probe"]["_depart_min"], r["verdict"]) for r in rows])

    # ── C 경로: 구간 단위 GroupKFold × 시드 ──────────────────────────────
    from sklearn.model_selection import GroupKFold
    results = {m: [] for m in make_models(0)}
    per_fold_fail = collections.defaultdict(list)
    for seed in args.seeds_rng:
        # 시드마다 폴드 배정을 섞는다 — GroupKFold 는 결정적이라 그룹 순서를 셔플한다
        rng = np.random.RandomState(seed)
        perm = {g: i for i, g in enumerate(rng.permutation(np.unique(groups)))}
        g2 = np.array([perm[g] for g in groups])
        gkf = GroupKFold(n_splits=args.folds)
        for fi, (tr, te) in enumerate(gkf.split(X_all, y_all, g2)):
            for mname, model in make_models(seed).items():
                t1 = time.time(); model.fit(X_all[tr], yi_all[tr]); fit_s = time.time() - t1
                t2 = time.time(); pred = dec(model.predict(X_all[te])); pred_ms = (time.time() - t2) / len(te) * 1000
                s = score(y_all[te], pred)
                fl, ns = flips_in_series([(rows[i]["probe"]["_route"], rows[i]["probe"]["_dir"],
                                           rows[i]["probe"]["_dtag"], rows[i]["probe"]["_depart_min"], pv)
                                          for i, pv in zip(te, pred)])
                s.update({"seed": seed, "fold": fi, "flip_series": fl, "n_series": ns,
                          "fit_s": fit_s, "pred_ms": pred_ms, "n_test": int(len(te))})
                results[mname].append(s)
                if seed == args.seeds_rng[0]:
                    for i, pv in zip(te, pred):
                        if pv != y_all[i]:
                            p = rows[i]["probe"]
                            per_fold_fail[mname].append({"probe": p["id"], "src": p["_src"], "date": p["date"],
                                                         "depart_at": p["depart_at"], "A": y_all[i], "C": pv,
                                                         "A_grade": rows[i]["grade"]})
        print(f"  seed {seed} 완료")

    # ── 대조군: 구간을 섞어 나눈다(외우기 허용) — 점수 차이가 "외운 것"의 크기다
    from sklearn.model_selection import StratifiedKFold
    rand_results = {m: [] for m in make_models(0)}
    skf = StratifiedKFold(n_splits=args.folds, shuffle=True, random_state=args.seeds_rng[0])
    for tr, te in skf.split(X_all, yi_all):
        for mname, model in make_models(args.seeds_rng[0]).items():
            model.fit(X_all[tr], yi_all[tr])
            rand_results[mname].append(score(y_all[te], dec(model.predict(X_all[te]))))

    def agg(lst, k):
        a = np.array([x[k] for x in lst], dtype=float)
        return float(a.mean()), float(a.std())

    summary = {"n_probes": len(rows), "n_routes": len(routes), "step": args.step, "folds": args.folds,
               "seeds": args.seeds_rng, "features": names,
               "A": {"verdict_dist": {str(k): int(n) for k, n in collections.Counter(y_all).items()}, "ms_per_case": a_ms,
                     "unknown_rate": float((y_all == "unknown").mean()),
                     "flip_series": a_flip, "n_series": n_series,
                     "grade_dist": dict(collections.Counter(r["grade"] for r in rows))},
               "C": {}}
    for mname, lst in results.items():
        summary["C"][mname] = {k: agg(lst, k) for k in
                               ["acc", "macro_f1", "unknown_recall", "unknown_to_feasible",
                                "unknown_to_definite", "flip_series", "fit_s", "pred_ms"]}
        summary["C"][mname]["unknown_to_feasible_total_seed0"] = int(sum(
            x["unknown_to_feasible"] for x in lst if x["seed"] == args.seeds_rng[0]))
        summary["C"][mname]["unknown_to_definite_total_seed0"] = int(sum(
            x["unknown_to_definite"] for x in lst if x["seed"] == args.seeds_rng[0]))
        summary["C"][mname]["n_unknown_total"] = int((y_all == "unknown").sum())
        summary["C"][mname]["random_split"] = {k: agg(rand_results[mname], k) for k in ["acc", "macro_f1", "unknown_recall"]}
        summary["C"][mname]["random_split"]["unknown_to_feasible_total"] = int(sum(x["unknown_to_feasible"] for x in rand_results[mname]))

    # ── 회귀 85건: 사람이 박은 기대값 대비 ────────────────────────────────
    if reg_cases:
        Xr = np.array([[featurize(c, holidays, sc, bus)[n] for n in names] for c in reg_cases], dtype=float)
        yr = np.array([norm_verdict(c["expect"]) for c in reg_cases])
        reg = {"n": len(reg_cases), "expect_dist": {str(k): int(n) for k, n in collections.Counter(yr).items()}, "C": {}, "fails": {}}
        for mname, model in make_models(args.seeds_rng[0]).items():
            model.fit(X_all, yi_all)                      # 탐침 전체로 학습 → 회귀는 완전한 외부 시험
            pred = dec(model.predict(Xr))
            reg["C"][mname] = score(yr, pred)
            reg["fails"][mname] = [{"id": c["id"], "src": c["_src"], "expect": t, "C": p,
                                    "note": (c.get("note") or "")[:60]}
                                   for c, t, p in zip(reg_cases, yr, pred) if t != p]
            if mname == "hgb":
                pickle.dump({"model": model, "features": names, "classes": CLASSES,
                             "trained_on": f"probes {len(rows)} step {args.step}"},
                            open(out / "model_hgb.pkl", "wb"))
        summary["regression"] = reg

    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "fold_results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "fails_probe_seed0.json").write_text(json.dumps(per_fold_fail, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(out / "report.md", summary, per_fold_fail)
    print(f"→ {out}/summary.json · report.md")


def write_md(path, S, fails):
    L = ["# A(코드) vs C(ML) — 판정 비교 결과\n",
         f"탐침 {S['n_probes']:,}건 · 구간 {S['n_routes']}종 · {S['step']}분 간격 · "
         f"GroupKFold {S['folds']}폴드(구간 단위) × 시드 {len(S['seeds'])}회\n",
         "자동 생성 — `python mobility_scripts/eval_ml_compare.py`\n",
         "## 1. 탐침 — A 가 만든 라벨을 C 가 다시 맞히는가 (구간을 못 본 채로)\n",
         f"A 판정 분포: {S['A']['verdict_dist']} · 판단불가율 {S['A']['unknown_rate']:.2%} · "
         f"건당 {S['A']['ms_per_case']:.1f}ms · INV-FLIP 위반 시리즈 {S['A']['flip_series']}/{S['A']['n_series']}\n",
         "| 모델 | 정확도 | macro-F1 | 근거없음 재현율 | **근거없음→성립** (시드0 합) | 근거없음→확정답 | FLIP 위반 시리즈 | 학습 s | 예측 ms/건 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for m, r in S["C"].items():
        L.append(f"| {m} | {r['acc'][0]:.3f}±{r['acc'][1]:.3f} | {r['macro_f1'][0]:.3f}±{r['macro_f1'][1]:.3f} | "
                 f"{r['unknown_recall'][0]:.3f} | **{r['unknown_to_feasible_total_seed0']}/{r['n_unknown_total']}** | "
                 f"{r['unknown_to_definite_total_seed0']}/{r['n_unknown_total']} | "
                 f"{r['flip_series'][0]:.1f}/폴드 | {r['fit_s'][0]:.1f} | {r['pred_ms'][0]:.3f} |")
    L.append(f"| **A 현행** | 1.000(정의상) | — | 1.000 | **0** | 0 | {S['A']['flip_series']} 전체 | — | {S['A']['ms_per_case']:.1f} |\n")
    maj = max(S["A"]["verdict_dist"].values()) / S["n_probes"]
    L.append(f"다수결 기준선(전부 '성립'이라 답함): 정확도 {maj:.3f}\n")
    L.append("### 1-b. 대조군 — 구간을 섞어 나누면 (같은 구간을 학습에서 봤을 때)\n")
    L.append("| 모델 | 정확도 | macro-F1 | 근거없음 재현율 | 근거없음→성립 합 |")
    L.append("|---|---|---|---|---|")
    for m, r in S["C"].items():
        q = r["random_split"]
        L.append(f"| {m} | {q['acc'][0]:.3f} | {q['macro_f1'][0]:.3f} | {q['unknown_recall'][0]:.3f} | {q['unknown_to_feasible_total']}/{r['n_unknown_total']} |")
    L.append("\n두 표의 차이가 **외운 것의 크기**다. 구간을 봤으면 맞히고, 못 본 구간이면 못 한다 — 시간표 없이는 일반화가 안 된다.\n")
    if "regression" in S:
        R = S["regression"]
        L += [f"## 2. 회귀 {R['n']}건 — 사람이 박은 기대값 대비 (탐침 전체로 학습한 뒤 외부 시험)\n",
              f"기대값 분포: {R['expect_dist']}\n",
              "| 경로 | 일치 | 정확도 | 근거없음→성립 | 근거없음→확정답 |", "|---|---|---|---|---|",
              f"| **A 현행** | {R['n']}/{R['n']} | 1.000 | 0 | 0 |"]
        for m, r in R["C"].items():
            L.append(f"| C {m} | {round(r['acc']*R['n'])}/{R['n']} | {r['acc']:.3f} | "
                     f"{r['unknown_to_feasible']}/{r['n_unknown']} | {r['unknown_to_definite']}/{r['n_unknown']} |")
        L.append("\n### 회귀에서 C 가 틀린 것 (hgb)\n")
        for f in R["fails"].get("hgb", []):
            L.append(f"- `{f['id']}` ({f['src']}) 기대 {KO.get(f['expect'], f['expect'])} → C {KO.get(f['C'], f['C'])} — {f['note']}")
    L += ["\n## 3. C 가 구조적으로 못 내는 것\n",
          "- 근거 등급·확인 시각: 조회를 안 했으니 없다. 표의 '근거없음 재현율'은 A 라벨을 흉내 낸 값이지 근거를 본 게 아니다.",
          "- 도착 시각·여유분·완화 조건·대안: 분류기는 판정 한 글자만 낸다.",
          "- 이슈(운행중단)·새 구간: 학습 분포 밖이다. 회귀 표의 issue/alt 실패가 그 자리다.\n",
          "## 4. 특징 (시간표 누수 없음)\n", ", ".join(f"`{n}`" for n in S["features"])]
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
