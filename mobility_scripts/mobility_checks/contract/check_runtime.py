# -*- coding: utf-8 -*-
"""전 구간 점검 — 실제 데이터로 어댑터를 끝까지 돌린다.

  python mobility_scripts/mobility_checks/contract/check_runtime.py

check_adapter.py 는 **가짜 판정기**로 분기를 본다. 이 파일은 **진짜 시간표 46만 행**을 올려
TeamTask 모양 → 판정 → 접기까지 한 번에 돈다. 약 33초 걸린다(전부 상주).

여기서만 잡히는 것: 판정기가 실제로 내는 CaseResult 의 필드 이름이 접기가 기대하는 것과 맞는가.
가짜 판정기는 우리가 만든 모양이라 그 어긋남을 못 본다.
"""
import importlib.util
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = next((p for p in HERE.parents
            if (p / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine").is_dir()), None)
if ROOT is None:
    raise SystemExit(f"final_project_cs/app/modules/travel_ops/mobility/engine 를 못 찾았다 (시작: {HERE})")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "final_project_cs"))
SRC = ROOT / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine"

pkg = types.ModuleType("mob"); pkg.__path__ = [str(SRC)]
sys.modules["mob"] = pkg
for m in ("fold", "adapter", "runtime"):
    spec = importlib.util.spec_from_file_location(f"mob.{m}", SRC / f"{m}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"mob.{m}"] = mod
    setattr(pkg, m, mod)
    spec.loader.exec_module(mod)
A, R = sys.modules["mob.adapter"], sys.modules["mob.runtime"]

ok = fail = 0
def chk(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  OK   {name}")
    else: fail += 1; print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))

print("[1] 판정기 기동 (약 33초)")
t0 = time.time()
rt = R.build_verifier()
load_s = time.time() - t0
chk(f"기동 {load_s:.1f}초", load_s < 180, "3분을 넘으면 상주안이 흔들린다")
chk("시간표 40만 행 이상", rt.stats["timetable_rows"] > 400_000, str(rt.stats))
chk("timetable_built_at 에 출처 접두어", rt.timetable_built_at.split(":")[0] in ("built", "fetched"),
    rt.timetable_built_at)
chk("rules_version 있다", bool(rt.rules_version), rt.rules_version)
chk("두 번 불러도 한 번만 올린다", R.get_verifier(quiet=True) is R.get_verifier(quiet=True))

class Ctx:
    def __init__(self, state): self.current_state = state; self.degraded = False; self.evidence = []
class Task:
    def __init__(self, mob, cap="mobility.check_route"):
        self.task_id = "T-SMOKE"; self.run_id = "R1"; self.case_id = "C-SMOKE"
        self.capability = cap; self.input_text = "여의도에서 왕십리"
        self.context = Ctx({"case_id": "C-SMOKE", "intent": "mobility", "mobility": mob})

CAPS = ("mobility.check_route", "mobility.exception", "mobility.status")
ad = A.MobilityAdapter(rt.verify_case,
                       basis={"timetable_built_at": rt.timetable_built_at,
                              "rules_version": rt.rules_version},
                       capabilities=CAPS)

print("\n[2] 성립하는 구간 — R-SHORT-01 과 같은 모양")
r = ad.run(Task({"date": "2026-09-11", "depart_at": "23:00",
                 "legs": [{"line": "05호선", "from": "여의도", "to": "왕십리"}]}))
chk("completed", r["outcome"] == "completed", f'{r.get("failure_code")} / {r.get("warnings")}')
chk("respond", r["next_action"] == "respond")
chk("answer 에 구간 표식", "[L" in (r["answer"] or ""), (r["answer"] or "")[:200])
kinds = [e["value"]["kind"] for e in r["evidence"]]
chk("run 근거 1건", kinds.count("run") == 1, str(kinds))
chk("verdict 근거 1건 이상", kinds.count("verdict") >= 1, str(kinds))
chk("decisions 비지 않음", bool(r["decisions"]))
chk("모든 근거에 등급", all("grade" in e["value"] for e in r["evidence"]))
chk("모든 근거에 observed_at", all(e.get("observed_at") for e in r["evidence"]))

print("\n[3] ★ 막차 이후 — 불가가 불가로 나오는가")
r2 = ad.run(Task({"date": "2026-09-11", "depart_at": "24:40",
                  "legs": [{"line": "05호선", "from": "여의도", "to": "왕십리"}]}))
chk("completed 로 낸다(불가는 실패가 아니다)", r2["outcome"] == "completed", r2.get("failure_code"))
run2 = [e for e in r2["evidence"] if e["value"]["kind"] == "run"][0]
chk("판정이 fail", run2["value"]["verdict"] == "fail", run2["value"]["verdict"])
chk("완화 조건이 담긴다", bool(run2["value"].get("relax")), str(run2["value"].get("relax")))

# ── ★ 2026-09-14: 여기 입력이 「없는 역」이 아니었다.
#   전에는 `02호선 신답` 을 없는 역으로 썼는데 **신답은 2호선 성수지선의 실제 역**이다
#   (성수–용답–신답–용두–신설동). 그래서 판정기가 `ok` 를 내고 이 검사가 FAIL 로 떴다 —
#   판정기가 지어낸 게 아니라 **점검기가 실재하는 역을 없는 역으로 골랐다.**
#   더 나쁜 것은, 신답이 데이터에 없었다면 `unknown` 이 나와 **통과하고 묻혔을** 것이라는 점이다.
#   → 음성 시험의 입력이 진짜 음성인지를 검사가 보장해야 한다. 이름을 실재할 수 없는 것으로 바꾸고,
#     찾은 것(성수지선은 판정된다)은 [4b] 로 고정한다.
GHOST = "없는역테스트ZZ"

print("\n[4] ★ 없는 역 — 지어내지 않는다")
r3 = ad.run(Task({"date": "2026-09-11", "depart_at": "14:00",
                  "legs": [{"line": "02호선", "from": GHOST, "to": "성수"}]}))
run3 = [e for e in r3["evidence"] if e["value"]["kind"] == "run"][0]
v3 = run3["value"]
chk("근거없음으로 낸다", v3["verdict"] == "unknown", f'{v3["verdict"]} / {v3.get("counts")}')
# 아래 둘은 스키마(run.counts · run.legs)에 있는 필드로만 본다 — 등급만 보면 "0건인데 근거없음"과 구별이 안 된다.
chk("판단불가를 세어서 남긴다", (v3.get("counts") or {}).get("unknown", 0) >= 1, str(v3.get("counts")))
# ★ `legs.without_evidence` 를 여기서 보면 안 된다(9/14 에 한 번 그랬다).
#   fold.py 를 열어 보면 그 목록은 **`i > MAXLEG` 인 구간**, 즉 구간 수 상한을 넘어 근거를 못 실은 것만 담는다.
#   "역을 못 찾았다"와 무관하고, 구간이 하나면 **영원히 빈 목록**이라 검사가 절대 통과하지 못한다.
#   구간마다 판정 근거는 **근거없음이어도 반드시 하나 만들어진다**(fold.py "구간마다 반드시 하나").
chk("구간이 요약에서 사라지지 않는다", bool((v3.get("legs") or {}).get("with_evidence")),
    str(v3.get("legs")))
legv = [e["value"] for e in r3["evidence"] if e["value"]["kind"] == "verdict"]
chk("구간 판정도 근거없음으로 남는다",
    bool(legv) and all(x["verdict"] == "unknown" and x["grade"] == "근거없음" for x in legv),
    str([(x.get("leg_id"), x.get("verdict"), x.get("grade")) for x in legv]))

print("\n[4b] ★ 성수지선은 판정된다 — [4] 가 이 역을 '없는 역'으로 잘못 골랐다(9/14)")
r3b = ad.run(Task({"date": "2026-09-11", "depart_at": "14:00",
                   "legs": [{"line": "02호선", "from": "신답", "to": "성수"}]}))
v3b = [e for e in r3b["evidence"] if e["value"]["kind"] == "run"][0]["value"]
chk("신답 → 성수 가 ok", v3b["verdict"] == "ok", f'{v3b["verdict"]} / {v3b.get("counts")}')

# ── ★ 2026-09-14 신설. v7·v8 전달문에 팀장에게 이렇게 적어 보냈다 —
#   "역명이 안 맞으면 조용히 실패하지 않습니다 — 같은 노선에서 비슷한 역명 후보를 돌려드립니다."
#   **남에게 약속한 동작인데 점검이 없었다.** 여기서 건다.
#   입력 '왕십리리' 는 02호선 실재 역 '왕십리' 와 difflib 비율 0.857 이라 cutoff 0.5 를 넘는다
#   (verify_time.Timetable.similar, n=5 · cutoff 0.5).
print("\n[4c] ★ 조용히 실패하지 않는다 — 비슷한 역명을 돌려준다")
r4c = ad.run(Task({"date": "2026-09-11", "depart_at": "14:00",
                   "legs": [{"line": "02호선", "from": "왕십리리", "to": "성수"}]}))
v4c = [e for e in r4c["evidence"] if e["value"]["kind"] == "run"][0]["value"]
warns = [w for e in r4c["evidence"] for w in (e["value"].get("warnings") or [])]
wcodes = [w.get("code") for w in warns]
wtexts = " / ".join(w.get("text", "") for w in warns) + " || " + (r4c["answer"] or "")
chk("근거없음으로 낸다", v4c["verdict"] == "unknown", f'{v4c["verdict"]} / {v4c.get("counts")}')
chk("역명 미발견 경고를 남긴다", "MOB_W_STATION_NOT_IN_TIMETABLE" in wcodes, str(wcodes))
chk("비슷한 역명이 실제로 실린다", "왕십리" in wtexts and "비슷한 역명" in wtexts, wtexts[:400])

print("\n[5] 예산")
chk(f"근거 {len(r['evidence'])}건 ≤ 40", len(r["evidence"]) <= 40)
chk(f"answer {len(r['answer'] or '')}자 ≤ 6000", len(r["answer"] or "") <= 6000)

print(f"\n합계 {ok+fail} · 통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
