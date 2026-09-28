# mobility_scripts/probe_kakao_walk.py — 카카오가 가정한 보행 속도를 재고, 대중교통 응답에서 도보/승차가 분리되는지 확인한다
# 실행: 저장소 루트에서  python mobility_scripts/probe_kakao_walk.py [--tag 낮]
# 왜 필요한가: "카카오 도보 시간은 노약자까지 고려한 평균이라 그냥 쓰면 된다"는 팀 의견(2026-09-09)을
#   논쟁이 아니라 숫자로 가른다. 도보 응답의 거리 ÷ 시간 = 카카오가 가정한 속도다.
#   - 1.1~1.4 m/s 로 나오면 '보통 성인' 가정 → 유아·짐·초행 보정이 필요하다(규칙 v0.1 유지).
#   - 0.8~0.9 m/s 로 나오면 '느린 보행자 포함' 가정 → base 계수를 1.0 쪽으로 낮추고 유아 계수도 줄인다.
#   - 구간마다 값이 크게 흔들리면 거리 기반 고정 속도가 아니라 다른 모델이므로 계수 자체를 재검토한다.
# 호출: 도보 6회 + 대중교통 2회 = 8회 (도보·대중교통 각 1,000/일)
import os, json, argparse, statistics
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
KEY = os.environ["KAKAO_REST_KEY"]          # ★ 하드코딩 금지 — .env 에서 읽는다
H = {"Authorization": f"KakaoAK {KEY}"}
KST = timezone(timedelta(hours=9))
OUT = REPO.parent / "data" / "travel" / "raw" / "mobility" / "kakao_walk_probe.jsonl"

# 서울시 보행신호 산정 기준 (서울시 보도자료) — 우리 값이 어디쯤인지 대는 잣대
REF = [("교통약자·보호구역 기준", 0.7), ("일반 보행신호 기준", 1.0),
       ("도보 길찾기 통상 4km/h", 1.11), ("성인 평균 보행", 1.3)]

PAIRS = [
    ("서울역 → 시청",       126.9707, 37.5547, 126.9780, 37.5663),
    ("시청 → 경복궁",       126.9780, 37.5663, 126.9770, 37.5796),
    ("서울역 → 경복궁",     126.9707, 37.5547, 126.9770, 37.5796),
    ("명동 → 시청",         126.9850, 37.5636, 126.9780, 37.5663),
    ("경복궁 → 광화문",     126.9770, 37.5796, 126.9769, 37.5720),
    ("홍대입구 → 합정",     126.9240, 37.5570, 126.9137, 37.5495),
]

ap = argparse.ArgumentParser()
ap.add_argument("--tag", default="", help="실행 구분 (예: 낮 / 심야) — 시간대별 비교용")
args = ap.parse_args()
now = datetime.now(KST)


def get(path, **p):
    r = requests.get(f"https://dapi.kakao.com/v2/routing/{path}", headers=H, params=p, timeout=20)
    if r.status_code != 200:
        print(f"  HTTP {r.status_code} {r.text[:200]}")
        return None
    return r.json()


def dig(o, keys):
    """중첩 dict 에서 첫 번째로 걸리는 키의 값 (응답 스펙이 바뀌어도 견디게)"""
    if isinstance(o, dict):
        for k, v in o.items():
            if k in keys and isinstance(v, (int, float)):
                return v
            got = dig(v, keys)
            if got is not None:
                return got
    elif isinstance(o, list):
        for v in o:
            got = dig(v, keys)
            if got is not None:
                return got
    return None


print(f"■ 도보 속도 측정  ({now:%Y-%m-%d %H:%M} KST{' · ' + args.tag if args.tag else ''})\n")
print(f"{'구간':<20}{'거리(m)':>9}{'시간(초)':>9}{'속도(m/s)':>11}{'km/h':>8}")
speeds, rows = [], []
for name, sx, sy, ex, ey in PAIRS:
    j = get("walk", start_x=sx, start_y=sy, end_x=ex, end_y=ey)
    if not j or j.get("status") not in (None, "OK"):
        print(f"{name:<20}  응답 이상: {str(j)[:80]}")
        continue
    dist = dig(j, {"totalDistance", "distance"})
    dur = dig(j, {"totalTime", "duration"})
    if not dist or not dur:
        print(f"{name:<20}  거리/시간 필드를 못 찾음 → 응답 구조 확인 필요")
        print("   keys:", list(j.get("route", j)) if isinstance(j, dict) else type(j))
        continue
    mps = dist / dur
    speeds.append(mps); rows.append({"pair": name, "distance_m": dist, "duration_s": dur, "mps": round(mps, 3)})
    print(f"{name:<20}{dist:>9.0f}{dur:>9.0f}{mps:>11.2f}{mps * 3.6:>8.1f}")

if speeds:
    med = statistics.median(speeds)
    print(f"\n중앙값 {med:.2f} m/s ({med * 3.6:.1f} km/h) · 범위 {min(speeds):.2f}~{max(speeds):.2f}")
    print("\n대조:")
    for label, v in REF:
        print(f"  {label:<24}{v:>5.2f} m/s   {'← 우리 측정값이 여기 근처' if abs(v - med) < 0.12 else ''}")
    print("\n판단:")
    if med >= 1.15:
        print("  카카오는 '보통 이상' 속도를 가정한다. 유아·짐·초행 보정 계수가 필요하다 — 규칙 v0.1 유지.")
    elif med >= 1.0:
        print("  카카오는 '일반 성인' 가정이다. 노약자까지 포함한 값이 아니므로 보정은 필요하되,")
        print("  base_first_visit 은 1.1 안팎이면 충분하다.")
    else:
        print("  카카오가 이미 느린 보행자를 반영하고 있다. base_first_visit 을 1.0 으로 내리고")
        print("  유아 계수도 1.5 → 1.2~1.3 으로 줄인다. 팀 의견이 맞는 쪽.")
    print("  ※ 구간별 편차가 0.2 m/s 를 넘으면 고정 속도 모델이 아니다(신호 대기·경사 반영). 그때는 계수 대신 버퍼로 흡수한다.")

# ── 대중교통 응답 구조: 도보 step 이 시간과 함께 오는가, totalTime 에 대기가 있는가 ──────
print("\n■ 대중교통 응답 구조\n")
TRANSIT = [("서울역 → 삼성(환승 있는 지하철)", 126.9707, 37.5547, 127.0632, 37.5088),
           ("서울역 → 경복궁(짧은 구간)",       126.9707, 37.5547, 126.9770, 37.5796)]
transit = []
for name, sx, sy, ex, ey in TRANSIT:
    j = get("publictraffic", start_x=sx, start_y=sy, end_x=ex, end_y=ey)
    if not j or j.get("status") != "OK" or not j.get("routes"):
        print(f"  {name}: 응답 이상 또는 경로 없음 — {str(j)[:150]}")
        continue
    rt = j["routes"][0]
    pr = rt.get("properties", {})
    steps = rt.get("steps", [])
    print(f"  ▷ {name}  type={pr.get('type')} totalTime={pr.get('totalTime')}s "
          f"totalDistance={pr.get('totalDistance')}m transfers={pr.get('transfers')}")
    sum_t = sum_d = 0
    walk_t = walk_d = 0
    for st in steps:
        sp = st.get("properties", st)
        t, d, ty = sp.get("time", 0), sp.get("distance", 0), sp.get("type")
        sum_t += t; sum_d += d
        if str(ty).upper() in ("WALK", "WALKING", "FOOT"):
            walk_t += t; walk_d += d
        veh = (sp.get("vehicles") or [{}])[0].get("name", "")
        print(f"     {str(ty):<10}{d:>7}m {t:>6}s  {sp.get('guidance', '')[:40]} {veh}")
    gap_t = (pr.get("totalTime") or 0) - sum_t
    print(f"     step 합계 {sum_d}m / {sum_t}s   ·   totalTime 과의 차이 {gap_t:+}s")
    if walk_t:
        print(f"     도보 step {walk_d}m / {walk_t}s → {walk_d/walk_t:.2f} m/s  ✔ 도보가 분리된다 = 계수를 여기에만 곱하면 된다")
    else:
        print("     도보 step 이 type 으로 구분되지 않는다 → 접근 도보는 도보 API 로 따로 구하고 나머지는 버퍼로 흡수")
    print(f"     {'대기 시간 없음 — 우리 시간표로 채워야 한다' if abs(gap_t) <= 2 else f'차이 {gap_t}s 가 대기일 수 있다 — 확인 필요'}")
    transit.append({"pair": name, "type": pr.get("type"), "totalTime": pr.get("totalTime"),
                    "totalDistance": pr.get("totalDistance"), "transfers": pr.get("transfers"),
                    "step_time_sum": sum_t, "step_distance_sum": sum_d,
                    "walk_time": walk_t, "walk_distance": walk_d, "gap_s": gap_t,
                    "step_types": [ (st.get("properties", st)).get("type") for st in steps ]})

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("a", encoding="utf-8") as f:
    f.write(json.dumps({"probed_at": now.isoformat(timespec="seconds"), "tag": args.tag,
                        "walk": rows, "median_mps": round(statistics.median(speeds), 3) if speeds else None,
                        "transit": transit}, ensure_ascii=False) + "\n")
print(f"\n기록 → {OUT}  (--tag 낮 / --tag 심야 로 두 번 돌리면 totalTime 이 시각에 따라 바뀌는지도 같이 확인된다)")
