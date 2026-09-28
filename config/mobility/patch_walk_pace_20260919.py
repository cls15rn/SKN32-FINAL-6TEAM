# -*- coding: utf-8 -*-
"""
16번 방(보행속도 근거) 반영 패치 — rules_v0.3.json 의 walk_pace·measured_baseline.대조·changelog 를 보강한다.
- 판정 무변경 · rules_version 유지(v0.3.1)
- 멱등: 이미 반영돼 있으면 아무것도 바꾸지 않는다
- 다른 방(15번 택시 등)이 추가한 절은 건드리지 않는다
실행: 레포 루트에서  python patch_walk_pace_20260919.py
"""
import json, sys, shutil, datetime
from collections import OrderedDict
from pathlib import Path

P = Path(__file__).resolve().parent / "rules_v0.3.json"   # 이 스크립트와 같은 폴더(config/mobility)의 규칙 파일
if not P.exists():
    sys.exit(f"없음: {P}")

T = "2026-09-19 20:10 KST"
S1 = ("경찰청 「교통신호기 설치·관리 매뉴얼」 2020-11-30 부분개정 p.61 (police.go.kr 공지 2020-12-01) · "
      "후속판 「2023 교통신호기 설치·운영 업무편람」(2023.12, police.go.kr 공지 2024-05-21) 제2편 제2장 p.112~113에서 "
      "일반 1.0 / 교통약자 0.7 유지 확인")
S2 = ("한음 외, 「노인보호구역 보행자녹색시간 산정을 위한 보행속도 기준 개선」, 한국ITS학회논문지 19(4), 2020 "
      "(KCI ART002621242) — 횡단보도 실측 n=4,857")
S3 = ("윤나미 외, 「우리나라 연령별 보행분석 비교연구」, 대한물리치료학회지 22(2), 2010 "
      "(KCI ART001441479) — 실내 10m 자율보행, 각 군 30명")
MARK = "2026-09-19 (16번 방"

d = json.loads(P.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)

vals = d["walk_pace"]["factors_deferred"]["값"]
already = "mobility_aid" in vals and any(c.startswith(MARK) for c in d["changelog_v0.3"])
if already:
    print("이미 반영됨 — 변경 없음:", list(vals))
    sys.exit(0)

bak = P.with_suffix(f".json.bak_{datetime.datetime.now():%Y%m%d_%H%M%S}")
shutil.copy2(P, bak)

mb = d["measured_baseline"]["kakao_walk_speed_mps"]
mb["대조"] = OrderedDict([
    ("경찰청 매뉴얼 일반 보행신호 설계속도", 1.0),
    ("경찰청 매뉴얼 교통약자(어린이·노인보호구역) 설계속도 — 2020-11-30 개정(2012년판 0.8), 2023 업무편람 유지", 0.7),
    ("경찰청 매뉴얼 녹색점멸 설계속도 — 일반 / 교통약자(2023 편람)", "1.3 / 1.0"),
    ("횡단보도 실측 일반인(65세 미만) 평균 / 하위15% (한 외 2020)", "1.28 / 1.00"),
    ("횡단보도 실측 노인(65세 이상) 평균 / 하위15% (한 외 2020)", "1.12 / 0.85"),
    ("횡단보도 실측 보행보조장치 사용자 평균 / 하위15% (한 외 2020)", "0.88 / 0.70"),
    ("실내 자율보행 소아12세 / 성인24세 / 노인71세 (윤 외 2010)", "1.14 / 1.27 / 1.03"),
])
mb["대조_출처"] = [S1, S2, S3]
mb["대조_확인시각"] = T
mb["해석"] = ("카카오 1.04는 경찰청 일반 설계속도(1.0)와 횡단보도 일반인 하위15%(1.00)에 해당하고, 성인 평균(1.27~1.28)·"
             "점멸 설계속도(1.3)보다 약 20% 느리다. 건강한 노인 평균(1.12 횡단보도·1.03 실내)도 이 기준선 이상이다. "
             "즉 이미 보수적이지만 교통약자·보조기구 기준(0.7)은 아니다. 팀 의견 '노약자까지 고려한 값'은 아니고, "
             "'보통 성인보다 느린 값'까지가 맞다. (2026-09-19 원문 확인으로 출처명 정정: 이전 '서울시 기준'은 경찰청 매뉴얼이었다)")

fd = d["walk_pace"]["factors_deferred"]
v = OrderedDict()
v["infant"] = OrderedDict([("value", 1.5), ("grade", "추정"), ("assumed_speed_mps", 0.7),
    ("근거", "유아·유아차 동반 실측은 국내 소스에서 찾지 못했다(2026-09-19). 경찰청 매뉴얼 어린이보호구역 설계속도 0.7 m/s를 "
            "대리값으로 쓴다(1.04÷0.7=1.49). 신호 설계용 보수값이라 안전 쪽이며 계단 회피·엘리베이터 우회를 함께 흡수한다. 합성 여행으로 조정."),
    ("출처", [S1]), ("확인시각", T)])
v["mobility_aid"] = OrderedDict([("value", 1.5), ("grade", "추정"), ("assumed_speed_mps", 0.7),
    ("근거", "지팡이·휠체어·보행보조기 동반. 독립 소스 둘이 같은 값: 경찰청 교통약자 설계속도 0.7 · 횡단보도 실측 보행보조장치 사용자 "
            "하위15% 0.70(평균 0.88, n=199). 1.04÷0.70=1.49. 2026-09-19 신설 — 이전에는 elderly 1.5가 이 몫까지 맡고 있었다."),
    ("출처", [S1, S2]), ("확인시각", T),
    ("party_어휘", "current_state.party 에 mobility_aid 값이 있는지 코어 규약 확인 필요(① 방)")])
v["elderly"] = OrderedDict([("value", 1.25), ("grade", "추정"), ("assumed_speed_mps", 0.83),
    ("근거", "2026-09-19 1.5 → 1.25. 횡단보도 실측(한 외 2020) 노인 평균 1.12는 기준선 1.04보다 빠르고 실내 자율보행(윤 외 2010) "
            "노인 평균 1.03도 기준선과 같다 — 노인이라는 이유만으로 1.5는 과대. 노인 하위15% 0.85 → 1.04÷0.85=1.22를 취하되, "
            "논문 스스로 횡단보도는 빠른 이동 전제라 일반 상황은 더 느릴 수 있다고 적어 1.25로 올림. 보조기구 동반이면 mobility_aid 1.5가 "
            "max 로 잡는다. 성별 차는 0.02~0.04 m/s(계수 차 0.02~0.04)로 단독 계수로 쓰지 않는다."),
    ("출처", [S2, S3]), ("확인시각", T)])
v["luggage"] = OrderedDict([("value", 1.2), ("grade", "추정"), ("assumed_speed_mps", 0.87),
    ("근거", "캐리어 동반 0.87 m/s 가정(1.04÷0.87=1.20). 인바운드는 공항↔숙소 구간에서 거의 항상 해당. 근거 약함(2026-09-19 확인): "
            "횡단보도 실측(한 외 2020)에서 손짐 보유자는 짐 없음보다 오히려 빠르고(1.18 vs 1.15, 유의차 없음) 캐리어는 측정 대상이 아니었다. "
            "실측 없음 — 합성 여행으로 조정."),
    ("출처", [S2 + " (반증에 가까움)"]), ("확인시각", T)])
v["fatigue_high"] = OrderedDict([("value", 1.3), ("grade", "근거없음"), ("assumed_speed_mps", 0.8),
    ("근거", "여행 후반·연속 일정으로 피로도 높음 표시 시 0.8 m/s 가정(1.04÷0.8=1.30). 2026-09-19 확인: 어느 소스에도 피로도별 "
            "보행속도 없음. 값은 두되 등급을 근거없음으로 내린다."),
    ("확인시각", T)])
fd["값"] = v
fd["근거_보존"] = ("값은 2026-09-09 실측 기준선 1.04 m/s 대비 비율(1.04÷가정속도)로 계산해 뒀다. 확장 때 enabled 만 true 로 바꾸면 된다. "
                 "근거 원문·수치표는 claude/인수인계/16_데이터_보행속도근거_인계_20260919.md.")

ww = d["walk_pace"]["walk_warn_min"]
ww["적용조건"] = "party 에 infant / elderly / mobility_aid / luggage / fatigue_high 중 하나라도 있을 때"
ww["근거"] = ww["근거"].replace("유아 계수 1.5 기준으로", "유아·보조기구 계수 1.5 기준으로")

d["changelog_v0.3"].append(
    "2026-09-19 (16번 방, 판정 무변경·버전 유지): walk_pace.factors_deferred 근거 보강 — elderly 1.5→1.25, mobility_aid 1.5 신설, "
    "fatigue_high 등급 근거없음, luggage 근거약함 명기. measured_baseline.대조 출처명 정정(서울시→경찰청 매뉴얼 2020-11-30 개정, "
    "교통약자 0.8→0.7; 2023 업무편람에서 유지 확인). 원문: 경찰청 매뉴얼 p.61 · 2023 편람 p.112 · 한 외 2020 · 윤 외 2010. "
    "※ 원격 커밋이 두 번 덮여 노트북에서 패치 스크립트로 반영.")

P.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("반영 완료. 백업:", bak.name)
print("factors_deferred 키:", list(d["walk_pace"]["factors_deferred"]["값"]), "| taxi 절:", "taxi" in d)
