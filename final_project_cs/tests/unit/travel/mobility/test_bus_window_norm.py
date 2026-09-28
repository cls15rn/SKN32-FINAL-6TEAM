# final_project_cs/tests/unit/travel/mobility/test_bus_window_norm.py — 45번 방 · 버스 운행 구간 정규화(normalize_window) 단위 시험
# 실행: python final_project_cs/tests/unit/travel/mobility/test_bus_window_norm.py      (pytest 로도 돈다)
# 판정 회귀(night_legs_v1.json)가 못 보는 것을 잠근다 — A21 보정 결과 자체(판정은 service_days=None 에서 먼저 끊긴다),
# 혼합 캐시 중단, 24시 넘은 역전, 기준일보다 이른 날짜.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "mobility_scripts" / "collect"))
from build_bus_all_v1 import normalize_window  # noqa: E402

B = "20260910"


def t(first, last):
    return normalize_window(first, last, B)


def test_normal_day_route():
    assert t("20260910042000", "20260910224000") == ("04:20", "22:40", "확정", None)


def test_normal_night_route_crossing_midnight():            # N15
    assert t("20260910233000", "20260911033000") == ("23:30", "27:30", "확정", None)


def test_n26_first_on_next_day():                           # N26 원문
    assert t("20260911000000", "20260911032500") == ("24:00", "27:25", "확정", None)


def test_a21_same_day_last_is_estimated():                  # 심야A21 원문
    f, l, g, note = t("20260910230000", "20260910034000")
    assert (f, l, g) == ("23:00", "27:40", "추정")
    assert "추정 보정" in note


def test_8772_first_eq_last_midnight_is_uninterpretable():  # 8772 원문
    f, l, g, note = t("20260911000000", "20260911000000")
    assert (f, l, g) == (None, None, "근거없음")
    assert "신뢰성 있게 해석할 수 없음" in note


def test_inverted_after_24_is_not_patched():                # 첫차 29:00 > 막차 27:00 — 보정하지 않고 해석 불가
    f, l, g, note = t("20260911050000", "20260911030000")
    assert (f, l, g) == (None, None, "근거없음")


def test_same_day_inversion_needs_both_before_24():         # 첫차 24:30 · 막차 03:00(같은 날) — 보정 조건 밖
    f, l, g, _ = t("20260911003000", "20260910030000")
    assert (f, l, g) == (None, None, "근거없음")


def test_mixed_cache_date_stops():                          # 기준일+2 = 자정 끼고 수집한 혼합 캐시
    try:
        t("20260912050000", "20260912220000")
    except ValueError:
        return
    raise AssertionError("기준일 밖 날짜를 조용히 정규화했다")


def test_date_before_base_stops():                          # 종전 bus_time 은 음수 차이를 무시했다
    try:
        t("20260909050000", "20260910220000")
    except ValueError:
        return
    raise AssertionError("기준일보다 이른 날짜를 조용히 정규화했다")


def test_missing_raw():
    f, l, g, _ = t("", "20260910220000")
    assert (f, g) == (None, "근거없음")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL {name} {e}")
    print(f"실패 {fails}")
    sys.exit(1 if fails else 0)
