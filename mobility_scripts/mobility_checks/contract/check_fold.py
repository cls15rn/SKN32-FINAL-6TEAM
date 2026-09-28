# -*- coding: utf-8 -*-
"""접기 산출물 자체 점검 — 스키마 + 구조 보장 8가지.
팀 코드 없이 돌아간다. 어댑터가 생기면 이 파일이 그 어댑터의 첫 테스트가 된다."""
import json, os, re, sys
from jsonschema import Draft202012Validator

D = os.path.dirname(os.path.abspath(__file__))
SCHEMA = json.load(open(os.path.join(D, "mob_evidence_value_v1.schema.json"), encoding="utf-8"))
R = json.load(open(os.path.join(D, "mob_evidence_golden_v1.json"), encoding="utf-8"))
V = Draft202012Validator(SCHEMA)
fail = []
def ck(cond, msg):
    print(("  OK  " if cond else "  FAIL") + "  " + msg)
    if not cond: fail.append(msg)

ev = R["evidence"]
ids = [e["evidence_id"] for e in ev]

print("[1] value 스키마")
for e in ev:
    errs = sorted(V.iter_errors(e["value"]), key=lambda x: x.path)
    ck(not errs, f"{e['evidence_id']} ({e['value']['kind']})" + ("" if not errs else " :: " + errs[0].message[:160]))

print("[2] evidence_id 결정적 모양  mob:{leg|_}:{kind}:{n}")
for e in ev:
    m = re.fullmatch(r"mob:([A-Za-z0-9_]+):([a-z_]+):(\d+)", e["evidence_id"])
    ck(bool(m) and m.group(2) == e["value"]["kind"], e["evidence_id"])
ck(len(ids) == len(set(ids)), "id 중복 없음")

print("[3] source_type 는 3종만 (저장소 CHECK)")
for e in ev:
    ck(e["source_type"] in ("db", "policy", "case_event"), f"{e['evidence_id']} = {e['source_type']}")

print("[4] 구간이 조용히 사라지지 않는다")
legs_dec = {d["leg_id"] for d in R["decisions"]}
legs_ver = {e["value"]["leg_id"] for e in ev if e["value"]["kind"] == "verdict"}
ck(legs_dec == legs_ver, f"decisions 구간 {sorted(legs_dec)} == verdict evidence 구간 {sorted(legs_ver)}")
run = [e for e in ev if e["value"]["kind"] == "run"]
ck(len(run) == 1, "run 근거 정확히 1건")
rv = run[0]["value"]
ck(set(rv["legs"]["with_evidence"]) | set(rv["legs"]["without_evidence"]) == legs_dec, "run.legs 가 전 구간을 덮는다")
ck(not (set(rv["legs"]["with_evidence"]) & set(rv["legs"]["without_evidence"])), "with/without 겹치지 않음")
c = rv["counts"]
ck(sum(c.values()) == len(legs_dec), f"counts 합 {sum(c.values())} == 구간 수 {len(legs_dec)}")

print("[5] 근거 그래프가 evidence 안에서 자족한다 (decisions 가 버려져도)")
for e in ev:
    for rid in e["value"].get("evidence_ids", []):
        ck(rid in ids, f"{e['evidence_id']} -> {rid}")
for p in R["action_proposals"]:
    for rid in p["rationale_evidence_ids"]:
        ck(rid in ids, f"proposal -> {rid}")
for e in ev:
    for aid in e["value"].get("alt_ids", []):
        ck(aid in ids, f"{e['evidence_id']} -> alt {aid}")

print("[6] 예산")
cap = rv["budget"]["cap"]
ck(rv["budget"]["evidence_used"] == len(ev), f"budget.evidence_used {rv['budget']['evidence_used']} == 실제 {len(ev)}")
ck(len(ev) <= cap <= 40, f"{len(ev)} <= cap {cap} <= 40")

print("[7] answer 가 to_answer 경고를 실제로 싣는다")
ans = R["answer"]
for e in ev:
    for w in e["value"].get("warnings", []):
        if w["to_answer"]:
            lg = w.get("leg_id") or e["value"].get("leg_id")
            ck(lg is None or f"[{lg}]" in ans, f"{w['code']} ({lg}) 가 answer 에 자리 있음")
ck(all(f"[{l}]" in ans for l in legs_dec), "모든 구간이 answer 에 표식으로 나온다")

print("[8] 계약 검증기가 강제하는 것")
ck(bool(R["answer"]) and len(ev) > 0, "answer 있으면 evidence 비지 않음")
ck(len(R["answer"]) <= 6000, f"answer {len(R['answer'])}자 <= 6000")
ck(R["next_action"] == "respond" and bool(R["answer"]), "respond 는 answer 필요")
ck(not any(p["approval_required"] for p in R["action_proposals"]) or R["next_action"] == "wait_for_approval",
   "approval_required 제안이 있으면 wait_for_approval")
ck(len(R["action_proposals"]) <= 1, "제안은 한 결과에 한 건 (멱등 키)")


print("[9] 음성 시험 — 담지 않기로 한 것이 실제로 거부되는가")
_base = [e["value"] for e in ev if e["value"]["kind"] == "verdict"][0]
def _t(name, mut):
    v = json.loads(json.dumps(_base)); mut(v)
    ck(not V.is_valid(v), "거부 — " + name)
_t("경로 나열(path)", lambda v: v.update(path=["을지로입구", "시청"]))
_t("좌표(coords)", lambda v: v.update(coords={"lat": 37.5, "lng": 127.0}))
_t("지도 링크(link)", lambda v: v.update(link="https://map.kakao.com/link/by/..."))
_t("외부 API 원값(route_api_raw)", lambda v: v.update(route_api_raw={"a": 1}))
_t("등급에 없는 값", lambda v: v.update(grade="대충"))
_t("옛 어휘(feasible) — DDL 과 다른 판정값", lambda v: v.update(verdict="feasible"))
_t("시각을 문자열로", lambda v: v.update(depart_min="23:52"))
_t("옛 이름(margin_min) — verify_time 에는 slack_min 이다", lambda v: v.update(margin_min=12))
_t("경고 코드 규칙 위반", lambda v: v.update(warnings=[{"code": "긴도보", "class": "effort", "text": "x", "to_answer": True}]))
_t("근거 등급 자리 비움", lambda v: v.pop("grade"))
_t("판 번호 없음", lambda v: v.pop("v"))
_r = json.loads(json.dumps([e["value"] for e in ev if e["value"]["kind"] == "rule"][0]))
_r["used"] = {"buffer": {"min": 10}}
ck(not V.is_valid(_r), "거부 — 규칙 used 에 중첩 객체")


print("[10] 경고 어휘 — rules_v0.3.json `warnings` 절")
import re as _re
RULES = os.path.join(D, "..", "..", "..", "final_project_cs", "app", "modules",
                     "travel_ops", "mobility", "engine", "rules", "rules_v0.3.json")   # 31번 방 — 규칙표가 패키지로 갔다
if not os.path.exists(RULES):
    RULES = os.path.join(D, "rules_v0.3.json")   # 대화방에서 단독으로 돌릴 때
try:
    WR = json.load(open(RULES, encoding="utf-8"))["warnings"]
except Exception as _e:
    WR = None
    ck(False, "rules_v0.3.json 의 warnings 절을 읽지 못했다: %s" % _e)
if WR:
    codes = {k: v for k, v in WR.items() if k.startswith("MOB_W_")}
    ck(bool(codes), "경고 코드 %d 종 (구현 %d · 미구현 %d)" % (
        len(codes), sum(1 for v in codes.values() if v.get("구현")),
        sum(1 for v in codes.values() if not v.get("구현"))))
    CLS = {"cost", "effort", "better_option", "data", "grade"}
    for c, v in codes.items():
        ok = _re.fullmatch(r"MOB_W_[A-Z0-9_]+", c) and v.get("class") in CLS \
             and isinstance(v.get("to_answer"), bool) and ("text" in v or "text_ref" in v)
        ck(bool(ok), "%s — 모양" % c)
        if "text" in v:
            fields = set(_re.findall(r"\{([a-z_]+)\}", v["text"]))
            ck(fields == set(v.get("params", [])), "%s — text 의 {} %s == params %s" % (
                c, sorted(fields), sorted(v.get("params", []))))
        if "text_ref" in v:
            node, okref = json.load(open(RULES, encoding="utf-8")), True
            for seg in v["text_ref"].split("."):
                if isinstance(node, dict) and seg in node:
                    node = node[seg]
                else:
                    okref = False
                    break
            ck(okref and isinstance(node, str), "%s — text_ref '%s' 가 규칙 안에 실재한다" % (c, v["text_ref"]))
        ck("site" in v, "%s — 코드상 자리(site)가 적혀 있다" % c)
        if v["class"] in ("cost", "effort", "better_option"):
            ck(v["to_answer"] is True, "%s — %s 는 answer 로 간다" % (c, v["class"]))
        if v["class"] == "grade":
            ck(v["to_answer"] is False, "%s — grade 는 answer 에 안 간다" % c)
    used = set()
    for e in ev:
        for w in e["value"].get("warnings", []):
            used.add(w["code"])
    for c in sorted(used):
        ck(c in codes, "픽스처가 쓴 %s 가 규칙에 정의돼 있다" % c)

# ── ★ 2026-09-14 신설. 판 번호가 두 군데에 있다 — rules_version 과 source_id.
#   판정 근거(policy Evidence)의 source_id 는 **source_id 쪽**을 그대로 쓴다(verify_time.py:165 _ev_rule).
#   9/14 에 rules_version 만 v0.3.1 로 올리고 source_id 를 v0.3 으로 두어,
#   **규칙은 v0.3.1 인데 판정 근거는 v0.3 이라고 기록되는** 상태가 있었다.
#   어느 판으로 낸 판정인지를 잃는 것이 이 모듈이 막으려는 바로 그 결함이다.
#   (RULES 는 [10] 에서 잡는다 — 이 절은 반드시 그 뒤, 합계 출력 앞에 온다.)
print("[11] 판 번호가 두 군데에서 같은 것을 가리키는가")
try:
    _R = json.load(open(RULES, encoding="utf-8"))
    _ver, _sid = _R.get("rules_version"), _R.get("source_id", "")
    ck(bool(_ver) and bool(_sid), "rules_version(%s) · source_id(%s) 둘 다 있다" % (_ver, _sid))
    ck(_sid.endswith("@" + str(_ver)),
       "source_id 가 rules_version 으로 끝난다 — %s vs %s" % (_sid, _ver))
except Exception as _e:
    ck(False, "판 번호를 읽지 못했다: %s" % _e)

# ── ★ 2026-09-20 신설(21번 방). 자동차·택시 구간 — kind=car_leg. 골든은 합성 경로 픽스처로 돌린 판정을 접은 것.
#   확인하는 것: ① 스키마 ② 자동차 구간마다 car_leg 근거가 verdict 와 짝으로 있다 ③ 택시 대안이 성립하면 alt(mode=taxi)+car_leg
#   ④ 라우터 다운(CAR-08)은 car_leg 이 **없다**(지어내지 않는다) ⑤ 골목 100%(CAR-06)는 등급 근거없음 ⑥ 하한 경고가 answer 에 있다
#   ⑦ 음성 — 좌표열·edge·경로 API 원값·모르는 mode 는 거부된다.
print("[12] 자동차·택시 접기 — kind=car_leg (v0.6 · 21번 방)")
CG = os.path.join(D, "mob_evidence_car_golden_v1.json")
try:
    CAR = json.load(open(CG, encoding="utf-8"))["results"]
except Exception as _e:
    CAR = None
    ck(False, "mob_evidence_car_golden_v1.json 을 읽지 못했다: %s" % _e)
if CAR:
    ck(set(CAR) >= {"CAR-01", "CAR-02", "CAR-06", "CAR-08", "CAR-11"}, "골든에 다섯 케이스가 있다 %s" % sorted(CAR))
    for cid, res in CAR.items():
        cev = res["evidence"]
        for e in cev:
            errs = sorted(V.iter_errors(e["value"]), key=lambda x: x.path)
            ck(not errs, f"{cid} {e['evidence_id']} ({e['value']['kind']})" + ("" if not errs else " :: " + errs[0].message[:120]))
        cl = [e for e in cev if e["value"]["kind"] == "car_leg"]
        vd = {e["value"]["leg_id"]: e["value"] for e in cev if e["value"]["kind"] == "verdict"}
        for e in cl:
            v = e["value"]
            ck(v["leg_id"] in vd, f"{cid} car_leg {e['evidence_id']} 의 구간 {v['leg_id']} 에 verdict 근거가 있다")
            ck(set(v["coverage_pct"]) == {"topis", "class", "default"} and abs(sum(v["coverage_pct"].values()) - 100) < 0.5,
               f"{cid} 커버 비율 합 100 — {v['coverage_pct']}")
            ck(all(k not in v for k in ("coords", "points", "edges", "path", "geometry")), f"{cid} car_leg 에 경로·좌표 없음")
            ck(v["grade"] in ("추정", "근거없음"), f"{cid} car_leg 등급은 확정이 될 수 없다({v['grade']})")
        if cid == "CAR-01":
            ck(len(cl) == 1 and cl[0]["value"]["mode"] == "car" and "fare_won" not in cl[0]["value"], "CAR-01 자동차 구간 car_leg 1건 · 요금 없음")
            ck(vd["L0"].get("by_mode") == {"car": "ok"}, "CAR-01 verdict.by_mode = {car: ok}")
        if cid == "CAR-02":
            v = cl[0]["value"]
            ck(v["mode"] == "taxi" and v["fare_won"] == 8000 and v["night_rate"] == 0.2, f"CAR-02 택시 요금 8,000 · 심야 0.2 — {v.get('fare_won')}/{v.get('night_rate')}")
            ck("호출료" in res["answer"] and "8,000" in res["answer"], "CAR-02 하한 경고가 answer 에 실렸다")
        if cid == "CAR-06":
            ck(cl and cl[0]["value"]["grade"] == "근거없음" and cl[0]["value"]["coverage_pct"]["default"] > 50, "CAR-06 골목 100% → 근거없음")
            ck(any(w["code"] == "MOB_W_CAR_SPEED_DEFAULT" for w in cl[0]["value"]["warnings"]), "CAR-06 car_leg.warnings 에 MOB_W_CAR_SPEED_DEFAULT")
        if cid == "CAR-08":
            ck(not cl, "CAR-08 라우터 다운 — car_leg 근거를 만들지 않는다")
            ck(vd["L0"]["verdict"] == "unknown" and "MOB_W_CAR_ROUTER_DOWN" in res["warnings"], "CAR-08 verdict unknown + ROUTER_DOWN 경고")
            ck("도로 경로 서버" in res["answer"], "CAR-08 라우터 다운이 answer 에 보인다")
        if cid == "CAR-11":
            alts = [e for e in cev if e["value"]["kind"] == "alt"]
            tx = [e for e in alts if e["value"]["mode"] == "taxi"]
            ck(len(tx) == 1 and tx[0]["value"]["verdict"] == "ok" and tx[0]["value"]["arrive_min"] == 280, "CAR-11 택시 대안 alt(mode=taxi, 04:40)")
            ck(len(cl) == 1 and cl[0]["evidence_id"] == "mob:_:car_leg:1" and cl[0]["value"]["fare_won"] == 6700, "CAR-11 택시 대안 car_leg 1건 · 요금 6,700")
            ck("택시 대안" in res["answer"] and "6,700" in res["answer"], "CAR-11 택시 대안이 answer 에 있다")
    _c = json.loads(json.dumps([e["value"] for e in CAR["CAR-02"]["evidence"] if e["value"]["kind"] == "car_leg"][0]))
    def _tc(name, mut):
        v = json.loads(json.dumps(_c)); mut(v)
        ck(not V.is_valid(v), "거부 — car_leg " + name)
    _tc("좌표열(coords)", lambda v: v.update(coords=[[127.0, 37.5]]))
    _tc("경로 점(points)", lambda v: v.update(points={"coordinates": [[127.0, 37.5]]}))
    _tc("edge 목록(edges)", lambda v: v.update(edges=[{"way": 1, "m": 10}]))
    _tc("경로 API 원값(route_api_raw)", lambda v: v.update(route_api_raw={"paths": []}))
    _tc("모르는 mode(bike)", lambda v: v.update(mode="bike"))
    _tc("등급 확정 이외 값", lambda v: v.update(grade="대충"))
    _tc("커버 비율 100 초과", lambda v: v["coverage_pct"].update(topis=120))
    _tc("링크 요약 21개 초과", lambda v: v.update(links=[{"link_id": "1", "m": 1.0}] * 21))
    _tc("시계외 문자열", lambda v: v.update(out_of_city="yes"))
    ck(V.is_valid(_c), "원본 car_leg 은 통과한다")

print("\n실패 %d 건" % len(fail))
sys.exit(1 if fail else 0)
