# -*- coding: utf-8 -*-
"""숙소 · 항공 확장 에이전트 시험 폴더의 pytest 설정 — 두 층. `[2026-10-12]` (이동 모듈 `tests/unit/travel/mobility/conftest.py` 와 같은 방식)

★**병합 게이트(기본)** — `pytest tests/unit/travel/booking -q` 로 도는 것. develop CI(`tests/unit` 전체)도 이것만 돈다.
  공식: 「주요 기능마다 시험 하나 — 이게 깨지면 그 기능이 죽은 것」. 목록은 아래 `GATE`(파일 → 시험 이름 → 깨지면).
★**우리 전체** — `pytest tests/unit/travel/booking -m "booking_full or not booking_full" -q`
  (`-m` 을 주면 팀 pytest.ini 의 `not live` 가 대체된다 · 지금 live 마커가 붙은 우리 시험은 없다).
  측정에서 나온 문장 모양 회귀 · 소스별 세부(쉬는 중 · 링크 모양 · 판매처 합치기 …). 고친 뒤 · 방을 닫을 때 우리가 돌린다.
  잠그는 것은 줄이지 않았다 — 층만 나눴다. 모델 · 네트워크 · DB 없이 돈다(가짜).

마커 등록과 기본 실행에서의 제외를 **이 폴더 안에서만** 한다 — 팀 pytest.ini 는 만지지 않는다.
게이트 밖 시험은 skip 이 아니라 **deselect** 다 — 요약 줄에 「N deselected」로만 보인다.
★`-k` 만으로 전체층 시험을 고르면 deselect 돼 「no tests ran」(exit 5) — 한 건은 `-m booking_full -k <이름>` 로.
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = "booking_full"

#: 병합 게이트 — 파일 → {시험 이름: 깨지면 무엇이 죽었나}
GATE: dict[str, dict[str, str]] = {
    "test_serpapi_budget.py": {
        "test_hotels_and_flights_count_the_same_serpapi_budget": "SerpApi 무료 월 250회 — 호텔 · 항공 같은 줄, 월 250 · 하루 8, DB 로 센다",
    },
    "test_choose_recheck.py": {
        "test_same_changed_gone_and_unreadable": "고를 때 숙소 가격 재확인 — 같음 · 바뀜 · 객실 없음 · 확인 못 함",
    },
    "test_transit_module.py": {                                   # `[2026-10-14]` 예약 모듈 — 기차 · 버스(TAGO)
        "test_the_rules_read_the_transport_mode_and_station_names": "규칙이 교통수단(기차 · 버스 · 비행기) · 역 · 등급을 읽음",
        "test_the_adapter_uses_the_2026_03_addresses_and_normalises_rows": "TAGO 어댑터 — 2026-03 주소 · 응답 정리",
        "test_train_search_turns_names_into_stations_and_sorts_by_departure": "역 이름 → 역 id · 운행 출발 시각순",
        "test_bus_search_asks_every_matching_terminal_pair_express_first_and_suburbs_only_when_needed": "터미널 조합 조회 · 고속 먼저 · 시외는 없을 때만",
        "test_a_train_sentence_is_answered_from_tago_by_time_band_with_booking_links": "기차 답 — 시간대별 운행 · 요금 · 예매 링크",
        "test_change_on_a_train_item_offers_same_grade_runs_near_the_original_departure_without_touching_the_plan": "기차 항목 「바꿔 줘」 → 같은 등급 · 앞뒤 3시간 운행을 보류 제안으로",
        "test_choosing_a_train_run_moves_both_times_and_rechecks_the_fare": "운행 고르기 → 출발 · 도착 시각 교체 · 요금 재확인",
    },
    "test_arex_module.py": {                                      # `[2026-10-15]` 예약 모듈 — 공항철도 직통(이동 모듈 시간표)
        "test_a_seoul_to_airport_train_search_reads_the_timetable_without_calling_tago": "서울역 ↔ 인천공항 기차 → AREX 직통 시간표(TAGO 안 부름)",
        "test_an_arex_sentence_is_answered_from_the_timetable_with_the_first_page_link_and_no_fare": "AREX 답 — 요일 시간표 표시 · 첫 화면 링크 · 요금 지어내지 않음",
    },
    "test_lodging_team.py": {
        "test_search_calls_the_model_once_and_answers_from_tool_values": "숙소 검색 — 모델은 해석 한 번, 답은 도구 값으로",
        "test_missing_values_are_asked_back_without_any_search": "빠진 값(날짜 · 인원 · 지역)을 검색 없이 되묻기",
        "test_a_bad_or_failed_interpretation_is_not_repaired": "모델 해석이 깨지면 지어내지 않고 사람에게",
        "test_google_hotels_alone_answer_when_myrealtrip_fails": "소스 하나가 죽어도 남은 소스로 답",
        "test_my_stay_returns_coordinates_address_and_link": "「내 숙소」 — 좌표 · 주소 · 링크",
        "test_the_prompt_key_is_registrable_and_the_file_exists": "프롬프트 키 등록 · 파일 있음",
        "test_a_case_pointing_at_a_trip_item_is_itinerary_work_and_a_bare_trip_stays_assist": "일정 항목 Case → lodging.itinerary 로 감",
        "test_change_on_a_lodging_item_holds_three_new_options_like_dining_and_activity": "「바꿔 줘」 → 새 후보 3개(다른 안)",
        "test_choosing_an_option_moves_the_item_to_that_lodging_and_keeps_the_old_one_as_an_option": "후보 고르기 → 항목 교체",
        "test_a_booked_lodging_is_never_changed_and_nothing_is_searched": "예약한 숙소는 바꾸지 않음",
    },
    "test_flight_team.py": {
        "test_search_calls_the_model_once_and_both_sources_once": "항공 검색 — 규칙 해석(모델 0번) · 소스마다 한 번",
        "test_missing_values_are_asked_back_without_any_search": "빠진 값(공항 · 날짜 · 인원)을 검색 없이 되묻기",
        "test_what_the_rules_cannot_read_is_asked_back_never_invented": "규칙이 못 읽은 칸은 지어내지 않고 되묻기(2026-10-14 예약 모듈)",
        "test_one_source_is_enough_and_both_missing_is_unknown": "소스 하나로도 답 · 다 없으면 모름",
        "test_a_time_of_day_the_customer_said_keeps_only_those_flights": "말한 시간대만",
        "test_only_direct_flights_unless_the_traveller_says_connecting_is_fine": "직항 우선(경유는 말할 때만)",
        "test_parse_needs_and_registration": "해석 모양 · 빠진 값 계산 · 프롬프트 등록",
        "test_a_case_pointing_at_a_flight_item_is_itinerary_work_and_a_bare_trip_stays_assist": "일정 항목 Case → flight.itinerary 로 감",
        "test_change_on_a_flight_item_holds_new_direct_flights_like_dining_and_activity": "「바꿔 줘」 → 새 편(다른 안)",
        "test_new_flights_near_the_original_departure_come_first_then_the_rest_by_price": "새 편은 원래 출발 앞뒤 3시간 먼저",
        "test_choosing_a_flight_moves_the_item_to_its_departure_and_keeps_the_old_one_as_an_option": "편 고르기 → 항목 교체",
        "test_a_booked_flight_is_never_changed_and_nothing_is_searched": "예약한 편은 바꾸지 않음",
    },
    "test_lodging_ledger.py": {
        "test_remember_and_read_back_what_was_shown_only_in_our_table": "보여 준 후보를 우리 표에만 남김",
        "test_a_chosen_stay_with_coordinates_is_adopted_as_a_trip_place_and_linked": "고른 숙소 → 여행 전용 장소",
        "test_the_hook_is_the_old_path_until_registered_and_the_ledger_once_registered": "조립 등록 전후(끼움 자리)",
    },
    "test_interpret_dates.py": {
        "test_the_day_follows_the_quote_and_the_year_follows_today": "날짜는 문장 근거 · 연도는 오늘 기준",
        "test_nights_alone_are_not_a_check_in_and_take_the_check_out_with_them": "「N박」만으로 체크인을 만들지 않음",
        "test_a_return_before_the_departure_is_left_for_needs_to_ask": "뒤집힌 날짜는 되묻기로",
    },
    "test_booking_module.py": {
        "test_flights_are_merged_across_sources_with_statuses_and_links": "항공 소스 합치기 · 상태 · 링크",
        "test_stays_give_both_lists_and_the_booking_link": "숙소 두 목록 · 예약 링크",
        "test_resting_and_missing_sources_are_counted_not_guessed": "쉬는 · 없는 소스는 세고 지어내지 않음",
        "test_the_tools_are_absent_without_sources": "소스 없으면 도구 없음",
    },
    "test_myrealtrip_source.py": {
        "test_stay_search_reads_numbers_out_of_the_price_text_and_keeps_units": "마이리얼트립 숙소 응답 읽기",
        "test_flight_search_uses_the_search_link_not_the_reservation_link": "마이리얼트립 항공 응답 · 링크",
        "test_every_failure_is_none_and_is_counted": "실패는 None 으로 세기",
        "test_after_a_429_the_source_waits_without_calling_out": "429 뒤 쉬기(호출 한도)",
    },
    "test_ignav_source.py": {
        "test_itineraries_come_back_in_the_myrealtrip_shape_with_the_seller": "Ignav 응답 → 공용 모양",
        "test_a_bad_airport_code_or_shape_is_counted_not_guessed": "Ignav 실패는 세고 지어내지 않음",
    },
    "test_serpapi_flights_source.py": {
        "test_itineraries_come_back_in_the_shared_shape_with_the_results_page_link": "구글 항공 응답 → 공용 모양",
        "test_errors_are_counted_not_guessed": "구글 항공 실패는 세기",
    },
    "test_serpapi_hotels_source.py": {
        "test_properties_come_back_as_stays_with_the_page": "구글 호텔 응답 → 숙소 모양",
        "test_no_results_and_missing_input_are_counted": "구글 호텔 없음 · 입력 부족은 세기",
    },
    "test_booking_gate.py": {
        "test_every_gate_name_is_a_test_in_its_file": "이 목록이 실제 시험을 가리킴",
        "test_every_test_file_here_has_a_gate_entry": "새 시험 파일도 게이트를 가짐",
    },
}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{MARK}: 숙소 · 항공 전체층 — 병합 게이트 밖 시험. `-m {MARK}` 가 식에 있을 때만 돈다")


def pytest_collection_modifyitems(config, items):
    expr = config.getoption("-m") or ""          # 팀 pytest.ini 가 -m "not live" 를 늘 주므로 식은 항상 있다
    keep, drop = [], []
    for it in items:
        path = Path(str(it.fspath)).resolve()
        if path.parent != HERE:
            keep.append(it)
            continue
        if it.originalname not in GATE.get(path.name, {}):
            it.add_marker(MARK)
            if MARK not in expr:
                drop.append(it)
                continue
        keep.append(it)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
