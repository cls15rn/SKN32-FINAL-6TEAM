#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sql/mobility_schema_pg.sql 점검기 — PG 이식본이 '지키기로 한 것'을 실제로 지키는지 본다.

  python mobility_scripts/check_pg_schema.py --dsn "postgresql://.../db"

문법이 통과하는 것과 제약이 도는 것은 다른 말이다. 여기서 보는 것은 후자다.
각 묶음은 '들어가야 하는 것'과 '거부돼야 하는 것'을 짝으로 넣는다 —
거부만 보면 컬럼 이름을 잘못 써도 통과한다.
"""
import argparse, sys

SCHEMA = "mobility"
ok = fail = 0

def run(sql, params=None):
    """psql 로 한 문장 실행. (성공여부, 메시지)"""
    raise NotImplementedError

def make_runner(conn):
    def _run(sql, args=None):
        # ★ args 를 빈 튜플로 주면 psycopg2 가 SQL 안의 % 를 플레이스홀더로 읽는다.
        #   LIKE 'chk:%' 가 통째로 실패한다 — 2026-09-14 에 이 점검기가 스스로 걸렸다.
        cur = conn.cursor()
        try:
            cur.execute(sql, args if args else None); conn.commit(); return True, ""
        except Exception as e:
            conn.rollback(); return False, str(e).strip().splitlines()[0]
    return _run

def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  OK   {name}")
    else:    fail += 1; print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    a = ap.parse_args()
    try:
        import psycopg
        conn = psycopg.connect(a.dsn, autocommit=False)
        Json = __import__("psycopg.types.json", fromlist=["Jsonb"]).Jsonb
    except ImportError:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(a.dsn)
        Json = psycopg2.extras.Json
    r = make_runner(conn)
    r(f"SET search_path TO {SCHEMA}, public")

    print("[1] 구조 — 표 13 · 인덱스 19")
    cur = conn.cursor()
    cur.execute("select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace "
                "where n.nspname=%s and c.relkind='r'", (SCHEMA,))
    check("표 13개", cur.fetchone()[0] == 13)
    cur.execute("select count(*) from pg_indexes where schemaname=%s and indexname not like %s",
                (SCHEMA, '%\\_pkey'))
    check("비-PK 인덱스 19개", cur.fetchone()[0] == 19)

    print("[2] ★ source_type — 카카오 응답을 스키마가 막는가")
    r("DELETE FROM mob_verdict_evidence WHERE verdict_id LIKE 'chk:%'")
    r("DELETE FROM mob_leg_verdict WHERE verdict_id LIKE 'chk:%'")
    base = ("INSERT INTO mob_leg_verdict (verdict_id, case_id, leg_id, decided_at, verdict, evidence_grade) "
            "VALUES (%s,%s,%s,'2026-09-14T10:00:00+09:00','ok','확정')")
    check("판정 행 적재", r(base, ("chk:V1", "chk:C1", "L3"))[0])
    ev = ("INSERT INTO mob_verdict_evidence (verdict_id, evidence_id, source_type, source_id, claim, observed_at) "
          "VALUES (%s,%s,%s,'tago_subway@2026-09-09','막차 23:42','2026-09-09')")
    check("'db' 는 들어간다", r(ev, ("chk:V1", "mob:L3:timetable:1", "db"))[0])
    s, m = r(ev, ("chk:V1", "mob:L3:x:1", "tool_result"))
    check("'tool_result' 는 거부된다", not s, m)
    s, m = r(ev, ("chk:V1", "mob:L3:x:2", "external"))
    check("'external' 도 거부된다 (2026-09-11 사고)", not s, m)

    print("[3] ★ PK 복합키 — 두 번째 Case 가 터지지 않는가 (2026-09-13 결함)")
    check("두 번째 판정 행", r(base, ("chk:V2", "chk:C2", "L3"))[0])
    s, m = r(ev, ("chk:V2", "mob:L3:timetable:1", "db"))
    check("같은 evidence_id 가 다른 Case 로 공존", s, m)
    s, m = r(ev, ("chk:V1", "mob:L3:timetable:1", "db"))
    check("같은 Case 안 중복은 거부", not s, m)

    print("[4] jsonb — 문자열이 아니라 구조로 들어가는가")
    s, m = r("UPDATE mob_verdict_evidence SET value_json=%s WHERE verdict_id='chk:V1'",
             (Json({"v": 1, "kind": "timetable", "grade": "확정"}),))
    check("dict 적재", s, m)
    cur.execute("select value_json->>'kind' from mob_verdict_evidence where verdict_id='chk:V1' limit 1")
    check("질의로 안을 본다 (kind='timetable')", cur.fetchone()[0] == "timetable")
    s, m = r("UPDATE mob_verdict_evidence SET value_json=%s::jsonb WHERE verdict_id='chk:V1'", ("{깨진",))
    check("깨진 JSON 은 거부된다", not s, m)

    print("[5] FK — 판정을 지우면 근거도 지워진다 / 참조 표에는 FK 가 없다")
    r("DELETE FROM mob_leg_verdict WHERE verdict_id='chk:V2'")
    cur.execute("select count(*) from mob_verdict_evidence where verdict_id='chk:V2'")
    check("ON DELETE CASCADE", cur.fetchone()[0] == 0)
    cur.execute("""select count(*) from pg_constraint co
                   join pg_class c on c.oid=co.conrelid
                   join pg_namespace n on n.oid=c.relnamespace
                   where n.nspname=%s and co.contype='f'
                     and c.relname in ('mob_leg_verdict','mob_verdict_evidence')
                     and co.confrelid <> (select oid from pg_class where relname='mob_leg_verdict'
                                          and relnamespace=n.oid)""", (SCHEMA,))
    check("산출 표 → 참조 표 FK 가 없다 (재적재 TRUNCATE 가 막히면 안 된다)", cur.fetchone()[0] == 0)

    print("[6] NULL 의 뜻 — 0 으로 채우지 않는다")
    tt = ("INSERT INTO mob_timetable (line, station_key, station_nm, dir, day_type, dep_min, "
          "source, source_station_id, fetched_at, fetched_at_precision) "
          "VALUES ('99호선','99호선|점검','점검','U','weekday',%s,'chk','chk','2026-09-14',%s)")
    check("dep_min NULL 허용 (시·종착역)", r(tt, (None, "day"))[0])
    check("dep_min 1490 (=24:50) 허용", r(tt, (1490, "day"))[0])
    s, m = r(tt, (0, "minute"))
    check("fetched_at_precision 'minute' 은 거부", not s, m)
    cg = ("INSERT INTO mob_congestion (station_key, station_nm, line, src_station_cd, dir, day_type, "
          "slot_min, congestion, reason, grade, source, source_id, fetched_at) "
          "VALUES ('99호선|점검','점검','99호선','chk','U',%s,600,%s,%s,'확정','chk','chk','2026-09-14')")
    check("congestion NULL + reason", r(cg, ("weekday", None, "after_last_train"))[0])
    s, m = r(cg, ("weekday", None, "모름"))
    check("reason 자유값 거부", not s, m)

    print("[7] ★ 요일축은 표마다 값 집합이 다르다")
    s, m = r(tt.replace("'weekday'", "'sunday'"), (None, "day"))
    check("시간표에 'sunday' 거부", not s, m)
    s, m = r(cg, ("holiday", None, "after_last_train"))
    check("혼잡도에 'holiday' 거부", not s, m)
    check("혼잡도에 'sunday' 허용", r(cg.replace("slot_min, congestion", "slot_min, congestion")
                                    .replace("600", "630"), ("sunday", 88.1, None))[0])

    print("[8] identity — 적재기가 id 를 직접 넣어도 막히지 않는가")
    cur.execute("select count(*) from mob_timetable where line='99호선'")
    check("자동 채번으로 들어간 행이 있다", cur.fetchone()[0] >= 2)
    s, m = r("INSERT INTO mob_timetable (id, line, station_key, station_nm, dir, day_type, "
             "source, source_station_id, fetched_at, fetched_at_precision) "
             "VALUES (999000001,'99호선','99호선|점검2','점검2','D','holiday','chk','chk2','2026-09-14','second')")
    check("id 를 직접 지정해도 들어간다 (BY DEFAULT)", s, m)

    print("[9] 뒷정리")
    for sql in ("DELETE FROM mob_timetable WHERE line='99호선'",
                "DELETE FROM mob_congestion WHERE line='99호선'",
                "DELETE FROM mob_leg_verdict WHERE verdict_id LIKE 'chk:%'"):
        r(sql)
    cur.execute("select (select count(*) from mob_timetable where line='99호선') "
                "+ (select count(*) from mob_congestion where line='99호선') "
                "+ (select count(*) from mob_leg_verdict where verdict_id like 'chk:%')")
    check("점검 데이터가 남지 않았다", cur.fetchone()[0] == 0)

    print(f"\n합계 {ok+fail}묶음 · 통과 {ok} · 실패 {fail}")
    sys.exit(1 if fail else 0)

if __name__ == "__main__":
    main()
