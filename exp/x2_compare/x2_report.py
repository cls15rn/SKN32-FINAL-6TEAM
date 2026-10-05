# -*- coding: utf-8 -*-
"""X2 ③ — compare.csv 를 축별로 세고, 어긋남을 분류표(classify_manual.csv)와 맞춘다.

읽는 법(GPT 대조 2026-10-05 반영)
  · 이것은 **같은 원천 시간표를 읽는 두 해석의 일치/불일치**다. 일치는 「맞다」가 아니고(공통 오판 가능),
    회귀 199건은 일반 입력의 대표 표본이 아니다(비슷한 케이스가 겹친다).
  · MOTIS 쪽 = 「MOTIS 구간 탐색 + 이 스크립트의 연결기」(고정 경로) 또는 「MOTIS 자유 탐색 첫 쪽」(multi) — 따로 센다.
  · 「②…」는 원천 시간표 행에 댄 것이다 — 실측·공식 편별 시간표 대조가 아니다.
분류표에 없는 어긋남 · 어긋나지 않은 분류 · 환승모델인데 환승을 맞춰도 안 사라지는 건이 있으면 끝 코드 1.
"""
import argparse, collections, csv, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOL = 1


def I(x):
    return int(x) if x not in ("", None) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=r"C:\final_project\exp\x2_compare")
    a = ap.parse_args()
    out = Path(a.out)
    R = list(csv.DictReader(open(out / "compare.csv", encoding="utf-8-sig")))
    man = {(r["id"], r["axis"]): r for r in csv.DictReader(open(HERE / "classify_manual.csv", encoding="utf-8-sig"))}
    case = [r for r in R if r["unit"] == "case"]
    cmp_ = [r for r in case if r["kind"] == "비교"]
    fixed = [r for r in cmp_ if r.get("m_same_path", "") == "" and r.get("m_status") != "search_exhausted" or r.get("last_kind", "") != ""]
    free = [r for r in cmp_ if r not in fixed]
    L = []
    P = L.append
    P("# X2 대조 요약 (자동 생성 · x2_report.py)\n")
    P("같은 원천 시간표를 읽는 **두 해석의 일치/불일치**다 — 일치는 「맞다」가 아니다.\n")
    P(f"- 케이스 199 중 **비교 가능 {len(cmp_)}/{len(case)}** (고정 경로 {len(fixed)} · multi 자유 탐색 {len(free)}) · 비교 밖 {len(case) - len(cmp_)}")
    why = collections.Counter(r["why"].split(":")[0].split("(")[0].split(" — ")[0].strip() for r in case if r["kind"] != "비교")
    for k, v in why.most_common():
        P(f"  - 비교 밖 · {k}: {v}")
    P(f"  - 적재 기간(2026-09-01 부터 90일) 밖: {sum(1 for r in case if r['why'] == '적재 기간 밖')}")
    st = collections.Counter(r.get("m_status", "") for r in cmp_)
    P(f"- MOTIS 쪽 상태({len(cmp_)}): 성립 {st['feasible']} · 목표에 늦음 {st['late_for_target']} · 그 운행일에 편 없음(GTFS 직접 계산으로 확인) {st['no_service_confirmed']}"
      f" · 운행일 밖(미확인) {st['no_service_in_day']} · 응답 후보에 없음(미확인) {st['search_exhausted']} · 질의 오류 {st['query_error']}")

    mism, used = [], set()

    def note(r, axis, detail):
        k = (r["id"], axis)
        m = man.get(k)
        used.add(k)
        mism.append((axis, r["id"], detail, m["class"] if m else "★분류없음", m["cause"] if m else "", m["basis"] if m else ""))

    # ── 축 V: 성립/불가(존재) — 미확인·오류는 빼고 센다 ─────────────────
    known = [r for r in cmp_ if r.get("m_status") in ("feasible", "late_for_target", "no_service_confirmed")]
    unk = [r for r in cmp_ if r not in known]
    # multi 자유 탐색이 못 찾은 것은, 그 케이스의 지하철 후보가 전부 직접 계산으로 「편 없음」이면 확인된 것으로 본다
    cands = collections.defaultdict(list)
    for r in R:
        if r["unit"] == "cand" and r["kind"] == "비교": cands[r["id"]].append(r)
    for r in list(unk):
        cs = cands.get(r["id"], [])
        if r.get("m_status") == "search_exhausted" and cs and all(c.get("m_status") == "no_service_confirmed" for c in cs):
            unk.remove(r); known.append(r)
    v_ok = 0
    for r in known:
        if r["ours_verdict"] == r["m_verdict"]: v_ok += 1
        else: note(r, "V", f"우리 {r['ours_verdict']}({r['ours_code'] or '-'}) ↔ MOTIS 쪽 {r['m_status']}")
    # ── 축 A: 도착 시각(둘 다 성립) ──────────────────────────────────
    both = [r for r in cmp_ if r["ours_verdict"] == "feasible" and r.get("m_status") == "feasible" and r.get("d_arrive", "") != ""]
    a_ok = 0
    for r in both:
        if abs(I(r["d_arrive"])) <= TOL: a_ok += 1
        else: note(r, "A", f"우리 {r['ours_arrive']} ↔ MOTIS 쪽 {r['m_arrive']} ({I(r['d_arrive']):+d}분)")
    # ── 축 L: 마지막 출발 — 뜻이 같은 것끼리(L1 운행일 마지막 · L3 목표−@ 까지) ──
    last = [r for r in cmp_ if r.get("d_last", "") != ""]
    l_ok = 0
    for r in last:
        if abs(I(r["d_last"])) <= TOL: l_ok += 1
        else: note(r, "L", f"[{r['last_kind']}] 우리 {r['ours_last']} ↔ MOTIS 쪽 {r['m_last']} ({I(r['d_last']):+d}분)")
    lk = collections.Counter(r["last_kind"] for r in last)
    lko = collections.Counter(r["last_kind"] for r in last if abs(I(r["d_last"])) <= TOL)
    l_na = [r for r in cmp_ if r["ours_last"] and r.get("d_last", "") == ""]

    bad_ids = {m[1] for m in mism}
    all_ok = len(cmp_) - len(bad_ids)
    P("\n## 결정 숫자 (±1분은 일치로 셈 — 시각 허용오차이지 같은 연결이라는 뜻은 아니다)\n")
    P("| 축 | 일치/적용 | 어긋남 | 미확인·해당 없음 |\n|---|---|---|---|")
    P(f"| 세 축 모두 | **{all_ok}/{len(cmp_)}** | {len(bad_ids)} | 비교 밖 {len(case) - len(cmp_)} |")
    P(f"| 성립/불가(같은 출발 · 공백 상한 · 운행일 조건) | **{v_ok}/{len(known)}** | {len(known) - v_ok} | 미확인 {len(unk)} |")
    P(f"| 도착 시각(둘 다 성립) | **{a_ok}/{len(both)}** | {len(both) - a_ok} | |")
    P(f"| 마지막 출발 | **{l_ok}/{len(last)}** | {len(last) - l_ok} | 우리 값은 있는데 못 댐 {len(l_na)} |")
    for k in sorted(lk):
        P(f"|  · {k} | {lko[k]}/{lk[k]} | {lk[k] - lko[k]} | |")

    cls = collections.Counter(m[3] for m in mism)
    order = ("①변환", "환승모델", "②원천행", "②추정연결", "②그래프", "③우리", "층", "미확정", "경계", "★분류없음")
    P(f"\n## 어긋남 분류 (케이스×축 {len(mism)}개 · 케이스 {len(bad_ids)}건)\n")
    P("| 분류 | 수 | 뜻 |\n|---|---|---|")
    mean = {"①변환": "GTFS 변환이 원천의 빈 곳을 못 메움", "환승모델": "환승 시간 모델 차이 — 우리 값으로 맞추면 사라짐(재실행으로 확인)",
            "②원천행": "우리 구간 소요 추정이 그 편의 도착역 원천 행보다 늦음", "②추정연결": "X1 잇기가 추정으로 이은 편을 판정기는 안 씀/다르게 봄 — 어느 쪽이 실제인지 미확인",
            "②그래프": "우리 환승 그래프에 없는 환승역", "③우리": "우리 쪽이 원천 행과 같음", "층": "판정 규칙 — MOTIS 에 없는 것",
            "미확정": "양쪽 다 추정이거나 근거 없는 값", "경계": "분 단위 내림/올림", "★분류없음": ""}
    for k in order:
        if cls.get(k) or k in ("①변환", "③우리"):
            P(f"| {k} | {cls.get(k, 0)} | {mean[k]} |")
    P("\n| 축 | 케이스 | 값 | 분류 | 원인 | 근거 |\n|---|---|---|---|---|---|")
    for m in sorted(mism, key=lambda m: (order.index(m[3]) if m[3] in order else 99, m[0], m[1])):
        P("| " + " | ".join(m) + " |")

    # 환승모델 분류는 「환승 시간만 맞춘 재실행」에서 사라져야 한다
    ox_fail = []
    for (cid, ax), m in man.items():
        if m["class"] != "환승모델": continue
        vals = [I(r["d_arrive_ox"]) for r in R if r["id"] == cid and r["unit"] in ("case", "cand") and r.get("d_arrive_ox", "") != ""]
        if not vals or min(abs(v) for v in vals) > TOL: ox_fail.append((cid, vals))
    oxr = [r for r in R if r["unit"] in ("case", "cand") and r.get("d_arrive_ox", "") != "" and r.get("d_arrive", "") != ""]
    P(f"\n## 환승 시간만 우리 값으로 맞춘 재실행 (환승이 있는 경로 {len(oxr)}줄 · 케이스+후보)\n")
    P(f"- 도착 ±{TOL}분: 맞추기 전 **{sum(1 for r in oxr if abs(I(r['d_arrive'])) <= TOL)}/{len(oxr)}** → 맞춘 뒤 **{sum(1 for r in oxr if abs(I(r['d_arrive_ox'])) <= TOL)}/{len(oxr)}**")
    P("\n| 케이스 | 후보 | 경로 | 맞추기 전 | 맞춘 뒤 |\n|---|---|---|---|---|")
    for r in oxr:
        if abs(I(r["d_arrive"])) > TOL:
            P(f"| {r['id']} | {r.get('cand') or ''} | {r['path']} | {I(r['d_arrive']):+d} | {I(r['d_arrive_ox']):+d} |")

    # ── 종착 추정 정차를 뺀 판 — 같은 케이스끼리 교차표 ─────────────────
    def ab(ax_a, ax_b, pool):
        c = collections.Counter()
        for r in pool:
            x = "A 일치" if r.get(ax_a, "") != "" and abs(I(r[ax_a])) <= TOL else ("A 못 댐" if r.get(ax_a, "") == "" else "A 어긋남")
            y = "B 일치" if r.get(ax_b, "") != "" and abs(I(r[ax_b])) <= TOL else ("B 경로 없음" if r.get(ax_b, "") == "" else "B 어긋남")
            c[(x, y)] += 1
        return c
    P("\n## 종착 추정 정차를 뺀 판(B) — 같은 케이스 교차표\n")
    P("B 판은 추정 시각만 뺀 것이 아니라 **그 정차(내릴 수 있는 곳) 자체**를 뺀다 — 일치가 주는 것은 종착 추정의 정확도와 무관하고, 그 결과가 종착 정차에 기대고 있었다는 표시다.\n")
    for name, xa, xb, pool in (("마지막 출발", "d_last", "d_last_noest", [r for r in cmp_ if r["ours_last"] and r.get("last_kind")]),
                               ("도착 시각", "d_arrive", "d_arrive_noest", [r for r in fixed if r["ours_verdict"] == "feasible" and r.get("m_status") == "feasible"])):
        c = ab(xa, xb, pool)
        P(f"- {name}({len(pool)}): " + " · ".join(f"{k[0]}→{k[1]} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])))

    # ── 추정 연결(성수·응암 이어 타기)에 기댄 결과 ─────────────────────
    rj = [r for r in R if r["kind"] == "비교" and (r.get("m_rejoin") not in ("", "0", None) or r.get("m_rejoin_last") not in ("", "0", None))]
    P(f"\n## 성수·응암 「이어 타기」 추정에 기댄 줄 — {len(rj)}줄 (같은 차량이라는 근거 없음 · 합치지 않으면 「같은 경로 없음」)\n")
    P("| 단위 | 케이스 | 기댄 값 | 우리 | MOTIS 쪽 |\n|---|---|---|---|---|")
    for r in rj:
        w = "도착" if r.get("m_rejoin") not in ("", "0", None) else "마지막 출발"
        P(f"| {r['unit']} | {r['id']} | {w} | {r['ours_arrive'] if w == '도착' else r['ours_last'] or '-'} | {r.get('m_arrive') if w == '도착' else r.get('m_last_day')} |")

    # ── 후보 · 구간 · 직접 계산 ──────────────────────────────────────
    cand = [r for r in R if r["unit"] == "cand" and r["kind"] == "비교"]
    cv = sum(1 for r in cand if r["ours_verdict"] == r["m_verdict"])
    cb = [r for r in cand if r.get("d_arrive", "") != "" and r["ours_verdict"] == "feasible" and r.get("m_status") == "feasible"]
    ca = sum(1 for r in cb if abs(I(r["d_arrive"])) <= TOL)
    leg = [r for r in R if r["unit"] == "leg"]
    lf = [r for r in leg if r.get("d_arrive", "") != ""]
    multi = [r for r in cmp_ if r.get("m_same_path", "") != ""]
    P("\n## 다른 단위\n")
    P(f"- multi 후보(지하철만 · 고정 경로): 성립/불가 **{cv}/{len(cand)}** · 도착 ±{TOL}분 **{ca}/{len(cb)}**")
    P(f"- multi 케이스 — MOTIS 자유 탐색(첫 쪽 6개 중 가장 이른 도착)이 우리 최선 후보와 같은 경로: **{sum(1 for r in multi if r['m_same_path'] == '1')}/{len(multi)}**")
    P(f"- 구간 단위(같은 노선 직행 · **우리가 성립으로 통과시킨 구간만** — 고른 표본): 승차 시각 같음 **{sum(1 for r in lf if I(r['d_board']) == 0)}/{len(leg)}** · 도착 ±{TOL}분 **{sum(1 for r in lf if abs(I(r['d_arrive'])) <= TOL)}/{len(leg)}** · 그 운행일에 없음 {len(leg) - len(lf)}")
    dd = collections.Counter(I(r["d_arrive"]) for r in lf)
    P("  - 도착 차(MOTIS 쪽 − 우리 · 분): " + " · ".join(f"{k:+d}: {v}" for k, v in sorted(dd.items())))
    P("\n| 구간(승차가 다르거나 도착 ±1분 밖) | 케이스 | 우리 승차→도착 | MOTIS 쪽 승차→도착 |\n|---|---|---|---|")
    for r in leg:
        if r.get("d_arrive", "") == "":
            P(f"| {r['path']} | {r['id']} | {r['ours_board']}→{r['ours_arrive']} | {r.get('m_note', '')} |")
        elif I(r["d_board"]) != 0 or abs(I(r["d_arrive"])) > TOL:
            P(f"| {r['path']} | {r['id']} | {r['ours_board']}→{r['ours_arrive']} | {r['m_board']}→{r['m_arrive']} |")
    g = [r for r in R if r["kind"] == "비교" and r.get("g_arrive") and r.get("m_arrive")]
    gd = [r for r in g if r["g_arrive"] != r["m_arrive"]]
    P(f"\n## 같은 GTFS 를 초 단위로 직접 읽은 값 ↔ MOTIS 쪽(분 단위) — 도착 같음 **{len(g) - len(gd)}/{len(g)}**\n")
    P("변환 GTFS 와 MOTIS 응답의 정합성이다 — 원천 열차 복원의 정확도 증거가 아니다.\n")
    for r in gd:
        P(f"- {r['id']} {r.get('cand') or ''} {r['path']}: MOTIS 쪽 {r['m_arrive']} ↔ 직접(초) {r['g_arrive']} — 분 내림으로 초 단위에서는 못 타는 편을 탔다")

    ex = [r for r in R if r.get("express_zone") == "1" and r["kind"] == "비교" and r["unit"] in ("case", "leg")]
    P(f"\n## 급행 구간(9호선 · R-EXPRESS) — {len(ex)}줄 · 둘이 같아도 「맞다」가 아니다(같은 원천 행)\n")
    P("| 단위 | 케이스 | 경로 | 우리 | MOTIS 쪽 | 도착 차 | 마지막 출발 차 |\n|---|---|---|---|---|---|---|")
    for r in ex:
        ours = f"{r['ours_verdict'] or ''} {r.get('ours_board') or ''}→{r['ours_arrive']}".strip()
        mo = f"{r.get('m_status') or ''} {r.get('m_board') or r.get('m_depart') or ''}→{r.get('m_arrive') or ''}".strip()
        P(f"| {r['unit']} | {r['id']} | {r['path']} | {ours} | {mo} | {r.get('d_arrive', '')} | {r.get('d_last', '')} |")

    tj = out / "timing.json"
    if tj.exists():
        t = json.loads(tj.read_text(encoding="utf-8"))
        P("\n## 응답 시간 (성공한 질의 · A·B 판 섞임 · 한 번에 하나씩 — 동시성·실패율·갱신 비용은 재지 않았다)\n")
        P(f"- 질의당: 중앙값 {t['median_ms']}ms · 95% {t['p95_ms']}ms · 최대 {t['max_ms']}ms · {t['n_queries']}회 · 오류 {t.get('query_errors', 0)}")
        if "case_median_ms" in t:
            P(f"- 케이스당(그 케이스가 쓴 질의 합 · 질의 수 중앙값 {t['case_queries_median']}): 중앙값 {t['case_median_ms']}ms · 95% {t['case_p95_ms']}ms · 최대 {t['case_max_ms']}ms")
    (out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    stale = [k for k in man if k not in used]
    missing = [m for m in mism if m[3] == "★분류없음"]
    if stale or missing or ox_fail:
        print(f"\n★ 분류표와 결과가 안 맞는다 — 분류 없음 {len(missing)} · 남는 분류 {stale} · 환승을 맞춰도 안 사라지는 환승모델 {ox_fail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
