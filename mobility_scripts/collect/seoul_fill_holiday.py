# mobility_scripts/collect/seoul_fill_holiday.py — 2·7호선 휴일 시간표를 열린데이터광장에서 보충
import os, json, time, requests
from _paths import RAW_MOBILITY

KEY = os.environ["SEOUL_OPENAPI_KEY"]
BASE = f"http://openapi.seoul.go.kr:8088/{KEY}/json"
SVC = "SearchSTNTimeTableByIDService"
OUT = RAW_MOBILITY / "seoul_timetable_holiday_2_7.jsonl"

stations = json.loads((RAW_MOBILITY / "stations_all.json").read_text(encoding="utf-8"))
targets = [s for s in stations if s["LINE_NUM"] in ("02호선", "07호선")]
print(len(targets), "역")

n = 0
with OUT.open("w", encoding="utf-8") as f:
    for s in targets:
        for updn in ("1", "2"):          # 1 상행/내선, 2 하행/외선
            j = requests.get(f"{BASE}/{SVC}/1/1000/{s['STATION_CD']}/3/{updn}/", timeout=20).json()  # 3 = 휴일
            rows = j.get(SVC, {}).get("row", [])
            for r in rows:
                f.write(json.dumps({**r, "_updn": updn, "_dow": "3"}, ensure_ascii=False) + "\n")
            n += len(rows)
            time.sleep(0.15)
        print(f"  {s['LINE_NUM']} {s['STATION_NM']} 누적 {n}")
print("완료", OUT, n, "건")