# mobility_scripts/collect/build_transfer_walk_v1.py — 환승역 도보 거리표 v1
#
# 소스: 서울교통공사_환승역거리 소요시간 정보 (열린데이터광장 OA-13290, 공공누리 1유형, 연 1회)
#       raw/mobility/서울교통공사_환승역거리 소요시간 정보_*.csv  (cp949)
#
# ★거리만 기록으로 받고 시간은 우리 속도로 다시 계산한다.
#   소스의 '환승소요시간'은 보행속도 1.2 m/s 기준 계산값이다(145쌍의 거리÷시간 중앙값 1.200으로 확인).
#   우리 기준선은 카카오 실측 1.04 m/s 이므로, 남의 기준으로 계산된 시간을 그대로 쓰면 등급 체계가 깨진다.
#   거리 = [확정](기록) · 환산 시간 = [추정](우리 계산).
#
# 출력: processed/mobility/transfer_walk_v1.json
import csv, json, re, sys
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY, PROCESSED          # DATA_DIR/travel 기준 (저장소 관례)

KST = timezone(timedelta(hours=9))
RAW = RAW_MOBILITY
OUT_DIR = PROCESSED / "mobility"
OUT = OUT_DIR / "transfer_walk_v1.json"
REPORT = OUT_DIR / "transfer_walk_v1_report.md"

OUR_SPEED = 1.04          # m/s — probe_kakao_walk.py 실측(rules.measured_baseline)
SRC_SPEED = 1.2           # m/s — 소스가 쓴 기준

# 소스 표기 → 우리 노선 코드
LINE = {"1호선": "01호선", "2호선": "02호선", "3호선": "03호선", "4호선": "04호선", "5호선": "05호선",
        "6호선": "06호선", "7호선": "07호선", "8호선": "08호선", "9호선": "09호선",
        "경의중앙선": "경의선", "수인분당선": "수인분당선", "공항철도": "공항철도", "신분당선": "신분당선",
        "우이신설선": "우이신설경전철", "김포골드라인": "김포도시철도", "서해선": "서해선",
        "경춘선": "경춘선", "경강선": "경강선", "인천1호선": "인천선", "인천2호선": "인천2호선",
        "GTX-A": "GTX-A", "신림선": "신림선", "용인경전철": "용인경전철", "의정부경전철": "의정부경전철"}


def norm_line(s):
    s = (s or "").strip()
    if re.fullmatch(r"\d", s):
        s += "호선"
    return LINE.get(s, s)


def sec(hhmm):
    m, s = hhmm.split(":")
    return int(m) * 60 + int(s)


src = sorted(RAW.glob("서울교통공사_환승역거리*.csv"))
if not src:
    sys.exit(f"소스가 없다: {RAW}/서울교통공사_환승역거리*.csv")
src = src[-1]

rows = list(csv.DictReader(src.open(encoding="cp949")))
basis = re.search(r"(\d{8})", src.name)
basis = f"{basis.group(1)[:4]}-{basis.group(1)[4:6]}-{basis.group(1)[6:]}" if basis else None

pairs, stations, speed_check = {}, {}, []
for r in rows:
    st = r["환승역명"].strip()
    a = norm_line(r["호선"])
    b = norm_line(r["환승노선"])
    m = float(r["환승거리"])
    s = sec(r["환승소요시간"])
    speed_check.append(m / s if s else None)
    ours = round(m / OUR_SPEED / 60, 1)          # 분, 우리 속도 환산
    rec = {"station_nm": st, "from_line": a, "to_line": b, "distance_m": m,
           "walk_min": ours, "src_min": round(s / 60, 1), "src_speed_mps": SRC_SPEED}
    # 무향 — 양쪽 다 조회되게
    pairs[f"{st}|{a}|{b}"] = rec
    pairs[f"{st}|{b}|{a}"] = {**rec, "from_line": b, "to_line": a}
    # 역 단위 대표값(노선쌍을 모를 때 쓰는 보수적 fallback = 그 역의 최대)
    if st not in stations or m > stations[st]["distance_m"]:
        stations[st] = {"station_nm": st, "distance_m": m, "walk_min": ours,
                        "worst_pair": f"{a}↔{b}"}

vs = [v for v in speed_check if v]
payload = {
    "schema": "transfer_walk_v1",
    "built_at": datetime.now(KST).isoformat(timespec="seconds"),
    "source": "seoul_metro_transfer_distance",
    "source_file": src.name,
    "data_basis_date": basis,
    "license": "공공누리 제1유형(출처표시) — 저장·변경 가능",
    "grade": {
        "distance_m": "확정 — 소스 기록 그대로",
        "walk_min": "추정 — 거리 ÷ 우리 실측 보행속도 1.04 m/s. 소스의 시간(1.2 m/s 기준)은 쓰지 않는다",
    },
    "src_speed_observed": {"median": round(sorted(vs)[len(vs) // 2], 3),
                           "min": round(min(vs), 3), "max": round(max(vs), 3),
                           "note": "거리÷소스시간. 1.2 근처면 소스가 1.2 m/s 로 계산한 것이 맞다"},
    "coverage": {"pairs": len(rows), "stations": len(stations),
                 "note": "서울교통공사 관할 환승만. 코레일·공항철도끼리의 환승은 없다 → 그 구간은 근거없음"},
    "usage": "판정기는 (역, 타던 노선, 갈아탈 노선)으로 pairs 를 찾는다. 없으면 stations 의 그 역 최대값(보수적), "
             "그것도 없으면 환승 도보를 '근거없음'으로 두고 warnings 에 남긴다.",
    "pairs": pairs,
    "stations": stations,
}
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

# ── 리포트 ──
by_st = sorted(stations.values(), key=lambda r: -r["distance_m"])
lines = ["# 환승역 도보 거리표 v1", "",
         f"소스 {src.name} (기준일자 {basis}) · 생성 {payload['built_at']}",
         f"환승쌍 {len(rows)} · 역 {len(stations)}",
         f"소스 함의 보행속도 중앙 {payload['src_speed_observed']['median']} m/s "
         f"(범위 {payload['src_speed_observed']['min']}~{payload['src_speed_observed']['max']})",
         "", "## 우리 속도(1.04 m/s) 환산 상위 20역", "",
         "| 역 | 거리(m) | 우리 환산 | 구간 |", "|---|---:|---:|---|"]
for r in by_st[:20]:
    lines.append(f"| {r['station_nm']} | {r['distance_m']:.0f} | {r['walk_min']:.1f}분 | {r['worst_pair']} |")
lines += ["", "## 짧은 환승 10역 (가산을 붙일 이유가 없는 곳)", "",
          "| 역 | 거리(m) | 우리 환산 | 구간 |", "|---|---:|---:|---|"]
for r in by_st[-10:]:
    lines.append(f"| {r['station_nm']} | {r['distance_m']:.0f} | {r['walk_min']:.1f}분 | {r['worst_pair']} |")
REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"환승쌍 {len(rows)} · 역 {len(stations)} → {OUT}")
print(f"리포트 → {REPORT}")
print(f"소스 함의 속도 중앙 {payload['src_speed_observed']['median']} m/s (1.2 기준 확인)")
print("상위 5:", ", ".join(f"{r['station_nm']} {r['distance_m']:.0f}m→{r['walk_min']}분" for r in by_st[:5]))
