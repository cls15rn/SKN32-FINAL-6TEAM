# -*- coding: utf-8 -*-
"""
budget_probe_v1.py — 42번 방(예산 시험 산출)

목적
----
시작화면 use case 에서 사용자를 선호 이동수단 라벨 셋(대중교통 / 택시 / 렌터카)으로 나눌 때,
서울 1도시 2박3일 대표 시나리오에서 이동비 총액이 "몇 배" 갈리는지 한 번 뽑아본다.

이 스크립트가 아닌 것
----------------------
- 판정기(mobility/engine)·규칙 파일(rules_v0.3.json)을 읽거나 고치지 않는다.
- 정밀 요금 계산기가 아니다. 배수가 뚜렷이 갈리는지만 보고, 갈리지 않으면 그 자체가 결론이다.
- 경로·좌표를 저장하지 않는다(팀 원칙). 구간은 이름과 대표 거리/시간(km, 분)만 가진다.

구간 거리·시간의 출처
----------------------
이 세션(클라우드)은 기기 셸이 없고(27번 규칙 22 참고), GraphHopper 로컬 서버를 직접 부를 수 없다
(18번 방 §0: 클라우드 egress 가 github.com·repo1.maven.org 를 막는다).
그래서 이 스크립트는 18번(도로망 그래프)·21번(자동차·택시 판정) 방에서 **실제 GraphHopper 엔진 +
TOPIS 속도 프로파일로 이미 검증된 표본 구간**(서울 시내 인기 관광/환승 거점 간)을 그대로 재사용한다.
새로 좌표를 잡거나 라우터를 부르지 않았다 — 42번 방 취지("판정기·규칙은 안 만진다")에 맞춘 선택이다.

각 구간은 예정값(엔진 산출)과 상한(같은 시간대 네이버 대중교통/자동차 예측 대조값, 18번 §4②)을
함께 들고 있다 — 9/22 판정 계약 변경(등급 대신 "예정값 + 상한")을 따른다.

실행
----
    python budget_probe_v1.py            # 사람이 읽는 표 출력
    python budget_probe_v1.py --json out.json   # 계산 결과를 JSON 으로도 저장
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from typing import Optional


# ===========================================================================
# 0. 출처 — 값 + 출처 + 확인 시각만 남긴다 (원문 캡처·표는 저장하지 않는다, 29번 방침과 동일)
# ===========================================================================

SOURCES = {
    "subway_fare": {
        "note": "지하철 기본요금(교통카드) 1,550원, 2025-06-28 시행",
        "source": "서울신문 2025-04-29 https://www.seoul.co.kr/news/society/2025/04/29/20250429009009",
        "checked_at": "2026-09-22",
    },
    "bus_fare": {
        "note": "서울 시내버스 기본요금(교통카드) 1,500원, 2023-08-12 시행 — 그 뒤 추가 인상 공지 확인 안 됨",
        "source": "경향신문 2023-08-10 https://www.khan.co.kr/national/national-general/article/202308100600021",
        "checked_at": "2026-09-22",
    },
    "distance_fare_band": {
        "note": "수도권 통합요금제 거리비례: 10km까지 기본, 10~50km 구간 5km마다 100원 추가, "
        "50km 초과 8km마다 100원 추가 — 지하철·버스 공통 적용으로 근사",
        "source": "티머니 대중교통 이용안내 https://www.t-money.co.kr/ncs/pct/ugd/ReadLrgMedmTrusGd.dev",
        "checked_at": "2026-09-22",
    },
    "airport_bus_6015": {
        "note": "6015번(인천공항 T1/T2 ↔ 명동) 성인 17,000원. 이비스명동 기준 T1 약 67분·T2 약 87분",
        "source": "공항리무진 공식 안내 https://airportlimousine.co.kr/sub/sub01.php?cat_no=21",
        "checked_at": "2026-09-22",
    },
    "taxi_fare_formula": {
        "note": "중형 기본 4,800원/1.6km · 거리 100원/131m · 시간 100원/30초(15.72km/h 미만) · "
        "심야 22~23·02~04시 20%, 23~02시 40% · 시계외 20% · 복합상한 60% (호출료·정차 미포함)",
        "source": "프로젝트 15번 방 claude/인수인계/15_데이터_택시요금_인계_20260919.md "
        "(원 출처: 서울시 교통실 「서울 택시요금」 고시)",
        "checked_at": "2026-09-19 (15번 방 확인 시각 그대로 인용)",
    },
    "taxi_fare_bunsan_ratio": {
        "note": "21번 방 실측: 테헤란로 상행 구간에서 '전 구간 거리+저속시간' 단순 근사 9,600원 vs "
        "edge별 병산 7,700원 — 병산이 단순 근사의 약 0.80배. 이 스크립트는 단순 근사를 상한으로, "
        "0.80배 한 값을 예정으로 쓴다(edge별 속도 데이터가 이 세션엔 없어 병산 자체는 재현 못 함)",
        "source": "프로젝트 21번 방 claude/인수인계/21_코드_자동차택시_인계_20260920.md §2⑤",
        "checked_at": "2026-09-20 (21번 방 확인 시각 그대로 인용)",
    },
    "airport_toll": {
        "note": "인천공항고속도로 인천공항영업소 승용 3,200원(공항 구간 상한값으로 사용, 18번 방이 "
        "정확한 통행 다리를 아직 못 정함)",
        "source": "프로젝트 15번 방 인용 hiway21.com 2023-10-01 기준",
        "checked_at": "2026-09-19 (15번 방 확인 시각 그대로 인용)",
    },
    "gasoline_price": {
        "note": "전국 휘발유 평균 1,859.1원/L",
        "source": "파이낸셜뉴스 2026-09-12 https://www.fnnews.com/news/202609120832262658 (한국석유공사 오피넷)",
        "checked_at": "2026-09-22",
    },
    "rental_daily_rate": {
        "note": "소형(경형~준중형) 1일 약 50,000원 · 중형 1일 약 95,000원 (카셰어링 단기 대여 기준, "
        "보험료 별도) — 소형~중형 범위의 하단/상단 대표값으로 사용",
        "source": "쏘카 요금 정리 블로그 대조 https://dailyatoz.com/쏘카-가격/ (쏘카 공식 요금표 페이지는 "
        "차종별 숫자를 이미지/JS 로만 제공해 텍스트 확인 불가 — 2차 출처)",
        "checked_at": "2026-09-22",
    },
    "fuel_efficiency": {
        "note": "소형 약 14 km/L · 중형 약 11 km/L (국내 가솔린 승용차 공인연비 참고치 — 팀 자체 가정, "
        "특정 차종 공시연비 대조는 안 함)",
        "source": "본 방 자체 가정 — 확정 근거 없음",
        "checked_at": "2026-09-22",
    },
    "seoul_leg_samples": {
        "note": "시내 4구간 거리·소요는 18번 방(claude/인수인계/18_전처리_도로망그래프_인계_20260919.md §4②) "
        "표본 10구간 중 4개(C01·C03·C05·C06)를 그대로 재사용. 도로 단위 MAPE 3.6%(±20% 안 100%, "
        "42표본)로 검증된 GraphHopper 실제 경로 값이며, 이 스크립트가 새로 좌표를 잡지 않았다.",
        "source": "18번 방 gh_sample10_result.csv, 네이버 '나중에 출발' 예측(토 14:00, 2026-10-17 기준값)",
        "checked_at": "2026-09-20 (18번 방 확인 시각 그대로 인용)",
    },
    "airport_leg_sample": {
        "note": "공항↔숙소 구간(인천공항 T1 ↔ 명동)은 18번 표본 C02 재사용. GraphHopper 값은 "
        "서울 상자 밖 간선에 서울 도시고속 평균속도를 잘못 적용하는 알려진 결함이 있어(§4② 각주) "
        "실제보다 느리게(46%) 나온다 — 그래서 이 구간만 네이버 값을 예정으로, GraphHopper 값을 "
        "상한으로 뒤집어 쓴다(엔진 결함이 자연스러운 상한 역할을 한다).",
        "source": "18번 방 §4② C02행",
        "checked_at": "2026-09-20 (18번 방 확인 시각 그대로 인용)",
    },
}


# ===========================================================================
# 1. 대표 시나리오 — 서울 1도시 2박3일, 명동 숙박
# ===========================================================================
#
#   Day1(도착, 평일 저녁 가정) 인천공항 T1 → 명동 숙소
#   Day2(관광, 토요일 낮 가정) 숙소 인근 ↔ 강남역 / DDP ↔ 남산타워 / 여의도 IFC ↔ 코엑스 /
#                              홍대입구 ↔ 롯데월드타워   (4구간, 18번 표본 재사용)
#   Day3(출국, 주말 낮 가정)   명동 숙소 → 인천공항 T1
#
# dist_km    : 도로 라우팅 거리(GraphHopper, km) — 택시 요금·유류비 계산에 쓴다.
# duration_est_min / duration_cap_min : 대중교통·택시 시간 계산에 참고(심야 할증 판단용).
# transit_note : 대중교통 항목에서 이 구간을 무엇으로 taxes 매기는지(공항버스 vs 지하철·버스 거리비례).


@dataclass
class Leg:
    name: str
    dist_km: float
    duration_est_min: float
    duration_cap_min: float
    is_airport: bool = False
    hour_bucket: str = "주간"  # 심야(22~04시) 여부만 구분한다
    taxi_engine_won: Optional[int] = None  # --legs 로 엔진 실측을 넣으면 채워진다(병산·통행료 포함)


def load_measured_legs(path: str) -> list[Leg]:
    """budget_probe_measure_v1.py 산출(JSON)로 구간을 바꾼다 — 거리·소요·택시요금은 엔진 값."""
    d = json.loads(open(path, encoding="utf-8").read())
    out = []
    for r in d["legs"]:
        est = r["topis_min"]
        cap = max(x for x in (r["topis_min"], r.get("gh_min") or 0))
        out.append(Leg(r["name"], dist_km=r["dist_km"], duration_est_min=est, duration_cap_min=cap,
                       is_airport=r["is_airport"], taxi_engine_won=r["taxi_fare_won"]))
    return out


LEGS: list[Leg] = [
    Leg("Day1 인천공항T1→명동(숙소)", dist_km=54.8, duration_est_min=70.0, duration_cap_min=102.1, is_airport=True),
    Leg("Day2 숙소 인근↔강남역(C01)", dist_km=10.5, duration_est_min=29.0, duration_cap_min=33.0),
    Leg("Day2 DDP↔남산서울타워(C05)", dist_km=4.8, duration_est_min=11.4, duration_cap_min=14.0),
    Leg("Day2 여의도IFC↔코엑스(C06)", dist_km=17.9, duration_est_min=31.6, duration_cap_min=52.0),
    Leg("Day2 홍대입구↔롯데월드타워(C03)", dist_km=23.6, duration_est_min=43.3, duration_cap_min=70.0),
    Leg("Day3 명동(숙소)→인천공항T1", dist_km=54.8, duration_est_min=82.0, duration_cap_min=98.8, is_airport=True),
]


# ===========================================================================
# 2. 대중교통 (지하철·버스 거리비례 + 공항 구간 공항버스)
# ===========================================================================

SUBWAY_BASE_FARE = 1550
AIRPORT_BUS_FARE = 17000


def distance_band_surcharge(dist_km: float) -> int:
    """수도권 통합요금제 거리비례 추가금 (기본요금 위에 얹는 원 단위)."""
    if dist_km <= 10:
        return 0
    if dist_km <= 50:
        excess = dist_km - 10
        bands = math.ceil(excess / 5)
        return bands * 100
    excess = dist_km - 50
    bands = math.ceil(excess / 8)
    return 40 * 100 + bands * 100  # 10~50km 구간 8칸(40) 다 채운 값+ 초과분


def transit_cost(leg: Leg) -> dict:
    if leg.is_airport:
        fare = AIRPORT_BUS_FARE
        basis = "공항버스(6015) 고정요금"
    else:
        fare = SUBWAY_BASE_FARE + distance_band_surcharge(leg.dist_km)
        basis = f"지하철 기본 {SUBWAY_BASE_FARE}원 + 거리비례 {distance_band_surcharge(leg.dist_km)}원"
    # 대중교통은 공시 요금이라 예정=상한 (혼잡·경로선택에 따른 변동은 이 분석 범위 밖)
    return {"est": fare, "cap": fare, "basis": basis}


# ===========================================================================
# 3. 택시 (15번 산식 — 단순 거리요금을 상한, 21번 병산비율 0.80을 곱한 값을 예정)
# ===========================================================================

TAXI_BASE_FARE = 4800
TAXI_BASE_DIST_KM = 1.6
TAXI_DIST_UNIT_M = 131
TAXI_DIST_UNIT_FARE = 100
TAXI_NIGHT_SURCHARGE = {"20~22_04": 0.20, "23_02": 0.40}  # 이 시나리오엔 심야 구간 없음(§0 가정)
TAXI_OUT_OF_CITY_SURCHARGE = 0.0  # 시계외 미판정(21번 §8 그대로) — 전 구간 서울 안으로 가정
TAXI_BUNSAN_RATIO = 0.80  # 21번 §2⑤ 실측 비율
AIRPORT_TOLL_WON = 3200


def taxi_cost(leg: Leg) -> dict:
    excess_km = max(0.0, leg.dist_km - TAXI_BASE_DIST_KM)
    excess_m = excess_km * 1000
    units = math.ceil(excess_m / TAXI_DIST_UNIT_M)
    metered_simple = TAXI_BASE_FARE + units * TAXI_DIST_UNIT_FARE  # 상한(단순 거리요금, 저속시간 미근사)
    toll = AIRPORT_TOLL_WON if leg.is_airport else 0

    cap = round(metered_simple / 100) * 100 + toll
    if leg.taxi_engine_won is not None:
        est = leg.taxi_engine_won
        return {"est": est, "cap": max(cap, est),
                "basis": f"예정=엔진 병산(car.py taxi_fare) {est}원 · 상한=단순 거리요금 {cap}원"}
    est = round((metered_simple * TAXI_BUNSAN_RATIO) / 100) * 100 + toll
    basis = (
        f"기본 {TAXI_BASE_FARE}원 + 거리 {units}칸×{TAXI_DIST_UNIT_FARE}원"
        + (f" + 통행료 {toll}원" if toll else "")
        + f" (예정=병산비율 {TAXI_BUNSAN_RATIO} 적용)"
    )
    return {"est": est, "cap": cap, "basis": basis}


# ===========================================================================
# 4. 렌터카 (대여료 3일 + 유류비 + 통행료·주차 — 소형=예정, 중형=상한)
# ===========================================================================

RENTAL_DAYS = 3  # 2박3일 — 첫날 오후 픽업 ~ 마지막날 오전 반납이라도 3영업일로 과금된다고 가정
RENTAL_DAILY_SMALL = 50000
RENTAL_DAILY_MID = 95000
FUEL_PRICE_WON_PER_L = 1859.1
FUEL_EFF_SMALL_KM_PER_L = 14.0
FUEL_EFF_MID_KM_PER_L = 11.0
PARKING_TOTAL_WON = 15000  # Day2 관광 4곳 중 유료 주차 2회 가정(1회 약 7,500원) — 확정 근거 없음, 자체 가정


def rental_car_total(legs: list[Leg]) -> dict:
    total_km = sum(leg.dist_km for leg in legs)
    airport_tolls = sum(AIRPORT_TOLL_WON for leg in legs if leg.is_airport)

    rental_small = RENTAL_DAILY_SMALL * RENTAL_DAYS
    rental_mid = RENTAL_DAILY_MID * RENTAL_DAYS

    fuel_small = total_km / FUEL_EFF_SMALL_KM_PER_L * FUEL_PRICE_WON_PER_L
    fuel_mid = total_km / FUEL_EFF_MID_KM_PER_L * FUEL_PRICE_WON_PER_L

    est = round(rental_small + fuel_small + airport_tolls + PARKING_TOTAL_WON)
    cap = round(rental_mid + fuel_mid + airport_tolls + PARKING_TOTAL_WON)

    return {
        "est": est,
        "cap": cap,
        "total_km": round(total_km, 1),
        "basis_est": f"소형 {RENTAL_DAYS}일×{RENTAL_DAILY_SMALL}원 + 유류비 {round(fuel_small):,}원"
        f"(연비 {FUEL_EFF_SMALL_KM_PER_L}km/L) + 통행료 {airport_tolls}원 + 주차 {PARKING_TOTAL_WON}원",
        "basis_cap": f"중형 {RENTAL_DAYS}일×{RENTAL_DAILY_MID}원 + 유류비 {round(fuel_mid):,}원"
        f"(연비 {FUEL_EFF_MID_KM_PER_L}km/L) + 통행료 {airport_tolls}원 + 주차 {PARKING_TOTAL_WON}원",
    }


# ===========================================================================
# 5. 집계 + 출력
# ===========================================================================


def build_result() -> dict:
    per_leg = []
    transit_est_sum = transit_cap_sum = 0
    taxi_est_sum = taxi_cap_sum = 0

    for leg in LEGS:
        t = transit_cost(leg)
        x = taxi_cost(leg)
        per_leg.append(
            {
                "leg": leg.name,
                "dist_km": leg.dist_km,
                "transit": t,
                "taxi": x,
            }
        )
        transit_est_sum += t["est"]
        transit_cap_sum += t["cap"]
        taxi_est_sum += x["est"]
        taxi_cap_sum += x["cap"]

    rental = rental_car_total(LEGS)

    totals = {
        "대중교통": {"est": transit_est_sum, "cap": transit_cap_sum},
        "택시": {"est": taxi_est_sum, "cap": taxi_cap_sum},
        "렌터카": {"est": rental["est"], "cap": rental["cap"], "total_km": rental["total_km"]},
    }

    base_est = totals["대중교통"]["est"]
    base_cap = totals["대중교통"]["cap"]
    multiples = {
        mode: {
            "est_x": round(totals[mode]["est"] / base_est, 2),
            "cap_x": round(totals[mode]["cap"] / base_cap, 2),
        }
        for mode in ("대중교통", "택시", "렌터카")
    }

    return {
        "per_leg": per_leg,
        "rental_detail": rental,
        "totals": totals,
        "multiples_vs_transit": multiples,
        "sources": SOURCES,
    }


def print_report(result: dict) -> None:
    print("=== 42번 방 예산 시험 산출 — 구간별 ===")
    for row in result["per_leg"]:
        print(f"- {row['leg']} ({row['dist_km']}km)")
        print(f"    대중교통: {row['transit']['est']:,}원  [{row['transit']['basis']}]")
        print(
            f"    택시    : 예정 {row['taxi']['est']:,}원 / 상한 {row['taxi']['cap']:,}원  "
            f"[{row['taxi']['basis']}]"
        )

    print("\n=== 렌터카 산정 근거 ===")
    rd = result["rental_detail"]
    print(f"- 총 주행거리: {rd['total_km']}km")
    print(f"- 예정(소형): {rd['basis_est']}")
    print(f"- 상한(중형): {rd['basis_cap']}")

    print("\n=== 왕복 6구간 합계 (2박3일, 1인 기준) ===")
    for mode, v in result["totals"].items():
        print(f"- {mode}: 예정 {v['est']:,}원 / 상한 {v['cap']:,}원")

    print("\n=== 대중교통 대비 배수 ===")
    for mode, m in result["multiples_vs_transit"].items():
        print(f"- {mode}: 예정 기준 {m['est_x']}배 / 상한 기준 {m['cap_x']}배")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", default=None, help="계산 결과를 JSON으로 저장할 경로")
    parser.add_argument("--legs", default=None, help="budget_probe_measure_v1.py 산출 JSON — 주면 18번 표본 대신 엔진 실측 구간을 쓴다")
    args = parser.parse_args()

    global LEGS
    if args.legs:
        LEGS = load_measured_legs(args.legs)
        print(f"[구간] 엔진 실측 {args.legs} — {len(LEGS)}구간\n")

    result = build_result()
    print_report(result)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[JSON 저장] {args.json_path}")


if __name__ == "__main__":
    main()
