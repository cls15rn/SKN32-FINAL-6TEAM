# -*- coding: utf-8 -*-
"""판정 로그·지표 시험 (40번 방). 저장소 루트에서:  python final_project_cs/tests/unit/travel/mobility/test_judgment_log.py

판정기·자료 없이 돈다(가짜 결과 객체). 실제 회귀 연결은 mobility_scripts/judgment_log_run.py 로 확인한다.
"""
import io
import json
import random
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

REPO = Path(__file__).resolve().parents[5]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine import judgment_log as jl                 # noqa: E402
from mobility_scripts.classification_metrics import classify_report, binary_report, misclassified_catalog   # noqa: E402
from mobility_scripts import judgment_metrics_report as rep                                     # noqa: E402

FIELDS = {"case_id", "ts", "run_id", "source", "bundle", "synthetic", "input", "verdict", "reason", "eta",
          "slack_min", "margin_min", "depart_min", "arrive_min", "verdict_internal", "grade", "grade_counts",
          "confirm_time", "rules_version", "timetable_build", "latency_ms", "expected", "match",
          "alt_source", "ext_calls", "alt_dropped", "device", "miss_axes"}


def result(verdict="feasible", code=None, internal=None, **kw):
    out = {"verdict": verdict, "depart_min": 840, "arrive_min": 845, "margin_min": 10, "slack_min": 5, "eta_min": 5,
           "last_feasible_depart_min": 840}
    if code:
        out["code"] = code
    base = dict(verdict=internal or verdict, grade="추정", arrive_min=845, reason="사유", relief=None, verdict_best=verdict,
                arrive_worst_min=845, buffer_min=10, eta_worst_min=5, last_feasible_depart_min=840, out=out,
                warnings=[{"code": "MOB_W_X", "text": "t"}], day_type="weekday", alternatives=[], taxi=None,
                candidates=None, legs=[NS(idx=0, label="02호선 강변→잠실", verdict=verdict, code=code, reason="r",
                                          grade="확정", depart_min=840, arrive_min=845, wait_min=0, ride_min=4.5,
                                          ride_grade="추정", relief=None, dropped={}, walk_min=None, worst=False,
                                          evidence=[], warnings=[], car=None)],
                legs_worst=[],
                evidence=[{"grade": "확정", "observed_at": "2026-09-09"}, {"grade": "추정", "observed_at": "2026-09-11T10:16:28+09:00"},
                          {"grade": "근거없음:시간표", "observed_at": None}])
    base.update(kw)
    return NS(**base)


CASE = {"id": "T-1", "date": "2026-09-11", "depart_at": "14:00", "arrive_by": "14:20",
        "party": {"luggage": True, "size": 2}, "legs": [{"line": "02호선", "from": "강변", "to": "잠실"}],
        "expect": "feasible"}

checks = []


def check(name, cond):
    checks.append((name, bool(cond)))
    print(("OK   " if cond else "FAIL ") + name)


def rec(case=CASE, res=None, **kw):
    return jl.build_record(case, res or result(), latency_ms=12.34, run_id="R", source="regression",
                           rules_version="v0.8", timetable_build="2026-09-09", **kw)


# 1 필드
r = rec()
check("1줄 필드 집합이 설계와 같다(GPS 필드 없음)", set(r) == FIELDS)
check("verdict 는 밖 2값 · reason 은 코드", r["verdict"] == "feasible" and r["reason"] is None)
check("grade_counts — '근거없음:…' 접두 처리", r["grade_counts"] == {"확정": 1, "추정": 1, "근거없음": 1})
check("confirm_time = 가장 늦은 observed_at", r["confirm_time"] == "2026-09-11T10:16:28+09:00")
check("match True · latency 반올림", r["match"] is True and r["latency_ms"] == 12.3)
check("alt_source 기본 own · ext_calls [] · alt_dropped 0", r["alt_source"] == "own" and r["ext_calls"] == []
      and r["alt_dropped"] == {"n": 0, "reasons": {}})

# 2 좌표·GPS
c = dict(CASE, legs=[{"mode": "car", "from": "37.4991,127.0310", "to": "37.5106,127.0704"},
                     {"mode": "bike", "from": {"lat": 37.5, "lng": 127.0, "name": "뚝섬역"}, "to": {"lat": 37.5, "lng": 127.1}}],
         origin_source="gps", accuracy_m=30, live_pos={"lat": 37.5, "lng": 127.0})
r = rec(c)
blob = json.dumps(r, ensure_ascii=False)
check("좌표 문자열 → <좌표> · dict → 이름만", r["input"]["legs"][0]["from"] == "<좌표>"
      and r["input"]["legs"][1]["from"] == "뚝섬역" and r["input"]["legs"][1]["to"] == "<좌표>")
check("GPS 키(origin_source·accuracy_m·live_pos)는 입력에 없다", all(k not in blob for k in ("origin_source", "accuracy_m", "live_pos", "127.0")))

c = dict(CASE, legs=[{"mode": "car", "from": [37.50, 127.03], "to": "127.0391 37.5012"}],
         party={"hotel": "37.5 127.0", "Lat": 37.5, "size": 2})
r = rec(c)
check("좌표 배열·공백/경도먼저 문자열·party 문자열·대문자 키도 가린다",
      r["input"]["legs"][0]["from"] == "<좌표>" and r["input"]["legs"][0]["to"] == "<좌표>"
      and r["input"]["party"] == {"hotel": "<좌표>", "size": 2})

# 3 차단 목록
def blocked(case, res=None):
    try:
        rec(case, res)
        return None
    except jl.BlockedRecord as e:
        return e.hits

check("ext_calls 에 응답 키가 섞이면 차단", blocked(dict(CASE, ext_calls=[{"api": "odsay", "n": 1, "ok": 1, "fail": 0, "subPath": []}])))
check("ext_calls 메타데이터만이면 통과", blocked(dict(CASE, alt_source="odsay", ext_calls=[{"api": "odsay", "n": 1, "ok": 1, "fail": 0, "latency_ms": 210}])) is None)
check("alt_dropped 에 후보 내용이 오면 차단", blocked(dict(CASE, alt_dropped={"n": 1, "candidates": [{"x": 1}]})))
check("alt_dropped 사유 코드만이면 통과", blocked(dict(CASE, alt_dropped={"n": 2, "reasons": {"route_not_collected": 2}})) is None)
check("disruptions 에 ODsay 키가 섞이면 차단", blocked(dict(CASE, disruptions=[{"kind": "suspend", "sectionTime": 3}])))
check("문자열에 원문 JSON 이 들어오면 차단", jl.scan_blocked({"a": '{"subPath": [1]}'}))
check("ext_calls 값에 목록이 오면 차단", blocked(dict(CASE, ext_calls=[{"api": "odsay", "n": [37.5, 127.0]}])))
check("alt_dropped.n 이 문자열이면 차단(예외로 새지 않는다)", blocked(dict(CASE, alt_dropped={"n": "x"})))
check("alt_source 어휘 밖이면 차단", blocked(dict(CASE, alt_source="naver")))
check("깊은 곳의 mapObj 도 잡는다", jl.scan_blocked({"a": [{"b": {"mapObj": "x"}}]}) == ["odsay:$.a[0].b.mapObj"])
check("우리 판정 dict 는 오탐 없음", jl.scan_blocked(result().out) == [])

# 4 기록기 — 덤프 · 차단 파일 · 한 기기
with tempfile.TemporaryDirectory() as d:
    err = io.StringIO()
    lg = jl.JudgmentLogger(d, device="laptop", run_id="R1", stream=err).open()
    lg.record(CASE, result(), latency_ms=1)
    lg.record(dict(CASE, id="T-2", expect="infeasible", expect_reason="after_last"), result(), latency_ms=1)
    lg.record(dict(CASE, id="T-3", ext_calls=[{"api": "odsay", "raw": "…"}]), result(), latency_ms=1)
    lg.record(dict(CASE, id="T-4", expect="infeasible", expect_reason="after_last"),
              result("infeasible", "no_data", internal="unknown"), latency_ms=1)
    dump = lg.close()
    lines = (Path(d) / jl.LOG_NAME).read_text(encoding="utf-8").splitlines()
    check("로그 3줄(차단 1줄은 안 씀)", len(lines) == 3 and lg.n_blocked == 1)
    bl = [json.loads(x) for x in (Path(d) / jl.BLOCKED_NAME).read_text(encoding="utf-8").splitlines()]
    check("차단 파일은 키 경로만(값·키 이름 없음)", bl[0]["hits"] == ["ext_calls_key:$.ext_calls[0].<1개 키>"]
          and "…" not in json.dumps(bl) and "raw" not in json.dumps(bl))
    dd = [json.loads(x) for x in Path(dump).read_text(encoding="utf-8").splitlines()]
    check("덤프(JSONL) = 어긋난 2건 · decisions_detail 있음", [x["case_id"] for x in dd] == ["T-2", "T-4"]
          and "legs" in dd[0]["decisions_detail"])
    check("이유만 어긋나도 match False(T-4 no_data ≠ after_last)", json.loads(lines[2])["match"] is False)
    try:
        jl.JudgmentLogger(d, device="home-pc", stream=err).open()
        check("다른 기기는 막힌다", False)
    except jl.DeviceMismatch:
        check("다른 기기는 막힌다", True)
    lg2 = jl.JudgmentLogger(d, device="home-pc", allow_other_device=True, stream=err).open()
    lg2.close()
    check("allow_other_device 로만 넘긴다", True)


# 4-2 기록기 견고성
with tempfile.TemporaryDirectory() as d:
    lg = jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open()
    check("로거 내부 예외는 판정 쪽으로 새지 않는다", lg.record(CASE, NS(out=None, evidence=[{"grade": 1}]), latency_ms=1) is None
          and lg.n_log_error == 1)
    lg.close()
    (Path(d) / jl.LOG_NAME).write_text('{"case_id": "A"}\n{"case_id": "B", "ver', encoding="utf-8")
    lg = jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open()
    lg.record(CASE, result(), latency_ms=1)
    lg.close()
    txt = (Path(d) / jl.LOG_NAME).read_text(encoding="utf-8").splitlines()
    check("잘린 끝 줄 뒤에는 줄을 바꿔 이어 쓴다", len(txt) == 3 and json.loads(txt[2])["case_id"] == "T-1")
    rep.BROKEN["n"] = 0
    check("보고서 load 는 깨진 줄을 세고 건너뛴다", len(rep.load(Path(d) / jl.LOG_NAME)) == 2 and rep.BROKEN["n"] == 1)
    (Path(d) / jl.DEVICE_FILE).write_text("\ufefflaptop\n", encoding="utf-8")
    ok = True
    try:
        jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open().close()
    except jl.DeviceMismatch:
        ok = False
    check("메모장 BOM 이 붙어도 주인 기기로 읽는다", ok)
    (Path(d) / jl.DEVICE_FILE).write_text("\n", encoding="utf-8")
    try:
        jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open()
        check("빈 DEVICE.txt 는 막는다(IndexError 아님)", False)
    except jl.DeviceMismatch:
        check("빈 DEVICE.txt 는 막는다(IndexError 아님)", True)
    (Path(d) / jl.DEVICE_FILE).write_text("laptop\n", encoding="utf-8")
    (Path(d) / "DEVICE (1).txt").write_text("home\n", encoding="utf-8")
    try:
        jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open()
        check("동기화 충돌 사본(DEVICE (1).txt)이 있으면 막는다", False)
    except jl.DeviceMismatch:
        check("동기화 충돌 사본(DEVICE (1).txt)이 있으면 막는다", True)

# 4-3 값 잠금 축 → 덤프
c = dict(CASE, expect_arrive="14:07", expect_slack_min=[5, 5], expect_margin_min=[0, 5], expect_last_depart="14:00")
r = rec(c)
check("miss_axes — 도착·@ 어긋남을 잡는다(match 는 2값이라 True)", r["match"] is True and r["miss_axes"] == ["arrive", "margin_min"])

# 4-4 GPT 대조(2026-09-24) 잠금
with tempfile.TemporaryDirectory() as d:
    out = io.StringIO()
    lg = jl.JudgmentLogger(d, device="laptop", stream=out).open()
    lg.record(dict(CASE, id="G1", alt_source="37.4991,127.0310"), result(), latency_ms=1)
    lg.record(dict(CASE, id="G1b", alt_dropped={"n": 1, "reasons": {"37.49,127.03": 1}}), result(), latency_ms=1)
    blob = (Path(d) / jl.BLOCKED_NAME).read_text(encoding="utf-8") + out.getvalue()
    check("(GPT #1) 어휘 밖 alt_source·사유 코드 원값이 차단 파일·콘솔에 안 남는다",
          "37.49" not in blob and "alt_source:$.alt_source(own/odsay 밖)" in blob)

    class Boom:
        def write(self, *_):
            raise OSError("disk full")

        def close(self):
            raise OSError("disk full")
    lg._fh = Boom()
    ok = True
    try:
        r0 = lg.record(dict(CASE, id="G2"), result(), latency_ms=1)
        lg.close()
    except Exception:
        ok = False
    check("(GPT #2) 로그 쓰기·닫기 OSError 가 판정 쪽으로 안 샌다 · 로거 실패로 센다", ok and r0 is None and lg.n_log_error == 2)

with tempfile.TemporaryDirectory() as d:
    lg = jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open()
    bad = result("infeasible", "after_last", evidence=[{"grade": "확정", "observed_at": "x", "sectionTime": 3}])
    lg.record(dict(CASE, id="G3", expect="feasible"), bad, latency_ms=1)
    lg.close()
    log_txt = (Path(d) / jl.LOG_NAME).read_text(encoding="utf-8") if (Path(d) / jl.LOG_NAME).exists() else ""
    bl = [json.loads(x) for x in (Path(d) / jl.BLOCKED_NAME).read_text(encoding="utf-8").splitlines()]
    check("(GPT #3) 덤프에서 걸린 ODsay 키 → 로그 줄도 안 쓰고 차단 파일·건수에 반영",
          "G3" not in log_txt and lg.n_blocked == 1 and any("sectionTime" in h for h in bl[0]["hits"])
          and not list(Path(d).glob("regression_fail_dump_*")))

    (Path(d) / "regression_fail_dump_20260101.json").write_text('{"cases": [', encoding="utf-8")
    lg = jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open()
    lg.record(dict(CASE, id="G4", expect=None, expect_last_depart_none=True), result(), latency_ms=1)
    lg.close()
    row = json.loads((Path(d) / jl.LOG_NAME).read_text(encoding="utf-8").splitlines()[-1])
    check("(GPT #4) expect 없이 값 축만 있어도 miss_axes · 덤프", row["expected"] is None
          and row["miss_axes"] == ["last_depart_none"] and lg.n_dumped == 1)
    check("(GPT #7) 덤프는 JSONL append — 옛 깨진 덤프 파일과 무관, 읽고 덮어쓰기 없음", lg.n_log_error == 0)
    lg2 = jl.JudgmentLogger(d, device="laptop", stream=io.StringIO()).open()
    lg2.record(dict(CASE, id="G5", expect="infeasible"), result(), latency_ms=1)
    lg.open()
    lg.record(dict(CASE, id="G6", expect="infeasible"), result(), latency_ms=1)
    lg.close()
    lg2.close()
    ids = [json.loads(x)["case_id"] for p_ in Path(d).glob("regression_fail_dump_*.jsonl")
           for x in p_.read_text(encoding="utf-8").splitlines()]
    check("(GPT #7) 두 기록기가 겹쳐 닫혀도 덤프를 안 잃는다", {"G4", "G5", "G6"} <= set(ids))

# 5 install — multi 재귀는 맨 바깥만
class FakeV:
    R = {"rules_version": "v0.8"}
    tt = NS(fetched_at="2026-09-09")

    def verify_case(self, case):
        if case.get("multi"):
            for n in range(3):
                self.verify_case({"id": f"{case['id']}/{n}", "legs": []})
        return result()


with tempfile.TemporaryDirectory() as d:
    with jl.install(FakeV, jl.JudgmentLogger(d, device="x", stream=io.StringIO())):
        FakeV().verify_case({"id": "M-1", "multi": {"from": "강남", "to": "잠실"}, "expect": "feasible"})
        FakeV().verify_case({"id": "S-1", "legs": []})
    lines = [json.loads(x) for x in (Path(d) / jl.LOG_NAME).read_text(encoding="utf-8").splitlines()]
    check("multi 후보 재귀는 안 적는다(2줄)", [x["case_id"] for x in lines] == ["M-1", "S-1"])
    check("install 이 끝나면 원래 함수로", FakeV.verify_case.__name__ == "verify_case")
    check("규칙·시간표 판이 줄에 실린다", lines[0]["rules_version"] == "v0.8" and lines[0]["timetable_build"] == "2026-09-09")
    try:
        with jl.install(FakeV, jl.JudgmentLogger(d, device="other", stream=io.StringIO())):
            pass
    except jl.DeviceMismatch:
        pass
    check("기기 표식에 막히면 패치가 남지 않는다", FakeV.verify_case.__name__ == "verify_case")

# 6 분류 지표 — sklearn 대조
try:
    from sklearn.metrics import confusion_matrix, precision_recall_fscore_support
    rnd = random.Random(0)
    labs = ["feasible", "infeasible"]
    ok = True
    for _ in range(50):
        n = rnd.randint(5, 60)
        yt = [rnd.choice(labs) for _ in range(n)]
        yp = [rnd.choice(labs) for _ in range(n)]
        mine = classify_report(yt, yp, labs)
        cm = confusion_matrix(yt, yp, labels=labs).tolist()
        p, r_, f, s = precision_recall_fscore_support(yt, yp, labels=labs, zero_division=0)
        ok &= mine.matrix == cm
        for i, lb in enumerate(labs):
            c = mine.per_class[lb]
            ok &= all(abs((c[k] if c[k] is not None else 0) - v) < 1e-9 for k, v in (("precision", p[i]), ("recall", r_[i]), ("f1", f[i])))
            ok &= c["support"] == s[i]
        for avg in ("macro", "weighted"):
            p2, r2, f2, _ = precision_recall_fscore_support(yt, yp, labels=labs, average=avg, zero_division=0)
            m = getattr(mine, avg)
            ok &= all(abs(m[k] - v) < 1e-9 for k, v in (("precision", p2), ("recall", r2), ("f1", f2)))
    check("confusion·P/R/F1·macro/weighted 가 sklearn 과 같다(50회 무작위)", ok)
    one = classify_report(["feasible"] * 2 + ["infeasible"] * 2, ["feasible"] * 4, labs)
    check("불가를 안 내는 판정기 — macro F1 0.333(sklearn 과 같게, 부풀림 없음)", abs(one.macro["f1"] - 1 / 3) < 1e-9)
except ImportError:
    print("SKIP sklearn 없음")

b = binary_report(["no_data", "x", "no_data", "x"], ["no_data", "no_data", "x", "x"], "no_data")
check("binary_report TP1 FP1 FN1 TN1 · P=R=0.5", (b["tp"], b["fp"], b["fn"], b["tn"]) == (1, 1, 1, 1) and b["precision"] == 0.5)
cr = classify_report(["a"], ["a"], ["a", "b"])
check("분모 0 은 precision None(표 —)", cr.per_class["b"]["precision"] is None)
check("(GPT #6) 실제·예측 모두 없는 클래스 F1 = 0 (None 아님) · 표에도 0.000",
      cr.per_class["b"]["f1"] == 0.0 and "| b | — | — | 0.000 |" in cr.to_markdown())
cat = misclassified_catalog(list("abcde"), ["f", "f", "i", "f", "f"], ["i", "i", "f", "f", "i"], 2)
check("카탈로그 셀 정렬·상한", list(cat) == [("f", "i"), ("i", "f")] and cat[("f", "i")]["count"] == 3
      and len(cat[("f", "i")]["examples"]) == 2)

# 7 보고서 — 끝까지
rows = [rec(dict(CASE, id=f"F{i}")) for i in range(3)]
rows.append(rec(dict(CASE, id="N1", expect="infeasible", expect_reason="no_data"), result("infeasible", "no_data")))
rows.append(rec(dict(CASE, id="N2", expect="infeasible", expect_reason="after_last"), result("infeasible", "no_data")))
rows.append(rec(dict(CASE, id="N3", expect="infeasible"), result("feasible")))
rows.append(rec(dict(CASE, id="SC1", expect=None), result("infeasible", "after_last")))
rows[-1]["expected"] = None
rows.append(rec(dict(CASE, id="OLD1", expect="unknown", expect_reason="no_data"), result("infeasible", "no_data")))
md = rep.report(rows)
check("보고서 절 6개", all(f"## {i}." in md for i in range(1, 7)))
check("no_data P=0.5 R=1.0 (TP1 FP1 FN0)", "| 1 | 1 | 0 |" in md and "0.500 | 1.000" in md)
check("카탈로그에 N3(성립 오판)·N2(이유 오판)", "N3" in md and "N2" in md and "after_last → no_data" in md)
check("기대 성립 줄의 진실 이유는 (성립)", "(성립)" in md)
check("옛 4값 기대는 채점에서 빼고 따로 적는다", "OLD1(unknown)" in md)
mix = [dict(rec(dict(CASE, id="R1")), source="regression"),
       dict(rec(dict(CASE, id="F1", expect="infeasible"), result("feasible")), source="field")]
md2 = rep.report(mix)
check("(GPT #5) 회귀·실측을 한 점수로 합치지 않는다 — field accuracy 0.000 · regression 1.000 따로",
      "source=field · 실데이터 · n=1" in md2 and "source=regression · 실데이터 · n=1" in md2
      and "accuracy 0.000 (n=1)" in md2 and "n=2" not in md2.split("## 3.")[0])
nox = [dict(rec(dict(CASE, id="X1", expect=None, expect_arrive="14:09")), expected=None)]
check("(GPT #4) 보고서 카탈로그가 expected 없는 값 축 실패를 싣는다", "X1" in rep.report(nox).split("## 6.")[0])
rows2 = [dict(r, run_id="zzz-old", ts="2026-09-24T09:00:00+09:00") for r in rows[:2]] + \
        [dict(r, run_id="aaa-new", ts="2026-09-24T10:00:00+09:00") for r in rows[2:4]]
check("latest 는 run_id 문자열이 아니라 시각으로 고른다", {r["run_id"] for r in rep.pick_runs(rows2, "latest")} == {"aaa-new"})

bad = [n for n, ok in checks if not ok]
print(f"\n판정 로그 시험 {len(checks) - len(bad)}/{len(checks)}" + (f" — 실패 {bad}" if bad else ""))
if __name__ == "__main__":
    sys.exit(1 if bad else 0)


def test_every_check_passes():
    """pytest 입구 (2026-09-28) — 위 검사는 이 파일을 불러올 때 이미 돈다. 실패가 0 이어야 한다.

    자세한 줄별 결과는 `python final_project_cs/tests/unit/travel/mobility/test_judgment_log.py` 로 본다.
    """
    assert not bad, f"실패 {bad}"
