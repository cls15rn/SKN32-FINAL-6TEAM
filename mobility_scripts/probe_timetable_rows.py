# mobility_scripts/probe_timetable_rows.py — 실제 시간표의 특정 역 행을 눈으로 보는 조사용 스크립트
# 실행: 저장소 루트에서  python mobility_scripts/probe_timetable_rows.py
#       python mobility_scripts/probe_timetable_rows.py --station 02호선:성수 --day weekday --around 14:00
#
# 판정을 하지 않는다. 검증기가 무엇을 보고 그런 판정을 냈는지 원자료로 확인하는 용도다.
# 2026-09-10 실제 시간표 실행에서 나온 네 가지를 확인하려고 만들었다.
#   ① 02호선 성수 — 출발 592편이 전부 dest=성수 라 종착열차 필터에 통째로 걸렸다. 시발인가 종착인가
#   ② 02호선 까치산 — 105편 전부 dest=까치산. 신정지선 종점의 시발 열차로 보인다
#   ③ 02호선 신도림 — 까치산행(신정지선)이 있는가. 없다면 지선 열차가 도림천 시발이라는 뜻
#   ④ 02호선 강변 — 요청 시각마다 '대기 0분'이 나왔다. 그 분에 정말 열차가 있는가
import argparse, json, collections, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "final_project_cs"))
sys.path.insert(0, str(REPO))
from app.modules.travel_ops.mobility.engine.timeutil import (to_min,  # noqa: E402
                                                          to_service_min, fmt_min)

DEFAULT = ["02호선:성수", "02호선:까치산", "02호선:신도림", "02호선:도림천",
           "02호선:강변", "02호선:잠실"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timetable")
    ap.add_argument("--station", action="append", help="노선:역 (여러 번 가능)")
    ap.add_argument("--day", default=None, help="weekday / holiday (기본: 둘 다)")
    ap.add_argument("--around", default="14:00", help="이 시각 앞뒤 행을 나열")
    ap.add_argument("--window", type=int, default=12, help="앞뒤 몇 분")
    a = ap.parse_args()
    if not a.timetable:
        from mobility_scripts.collect._paths import PROCESSED
        a.timetable = str(PROCESSED / "mobility" / "timetable_v1.jsonl")
    targets = set()
    for s in (a.station or DEFAULT):
        ln, nm = s.split(":", 1)
        targets.add((ln, nm))

    rows = collections.defaultdict(list)      # (line, nm, day) → [(min, dir, dest)]
    nodep = collections.Counter()
    nodep_dest = collections.defaultdict(collections.Counter)
    ids = collections.defaultdict(collections.Counter)
    with open(a.timetable, encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue
            r = json.loads(raw)
            k = (r.get("line"), r.get("station_nm"))
            if k not in targets:
                continue
            m = to_min(r.get("dep_time"))
            if m is None:
                nodep[(k[0], k[1], r.get("day_type"))] += 1
                nodep_dest[(k[0], k[1], r.get("day_type"))][
                    (r.get("source_station_id"), r.get("dest_nm"))] += 1
                continue
            rows[(k[0], k[1], r.get("day_type"))].append((m, r.get("dir"), r.get("dest_nm")))
            ids[(k[0], k[1], r.get("day_type"))][
                (r.get("source_station_id"), r.get("source"), r.get("dest_nm"))] += 1

    around = to_service_min(a.around)
    for key in sorted(rows):
        ln, nm, day = key
        if a.day and day != a.day:
            continue
        rs = sorted(rows[key])
        print(f"\n{'='*70}\n{ln} {nm} [{day}]  출발 {len(rs)}행 · 출발없음 {nodep.get(key,0)}행")
        print(f"  첫 출발 {fmt_min(rs[0][0])} {rs[0][2]}행 · 마지막 {fmt_min(rs[-1][0])} {rs[-1][2]}행")

        # dest × dir 분포 — 방향이 dir 로 갈리는지, dest 가 그 역 자신인지
        cnt = collections.Counter((d, dr) for _, dr, d in rs)
        print("  행선지 × dir:")
        for (dest, dr), n in cnt.most_common(12):
            mark = "  ← 이 역 자신" if dest == nm else ""
            span = [m for m, x, y in rs if y == dest and x == dr]
            print(f"    {str(dest):>14} {dr}  {n:>5}편  {fmt_min(min(span))}~{fmt_min(max(span))}{mark}")
        if len(cnt) > 12:
            print(f"    … 외 {len(cnt)-12}종")

        # dest == 역 인 행의 시각 분포 — 하루 종일이면 시발, 막차 근처뿐이면 입고
        self_rows = [m for m, dr, d in rs if d == nm]
        if self_rows:
            frac = len(self_rows) / len(rs)
            print(f"  ★ dest == '{nm}' 인 행 {len(self_rows)}편 ({frac:.0%}) "
                  f"{fmt_min(min(self_rows))}~{fmt_min(max(self_rows))}")
            print("     → 하루 종일 고르게 있으면 **시발 열차**, 막차 근처에만 있으면 **입고 열차**다.")

        # ★ 역 ID — 같은 역이 승강장별로 다른 ID 로 오는지. 지선 편성이 통째로 비면 여기가 원인이다.
        print("  출발행 source_station_id × 행선지:")
        for (sid, src, dest), n in ids[key].most_common(10):
            print(f"    {str(sid):>16} {src:<22} {str(dest):>14} {n:>5}편")
        if nodep_dest.get(key):
            print("  출발없음(도착 전용) source_station_id × 행선지:")
            for (sid, dest), n in nodep_dest[key].most_common(10):
                print(f"    {str(sid):>16} {str(dest):>14} {n:>5}편")

        # 요청 시각 앞뒤 — '대기 0분' 확인
        near = [(m, dr, d) for m, dr, d in rs if around - a.window <= m <= around + a.window]
        print(f"  {a.around} 앞뒤 {a.window}분: {len(near)}편")
        for m, dr, d in near[:20]:
            print(f"    {fmt_min(m)} {dr} {d}행")


if __name__ == "__main__":
    main()
