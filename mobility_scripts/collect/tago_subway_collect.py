# mobility_scripts/collect/tago_subway_collect.py — TAGO 지하철정보: 전 노선 역별 시간표 수집 (체크포인트·이어받기)
# 실행: 저장소 루트에서  python mobility_scripts/collect/tago_subway_collect.py
# 호출량: 역명 검색 ≈ 650 + 역ID × 요일3 × 방향2 ≈ 5,000  → 일 10,000 안. 중간에 끊기면 다시 실행하면 이어감.
import os, json, time, requests
from _paths import RAW_MOBILITY

KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "https://apis.data.go.kr/1613000/SubwayInfo"
COMMON = {"serviceKey": KEY, "_type": "json", "numOfRows": 1000, "pageNo": 1}

STATIONS_SRC = RAW_MOBILITY / "stations_all.json"        # 어제 받은 열린데이터광장 역 목록 (역명 소스)
ID_MAP = RAW_MOBILITY / "tago_station_ids.json"           # 역명 → TAGO 역 목록 (체크포인트 1)
TIMETABLE = RAW_MOBILITY / "tago_timetable.jsonl"         # 열차 1건 = 1줄 (체크포인트 2)
DONE = RAW_MOBILITY / "tago_timetable_done.json"          # 완료한 (역ID, 요일, 방향)


def call(op, **p):
    for attempt in range(5):
        r = requests.get(f"{BASE}/{op}", params={**COMMON, **p}, timeout=30)
        if r.status_code == 200:
            try:
                return r.json()["response"]["body"]
            except (KeyError, ValueError):
                print("  응답 형식 이상:", r.text[:200])
                return {}
        txt = r.text
        if "PER_SECOND" in txt or "SERVICETIMEOUT" in txt or r.status_code >= 500:
            time.sleep(2 + attempt * 2)
            continue
        print("  HTTP", r.status_code, txt[:200])
        return {}
    return {}


def load_json(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


# ── ① 역명 → TAGO 역 ID (노선별로 여러 개) ─────────────────────────────
stations = load_json(STATIONS_SRC, [])
names = sorted({s["STATION_NM"] for s in stations})
id_map = load_json(ID_MAP, {})
print(f"① 역명 {len(names)}개, 이미 검색 {len(id_map)}개")
for i, nm in enumerate(names, 1):
    if nm in id_map:
        continue
    body = call("GetKwrdFndSubwaySttnList", subwayStationName=nm)
    items = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
    if isinstance(items, dict):
        items = [items]
    # 키워드 검색이라 부분일치가 섞임 → 역명이 정확히 같은 것만
    id_map[nm] = [it for it in items if it.get("subwayStationName") == nm]
    if i % 25 == 0:
        ID_MAP.write_text(json.dumps(id_map, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  … {i}/{len(names)}")
    time.sleep(0.15)
ID_MAP.write_text(json.dumps(id_map, ensure_ascii=False, indent=1), encoding="utf-8")
targets = [(nm, it["subwayStationId"], it.get("subwayRouteName")) for nm, its in id_map.items() for it in its]
print(f"① TAGO 역 ID {len(targets)}개")

# ── ② 역ID × 요일(01 평일/02 토/03 일·공휴일) × 방향(U/D) 시간표 ─────────
done = set(load_json(DONE, []))
keys = [(sid, d, u) for _, sid, _ in targets for d in ("01", "02", "03") for u in ("U", "D")]
todo = [k for k in keys if "|".join(k) not in done]
print(f"② 조합 {len(keys)}개, 남은 {len(todo)}개")

with TIMETABLE.open("a", encoding="utf-8") as f:
    for n, (sid, d, u) in enumerate(todo, 1):
        body = call("GetSubwaySttnAcctoSchdulList", subwayStationId=sid, dailyTypeCode=d, upDownTypeCode=u)
        items = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
        if isinstance(items, dict):
            items = [items]
        for it in items:
            f.write(json.dumps({
                "station_id": it.get("subwayStationId"), "station_nm": it.get("subwayStationNm"),
                "route_id": it.get("subwayRouteId"),
                "end_station_id": it.get("endSubwayStationId"), "end_station_nm": it.get("endSubwayStationNm"),
                "dep_time": it.get("depTime"), "arr_time": it.get("arrTime"),   # 'HHMMSS' 또는 '0'(시·종착)
                "daily_type": it.get("dailyTypeCode", d), "updown": it.get("upDownTypeCode", u),
            }, ensure_ascii=False) + "\n")
        done.add("|".join((sid, d, u)))
        if n % 50 == 0:
            f.flush()
            DONE.write_text(json.dumps(sorted(done)), encoding="utf-8")
            print(f"  … {n}/{len(todo)}  (역 {sid} {d}/{u}: {len(items)}건)")
        time.sleep(0.12)

DONE.write_text(json.dumps(sorted(done)), encoding="utf-8")
print("완료. 시간표 파일:", TIMETABLE, f"({TIMETABLE.stat().st_size // 1024} KB)")