# -*- coding: utf-8 -*-
"""X2 ③ — compare.csv 를 세 축(성립/불가 · 도착 시각 · 막차)으로 세고, 어긋남을 분류표(classify_manual.csv)와 맞춘다.

분류는 사람이 원천 시간표 행을 보고 붙인 것이다(classify_manual.csv 의 basis 칸). 이 스크립트는
  · 어긋난 건마다 분류가 있는지 · 분류표에 있는데 어긋나지 않은 건이 없는지
를 검사해 숫자와 분류가 따로 놀지 않게 한다(하나라도 어긋나면 끝 코드 1).
「②MOTIS」「③우리」는 **원천 시간표 행**에 댄 것이다 — 실측·공식 시간표 대조가 아니다(X0 규칙 11).
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
    L = []
    P = L.append
    P(f"# X2 대조 요약 (자동 생성 · x2_report.py)\n")
    P(f"- 케이스 **{len(case)}** = 비교 {len(cmp_)} + 비교 밖 {len(case) - len(cmp_)}")
    why = collections.Counter(r["why"].split(":")[0].split("(")[0].split(" — ")[0].strip() for r in case if r["kind"] != "비교")
    for k, v in why.most_common():
        P(f"  - 비교 밖 · {k}: {v}")
    P(f"  - 적재 기간(2026-09-01 부터 90일) 밖: {sum(1 for r in case if r['why'] == '적재 기간 밖')}")

    mism, used = [], set()

    def note(r, axis, detail):
        k = (r["id"], axis)
        m = man.get(k)
        used.add(k)
        mism.append((axis, r["id"], detail, m["class"] if m else "★분류없음", m["cause"] if m else "", m["basis"] if m else ""))

    # ── 축 1: 성립/불가 ──────────────────────────────────────────────
    v_ok = 0
    for r in cmp_:
        if r["ours_verdict"] == r["m_verdict"]: v_ok += 1
        else: note(r, "V", f"우리 {r['ours_verdict']}({r['ours_code'] or '-'}) ↔ MOTIS {r['m_verdict']}")
    # ── 축 2: 도착 시각(둘 다 성립) ──────────────────────────────────
    both = [r for r in cmp_ if r["ours_verdict"] == "feasible" and r["m_verdict"] == "feasible" and r.get("d_arrive", "") != ""]
    a_ok = 0
    for r in both:
        if abs(I(r["d_arrive"])) <= TOL: a_ok += 1
        else: note(r, "A", f"우리 {r['ours_arrive']} ↔ MOTIS {r['m_arrive']} ({I(r['d_arrive']):+d}분)")
    # ── 축 3: 막차(늦어도 출발) ─────────────────────────────────────
    last = [r for r in cmp_ if r.get("d_last", "") != ""]
    l_ok = 0
    for r in last:
        if abs(I(r["d_last"])) <= TOL: l_ok += 1
        else: note(r, "L", f"우리 {r['ours_last']} ↔ MOTIS {r['m_last']} ({I(r['d_last']):+d}분)")
    lastB = [r for r in cmp_ if r.get("d_last_noest", "") != ""]
    lB_ok = sum(1 for r in lastB if abs(I(r["d_last_noest"])) <= TOL)
    bothB = [r for r in cmp_ if r["ours_verdict"] == "feasible" and r.get("m_verdict_noest") == "feasible" and r.get("d_arrive_noest", "") != ""]
    aB_ok = sum(1 for r in bothB if abs(I(r["d_arrive_noest"])) <= TOL)
    vB = [r for r in cmp_ if r.get("m_verdict_noest", "") != ""]
    vB_ok = sum(1 for r in vB if r["ours_verdict"] == r["m_verdict_noest"])

    bad_ids = {m[1] for m in mism}
    all_ok = len(cmp_) - len(bad_ids)
    P(f"\n## 결정 숫자\n")
    P(f"| 축 | 일치 | 어긋남 |\n|---|---|---|")
    P(f"| 세 축 모두(±{TOL}분) | **{all_ok}/{len(case)}** (비교 {len(cmp_)}건 중 {all_ok}) | {len(bad_ids)} |")
    P(f"| 성립/불가 | **{v_ok}/{len(cmp_)}** | {len(cmp_) - v_ok} |")
    P(f"| 도착 시각(둘 다 성립) | **{a_ok}/{len(both)}** | {len(both) - a_ok} |")
    P(f"| 막차(늦어도 출발) | **{l_ok}/{len(last)}** | {len(last) - l_ok} |")
    P(f"| (종착 추정 정차를 뺀 판) 성립/불가 · 도착 · 막차 | {vB_ok}/{len(vB)} · {aB_ok}/{len(bothB)} · {lB_ok}/{len(lastB)} | |")

    cls = collections.Counter(m[3] for m in mism)
    P(f"\n## 어긋남 분류 (케이스×축 {len(mism)}개 · 케이스 {len(bad_ids)}건)\n")
    P("| 분류 | 수 |\n|---|---|")
    for k in ("①변환", "②MOTIS", "③우리", "④층", "미확정", "경계", "★분류없음"):
        if cls.get(k) or k in ("①변환", "②MOTIS", "③우리"):
            P(f"| {k} | {cls.get(k, 0)} |")
    P("\n| 축 | 케이스 | 값 | 분류 | 원인 | 근거 |\n|---|---|---|---|---|---|")
    for m in sorted(mism, key=lambda m: (m[3], m[0], m[1])):
        P("| " + " | ".join(m) + " |")

    # ── 후보 · 구간 단위 ────────────────────────────────────────────
    cand = [r for r in R if r["unit"] == "cand" and r["kind"] == "비교"]
    cv = sum(1 for r in cand if r["ours_verdict"] == r["m_verdict"])
    cb = [r for r in cand if r.get("d_arrive", "") != "" and r["ours_verdict"] == "feasible" and r["m_verdict"] == "feasible"]
    ca = sum(1 for r in cb if abs(I(r["d_arrive"])) <= TOL)
    leg = [r for r in R if r["unit"] == "leg"]
    lf = [r for r in leg if r.get("d_arrive", "") != ""]
    lb = sum(1 for r in lf if abs(I(r["d_board"])) <= TOL)
    la = sum(1 for r in lf if abs(I(r["d_arrive"])) <= TOL)
    multi = [r for r in cmp_ if r.get("m_same_path", "") != ""]
    P(f"\n## 다른 단위\n")
    P(f"- multi 후보(지하철만): 성립/불가 **{cv}/{len(cand)}** · 도착 ±{TOL}분 **{ca}/{len(cb)}**(어긋남은 전부 환승 시간·구간 소요 — 케이스 표와 같은 원인)")
    P(f"- multi 케이스의 MOTIS 자유 탐색이 우리 최선 후보와 같은 경로: **{sum(1 for r in multi if r['m_same_path'] == '1')}/{len(multi)}**")
    P(f"- 구간 단위(같은 노선 직행 · 우리가 성립으로 통과시킨 지하철 구간): 같은 편을 탐 **{lb}/{len(lf)}** · 도착 ±{TOL}분 **{la}/{len(lf)}** (찾지 못함 {len(leg) - len(lf)})")
    dd = collections.Counter(I(r["d_arrive"]) for r in lf)
    P(f"  - 도착 차(MOTIS − 우리 · 분) 분포: " + " · ".join(f"{k:+d}: {v}" for k, v in sorted(dd.items())))
    P("\n| 구간(±1분 밖) | 케이스 | 우리 승차→도착 | MOTIS 승차→도착 |\n|---|---|---|---|")
    for r in lf:
        if abs(I(r["d_board"])) > TOL or abs(I(r["d_arrive"])) > TOL:
            P(f"| {r['path']} | {r['id']} | {r['ours_board']}→{r['ours_arrive']} | {r['m_board']}→{r['m_arrive']} |")

    # ── 급행 구간 ──────────────────────────────────────────────────
    ex = [r for r in R if r.get("express_zone") == "1" and r["kind"] == "비교" and r["unit"] in ("case", "leg")]
    P(f"\n## 급행 구간(9호선 · R-EXPRESS) — {len(ex)}줄 · 둘이 같아도 「맞다」가 아니다(같은 원천 행)\n")
    P("| 단위 | 케이스 | 경로 | 우리 | MOTIS | 도착 차 | 막차 차 |\n|---|---|---|---|---|---|---|")
    for r in ex:
        ours = f"{r['ours_verdict'] or ''} {r.get('ours_board') or ''}→{r['ours_arrive']}".strip()
        mo = f"{r.get('m_verdict') or ''} {r.get('m_board') or r.get('m_depart') or ''}→{r.get('m_arrive') or ''}".strip()
        P(f"| {r['unit']} | {r['id']} | {r['path']} | {ours} | {mo} | {r.get('d_arrive', '')} | {r.get('d_last', '')} |")

    tj = out / "timing.json"
    if tj.exists():
        P(f"\n## 응답 시간\n\n`{json.loads(tj.read_text(encoding='utf-8'))}`")
    (out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    stale = [k for k in man if k not in used]
    missing = [m for m in mism if m[3] == "★분류없음"]
    if stale or missing:
        print(f"\n★ 분류표와 결과가 안 맞는다 — 분류 없음 {len(missing)} · 남는 분류 {stale}")
        sys.exit(1)


if __name__ == "__main__":
    main()
