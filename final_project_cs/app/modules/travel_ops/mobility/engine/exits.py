# modules/mobility/exits.py — 역 출구 좌표 조회 계층 (19번 방 · 2026-09-19)
# 소스: processed/mobility/station_exits_v1.json (OSM railway=subway_entrance · ODbL · 등급 **추정**)
# 규칙: rules.transfer.stop_station_walk
#
# ★ 쓰는 곳은 둘뿐이다 — 정류장↔역 환승 도보(지하철↔버스)와 그 근접 상한 판정.
#   시각을 확정하는 데는 쓰지 않는다(13번 인계 §4 적용 원칙). 시각 확정은 시간표로만.
# ★ 출구가 없는 역(광명·인천공항2터미널 등 7역명)은 호출 쪽이 역 좌표로 대신한다.
import json
from pathlib import Path

from .geo import meters


class StationExits:
    def __init__(self, doc):
        self.doc = doc
        self.exits = doc.get("exits", {})          # 역명 → [ {lat,lng,ref,desc,attrib,...} ]
        self.built_at = doc.get("built_at")
        self.source_id = doc.get("source_id", "osm_subway_entrance")
        self.grade = doc.get("grade", "추정")

    @classmethod
    def load(cls, path=None):
        if path is None:
            from .paths import PROCESSED
            path = PROCESSED / "mobility" / "station_exits_v1.json"
        p = Path(path)
        if not p.exists():
            return None                               # 없으면 호출 쪽이 역 좌표로 대신한다
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def bind(self, sc):
        """★ 55 ④ — 출구표는 역명 키라 동명이역(양평 5개 출구가 53 km 에 걸침 · 신촌 10개)이 한 목록이다.
        좌표표(StationCoords)의 물리적 역 묶음으로 **그 역명의 출구만** 나눠 둔다 — 출구마다 가장 가까운 물리적 역.
        파일은 안 바꾼다(34·57 산출). 동명이역이 아닌 역명은 종전 그대로."""
        self._split = {}
        if sc is None:
            return self
        for nm, groups in sc.ambiguous.items():
            parts = {p["lines"]: [] for p in groups}
            for e in self.exits.get(nm, []):
                best = None
                for p in groups:
                    for ln in p["lines"]:
                        v = sc.by_key.get(f"{ln}|{nm}")
                        if v is None or v.get("lat") is None:
                            continue
                        d = meters(e["lat"], e["lng"], v["lat"], v["lng"])
                        if best is None or d < best[0]:
                            best = (d, p["lines"])
                if best is not None:
                    parts[best[1]].append(e)
            self._split[nm] = parts
        return self

    def exits_of(self, station_nm, line=None):
        """그 역의 출구. 동명이역이면 **노선이 있어야** 그 물리적 역의 출구만 — 노선이 없으면 빈 목록(호출 쪽이 역 좌표로)."""
        parts = getattr(self, "_split", {}).get(station_nm)
        if parts is None:
            return self.exits.get(station_nm, [])
        if not line:
            return []
        return next((v for k, v in parts.items() if line in k), [])

    def nearest(self, station_nm, lat, lng, line=None):
        """그 역의 출구 중 (lat, lng) 에 가장 가까운 것. (거리 m, 출구) — 출구가 없으면 None.
        동명이역은 노선으로 물리적 역을 고른 뒤 그 출구만 본다(55 ④)."""
        best = None
        for e in self.exits_of(station_nm, line):
            d = meters(lat, lng, e["lat"], e["lng"])
            if best is None or d < best[0]:
                best = (d, e)
        return best
