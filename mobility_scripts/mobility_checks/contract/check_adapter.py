# -*- coding: utf-8 -*-
"""어댑터 점검 — 계약 객체 없이, 가짜 판정기로 돈다.

  python mobility_scripts/mobility_checks/contract/check_adapter.py

★ 경로를 박지 않는다. 이 파일 위치에서 저장소 뿌리를 거슬러 올라가 final_project_cs/app/modules/travel_ops/mobility/engine 를 찾는다.
  (2026-09-14: 컨테이너 절대경로가 박혀 있어 노트북에서 FileNotFoundError 가 났다.)
"""
import importlib.util
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = next((p for p in HERE.parents
            if (p / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine").is_dir()), None)
if ROOT is None:
    raise SystemExit(f"final_project_cs/app/modules/travel_ops/mobility/engine 를 못 찾았다 (시작: {HERE})")
SRC = ROOT / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine"

# 패키지로 올려야 adapter.py 의 `from . import fold` 가 산다.
pkg = types.ModuleType("mob"); pkg.__path__ = [str(SRC)]
sys.modules["mob"] = pkg
for m in ("fold", "adapter"):
    f = SRC / f"{m}.py"
    if not f.exists():
        raise SystemExit(f"없다: {f}")
    spec = importlib.util.spec_from_file_location(f"mob.{m}", f)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"mob.{m}"] = mod
    setattr(pkg, m, mod)
    spec.loader.exec_module(mod)
A = sys.modules["mob.adapter"]
ADAPTER_SRC = SRC / "adapter.py"

ok = fail = 0
def chk(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  OK   {name}")
    else: fail += 1; print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))

class Ctx:
    def __init__(self, state, degraded=False):
        self.current_state = state; self.degraded = degraded; self.evidence = []
class Task:
    def __init__(self, state, cap="mobility.check_route", degraded=False):
        self.task_id = "T1"; self.run_id = "R1"; self.case_id = "C1234567"
        self.capability = cap; self.input_text = "명동에서 경복궁 갈 수 있나요"
        self.context = Ctx(state, degraded)

GOOD = {"mobility": {"date": "2026-09-24", "depart_at": "09:00",
                     "legs": [{"line": "05호선", "from": "여의도", "to": "왕십리"}]}}
BASIS = {"timetable_built_at": "2026-09-09", "rules_version": "rules_v0.3"}

seen = {}

class _Res:
    """★ 진짜 판정기는 **CaseResult 데이터클래스**를 돌려준다. 가짜가 dict 를 주면
    경계 변환이 빠진 것을 못 잡는다 — 2026-09-14 에 실제로 그랬다(check_runtime 이 잡음)."""
    def __init__(self, d): self.__dict__.update(d)

def fake_verify(case):
    seen["case"] = case
    return _Res({"id": case["id"], "verdict": "feasible", "grade": "추정",
            "reason": "성립", "arrive_min": 574, "slack_min": None, "warnings": [],
            "evidence": [{"source_type": "db", "source_id": "tago_subway@2026-09-09",
                          "claim": "막차 24:25", "grade": "확정"}],
            "legs": [{"line": "05호선", "from": "여의도", "to": "왕십리", "verdict": "feasible",
                      "grade": "추정", "arrive_min": 574, "label": "여의도→왕십리",
                      "evidence": [], "warnings": []}]})

CAPS = ("mobility.check_route", "mobility.exception", "mobility.status")
ad = A.MobilityAdapter(fake_verify, basis=BASIS, capabilities=CAPS)

print("[1] 정상 경로")
r = ad.run(Task(GOOD))
chk("outcome=completed", r["outcome"] == "completed", r.get("failure_code"))
chk("next_action=respond", r["next_action"] == "respond")
chk("answer 있다", bool(r.get("answer")))
chk("evidence 있다", len(r["evidence"]) >= 2)
chk("판정기에 넘어간 케이스가 우리 모양", set(seen["case"]) >= {"date", "depart_at", "legs", "id"})
chk("case_id 로 id 를 채웠다", seen["case"]["id"] == "C1234567"[:8])

print("[2] ★ 입력이 없으면 지어내지 않는다")
r = ad.run(Task({"case_id": "C1", "intent": "mobility"}))
chk("mobility_input_missing", r.get("failure_code") == "mobility_input_missing", r.get("failure_code"))
chk("escalate 로 낸다", r["next_action"] == "escalate" and r["outcome"] == "escalated")
chk("answer 를 만들지 않는다", r["answer"] is None)
chk("warnings 가 비지 않는다(계약 요구)", bool(r["warnings"]))

print("[3] 부분 입력")
r = ad.run(Task({"mobility": {"date": "2026-09-24"}}))
chk("legs 없음을 이름까지 말한다", r.get("failure_code", "").startswith("mobility_input_incomplete:legs"), r.get("failure_code"))
r = ad.run(Task({"mobility": {"date": "2026-09-24", "legs": [{"line": "05호선", "from": "a", "to": "b"}]}}))
chk("시각이 둘 다 없으면 거부", "depart_at|arrive_by" in r.get("failure_code", ""), r.get("failure_code"))
r = ad.run(Task({"mobility": {"date": "2026-09-24", "legs": [{"line": "05호선", "from": "a", "to": "b"}],
                              "arrive_by": "10:30"}}))
chk("arrive_by 만 있어도 통과", r["outcome"] == "completed", r.get("failure_code"))
r = ad.run(Task({"mobility": ["legs"]}))
chk("dict 가 아니면 malformed", r.get("failure_code") == "mobility_input_malformed")

print("[4] 모르는 키는 버리되 알린다")
bad = {"mobility": dict(GOOD["mobility"], weather="비", budget=30000)}
r = ad.run(Task(bad))
chk("판정은 그대로 된다", r["outcome"] == "completed")
chk("unknown_keys 를 warnings 에 남긴다",
    any("unknown_keys" in w and "weather" in w and "budget" in w for w in r["warnings"]), r["warnings"])
chk("판정기에는 안 넘긴다", "weather" not in seen["case"])

print("[5] capability 분기")
r = ad.run(Task(GOOD, cap="dining.suggest"))
chk("남의 capability 는 escalate", r.get("failure_code") == "unsupported_capability")
r = ad.run(Task(GOOD, cap="mobility.status"))
chk("재확인은 '안 된다'고 말한다", r.get("failure_code") == "recheck_not_implemented")
r = ad.run(Task(GOOD, cap="mobility.exception"))
chk("exception 은 판정으로 간다", r["outcome"] == "completed")

print("[6] degraded")
r = ad.run(Task(GOOD, degraded=True))
chk("degraded 면 조회 전에 끝낸다", r.get("failure_code") == "degraded_context")

print("[7] ★ basis — 케이스마다 달라지는 값을 굳히지 않는다")
try:
    A.MobilityAdapter(fake_verify, basis={"rules_version": "v"}); bad=False
except ValueError: bad=True
chk("timetable_built_at 없으면 생성 거부", bad)
calls=[]
ad2 = A.MobilityAdapter(fake_verify, basis=BASIS, capabilities=CAPS,
                        now=lambda: (calls.append(1), f"2026-09-24T00:00:0{len(calls)}+09:00")[1])
r1 = ad2.run(Task(GOOD))
g2 = {"mobility": dict(GOOD["mobility"], date="2026-10-03")}
r2 = ad2.run(Task(g2))
run1 = [e for e in r1["evidence"] if e["value"]["kind"] == "run"][0]
run2 = [e for e in r2["evidence"] if e["value"]["kind"] == "run"][0]
chk("판정 시각이 호출마다 다르다", run1["observed_at"] != run2["observed_at"],
    f'{run1["observed_at"]} vs {run2["observed_at"]}')
lv2 = [e for e in r2["evidence"] if e["value"]["kind"] == "verdict"]
chk("service_date 가 입력 date 를 따른다",
    all(e["value"].get("service_date") == "2026-10-03" for e in lv2) if lv2 else True,
    str([e["value"].get("service_date") for e in lv2]))

print("[8] 경계 변환 — 객체를 dict 로 바꾸는가")
chk("데이터클래스 산출을 받는다", ad.run(Task(GOOD))["outcome"] == "completed")
chk("dict 산출도 그대로 받는다",
    A.MobilityAdapter(lambda c: fake_verify(c).__dict__, basis=BASIS, capabilities=CAPS)
     .run(Task(GOOD))["outcome"] == "completed")
class _Bad: pass
try:
    A.MobilityAdapter(lambda c: _Bad(), basis=BASIS, capabilities=CAPS).run(Task(GOOD)); bad = False
except TypeError as e:
    bad = "dict 로 못 바꾼다" in str(e) or "verdict 가 없다" in str(e)
chk("판정 아닌 것을 조용히 접지 않는다", bad)

print("[9] ★ 계약 타입을 import 하지 않는다")
# ★ 문자열 검색으로는 안 된다 — 주석·독스트링의 'contracts.py' 언급까지 센다(2026-09-14 에 걸림).
#   AST 로 실제 import 문만 본다.
import ast
tree = ast.parse(ADAPTER_SRC.read_text(encoding="utf-8"))
mods = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Import):
        mods |= {a.name for a in n.names}
    elif isinstance(n, ast.ImportFrom):
        mods.add(n.module or "")
banned = [m for m in mods if "contract" in m or m.startswith("app.")]
chk("계약·코어 모듈을 import 하지 않는다", not banned, f"import: {sorted(mods)} / 금지: {banned}")
chk("표준 라이브러리와 자기 패키지만 쓴다", mods <= {"datetime", "json", ""}, str(sorted(mods)))
chk("dict 로 준 task 도 받는다(duck typing)",
    A.map_task_to_case({"context": {"current_state": GOOD}, "case_id": "X"})[0] is not None)

print(f"\n합계 {ok+fail} · 통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
