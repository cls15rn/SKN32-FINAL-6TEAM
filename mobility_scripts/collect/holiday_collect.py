# mobility_scripts/collect/holiday_collect.py — 공휴일(특일 정보) 수집 → 시간표 요일 판정용
# 실행: 저장소 루트에서  python mobility_scripts/collect/holiday_collect.py --years 2026 2027
# 선행: 공공데이터포털 "한국천문연구원_특일 정보"(15012690) 활용신청 — 무료·자동승인. DATA_GO_KR_KEY 사용.
#
# ★왜 필수인가: 시간표를 평일(weekday)/휴일(holiday)로 나눠 저장했는데, **특정 날짜가 공휴일인지 아는 수단이
#   없으면 어느 시간표를 봐야 할지 모른다**. 2026-10-03(개천절)에 평일 시간표로 막차를 판정하면 그냥 틀린다.
#   인바운드 성수기가 추석·개천절·한글날이 몰린 시기라 더 그렇다.
#
# 요일 판정 규칙 (이 파일의 day_type_of 가 정본):
#   평일이고 공휴일 아님 → weekday
#   토요일 · 일요일 · 공휴일 → holiday
#   ※ 수도권 TAGO 는 토요일(02)을 주지 않고 토·일·공휴일을 03 으로 준다(소싱 문서 3-1).
#     서울교통공사 공식 시간표도 평일 / 토·공휴일 2종이라 이 규칙과 맞는다.
# 출력: final_project_cs/app/modules/travel_ops/mobility/engine/rules/holidays_<시작>_<끝>.json — git 에 둔다(연 단위로만 바뀌고 팀원도 쓴다).
#   ★ 31번 방(2026-09-21) — 판정기가 읽는 자리로 옮겼다. 옛 config/mobility 에 쓰면 판정기는 **조용히 옛 달력을 쓴다.**
import os, json, argparse, time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import requests

REPO = Path(__file__).resolve().parents[2]
load_dotenv(REPO / ".env")
KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService"
SOURCE = "kasi_spcde"                       # 한국천문연구원 특일 정보
KST = timezone(timedelta(hours=9))
OUT_DIR = REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules"


def day_type_of(d: date, holidays: set) -> str:
    """날짜 → 시간표 요일 축. 검증기가 이 함수를 쓴다."""
    if d.weekday() >= 5 or d.isoformat() in holidays:      # 5=토, 6=일
        return "holiday"
    return "weekday"


def call(op, **p):
    for attempt in range(4):
        r = requests.get(f"{BASE}/{op}", params={"serviceKey": KEY, "_type": "json",
                                                 "numOfRows": 100, **p}, timeout=30)
        if r.status_code == 200:
            try:
                body = r.json()["response"]["body"]
            except (KeyError, ValueError):
                if "<" in r.text[:200]:                     # XML 로 오는 경우
                    import xml.etree.ElementTree as ET
                    try:
                        root = ET.fromstring(r.text)
                        items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
                        return items
                    except ET.ParseError:
                        pass
                print("  응답 형식 이상:", r.text[:250]); return []
            it = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
            return [it] if isinstance(it, dict) else list(it)
        if r.status_code >= 500:
            time.sleep(2 + attempt * 2); continue
        print("  HTTP", r.status_code, r.text[:250]); return []
    return []


ap = argparse.ArgumentParser()
ap.add_argument("--years", nargs="+", type=int, default=[date.today().year, date.today().year + 1])
ap.add_argument("--op", default="getRestDeInfo", help="오퍼레이션 (기본 공휴일정보조회)")
args = ap.parse_args()

rows, fetched = [], datetime.now(KST).isoformat(timespec="seconds")
for y in args.years:
    got_year = 0
    for m in range(1, 13):
        items = call(args.op, solYear=y, solMonth=f"{m:02d}")
        rows += items
        got_year += len(items)
        time.sleep(0.1)
    print(f"  {y}년: {got_year}건")

holidays, names = {}, {}
for it in rows:
    loc = str(it.get("locdate") or "").strip()
    if len(loc) != 8 or not loc.isdigit():
        continue
    iso = f"{loc[:4]}-{loc[4:6]}-{loc[6:]}"
    is_h = str(it.get("isHoliday") or "").strip().upper() == "Y"
    nm = str(it.get("dateName") or "").strip()
    if is_h:
        holidays[iso] = nm
    names.setdefault(iso, []).append({"name": nm, "is_holiday": is_h})

if not rows:
    raise SystemExit("응답이 비었다. 활용신청 승인 여부와 오퍼레이션명을 확인한다 "
                     "(--op getRestDeInfo / getHoliDeInfo). mobility_scripts/probe_api.py 로 먼저 찍어봐도 된다.")

OUT_DIR.mkdir(parents=True, exist_ok=True)
span = f"{min(args.years)}_{max(args.years)}"
out = OUT_DIR / f"holidays_{span}.json"
out.write_text(json.dumps({
    "source": SOURCE, "endpoint": f"{BASE}/{args.op}", "years": args.years,
    "fetched_at": fetched, "fetched_at_precision": "second",
    "grade": "확정", "근거": "한국천문연구원 특일 정보 — 관보 고시 기준 공휴일 기록",
    "day_type_rule": "평일이고 공휴일 아님 → weekday · 토·일·공휴일 → holiday "
                     "(수도권 TAGO 는 토요일 시간표를 주지 않고 토·일·공휴일을 03 으로 준다)",
    "count": len(holidays), "holidays": holidays, "all_dates": names,
}, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"\n공휴일 {len(holidays)}일 → {out}")
for iso in sorted(holidays):
    print(f"  {iso} {date.fromisoformat(iso).strftime('%a')}  {holidays[iso]}")

# 최종발표 전후 요일 판정 미리보기 — 시연 일정과 겹치는지 본다
print("\n요일 판정 예시 (2026-09-25 ~ 10-12):")
hs = set(holidays)
d = date(2026, 9, 25)
while d <= date(2026, 10, 12):
    t = day_type_of(d, hs)
    mark = f"  ← {holidays[d.isoformat()]}" if d.isoformat() in hs else ""
    print(f"  {d} {d.strftime('%a')}  {t}{mark}")
    d += timedelta(days=1)
