# mobility_scripts/rules_check.py — 규칙 표(rules_v0.1.json) 검사 + 계산 예시
# 실행: 저장소 루트에서  python mobility_scripts/collect/rules_check.py
# 하는 일 ① 규칙 파일이 열리고 필수 키·grade가 다 있는지 ② 이중 계산이 없는지(환승 고정값 = 0)
#        ③ 대표 케이스 3개의 여유 판정을 손으로 따라갈 수 있게 풀어서 출력
#        ④ (2026-09-19) 택시 요금 산식 taxi.fare 의 항등식·시간대 덮임·검산 예시 — 문제가 있으면 종료 코드 1
# 이 스크립트는 판정기 본체가 아니다. 규칙값이 말이 되는지 눈으로 보는 용도다.
import json, sys
from pathlib import Path

def find_rules():
    """인자로 받거나, 상위 폴더를 훑어 final_project_cs/app/modules/travel_ops/mobility/engine/rules/rules_*.json 을 찾는다."""
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    here = Path(__file__).resolve()
    for parent in here.parents:
        hits = sorted((parent / "final_project_cs" / "app" / "modules"
                      / "travel_ops" / "mobility" / "engine" / "rules").glob("rules_*.json"))
        if hits:
            return hits[-1]
    raise SystemExit("final_project_cs/app/modules/travel_ops/mobility/engine/rules/rules_*.json 을 찾지 못했다. 경로를 인자로 주면 된다.")


RULES = find_rules()
R = json.loads(RULES.read_text(encoding="utf-8"))
print(f"규칙 {R['rules_version']} (개정일 {R['effective_date']}, source_id {R['source_id']})\n")

# ── ① 구조 검사 ───────────────────────────────────────────────────
problems = []
for key in ("walk_pace", "transfer", "buffer", "limits", "bus", "evidence_grade", "taxi"):
    if key not in R:
        problems.append(f"필수 절 없음: {key}")


def walk(node, path=""):
    """value 를 가진 규칙마다 grade 와 근거가 붙어 있는지."""
    if isinstance(node, dict):
        if "value" in node:
            if "grade" not in node:
                problems.append(f"grade 없음: {path}")
            elif node["grade"] not in ("확정", "추정", "근거없음"):
                problems.append(f"grade 값 이상: {path} = {node['grade']}")
            if "근거" not in node:
                problems.append(f"근거 없음: {path}")
        for k, v in node.items():
            walk(v, f"{path}.{k}" if path else k)


walk(R)
if R["transfer"]["fixed_allowance_min"]["value"] != 0:
    problems.append("환승 고정 여유가 0이 아니다 — 카카오 도보 시간과 이중 계산이 된다")
mb = R.get("measured_baseline", {}).get("kakao_walk_speed_mps")
if mb:
    print(f"실측 기준선: 카카오 보행 {mb['value']} m/s ({mb['value']*3.6:.1f} km/h) — 계수는 이 값 대비 비율\n")
mv = R.get("mvp_scope")
if mv:
    print(f"MVP 적용: {len(mv['적용'])}항목 · 미적용: {', '.join(mv['미적용'])}")
print(f"보행 보정 계수: {'ON' if R['walk_pace'].get('enabled', True) else 'OFF (MVP — 카카오 시간 그대로)'}\n")
print("① 구조 검사:", "통과" if not problems else "문제 " + str(len(problems)) + "건")
for p in problems:
    print("   -", p)

# ── ② 계산 ────────────────────────────────────────────────────────
WP = R["walk_pace"]


def deferred_factors():
    d = WP.get("factors_deferred")
    return (d or {}).get("값", WP.get("factors", {}))


def pace(party, force=False):
    """보정 계수 — 가장 느린 요인 하나만 쓴다(combine=max).
    MVP 에서는 walk_pace.enabled=false 라 1.0 을 돌려준다. force=True 면 켰을 때 값."""
    base = (WP.get("base") or WP.get("base_first_visit"))["value"]
    if not (WP.get("enabled", True) or force):
        return base
    fac = deferred_factors()
    f = [fac[k]["value"] for k in fac if party.get(k)]
    return round(max([base] + f), 3)


def judge(case):
    """카카오가 준 소요(ride/walk 분리) + 시간표에서 온 배차로 도착 추정과 판정."""
    p = pace(case["party"])
    p_ext = pace(case["party"], force=True)
    walk_adj = case["walk_min"] * p
    large = [s for s in case.get("transfer_stations", []) if s in R["transfer"]["large_station_addition_min"]["stations"]]
    large_add = len(large) * R["transfer"]["large_station_addition_min"]["value"]
    wf = R["transfer"].get("wayfinding_addition_min")
    way_add = case["transfers"] * wf["value"] if (wf and case.get("first_visit", True)) else 0
    hw = case.get("headway_min", 0)
    worst = case["party"].get("in_progress") or case.get("near_last_train")
    wait = hw if worst else hw / 2          # headway_full vs headway_half
    stage = "in_progress" if case["party"].get("in_progress") else "planning"
    buf = R["buffer"]["by_stage"][stage]["value"]
    total = case["ride_min"] + walk_adj + large_add + way_add + wait
    arrive = case["depart_min"] + total
    slack = case["arrive_by_min"] - (arrive + buf)

    lim_t = min([R["limits"]["transfers"]["default"]["value"]] +
                [R["limits"]["transfers"][k]["value"] for k in R["limits"]["transfers"]
                 if k != "default" and isinstance(R["limits"]["transfers"][k], dict) and case["party"].get(k)])
    lim_w = R["limits"]["walk_m"]["infant_or_luggage"]["value"] if (case["party"].get("infant") or case["party"].get("luggage")) \
        else R["limits"]["walk_m"]["default"]["value"]
    over = []
    if case["transfers"] > lim_t:
        over.append(f"환승 {case['transfers']}회 > 상한 {lim_t}회")
    if case["walk_m"] > lim_w:
        over.append(f"도보 {case['walk_m']}m > 상한 {lim_w}m")

    print(f"\n■ {case['name']}")
    print(f"   카카오 원값     승차 {case['ride_min']}분 + 도보 {case['walk_min']}분, 환승 {case['transfers']}회, 도보 {case['walk_m']}m")
    on = WP.get("enabled", True)
    slow = [k for k in ("infant", "elderly", "luggage", "fatigue_high") if case["party"].get(k)]
    print(f"   보행 보정       ×{p}  → 도보 {walk_adj:.1f}분   "
          f"{'(MVP 미적용 — 켜면 ×' + str(p_ext) + ', 도보 ' + format(case['walk_min']*p_ext, '.1f') + '분)' if not on and p_ext > p else ''}")
    warn = WP.get("walk_warn_min")
    if not on and warn and slow and case["walk_min"] > warn["value"]:
        print(f"   ⚠ 경고          도보 {case['walk_min']}분 > {warn['value']}분 + 느린 동행({', '.join(slow)}) "
              f"→ 보정을 끈 과소평가 약 {case['walk_min']*(p_ext-1):.0f}분, 버퍼가 다 흡수하지 못함")
    print(f"   길찾기 가산     +{way_add}분  (초행, 환승 {case['transfers']}회 × {wf['value'] if wf else 0}분)")
    print(f"   대형 환승역     +{large_add}분 {large or ''}")
    print(f"   환승 대기       +{wait:.1f}분  (배차 {hw}분의 {'전부 — 최악값' if worst else '절반 — 평균'})")
    print(f"   추정 소요       {total:.1f}분  → 도착 추정 {int(arrive)//60:02d}:{int(arrive)%60:02d} [추정]")
    print(f"   버퍼            {buf}분 ({stage})")
    verdict = "성립" if slack >= 0 else "불가"
    if over:
        verdict = "성립하나 탈락"
    print(f"   판정            {verdict}  (여유 {slack:+.1f}분)")
    for o in over:
        print(f"                   탈락 사유: {o}")
    if slack < 0:
        print(f"                   완화 조건: 출발을 {abs(slack):.0f}분 당기면 성립")


T = 60
judge({"name": "명동교자 → 경복궁, 유아 1명, 15:00 고정",
       "depart_min": 14 * T + 20, "arrive_by_min": 15 * T, "ride_min": 12, "walk_min": 8,
       "transfers": 1, "walk_m": 600, "headway_min": 4,
       "transfer_stations": ["을지로3가"], "party": {"infant": True}})
judge({"name": "인천공항 → 홍대입구, 캐리어, 도착 23:40 필요, 막차 근처",
       "depart_min": 22 * T + 30, "arrive_by_min": 23 * T + 40, "ride_min": 52, "walk_min": 10,
       "transfers": 1, "walk_m": 700, "headway_min": 12,
       "transfer_stations": ["서울역"], "party": {"luggage": True}, "near_last_train": True})
judge({"name": "숙소 → 북촌 산책 구간, 유아 동반, 도보가 긴 경우",
       "depart_min": 10 * T + 0, "arrive_by_min": 11 * T + 10, "ride_min": 15, "walk_min": 18,
       "transfers": 1, "walk_m": 750, "headway_min": 5,
       "transfer_stations": ["종로3가"], "party": {"infant": True}})
judge({"name": "성수 → 남산, 진행 중, 환승 3회",
       "depart_min": 13 * T + 10, "arrive_by_min": 14 * T + 30, "ride_min": 30, "walk_min": 14,
       "transfers": 3, "walk_m": 1400, "headway_min": 6,
       "transfer_stations": ["왕십리", "충무로"], "party": {"in_progress": True}})

print("\n주: 위 숫자는 규칙값이 말이 되는지 보려고 카카오 응답을 손으로 넣은 예시다. 실제 판정기는 카카오 응답과 시간표에서 읽는다.")

# ── ④ 택시 요금 산식 (taxi.fare) — 2026-09-19 15번 방 ─────────────────
# 산식은 확정(서울시 고시)이지만 요금은 거리·저속시간(18번 방 뒤)이 있어야 나온다.
# 여기서는 ⓐ 상수끼리의 항등식 ⓑ 시간대·구역이 빈틈·겹침 없이 덮이는지 ⓒ 복합 상한 ⓓ 규칙 파일의 검산 예시를 코드가 같은 수로 내는지 본다.
import math

SEOUL_GU = {"종로", "중", "용산", "성동", "광진", "동대문", "중랑", "성북", "강북", "도봉", "노원", "은평", "서대문",
            "마포", "양천", "강서", "구로", "금천", "영등포", "동작", "관악", "서초", "강남", "송파", "강동"}


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def taxi_rate(fare, kind, hhmm):
    """승차 시각 기준 심야 할증률 하나. 구간은 [from, to) 이고 자정을 넘는 구간(23:00~02:00)을 처리한다."""
    t = _hm(hhmm)
    for seg in fare[kind]["night_surcharge"]["value"]:
        a, b = _hm(seg["from"]), _hm(seg["to"])
        hit = (a <= t < b) if a < b else (t >= a or t < b)   # 뒤쪽은 자정 넘김
        if hit:
            return seg["rate"]
    return 0.0


def taxi_fare(fare, kind, dist_m, slow_s, hhmm, out_of_city=False):
    """규칙 taxi.fare.산식 그대로. 병산 근사 — 전 구간 거리요금 + 저속 시간요금, 단위는 올림(보수적), 할증 후 총액은 step 최근접."""
    k = fare[kind]
    base = k["base_fare_won"]["value"]
    extra_m = max(0, dist_m - k["base_dist_m"]["value"])
    dist_won = math.ceil(extra_m / k["dist_unit_m"]["value"]) * k["dist_unit_won"]["value"]
    time_won = math.ceil(slow_s / k["time_unit_s"]["value"]) * k["time_unit_won"]["value"]
    meter = base + dist_won + time_won
    rate = taxi_rate(fare, kind, hhmm) + (k["out_of_city_rate"]["value"] if out_of_city else 0.0)
    rate = min(rate, k["max_combined_rate"]["value"])
    step = k["fare_step_won"]["value"]
    return int(math.floor(meter * (1 + rate) / step + 0.5) * step)   # 할증 후 총액은 100원 최근접(5,760→5,800 · 6,720→6,700)


taxi_problems = []
F = R["taxi"]["fare"]
for kind in ("중형", "대형_모범"):
    k = F[kind]
    # ⓐ 거리 단위 / 시간 단위 = 전환 속도. 131m/30s = 15.72 km/h, 151m/36s = 15.10 km/h
    calc = round(k["dist_unit_m"]["value"] / k["time_unit_s"]["value"] * 3.6, 2)
    if abs(calc - k["time_speed_threshold_kmh"]["value"]) > 0.005:
        taxi_problems.append(f"{kind}: 전환속도 {k['time_speed_threshold_kmh']['value']} ≠ 거리단위/시간단위 {calc} km/h")
    if k["dist_unit_won"]["value"] != k["time_unit_won"]["value"]:
        taxi_problems.append(f"{kind}: 거리 단위요금과 시간 단위요금이 다르다 — 병산제 전환속도 항등식이 성립하지 않는다")
    # ⓑ 심야 구간이 22:00~04:00 을 빈틈·겹침 없이 덮는가
    segs = k["night_surcharge"]["value"]
    cover = [0] * (24 * 60)
    for seg in segs:
        a, b = _hm(seg["from"]), _hm(seg["to"])
        rng = range(a, b) if a < b else list(range(a, 24 * 60)) + list(range(0, b))
        for t in rng:
            cover[t] += 1
        if seg["rate"] not in (0.2, 0.4):
            taxi_problems.append(f"{kind}: 심야율 {seg['rate']} 는 고시에 없는 값(20%/40%)")
    night = set(range(22 * 60, 24 * 60)) | set(range(0, 4 * 60))
    if any(cover[t] != (1 if t in night else 0) for t in range(24 * 60)):
        taxi_problems.append(f"{kind}: 심야 구간이 22:00~04:00 을 정확히 한 번씩 덮지 않는다")
    # ⓒ 복합 상한 ≥ 최대 심야율 + 시계외율 (외국인 20% 은 우리 산식 밖)
    top = max(s["rate"] for s in segs) + k["out_of_city_rate"]["value"]
    if k["max_combined_rate"]["value"] + 1e-9 < top:
        taxi_problems.append(f"{kind}: 복합 상한 {k['max_combined_rate']['value']} < 심야최대+시계외 {top}")

# 인터내셔널택시 공항 구간요금(일반 택시 정액이 아니다): 25개 자치구가 A~E 에 정확히 한 번씩, 금액은 A→E 단조 증가
seen = {}
zones = F["공항"]["인터내셔널택시_구간요금"]["value"]
for z in zones:
    for gu in z["자치구"]:
        seen.setdefault(gu, []).append(z["zone"])
dup = {g: zs for g, zs in seen.items() if len(zs) > 1}
missing = SEOUL_GU - set(seen)
extra = set(seen) - SEOUL_GU
if dup or missing or extra:
    taxi_problems.append(f"공항 구간요금 자치구 — 중복 {dup or '없음'} · 누락 {sorted(missing) or '없음'} · 서울 밖 {sorted(extra) or '없음'}")
for col in ("중형", "대형_모범"):
    if any(zones[i][col] >= zones[i + 1][col] for i in range(len(zones) - 1)):
        taxi_problems.append(f"공항 구간요금({col})이 A→E 로 단조 증가하지 않는다")

# ⓓ 규칙 파일의 검산 예시를 코드가 같은 수로 내는가
print("\n④ 택시 요금 산식 검산 (taxi.fare · 산식 등급 확정, 요금 등급 추정 — 거리·시간은 18번 방 뒤)")
for c in F["검산_예시"]["cases"]:
    got = taxi_fare(F, c["type"], c["dist_m"], c["slow_s"], c["time"], c.get("out_of_city", False))
    ok = got == c["expect_won"]
    print(f"   {'OK ' if ok else 'NG '} {c['name']:<28} 계산 {got:>7,}원 · 기대 {c['expect_won']:>7,}원")
    if not ok:
        taxi_problems.append(f"검산 예시 어긋남: {c['name']} — 계산 {got} ≠ 기대 {c['expect_won']}")
m = F["중형"]
print(f"   중형 {m['base_fare_won']['value']:,}원/{m['base_dist_m']['value']}m · {m['dist_unit_m']['value']}m·{m['time_unit_s']['value']}s 당 "
      f"{m['dist_unit_won']['value']}원 · 전환 {m['time_speed_threshold_kmh']['value']} km/h · "
      f"심야 {[s['rate'] for s in m['night_surcharge']['value']]} · 시계외 {m['out_of_city_rate']['value']} · 상한 {m['max_combined_rate']['value']}")
print(f"   유효성: {F['유효성_2026-09']['value']} [{F['유효성_2026-09']['grade']}] · 확인 {F['source']['확인시각']}")
print("④ 택시 검사:", "통과" if not taxi_problems else "문제 " + str(len(taxi_problems)) + "건")
for p in taxi_problems:
    print("   -", p)
if problems or taxi_problems:
    sys.exit(1)
