# modules/mobility/bike.py — 따릉이 조회 계층 (규칙 v0.7 · 22번 방 · 2026-09-20)
#
# 둘로 나뉜다. 판정은 여기서 하지 않는다(verify_time.py verify_leg_bike 가 한다).
#   BikeStations  운영 대여소 목록(processed/mobility/bike_stations_v1.jsonl · 17번 산출). 정적 속성만. 확정(위치) · 추정(운영방식).
#   BikeLive      실시간 거치 수. bikeList?stationId= **단건** 조회 → parkingBikeTotCnt. 응답은 값만 쓰고 버린다 —
#                 여기서도 캐시하지 않고, 돌려주는 dict 에는 개수와 조회 시각만 있다(원 응답 보관 없음).
#                 소스 셋: 'env'(ACOP_SEOUL_OPENAPI_KEY 로 실제 호출) · dict 픽스처(회귀 — 케이스 bike_live) · None(조회 불가).
#   ☆99(2026-10-04) 자전거 **경로 계산은 없다** — 경로 서버(bike·foot 프로파일)를 부르던 조회 계층을 지웠다. 승차 소요는
#                 근거없음으로 낸다(판정기가 「자전거 경로 계산 없음」이라 말한다 — 다른 수단으로 몰래 바꾸지 않는다).
#                 살릴 때는 전용 경로 계산을 만들지 않고 보행 경로 거리 ÷ 자전거 평균 속도(단위 환산)(본인 10/4 · 보행 그래프 뒤).
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
