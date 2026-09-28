# -*- coding: utf-8 -*-
"""버스 구간 통행시간 프로파일 — 41번 방(2026-09-25 · 규칙 v0.9).

소스: processed/mobility/bus_seg_profile_v1.jsonl.gz
      (mobility_scripts/collect/build_bus_seg_profile_v1.py · 서울시 OA-21217 · TOPIS BMS)
      우리 717노선의 **연속 정류장 구간**마다 요일형(weekday/holiday) × 시간(0~23) 의 n(날 수)·p10·p50·p90(초).

★ 값의 뜻 — 「날짜별 시간대 평균 운행시간」의 분포다. **개별 운행의 분위가 아니다.**
  그래서 p90 누적을 「최악값」이라 부르지 않는다. 판정기 안의 worst 통과가 이것을 쓸 뿐이다(41 GPT 4).
★ 누적은 **구간 진입 시각대로** 한다 — 17:50 에 타서 18시를 넘기면 뒤 구간은 18시 칸(41 GPT 10).
★ 셀 날 수가 min_days 미만이거나 구간이 없거나 **양끝 정류장 ID 가 정류장 표와 다르면** 그 구간은 값을 내지 않고
  부른 쪽이 종전 모델로 대신한다(41 GPT 11 · 적용 GPT 7).
★ 운행일 자정(24:00) 이후 연장 구간(분 ≥ 1440)은 달력상 다음 날이다. 원천의 00~23시가 달력일 기준인지 운행일 기준인지
  확인하지 못했다(41 GPT 9 보류) — 두 날의 요일형이 다르면 **두 칸을 다 본다**. 두 칸 중 하나가 비면 「부분」으로 센다
  (적용 GPT 4). 48:00(2880) 이상에 닿으면 그 승차는 **셈하지 않는다**(날짜가 이틀 넘게 밀린다 · 적용 2차 GPT 9).
★ 로드할 때 칸 값을 검증한다 — 날 수가 있는 칸은 p10·p50·p90 이 유한한 양수이고 p10 ≤ p50 ≤ p90, 구간마다 양끝 정류장 ID·
  도착 순번이 있어야 한다. 어기면 멈춘다(적용 2차 GPT 8·9).
"""
import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DT = ("weekday", "holiday")
Q = {"n": 0, "p10": 1, "p50": 2, "p90": 3}


@dataclass
class Walk:
    """walk() 결과. minutes 가 None 이면 대체 모델도 못 썼다(fail_seq 에서)."""
    minutes: float = None
    used: int = 0          # 프로파일로 센 구간(요일형 후보 전부 유효)
    half: int = 0          # 요일형 후보 중 일부만 유효해 남은 칸으로 센 구간(부분)
    fb: int = 0            # 종전 모델로 대신한 구간
    edge: int = 0          # 요일형 후보가 둘이었던 구간
    mismatch: int = 0      # 양끝 정류장 ID 불일치로 버린 구간(fb 에 포함)
    fail_seq: int = None   # 대체 모델도 못 쓴 구간의 시작 seq(시작·도착 미도달이면 그 seq)
    out_of_range: bool = False   # 48:00 이상에 닿아 멈췄다

    @property
    def total(self):
        return self.used + self.half + self.fb

    @property
    def complete(self):
        """모든 구간이 요일형 후보 전부 유효한 프로파일로 셈해졌는가."""
        return self.minutes is not None and self.fb == 0 and self.half == 0 and self.used > 0


class BusSegProfile:
    def __init__(self, index, ends, arr, meta):
        self.index = index      # (route_id, from_seq) → 행 번호
        self.ends = ends        # 행 번호 → (from_id, to_id, to_seq)
        self.arr = arr          # float32 (N, 2 요일형, 4 [n,p10,p50,p90], 24 시간) · 없는 칸 NaN
        self.meta = meta
        self.source_id = meta.get("source_id", "seoul_OA-21217")
        self.built_at = meta.get("built_at")
        self.dates = meta.get("dates")

    @classmethod
    def load(cls, path=None):
        if path is None:
            from .paths import PROCESSED
            path = PROCESSED / "mobility" / "bus_seg_profile_v1.jsonl.gz"
        p = Path(path)
        if not p.exists():
            return None
        meta, recs = {}, []
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r.get("_meta"):
                    meta = r
                else:
                    recs.append(r)
        arr = np.full((len(recs), 2, 4, 24), np.nan, dtype=np.float32)
        index, ends = {}, {}
        for i, r in enumerate(recs):
            k = (r["route_id"], int(r["from_seq"]))
            if k in index:          # 같은 구간이 두 번 — 조용히 덮어쓰지 않는다(적용 GPT 7)
                raise ValueError(f"bus_seg_profile 중복 구간 {k}")
            index[k] = i
            ends[i] = (r.get("from_id"), r.get("to_id"), r.get("to_seq"))
            if None in ends[i]:
                raise ValueError(f"bus_seg_profile 구간 {k} 에 양끝 정류장 ID·도착 순번이 없다")
            for j, dk in enumerate(DT):
                c = r.get(dk)
                if not c:
                    continue
                arr[i, j, 0] = c["n"]
                for q in ("p10", "p50", "p90"):
                    arr[i, j, Q[q]] = [np.nan if v is None else v for v in c[q]]
        has = arr[:, :, 0, :] > 0
        qv = arr[:, :, 1:, :]
        bad = has & ~(np.isfinite(qv).all(axis=2) & (qv > 0).all(axis=2)
                      & (qv[:, :, 0] <= qv[:, :, 1]) & (qv[:, :, 1] <= qv[:, :, 2]))
        if bad.any():
            raise ValueError(f"bus_seg_profile 칸 값 이상 {int(bad.sum())}칸(유한 양수 · p10 ≤ p50 ≤ p90 위반)")
        return cls(index, ends, arr, meta)

    def row(self, route_id, s, nxt):
        """정류장 표의 연속 두 행(s → nxt)에 맞는 프로파일 행. 양끝 ID·순번이 다르면 (None, True)."""
        i = self.index.get((route_id, int(s["seq"])))
        if i is None:
            return None, False
        fid, tid, tseq = self.ends[i]
        if fid != s.get("station_id") or tid != nxt.get("station_id") or int(tseq) != int(nxt["seq"]):
            return None, True
        return i, False

    def cell(self, i, day_type, hour, q, min_days):
        """행 i 의 (요일형, 시간) 한 칸 분위(초). 날 수가 min_days 미만이거나 없으면 None."""
        j = DT.index(day_type)
        n = self.arr[i, j, 0, hour]
        if not (n >= min_days):
            return None
        v = self.arr[i, j, Q[q], hour]
        return None if np.isnan(v) else float(v)

    def walk(self, route_id, stops, a_seq, b_seq, depart_min, day_types, q, min_days, fallback, pick=max):
        """a → b 승차를 구간마다 진입 시각대로 누적한다.

        stops      : 그 노선의 정류장 행(seq 순) — 구간 = 연속한 두 행
        day_types  : fn(분) → 그 시각에 볼 요일형 튜플(보통 하나 · 자정 뒤 연장 구간에서 날이 갈리면 둘)
        fallback   : fn(구간 거리 m) → 분 또는 None — 프로파일을 못 쓰는 구간의 종전 모델(거리 ÷ 표정속도)
        pick       : 요일형 후보가 둘일 때 고르는 법 — 소요는 느린 쪽(max). 부른 쪽이 목적에 맞게 준다
        """
        w = Walk()
        t = float(depart_min)
        reached = a_seq == b_seq
        started = reached
        for s, nxt in zip(stops, stops[1:]):
            if s["seq"] < a_seq:
                continue
            if not started:
                if s["seq"] != a_seq:   # 정류장 표에 a 가 없다 — 뒤 구간부터 세지 않는다(적용 2차 GPT 8)
                    break
                started = True
            if s["seq"] >= b_seq or nxt["seq"] > b_seq:
                break                   # b 를 건너뛰는 구간은 세지 않는다(표에 b 가 없으면 아래에서 미도달)
            if t >= 2 * 1440:
                w.out_of_range = True
                w.fail_seq = s["seq"]
                return w
            h = int(t // 60) % 24
            dts = day_types(t)
            if len(dts) > 1:
                w.edge += 1
            i, bad = self.row(route_id, s, nxt)
            if bad:
                w.mismatch += 1
            vals = [] if i is None else [self.cell(i, d, h, q, min_days) for d in dts]
            ok = [v for v in vals if v is not None]
            if ok:
                t += pick(ok) / 60.0
                if len(ok) == len(dts):
                    w.used += 1
                else:
                    w.half += 1
            else:
                m = fallback(nxt.get("sect_dist_m"))
                if m is None:
                    w.fb += 1
                    w.fail_seq = s["seq"]
                    return w
                t += m
                w.fb += 1
            if nxt["seq"] == b_seq:
                reached = True
        if not started:
            w.fail_seq = a_seq
            return w
        if not reached:          # 정류장 표에 b 까지의 연속 구간이 없다 — 요청 구간을 다 돌지 못했다(적용 GPT 7)
            w.fail_seq = b_seq
            return w
        w.minutes = t - depart_min
        return w


def worst_not_before_best(dep_best, ride_best, dep_worst, ride_worst):
    """**동일 요청의 시나리오 도착 역전 보정** — 같은 요청 시각에서 worst 도착이 best 도착보다 이르면 worst 승차를 늘려
    맞춘다(시간 칸이 바뀌어 늦게 탄 worst 가 한산한 칸을 만나는 경우 · 적용 GPT 5). FIFO 가 아니다 — 요청 시각 t1 < t2 의
    도착 단조성은 보장하지 않는다(시간대별 상수 모델의 한계 · 적용 2차 GPT 4). 반환: (보정된 worst 승차 분, 보정했는가)."""
    need = (dep_best + ride_best) - dep_worst
    if ride_worst < need:
        return need, True
    return ride_worst, False


def pass_minutes(prof, route_id, stops, a_seq, last_min, day_types, q, min_days):
    """막차(기점 출발 last_min)가 정류장 a 에 닿기까지 q 누적(분) — 사슬이 온전할 때만(대체·부분·ID 불일치 없음).
    기점(첫 정류장)이면 0(시간표 확정 · 프로파일 불필요). 모르면 None."""
    if a_seq == stops[0]["seq"]:
        return 0.0
    if prof is None:
        return None
    w = prof.walk(route_id, stops, stops[0]["seq"], a_seq, last_min, day_types, q, min_days, lambda _d: None, pick=max)
    if w.minutes is None or w.fb or w.half:
        return None
    return w.minutes


def board_caps(prof, route_id, stops, a_seq, last_min, day_types, min_days):
    """승차 시각 상한(막차 통과 추정) — (best 용 p50, worst 용) 분. worst 용은 max(p90 누적, p50 누적)이다:
    칸마다 p90 ≥ p50 이어도 시간 칸이 바뀌면 누적은 역전될 수 있어 worst 상한이 best 상한보다 앞서지 않게 한다(적용 2차 GPT 3).
    기점이면 둘 다 last_min(확정). 모르면 (None, None)."""
    b = pass_minutes(prof, route_id, stops, a_seq, last_min, day_types, "p50", min_days)
    if b is None:
        return None, None
    w = pass_minutes(prof, route_id, stops, a_seq, last_min, day_types, "p90", min_days)
    if w is None:
        return None, None
    import math
    cb, cw = last_min + math.ceil(b), last_min + math.ceil(w)
    return cb, max(cb, cw)
