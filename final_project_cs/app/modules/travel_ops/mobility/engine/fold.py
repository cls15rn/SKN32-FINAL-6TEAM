# -*- coding: utf-8 -*-
"""Verdict → TeamResult 접기. 순수 함수다 — DB·네트워크를 보지 않는다.

왜: 코어는 `answer` 와 `evidence` 만 저장한다. 우리 산출은 전부 `decisions[]` 에 있어
    저장 단계에서 버려진다(회귀 85건 기준 보존율 33.5%). 판정 상세를 `Evidence.value` 로
    올려 100% 로 만든다. 설계서: `2026-09-13_Mobility_Verdict접기_설계_v1.md`.

경계: 이 파일은 계약 타입을 import 하지 않는다. dict 를 만들 뿐이다.
      팀 코드가 오면 `MobilityTeam.execute()` 가 이 dict 로 `TeamResult` 를 만든다.
      **팀 코드 없이 지금 돌아가고, 골든 픽스처가 그대로 시험이 된다.**

v0.6 (2026-09-20 · 21번 방): 자동차·택시 구간(LegResult.car)과 성립한 택시 대안(CaseResult.taxi.car)을
      `kind = car_leg` 로 올린다 — 거리·소요·커버·링크 요약·요금뿐, 좌표열·edge 는 없다(저장소 3차 변경점 §3).
"""

# 판정 어휘가 두 곳에서 다르다 — 판정기는 feasible/infeasible, 저장소 CHECK 는 ok/fail.
# 저장소 어휘를 정본으로 삼는다(설계서 §15).
VERDICT = {"feasible": "ok", "infeasible": "fail",
           "rejected_by_limit": "rejected_by_limit", "unknown": "unknown"}
# 계약 decisions[] 는 3종만 쓴다 — 탈락은 fail + 완화 조건으로 접는다(DDL 주석).
DECISION = {"ok": "ok", "fail": "fail", "rejected_by_limit": "fail", "unknown": "unknown"}
CONF = {"확정": 0.95, "추정": 0.6, "근거없음": 0.2}
SRC_OF = [("timetable_v1", "timetable"), ("tago_subway", "timetable"),
          ("mobility_rules", "rule"), ("transfer_walk", "walk"),
          ("osm_subway_entrance", "walk"), ("station_coords", "walk"),   # 정류장↔역 환승 도보 (v0.4 · 19번 방)
          ("bus", "bus"), ("core.current_state", "issue"),
          ("seoul_bike_station", "bike"), ("seoul_bikeList", "bike"), ("osm_bike_graph", "bike"),   # 따릉이 (v0.7 · 22번 방)
          ("bikeseoul_foreigner_guide", "bike")]
CAR_KEYS = ("mode", "distance_m", "topis_time_s", "gh_time_s", "day_type", "hour_start", "coverage_m", "coverage_pct",
            "n_edges", "n_links", "links", "source_id", "fare_kind", "fare_won", "meter_won", "toll_won", "toll_basis",
            "slow_s", "slow_m", "night_rate", "out_of_city", "fare_basis")   # car_leg 로 올리는 필드 — 경로·좌표는 없다


def _car_value(car, leg_id, depart_min, arrive_min, warnings, basis):
    """LegResult.car / taxi.car (CarService.leg 요약) → Evidence.value kind=car_leg."""
    v = {"v": 1, "kind": "car_leg", "leg_id": leg_id, "grade": car.get("grade", "근거없음")}
    for k in CAR_KEYS:
        if k in car:
            v[k] = car[k]
    v["depart_min"], v["arrive_min"] = depart_min, arrive_min
    v["warnings"] = [w for w in (warnings or []) if isinstance(w, dict)][:6]
    v["basis"] = {k: basis[k] for k in ("timetable_built_at", "rules_version")}
    return v
MAXLEG = 8          # 구간 상한 — 예산 34건(run 1 + rule 1 + 8×4)
MAXEV = 40          # ContextPack.evidence 상한. TeamResult 쪽 상한은 미확인이라 같은 값을 쓴다
ANSWER_MAX = 6000


def _of(source_id):
    for k, v in SRC_OF:
        if k in (source_id or ""):
            return v
    return "기타"


def _fmt(m):
    return "미상" if m is None else f"{m // 60:02d}:{m % 60:02d}"


def _leg_id(leg, i):
    """계약 입력에는 leg_id 가 온다. 회귀 케이스에는 없어 idx 로 만든다(설계서 §16)."""
    return leg.get("leg_id") or f"L{leg.get('idx', i)}"


def fold_case(case, *, task_id, basis, case_id=None):
    """판정 결과 한 건(CaseResult dict) → TeamResult dict."""
    legs = case.get("legs") or []
    verdict = VERDICT.get(case.get("verdict"), "unknown")
    grade = case.get("grade", "근거없음")
    sid = f"mobility_verdict@{task_id}"
    ev, decisions, warn_all = [], [], []

    # ── 소스 근거. 판정기는 아직 구조화 value 를 안 만든다 → 과도기 kind 'source'
    n_src = 0
    for e in (case.get("evidence") or []):
        n_src += 1
        ev.append({"evidence_id": f"mob:_:source:{n_src}",
                   "source_type": e.get("source_type", "db"),
                   "source_id": e.get("source_id", "?"), "claim": e.get("claim", ""),
                   "value": {"v": 1, "kind": "source", "leg_id": None,
                             "grade": e.get("grade", "근거없음"), "of": _of(e.get("source_id"))},
                   "confidence": CONF.get(e.get("grade"), 0.2),
                   "observed_at": e.get("observed_at") or basis["timetable_built_at"]})

    # ── 구간 판정. ★ 구간마다 반드시 하나 만든다 — 근거없음이어도(설계서 §5)
    #    ★ without_ev 는 「근거가 없는 구간」이 아니다 — **근거 예산 상한(MAXLEG) 초과분**만 담는다.
    #      이름이 오해를 부른다. 2026-09-14 에 이걸 「근거 없음」으로 읽고 쓴 점검 항목이
    #      **절대 통과할 수 없는 검사**가 됐다(구간이 하나면 이 목록은 영원히 빈다).
    with_ev, without_ev = [], []
    for i, lg in enumerate(legs, 1):
        lid = _leg_id(lg, i)
        if i > MAXLEG:
            without_ev.append(lid)
            continue
        with_ev.append(lid)
        lv = VERDICT.get(lg.get("verdict"), "unknown")
        lw = [w for w in (lg.get("warnings") or []) if isinstance(w, dict)]
        warn_all += lw
        srcs = [x["evidence_id"] for x in ev][:3]
        val = {"v": 1, "kind": "verdict", "leg_id": lid, "grade": lg.get("grade", "근거없음"),
               "verdict": lv, "service_date": basis["service_date"],
               "day_type": case.get("day_type") or "weekday",
               "depart_min": lg.get("depart_min"), "arrive_min": lg.get("arrive_min"),
               "arrive_unknown_code": None if lg.get("arrive_min") is not None else "MOB_R_NO_TRAVEL_MIN",
               "reasons": [{"code": "MOB_R_LEG", "text": (lg.get("reason") or "")[:200]}],
               "relax": ([{"code": "MOB_X_RELIEF", "text": lg["relief"][:200]}] if lg.get("relief") else []),
               "limits": [], "alt_ids": [], "warnings": lw,
               "evidence_ids": srcs, "basis": {k: basis[k] for k in ("timetable_built_at", "rules_version")}}
        if lg.get("wait_min") is not None or lg.get("ride_min") is not None:
            val["by_mode"] = {}
        ev.append({"evidence_id": f"mob:{lid}:verdict:1", "source_type": "db", "source_id": sid,
                   "claim": f"{lg.get('label', lid)} 판정 — {lv}({lg.get('grade')})",
                   "value": val, "confidence": CONF.get(lg.get("grade"), 0.2),
                   "observed_at": basis["decided_at"]})
        if lg.get("car"):                                   # 자동차·택시 구간 요약 (v0.6)
            cv = _car_value(lg["car"], lid, lg.get("depart_min"), lg.get("arrive_min"), lw, basis)
            val["by_mode"] = {cv["mode"]: lv}
            ev.append({"evidence_id": f"mob:{lid}:car_leg:1", "source_type": "db", "source_id": sid,
                       "claim": f"{lg.get('label', lid)} — {cv['distance_m']/1000:.1f} km · {cv['topis_time_s']/60:.1f}분"
                                + (f" · 요금 하한 {cv['fare_won']:,}원" if cv.get("fare_won") is not None else "")
                                + f" [{cv['grade']}]",
                       "value": cv, "confidence": CONF.get(cv["grade"], 0.2),
                       "observed_at": basis["decided_at"]})
        decisions.append({"leg_id": lid, "verdict": DECISION[lv],
                          "evidence_grade": lg.get("grade", "근거없음"),
                          "evidence_ids": [f"mob:{lid}:verdict:1"] + srcs})

    # ── 대안(F3). 제안은 한 건만 내지만 근거는 전부 남긴다
    alt_ids = []
    for j, al in enumerate((case.get("alternatives") or [])[:3], 1):
        aid = f"mob:_:alt:{j}"
        alt_ids.append(aid)
        aw = [w for w in (al.get("warnings") or []) if isinstance(w, dict)]
        warn_all += aw
        ev.append({"evidence_id": aid, "source_type": "db", "source_id": sid,
                   "claim": f"대안 — {al.get('label', '')}",
                   "value": {"v": 1, "kind": "alt", "leg_id": (with_ev or ["L1"])[0],
                             "grade": al.get("grade", "근거없음"),
                             "axis": al.get("axis", "수단교체"),
                             "mode": ((al.get("leg") or {}).get("mode")
                                      or al.get("mode")     # 22번(v0.7): 대안 dict 의 mode(subway/bus/bike)
                                      or ("bus" if "버스" in str(al.get("label")) else "subway")),
                             "verdict": "ok", "depart_min": al.get("depart_min"),
                             "arrive_min": al.get("arrive_min"), "warnings": aw},
                   "confidence": CONF.get(al.get("grade"), 0.2), "observed_at": basis["decided_at"]})

    # ── 택시 대안 (v0.6). 성립했을 때만 — 근거없음(라우터 없음)은 종전대로 answer 문장에만 남는다
    tx = case.get("taxi") or {}
    if tx.get("verdict") == "feasible" and tx.get("car"):
        tw = [w for w in (tx.get("warnings") or []) if isinstance(w, dict)]
        warn_all += tw
        tlid = (with_ev or ["L1"])[-1]                      # 불가가 난 구간 = 마지막 구간
        aid = f"mob:_:alt:{len(alt_ids) + 1}"
        alt_ids.append(aid)
        ev.append({"evidence_id": aid, "source_type": "db", "source_id": sid,
                   "claim": f"대안 — 택시 {tx.get('reason', '')}",
                   "value": {"v": 1, "kind": "alt", "leg_id": tlid, "grade": tx.get("grade", "근거없음"),
                             "axis": "수단교체", "mode": "taxi", "verdict": "ok",
                             "depart_min": tx.get("depart_min"), "arrive_min": tx.get("arrive_min"),
                             "warnings": tw[:6]},
                   "confidence": CONF.get(tx.get("grade"), 0.2), "observed_at": basis["decided_at"]})
        cv = _car_value(tx["car"], tlid, tx.get("depart_min"), tx.get("arrive_min"), tw, basis)
        ev.append({"evidence_id": "mob:_:car_leg:1", "source_type": "db", "source_id": sid,
                   "claim": f"택시 대안 — {cv['distance_m']/1000:.1f} km · {cv['topis_time_s']/60:.1f}분 · "
                            f"요금 하한 {cv.get('fare_won', 0):,}원 [{cv['grade']}]",
                   "value": cv, "confidence": CONF.get(cv["grade"], 0.2), "observed_at": basis["decided_at"]})

    # ── 결과 요약. ★ 이 한 건이 '구간이 조용히 사라지지 않는다'의 마지막 장치다
    cnt = {"ok": 0, "fail": 0, "rejected_by_limit": 0, "unknown": 0}
    for lg in legs:
        cnt[VERDICT.get(lg.get("verdict"), "unknown")] += 1
    case_w = [w for w in (case.get("warnings") or []) if isinstance(w, dict)]
    ev.insert(0, {"evidence_id": "mob:_:run:1", "source_type": "db", "source_id": sid,
                  "claim": f"판정 요약 — 구간 {len(legs)} · {verdict}",
                  "value": {"v": 1, "kind": "run", "grade": grade, "verdict": verdict,
                            "arrive_min": case.get("arrive_min"), "slack_min": case.get("slack_min"),
                            "reasons": ([{"code": "MOB_R_CASE", "text": case["reason"][:200]}]
                                        if case.get("reason") else []),
                            "relax": ([{"code": "MOB_X_RELIEF", "text": case["relief"][:200]}]
                                      if case.get("relief") else []),
                            "counts": cnt,
                            "legs": {"with_evidence": with_ev, "without_evidence": without_ev},
                            "confidence": CONF.get(grade, 0.2),
                            "budget": {"evidence_used": len(ev) + 1, "cap": MAXEV},
                            "warnings": case_w,
                            "basis": {k: basis[k] for k in ("timetable_built_at", "rules_version")}},
                  "confidence": CONF.get(grade, 0.2), "observed_at": basis["decided_at"]})

    # ── answer. to_answer 경고만 문장에 싣는다. 구간마다 [leg_id] 표식(◆10 대비)
    seen, lines = set(), [f"[{case.get('id')}] 판정 {verdict}({grade}) — {case.get('reason', '')}"]
    if case.get("relief"):
        lines.append(f"완화 조건: {case['relief']}")
    for i, lg in enumerate(legs, 1):
        lid = _leg_id(lg, i)
        t = f"[{lid}] {lg.get('label', '')} {VERDICT.get(lg.get('verdict'), 'unknown')}" \
            f"({lg.get('grade')}) — 도착 {_fmt(lg.get('arrive_min'))}"
        lines.append(t)
    if tx.get("verdict") == "feasible":
        lines.append(f"[{(with_ev or ['L1'])[-1]}] 택시 대안 {_fmt(tx.get('depart_min'))} 출발 → 도착 {_fmt(tx.get('arrive_min'))} · "
                     f"{tx.get('reason', '')} ({tx.get('grade')})")
    for w in warn_all + case_w:
        if w.get("to_answer") and w["code"] not in seen:
            seen.add(w["code"])
            lines.append(f"· {w['text']}")
    answer = "\n".join(lines)[:ANSWER_MAX]

    return {"contract_name": "a_cop.team_result", "contract_version": "1.0",
            "task_id": task_id, "team_id": "mobility",
            "outcome": "completed", "next_action": "respond",
            "confidence": CONF.get(grade, 0.2), "answer": answer,
            "evidence": ev, "decisions": decisions,
            "action_proposals": [], "warnings": [f"{w['code']}" for w in warn_all + case_w]}
