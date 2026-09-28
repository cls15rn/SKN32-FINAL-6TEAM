# mobility_scripts/collect/tago_retry_empty.py — 완료 처리됐지만 행이 0개인 조합을 한 번 더 받아 "실패"와 "진짜 없음"을 가른다
# 실행: 저장소 루트에서  python mobility_scripts/collect/tago_retry_empty.py
# 배경: v1 수집 스크립트는 HTTP 실패·형식 이상도 done 에 넣었다. 2호선 평일 건대입구·잠실나루가 0건인 것은 실패 쪽이 의심된다.
# 호출 ≈ 빈 조합 수(수도권만, 토요일 02 제외 시 약 500). 결과: 행이 오면 jsonl 에 추가, 안 오면 tago_empty_confirmed.json 에 기록.
import os, json, time, requests
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY

KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "https://apis.data.go.kr/1613000/SubwayInfo"
COMMON = {"serviceKey": KEY, "_type": "json", "numOfRows": 1000, "pageNo": 1}
KST = timezone(timedelta(hours=9))
ID_MAP = RAW_MOBILITY / "tago_station_ids.json"
TIMETABLE = RAW_MOBILITY / "tago_timetable.jsonl"
DONE = RAW_MOBILITY / "tago_timetable_done.json"
CONFIRMED = RAW_MOBILITY / "tago_empty_confirmed.json"
EXCLUDE_PREFIX = ("MTRBS", "MTRDG", "MTRDJ", "MTRGJ", "MTRIAM")
SKIP_DAILY = {"02"}   # 수도권 TAGO 는 토요일(02)이 비어 있고 토·일·공휴일 = 03 이다 (우이신설만 02 제공). 재시도 대상에서 뺀다


def call(**p):
    """(items, status) status ∈ ok / http / format / net"""
    for attempt in range(5):
        try:
            r = requests.get(f"{BASE}/GetSubwaySttnAcctoSchdulList", params={**COMMON, **p}, timeout=30)
        except requests.RequestException:
            time.sleep(2 + attempt * 2); continue
        if r.status_code == 200:
            try:
                body = r.json()["response"]["body"]
            except (KeyError, ValueError):
                return [], "format:" + r.text[:80]
            items = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
            return ([items] if isinstance(items, dict) else list(items)), "ok"
        if "PER_SECOND" in r.text or "SERVICETIMEOUT" in r.text or r.status_code >= 500:
            time.sleep(2 + attempt * 2); continue
        return [], f"http:{r.status_code}"
    return [], "net"


id_map = json.loads(ID_MAP.read_text(encoding="utf-8"))
targets = {it["subwayStationId"]: (nm, it.get("subwayRouteName")) for nm, its in id_map.items() for it in its}
done = set(json.loads(DONE.read_text(encoding="utf-8")))
have = set()
with TIMETABLE.open(encoding="utf-8") as f:
    for line in f:
        try:
            r = json.loads(line); have.add("|".join((r["station_id"], r["daily_type"], r["updown"])))
        except (ValueError, KeyError):
            pass
empty = sorted(k for k in done - have
               if k.split("|")[1] not in SKIP_DAILY and not k.startswith(EXCLUDE_PREFIX) and k.split("|")[0] in targets)
print(f"빈 조합 {len(done - have)}개 중 재시도 대상 {len(empty)}개 (토요일·수도권 밖 제외)")

confirmed = json.loads(CONFIRMED.read_text(encoding="utf-8")) if CONFIRMED.exists() else {}
recovered = 0
with TIMETABLE.open("a", encoding="utf-8") as f:
    for n, key in enumerate(empty, 1):
        sid, d, u = key.split("|")
        items, status = call(subwayStationId=sid, dailyTypeCode=d, upDownTypeCode=u)
        ts = datetime.now(KST).isoformat(timespec="seconds")
        if items:
            f.write("\n".join(json.dumps({
                "station_id": it.get("subwayStationId", sid), "station_nm": it.get("subwayStationNm"),
                "route_id": it.get("subwayRouteId"),
                "end_station_id": it.get("endSubwayStationId"), "end_station_nm": it.get("endSubwayStationNm"),
                "dep_time": it.get("depTime"), "arr_time": it.get("arrTime"),
                "daily_type": it.get("dailyTypeCode", d), "updown": it.get("upDownTypeCode", u),
                "fetched_at": ts, "source": "data.go.kr/1613000/SubwayInfo",
            }, ensure_ascii=False) for it in items) + "\n"); f.flush()
            recovered += 1
            confirmed.pop(key, None)
            print(f"  복구 {targets[sid][1]} {targets[sid][0]} {d}/{u}: {len(items)}건")
        else:
            confirmed[key] = {"station": targets[sid][0], "route": targets[sid][1], "status": status, "checked_at": ts}
        if n % 50 == 0:
            CONFIRMED.write_text(json.dumps(confirmed, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"  … {n}/{len(empty)}")
        time.sleep(0.12)
CONFIRMED.write_text(json.dumps(confirmed, ensure_ascii=False, indent=1), encoding="utf-8")
by_status = {}
for v in confirmed.values():
    by_status[v["status"].split(":")[0]] = by_status.get(v["status"].split(":")[0], 0) + 1
print(f"복구 {recovered}개, 확인된 빈 조합 {len(confirmed)}개 {by_status} → {CONFIRMED.name}")
print("status 가 ok 인 것만 '진짜 없음'. http/format/net 은 다시 돌리면 됨.")
