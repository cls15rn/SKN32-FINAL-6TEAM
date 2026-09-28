# mobility_scripts/mobility_checks/make_mini_timetable.py — 축소 시간표 생성 (판정 로직 확인용)
# 실행: 저장소 루트에서  python mobility_scripts/mobility_checks/make_mini_timetable.py
# 출력: final_project_cs/tests/unit/travel/mobility/mini_timetable_v2.jsonl  (timetable_v1.jsonl 과 같은 스키마)
#
# ★★ 여기 시각은 가짜다. 실제 운행 시각이 아니다. 실제 판정은 반드시 processed 의
#    timetable_v1.jsonl 로 다시 돌린다.
#
# ★ v1 의 교훈 — 축소 시간표가 24/24 를 통과했는데 실제로는 막차 판정이 틀려 있었다.
#   가짜 시각에 **실제 데이터의 성질이 없었기 때문**이다. 그래서 v2 는 C4·C5 가 소스 간
#   대조로 잡아낸 네 가지 성질을 일부러 심는다. 이게 없으면 이 파일은 검증기를 통과시키는
#   장식일 뿐이다.
#     ① 24 시를 넘는 출발 시각 (24:05 · 24:50)
#     ② 막차 근처 단축운행 (05호선 여의도 애오개행 — 왕십리 앞에서 내려준다)
#     ③ 종착 열차가 막차로 잡힘 (05호선 여의도 24:47 여의도행 — 손님이 못 타는 행)
#     ④ 2호선 신정지선(토요일 예외 대상 역)이 들어 있을 것
#   덧붙여 ⑤ dest_nm 이 빈 행, ⑥ dep_time 이 없는 시·종착역 행도 넣는다.
#
# 역 순서·소요시간은 **실제** line_station_order_v1.json 에서 가져온다. 역명과 순서까지
# 지어내면 LineOrder 조회가 통째로 헛돈다.
import json, sys, argparse, collections
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "final_project_cs"))
from app.modules.travel_ops.mobility.engine.line_order import LineOrder  # noqa: E402
from app.modules.travel_ops.mobility.engine.timeutil import fmt_min      # noqa: E402

OUT = REPO / "final_project_cs" / "tests" / "unit" / "travel" / "mobility" / "mini_timetable_v2.jsonl"
FETCHED = "2026-09-09"


def M(hhmm):
    """'24:50' → 1490. 축소 시간표도 같은 자를 쓴다."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# (line, dest, 출발역, 첫차, 막차, 배차, day_types, dir)
# dir 은 참고용으로만 넣는다 — 판정기는 dir 을 보지 않고 dest_nm 으로 방향을 정한다.
ROUTES = [
    # 02호선 내선(fr_order 오름차순). 강변→잠실 이 이 안에 있다.
    dict(line="02호선", start="시청", dest="신도림", first="05:30", last="24:20",
         headway=10, days=("weekday", "holiday"), dir="U"),
    # 02호선 막차 한 편만 더 — ① 24 시 넘김. 강변 출발이 24:50 이 되도록 잡았다.
    dict(line="02호선", start="시청", dest="신도림", first="24:22", last="24:22",
         headway=10, days=("weekday",), dir="U"),
    # 02호선 외선(fr_order 내림차순) — 강변에서 성수 방향. 잠실로는 못 간다(방향 확인용).
    dict(line="02호선", start="잠실", dest="성수", first="05:40", last="24:10",
         headway=12, days=("weekday", "holiday"), dir="D", desc=True),
    # ④ 신정지선. 토요일도 holiday 시간표 하나로 덮여 있다는 게 예외의 출발점이다.
    dict(line="02호선", start="신도림", dest="까치산", first="05:50", last="24:00",
         headway=12, days=("weekday", "holiday"), dir="D"),
    dict(line="02호선", start="까치산", dest="신도림", first="05:45", last="23:50",
         headway=12, days=("weekday",), dir="U", origin_self_dest=True),
    # ⑦ 순환선 시발역 — 성수 출발은 전부 '성수행'(한 바퀴). 실제 데이터가 이렇다
    #    (02호선 성수 평일 유효 출발 592편이 전부 dest='성수'). 입고 열차가 아니라 시발이다.
    dict(line="02호선", start="성수", dest="성수", first="05:30", last="24:10",
         headway=6, days=("weekday", "holiday"), dir="U", origin_self_dest=True),
    # 지선 휴일 편성을 **일부러 두 토막으로** 냈다. 12:00~16:00 에 4시간 공백이 생긴다.
    # 실제 운행이 아니라 service_window.gap_max_min 규칙을 걸어 보기 위한 구멍이다.
    dict(line="02호선", start="까치산", dest="신도림", first="05:45", last="12:00",
         headway=12, days=("holiday",), dir="U", origin_self_dest=True),
    dict(line="02호선", start="까치산", dest="신도림", first="16:00", last="23:45",
         headway=12, days=("holiday",), dir="U", origin_self_dest=True),
    # 05호선 여의도 → 왕십리 방향(정상 운행분)
    dict(line="05호선", start="방화", dest="하남검단산", first="05:20", last="23:30",
         headway=10, days=("weekday", "holiday"), dir="U"),
    # ② 막차 근처 단축운행 — 애오개행. 여의도에서 타면 왕십리 앞에서 내려준다.
    dict(line="05호선", start="방화", dest="애오개", first="24:05", last="24:35",
         headway=15, days=("weekday",), dir="U"),
    # 08호선 양방향 — 잠실 환승 케이스용
    dict(line="08호선", start="암사", dest="모란", first="05:35", last="24:15",
         headway=11, days=("weekday", "holiday"), dir="D"),
    dict(line="08호선", start="모란", dest="암사", first="05:35", last="24:15",
         headway=11, days=("weekday", "holiday"), dir="U"),
    # 05호선 반대 방향
    dict(line="05호선", start="하남검단산", dest="방화", first="05:25", last="23:35",
         headway=10, days=("weekday", "holiday"), dir="D"),
]

# 손으로 박는 특수 행 — 규칙이 없으면 안 생기는 것들
EXTRA = [
    # ③ 종착 열차. 이 역에서 운행을 마치는 행이라 손님이 탈 수 없는데, 시간표만 보면
    #    24:47 은 완벽하게 정상적인 값이다. 거르지 않으면 가짜 막차가 된다.
    dict(line="05호선", station="여의도", day="weekday", dep="24:47", dest="여의도", dir="U"),
    dict(line="05호선", station="애오개", day="weekday", dep="25:01", dest="애오개", dir="U"),
    # ⑤ dest_nm 이 빈 행 — 행선지를 모르는 열차로 '목적지까지 간다'고 말하지 않는다.
    dict(line="05호선", station="여의도", day="weekday", dep="24:55", dest=None, dir="U"),
    # ⑥ 시·종착역의 '출발 없음'. 소스가 '000000' 으로 준다 → dep_time None.
    dict(line="02호선", station="성수", day="weekday", dep=None, dest="성수", dir="U"),
]


def order_of(lo, line, start, dest, desc=False):
    """출발역부터 행선지까지 열차가 지나는 역 순서.

    순환선은 최단경로가 실제 운행 방향과 다르다 — fr_order 로 방향을 정한다.
    desc=True 면 fr_order 내림차순(외선) 으로 돈다.
    """
    if lo.is_loop(line):
        names = lo.main_order(line)
        if start in names and dest in names:
            if desc:
                names = names[::-1]
            i, j = names.index(start), names.index(dest)
            if i == j:                                   # 시발=종착 → 한 바퀴
                return names[i:] + names[:i + 1]
            return names[i:j + 1] if i <= j else names[i:] + names[:j + 1]
    return lo.path(line, start, dest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", default=None)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    if a.order:
        lo = LineOrder.load(a.order)
    else:
        from mobility_scripts.collect._paths import PROCESSED
        lo = LineOrder.load(PROCESSED / "mobility" / "line_station_order_v1.json")

    rows, seen = [], set()

    def emit(line, station, day, dep_min, dest, dr):
        key = (line, station, day, dep_min, dest, dr)
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "station_key": f"{line}|{station}", "station_cd": None, "station_nm": station,
            "station_nm_en": None, "line": line, "dir": dr, "day_type": day,
            "dep_time": (fmt_min(dep_min, seconds=True) if dep_min is not None else None),
            "arr_time": None, "dest_nm": dest, "train_no": None, "express": None,
            "source": "mini_synthetic", "source_station_id": None,
            "fetched_at": FETCHED, "fetched_at_precision": "day",
        })

    for r in ROUTES:
        path = order_of(lo, r["line"], r["start"], r["dest"], r.get("desc", False))
        if not path:
            raise SystemExit(f"역 순서를 못 만들었다: {r['line']} {r['start']}→{r['dest']}")
        offs, cum = [0], 0.0
        for i in range(len(path) - 1):
            t = lo.travel_min(r["line"], path[i], path[i + 1])
            cum += t if t is not None else 2.0            # 확정 간선이 없으면 2분으로 둔다
            offs.append(cum)
        for day in r["days"]:
            t = M(r["first"])
            while t <= M(r["last"]):
                for k, (st, off) in enumerate(zip(path, offs)):
                    if st == r["dest"] and k > 0:         # 행선지 역은 출발이 없다
                        continue
                    # ⑦ 시발역은 소스가 행선지를 역 자신으로 준다
                    dest = st if (r.get("origin_self_dest") and k == 0) else r["dest"]
                    emit(r["line"], st, day, t + round(off), dest, r["dir"])
                t += r["headway"]

    for e in EXTRA:
        emit(e["line"], e["station"], e["day"],
             M(e["dep"]) if e["dep"] else None, e["dest"], e["dir"])

    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 심어 둔 성질이 실제로 파일에 있는지 확인한다. 없으면 이 파일은 장식이다.
    over24 = [r for r in rows if r["dep_time"] and int(r["dep_time"][:2]) >= 24]
    term = [r for r in rows if r["dest_nm"] == r["station_nm"] and r["dep_time"]]
    nodest = [r for r in rows if r["dest_nm"] is None]
    nodep = [r for r in rows if r["dep_time"] is None]
    spur = [r for r in rows if r["station_nm"] in ("도림천", "양천구청", "신정네거리", "까치산")]
    orig = collections.Counter()
    tot = collections.Counter()
    for r in rows:
        if r["dep_time"]:
            tot[(r["line"], r["station_nm"], r["day_type"])] += 1
            if r["dest_nm"] == r["station_nm"]:
                orig[(r["line"], r["station_nm"], r["day_type"])] += 1
    first_st = {k: orig[k] / tot[k] for k in orig if orig[k] / tot[k] >= 0.5}
    print(f"{a.out}  {len(rows):,}행")
    print(f"  ① 24 시 이상 출발 {len(over24):,}행 (최대 {max(r['dep_time'] for r in over24)})")
    print(f"  ② 단축운행 행선지(애오개행) {sum(1 for r in rows if r['dest_nm']=='애오개'):,}행")
    print(f"  ③ 종착 열차(dest==역) {len(term)}행")
    print(f"  ④ 신정지선 역 {len(spur):,}행")
    print(f"  ⑤ 행선지 없음 {len(nodest)}행 · ⑥ 출발 없음 {len(nodep)}행")
    print(f"  ⑦ 시발역(dest==역 비율 50% 이상) {len(first_st)}조합: "
          + ", ".join(f"{k[1]}[{k[2]}] {v:.0%}" for k, v in sorted(first_st.items())[:6]))
    for k in "①②③④":
        pass
    if not (over24 and term and nodest and nodep and spur and first_st):
        raise SystemExit("심어야 할 성질이 빠졌다 — 이대로면 v1 과 같은 착시가 생긴다")


if __name__ == "__main__":
    main()
