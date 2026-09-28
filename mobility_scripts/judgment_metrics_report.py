#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""판정 로그 → 분류 지표 보고서 (40번 방 · 설계 v2 §6-2 · 2값 기준)

  python mobility_scripts/judgment_metrics_report.py                       # 기본 로그 · 소스별 가장 최근 실행
  python mobility_scripts/judgment_metrics_report.py --run all --out .metrics/judgment_report.md

절
  1 실행 메타          run · 줄 수 · 규칙/시간표 판 · 지연 p50/p95 · ext_calls 합 · 차단 건수
  2 2×2               `expected.verdict` 있는 줄 — 밖 판정(feasible/infeasible) confusion + P/R/F1
                        · 실데이터와 합성(synthetic)을 나눈다 · `alt_source`(own/odsay)로도 나눈다
  3 reason 분해       예측 불가의 이유 코드 분포 · `expected.reason` 이 있는 줄의 이유 confusion(실제 코드 × 예측 코드)
  4 no_data P/R       양성 = 이유 코드 no_data. 진실을 아는 줄만(기대 성립 → 음성 · 기대 불가+기대 이유 있음)
  5 오분류 카탈로그    2값 셀 · 이유 셀별 사례 N건 — 덤프 파일에 decisions_detail 이 있다
  6 정답 없는 줄       자기점검 탐침 — 밖 판정·이유 분포만(채점 안 함)
"""
import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mobility_scripts.classification_metrics import classify_report, binary_report, misclassified_catalog   # noqa: E402

VERDICTS = ["feasible", "infeasible"]
FEAS = "(성립)"          # 이유 confusion 에서 「이유 없음 = 성립」 칸


BROKEN = {"n": 0}


def load(path):
    """깨진 줄(도중에 죽은 실행의 잘린 끝 줄)은 세고 건너뛴다 — 한 줄 때문에 보고서 전체가 멈추지 않게."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                BROKEN["n"] += 1
    return rows


def pick_runs(rows, run):
    if run == "all":
        return rows
    if run != "latest":
        return [r for r in rows if r.get("run_id") == run]
    end = {}                                         # run 마다 마지막 줄 시각 — run_id 문자열 순서에 기대지 않는다
    for n, r in enumerate(rows):
        k = r.get("run_id")
        end[k] = max(end.get(k, ("", -1)), (r.get("ts") or "", n))
    last = {}
    for r in rows:                                   # 소스별로 가장 늦게 끝난 run
        s, k = r.get("source"), r.get("run_id")
        if s not in last or end[k] > end[last[s]]:
            last[s] = k
    return [r for r in rows if r.get("run_id") == last.get(r.get("source"))]


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, round(q * (len(xs) - 1))))
    return xs[k]


def pv(r):
    """예측 밖 판정 — 없으면 「(판정없음)」(성립으로 세지 않는다)."""
    return r.get("verdict") if r.get("verdict") in VERDICTS else "(판정없음)"


def brief(r):
    i = r.get("input") or {}
    if i.get("legs"):
        path = " / ".join(f"{g.get('line') or ('버스' + g['route'] if g.get('route') else g.get('mode'))} {g.get('from')}→{g.get('to')}"
                          for g in i["legs"])
    elif i.get("multi"):
        path = f"multi {i['multi'].get('from')}→{i['multi'].get('to')}"
    else:
        path = "?"
    t = f"{i.get('date', '')} {i.get('depart_at', '')}" + (f"→목표 {i['arrive_by']}" if i.get("arrive_by") else "")
    return f"{path} · {t}"


def report(rows, *, catalog_n=5, dump_dir=None, blocked_n=0):
    L = []
    w = L.append
    runs = collections.Counter((r.get("source"), r.get("run_id")) for r in rows)
    w("# 판정 로그 지표 보고서 (40 · 2값)\n")
    w("## 1. 실행 메타\n")
    w("| source | run_id | 줄 | 규칙 | 시간표 | 묶음 |")
    w("|---|---|---:|---|---|---|")
    for (s, rid), n in sorted(runs.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        rs = [r for r in rows if r.get("run_id") == rid]
        w(f"| {s} | {rid} | {n} | {','.join(sorted({str(r.get('rules_version')) for r in rs}))} | "
          f"{','.join(sorted({str(r.get('timetable_build')) for r in rs}))} | "
          f"{len({r.get('bundle') for r in rs})} |")
    lat = [r.get("latency_ms") for r in rows]
    ext = collections.Counter()
    for r in rows:
        for c in r.get("ext_calls") or []:
            ext[(c.get("api"), "n")] += c.get("n") or 0
            ext[(c.get("api"), "fail")] += c.get("fail") or 0
    dropped = sum((r.get("alt_dropped") or {}).get("n", 0) for r in rows)
    w("")
    w(f"- 지연(latency_ms) p50 {pct(lat, .5)} · p95 {pct(lat, .95)} · 최대 {pct(lat, 1.0)} (lfd 역산 포함 여부는 실행 인자에 따름)")
    w(f"- 외부 호출 {dict(ext) or '없음'} · ODsay 후보 버림 {dropped}건 · 차단된 줄 {blocked_n}건")
    w(f"- 판정 {len(rows)}줄 · 밖 {dict(collections.Counter(r.get('verdict') for r in rows))}"
      f" · 기기 {sorted({str(r.get('device')) for r in rows})}" + (f" · **깨진 줄 {BROKEN['n']}건 건너뜀**" if BROKEN["n"] else ""))
    w("")

    lab_all = [r for r in rows if r.get("expected")]
    # 기대값이 2값 어휘 밖(옛 4값 unknown·rejected_by_limit)이면 채점에서 빼고 따로 센다 — 조용히 떨구지 않는다
    off = [r for r in lab_all if r["expected"].get("verdict") not in VERDICTS]
    lab = [r for r in lab_all if r["expected"].get("verdict") in VERDICTS]

    # (GPT #5) 채점은 **source 별로만** 낸다 — 회귀(판정기 잠금값)와 실측(field)을 한 점수로 합치지 않는다
    SRC_ORDER = {"field": 0, "regression": 1, "selfcheck": 2, "adhoc": 3}
    sources = sorted({str(r.get("source")) for r in lab}, key=lambda x: (SRC_ORDER.get(x, 9), x))

    def f3(v):
        return "—" if v is None else f"{v:.3f}"

    def reason_sets(part):
        known = [r for r in part if r["expected"]["verdict"] == "feasible" or r["expected"].get("reason")]
        yt = [(r["expected"]["reason"] if r["expected"]["verdict"] == "infeasible" else FEAS) for r in known]
        yp = [((r.get("reason") or "(코드없음)") if r.get("verdict") == "infeasible"
               else FEAS if r.get("verdict") == "feasible" else "(판정없음)") for r in known]
        return known, yt, yp

    w("## 2. 2×2 — 밖 판정(feasible/infeasible) · source 별\n")
    if "regression" in sources:
        w("> **읽는 법** — 회귀(`source=regression`)의 `expect` 는 판정기를 **잠근 값**이다(39 가 v0.8 출력으로 재정의). "
          "회귀 표의 1.000 은 「회귀가 안 깨졌다」와 같은 말이지 정확도가 아니다. 정확도는 `source=field`(25 실측) 표만 본다. "
          "두 소스는 합치지 않는다.\n")
    if not sources:
        w("_기대값 있는 줄 없음_\n")
    for src in sources:
        s_lab = [r for r in lab if str(r.get("source")) == src]
        for name, part in (("실데이터", [r for r in s_lab if not r.get("synthetic")]),
                           ("합성(synthetic)", [r for r in s_lab if r.get("synthetic")])):
            if not part:
                continue
            rep = classify_report([r["expected"]["verdict"] for r in part], [pv(r) for r in part], VERDICTS)
            w(rep.to_markdown(f"source={src} · {name} · n={len(part)}"))
        alts = {r.get("alt_source") for r in s_lab}
        if len(alts) > 1 or alts - {"own"}:
            for a_src in sorted(str(x) for x in alts):
                part = [r for r in s_lab if str(r.get("alt_source")) == a_src and not r.get("synthetic")]
                if part:
                    rep = classify_report([r["expected"]["verdict"] for r in part], [pv(r) for r in part], VERDICTS)
                    w(rep.to_markdown(f"source={src} · alt_source={a_src} · 실데이터 n={len(part)}"))
    w(f"_alt_source 분포: {dict(collections.Counter(r.get('alt_source') for r in lab))}_\n")
    if off:
        w(f"_기대값이 2값 어휘 밖이라 채점에서 뺀 줄 {len(off)}건: "
          + ", ".join(f"{r['case_id']}({r['expected'].get('verdict')})" for r in off[:10]) + "_\n")
    nopred = [r["case_id"] for r in lab if r.get("verdict") not in VERDICTS]
    if nopred:
        w(f"_밖 판정이 없는 줄(표에서 「라벨 밖」으로 빠짐) {len(nopred)}건: {', '.join(nopred[:10])}_\n")

    w("## 3. reason 분해 · source 별\n")
    pred_codes = collections.Counter(r.get("reason") for r in rows if r.get("verdict") == "infeasible")
    w("예측 불가의 이유 코드 (고른 줄 전체 · 채점 아님):\n")
    w("| 이유 | 건수 |")
    w("|---|---:|")
    for k, n in pred_codes.most_common():
        w(f"| {k} | {n} |")
    w("")
    per_src = {}
    for src in sources:
        real = [r for r in lab if str(r.get("source")) == src and not r.get("synthetic")]
        known, yt, yp = reason_sets(real)
        per_src[src] = (known, yt, yp)
        if known:
            codes = [FEAS] + sorted({c for c in yt + yp if c != FEAS})
            w(classify_report(yt, yp, codes).to_markdown(
                f"source={src} · 이유 confusion (실데이터 · 진실 이유를 아는 줄 n={len(known)})"))
        n_unk = sum(1 for r in real if r["expected"]["verdict"] == "infeasible" and not r["expected"].get("reason"))
        if n_unk:
            w(f"_source={src}: 기대 불가인데 expect_reason 이 없어 이유 채점에서 뺀 줄 {n_unk}_\n")

    w("## 4. no_data precision/recall · source 별\n")
    w("| source | TP | FP | FN | TN | precision | recall | F1 | n |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for src, (known, yt, yp) in per_src.items():
        if known:
            b = binary_report(yt, yp, "no_data")
            w(f"| {src} | {b['tp']} | {b['fp']} | {b['fn']} | {b['tn']} | {f3(b['precision'])} | {f3(b['recall'])} | {f3(b['f1'])} | {b['n']} |")
    w("")
    w("- precision = 「모른다」고 낸 것 중 진짜 근거가 없던 비율(등급 정직도와 같은 성격) · recall = 근거 없는 것을 「모른다」로 잡은 비율")
    w("- 합성 줄은 뺐다(mini 시간표는 일부러 비운 칸이 있다)\n")

    w("## 5. 오분류 카탈로그\n")
    any_cat = False
    for src in sources:
        s_lab = [r for r in lab if str(r.get("source")) == src]
        known, yt, yp = per_src[src]
        cat2 = misclassified_catalog(s_lab, [r["expected"]["verdict"] for r in s_lab], [pv(r) for r in s_lab], catalog_n)
        catr = misclassified_catalog(known, yt, yp, catalog_n) if known else {}
        for title, cat in (("2값", cat2), ("이유 코드", catr)):
            for (t, p), cell in cat.items():
                any_cat = True
                w(f"**source={src} · {title} {t} → {p}** · {cell['count']}건\n")
                w("| case_id | 묶음 | 입력 | 예측 이유 | 내부 | run |")
                w("|---|---|---|---|---|---|")
                for r in cell["examples"]:
                    w(f"| {r['case_id']} | {r.get('bundle')} | {brief(r)} | {r.get('reason')} | {r.get('verdict_internal')} | {r.get('run_id')} |")
                w("")
    # (GPT #4) 값 잠금 축은 판정 기대값이 없는 줄도 본다
    ax = [r for r in rows if r.get("miss_axes")]
    if ax:
        any_cat = True
        w(f"**값 잠금 축 어긋남(도착·여유·@·늦어도 출발)** · {len(ax)}건 — 2값·이유가 맞거나 판정 기대값이 없어도 덤프된다\n")
        w("| source | case_id | 묶음 | 축 | 입력 |")
        w("|---|---|---|---|---|")
        for r in ax[:catalog_n * 4]:
            w(f"| {r.get('source')} | {r['case_id']} | {r.get('bundle')} | {', '.join(r['miss_axes'])} | {brief(r)} |")
        w("")
    if any_cat:
        w(f"상세(decisions_detail)는 `{dump_dir or 'logs'}/regression_fail_dump_YYYYMMDD.jsonl` — case_id·run_id 로 찾는다.\n")
    else:
        w("_어긋난 줄 없음 — 2값·이유·값 잠금 축 모두 기대와 같다_\n")

    unl = [r for r in rows if not r.get("expected")]
    w("## 6. 정답 없는 줄 (자기점검 등 · 채점 안 함)\n")
    if not unl:
        w("_없음_\n")
    else:
        for src in sorted({str(r.get("source")) for r in unl}):
            part = [r for r in unl if str(r.get("source")) == src]
            by = collections.Counter((r.get("verdict"), r.get("reason")) for r in part)
            vi = collections.Counter(r.get("verdict_internal") for r in part)
            w(f"**{src}** · n={len(part)} · 합성 {sum(1 for r in part if r.get('synthetic'))} · 내부 {dict(vi)}\n")
            w("| 밖 판정 | 이유 | 건수 | 비율 |")
            w("|---|---|---:|---:|")
            for (v, c), n in by.most_common():
                w(f"| {v} | {c or ''} | {n} | {n / len(part):.1%} |")
            w("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="판정 로그 지표 보고서(2값)")
    ap.add_argument("--log", help="기본 DATA_DIR/travel/processed/mobility/logs/judged_log_v1.jsonl")
    ap.add_argument("--run", default="latest", help="latest(소스별 최근) · all · <run_id>")
    ap.add_argument("--source", help="regression · selfcheck · field 중 하나만")
    ap.add_argument("--catalog-n", type=int, default=5, help="혼동 셀당 사례 수(◆5 · 기본 5)")
    ap.add_argument("--out", default=".metrics/judgment_report.md")
    a = ap.parse_args()
    if a.log:
        log = Path(a.log)
    else:
        from app.modules.travel_ops.mobility.engine.judgment_log import default_log_dir, LOG_NAME
        log = default_log_dir() / LOG_NAME
    rows = pick_runs(load(log), a.run)
    if a.source:
        rows = [r for r in rows if r.get("source") == a.source]
    if not rows:
        raise SystemExit(f"{log} 에 고른 줄이 없다(--run {a.run})")
    bl = log.parent / "judged_log_blocked_v1.jsonl"
    run_ids = {r.get("run_id") for r in rows}
    blocked_n = sum(1 for x in load(bl) if x.get("run_id") in run_ids) if bl.exists() else 0
    md = report(rows, catalog_n=a.catalog_n, dump_dir=log.parent, blocked_n=blocked_n)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(md, encoding="utf-8")
    lab = [r for r in rows if r.get("expected")]
    print(f"{log} · {len(rows)}줄(기대값 {len(lab)}) · 어긋남 {sum(1 for r in lab if r.get('match') is False)} → {a.out}")


if __name__ == "__main__":
    main()
