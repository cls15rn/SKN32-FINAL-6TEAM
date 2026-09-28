# final_project_cs/tests/unit/travel/mobility/test_timeutil.py — 시각 자(尺) 단위테스트
# 실행: 저장소 루트에서  python final_project_cs/tests/unit/travel/mobility/test_timeutil.py
# 실패하면 종료코드 1. 여기서 막히면 판정기 전체가 틀린다.
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "final_project_cs"))
from app.modules.travel_ops.mobility.engine.timeutil import (to_min, to_service_min, fmt_min, fmt_wall,
                                       normalize_raw, day_type_of, is_next_day)

CASES = []


def eq(got, want, note):
    CASES.append((got == want, note, got, want))


# ── 파싱 — 24 시 이상을 살린다 ──
eq(to_min("24:50:00"), 1490, "24:50:00 → 1490 (strptime 이면 여기서 죽는다)")
eq(to_min("27:59:00"), 1679, "버스 막차 27:59 도 같은 자로 담긴다")
eq(to_min("24:30"), 1470, "혼잡도 슬롯 24:30")
eq(to_min("235200"), 1432, "HHMMSS 표기")
eq(to_min("2352"), 1432, "HHMM 표기")
eq(to_min(1490), 1490, "이미 분이면 그대로")
eq(to_min("05:36:00"), 336, "평범한 시각")

# ── 시각이 아닌 값 ──
eq(to_min("000000"), None, "'000000' 은 출발 없음이지 0 시가 아니다 (가짜 막차의 원인)")
eq(to_min("0"), None, "'0' 도 출발 없음")
eq(to_min(None), None, "None")
eq(to_min(""), None, "빈 문자열")
eq(to_min("31:00"), None, "30 시를 넘는 값은 시각으로 보지 않는다")
eq(to_min("12:75"), None, "분이 60 을 넘으면 시각이 아니다")

# ── 정렬 — 이게 v1 이 조용히 틀렸던 자리다 ──
deps = ["05:36:00", "23:53:00", "24:05:00", "24:50:00"]
mins = [to_min(t) for t in deps]
eq(min(mins), 336, "첫차는 05:36 이다 (00:05 로 되돌리면 24:05 가 첫차가 된다)")
eq(max(mins), 1490, "막차는 24:50 이다 (되돌리면 23:53 이 막차가 된다)")

# ── 사람이 준 벽시계 → 운행일 축 ──
eq(to_service_min("00:30"), 1470, "고객의 00:30 은 24:30 과 같은 값이어야 한다")
eq(to_service_min("03:59"), 1679, "03:59 는 전날 운행분의 연장")
eq(to_service_min("04:00"), 240, "04:00 부터는 그날")
eq(to_service_min("06:00"), 360, "아침 시각을 30:00 으로 올리지 않는다")
eq(to_service_min("24:30"), 1470, "이미 24 시 이상이면 그대로")

# ── 표기 ──
eq(fmt_min(1490), "24:50", "판정 근거에는 24:50 으로 남긴다")
eq(fmt_min(1490, seconds=True), "24:50:00", "시간표 표기")
eq(fmt_wall(1490), "00:50 (+1일)", "고객에게는 벽시계로")
eq(fmt_wall(336), "05:36", "당일이면 꼬리표 없음")
eq(is_next_day(1490), True, "24 시 이상은 익일")
eq(is_next_day(1439), False, "23:59 는 당일")

# ── raw 정규화 (수집 스크립트와 같은 규칙) ──
eq(normalize_raw("000500"), 1445, "소스의 '000500' 은 24:05 로 읽는다")
eq(normalize_raw("000000"), None, "'000000' 은 보정 전에 걸러야 한다")

# ── 요일축 ──
H = {"2026-10-03", "2026-10-09"}
eq(day_type_of(date(2026, 9, 11), H), "weekday", "금요일")
eq(day_type_of(date(2026, 9, 12), H), "holiday", "토요일")
eq(day_type_of(date(2026, 9, 13), H), "holiday", "일요일")
eq(day_type_of(date(2026, 10, 9), H), "holiday", "한글날(금)")

bad = [c for c in CASES if not c[0]]
for ok, note, got, want in CASES:
    print(("  OK  " if ok else "  FAIL") + f" {note}" + ("" if ok else f"  → {got!r} (기대 {want!r})"))
print(f"\n{len(CASES) - len(bad)}/{len(CASES)} 통과")
if __name__ == "__main__":
    sys.exit(1 if bad else 0)


def test_every_check_passes():
    """pytest 입구 (2026-09-28) — 위 검사는 이 파일을 불러올 때 이미 돈다. 실패가 0 이어야 한다.

    자세한 줄별 결과는 `python final_project_cs/tests/unit/travel/mobility/test_timeutil.py` 로 본다.
    """
    assert not bad, f"실패 {bad}"
