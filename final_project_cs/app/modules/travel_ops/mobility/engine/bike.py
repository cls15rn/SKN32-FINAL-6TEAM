# modules/mobility/bike.py — 따릉이 조회 계층 (규칙 v0.7 · 22번 방 · 2026-09-20)
#
# 둘로 나뉜다. 판정은 여기서 하지 않는다(verify_time.py verify_leg_bike 가 한다).
#   BikeStations  운영 대여소 목록(processed/mobility/bike_stations_v1.jsonl · 17번 산출). 정적 속성만. 확정(위치) · 추정(운영방식).
#   BikeLive      실시간 거치 수. bikeList?stationId= **단건** 조회 → parkingBikeTotCnt. 응답은 값만 쓰고 버린다 —
#                 여기서도 캐시하지 않고, 돌려주는 dict 에는 개수와 조회 시각만 있다(원 응답 보관 없음).
#                 소스 셋: 'env'(ACOP_SEOUL_OPENAPI_KEY 로 실제 호출) · dict 픽스처(회귀 — 케이스 bike_live) · None(조회 불가).
#   BikeRouter    자전거·도보 경로의 거리·소요(요약만 · 좌표는 버린다). ☆101(2026-10-05 · 합치기) 팀장 판대로 되살렸다 —
#                 경로는 저장소 안 도로 그래프 위 파이썬 길찾기(graph_router.GraphRouter)가 낸다. 99(10/4)에서 「자전거 경로 계산
#                 없음 · 승차 소요 근거없음」으로 지웠던 것을 본인 10/5 결정(팀장 판에 있으면 유지)으로 뒤집었다. **경로 서버는
#                 부르지 않는다**(GraphHopper 호출 코드 0 은 그대로) — `router` 자리에 오는 것은 로컬 길찾기나 시험 대역뿐이다.
#                 걷기 길찾기(장소↔역·정류장 · 장소↔장소)도 이 객체의 `router` 를 꺼내 쓴다(팀장 통로 그대로).
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
    """bikeList stationId 단건 조회. get() 은 {'available': int, 'checked_at': str, 'source_id': str} 또는 None(조회 못 함).

    ☆`[2026-09-29 문제목록 #28]` 조회 못 한 **까닭**을 `last_error` 에 남긴다 — no_key · network · http · bad_response ·
      not_found. 앞 판은 모든 예외를 삼켜 키 오류와 통신 실패를 가를 수 없었다. 통신·응답 외의 예외(코드 결함)는 삼키지 않는다.
    ☆`[2026-09-29 문제목록 #50]` 제공처(서울 열린데이터광장 8088)가 **http 만 받는다** — https 세 표기
      (8088·443·포트 생략)가 모두 연결되지 않았다(2026-09-29 실측). 그래서 키를 담은 주소를 **오류 메시지·기록에
      싣지 않는다**(last_error 에는 종류와 상태 코드만). 전송 구간 노출은 제공처 제약으로 남는다.
    """
    URL = "http://openapi.seoul.go.kr:8088/{key}/json/bikeList/1/5/{sid}"
    KEY_NAME = "ACOP_SEOUL_OPENAPI_KEY"

    def __init__(self, fixture=None, key=None, timeout=5.0):
        self.fixture = fixture          # {'checked_at': ..., 'counts': {stationId: n}} — 회귀용
        self.key = key
        self.timeout = timeout
        self.calls = 0
        self.last_error = None          # 마지막 조회 실패의 종류(#28) — 키가 든 주소는 싣지 않는다(#50)

    @classmethod
    def from_env(cls):
        """명령줄 관례 — 팀 양식 이름 `ACOP_SEOUL_OPENAPI_KEY` 하나만 본다(99 · 옛 이름 SEOUL_OPENAPI_KEY 는 안 읽는다).
        읽는 순서는 수집 쪽 `datasets/mobility/scripts/_paths.api_key()` 와 같다: 환경변수 → final_project_cs/.env →
        final_project_cs/.env.apikeys. 서버는 이 길을 타지 않는다(설정 값 seoul_key 를 넘긴다 · #48). 값은 어디에도 찍지 않는다."""
        from dotenv import dotenv_values

        from .paths import REPO_ROOT
        cs_root = REPO_ROOT / "final_project_cs"
        merged = {}
        for f in (cs_root / ".env", cs_root / ".env.apikeys"):      # 뒤 파일이 이긴다 — 빈 값으로 적혀 있으면 빈 값이 된다(_paths 와 같다)
            if f.exists():
                merged.update(dotenv_values(f))
        k = (os.environ.get(cls.KEY_NAME) or merged.get(cls.KEY_NAME) or "").strip()
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
        self.last_error = None
        if not self.key:
            self.last_error = {"kind": "no_key"}
            return None
        import urllib.error
        import urllib.request
        try:
            self.calls += 1
            with urllib.request.urlopen(self.URL.format(key=self.key, sid=station_id), timeout=self.timeout) as f:
                doc = json.loads(f.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:           # URLError 보다 먼저 — HTTPError 는 URLError 의 자식이다
            self.last_error = {"kind": "http", "status": ex.code}
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            self.last_error = {"kind": "network", "error": type(ex).__name__}
            return None
        except (ValueError, UnicodeDecodeError) as ex:   # JSON 이 아니다
            self.last_error = {"kind": "bad_response", "error": type(ex).__name__}
            return None
        result = (doc.get("rentBikeStatus") or {}).get("RESULT") or doc.get("RESULT") or {}
        rows = ((doc.get("rentBikeStatus") or {}).get("row")) or []
        row = next((r for r in rows if r.get("stationId") == station_id), None)
        if row is None:
            # 인증 오류(INFO-100 등)와 「그 대여소 없음」을 가른다 — 응답의 결과 코드만 남긴다
            code = result.get("CODE")
            self.last_error = {"kind": "not_found" if code in (None, "INFO-000", "INFO-200") else "api_error",
                               "code": code}
            return None
        at = _dt.datetime.now().astimezone().isoformat(timespec="minutes")
        # ★ 원 응답(doc·row)은 여기서 버린다. 남기는 것은 개수와 시각뿐.
        try:
            n = int(row.get("parkingBikeTotCnt"))
        except (TypeError, ValueError):
            self.last_error = {"kind": "bad_response", "error": "parkingBikeTotCnt"}
            return None
        return {"available": n, "checked_at": at, "source_id": f"seoul_bikeList@{at}"}


class BikeRouter:
    """bike/foot 소요 — **car.py 의 라우터(로컬 길찾기 graph_router.GraphRouter · 시험 대역 FixtureRouter · NoRouter)를 그대로 쓴다.**
    새 클라이언트를 만들지 않는다. ☆101 경로 서버 클라이언트는 없다(99 에서 삭제 · 팀장 판에 남아 있던 것도 지움).

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
        self.last_error = None          # 마지막 경로 조회 실패의 종류(#28)

    @staticmethod
    def key(profile, lat1, lng1, lat2, lng2):
        return f"{profile}|{lat1:.5f},{lng1:.5f}|{lat2:.5f},{lng2:.5f}"

    @property
    def source_id(self):
        # ☆`[2026-10-04]` 파이썬 로컬 라우터는 자기 자료 식별자를 가진다(근거 칸이 다른 출처로 찍히지 않게)
        return getattr(self.router, "source_id", None) or f"osm_bike_graph@{self.pbf_date}"

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
        self.last_error = None
        if self.router is None:
            self.last_error = {"kind": "no_router"}
            return None
        from .car import RouterDown
        try:
            self.calls += 1
            doc = self.router.route((lng1, lat1), (lng2, lat2), profile=profile)   # car.py 와 같은 (lng, lat) 순서
        except RouterDown as ex:               # 라우터에 못 닿음 — 자전거는 소요 근거없음으로 낸다(죽지 않는다)
            self.last_error = {"kind": "router_down", "error": str(ex)[:120]}
            return None
        except (OSError, ValueError) as ex:    # 통신·응답 해석 실패. 그 밖의 예외(코드 결함)는 삼키지 않는다(#28)
            self.last_error = {"kind": "router_error", "error": type(ex).__name__}
            return None
        paths = (doc or {}).get("paths") or []
        if not paths:
            self.last_error = {"kind": "no_path"}
            return None
        p = paths[0]
        # ☆`[2026-09-29 문제목록 #10]` 거리·시간이 빠진 응답을 0 으로 채우지 않는다 — 앞 판은 {"paths":[{}]} 를
        #   「0 m · 0 초 경로」로 만들었다. 빠졌으면 근거없음이다.
        try:
            dist, tms = float(p["distance"]), float(p["time"])
        except (KeyError, TypeError, ValueError):
            self.last_error = {"kind": "bad_response", "error": "distance/time 없음"}
            return None
        if dist < 0 or tms < 0:
            self.last_error = {"kind": "bad_response", "error": "음수 거리·시간"}
            return None
        out = {"distance_m": round(dist, 1),
               "time_s": int(round(tms / 1000)), "basis": getattr(self.router, "basis", "router"),
               "source_id": self.source_id}
        q = p.get("quality")
        if q:                                          # 로컬 길찾기가 낸 품질 표시(낙관 가능 · 접근 거리 · 큰길 비율)
            out["quality"] = q
            out["optimistic"] = bool(q.get("optimistic"))
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
