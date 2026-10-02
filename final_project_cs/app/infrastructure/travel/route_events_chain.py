# -*- coding: utf-8 -*-
"""경로 사건 소스 **합치기** — 도로(UTIC) · 지하철(서울교통공사 알림) · 버스(TOPIS 예고 공지).

감시 루프(`trip_watch.py`)·출발 안내(`trip_reminders.py`)는 경로 사건 소스 **하나**에 두 가지를 묻는다 —
`affecting(targets)`(이 대상들에 지금 걸린 사건)와 `unsupported(targets)`(이 소스가 못 보는 대상). 그 자리에
이것을 꽂는다(`base.build_travel_sources`). 대상 머리로 나눠 맡긴다:

    도로:…        → UTIC(`UticRouteEvents`)            못 읽으면 None
    <노선>:<역>   → 지하철 알림(`SeoulMetroAlerts`)     못 읽으면 None
    버스:<노선>   → TOPIS 공지(`TopisNotices`)          못 읽으면 None

★**맡을 소스가 없는 대상은 `unsupported()`** 로 낸다 — 「사건 없음」과 다르다. 합치면서 이 구분이 사라지면 지하철·버스
  사고가 없는 것처럼 읽힌다(전에 UTIC 하나만 꽂혀 있을 때 지하철·버스는 전부 「확인 못 한 대상」이었다).
★**맡은 소스가 못 읽으면 `None`** — 감시 루프가 그 이동 항목을 치명으로 남긴다(결정 15). 다른 소스가 답한
  것으로 메우지 않는다.
★그 대상 무리를 맡은 소스에게만 묻는다 — 버스를 안 쓰는 이동 항목은 TOPIS 가 못 읽어도 치명이 되지 않는다.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROAD, BUS = "도로:", "버스:"

#: ★대상 바꾸는 표(역 코드 → `2호선:이대` · 정류장 번호 → 지나는 노선)는 **이동 계산기 자료 파일**에만 있다
#:  (`<mobility_data_dir>/travel/processed/mobility/` 의 station_coords.json · bus_stops_v1.jsonl). 바닥(infrastructure)은
#:  도메인 모듈을 import 하지 않고(INV-CS-ARCH-002), 팀 모듈은 바닥을 import 하지 않으므로(tool discipline) **파일을 직접**
#:  읽는다. 자료 폴더가 비었거나 파일이 없으면 표 없음 → 지하철·버스 대상은 전부 「확인 못 함」.
#: ★표기 바꾸기는 이동 계산기(`mobility/engine/options.line_name`·`station_name`)와 같은 규칙이다 — 둘이 어긋나면
#:  사건을 못 잡는다. 이동 시험(`test_bus_stop_skip_v1.py`)이 두 함수의 결과가 같은지 잠근다.
LINE_OFFICIAL = {"경의선": "경의중앙선", "인천선": "인천1호선", "우이신설경전철": "우이신설선", "김포도시철도": "김포골드라인"}
COVERED_LINES = frozenset(f"{n}호선" for n in range(1, 9))
COVERED_OPERATOR = "서울교통공사"


def line_name(line: str) -> str:
    if line and len(line) == 4 and line.endswith("호선") and line[:2].isdigit():
        return f"{int(line[:2])}호선"
    return LINE_OFFICIAL.get(line, line)


def station_name(nm: str) -> str:
    s = str(nm).split("(")[0].strip()
    if s == "서울역":
        return s
    return s[:-1] if s.endswith("역") and len(s) > 1 else s


class MobilityTables:
    """이동 계산기 자료 파일에서 대상 표를 만든다(처음 쓸 때 한 번 · 이후 재사용)."""

    def __init__(self, data_dir: str | None) -> None:
        self.data_dir = data_dir or ""
        self._stations: dict[str, tuple[str, bool]] | None = None
        self._stops: Any = None

    def _dir(self) -> Path | None:
        if not self.data_dir:
            return None
        base = Path(self.data_dir)
        processed = base if base.name == "processed" else base / "travel" / "processed"
        return processed / "mobility"

    def station_table(self) -> dict[str, tuple[str, bool]] | None:
        if self._stations is None:
            d = self._dir()
            path = d / "station_coords.json" if d else None
            if path is None or not path.exists():
                return None
            doc = json.loads(path.read_text(encoding="utf-8"))
            table = {}
            for rec in (doc.get("stations") or {}).values():
                cd = rec.get("station_cd")
                if not cd:
                    continue
                line = line_name(rec["line"])
                table[str(cd)] = (f"{line}:{station_name(rec['station_nm'])}",
                                  line in COVERED_LINES and rec.get("operator") == COVERED_OPERATOR)
            self._stations = table
        return self._stations

    def stop_table(self) -> Any:
        if self._stops is None:
            d = self._dir()
            path = d / "bus_stops_v1.jsonl" if d else None
            if path is None or not path.exists():
                return None
            by_ars: dict[str, set[str]] = {}
            routes: set[str] = set()
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    routes.add(row["route_nm"])
                    if row.get("ars_id"):
                        by_ars.setdefault(str(row["ars_id"]), set()).add(row["route_nm"])
            self._stops = SimpleNamespace(routes_by_ars=by_ars, routes=routes)
        return self._stops


def _group(target: str) -> str:
    if target.startswith(ROAD):
        return "road"
    if target.startswith(BUS):
        return "bus"
    return "subway"


class CombinedRouteEvents:
    name = "route_events_combined"

    def __init__(self, *, road: Any = None, subway: Any = None, bus: Any = None) -> None:
        self.sources = {"road": road, "subway": subway, "bus": bus}
        if not any(self.sources.values()):
            raise ValueError("CombinedRouteEvents 에 소스가 하나도 없다")

    def affecting(self, targets: list[str], at: datetime | None = None
                  ) -> dict[str, dict[str, Any]] | None:
        found: dict[str, dict[str, Any]] = {}
        for group, source in self.sources.items():
            mine = [t for t in targets if _group(t) == group]
            if not mine or source is None:
                continue
            got = source.affecting(mine, at) if at is not None else source.affecting(mine)
            if got is None:
                return None
            found.update({t: e for t, e in got.items() if t in mine})
        return found

    def unsupported(self, targets: list[str]) -> list[str]:
        out = []
        for group, source in self.sources.items():
            mine = [t for t in targets if _group(t) == group]
            if not mine:
                continue
            if source is None:
                out += mine
                continue
            check = getattr(source, "unsupported", None)
            # ★unsupported() 가 없는 소스는 맡은 대상을 다 본다고 말하는 셈이다 — 그런 소스는 받지 않는다
            if not callable(check):
                raise TypeError(f"{type(source).__name__} 에 unsupported() 가 없다 — 못 보는 대상이 「사건 없음」이 된다")
            out += [t for t in check(mine) if t in mine]
        return [t for t in targets if t in out]


__all__ = ["CombinedRouteEvents", "MobilityTables", "line_name", "station_name"]
