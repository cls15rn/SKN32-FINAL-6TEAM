# -*- coding: utf-8 -*-
"""자기점검 탐지기 자체를 점검한다 — **탐지기가 안 우는 것**이 가장 위험하다.

selfcheck_mobility.py 는 "이상이 0건"을 자주 낸다. 그게 정말 깨끗한 것인지,
탐지기가 고장 나 조용한 것인지는 구별되지 않는다. 그래서 과거에 실제로 났던
버그의 모양을 인공 데이터로 넣어 **규칙이 실제로 우는지** 확인한다.

  ① 자정 넘김 미정규화 (2026-09-10) → INV-MIDNIGHT
  ② 선입선출 붕괴                   → INV-MONO
  ③ 행선지 필드 역전 (2026-09-10)   → INV-SYM
  ④ 막차 경계 불안정                → INV-FLIP
  ⑤ 등급·판정 모순                  → INV-GRADE
  ⑥ 판정 중 예외                    → INV-CRASH
  ⑦ 특정 역 판단불가 편중           → INV-UNKNOWN
  ⑧ 정상 데이터 → 아무것도 울지 않아야 한다 (오탐 확인)

실행: python mobility_scripts/mobility_checks/check_selfcheck_invariants.py
"""
import sys, types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# 67: 우리 스크립트 패키지를 `mobility_scripts` 로 옮겨 팀 `scripts`(final_project_cs/scripts)와 이름이 안 겹친다 —
#     41 때의 spec_from_file_location 우회를 걷어내고 보통 import 로 올린다
from mobility_scripts.selfcheck_mobility import check_invariants  # noqa: E402

ARGS = types.SimpleNamespace(
    mono_tol=0, sym_min=10, sym_ratio=0.5, jump_min=60, daytype_min=30,
    unknown_min_n=5, unknown_floor=0.3, unknown_global=0.6, per_rule=8)


def P(pid, route, direction, dtag, m, legs=None):
    return {"id": pid, "_route": route, "_dir": direction, "_dtag": dtag,
            "_depart_min": m, "date": "2026-09-14", "_src": "test",
            "depart_at": f"{m // 60:02d}:{m % 60:02d}",
            "legs": legs or [{"line": "02호선", "from": "A", "to": "B"}]}


def R(verdict="feasible", arrive=None, grade="확정", ride=None, bus=False):
    return {"verdict": verdict, "grade": grade, "arrive_min": arrive,
            "ride_sum": ride, "has_bus": bus}


CASES = []


def case(name, expect, rows):
    CASES.append((name, expect, rows))


# ① 24:50 출발인데 도착이 00:05(=5분)로 남았다 — 운행일 축으로 안 올린 모양이다
case("자정 넘김 붕괴", {"INV-MIDNIGHT"},
     [{"probe": P("a", 0, "F", "weekday", 1490), "result": R(arrive=5)}])

# ② 늦게 떠났는데 더 일찍 도착한다
case("단조성 위반", {"INV-MONO"},
     [{"probe": P("a", 0, "F", "weekday", 600), "result": R(arrive=700)},
      {"probe": P("b", 0, "F", "weekday", 630), "result": R(arrive=690)}])

# ③ 정방향 승차 8분 / 역방향 승차 47분 — 방향·역 순서가 뒤집힌 모양이다
case("왕복 비대칭", {"INV-SYM"},
     [{"probe": P("a", 0, "F", "weekday", 600), "result": R(arrive=608, ride=8)},
      {"probe": P("b", 0, "R", "weekday", 600), "result": R(arrive=647, ride=47)}])

# ③' 같은 비대칭이라도 버스면 울지 않아야 한다 — 왕복이 같은 길이 아니다
case("버스 왕복은 제외", set(),
     [{"probe": P("a", 0, "F", "weekday", 600), "result": R(arrive=608, ride=8, bus=True)},
      {"probe": P("b", 0, "R", "weekday", 600), "result": R(arrive=647, ride=47, bus=True)}])

# ④ 성립/불가가 네 번 뒤집힌다 (정상은 두 번)
case("경계 불안정", {"INV-FLIP"},
     [{"probe": P(f"a{i}", 0, "F", "weekday", 600 + i * 30),
       "result": R(v, arrive=620 + i * 30)}
      for i, v in enumerate(["infeasible", "feasible", "infeasible",
                             "feasible", "infeasible", "feasible"])])

# ⑤ 도착 시각을 내놓으면서 근거가 없다고 한다
case("등급·판정 모순", {"INV-GRADE"},
     [{"probe": P("a", 0, "F", "weekday", 600), "result": R(arrive=610, grade="근거없음")}])

# ⑤' '성립 + 도착 미상' 은 의도된 상태라 울지 않아야 한다 (R-ORIG-01·BUS-11)
case("성립+도착미상은 제외", set(),
     [{"probe": P("a", 0, "F", "weekday", 600), "result": R(arrive=None, grade="근거없음")}])

# ⑥ 어떤 입력이든 예외 대신 판정이 나와야 한다
case("판정 중 예외", {"INV-CRASH"},
     [{"probe": P("a", 0, "F", "weekday", 600), "error": "SystemExit: 모르는 수단이다"}])

# ⑦ 한 역에만 판단불가가 몰린다
case("판단불가 편중", {"INV-UNKNOWN"},
     [{"probe": P(f"a{i}", 0, "F", "weekday", 600 + i * 30,
                  [{"line": "02호선", "from": "신답", "to": "B"}]),
       "result": R("unknown", None)} for i in range(6)] +
     [{"probe": P(f"b{i}", 1, "F", "weekday", 600 + i * 30,
                  [{"line": "02호선", "from": "강변", "to": "잠실"}]),
       "result": R(arrive=620 + i * 30)} for i in range(6)])

# ⑨ v0.8 — 최악 도착이 예정보다 이르다 · @ 가 버퍼보다 작다
case("이중 계산 붕괴", {"INV-WORST"},
     [{"probe": P("a", 0, "F", "weekday", 600),
       "result": dict(R(arrive=620), arrive_worst_min=615, margin_min=5, buffer_min=10,
                      out={"verdict": "feasible", "arrive_min": 620})}])

# ⑩ v0.8 — 밖 판이 내부와 어긋난다: 불가인데 코드 없음 · 성립인데 여유 음수 · 둘 밖의 값
case("밖 판 어긋남", {"INV-OUT"},
     [{"probe": P("a", 0, "F", "weekday", 600),
       "result": dict(R("infeasible"), out={"verdict": "infeasible"})},
      {"probe": P("b", 0, "F", "weekday", 630),
       "result": dict(R(arrive=650), out={"verdict": "feasible", "arrive_min": 650, "slack_min": -2})},
      {"probe": P("c", 0, "F", "weekday", 660),
       "result": dict(R("unknown"), out={"verdict": "unknown", "code": "no_data"})}])

# ⑪ v0.8 정상 — 내부 unknown → 밖 불가 no_data · 내부 성립 + 예정 없음 → 밖 불가 no_data 는 정상이다
case("밖 판 정상(오탐 확인)", set(),
     [{"probe": P(f"a{i}", 0, "F", "weekday", 600 + i * 30),
       "result": dict(R(arrive=620 + i * 30, ride=20), arrive_worst_min=625 + i * 30, margin_min=15, buffer_min=10,
                      out={"verdict": "feasible", "arrive_min": 620 + i * 30, "margin_min": 15})} for i in range(6)] +
     [{"probe": P("u", 1, "F", "weekday", 600), "result": dict(R("unknown"), out={"verdict": "infeasible", "code": "no_data"})},
      {"probe": P("v", 1, "F", "weekday", 630), "result": dict(R(arrive=None), out={"verdict": "infeasible", "code": "no_data"})}])

# ⑧ 정상 데이터 — 하나도 울면 안 된다
case("정상(오탐 확인)", set(),
     [{"probe": P(f"n{i}", 0, "F", "weekday", 600 + i * 30),
       "result": R(arrive=620 + i * 30, ride=20)} for i in range(8)] +
     [{"probe": P(f"m{i}", 0, "R", "weekday", 600 + i * 30),
       "result": R(arrive=622 + i * 30, ride=22)} for i in range(8)])


def main():
    bad = 0
    for name, expect, rows in CASES:
        got = {f["rule"] for f in check_invariants(rows, ARGS)}
        ok = got == expect
        bad += not ok
        mark = "OK  " if ok else "FAIL"
        print(f"{mark} {name:<22} 기대 {sorted(expect) or '없음'} / 실제 {sorted(got) or '없음'}")
    print("─" * 60)
    print(f"{len(CASES)}건 중 {len(CASES) - bad}건 통과")
    return 1 if bad else 0


def test_selfcheck_invariants():          # 67: pytest 수집용 · 합성 자료만 — 실데이터 불필요
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
