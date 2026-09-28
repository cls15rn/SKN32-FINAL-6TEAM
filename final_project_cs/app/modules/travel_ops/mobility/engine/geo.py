# modules/mobility/geo.py — 좌표 조회와 거리. 대안 열거가 '역 앞 정류장'을 찾을 때 쓴다.
# 소스: processed/mobility/station_coords.json (지하철 793역)
#       processed/mobility/bus_stops_v1.jsonl 의 lat/lng (버스 정류장 906개)
#
# ★ 55 ④(2026-09-28) — 동명이역. 57 이 좌표를 노선+역명으로 다시 붙인 뒤 「같은 역명이면 좌표가 같다」는 사실이 아니다
#   (경의선 양평 = 양평군 · 5호선 양평 = 영등포구 53.6 km · 신촌 2호선 ↔ 경의선 702 m). 같은 역명의 노선별 좌표를
#   규칙 station_names.동명이역_좌표차_m 로 묶어 「물리적 역」(역명 + 노선군)을 만들고, 한 역명에 물리적 역이 둘 이상이면
#   **역명만으로는 값을 내지 않는다** — by_name 에서 빼고, resolve(역명, 노선) 로만 찾는다.
import json, math
from pathlib import Path


def meters(lat1, lng1, lat2, lng2):
    """평면 근사. 서울 규모(수십 km)에서 오차가 무시할 만하다."""
    dy = (lat2 - lat1) * 111_320
    dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def same_station(a_nm, a_lines, b_nm, b_lines):
    """두 (역명, 노선군)이 같은 물리적 역인가(55 GPT #2). 역명이 다르면 아니다 · 같아도 양쪽 노선군이 있고 겹치지 않으면
    동명이역(경의선 양평 ↔ 5호선 양평)이라 다른 역이다. 노선군이 없으면(동명이역 아님) 역명으로 본다."""
    if a_nm != b_nm:
        return False
    return not (a_lines and b_lines and not (set(a_lines) & set(b_lines)))


def _rule_ambig_m(rules=None):
    """규칙 station_names.동명이역_좌표차_m. rules 를 안 주면 기본 규칙 파일에서 읽는다(하드코딩 금지 · 27 규칙 14)."""
    if rules is None:
        from .paths import RULES_DIR
        rules = json.loads((RULES_DIR / "rules_v0.3.json").read_text(encoding="utf-8"))
    return rules["station_names"]["동명이역_좌표차_m"]["value"]


class StationCoords:
    def __init__(self, doc, ambig_m=None, rules=None):
        self.doc = doc
        self.by_key = doc.get("stations", {})
        self.built_at = doc.get("built_at") or doc.get("data_basis_date")
        self.configure(ambig_m if ambig_m is not None else _rule_ambig_m(rules))

    def configure(self, ambig_m):
        """물리적 역 묶기 — 같은 역명의 노선별 좌표를 단일 연결(거리 ≤ ambig_m)로 묶는다. 판정기가 자기 규칙 값으로 다시 부른다."""
        self.ambig_m = ambig_m
        names = {}
        for v in self.by_key.values():              # 파일 순서 그대로 — 종전 by_name(역명당 첫 키)과 같은 대표를 둔다
            names.setdefault(v["station_nm"], []).append(v)
        self._group_of = {}             # station_key → 물리적 역(dict: station_nm · lines · rep)
        self.ambiguous = {}             # 역명 → [물리적 역, …] (둘 이상일 때만)
        self.by_name = {}
        for nm, recs in names.items():
            groups = []                 # [[rec, …], …]
            for r in recs:
                if r.get("lat") is None:
                    groups.append([r])  # 좌표 없는 노선은 따로 — 어디에 붙는지 모른다
                    continue
                hit = [g for g in groups if any(x.get("lat") is not None and
                                                meters(r["lat"], r["lng"], x["lat"], x["lng"]) <= ambig_m for x in g)]
                merged = [r]
                for g in hit:
                    merged += g
                    groups.remove(g)
                groups.append(merged)
            # 좌표 없는 노선이 따로 떨어진 것은 동명이역의 근거가 아니다 — 좌표 있는 묶음이 하나면 전부 한 역으로 둔다
            with_xy = [g for g in groups if any(x.get("lat") is not None for x in g)]
            if len(with_xy) <= 1:
                groups = [[x for g in groups for x in g]]
            phys = []
            order = {id(x): i for i, x in enumerate(recs)}
            for g in groups:
                g.sort(key=lambda x: order[id(x)])
                rep = next((x for x in g if x.get("lat") is not None), g[0])
                p = {"station_nm": nm, "lines": frozenset(x["line"] for x in g), "rep": rep}
                phys.append(p)
                for x in g:
                    self._group_of[x["station_key"]] = p
            if len(phys) > 1:
                self.ambiguous[nm] = sorted(phys, key=lambda p: sorted(p["lines"]))
            else:
                self.by_name[nm] = recs[0]             # 한 역명 = 한 물리적 역 — 종전과 같이 역명당 첫 키(환승역 노선별 좌표는 ambig_m 안)
        return self

    @classmethod
    def load(cls, path=None, ambig_m=None, rules=None):
        if path is None:
            from .paths import PROCESSED
            path = PROCESSED / "mobility" / "station_coords.json"
        p = Path(path)
        if not p.exists():
            return None
        return cls(json.loads(p.read_text(encoding="utf-8")), ambig_m=ambig_m, rules=rules)

    def get(self, line, station):
        """(노선, 역) → 좌표. 그 노선 키가 없으면 역명으로 — **동명이역이면 역명으로는 안 찾는다**(None)."""
        return self.by_key.get(f"{line}|{station}") or self.by_name.get(station)

    def is_ambiguous(self, station):
        return station in self.ambiguous

    def resolve(self, station, lines=None):
        """역명(+ 노선들) → 대표 좌표 레코드. 동명이역인데 노선이 없거나 노선으로 한 역을 못 고르면 None."""
        if station not in self.ambiguous:
            return self.by_name.get(station)
        if not lines:
            return None
        want = set(lines)
        hit = [p for p in self.ambiguous[station] if p["lines"] & want]
        return hit[0]["rep"] if len(hit) == 1 else None

    def group_lines(self, rec):
        """좌표 레코드가 속한 물리적 역의 노선군. 동명이역이 아니면 그 역명의 모든 노선(좌표표 기준)."""
        p = self._group_of.get(rec.get("station_key"))
        return p["lines"] if p else frozenset([rec.get("line")])

    def phys_key(self, rec):
        """물리적 역 키 — 동명이역이면 「역명|노선+노선」, 아니면 역명."""
        nm = rec["station_nm"]
        if nm not in self.ambiguous:
            return nm
        return nm + "|" + "+".join(sorted(self.group_lines(rec)))

    def ambiguous_lines(self, station):
        """동명이역의 노선군 목록(경고 문구용). 아니면 빈 목록."""
        return ["+".join(sorted(p["lines"])) for p in self.ambiguous.get(station, [])]

    def stations_near(self, lat, lng, within_m):
        """좌표 근처의 역. **물리적 역** 단위로 (거리 m, 역 레코드) 가까운 순 — 환승역은 한 번만.
        동명이역(신촌 2호선 ↔ 경의선)은 둘 다 나올 수 있다 — 레코드의 line 으로 어느 쪽인지 안다.

        19번 방(2026-09-19): 버스 구간의 수단교체 대안이 '정류장 근처 역'을 찾을 때 쓴다.
        """
        best = {}
        for v in self.by_key.values():
            if v.get("lat") is None:
                continue
            d = meters(lat, lng, v["lat"], v["lng"])
            k = self.phys_key(v)
            if d <= within_m and (k not in best or d < best[k][0]):
                best[k] = (d, v)
        return sorted(best.values(), key=lambda t: t[0])
