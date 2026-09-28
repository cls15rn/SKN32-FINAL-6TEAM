# modules/mobility/bike.py — 따릉이 조회 계층 (규칙 v0.7 · 22번 방 · 2026-09-20)
#
# 셋으로 나뉜다. 판정은 여기서 하지 않는다(verify_time.py verify_leg_bike 가 한다).
#   BikeStations  운영 대여소 목록(processed/mobility/bike_stations_v1.jsonl · 17번 산출). 정적 속성만. 확정(위치) · 추정(운영방식).
#   BikeLive      실시간 거치 수. bikeList?stationId= **단건** 조회 → parkingBikeTotCnt. 응답은 값만 쓰고 버린다 —
#                 여기서도 캐시하지 않고, 돌려주는 dict 에는 개수와 조회 시각만 있다(원 응답 보관 없음).
#                 소스 셋: 'env'(SEOUL_OPENAPI_KEY 로 실제 호출) · dict 픽스처(회귀 — 케이스 bike_live) · None(조회 불가).
#   BikeRouter    21번 car.py 의 라우터 객체(GraphHopperClient·FixtureRouter)를 bike·foot 프로파일로 부르는 얇은 껍데기.
#                 응답에서 **거리·시간만** 받고 형상은 버린다(경로 비저장 원칙). 자전거 픽스처(거리·시간 요약)가 있으면 그것을 먼저 본다.
#
# ★ 이 모듈은 규칙 파일을 읽지 않는다 — 규칙값(반경·요금·연령)은 판정기가 넘긴다. fare()·party_excluded() 만 규칙 dict 를 받는다.
import json, os, math, datetime as _dt
from pathlib import Path

from .geo import meters


class BikeStations:
    def __init__(self, rows):
        self.rows = [r for r in rows if r.get("lat") is not None and r.get("lon") is not None]
        self.by_id = {r["stationId"]: r for r in self.rows}
        self.checked_at = self.rows[0].get("checked_at") if self.rows else None
        self.source_id = self.rows[0].get("source_id") if self.rows else "seoul_bike_station"

    @classmethod
    def load(cls, path=None):
        if path is None:
            from .paths import PROCESSED
            path = PROCESSED / "mobility" / "bike_stations_v1.jsonl"
        p = Path(path)
        if not p.exists():
            return None
        return cls([json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()])

    def stations_near(self, lat, lng, within_m):
        """좌표 반경 안 운영 대여소 — (직선 m, 행) 가까운 순."""
        out = []
        for r in self.rows:
            d = meters(lat, lng, r["lat"], r["lon"])
            if d <= within_m:
                out.append((d, r))
        return sorted(out, key=lambda t: t[0])

    @staticmethod
    def qr_ok(row):
        """외국인 비회원이 빌릴 수 있는 운영방식인가. None = 미상(신설) — 허용하되 판정기가 등급을 내린다."""
        m = row.get("mode")
        if m is None:
            return None
        return "QR" in m


class BikeLive:
    """bikeList stationId 단건 조회. get() 은 {'available': int, 'checked_at': str, 'source_id': str} 또는 None(조회 못 함)."""
    URL = "http://openapi.seoul.go.kr:8088/{key}/json/bikeList/1/5/{sid}"

    def __init__(self, fixture=None, key=None, timeout=5.0):
        self.fixture = fixture          # {'checked_at': ..., 'counts': {stationId: n}} — 회귀용
        self.key = key
        self.timeout = timeout
        self.calls = 0

    @classmethod
    def from_env(cls):
        k = os.environ.get("SEOUL_OPENAPI_KEY")
        return cls(key=k) if k else None

    @classmethod
    def from_fixture(cls, doc):
        if not doc:
            return None
        return cls(fixture={"checked_at": doc.get("checked_at"), "counts": dict(doc.get("counts") or {})})

    def get(self, station_id):
        if self.fixture is not None:
            n = self.fixture["counts"].get(station_id)
            if n is None:
                return None
            at = self.fixture.get("checked_at") or "fixture"
            return {"available": int(n), "checked_at": at, "source_id": f"seoul_bikeList@{at}"}
        if not self.key:
            return None
        try:
            import urllib.request
            self.calls += 1
            with urllib.request.urlopen(self.URL.format(key=self.key, sid=station_id), timeout=self.timeout) as f:
                doc = json.loads(f.read().decode("utf-8"))
        except Exception:
            return None
        rows = ((doc.get("rentBikeStatus") or {}).get("row")) or []
        row = next((r for r in rows if r.get("stationId") == station_id), None)
        if row is None:
            return None
        at = _dt.datetime.now().astimezone().isoformat(timespec="minutes")
        # ★ 원 응답(doc·row)은 여기서 버린다. 남기는 것은 개수와 시각뿐.
        try:
            n = int(row.get("parkingBikeTotCnt"))
        except (TypeError, ValueError):
            return None
        return {"available": n, "checked_at": at, "source_id": f"seoul_bikeList@{at}"}


class BikeRouter:
    """bike/foot 소요 — **21번 car.py 의 라우터(GraphHopperClient · FixtureRouter · NoRouter)를 그대로 쓴다.** 새 클라이언트를 만들지 않는다.

    router   : car.make_router(spec) 이 준 객체. route((lng,lat),(lng,lat), profile=...) 을 부르고 응답에서 **거리·시간만** 남긴다(형상은 버린다).
               None 이면 호출하지 않는다.
    fixture  : {'<profile>|<lat1>,<lng1>|<lat2>,<lng2>': {'distance_m':..,'time_s':..}} (좌표 5자리) — 자전거 회귀용 요약값. 라우터보다 먼저 본다.
    pbf_date : source_id 'osm_bike_graph@<pbf_date>' 에 쓴다.
    record   : dict 를 주면 실제 호출 결과 요약을 모은다(픽스처 기록용 · 형상 없음).
    """

    def __init__(self, router=None, fixture=None, pbf_date=None, record=None):
        self.router = router
        self.fixture = fixture or {}
        self.pbf_date = pbf_date or "unknown"
        self.record = record
        self.calls = 0

    @staticmethod
    def key(profile, lat1, lng1, lat2, lng2):
        return f"{profile}|{lat1:.5f},{lng1:.5f}|{lat2:.5f},{lng2:.5f}"

    @property
    def source_id(self):
        return f"osm_bike_graph@{self.pbf_date}"

    @property
    def url(self):
        return getattr(self.router, "url", None)

    def available(self):
        return self.router is not None or bool(self.fixture)

    def route(self, profile, lat1, lng1, lat2, lng2):
        k = self.key(profile, lat1, lng1, lat2, lng2)
        if k in self.fixture:
            v = self.fixture[k]
            return {"distance_m": v["distance_m"], "time_s": v["time_s"], "basis": "fixture",
                    "source_id": v.get("source_id") or self.source_id}
        if self.router is None:
            return None
        try:
            self.calls += 1
            doc = self.router.route((lng1, lat1), (lng2, lat2), profile=profile)   # car.py 와 같은 (lng, lat) 순서
        except Exception:                      # RouterDown 포함 — 자전거는 소요 근거없음으로 낸다(죽지 않는다)
            return None
        paths = (doc or {}).get("paths") or []
        if not paths:
            return None
        p = paths[0]
        out = {"distance_m": round(float(p.get("distance", 0)), 1),
               "time_s": int(round(float(p.get("time", 0)) / 1000)), "basis": "graphhopper",
               "source_id": self.source_id}
        if self.record is not None:
            self.record[k] = {"distance_m": out["distance_m"], "time_s": out["time_s"],
                              "source_id": out["source_id"]}
        return out       # ★ doc(형상 포함)은 여기서 버린다


def party_excluded(party, rules_bike):
    """만 12세 이하 동반이면 (사유) 를, 아니면 None."""
    ex = rules_bike["exclude"]["age_max_excluded"]
    amax = ex["value"]
    party = party or {}
    if party.get("infant") or party.get("child_under_13"):
        return f"만 {amax}세 이하 동반(party.{'infant' if party.get('infant') else 'child_under_13'}) — 따릉이 이용 불가"
    ages = party.get("ages") or []
    if any(isinstance(x, (int, float)) and x <= amax for x in ages):
        return f"만 {amax}세 이하 동반(party.ages) — 따릉이 이용 불가"
    return None


def fare(rules_fare, ride_min):
    """소요(대여~반납, 분)에 대한 이용권·초과요금. 값은 규칙에서만 온다."""
    passes = rules_fare["passes"]["value"]
    base = rules_fare["base_ride_min"]["value"]
    ot = rules_fare["overtime"]["value"]
    m = math.ceil(ride_min)
    # 1회 대여 기본시간은 이용권과 무관하게 base 분 — 초과분은 5분당 200원. 이용권은 가장 싼 것(1h).
    cheapest = min(passes, key=lambda p: p["won"])
    over = max(0, m - base)
    over_won = math.ceil(over / ot["per_min"]) * ot["won"] if over else 0
    return {"pass": cheapest["name"], "pass_won": cheapest["won"], "ride_min": m,
            "overtime_min": over, "overtime_won": over_won, "total_won": cheapest["won"] + over_won}
