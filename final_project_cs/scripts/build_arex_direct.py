# -*- coding: utf-8 -*-
"""공항철도 **직통열차** 출발 시각표를 이동 모듈 시간표에서 뽑는다 — 예약 모듈(`offers/arex.py`)이 읽는 작은 파일을 만든다. `[2026-10-15]`

왜: AREX 직통은 TAGO 기차(`GetStrtpntAlocFndTrainInfo`)에 없다(서울→인천공항T1 0편, 10-15 실호출). 예매 사이트는 조건을 담은 링크가 안 된다
(검색 조건을 세션에만 둔다). 그런데 이동 모듈이 모아 둔 지하철 시간표(`datasets/mobility/processed/mobility/timetable_v1.jsonl.gz`,
TAGO 지하철정보 `SubwayInfo/GetSubwaySttnAcctoSchdulList` 수집)에 공항철도 14역이 있고, 직통 편도 섞여 있다(열차 번호 · 직통 표시 칸은 없다).

가르는 규칙(값은 전부 시간표 파일에서 — 손으로 적는 시각 없음):
  - 서울역 → 공항(하행 D): 서울역 출발 중 **다음 역 공덕 하행에 이어지는 행이 없는 편**. 「이어진다」 = 서울역 → 공덕 출발 차이의 최빈값(일반 열차) ± 30초.
  - 공항 → 서울역(상행 U): 인천공항2터미널 출발 중 **인천공항1터미널 상행에 일반 열차 최빈 차이 ± 60초 행이 없는 편**. 그 편의 T1 출발은
    직통 편들의 T2 → T1 차이 최빈값으로 찾고, 그 시각의 T1 행이 실제로 있을 때만 적는다.
  - 요일은 시간표 그대로 weekday · holiday 둘(수도권 TAGO 는 토요일을 holiday 로 준다 — 이동 모듈 `timetable_v1_coverage.md`).

대조(10-15, playdata, AREX 예매 화면 1회씩 — 공식 API 아님): 2026-10-16(금) 서울역→T1 09시 이후 21편 · T1→서울역 · T2→서울역 앞 6편,
2026-10-17(토) 서울역→T2 26편(06:00~23:00)이 이 규칙의 결과와 같았다.

실행 위치: final_project_cs
  python -m scripts.build_arex_direct                      # 기본 입력 ../datasets/mobility/processed/mobility/timetable_v1.jsonl.gz
  python -m scripts.build_arex_direct --timetable <경로>    # 다른 판
출력: app/domains/travel_ops/components/booking/offers/arex_direct.json (커밋한다 — 수 KB)
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMETABLE = ROOT.parent / "datasets" / "mobility" / "processed" / "mobility" / "timetable_v1.jsonl.gz"
OUTPUT = ROOT / "app" / "domains" / "travel_ops" / "components" / "booking" / "offers" / "arex_direct.json"
LINE = "공항철도"
SEOUL, NEXT_DOWN = "서울역", "공덕"
T1, T2 = "인천공항1터미널", "인천공항2터미널"


def _sec(clock: str) -> int:
    hour, minute, second = (int(part) for part in clock.split(":"))
    return hour * 3600 + minute * 60 + second


def _clock(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def _modal(starts: list[int], follows: set[int], low: int, high: int) -> int:
    """앞 역 출발 → 다음 역 출발 차이의 최빈값(`low`~`high` 초 안)."""
    counted = Counter(after - start for start in starts for after in follows if low <= after - start <= high)
    if not counted:
        raise SystemExit(f"차이를 못 찾음({low}~{high}초) — 시간표 모양이 바뀌었는지 확인")
    return counted.most_common(1)[0][0]


def _unfollowed(starts: list[int], follows: set[int], gap: int, slack: int) -> list[int]:
    return [start for start in starts if not any(start + gap + delta in follows for delta in range(-slack, slack + 1, 30))]


def build(rows: list[dict]) -> dict:
    runs: dict[str, dict[str, list[str]]] = {}
    checks: dict[str, dict[str, int]] = {}
    for day_type in sorted({row["day_type"] for row in rows}):
        def times(station: str, direction: str) -> list[int]:
            return sorted(_sec(row["dep_time"]) for row in rows
                          if row["day_type"] == day_type and row["station_nm"] == station and row["dir"] == direction)
        seoul, gongdeok = times(SEOUL, "D"), set(times(NEXT_DOWN, "D"))
        down = _unfollowed(seoul, gongdeok, _modal(seoul, gongdeok, 120, 600), 30)
        t2_up, t1_up = times(T2, "U"), set(times(T1, "U"))
        general = _modal(t2_up, t1_up, 240, 600)
        up_t2 = _unfollowed(t2_up, t1_up, general, 60)
        express_gap = Counter(t1 - t2 for t2 in up_t2 for t1 in t1_up if 300 <= t1 - t2 <= 720).most_common(1)
        up_t1 = [t2 + express_gap[0][0] for t2 in up_t2 if express_gap and t2 + express_gap[0][0] in t1_up]
        runs[day_type] = {SEOUL: [_clock(s) for s in down], T2: [_clock(s) for s in up_t2], T1: [_clock(s) for s in up_t1]}
        checks[day_type] = {"seoul_down_rows": len(seoul), "direct_down": len(down), "t2_up_rows": len(t2_up),
                            "direct_up_t2": len(up_t2), "direct_up_t1": len(up_t1),
                            "t2_to_t1_express_gap_s": express_gap[0][0] if express_gap else -1}
    return {"runs": runs, "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timetable", type=Path, default=DEFAULT_TIMETABLE)
    args = parser.parse_args()
    if not args.timetable.exists():
        sys.exit(f"시간표 파일 없음: {args.timetable}")
    with gzip.open(args.timetable, "rt", encoding="utf-8") as handle:
        rows = [row for row in map(json.loads, handle) if row.get("line") == LINE]
    if not rows:
        sys.exit("공항철도 행이 없음")
    built = build(rows)
    fetched = sorted({str(row.get("fetched_at")) for row in rows if row.get("fetched_at")})
    payload = {
        "about": "공항철도 직통열차 출발 시각(출발역별). 서울역 → 공항, 공항(T2 · T1) → 서울역. 요일 시간표 — 날짜별 운휴 · 임시 변경은 없다",
        "source": "datasets/mobility/processed/mobility/timetable_v1.jsonl.gz (TAGO 지하철정보)",
        "collected": fetched[-1] if fetched else None,
        "built_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "rule": "서울역 하행 중 공덕 하행에 안 이어지는 편 · T2 상행 중 T1 상행에 일반 열차 간격으로 안 이어지는 편(scripts/build_arex_direct.py)",
        **built,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"입력 {args.timetable} · 공항철도 {len(rows)}행 · 수집 {payload['collected']}")
    for day_type, check in built["checks"].items():
        print(f"  {day_type}: {check}")
        for station, clocks in built["runs"][day_type].items():
            print(f"    {station} 출발 {len(clocks)}편 · {clocks[:3]} … {clocks[-2:]}")
    print(f"출력 {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
