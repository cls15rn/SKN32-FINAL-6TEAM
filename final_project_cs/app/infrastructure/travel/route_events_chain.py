# -*- coding: utf-8 -*-
"""경로 사건 소스가 쓰는 **대상 표**(이동 계산기 자료 파일) · 대상 표기 규칙.

감시 루프(`trip_watch.py`)·출발 안내(`trip_reminders.py`)는 경로 사건 소스 하나에 두 가지를 묻는다 — `affecting(targets)`(이 대상들에
지금 걸린 사건)와 `unsupported(targets)`(이 소스가 못 보는 대상). 대상 머리: `도로:…`(UTIC·ITS) · `<노선>:<역>`(지하철 알림) ·
`버스:<노선>`(TOPIS 공지).

☆`[2026-10-05 합치기]` 소스를 하나로 합치는 일은 팀장 `subway_notice.CompositeRouteEvents` 가 한다(`base.build_travel_sources`). 이
  파일에 있던 `CombinedRouteEvents`(이동 담당 10/1)는 같은 일을 따로 한 것이라 내렸다. 남은 것은 버스 소스(`topis_notice.py`)가 쓰는
  정류장 표(`MobilityTables.stop_table`)와 대상 표기 함수 둘이다. `station_table`(역 코드 → 대상)은 지금 쓰는 소스가 없다(내린
  `seoulmetro_alert.py` 가 썼다) — 표는 남겨 둔다.
"""
from __future__ import annotations

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


__all__ = ["MobilityTables", "line_name", "station_name"]
