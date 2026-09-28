# -*- coding: utf-8 -*-
"""판정 결과를 계약 모양으로 접어 보고 **스키마로 검증**한다. 2026-09-13.

    python mobility_scripts/mobility_fold.py            # 회귀를 돌려 접는다
    python mobility_scripts/mobility_fold.py --from .metrics/real_legs_v1_result.json

팀 코드가 없어도 돈다 — 계약 타입을 import 하지 않고 dict 만 만든다.
코드가 오는 날 `MobilityTeam.execute()` 가 이 dict 로 `TeamResult` 를 만들면 끝이다.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "final_project_cs"))
from app.modules.travel_ops.mobility.engine.fold import fold_case  # noqa: E402

SCHEMA = REPO / "tests" / "mobility" / "contract" / "mob_evidence_value_v1.schema.json"
METRICS = REPO / ".metrics"


def units_of(c):
    """판정이 만든 정보 단위. 사라지면 답이 나빠지는 것들이다."""
    n = 2                                            # 판정 + 근거등급
    n += 1 if c.get("reason") else 0
    n += 1 if c.get("relief") else 0
    n += 1 if c.get("arrive_min") is not None else 0
    n += 1 if c.get("slack_min") is not None else 0
    n += len(c.get("warnings") or []) + len(c.get("alternatives") or [])
    n += len(c.get("legs") or []) + len(c.get("evidence") or [])
    return n


def carried_of(r):
    """★ 접은 결과에서 **실제로 실린** 단위를 센다.
    앞선 판(2026-09-13)은 units 를 그대로 돌려줘 무조건 100% 가 나왔다 — 동어반복이었다.
    세는 대상은 `Evidence.value` 안이다. answer 문장에만 있는 것은 세지 않는다
    (개인 에이전트가 요약하면 사라질 수 있다 — ◆10)."""
    run = next((e["value"] for e in r["evidence"] if e["value"]["kind"] == "run"), None)
    if run is None:
        return 0
    n = 0
    n += 1 if run.get("verdict") else 0
    n += 1 if run.get("grade") else 0
    n += 1 if run.get("reasons") else 0
    n += 1 if run.get("relax") else 0
    n += 1 if run.get("arrive_min") is not None else 0
    n += 1 if run.get("slack_min") is not None else 0
    n += len(run.get("warnings") or [])
    n += sum(1 for e in r["evidence"] if e["value"]["kind"] == "alt")
    n += sum(1 for e in r["evidence"] if e["value"]["kind"] == "verdict")
    n += sum(1 for e in r["evidence"] if e["value"]["kind"] == "source")
    return n


def main():
    ap = argparse.ArgumentParser(description="Verdict → TeamResult 접기 검증")
    ap.add_argument("--from", dest="src", nargs="*")
    ap.add_argument("--out", default=str(REPO / "fold_sample.json"))
    a = ap.parse_args()

    if not a.src:
        if not METRICS.exists() or not list(METRICS.glob("*_result.json")):
            print("판정 결과가 없다 — mobility_metrics.py 를 먼저 돌린다")
            subprocess.run([sys.executable, str(REPO / "scripts" / "mobility_metrics.py")],
                           check=False)
        a.src = [str(p) for p in sorted(METRICS.glob("*_result.json"))]
    cases = []
    for p in a.src:
        cases += json.loads(Path(p).read_text(encoding="utf-8"))
    if not cases:
        sys.exit("판정 결과 0건 — 통과가 아니라 실패다")

    from jsonschema import Draft202012Validator
    V = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))

    basis = {"timetable_built_at": "2026-09-09", "rules_version": "v0.3",
             "service_date": "2026-09-11", "decided_at": "2026-09-13T00:00:00+09:00"}
    bad, n_ev, kinds = [], 0, {}
    units = carried = 0
    results = []
    for i, c in enumerate(cases, 1):
        r = fold_case(c, task_id=f"T-{i:04d}", basis=basis)
        results.append(r)
        ids = {e["evidence_id"] for e in r["evidence"]}
        for e in r["evidence"]:
            n_ev += 1
            k = e["value"]["kind"]
            kinds[k] = kinds.get(k, 0) + 1
            errs = sorted(V.iter_errors(e["value"]), key=lambda x: x.path)
            if errs:
                bad.append((c.get("id"), e["evidence_id"], errs[0].message[:120]))
            for rid in e["value"].get("evidence_ids", []) + e["value"].get("alt_ids", []):
                if rid not in ids:
                    bad.append((c.get("id"), e["evidence_id"], f"근거 그래프가 끊겼다 → {rid}"))
        if len(r["evidence"]) > 40:
            bad.append((c.get("id"), "-", f"근거 {len(r['evidence'])}건 > 40"))
        if len(r["answer"]) > 6000:
            bad.append((c.get("id"), "-", "answer 6,000자 초과"))
        if not r["evidence"]:
            bad.append((c.get("id"), "-", "answer 가 있는데 evidence 가 비었다"))
        # 보존율 — 접기 전에는 소스 근거만 남았다
        units += units_of(c)
        carried += carried_of(r)

    print(f"\n접은 결과 {len(results)}건 · 근거 {n_ev}건 · 구간당 평균 {n_ev/len(results):.1f}건")
    print("kind 분포 — " + " · ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    print(f"근거 보존율 — 접기 후 {carried}/{units} = {carried/units*100:.1f}%  (Evidence.value 안에 실린 것만 셌다)")
    if carried < units:
        print(f"  ⚠ {units - carried}단위가 answer 문장에만 있거나 상한에 잘렸다")
    if bad:
        print(f"\n✗ 어긋남 {len(bad)}건")
        for x in bad[:12]:
            print("   ", x)
        sys.exit(1)
    print("스키마·근거그래프·예산·answer 전부 통과")
    Path(a.out).write_text(json.dumps(results[:2], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"표본 2건 → {a.out}")


if __name__ == "__main__":
    main()
