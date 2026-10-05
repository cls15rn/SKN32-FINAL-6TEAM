"""생성 SQL을 실행해 미쉐린 검수 행이 실제 연결된 장소에 붙는지 확인한다."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from tests.integration.dining.test_dining_judgment import add_closure, add_hours


@pytest.mark.parametrize("near_count, far_count, tourapi, operator_hours, partial_review", [
    (1, 1, True, False, False), (1, 0, False, False, False),
    (0, 1, True, False, False), (2, 0, True, False, False),
    (1, 0, False, True, False), (1, 0, False, True, True)])
def test_hours_use_the_sql_resolved_place_and_reimport_cleanly(
        conn, place, tmp_path, monkeypatch, near_count, far_count, tourapi, operator_hours, partial_review):
    spec = importlib.util.spec_from_file_location(
        "michelin_import_test", Path(__file__).resolve().parents[3] / "scripts/dining/make_michelin_sql.py")
    mm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mm)
    label = f"검수시험-{uuid4().hex}"
    existing = [place(lat=37.5 if i < near_count else 37.54)
                for i in range(near_count + far_count)]
    old_rules = []
    for uid in existing:
        conn.execute("UPDATE dining.dn_place SET name_ko=%s WHERE place_uid=%s", (label, uid))
        old_rules.append(add_hours(conn, uid, 1, [(600, 1320, None)]))
        if operator_hours:
            conn.execute("UPDATE dining.dn_hours_rule SET source_code='operator_check', extract_method='manual' "
                         "WHERE rule_id=%s", (old_rules[-1],))
        if operator_hours and not partial_review:
            add_closure(conn, uid, pattern_kind="weekly", weekday=1)
            conn.execute("INSERT INTO dining.dn_closure_rule (place_uid, source_code, entered_by, verified_at, "
                         "pattern_kind, weekday, nth, extract_method, valid_from) "
                         "VALUES (%s, 'operator_check', 'test', now(), 'monthly_nth', 1, ARRAY[1]::smallint[], "
                         "'manual', '2026-09-01')", (uid,))
        if partial_review:
            conn.execute("INSERT INTO dining.dn_closure_coverage (place_uid, source_code, state, note, valid_from) "
                         "VALUES (%s, 'operator_check', 'none', '이전 검수에서 휴무 없음 확인', '2026-09-01')", (uid,))
    listed = [{"contentid": str(i), "title": label, "mapy": "37.5" if i < near_count else "37.54",
               "mapx": "127.0"} for i in range(len(existing))] if tourapi else []
    for filename, rows in [("tourapi_서울_음식점_목록.json", listed),
                           ("tourapi_음식점_소개정보.json", listed)]:
        (tmp_path / filename).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "list.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["상호", "등급"])
        writer.writeheader()
        writer.writerow({"상호": label, "등급": "셀렉티드"})
    facts = tmp_path / "facts.jsonl"
    facts.write_text(json.dumps({"상호": label, "위도": 37.5, "경도": 127.0, "주소": "시험구 시험로 1",
                                 "url": f"https://example.test/{uuid4().hex}", "요리": "한식",
                                 "우편번호": "00000", "전화": None, "가격대": None,
                                 "편의시설": [], "가족": None},
                                ensure_ascii=False) + "\n", encoding="utf-8")
    sheet = tmp_path / "hours.csv"
    row = {"번호": "1", "상호(목록)": label, **{d: "11:00-20:00" for d in "월화수목금토일"},
           "라스트오더": "없음", "정기휴무 외": "없음", "확인일": "2026-09-28", "메모": "시험"}
    row["화"] = "휴무"
    if partial_review:
        row.update({"월": "", "화": "11:00-20:00", "정기휴무 외": ""})
    with sheet.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    for name, value in {"DATA": str(tmp_path), "LIST": str(source), "FACTS": str(facts),
                        "HOURS_SHEET": str(sheet), "OUT": str(tmp_path)}.items():
        monkeypatch.setattr(mm, name, value)
    monkeypatch.setattr("sys.argv", ["make_michelin_sql.py"])
    mm.main()
    sql = (tmp_path / "michelin.sql").read_text(encoding="utf-8")
    for _ in range(2):
        try:
            conn.execute(sql)
        finally:
            conn.rollback()  # 실패한 생성 SQL이 다음 시험의 연결을 오염시키지 않게 한다.
        uid = str(conn.execute("SELECT place_uid FROM dining.dn_source_record "
                               "WHERE source_code='michelin_guide' AND external_id=%s", (label,)).fetchone()[0])
        if near_count == 1:
            assert uid == existing[0]
            assert conn.execute("SELECT retired_at IS NOT NULL FROM dining.dn_hours_rule WHERE rule_id=%s",
                                (old_rules[0],)).fetchone()[0] is (not partial_review)
        else:
            assert uid not in existing
        checked_uid = str(conn.execute("SELECT place_uid FROM dining.dn_source_record "
                                       "WHERE source_code='operator_check' AND external_id=%s",
                                       ("hours:" + label,)).fetchone()[0])
        assert checked_uid == uid
        assert conn.execute("SELECT count(*) FROM dining.dn_hours_rule WHERE place_uid=%s "
                            "AND source_code='operator_check' AND retired_at IS NULL", (uid,)).fetchone()[0] == 7
        assert conn.execute("SELECT count(*) FROM dining.dn_closure_rule WHERE place_uid=%s "
                            "AND source_code='operator_check' AND retired_at IS NULL", (uid,)).fetchone()[0] == (0 if partial_review else 1)
        assert conn.execute("SELECT count(*) FROM dining.dn_closure_coverage WHERE place_uid=%s "
                            "AND source_code='operator_check'", (uid,)).fetchone()[0] == 1
        if partial_review:
            assert conn.execute("SELECT state FROM dining.dn_closure_coverage WHERE place_uid=%s "
                                "AND source_code='operator_check'", (uid,)).fetchone()[0] == "none"
            assert conn.execute("SELECT dining.needs_closure_check(%s)", (uid,)).fetchone()[0] is False
        assert conn.execute("SELECT open_min, close_min FROM dining.day_intervals(%s, '2026-10-05')",
                            (uid,)).fetchall() == ([(600, 1320)] if partial_review else [(660, 1200)])
        assert conn.execute("SELECT dining.open_at_slot(%s, '2026-10-05 12:00+09', '2026-10-05 12:50+09')",
                            (uid,)).fetchone()[0] is True
        assert conn.execute("SELECT dining.open_at_slot(%s, '2026-10-05 21:00+09', '2026-10-05 21:50+09')",
                            (uid,)).fetchone()[0] is partial_review
