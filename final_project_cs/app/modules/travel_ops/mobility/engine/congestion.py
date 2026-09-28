# -*- coding: utf-8 -*-
"""혼잡도 조회 — @ 부품 (규칙 v0.8 · 39번 방 · rules congestion.levels).

판정 입력이 아니라 **최악값 부품**이다. 33번 방이 「후보 표시 +@분의 근거로만」이라고 넘긴 것을
v0.8 이 최악값 계산에 넣는다 — 매우혼잡(≥100%) + 조건(luggage·infant·elderly)이면 최악 승차 대기에
배차 1회를 더한다(levels.매우혼잡.action = board_wait_extra_headway). 극심(≥130%)은 경고만.

소스 둘 · 모양은 같다(`congestion_v1.jsonl` 1~8호선 · `congestion_line9_v1.jsonl` 9호선 + service).
  · 1~8호선 요일 3종(weekday/saturday/sunday) — 공휴일은 sunday 로 본다(rules congestion.day_type.공휴일_처리)
  · 9호선 요일 2종(weekday/holiday) — 토·일·공휴일 전부 holiday
  · 30분 슬롯 05:30~00:30. 24시 넘김은 시간표와 같은 분 자(24:30 = 1470)
  · congestion=null 은 0% 가 아니라 근거없음(reason) — 조회 결과 None
Timetable 처럼 케이스에 나오는 (노선, 역)만 올린다.
"""
import json
from pathlib import Path

LEVELS = ("여유", "보통", "혼잡", "매우혼잡", "극심")


class Congestion:
    def __init__(self):
        self.by_key = {}        # (line, station, dir, day_type, slot_min) → row
        self.rows = 0
        self.source_ids = set()
        self.files = []

    @classmethod
    def load(cls, paths, wanted=None):
        """paths: 파일 경로 목록(없는 파일은 건너뛴다). wanted: {(line, station)} 또는 None(전부)."""
        cg = cls()
        for p in paths or []:
            p = Path(p)
            if not p.exists():
                continue
            cg.files.append(str(p))
            with open(p, encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    r = json.loads(raw)
                    if wanted is not None and (r.get("line"), r.get("station_nm")) not in wanted:
                        continue
                    if r.get("service") not in (None, "local"):
                        continue                      # 9호선 급행 열은 안 쓴다 — 통합 시간표에 급행 구분이 없다(33번)
                    hh, mm = r["slot"].split(":")
                    slot = int(hh) * 60 + int(mm)
                    if slot < 5 * 60:                 # 00:00·00:30 → 24:00·24:30 (시간표와 같은 자)
                        slot += 24 * 60
                    k = (r["line"], r["station_nm"], r["dir"], r["day_type"], slot)
                    if k in cg.by_key and r.get("branch"):
                        continue                      # 본선 행이 먼저다 — 지선(branch)은 본선 값이 없을 때만
                    cg.by_key[k] = r
                    cg.rows += 1
                    cg.source_ids.add(r.get("source_id"))
        return cg or None

    def __bool__(self):
        return bool(self.by_key)

    @staticmethod
    def day_key(day_type, date, is_holiday=False):
        """시간표 요일형(weekday/holiday) + 날짜 + 공휴일 여부 → 혼잡도 요일 키 후보(우선순위 순).

        1~8호선은 3종(weekday/saturday/sunday) — 공휴일은 **날짜가 토요일이어도** sunday 로 본다
        (rules congestion.day_type.공휴일_처리 · GPT 대조 2026-09-24 #2: 토요일과 겹친 공휴일이 saturday 로 새던 결함).
        9호선은 2종이라 holiday 를 뒤에 둔다.
        """
        if day_type == "weekday":
            return ["weekday"]
        wd = date.weekday() if date is not None else 6
        three = "saturday" if (wd == 5 and not is_holiday) else "sunday"
        return [three, "holiday"]

    def lookup(self, line, station, dir, day_type, date, minute, levels, is_holiday=False):
        """(값, 등급명, 행) — 없거나 근거없음이면 None."""
        if minute is None:
            return None
        slot = (minute // 30) * 30
        for dk in self.day_key(day_type, date, is_holiday):
            r = self.by_key.get((line, station, dir, dk, slot))
            if r is not None:
                break
        else:
            return None
        v = r.get("congestion")
        if v is None:
            return None
        return v, level_of(v, levels), r


def level_of(v, levels):
    """rules congestion.levels 의 gte/lt 로 등급명을 정한다."""
    for name in LEVELS:
        spec = levels.get(name)
        if not isinstance(spec, dict):
            continue
        lo, hi = spec.get("gte"), spec.get("lt")
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return name
    return None
