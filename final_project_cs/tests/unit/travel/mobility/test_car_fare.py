# -*- coding: utf-8 -*-
"""택시 요금 산식 · 요일형 — modules/mobility/car.py 단위 검사 (21번 방 · 2026-09-20).

  python final_project_cs/tests/unit/travel/mobility/test_car_fare.py

① 규칙 파일 taxi.fare.검산_예시 8건을 car.taxi_fare 로 다시 낸다 — mobility_scripts/rules_check.py 의 사본과 같은 답이어야 한다
   (두 구현이 갈리면 여기서 난다).
② 심야 구간 경계 — 21:59 / 22:00 / 23:00 / 01:59 / 02:00 / 03:59 / 04:00.  ③ 병산 — 저속 m 를 거리요금에서 뺀다.
④ 요일형 — config holidays(A10) 가 달력을 이긴다(2026-09-26 토요일이지만 추석 → 휴일), 일요일 = 휴일, 제헌절은 A10 대로(18번 달력과 어긋남 — 기록).
⑤ 공항 상자 — T1·T2 안, 서울역 밖.
"""
import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "final_project_cs"))
from app.modules.travel_ops.mobility.engine.car import (taxi_fare, taxi_rate,  # noqa: E402
                                                        daytype_kr, CarService)

R = json.loads((REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "rules_v0.3.json").read_text(encoding="utf-8"))
H = set(json.loads((REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "holidays_2026_2027.json").read_text(encoding="utf-8"))["holidays"])
F = R["taxi"]["fare"]
ok = fail = 0


def chk(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


print("[1] 규칙 검산_예시 8건")
for c in F["검산_예시"]["cases"]:
    got = taxi_fare(F, c["type"], c["dist_m"], c["slow_s"], c["time"], c.get("out_of_city", False))
    chk(f"{c['name']} = {c['expect_won']:,}", got == c["expect_won"], f"계산 {got:,}")

print("[2] 심야 구간 경계 (중형)")
for t, want in (("21:59", 0.0), ("22:00", 0.2), ("22:59", 0.2), ("23:00", 0.4), ("01:59", 0.4),
                ("02:00", 0.2), ("03:59", 0.2), ("04:00", 0.0), ("12:00", 0.0)):
    chk(f"{t} → {want:g}", taxi_rate(F, "중형", t) == want, str(taxi_rate(F, "중형", t)))
chk("대형_모범 23:30 → 0.2 (2단계 없음)", taxi_rate(F, "대형_모범", "23:30") == 0.2)

print("[3] 병산 — 저속 구간은 시간요금만")
# 5.0 km 중 1.0 km 가 저속(300초): 거리요금은 4.0 km 에 대해서만
a = taxi_fare(F, "중형", 5000, 0, "14:00")                # 7,400
b = taxi_fare(F, "중형", 5000 - 1000, 300, "14:00")       # 4,800 + ceil(2400/131)=19×100 + ceil(300/30)=10×100 = 7,700
chk("전 구간 주행 5.0 km = 7,400", a == 7400, str(a))
chk("4.0 km 주행 + 저속 300초 = 7,700", b == 7700, str(b))
chk("저속 300초를 거리요금과 겹쳐 세면(구 근사) 8,400 — 이 값이 나오면 안 된다",
    taxi_fare(F, "중형", 5000, 300, "14:00") == 8400 and b != 8400)

print("[4] 요일형 — config holidays 정본")
chk("2026-09-26 토요일 · 추석 → 휴일", daytype_kr(dt.date(2026, 9, 26), H) == "휴일")
chk("2026-10-10 토요일 → 토요일", daytype_kr(dt.date(2026, 10, 10), H) == "토요일")
chk("2026-09-27 일요일 → 휴일", daytype_kr(dt.date(2026, 9, 27), H) == "휴일")
chk("2026-09-22 화요일 → 평일", daytype_kr(dt.date(2026, 9, 22), H) == "평일")
# ★ 2026-07-17 제헌절 — config holidays(KASI 특일, A10, 확정)는 is_holiday=True 로 준다. 18번 달력은 「패키지 오류」로 보고
#   평일로 뺐다. 두 소스가 어긋난다(21번 §확인 안 한 것). 코드는 저장소 3차 변경점 §1 대로 **A10 을 따른다** — 이 줄은
#   「어느 쪽을 따르는가」를 잠그는 것이지 「제헌절이 공휴일이다」를 말하는 것이 아니다. A10 이 고쳐지면 기대값도 바뀐다.
chk("2026-07-17 제헌절 → config holidays(A10) 가 정하는 대로 (지금 값: 휴일) — 18번 달력(평일)과 어긋남",
    daytype_kr(dt.date(2026, 7, 17), H) == ("휴일" if "2026-07-17" in H else "평일"))
chk("A10 과 18번 달력이 제헌절에서 어긋나 있다는 사실 자체를 기록한다", "2026-07-17" in H)

print("[5] 공항 상자")
svc = CarService.__new__(CarService)
svc.C, svc.F, svc.R = R["car"], F, R
chk("인천공항 T1 안", svc.in_airport_box((126.4505, 37.4602)))
chk("인천공항 T2 안", svc.in_airport_box((126.4340, 37.4667)))
chk("서울역 밖", not svc.in_airport_box((126.9707, 37.5547)))
chk("김포공항 밖", not svc.in_airport_box((126.8010, 37.5586)))

print(f"\n통과 {ok} · 실패 {fail}")
if __name__ == "__main__":
    sys.exit(1 if fail else 0)


def test_every_check_passes():
    """pytest 입구 (2026-09-28) — 위 검사는 이 파일을 불러올 때 이미 돈다. 실패가 0 이어야 한다.

    자세한 줄별 결과는 `python final_project_cs/tests/unit/travel/mobility/test_car_fare.py` 로 본다.
    """
    assert fail == 0, f"실패 {fail}"
