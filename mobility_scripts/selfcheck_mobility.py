#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""이동 판정기 자기점검 — **정답 없이** 이상을 찾는다.

회귀 케이스(verify_time.py --check-expect)는 `expect` 가 박힌, 이미 답을 아는 케이스다.
거기서는 **이미 잡은 버그만** 다시 확인된다. 아직 아무도 모르는 버그는 안 나온다.

이 스크립트는 반대로 간다. 씨앗 케이스의 구간을 가져다 **시각·요일·방향을 대량으로 흔들어**
판정기를 수천 번 돌리고, 정답 대신 **불변식(invariant)** 으로 이상을 잡는다.
"정답이 뭔지"는 몰라도 "이건 앞뒤가 안 맞는다"는 기계가 판단할 수 있다.

  INV-CRASH     판정 중 예외가 났다
  INV-MIDNIGHT  도착이 출발보다 이르다 (자정 넘김 정규화 붕괴)
  INV-MONO      늦게 떠났는데 더 일찍 도착한다 (선입선출 위반)
  INV-FLIP      하루 안에서 성립/불가가 세 번 넘게 뒤집힌다 (경계 불안정)
  INV-SYM       A→B 와 B→A 의 소요가 과하게 다르다 (방향·역순서 뒤집힘)
  INV-GRADE     성립인데 근거 등급이 '근거없음' 이다
  INV-JUMP      1스텝 차이인데 도착이 60분 넘게 벌어진다 (배차 공백일 수도 있다)
  INV-DAYTYPE   같은 시각인데 평일/휴일 도착이 30분 넘게 다르다
  INV-UNKNOWN   특정 역·노선에만 판단불가가 몰린다 (데이터 구멍)
  INV-WORST     최악 도착이 예정 도착보다 이르다 · @ 가 버퍼보다 작다 (v0.8 이중 계산 붕괴)
  INV-OUT       밖 판(out)이 내부와 어긋난다 — 성립인데 예정 시각이 없다 · 불가인데 이유 코드가 없다 ·
                성립인데 여유가 음수다 · 밖 판정이 둘 밖의 값이다 (v0.8)

2026-09-10 에 잡았던 실제 버그 둘이 이 규칙에 그대로 걸린다 —
자정 넘김 미정규화는 INV-MIDNIGHT·INV-MONO, 행선지 필드 역전은 INV-SYM.

사용:
  python mobility_scripts/selfcheck_mobility.py --seeds final_project_cs/tests/unit/travel/mobility/real_legs_v1.json \\
      --timetable <...> --order <...> --transfer-walk <...> \\
      --bus-route <...> --bus-stops <...> --station-coords <...> \\
      --step 30 --out selfcheck_report.md --json selfcheck.json

경로 인자를 생략하면 verify_time.py 와 같은 기본값(.env 의 DATA_DIR)을 쓴다.
"""
import argparse, glob as _glob, json, sys, traceback, collections
from datetime import date as _date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine.verify_time import Timetable, Verifier                       # noqa: E402
from app.modules.travel_ops.mobility.engine.line_order import LineOrder                         # noqa: E402
from app.modules.travel_ops.mobility.engine.transfer_walk import TransferWalk                   # noqa: E402
from app.modules.travel_ops.mobility.engine.bus import BusRoutes                                # noqa: E402
from app.modules.travel_ops.mobility.engine.geo import StationCoords                            # noqa: E402
from app.modules.travel_ops.mobility.engine.exits import StationExits                           # noqa: E402
from app.modules.travel_ops.mobility.engine.bike import BikeStations                             # noqa: E402
from app.modules.travel_ops.mobility.engine.timeutil import fmt_min  # noqa: E402

SEV_ORDER = {"critical": 0, "warn": 1, "info": 2}


# ── 탐침 만들기 ────────────────────────────────────────────────────────────
def route_key(legs):
    """구간 묶음을 비교 가능한 열쇠로. mode·route 까지 포함해야 버스가 안 섞인다."""
    nm = lambda x: x.get("name") or f"{x.get('lat')},{x.get('lng')}" if isinstance(x, dict) else x   # 자전거 좌표 dict(v0.7)
    return tuple((l.get("mode", "subway"), l.get("line"), l.get("route"),
                  nm(l.get("from")), nm(l.get("to"))) for l in legs)


def reverse_route(legs):
    """A→B→C 를 C→B→A 로. 환승역은 그대로 남는다."""
    out = []
    for l in reversed(legs):
        r = dict(l)
        r["from"], r["to"] = l.get("to"), l.get("from")
        out.append(r)
    return out


def pick_dates(holidays, base):
    """평일·토·일·공휴일 대표일 넷을 고른다. 못 고르면 있는 것만 쓴다."""
    want = {}
    d = base
    for _ in range(400):
        iso = d.isoformat()
        if iso in holidays:
            want.setdefault("holiday", iso)
        elif d.weekday() == 5:
            want.setdefault("sat", iso)
        elif d.weekday() == 6:
            want.setdefault("sun", iso)
        else:
            want.setdefault("weekday", iso)
        if len(want) == 4:
            break
        d += timedelta(days=1)
    return want


def expand_seeds(paths):
    """`final_project_cs/tests/unit/travel/mobility/*.json` 같은 와일드카드를 직접 편다.

    PowerShell 은 인자의 `*` 를 확장해 주지 않는다(bash 와 다른 점이다).
    쉘에 맡기면 Windows 에서만 OSError 22 로 죽는다.
    """
    out, seen = [], set()
    for p in paths:
        hits = sorted(_glob.glob(p)) if any(c in p for c in "*?[") else [p]
        if not hits:
            raise SystemExit(f"씨앗 파일을 못 찾았다: {p}")
        for h in hits:
            if h.lower().endswith(".json") and h not in seen:
                seen.add(h)
                out.append(h)
    if not out:
        raise SystemExit(f"씨앗에 .json 이 하나도 없다: {paths}")
    return out


def build_probes(seed_files, holidays, step, lo_min, hi_min, base_date,
                 with_reverse=True, max_routes=None):
    routes, seen = [], set()
    for p in expand_seeds(seed_files):
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        cases = doc.get("cases") if isinstance(doc, dict) else doc
        if not isinstance(cases, list):
            print(f"! {p} 는 케이스 파일이 아니라 건너뛴다")
            continue
        for c in cases:
            if not isinstance(c, dict):
                continue
            legs = c.get("legs") or []
            if not legs:
                continue
            # ★ 설계상 근거없음인 케이스(경계값·좌표 없음 — mixed MIX-05·06)는 씨앗에서 뺀다(2026-09-19).
            #   넣으면 모든 시각에서 판단불가라 INV-UNKNOWN 이 울고 판단불가율에 '설계된 근거없음'이 섞인다.
            #   케이스 파일이 "selfcheck": false 로 표시한다 — 여기서 골라내지 않는다.
            if c.get("selfcheck") is False:
                continue
            # 이슈(disruptions)가 붙은 케이스는 구간만 빌려 온다 — 이슈는 빼고 흔든다.
            k = route_key(legs)
            if k in seen:
                continue
            seen.add(k)
            routes.append({"src": f"{Path(p).name}:{c.get('id')}", "legs": legs,
                           "party": c.get("party")})
    if not routes:
        # ★ 탐침 0건은 '깨끗하다'가 아니라 '아무것도 안 봤다'이다. 조용히 통과시키면
        #   자기점검이 통째로 무력해진다(2026-09-12 에 실제로 한 번 그렇게 지나갔다).
        raise SystemExit(f"씨앗에서 구간을 하나도 못 읽었다: {seed_files}")
    if max_routes:
        routes = routes[:max_routes]

    dates = pick_dates(holidays, base_date)
    probes = []
    for ri, r in enumerate(routes):
        variants = [("F", r["legs"])]
        if with_reverse:
            variants.append(("R", reverse_route(r["legs"])))
        for tag, legs in variants:
            for dtag, iso in dates.items():
                for m in range(lo_min, hi_min + 1, step):
                    probes.append({
                        "id": f"P{ri:03d}{tag}-{dtag}-{fmt_min(m).replace(':', '')}",
                        "date": iso, "depart_at": fmt_min(m),
                        "legs": legs,
                        **({"party": r["party"]} if r.get("party") else {}),
                        "_route": ri, "_dir": tag, "_dtag": dtag,
                        "_depart_min": m, "_src": r["src"],
                    })
    return routes, dates, probes


# ── 불변식 ────────────────────────────────────────────────────────────────
def add(findings, rule, sev, probe, msg, extra=None):
    findings.append({"rule": rule, "severity": sev, "probe_id": probe.get("id"),
                     "route": probe.get("_route"), "dir": probe.get("_dir"),
                     "date": probe.get("date"), "depart_at": probe.get("depart_at"),
                     "src": probe.get("_src"), "message": msg, **(extra or {})})


def check_invariants(rows, args):
    """rows: [{probe, result(dict) or error}] → findings"""
    findings = []

    # 1) 예외 · 자정 넘김 · 등급 모순 — 한 건씩 본다
    for row in rows:
        p, r = row["probe"], row.get("result")
        if row.get("error"):
            add(findings, "INV-CRASH", "critical", p,
                f"판정 중 예외: {row['error']}", {"trace": row.get("trace")})
            continue
        dep, arr = p["_depart_min"], r.get("arrive_min")
        if arr is not None and arr < dep:
            add(findings, "INV-MIDNIGHT", "critical", p,
                f"도착 {fmt_min(arr)} 이 출발 {fmt_min(dep)} 보다 이르다")
        if (r.get("verdict") == "feasible" and arr is not None
                and str(r.get("grade", "")).startswith("근거없음")):
            # ★ '성립 + 도착 미상' 은 의도된 상태라 제외한다(2026-09-10 결정).
            #   도착 시각을 내놓으면서 근거가 없다고 하는 것만 모순이다.
            add(findings, "INV-GRADE", "warn", p,
                f"도착 {fmt_min(arr)} 을 내놓으면서 근거 등급은 '근거없음' 이다")
        # v0.8 — 이중 계산 · 밖 판
        aw, mg, bf = r.get("arrive_worst_min"), r.get("margin_min"), r.get("buffer_min")
        if arr is not None and aw is not None and aw < arr:
            add(findings, "INV-WORST", "critical", p,
                f"최악 도착 {fmt_min(aw)} 이 예정 도착 {fmt_min(arr)} 보다 이르다")
        if mg is not None and bf is not None and mg < bf:
            add(findings, "INV-WORST", "critical", p, f"@ {mg}분이 버퍼 {bf}분보다 작다")
        o = r.get("out")
        if o is not None:
            ov = o.get("verdict")
            if ov not in ("feasible", "infeasible"):
                add(findings, "INV-OUT", "critical", p, f"밖 판정이 둘 밖의 값이다: {ov!r}")
            elif ov == "feasible":
                if o.get("arrive_min") is None and "candidates" not in o:
                    add(findings, "INV-OUT", "critical", p, "밖 성립인데 예정 시각(arrive_min)이 없다")
                if o.get("slack_min") is not None and o["slack_min"] < 0:
                    add(findings, "INV-OUT", "critical", p, f"밖 성립인데 여유 {o['slack_min']}분(음수)이다")
                if o.get("code"):
                    add(findings, "INV-OUT", "warn", p, f"밖 성립인데 이유 코드 {o['code']} 가 붙어 있다")
            else:
                if not o.get("code"):
                    add(findings, "INV-OUT", "critical", p, "밖 불가인데 이유 코드가 없다")
                if r.get("verdict") == "feasible" and o.get("code") not in ("no_data",):
                    add(findings, "INV-OUT", "critical", p,
                        f"내부 성립인데 밖 불가({o.get('code')}) — no_data(예정 시각 없음) 말고는 갈릴 수 없다")

    # 2) 같은 (구간·방향·요일) 안에서 출발시각을 따라가며 본다
    by_series = collections.defaultdict(list)
    for row in rows:
        if row.get("error"):
            continue
        p = row["probe"]
        by_series[(p["_route"], p["_dir"], p["_dtag"])].append(row)

    for key, series in by_series.items():
        series.sort(key=lambda x: x["probe"]["_depart_min"])

        # 단조성 — 늦게 떠났는데 더 일찍 도착하면 선입선출이 깨진 것이다
        prev = None
        for row in series:
            arr = row["result"].get("arrive_min")
            if arr is None:
                continue
            if prev is not None and arr < prev[1] - args.mono_tol:
                add(findings, "INV-MONO", "critical", row["probe"],
                    f"{prev[0]} 출발 → {fmt_min(prev[1])} 도착 인데 "
                    f"{row['probe']['depart_at']} 출발 → {fmt_min(arr)} 도착 "
                    f"({prev[1] - arr}분 앞선다)",
                    {"prev_depart_at": prev[0], "prev_arrive": fmt_min(prev[1])})
            # 인접 급변
            if prev is not None and arr - prev[1] > args.jump_min:
                add(findings, "INV-JUMP", "info", row["probe"],
                    f"{prev[0]} → {row['probe']['depart_at']} 사이에서 도착이 "
                    f"{arr - prev[1]}분 벌어진다 (배차 공백일 수 있다)")
            prev = (row["probe"]["depart_at"], arr)

        # 경계 안정성 — 하루는 '불가 → 성립 → 불가' 가 정상이라 전이는 최대 2회다
        seq = [(r["probe"]["depart_at"], r["result"].get("verdict")) for r in series]
        flips = [(a, b) for (ta, a), (tb, b) in zip(seq, seq[1:])
                 if a != b and {a, b} <= {"feasible", "infeasible"}]
        if len(flips) > 2:
            ts = [tb for (ta, a), (tb, b) in zip(seq, seq[1:])
                  if a != b and {a, b} <= {"feasible", "infeasible"}]
            add(findings, "INV-FLIP", "warn", series[0]["probe"],
                f"하루 안에서 성립/불가가 {len(flips)}번 뒤집힌다 (정상은 2회 이하) — 전이 시각 {ts}",
                {"flips": len(flips), "at": ts})

    # 3) 방향 대칭 — A→B 와 B→A 의 총 소요가 과하게 다르면 방향·역순서가 의심된다
    by_fd = {}
    for row in rows:
        if row.get("error"):
            continue
        p = row["probe"]
        by_fd[(p["_route"], p["_dir"], p["_dtag"], p["_depart_min"])] = row
    for (ri, dr, dt, m), row in by_fd.items():
        if dr != "F":
            continue
        other = by_fd.get((ri, "R", dt, m))
        if other is None:
            continue
        # ★ 버스는 왕복이 같은 길이 아니다(정류장 짝이 다르고 일방통행이 있다).
        #   BUS-05 가 그 예다 — 역방향 탐침 자체가 성립하지 않아 전부 오탐이 된다.
        if row["result"].get("has_bus") or other["result"].get("has_bus"):
            continue
        # ★ 총 소요가 아니라 **순수 승차 소요**로 본다. 막차 근처에서는 대기시간이
        #   방향마다 크게 다른 게 정상이라, 총 소요로 보면 심야가 통째로 오탐이 된다.
        ta, tb = row["result"].get("ride_sum"), other["result"].get("ride_sum")
        if ta is None or tb is None or min(ta, tb) <= 0:
            continue
        gap = abs(ta - tb)
        if gap > args.sym_min and gap > min(ta, tb) * args.sym_ratio:
            add(findings, "INV-SYM", "critical", row["probe"],
                f"정방향 승차 {ta}분 / 역방향 승차 {tb}분 — {gap:.0f}분 차이 "
                f"(역방향 탐침 {other['probe']['id']})",
                {"forward_ride": ta, "reverse_ride": tb, "reverse_probe": other["probe"]["id"]})

    # 4) 요일축 — 같은 시각인데 평일/휴일 도착이 크게 다르다
    by_dt = collections.defaultdict(dict)
    for row in rows:
        if row.get("error"):
            continue
        p = row["probe"]
        by_dt[(p["_route"], p["_dir"], p["_depart_min"])][p["_dtag"]] = row
    for key, d in by_dt.items():
        vals = {k: v["result"].get("arrive_min") for k, v in d.items()
                if v["result"].get("arrive_min") is not None}
        if len(vals) < 2:
            continue
        lo_k = min(vals, key=vals.get)
        hi_k = max(vals, key=vals.get)
        if vals[hi_k] - vals[lo_k] > args.daytype_min:
            add(findings, "INV-DAYTYPE", "info", d[hi_k]["probe"],
                f"{lo_k} 도착 {fmt_min(vals[lo_k])} / {hi_k} 도착 {fmt_min(vals[hi_k])} — "
                f"{vals[hi_k] - vals[lo_k]}분 차이")

    # 5) 판단불가 편중 — 특정 노선·역에만 몰리면 데이터 구멍이다
    tot = collections.Counter()
    bad = collections.Counter()
    # ★ 비율만으로는 "왜"로 못 간다. 요일·방향별로 쪼개 두면 38% 같은 숫자가
    #   "토·일·공휴일 정방향에서만" 처럼 바로 짚을 수 있는 모양이 된다.
    cell_tot = collections.Counter()
    cell_bad = collections.Counter()
    for row in rows:
        if row.get("error"):
            continue
        unk = (row["result"].get("verdict") == "unknown"
               or str(row["result"].get("grade", "")).startswith("근거없음"))
        cell = (row["probe"]["_dtag"], row["probe"]["_dir"])
        for l in row["probe"]["legs"]:
            for nm in (l.get("from"), l.get("to")):
                if isinstance(nm, dict):                      # 자전거 좌표 끝점(v0.7) — 이름으로 센다
                    nm = nm.get("name") or f"{nm.get('lat')},{nm.get('lng')}"
                k = ("자전거" if l.get("mode") == "bike" else l.get("line") or f"버스{l.get('route')}", nm)
                tot[k] += 1
                cell_tot[(k, cell)] += 1
                if unk:
                    bad[k] += 1
                    cell_bad[(k, cell)] += 1
    if tot:
        rates = {k: bad[k] / tot[k] for k in tot if tot[k] >= args.unknown_min_n}
        gr = sum(bad.values()) / sum(tot.values())
        if rates and gr > args.unknown_global:
            # 몰린 게 아니라 전반적으로 많은 것이다 — 역별로 나열하면 오히려 안 보인다
            findings.append({
                "rule": "INV-UNKNOWN", "severity": "warn", "probe_id": None,
                "message": f"판단불가/근거없음이 전반적으로 많다 — 전체 {gr:.0%}. "
                           f"특정 역 문제가 아니라 데이터·규칙 쪽을 본다",
                "rate": round(gr, 3)})
        elif rates:
            # 전체 비율의 1.5배를 넘고 바닥값도 넘는 역만 — 평균을 쓰면 불량 역이
            # 평균 자체를 끌어올려 스스로를 가린다(2026-09-12 자기점검에서 확인).
            for k, v in sorted(rates.items(), key=lambda x: -x[1]):
                if v > args.unknown_floor and v >= gr * 1.5:
                    cells = {f"{dt}/{dr}": round(cell_bad[(k, (dt, dr))]
                                                 / cell_tot[(k, (dt, dr))], 2)
                             for (kk, (dt, dr)) in cell_tot if kk == k}
                    hit = sorted([c for c, r in cells.items() if r > 0.5])
                    clean = sorted([c for c, r in cells.items() if r == 0])
                    where = ""
                    if hit and clean:
                        where = f" — {', '.join(hit)} 에서만 (나머지 {', '.join(clean)} 는 0%)"
                    findings.append({
                        "rule": "INV-UNKNOWN", "severity": "warn", "probe_id": None,
                        "message": f"{k[0]} {k[1]} — 판단불가/근거없음 {bad[k]}/{tot[k]} "
                                   f"({v:.0%}, 전체 {gr:.0%}){where}",
                        "station": list(k), "rate": round(v, 3), "by_cell": cells})

    findings.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["rule"]))
    return findings


# ── 보고서 ────────────────────────────────────────────────────────────────
RULE_DESC = {
    "INV-CRASH": "판정 중 예외가 났다 — 어떤 입력이든 예외 대신 판정이 나와야 한다",
    "INV-MIDNIGHT": "도착이 출발보다 이르다 — 자정 넘김 정규화가 깨진 모양이다",
    "INV-MONO": "늦게 떠났는데 더 일찍 도착한다 — 선입선출이 깨졌다",
    "INV-FLIP": "하루 안에서 성립/불가가 여러 번 뒤집힌다 — 경계 처리가 불안정하다",
    "INV-SYM": "왕복 승차 소요가 과하게 다르다 — 방향·역 순서가 뒤집혔을 수 있다 (버스는 제외)",
    "INV-GRADE": "도착 시각을 내놓으면서 근거 등급은 '근거없음' 이다 — 등급과 판정이 어긋난다 "
                 "('성립 + 도착 미상' 은 의도된 상태라 제외했다)",
    "INV-JUMP": "인접 출발 사이 도착이 크게 벌어진다 — 배차 공백일 수도 있다",
    "INV-DAYTYPE": "평일/휴일 도착 차이가 크다 — 요일축 선택이 의심된다",
    "INV-UNKNOWN": "특정 역·노선에만 판단불가가 몰린다 — 데이터 구멍이다",
}


def write_report(path, findings, rows, routes, dates, args, elapsed):
    by_rule = collections.Counter(f["rule"] for f in findings)
    by_sev = collections.Counter(f["severity"] for f in findings)
    L = []
    L.append("# 이동 판정기 자기점검 보고서\n")
    L.append(f"탐침 {len(rows):,}건 · 구간 {len(routes)}종 · 요일 {len(dates)}종 · "
             f"{args.step}분 간격 · {elapsed:.1f}초\n")
    L.append(f"**이상 {len(findings)}건** — "
             f"치명 {by_sev['critical']} · 주의 {by_sev['warn']} · 참고 {by_sev['info']}\n")
    L.append("> 이 보고서는 **정답과 대조한 결과가 아니다.** 앞뒤가 안 맞는 곳만 모았다.\n"
             "> 참고(info)는 정상일 수 있다 — 배차 공백·요일 시간표 차이가 그대로 걸린다.\n")
    if not findings:
        L.append("\n이번 탐침 범위에서는 걸린 것이 없다. "
                 "`--step` 을 줄이거나 `--seeds` 를 늘려 범위를 넓혀 본다.\n")

    for rule, n in sorted(by_rule.items(), key=lambda x: (SEV_ORDER.get(
            next(f["severity"] for f in findings if f["rule"] == x[0]), 9), -x[1])):
        fs = [f for f in findings if f["rule"] == rule]
        L.append(f"\n## {rule} — {n}건 ({fs[0]['severity']})\n")
        L.append(f"{RULE_DESC.get(rule, '')}\n")
        for f in fs[:args.per_rule]:
            head = f"- **{f.get('probe_id') or '—'}**"
            if f.get("date"):
                head += f" `{f['date']} {f['depart_at']}`"
            if f.get("src"):
                head += f" (씨앗 {f['src']})"
            L.append(head)
            L.append(f"  - {f['message']}")
            if f.get("probe_id"):
                L.append(f"  - 재현: `--only {f['probe_id']}`")
        if n > args.per_rule:
            L.append(f"\n  … 외 {n - args.per_rule}건 (전체는 JSON 에)")
    Path(path).parent.mkdir(parents=True, exist_ok=True)      # .metrics/ 가 없는 기기(집 PC · 41)
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


# ── GPT 설명 (선택) ────────────────────────────────────────────────────────
EXPLAIN_SYS = (
    "너는 파이썬 코드 리뷰어다. 지하철 경로 성립 여부를 판정하는 코드가 있고, "
    "그 코드가 낸 결과에서 논리적으로 앞뒤가 안 맞는 사례들이 발견됐다. "
    "각 사례마다 (1) 왜 이 결과가 이상한지 한 줄 (2) 어떤 처리 단계가 의심되는지 "
    "(시각 정규화 / 방향·역순서 / 환승 / 요일축 / 막차 경계 / 데이터 누락 중) "
    "(3) 무엇을 먼저 확인해야 하는지 한 줄. 한국어로, 사례당 3줄 이내로 답하라. "
    "코드를 보지 않았으므로 단정하지 말고 의심 순위로 말하라."
)


def explain(findings, model, n):
    try:
        from openai import OpenAI
    except ImportError:
        print("! openai 패키지가 없다 — pip install openai 후 --explain 을 다시 쓴다")
        return None
    import os
    if not os.environ.get("OPENAI_API_KEY"):
        try:
            from dotenv import load_dotenv
            load_dotenv(REPO / ".env")
        except ImportError:
            pass
    if not os.environ.get("OPENAI_API_KEY"):
        print("! OPENAI_API_KEY 가 없다 — .env 를 확인한다")
        return None
    top = findings[:n]
    body = "\n".join(
        f"{i+1}. [{f['rule']}] {f.get('probe_id') or ''} {f.get('date') or ''} "
        f"{f.get('depart_at') or ''} — {f['message']}"
        for i, f in enumerate(top))
    cli = OpenAI()
    r = cli.chat.completions.create(
        model=model, temperature=0,
        messages=[{"role": "system", "content": EXPLAIN_SYS},
                  {"role": "user", "content": body}])
    return r.choices[0].message.content


# ── main ──────────────────────────────────────────────────────────────────
def main():
    import time
    ap = argparse.ArgumentParser(description="이동 판정기 자기점검 — 정답 없이 이상을 찾는다")
    ap.add_argument("--seeds", nargs="+", required=True,
                    help="구간을 빌려올 케이스 JSON. 와일드카드를 써도 된다 "
                         "(PowerShell 을 위해 스크립트가 직접 편다)")
    ap.add_argument("--timetable"); ap.add_argument("--order")
    ap.add_argument("--transfer-walk"); ap.add_argument("--bus-route")
    ap.add_argument("--bus-stops"); ap.add_argument("--station-coords")
    ap.add_argument("--station-exits")

    ap.add_argument("--bike-stations")
    ap.add_argument("--bus-profile", help="버스 구간 통행시간 프로파일(v0.9 · 41) · 'none' 이면 종전 모델 · 기본 processed")
    ap.add_argument("--rules", default=str(REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "rules_v0.3.json"))
    ap.add_argument("--holidays",
                    default=str(REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "holidays_2026_2027.json"))
    ap.add_argument("--base-date", default="2026-09-14", help="요일 대표일을 여기서부터 고른다")
    ap.add_argument("--step", type=int, default=30, help="출발 시각 간격(분)")
    ap.add_argument("--from-min", type=int, default=5 * 60)
    ap.add_argument("--to-min", type=int, default=28 * 60)
    ap.add_argument("--max-routes", type=int, default=None)
    ap.add_argument("--no-reverse", action="store_true", help="역방향 탐침을 만들지 않는다")
    ap.add_argument("--only", help="이 탐침 id 만 돌린다(재현용)")
    # 임계값 — 전부 바꿀 수 있게 둔다. 기본값은 보수적으로(오탐을 줄이는 쪽으로) 잡았다.
    ap.add_argument("--mono-tol", type=int, default=0, help="단조성 허용 오차(분)")
    ap.add_argument("--sym-min", type=int, default=10, help="왕복 차이 최소 분")
    ap.add_argument("--sym-ratio", type=float, default=0.5, help="왕복 차이 비율")
    ap.add_argument("--jump-min", type=int, default=None,
                    help="인접 급변 임계(분). 기본값은 step 의 4배(최소 60) — "
                         "간격을 넓히면 도착 차이도 같이 벌어지므로 고정값을 쓰면 오탐이 는다")
    ap.add_argument("--daytype-min", type=int, default=30)
    ap.add_argument("--unknown-min-n", type=int, default=5)
    ap.add_argument("--unknown-floor", type=float, default=0.3)
    ap.add_argument("--unknown-global", type=float, default=0.6,
                    help="전체 판단불가 비율이 이보다 높으면 역별 편중 대신 한 건으로 낸다")
    ap.add_argument("--per-rule", type=int, default=8, help="보고서에 규칙당 몇 건까지 쓸지")
    # ★ v0.8(39번 방): 기본 출력을 추적 파일(selfcheck_report.md)에서 미추적 폴더(.metrics/)로 옮긴다 —
    #   50번 방에서 자기점검이 추적 파일을 덮어써 merge 를 막았다(결함 2). `selfcheck*.json` 과 같은 자리다.
    ap.add_argument("--out", default=".metrics/selfcheck_report.md")
    ap.add_argument("--lfd", action="store_true", help="마지막 성립 출발 역산까지 돌린다(느리다 · 기본 끔)")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--explain", action="store_true", help="상위 이상을 GPT 에게 설명시킨다")
    ap.add_argument("--explain-model", default="gpt-4o-mini")
    ap.add_argument("--explain-n", type=int, default=15)
    args = ap.parse_args()
    if args.jump_min is None:
        args.jump_min = max(60, args.step * 4)

    if not all((args.timetable, args.order, args.transfer_walk, args.bus_route,
                args.bus_stops, args.station_coords, args.station_exits)):
        # 판정기와 같은 경로 규칙(.env DATA_DIR) — `scripts` 패키지 이름에 기대지 않는다(팀 final_project_cs/scripts 와 이름이 겹친다 · 41)
        from app.modules.travel_ops.mobility.engine.paths import PROCESSED
        M = PROCESSED / "mobility"
        args.timetable = args.timetable or str(M / "timetable_v1.jsonl")
        args.order = args.order or str(M / "line_station_order_v1.json")
        args.transfer_walk = args.transfer_walk or str(M / "transfer_walk_v1.json")
        args.bus_route = args.bus_route or str(M / "bus_route_v1.jsonl")
        args.bus_stops = args.bus_stops or str(M / "bus_stops_v1.jsonl")
        args.station_coords = args.station_coords or str(M / "station_coords.json")
        args.station_exits = args.station_exits or str(M / "station_exits_v1.json")
        args.bike_stations = args.bike_stations or str(M / "bike_stations_v1.jsonl")

    holidays = set(json.loads(Path(args.holidays).read_text(encoding="utf-8"))["holidays"])
    rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))

    routes, dates, probes = build_probes(
        args.seeds, holidays, args.step, args.from_min, args.to_min,
        _date.fromisoformat(args.base_date),
        with_reverse=not args.no_reverse, max_routes=args.max_routes)
    if args.only:
        probes = [p for p in probes if p["id"] == args.only]
        if not probes:
            raise SystemExit(f"탐침 {args.only} 가 없다")

    print(f"구간 {len(routes)}종 × 요일 {len(dates)}종 × "
          f"{(args.to_min - args.from_min) // args.step + 1}시각"
          f"{' × 왕복' if not args.no_reverse else ''} = 탐침 {len(probes):,}건")
    print(f"요일 대표일 {dates}")

    wanted = {(l["line"], nm) for p in probes for l in p["legs"] if l.get("line")
              for nm in (l["from"], l["to"])}
    tt = Timetable.load(args.timetable, wanted)
    lo = LineOrder.load(args.order)
    tw = TransferWalk.load(args.transfer_walk,
                           rules["measured_baseline"]["kakao_walk_speed_mps"]["value"])
    bus = BusRoutes.load(args.bus_route, args.bus_stops)
    sc = StationCoords.load(args.station_coords)
    ex = StationExits.load(args.station_exits)          # 19번 방 — 지하철↔버스 환승용
    bk = BikeStations.load(args.bike_stations)          # 22번 방 — 자전거 씨앗(bike_legs_v1)이 근거없음으로 죽지 않게
    print(f"시간표 {tt.rows:,}행 · 역 {len(tt.stations)} · 수집 {tt.fetched_at}")

    # v0.8 — 혼잡도(@ 부품)를 CLI·runtime 과 같은 두 파일로 연결한다(GPT 대조 2026-09-24 #3 — 없으면 혼잡 가산 경로가 탐침에서 안 돈다)
    from app.modules.travel_ops.mobility.engine.congestion import Congestion
    cg_dir = Path(args.timetable).parent
    cg_data = Congestion.load([cg_dir / "congestion_v1.jsonl", cg_dir / "congestion_line9_v1.jsonl"], wanted)
    print(f"혼잡도 {cg_data.rows:,}셀" if cg_data else "혼잡도 없음(가산 근거없음)")
    # v0.9 — 버스 구간 통행시간 프로파일(41)을 CLI·runtime 과 같은 파일로 연결한다(없으면 버스 승차가 종전 모델로 탐침된다)
    from app.modules.travel_ops.mobility.engine.bus_profile import BusSegProfile
    bus_prof = None if args.bus_profile == "none" else BusSegProfile.load(args.bus_profile)
    print(f"버스 구간 프로파일 {len(bus_prof.index):,}구간" if bus_prof else "버스 구간 프로파일 없음(종전 모델)")
    v = Verifier(tt, lo, rules, holidays, tw, bus, sc, ex, bk=bk, cg_data=cg_data, bus_prof=bus_prof)
    v.lfd_enabled = bool(args.lfd)                       # v0.8 — 탐침 수천 건에 역산을 얹으면 수십 배 느려진다
    t0 = time.time()
    rows = []
    for i, p in enumerate(probes):
        case = {k: v2 for k, v2 in p.items() if not k.startswith("_")}
        try:
            r = v.verify_case(case)
            rides = [l.ride_min for l in (r.legs or []) if l.ride_min is not None]
            rows.append({"probe": p, "result": {
                "verdict": r.verdict, "grade": r.grade, "reason": r.reason,
                "arrive_min": r.arrive_min, "slack_min": r.slack_min,
                # v0.8 — 이중 계산 값과 밖 판
                "arrive_worst_min": r.arrive_worst_min, "margin_min": r.margin_min,
                "buffer_min": r.buffer_min, "code": r.code, "out": r.out,
                "day_type": r.day_type, "n_alt": len(r.alternatives or []),
                "ride_sum": round(sum(rides), 1) if len(rides) == len(r.legs or []) and rides else None,
                "has_bus": any((l.get("mode") == "bus") for l in p["legs"]),
                "warnings": r.warnings}})
        except BaseException as e:                    # SystemExit 도 잡는다 — 그것도 이상이다
            rows.append({"probe": p, "error": f"{type(e).__name__}: {e}",
                         "trace": traceback.format_exc(limit=3)})
        if (i + 1) % 500 == 0:
            print(f"  {i+1:,}/{len(probes):,} …")
    elapsed = time.time() - t0

    tally = collections.Counter(
        r["result"]["verdict"] if not r.get("error") else "ERROR" for r in rows)
    print(f"판정 {dict(tally)} · {elapsed:.1f}초")
    cg_hits = collections.Counter(w["code"] for r in rows if not r.get("error")
                                  for w in (r["result"].get("warnings") or []) if w["code"].startswith("MOB_W_CONGESTION"))
    moved = sum(1 for r in rows if not r.get("error") and r["result"].get("arrive_worst_min") is not None
                and r["result"].get("arrive_min") is not None and r["result"]["arrive_worst_min"] > r["result"]["arrive_min"]
                and not r["result"].get("has_bus"))
    print(f"혼잡 경고 {dict(cg_hits) or 0} · 버스 없는 탐침에서 최악 > 예정(혼잡 다음 편 이동 실행) {moved}건")

    findings = check_invariants(rows, args)
    sev = collections.Counter(f["severity"] for f in findings)
    print(f"\n이상 {len(findings)}건 — 치명 {sev['critical']} · 주의 {sev['warn']} · 참고 {sev['info']}")
    for rule, n in collections.Counter(f["rule"] for f in findings).most_common():
        print(f"  {rule:<14} {n}")

    write_report(args.out, findings, rows, routes, dates, args, elapsed)
    print(f"보고서 → {args.out}")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"probes": len(rows), "routes": len(routes), "dates": dates,
             "findings": findings}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"전체 결과 → {args.json_out}")

    if args.explain and findings:
        print("\nGPT 설명 요청 중 …")
        txt = explain(findings, args.explain_model, args.explain_n)
        if txt:
            Path(args.out).open("a", encoding="utf-8").write(
                "\n\n---\n\n## GPT 설명 (참고 — 코드를 보지 않은 추측이다)\n\n" + txt + "\n")
            print("설명을 보고서 끝에 붙였다")

    sys.exit(1 if sev["critical"] else 0)


if __name__ == "__main__":
    main()
