# -*- coding: utf-8 -*-
"""판정 로그 (40번 방 · 판정로그 설계 v2 구현 · 2값 판정 기준)

두 층 — 설계 v2 §1
  · 상시 로그  `judged_log_v1.jsonl` — 판정 1건 1줄(얇게). 회귀·자기점검·실측 대조 실행마다 append
  · 실패 덤프  `regression_fail_dump_YYYYMMDD.jsonl` — 기대와 어긋난 케이스만(두껍게 · 한 케이스 한 줄 append). 로그만 보고 어느 분기를 고칠지

1줄 필드 (27 첫 메시지 · 39 인계 §5 「40」)
  case_id · ts · input · verdict(밖 2값) · reason(밖 이유 코드) · eta(예정 소요 · 중앙값) · slack_min
  · grade_counts(내부) · rules_version · timetable_build · latency_ms · expected/match
  · alt_source · ext_calls · alt_dropped
  + 28 이 읽는 것: depart_min · arrive_min · margin_min(@) · verdict_internal · grade · confirm_time
  + 집계용 메타: run_id · source · bundle · synthetic · device
  + miss_axes: 도착·여유·@·늦어도 출발 잠금 축 중 어긋난 것(분류 채점 밖 · 덤프 조건에만 쓴다)
  ★ G4 GPS 필드는 없다(45차에서 접혔다). 좌표·GPS 키는 입력에서 버린다.

저장 금지 — 설계 v2 §1·§2-1 · 규칙 23
  · ODsay 가 준 값(응답 원문 · subPath · sectionTime · mapObj/loadLane · ODsay 소요·요금) → **차단 목록 검사**.
    걸리면 그 줄을 쓰지 않고 경고 + `judged_log_blocked_v1.jsonl` 에 **키 경로만** 남긴다(값은 안 남김)
  · 차단 범위: 로그 줄과 덤프를 **쓰기 전에 같이** 검사한다 — 어느 쪽이 걸려도 둘 다 안 쓴다. 차단 파일·콘솔에는 값 없이 경로·사유만
  · ★ 로거는 키 이름·표식·좌표로만 본다. ODsay 숫자가 **이름을 바꿔** 우리 칸(eta 등)에 들어오면 구별하지 못한다(GPT #8) —
    그건 44 어댑터 계약이다: 로거에 넘기는 결과는 D7 재조립 후 **우리 판정기가 다시 낸 CaseResult** 뿐이다
  · 좌표 원값 → 입력 정리 단계에서 버린다(역명·노선명·시각·동행조건만). 문자열 안의 좌표쌍도 가린다
  · `ext_calls` 는 호출 메타데이터(api·n·ok·fail·latency_ms)만 · `alt_dropped` 는 건수·사유 코드만

한 기기 — 로그 폴더에 `DEVICE.txt` 를 두고 처음 쓴 기기 이름을 적는다. 다른 기기면 쓰지 않는다
  (데이터 폴더가 드라이브 동기화라 두 기기가 같은 jsonl 에 append 하면 충돌한다). `allow_other_device=True` 로만 넘긴다.

판정기(verify_time.py)는 안 고친다 — `install()` 이 `Verifier.verify_case` 를 **밖에서 감싼다**(시간 재기 + 기록).
multi 는 후보마다 verify_case 를 다시 부르므로 **맨 바깥 호출만** 기록한다(깊이 카운터).
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOG_NAME = "judged_log_v1.jsonl"
BLOCKED_NAME = "judged_log_blocked_v1.jsonl"
DUMP_FMT = "regression_fail_dump_{ymd}.jsonl"   # (GPT #7) 한 케이스 한 줄 append — 읽고 덮어쓰기 없음
DEVICE_FILE = "DEVICE.txt"
LOG_SCHEMA = "judged_log/1 (40 · 2값)"
KST = timezone(timedelta(hours=9))

OUT_VERDICTS = ("feasible", "infeasible")
GRADES = ("확정", "추정", "근거없음")

# ── 차단 목록 (설계 v2 §5 · D5) ─────────────────────────────────────────
# ODsay 응답 키. 우리 판정 dict 에는 안 나오는 이름들이다 — 나오면 D7 재조립을 안 거친 값이 섞인 것이다.
ODSAY_KEYS = frozenset({
    "subPath", "sectionTime", "mapObj", "loadLane", "lane", "trafficType", "passStopList",
    "totalTime", "payment", "busPayment", "subwayPayment", "totalPayment",
    "busTransitCount", "subwayTransitCount", "totalDistance", "totalWalk", "totalStationCount",
    "firstStartStation", "lastEndStation", "pathType", "searchType", "graphPos", "graphPosX", "graphPosY",
    "startX", "startY", "endX", "endY", "startID", "endID", "busLocalBlID", "busID", "subwayCode",
    "wayCode", "stationClass", "startExitNo", "endExitNo", "startExitX", "startExitY", "endExitX", "endExitY",
})
# 문자열 안에 원문 JSON 이 통째로 들어온 경우를 잡는 표식(흔한 낱말은 뺐다 — 오탐 방지)
ODSAY_TOKENS = ("subPath", "sectionTime", "mapObj", "loadLane", "passStopList", "trafficType")
# 좌표·GPS — 입력 정리에서 버린다. 남아 있으면 차단한다(G4 는 접혔다)
COORD_KEYS = frozenset({"lat", "lng", "lon", "latitude", "longitude", "x", "y", "coord", "coords",
                        "gps", "live_pos", "accuracy_m", "origin_source", "geometry", "points", "pts"})
_COORD_RE = re.compile(r"\b3[3-8]\.\d+[\s,;/]+12[4-9]\.\d+\b|\b12[4-9]\.\d+[\s,;/]+3[3-8]\.\d+\b")

EXT_CALL_KEYS = ("api", "n", "ok", "fail", "latency_ms")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class BlockedRecord(ValueError):
    """hits 는 **키 경로·사유 코드만**. 만드는 쪽이 값을 넣어도 여기서 좌표쌍을 가린다(GPT #1)."""
    def __init__(self, hits):
        hits = [scrub_text(str(h)) for h in hits]
        super().__init__("차단 목록에 걸렸다: " + ", ".join(hits[:8]))
        self.hits = hits


def scan_blocked(obj, path="$"):
    """ODsay 키·좌표 키·원문 표식·좌표쌍 문자열을 찾아 **경로만** 돌려준다(값은 안 돌려준다)."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if k in ODSAY_KEYS:
                hits.append(f"odsay:{p}")
            elif str(k).lower() in COORD_KEYS:
                hits.append(f"coord:{p}")
            hits += scan_blocked(v, p)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits += scan_blocked(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        if any(t in obj for t in ODSAY_TOKENS):
            hits.append(f"odsay_text:{path}")
        if _COORD_RE.search(obj):
            hits.append(f"coord_text:{path}")
    return hits


def scrub_text(s):
    """덤프용 — 문자열 안 좌표쌍을 가린다(자전거 구간 표기 `lat,lng` 가 사유 문장에 섞일 수 있다)."""
    return _COORD_RE.sub("<좌표>", s) if isinstance(s, str) else s


def scrub(obj):
    """덤프용 재귀 정리 — 좌표 키 삭제 · 문자열 좌표쌍 가림. ODsay 키는 지우지 않는다(검사가 잡아야 하니까)."""
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if str(k).lower() not in COORD_KEYS}
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return scrub_text(obj)


# ── 입력 정리 (설계 v2 §2 input — 역명·노선명·출발시각·요일유형·동행조건 · 좌표·경로 상세 금지) ─
def _name(x):
    """역·정류장 이름만. 좌표 dict → name · 좌표 문자열 → <좌표> · 숫자·목록(좌표 배열) → <좌표>."""
    if isinstance(x, dict):
        x = x.get("name") or "<좌표>"
    if x is None:
        return None
    if not isinstance(x, str):
        return "<좌표>"
    # 자동차 케이스는 from/to 가 "37.4991,127.0310" 문자열이다(car_legs_v1) — 첫 회귀에서 차단 10건으로 드러났다
    return scrub_text(x)


def _leg_in(leg):
    m = leg.get("mode", "subway")
    o = {"mode": m}
    if leg.get("line"):
        o["line"] = leg["line"]
    if leg.get("route") is not None:
        o["route"] = str(leg["route"])
    o["from"], o["to"] = _name(leg.get("from")), _name(leg.get("to"))
    return o


def sanitize_input(case, result=None):
    keep = ("date", "depart_at", "arrive_by", "stage", "first_visit", "foreign")
    o = {k: case[k] for k in keep if k in case}
    if result is not None and getattr(result, "day_type", None):
        o["day_type"] = result.day_type
    party = case.get("party")
    if party:
        o["party"] = {k: scrub_text(v) for k, v in party.items()
                      if isinstance(v, (bool, int, float, str)) and str(k).lower() not in COORD_KEYS}
    if case.get("legs"):
        o["legs"] = [_leg_in(x) for x in case["legs"]]
    if case.get("multi"):
        mu = case["multi"]
        o["multi"] = {"from": _name(mu.get("from")), "to": _name(mu.get("to"))}
    if case.get("disruptions"):
        o["disruptions"] = scrub(case["disruptions"])
    return o


def _ext_calls(x):
    out = []
    for i, c in enumerate(x or []):
        extra = set(c) - set(EXT_CALL_KEYS)
        if extra:
            raise BlockedRecord([f"ext_calls_key:$.ext_calls[{i}].<{len(extra)}개 키>"])
        o = {k: c[k] for k in EXT_CALL_KEYS if k in c}
        bad = [k for k in ("n", "ok", "fail") if k in o and not (isinstance(o[k], int) and not isinstance(o[k], bool))]
        if "latency_ms" in o and not (isinstance(o["latency_ms"], (int, float)) and not isinstance(o["latency_ms"], bool)):
            bad.append("latency_ms")
        if not isinstance(o.get("api"), str) or not _CODE_RE.match(o.get("api") or ""):
            bad.append("api")
        if bad:
            raise BlockedRecord([f"ext_calls_value:{k}" for k in bad])
        out.append(o)
    return out


def _alt_dropped(x):
    if not x:
        return {"n": 0, "reasons": {}}
    reasons = x.get("reasons") or {}
    bad_code = [k for k in reasons if not _CODE_RE.match(str(k))]
    bad_key = [k for k in x if k not in ("n", "reasons")]
    if bad_code or bad_key or not all(isinstance(v, int) for v in reasons.values()):
        # 사유 코드·키 이름도 입력값이다 — 이름은 안 남기고 개수만(GPT #1)
        raise BlockedRecord((["alt_dropped:$.alt_dropped.reasons.<코드 형식 밖 %d개>" % len(bad_code)] if bad_code else [])
                            + (["alt_dropped:$.alt_dropped.<허용 밖 키 %d개>" % len(bad_key)] if bad_key else [])
                            or ["alt_dropped:$.alt_dropped.reasons.<정수 아님>"])
    n = x.get("n", sum(reasons.values()))
    if not isinstance(n, int) or isinstance(n, bool):
        raise BlockedRecord(["alt_dropped:n 이 정수가 아니다"])
    return {"n": n, "reasons": dict(reasons)}


def grade_counts(result):
    c = {g: 0 for g in GRADES}
    for e in getattr(result, "evidence", None) or []:
        g = (e.get("grade") or "").split(":")[0]
        if g in c:
            c[g] += 1
    return c


def confirm_time(result):
    ts = [e.get("observed_at") for e in (getattr(result, "evidence", None) or []) if e.get("observed_at")]
    return max(ts) if ts else None


def expected_of(case):
    e = case.get("expect")
    if e is None:
        return None
    return {"verdict": e, "reason": case.get("expect_reason")}


def is_match(expected, out):
    if expected is None:
        return None
    if expected["verdict"] != out.get("verdict"):
        return False
    if expected.get("reason") is not None and expected["reason"] != out.get("code"):
        return False
    return True


def miss_axes(case, result):
    """2값·이유 밖의 **값** 잠금 축 — 도착·여유·@·늦어도 출발. 어긋난 축 이름 목록.

    `match` 는 분류 채점용(2값 + 이유)이라 이 축들을 안 본다. 덤프는 둘 중 하나라도 어긋나면 남긴다.
    대안·경고·택시·후보 축은 verify_time 의 MISS 목록이 정본이다(판정기를 안 고치므로 여기서 다시 세지 않는다).
    """
    from .timeutil import to_service_min
    o = getattr(result, "out", None) or {}
    bad = []
    ea = case.get("expect_arrive")
    if ea and to_service_min(ea) != getattr(result, "arrive_min", None):
        bad.append("arrive")
    for key, val in (("expect_slack_min", o.get("slack_min")), ("expect_margin_min", o.get("margin_min"))):
        rng = case.get(key)
        if rng is not None:
            lo, hi = rng
            if val is None or (lo is not None and val < lo) or (hi is not None and val > hi):
                bad.append(key[len("expect_"):])
    el = case.get("expect_last_depart")
    lfd = o.get("last_feasible_depart_min")
    if el is not None and to_service_min(el) != lfd:
        bad.append("last_depart")
    if case.get("expect_last_depart_none") and lfd is not None:
        bad.append("last_depart_none")
    return bad


def build_record(case, result, *, latency_ms, run_id, source, bundle=None, synthetic=False,
                 rules_version=None, timetable_build=None, now=None, device=None):
    """판정 1건 → 로그 1줄(dict). 차단 목록에 걸리면 BlockedRecord."""
    out = getattr(result, "out", None) or {}
    exp = expected_of(case)
    rec = {
        "case_id": case.get("id") or f"adhoc-{uuid.uuid4().hex[:12]}",
        "ts": (now or datetime.now(KST)).isoformat(timespec="seconds"),
        "run_id": run_id, "source": source, "bundle": bundle, "device": device,
        "synthetic": bool(synthetic or case.get("synthetic")),
        "input": sanitize_input(case, result),
        "verdict": out.get("verdict"),
        "reason": out.get("code"),
        "eta": out.get("eta_min"),
        "slack_min": out.get("slack_min"),
        "margin_min": out.get("margin_min"),
        "depart_min": out.get("depart_min"),
        "arrive_min": out.get("arrive_min"),
        "verdict_internal": getattr(result, "verdict", None),
        "grade": getattr(result, "grade", None),
        "grade_counts": grade_counts(result),
        "confirm_time": confirm_time(result),
        "rules_version": rules_version,
        "timetable_build": timetable_build,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "expected": exp,
        "match": is_match(exp, out),
        "miss_axes": miss_axes(case, result),      # (GPT #4) 판정 기대값 유무와 무관하게
        "alt_source": case.get("alt_source", "own"),
        "ext_calls": _ext_calls(case.get("ext_calls")),
        "alt_dropped": _alt_dropped(case.get("alt_dropped")),
    }
    if rec["alt_source"] not in ("own", "odsay"):
        raise BlockedRecord(["alt_source:$.alt_source(own/odsay 밖)"])      # 값은 안 남긴다(GPT #1)
    hits = scan_blocked(rec)
    if hits:
        raise BlockedRecord(hits)
    return rec


def _leg_detail(leg):
    d = leg.__dict__ if hasattr(leg, "__dict__") else dict(leg)
    keep = ("idx", "label", "verdict", "code", "reason", "grade", "depart_min", "arrive_min", "wait_min",
            "ride_min", "ride_grade", "relief", "dropped", "walk_min", "worst", "evidence")
    o = {k: d.get(k) for k in keep if d.get(k) is not None}
    o["warnings"] = [w.get("code") for w in d.get("warnings") or [] if isinstance(w, dict)]
    if d.get("car"):
        o["car"] = {k: v for k, v in d["car"].items() if k in (
            "verdict", "grade", "eta_min", "fare_won", "night_rate", "slow_s", "day_type", "coverage_pct")}
    return o


def build_dump(record, case, result):
    """실패 덤프 1건 — 로그 줄 전체 + decisions_detail(구간별 best/worst · 경고 · 근거 · 대안 요약)."""
    r = result
    detail = {
        "reason_text": r.reason, "relief": r.relief, "verdict_best": r.verdict_best,
        "arrive_worst_min": r.arrive_worst_min, "buffer_min": r.buffer_min,
        "eta_worst_min": r.eta_worst_min, "last_feasible_depart_min": r.last_feasible_depart_min,
        "out": r.out,
        "warnings": [{"code": w.get("code"), "text": w.get("text")} for w in r.warnings or [] if isinstance(w, dict)],
        "evidence": r.evidence,
        "legs": [_leg_detail(x) for x in r.legs or []],
        "legs_worst": [_leg_detail(x) for x in r.legs_worst or []],
        "alternatives": [{"label": a.get("label"), "axis": a.get("axis"), "verdict": a.get("verdict"),
                          "mode": a.get("mode")} for a in r.alternatives or []],
        "taxi": ({k: r.taxi.get(k) for k in ("verdict", "grade", "arrive_min", "fare_won")} if r.taxi else None),
        "candidates": ([{"label": c.get("label"), "criteria": c.get("criteria"), "verdict": c.get("verdict"),
                         "out": c.get("out")} for c in r.candidates] if r.candidates else None),
        "expect_axes": {k: v for k, v in case.items() if k.startswith("expect")},
    }
    dump = dict(record, decisions_detail=scrub(detail))
    hits = scan_blocked(dump)
    if hits:
        raise BlockedRecord(hits)
    return dump


# ── 기록기 ───────────────────────────────────────────────────────────────
def default_log_dir():
    from .paths import PROCESSED
    return PROCESSED / "mobility" / "logs"


class DeviceMismatch(RuntimeError):
    pass


class JudgmentLogger:
    def __init__(self, log_dir=None, *, source="regression", bundle=None, synthetic=False, run_id=None,
                 dump=True, allow_other_device=False, device=None, stream=None):
        self.dir = Path(log_dir) if log_dir else default_log_dir()
        self.source, self.bundle, self.synthetic = source, bundle, synthetic
        self.run_id = run_id or datetime.now(KST).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.dump_on = dump
        self.device = device or socket.gethostname()
        self.allow_other_device = allow_other_device
        self.stream = stream or sys.stdout      # PowerShell 5 는 stderr 를 오류 레코드로 감싼다 — stdout 으로
        self.n_written = self.n_blocked = self.n_mismatch = self.n_log_error = self.n_judge_error = 0
        self.n_dumped = 0
        self.dump_path = None
        self._fh = None

    # 한 기기 규칙
    def _check_device(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.dir / DEVICE_FILE
        # 드라이브 동기화 충돌 사본(DEVICE (1).txt 등)이 있으면 두 기기가 동시에 주인이 된 것이다
        twins = sorted(x.name for x in self.dir.glob("DEVICE*.txt") if x.name != DEVICE_FILE)
        if twins and not self.allow_other_device:
            raise DeviceMismatch(f"로그 폴더에 기기 표식이 여럿이다({DEVICE_FILE} · {', '.join(twins)}) — "
                                 f"동기화 충돌이다. 한 기기만 남기고 지운 뒤 다시 돌린다")
        if not f.exists():
            try:
                with open(f, "x", encoding="utf-8") as fh:          # 없을 때만 만든다(같은 기기 두 프로세스 경합)
                    fh.write(f"{self.device}\n# 40번 방 · 판정 로그는 이 기기에만 쌓는다({datetime.now(KST).date()})\n")
                return
            except FileExistsError:
                pass
        lines = [x.strip() for x in f.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        owner = lines[0] if lines else ""
        if not owner:
            raise DeviceMismatch(f"{f} 가 비었다 — 로그를 쌓는 기기 이름을 첫 줄에 적는다")
        if owner != self.device and not self.allow_other_device:
            raise DeviceMismatch(f"로그 폴더 {self.dir} 는 기기 '{owner}' 전용이다(지금 '{self.device}'). "
                                 f"다른 기기는 --log-dir 로 다른 폴더를 쓰거나 --log-allow-other-device 로 넘긴다")

    def open(self):
        self._check_device()
        lp = self.dir / LOG_NAME
        tail_ok = True
        if lp.exists() and lp.stat().st_size:
            with open(lp, "rb") as fb:
                fb.seek(-1, 2)
                tail_ok = fb.read(1) == b"\n"
        # 한 줄씩 한 번에 쓰고 바로 비운다 — 도중에 죽어도 잘린 줄은 마지막 한 줄뿐이고, 다음 실행이 줄을 바꿔 붙인다
        self._fh = open(lp, "a", encoding="utf-8", newline="\n", buffering=1)
        if not tail_ok:
            self._fh.write("\n")
            print(f"  ! {lp.name} 끝 줄이 잘려 있었다 — 줄을 바꿔 이어 쓴다(보고서는 깨진 줄을 세고 건너뛴다)", file=self.stream)
        return self

    def _block(self, case_id, hits, what="판정 로그"):
        self.n_blocked += 1
        print(f"  ! {what} 차단 [{case_id}] — 키 경로 {len(hits)}건(값 없음): {', '.join(hits[:4])}", file=self.stream)
        try:
            with open(self.dir / BLOCKED_NAME, "a", encoding="utf-8", newline="\n") as bf:
                bf.write(json.dumps({"ts": datetime.now(KST).isoformat(timespec="seconds"), "run_id": self.run_id,
                                     "case_id": case_id, "what": what, "hits": hits}, ensure_ascii=False) + "\n")
        except Exception as e:                       # (GPT #2) 차단 기록 실패도 판정 쪽으로 안 샌다
            self.n_log_error += 1
            print(f"  ! 차단 기록 실패 — {type(e).__name__}", file=self.stream)

    def record(self, case, result, *, latency_ms, rules_version=None, timetable_build=None):
        """판정 1건 기록. **어떤 예외도 밖으로 안 낸다**(GPT #2) — 로거 실패가 판정·자기점검 결과를 바꾸면 안 된다.

        차단 범위(GPT #3): 줄과 덤프를 **쓰기 전에 같이** 검사한다. 어느 쪽이든 걸리면 둘 다 안 쓰고 차단 파일에 경로만.
        """
        cid = scrub_text(str(case.get("id")))
        try:
            rec = build_record(case, result, latency_ms=latency_ms, run_id=self.run_id, source=self.source,
                               bundle=self.bundle, synthetic=self.synthetic, device=self.device,
                               rules_version=rules_version, timetable_build=timetable_build)
            bad = rec["match"] is False or bool(rec["miss_axes"])
            dump = build_dump(rec, case, result) if (bad and self.dump_on) else None
        except BlockedRecord as e:
            self._block(cid, e.hits)
            return None
        except Exception as e:
            self.n_log_error += 1
            print(f"  ! 판정 로그 실패 [{cid}] — {type(e).__name__}", file=self.stream)
            return None
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.n_written += 1
            if bad:
                self.n_mismatch += 1
            if dump is not None:
                self.dump_path = self.dir / DUMP_FMT.format(ymd=datetime.now(KST).strftime("%Y%m%d"))
                with open(self.dump_path, "a", encoding="utf-8", newline="\n") as df:
                    df.write(json.dumps(dump, ensure_ascii=False, default=str) + "\n")    # 한 번의 write
                self.n_dumped += 1
        except Exception as e:
            self.n_log_error += 1
            print(f"  ! 판정 로그 쓰기 실패 [{cid}] — {type(e).__name__}: {e}", file=self.stream)
            return None
        return rec

    def close(self):
        try:
            if self._fh:
                self._fh.close()
        except Exception as e:
            self.n_log_error += 1
            print(f"  ! 로그 닫기 실패 — {type(e).__name__}", file=self.stream)
        self._fh = None
        path = self.dump_path if self.n_dumped else None
        print(f"판정 로그 {self.n_written}줄 → {self.dir / LOG_NAME} · run {self.run_id} · 기기 {self.device}"
              f" · 기대 어긋남 {self.n_mismatch}" + (f" → 덤프 {self.n_dumped}건 {path}" if path else "")
              + (f" · **차단 {self.n_blocked}**" if self.n_blocked else "")
              + (f" · 로거 실패 {self.n_log_error}" if self.n_log_error else "")
              + (f" · 판정 예외(기록 안 됨) {self.n_judge_error}" if self.n_judge_error else ""), file=self.stream)
        return path


@contextmanager
def install(verifier_cls, logger):
    """`verifier_cls.verify_case` 를 감싼다(판정기 코드는 그대로). 맨 바깥 호출만 기록한다."""
    orig = verifier_cls.verify_case
    # 56 (GPT #1) — 호출 깊이는 **스레드마다**. 공유 dict 였을 때는 A 요청 중에 들어온 B 요청을 중첩으로 오인해
    #   기록·판정 예외 집계를 빠뜨렸다. 기록(파일 쓰기·카운터)은 잠금 안에서 한 번에.
    depth = threading.local()
    lock = threading.Lock()

    def wrapped(self, case):
        n = getattr(depth, "n", 0)
        top = n == 0
        depth.n = n + 1
        t0 = time.perf_counter()
        try:
            r = orig(self, case)
        except BaseException:
            if top:
                with lock:
                    logger.n_judge_error += 1
            raise
        finally:
            depth.n -= 1
        if top:
            with lock:
                logger.record(case, r, latency_ms=(time.perf_counter() - t0) * 1000,
                              rules_version=self.R.get("rules_version"),
                              timetable_build=getattr(self.tt, "fetched_at", None))
        return r

    logger.open()                                   # 기기 표식에 막히면 여기서 끝난다 — 패치 전에
    verifier_cls.verify_case = wrapped
    try:
        yield logger
    finally:
        verifier_cls.verify_case = orig
        logger.close()
