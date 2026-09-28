#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""이동 모듈 평가 지표 v2 (28번 방 · 2026-09-27) — 발표 후보 지표 넷을 한 보고서로.

v1(9/13 · 근거 보존율)은 접기 설계 검증용이었다 — 접기가 끝나 뜻이 없어졌고 git 이력에 남는다.
v2 는 **판정 로그(40) + 25 실측 정답**만 읽는다. 판정기를 부르지 않는다(판정은 run28.ps1 이 로그를 켜고 돌린다).

    python mobility_scripts/mobility_metrics.py                                  # 기본 경로 · 소스별 가장 최근 실행
    python mobility_scripts/mobility_metrics.py --field-json <verify_time --json 출력> --out <md>

지표 (0단계 · 본인 9/27 — 넷 다 뽑아 보고 셋으로 줄인다)
  A 여유 적중률     실측 하차 ∈ [예정, 예정+@] · source=field · 수단별 · 모집단별
  B 앱 대비 도착     25 앱 짝 5건(문앞 − 앱 도착 · 앱 N분은 첫 대기 제외라 출발대기를 더한다) + 같은 여정의 우리 오차
  C no_data 비율    소스별(regression · selfcheck · field) · 빈칸 목록(어느 노선·역에서 값을 못 냈나)
  D 2×2 + reason   기대 판정이 있는 줄(regression · field) — 혼동표 · 불가 P/R · 이유 분해
보조
  E 9월 태그 22여정 분포(commute_trips) · F 버스 승차 오차(41 p90·정책 버퍼 대조 재료) · G 탐침 분포(정본용)

★ 표본은 본인 출퇴근 노선(742·040·752·4319 · 3·7호선 · 2호선 1)뿐이다 — A·B 는 「이 노선들에서」로만 읽는다.
★ 초(지연)는 적지 않는다(v1 결정 그대로 — 실행마다 바뀐다).
"""
import argparse
import collections
import csv
import json
import re
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _default_paths():
    try:
        from app.modules.travel_ops.mobility.engine.paths import PROCESSED
    except Exception:                                   # dotenv 없는 환경 — 인자로 넘긴다
        return {}
    gt = PROCESSED / "mobility" / "ground_truth"
    return {"log": PROCESSED / "mobility" / "logs" / "judged_log_v1.jsonl",
            "cases": gt / "field_legs_v1.json", "r13": gt / "r13_field_log_v1.csv",
            "trips": gt / "commute_trips_202609.csv"}


# ── 공통 ────────────────────────────────────────────────────────────────
def hm(s):
    """'07:31' · '07:31:21' · '08:02(추정)' → 분. 못 읽으면 None."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})", s or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def svc(m, ref):
    """실측 시각을 예정과 같은 운행일 축으로 — 자정 넘긴 실측('00:10')은 예정(24:10)에 맞춰 +1440 (GPT 9/28 #5)."""
    if m is None or ref is None:
        return m
    while m < ref - 600:
        m += 1440
    while m > ref + 600:
        m -= 1440
    return m


def fmt(m):
    return "—" if m is None else f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def pct(a, b):
    return "—" if not b else f"{a / b * 100:.0f}%"


def sgn(x):
    return "—" if x is None else (f"+{x:g}" if x > 0 else f"{x:g}")


def load_log(path):
    rows, broken = [], 0
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                broken += 1
    return rows, broken


def latest_by_source(rows):
    """소스마다 가장 최근 실행(마지막 줄 시각 기준 · run_id 문자열 순서에 기대지 않는다). 소스끼리 안 합친다."""
    end = {}
    for n, r in enumerate(rows):
        k = (r.get("source"), r.get("run_id"))
        end[k] = max(end.get(k, ("", -1)), (r.get("ts") or "", n))
    pick = {}
    for (src, rid), t in end.items():
        if src not in pick or t > pick[src][1]:
            pick[src] = (rid, t)
    out = collections.defaultdict(list)
    for r in rows:
        s = r.get("source")
        if s in pick and r.get("run_id") == pick[s][0]:
            out[s].append(r)
    return out, {s: v[0] for s, v in pick.items()}


def is_nodata(r):
    return r.get("reason") == "no_data" or r.get("verdict_internal") == "unknown"


def leg_key(leg):
    if not isinstance(leg, dict):
        return "?"
    if leg.get("mode") == "bus":
        return f"버스 {leg.get('route')}"
    if leg.get("mode") in ("bike", "taxi", "car", "walk"):
        return leg["mode"]
    return str(leg.get("line") or leg.get("mode") or "?")


# ── A 여유 적중률 ───────────────────────────────────────────────────────
def section_a(field_rows, cases, L):
    L.append("## A. 여유 적중률 — 실측 하차가 [예정, 예정+@] 안에 들었나\n")
    L.append("표본: 25 티머니 태그 여정(케이스 = 첫 승차 태그에서 출발 · 정답 = 마지막 하차 태그). "
             "**본인 출퇴근 노선에서만** — 관광 구간·공항 0.\n")
    by_id = {r["case_id"]: r for r in field_rows}
    recs = []
    for c in cases:
        f = c["field"]
        r = by_id.get(c["id"])
        pred = r.get("arrive_min") if r else None
        act = svc(hm(f["actual_last_alight"]), pred if pred is not None else hm(c["depart_at"]))
        mg = r.get("margin_min") if r else None
        if r is None:
            cat = "로그 없음"
        elif r.get("verdict") != "feasible" or pred is None:
            cat = "값 없음"
        elif mg is None:
            cat = "@ 없음"
        elif act < pred:
            cat = "이름"
        elif act <= pred + mg:
            cat = "적중"
        else:
            cat = "늦음"
        recs.append({"c": c, "r": r, "act": act, "pred": pred, "mg": mg, "cat": cat,
                     "err": (act - pred) if (pred is not None and act is not None) else None})

    def row(label, rs):
        n = len(rs)
        valued = [x for x in rs if x["cat"] in ("이름", "적중", "늦음")]
        k = collections.Counter(x["cat"] for x in rs)
        errs = [x["err"] for x in valued]
        med = statistics.median(errs) if errs else None
        return (f"| {label} | {n} | {len(valued)} | **{k['적중']} ({pct(k['적중'], len(valued))})** | "
                f"{k['이름']} | {k['늦음']} | {pct(k['적중'] + k['이름'], len(valued))} | "
                f"{n - len(valued)} | {sgn(med)} |")

    L.append("| 묶음 | 여정 | 값 있음 | 적중 | 이름(예정보다 일찍) | 늦음(@ 넘김) | 안 늦음 | 값 없음 | 오차 중앙값(실측−예정 분) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    L.append(row("**전체**", recs))
    for m in sorted({x["c"]["field"]["mode"] for x in recs}):
        L.append(row(f"수단 {m}", [x for x in recs if x["c"]["field"]["mode"] == m]))
    groups = collections.OrderedDict()
    for x in recs:
        p = x["c"]["field"]["pattern"]
        g = "출근" if p.startswith("출근") else "퇴근" if p.startswith("퇴근") else "휴일" if p.startswith("휴일") else "기타(주말 생활)"
        groups.setdefault(g, []).append(x)
    for g, rs in groups.items():
        L.append(row(f"모집단 {g}", rs))
    L.append("")
    L.append("- **적중** = 실측 ∈ [예정, 예정+@] (정의 그대로) · **안 늦음** = 이름 + 적중 — 알림 쪽에서 실제로 중요한 것은 이 값이다(일찍 도착은 손해가 작다)")
    L.append("- ★ 읽는 법: 「늦음 0」은 **모델의 예정+@ 상한을 실측 하차가 넘긴 건이 0** 이라는 뜻이지 약속 지각률이나 @의 보장률이 아니다. "
             "입력은 태그로 재구성한 것(출발 = 첫 승차 태그 · 버스 노선 일부 추정)이고, 지하철 하차 태그에는 열차 도착 뒤 개찰까지의 이동이 섞여 있다. "
             "발표 문구는 「본인 관측 N여정의 재구성 입력에서 상한 초과 0건 · 구간 적중 n건」까지(GPT 9/28 #6)")
    L.append("- ★ 이 26건은 28 이 시간표 행선지 채우기를 결정한 근거(T16)를 **포함**한다 — 독립 평가 표본이 아니다. 별도 날짜의 미사용 실측으로 다시 본다(GPT 9/28 #7)")
    L.append("- ★ 버스로 시작하는 여정은 출발이 **차내 태그**(정류장 대기가 끝난 뒤)라 판정기가 더하는 배차 절반 대기만큼 예정이 늦게 나온다 → 「이름」 쪽으로 기운다. 지하철은 개찰 태그라 대기가 들어 있다")
    L.append("- 지하철 3→7 환승은 태그가 없어 여정 끝만 본다\n")
    L.append("<details><summary>여정별</summary>\n")
    L.append("| 케이스 | run | 날짜 | 모집단 | 수단 | 출발(태그) | 예정 | @ | 실측 하차 | 오차 | 판정 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for x in recs:
        f = x["c"]["field"]
        L.append(f"| {x['c']['id']} | {f.get('run_id') or ''} | {x['c']['date'][5:]}({f['weekday']}) | {f['pattern']} | {f['mode']} | "
                 f"{x['c']['depart_at']} | {fmt(x['pred'])} | {x['mg'] if x['mg'] is not None else '—'} | "
                 f"{f['actual_last_alight']} | {sgn(x['err'])} | {x['cat']}"
                 + (f" ({x['r'].get('reason')})" if x['r'] and x['r'].get('reason') else "") + " |")
    L.append("\n</details>\n")
    return recs


# ── B 앱 대비 도착 ──────────────────────────────────────────────────────
def app_arrive(row):
    """앱 도착 = 기준 시각 + N분 + 출발대기. 앱 N분은 첫 대기를 뺀 값이라 25 가 적은 「+출발대기K」를 더한다.
    기준 시각 = 캡처 시각 · 단 naver_min 에 「(HH:MM 안내」가 있으면 그 시각(R01-09-23)."""
    s = row.get("naver_min") or ""
    m = re.match(r"\s*(\d+)", s)
    if not m:
        return None, None
    n = int(m.group(1))
    k = re.search(r"출발대기\s*(\d+)", s)
    wait = int(k.group(1)) if k else 0
    a = re.search(r"\((\d{1,2}:\d{2})\s*안내", s)
    base = hm(a.group(1)) if a else hm(row.get("캡처_시각(출발)"))
    return (base + n + wait if base is not None else None), n + wait


def section_b(r13_rows, recs, L):
    L.append("## B. 앱 대비 도착 — 같은 날 같은 여정\n")
    by_run = {x["c"]["field"].get("run_id"): x for x in recs if x["c"]["field"].get("run_id")}
    L.append("| run | 앱 도착(기준+N+대기) | 실측 문앞 | **앱 오차(문앞)** | 우리 예정 하차 | @ | 실측 하차 | **우리 오차(하차)** | 우리 판정 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    diffs, ours = [], []
    for row in r13_rows:
        aa, _ = app_arrive(row)
        door = svc(hm(row.get("도착_시각(문앞)")), aa)
        if door is None or aa is None:
            continue
        d = door - aa
        diffs.append(d)
        x = by_run.get(row["run_id"])
        if x:
            ours.append(x["err"])
        L.append(f"| {row['run_id']} | {fmt(aa)} | {fmt(door)} | **{sgn(d)}** | "
                 f"{fmt(x['pred']) if x else '—'} | {x['mg'] if x and x['mg'] is not None else '—'} | "
                 f"{x['c']['field']['actual_last_alight'] if x else '—'} | **{sgn(x['err']) if x else '—'}** | {x['cat'] if x else '—'} |")
    if diffs:
        L.append(f"\n- 앱 오차 {len(diffs)}건: {' · '.join(sgn(d) for d in diffs)}분 · 범위 {sgn(min(diffs))}~{sgn(max(diffs))} · 절댓값 평균 {sum(abs(d) for d in diffs) / len(diffs):.1f}분")
    ov = [o for o in ours if o is not None]
    if ov:
        L.append(f"- 우리 오차 {len(ov)}건: {' · '.join(sgn(o) for o in ov)}분 — **앱은 숫자 하나, 우리는 예정 + @(폭)** 이라 비교 축은 「@ 안에 들었나」다")
    L.append("- ★ 두 오차는 기준이 다르다 — 앱은 문앞(도보 포함), 우리는 하차 태그(도보는 판정 밖). 같은 표에 두되 빼거나 더하지 않는다 · **앱과의 우열을 말하지 않는다**(시작·종료 기준을 맞추기 전까지)")
    L.append("- 표본 5건 · 본인 출퇴근 2노선 · 9/21~23\n")


# ── C no_data ───────────────────────────────────────────────────────────
def section_c(by_src, L):
    L.append("## C. no_data 비율 — 모르면 모른다고 냈나\n")
    L.append("| 소스 | 줄 | no_data | 비율 | 채점(기대 이유 no_data 인 줄) |")
    L.append("|---|---|---|---|---|")
    for s in ("regression", "selfcheck", "field"):
        rs = by_src.get(s) or []
        if not rs:
            continue
        nd = [r for r in rs if is_nodata(r)]
        exp_nd = [r for r in rs if (r.get("expected") or {}).get("reason") == "no_data"]
        hit = sum(1 for r in exp_nd if is_nodata(r))
        L.append(f"| {s} | {len(rs):,} | {len(nd):,} | **{pct(len(nd), len(rs))}** | "
                 + (f"{hit}/{len(exp_nd)}" if exp_nd else "—") + " |")
    L.append("")
    sc = by_src.get("selfcheck") or []
    if sc:
        cnt, tot = collections.Counter(), collections.Counter()
        for r in sc:
            legs = (r.get("input") or {}).get("legs") or []
            k = " + ".join(leg_key(x) for x in legs) if legs else "(multi)"
            tot[k] += 1
            if is_nodata(r):
                cnt[k] += 1
        L.append("- ★ 탐침의 no_data 는 **자기점검 씨앗이 덮는 구간**에서의 값이다 — 일부러 모르는 입력 씨앗(버스 9999 · 없는역 · 라우터 없는 taxi)을 빼고 0 이어도 전체 실데이터에 빈칸이 없다는 뜻은 아니다(GPT 9/28 #7)\n")
        L.append("**빈칸 목록 — 탐침에서 no_data 가 많이 나는 노선 조합(상위 15)**\n")
        L.append("| 노선 조합 | 탐침 | no_data | 비율 |")
        L.append("|---|---|---|---|")
        for k, v in cnt.most_common(15):
            L.append(f"| {k} | {tot[k]} | {v} | {pct(v, tot[k])} |")
        L.append("")
    fr = [r for r in by_src.get("field") or [] if is_nodata(r)]
    if fr:
        L.append("**실측 여정 중 값을 못 낸 것**: " + " · ".join(r["case_id"] for r in fr) + "\n")


# ── D 2×2 + reason ─────────────────────────────────────────────────────
def section_d(by_src, L):
    L.append("## D. 2×2 + reason 분해 — 기대 판정이 있는 줄\n")
    for s in ("regression", "field"):
        rs = [r for r in by_src.get(s) or [] if (r.get("expected") or {}).get("verdict") in ("feasible", "infeasible")]
        if not rs:
            continue
        real = [r for r in rs if not r.get("synthetic")]
        for tag, sub in (("실데이터", real), ("합성", [r for r in rs if r.get("synthetic")])):
            if not sub:
                continue
            m = collections.Counter((r["expected"]["verdict"], r.get("verdict")) for r in sub)
            tp, fn = m[("infeasible", "infeasible")], m[("infeasible", "feasible")]
            fp, tn = m[("feasible", "infeasible")], m[("feasible", "feasible")]
            other = len(sub) - tp - fn - fp - tn
            L.append(f"**{s} · {tag}** ({len(sub)}줄)\n")
            L.append("| 기대 \\ 판정 | 불가 | 성립 |")
            L.append("|---|---|---|")
            L.append(f"| 불가 | {tp} | {fn} |")
            L.append(f"| 성립 | {fp} | {tn} |")
            prec = pct(tp, tp + fp) if (tp + fp) else "—"
            rec = pct(tp, tp + fn) if (tp + fn) else "—"
            L.append(f"\n- 일치 **{pct(tp + tn, len(sub))}** ({tp + tn}/{len(sub)}) · 불가 정밀도 {prec} · 불가 재현율 {rec}"
                     + (f" · 판정값 없음 {other}" if other else ""))
            rc = collections.Counter(r.get("reason") for r in sub if r.get("verdict") == "infeasible")
            if rc:
                L.append("- 불가 이유 분해: " + " · ".join(f"`{k}` {v}" for k, v in rc.most_common()))
            er = [r for r in sub if r["expected"].get("reason")]
            if er:
                ok = sum(1 for r in er if r.get("reason") == r["expected"]["reason"])
                L.append(f"- 이유 일치(기대 이유 있는 줄): {ok}/{len(er)}")
                bad = [r for r in er if r.get("reason") != r["expected"]["reason"]]
                for r in bad[:8]:
                    L.append(f"  - `{r['case_id']}` 기대 {r['expected']['reason']} / 판정 {r.get('reason')}")
            L.append("")
    L.append("- 실측(field)은 전부 실제로 탄 여정이라 기대 = 성립 — 불가 판정은 거짓 음성이다")
    L.append("- 상세(오분류 카탈로그·no_data P/R)는 `mobility_scripts/judgment_metrics_report.py`(40)\n")


# ── E 22여정 분포 ───────────────────────────────────────────────────────
def section_e(trips, L):
    L.append("## E. 9월 태그 22여정 분포(`commute_trips_202609.csv`)\n")
    L.append("| 패턴 | 여정 | 태그 합(분) 최소·중앙·최대 | 문앞 추정(분) | 1차 차내 | 환승 대기 | 2차 차내 |")
    L.append("|---|---|---|---|---|---|---|")
    g = collections.defaultdict(list)
    for t in trips:
        g[t["pattern"]].append(t)

    def rng(vals):
        v = [float(x) for x in vals if x not in (None, "")]
        return "—" if not v else f"{min(v):g}·{statistics.median(v):g}·{max(v):g}"
    for p, ts in sorted(g.items(), key=lambda kv: -len(kv[1])):
        lo = [t["d2d_min_lo"] for t in ts]
        hi = [t["d2d_min_hi"] for t in ts]
        d2d = "—" if not any(lo) else f"{rng(lo).split('·')[0]}~{rng(hi).split('·')[-1]}"
        L.append(f"| {p} | {len(ts)} | {rng(t['total_tag_min'] for t in ts)} | {d2d} | "
                 f"{rng(t['leg1_ride_min'] for t in ts)} | {rng(t['transfer_wait_min'] for t in ts)} | {rng(t['leg2_ride_min'] for t in ts)} |")
    L.append("\n- 태그 합 = 첫 승차 ~ 마지막 하차(첫 승차 전 대기 없음) · 문앞 = 25 가 도보를 더한 추정\n")


# ── F 버스 승차 오차 ────────────────────────────────────────────────────
def section_f(field_json, cases, L):
    if not field_json:
        return
    res = {r["id"]: r for r in json.loads(Path(field_json).read_text(encoding="utf-8"))}
    L.append("## F. 버스 승차 오차 — 41 p90 · 정책 버퍼 대조 재료\n")
    L.append("| 케이스 | 노선 | 구간 | 예정 차내(분) | 실측 차내(태그) | 오차 | 판정 @ |")
    L.append("|---|---|---|---|---|---|---|")
    by_route = collections.defaultdict(list)
    for c in cases:
        r = res.get(c["id"])
        if not r:
            continue
        by_idx = {lg.get("idx"): lg for lg in (r.get("legs") or []) if not lg.get("worst")}
        li = 0
        for lt in c["field"]["legs_truth"]:
            start, li = li, li + lt["n_legs"]
            if lt["n_legs"] != 1:
                continue
            leg_in, lr = c["legs"][start], by_idx.get(start)
            if leg_in.get("mode") != "bus" or lr is None:
                continue
            ride = lr.get("ride_min")
            b0 = hm(lt["board"])
            act = svc(hm(lt["alight"]), b0) - b0
            err = None if ride is None else round(act - ride, 1)
            by_route[leg_in["route"]].append(err)
            L.append(f"| {c['id']} | {leg_in['route']} | {leg_in['from']}→{leg_in['to']} | "
                     f"{'—' if ride is None else f'{ride:.1f}'} | {act} | {sgn(err)} | {(r.get('margin_min'))} |")
    L.append("")
    for k, v in by_route.items():
        v = [x for x in v if x is not None]
        if v:
            L.append(f"- {k}: {len(v)}구간 · 오차 중앙 {sgn(statistics.median(v))} · 범위 {sgn(min(v))}~{sgn(max(v))}")
    L.append("- 차내 = 승차 태그 ~ 하차 태그(대기 없음) · 예정 차내 = 판정기 best 통과 `ride_min`(p50 누적)\n")


# ── G 탐침 분포 ────────────────────────────────────────────────────────
def section_g(by_src, runs, L):
    sc = by_src.get("selfcheck") or []
    if not sc:
        return
    k = collections.Counter(r.get("verdict_internal") for r in sc)
    L.append("## G. 자기점검 탐침 분포(정본용)\n")
    L.append(f"- run `{runs.get('selfcheck')}` · 규칙 {sc[0].get('rules_version')} · 탐침 **{len(sc):,}** "
             + "{" + " · ".join(f"{a} {b}" for a, b in sorted(k.items(), key=lambda kv: str(kv[0]))) + "}\n")


def main():
    d = _default_paths()
    ap = argparse.ArgumentParser(description="이동 모듈 평가 지표 v2(28)")
    ap.add_argument("--log", default=d.get("log"))
    ap.add_argument("--cases", default=d.get("cases"), help="build_field_cases_v1.py 산출")
    ap.add_argument("--r13", default=d.get("r13"))
    ap.add_argument("--trips", default=d.get("trips"))
    ap.add_argument("--field-json", help="field 케이스 verify_time --json 출력(F 절 · 구간 차내)")
    ap.add_argument("--out", default=str(REPO / ".metrics" / "mobility_metrics_v2.md"))
    a = ap.parse_args()
    for k in ("log", "cases", "r13", "trips"):
        if not getattr(a, k) or not Path(getattr(a, k)).exists():
            sys.exit(f"입력이 없다: --{k} {getattr(a, k)} — 부분 입력으로 낸 숫자는 근거가 아니다")

    rows, broken = load_log(a.log)
    by_src, runs = latest_by_source(rows)
    cases = json.loads(Path(a.cases).read_text(encoding="utf-8"))
    with open(a.r13, encoding="utf-8-sig", newline="") as f:
        r13 = list(csv.DictReader(f))
    with open(a.trips, encoding="utf-8-sig", newline="") as f:
        trips = list(csv.DictReader(f))

    L = ["# 이동 모듈 평가 지표 v2\n",
         "자동 생성 — `mobility_scripts/mobility_metrics.py`(28) · 판정기 무수정 · 로그 소스별 가장 최근 실행 · 소스끼리 안 합친다\n"]
    L.append("| 소스 | run | 줄 | 규칙 |")
    L.append("|---|---|---|---|")
    for s, rid in sorted(runs.items()):
        rs = by_src[s]
        L.append(f"| {s} | `{rid}` | {len(rs):,} | {', '.join(sorted({str(r.get('rules_version')) for r in rs}))} |")
    if broken:
        L.append(f"\n깨진 줄 {broken} 건너뜀")
    L.append("")
    field_rows = by_src.get("field") or []
    if not field_rows:
        L.append("> ⚠ field 소스가 로그에 없다 — run28.ps1 의 field 단계를 먼저 돌린다. A·B 의 우리 쪽 값은 빈칸이 된다.\n")
    recs = section_a(field_rows, cases["cases"], L)
    section_b(r13, recs, L)
    section_c(by_src, L)
    section_d(by_src, L)
    section_e(trips, L)
    section_f(a.field_json, cases["cases"], L)
    section_g(by_src, runs, L)
    if cases.get("skipped"):
        L.append("## 제외한 여정\n")
        for s in cases["skipped"]:
            L.append(f"- {s['trip']}: {s['why']}")
        L.append("")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"보고서 → {a.out}")
    for s, rid in sorted(runs.items()):
        print(f"  {s}: {len(by_src[s]):,}줄 · run {rid}")


if __name__ == "__main__":
    main()
