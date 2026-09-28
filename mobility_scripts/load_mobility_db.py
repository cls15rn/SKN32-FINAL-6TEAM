# mobility_scripts/load_mobility_db.py — processed/mobility/*.jsonl → SQLite
# 실행: 저장소 루트에서  python mobility_scripts/load_mobility_db.py [--db data/travel/mobility.sqlite]
#       python mobility_scripts/load_mobility_db.py --verify-only     (적재 없이 점검만)
#
# 재적재는 **참조 표만 비우고 다시 넣는다.** 판정 이력(mob_leg_verdict ·
# mob_verdict_evidence)은 건드리지 않는다 — 되돌릴 수 없는 모듈 산출이고,
# 시간표가 개정돼도 그 시점에 낸 판정은 그대로 남아야 한다.
#
# 시각은 전부 modules/mobility/timeutil.to_min 으로 분 단위 정수로 바꾼다.
# 이 파일에서 시각 파싱을 따로 하지 않는다 — 자가 둘이 되면 그때부터 어긋난다.
import argparse, json, re, sqlite3, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                 # 저장소 루트
sys.path.insert(0, str(HERE.parent / "final_project_cs"))   # 판정 패키지
sys.path.insert(0, str(HERE / "collect"))            # _paths
from _paths import PROCESSED           # noqa: E402
from app.modules.travel_ops.mobility.engine.timeutil import to_min   # noqa: E402

KST = timezone(timedelta(hours=9))
MOB = PROCESSED / "mobility"
SCHEMA = HERE.parent / "sql" / "mobility_schema.sql"
CONFIG = (HERE.parent / "final_project_cs" / "app" / "modules"
          / "travel_ops" / "mobility" / "engine" / "rules")
REF_TABLES = ["mob_timetable", "mob_line_edge", "mob_station_coord", "mob_transfer_walk",
              "mob_congestion", "mob_bus_route", "mob_bus_stop", "mob_bus_speed",
              "mob_airport_bus_departure", "mob_holiday"]


def jsonl(p):
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_json(p):
    return json.loads(p.read_text(encoding="utf-8"))


def note(con, table, n, src, built_at):
    con.execute("INSERT OR REPLACE INTO mob_load_meta VALUES (?,?,?,?,?)",
                (table, n, src, built_at, datetime.now(KST).isoformat(timespec="seconds")))
    print(f"  {table:28s} {n:>8,}  ← {src}")


# ── 표별 적재 ───────────────────────────────────────────────────────────
def dest_normalizer():
    """소스 행선지 표기 → 역명. line_station_order_v1.json 의 dest_alias 를 정본으로 쓰고,
    거기 없는 표기는 같은 규칙(괄호 제거 → 끝의 '역' 제거)을 적용한다."""
    d = load_json(MOB / "line_station_order_v1.json")
    alias = {ln: dict(v) for ln, v in d.get("dest_alias", {}).items()}
    names = {ln: {s["station_nm"] for s in v["stations"]} for ln, v in d["lines"].items()}
    cache = {}

    def norm(line, raw):
        if not raw:
            return None
        k = (line, raw)
        if k in cache:
            return cache[k]
        ns = names.get(line, ())
        v = raw if raw in ns else alias.get(line, {}).get(raw)
        if v is None:
            c = re.sub(r"\s*\(.*?\)\s*", "", raw).strip()
            if c not in ns and c.endswith("역") and c[:-1] in ns:
                c = c[:-1]
            v = c if c in ns else raw          # 끝내 못 맞추면 원표기 그대로 (점검에서 잡힌다)
        cache[k] = v
        return v
    return norm


def load_timetable(con):
    meta = load_json(MOB / "timetable_v1_meta.json") if (MOB / "timetable_v1_meta.json").exists() else {}
    norm = dest_normalizer()
    seen, rows, dropped = set(), [], 0
    for r in jsonl(MOB / "timetable_v1.jsonl"):
        dep, arr = to_min(r.get("dep_time")), to_min(r.get("arr_time"))
        # UNIQUE 인덱스는 NULL 을 서로 다른 값으로 본다. dep 가 NULL 인 행(시·종착역)은
        # 인덱스로 안 걸러지므로 여기서 한 번 더 거른다.
        dest = norm(r["line"], r.get("dest_nm"))
        orig = norm(r["line"], r.get("orig_nm"))
        key = (r["source"], r["source_station_id"], r["day_type"], r["dir"],
               dep, arr, dest, r.get("train_no"))
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        rows.append((r["line"], r["station_key"], r.get("station_cd"), r["station_nm"],
                     r.get("station_nm_en"), r["dir"], r["day_type"], dep, arr,
                     dest, r.get("dest_nm"), orig, r.get("orig_nm"), r.get("train_no"), r.get("express"),
                     r["source"], r["source_station_id"], r["fetched_at"], r["fetched_at_precision"]))
    con.executemany("""INSERT INTO mob_timetable
        (line,station_key,station_cd,station_nm,station_nm_en,dir,day_type,dep_min,arr_min,
         dest_station_nm,dest_station_nm_src,orig_station_nm,orig_station_nm_src,train_no,express,
         source,source_station_id,fetched_at,fetched_at_precision)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    if dropped:
        print(f"    ※ 적재 단계 중복 제거 {dropped:,}행 (dep_min NULL 이라 인덱스로 안 걸리는 것)")
    note(con, "mob_timetable", len(rows), "timetable_v1.jsonl", meta.get("built_at"))
    return meta


def load_line_order(con):
    d = load_json(MOB / "line_station_order_v1.json")
    st, ed = [], []
    for ln, v in d["lines"].items():
        for s in v["stations"]:
            st.append((s["station_key"], ln, s["station_nm"], s.get("station_nm_en"),
                       s.get("station_cd"), s.get("fr_code"), s.get("fr_order"),
                       int(bool(s.get("is_spur"))), int(bool(s.get("has_timetable")))))
        for e in v["edges"]:
            a, b = sorted((e["a"], e["b"]))
            flip = (a != e["a"])                     # a,b 를 정렬해 넣으므로 방향 라벨도 뒤집는다
            ed.append((ln, a, b, e["source"], e["grade"], e.get("travel_min"),
                       e.get("travel_min_source"), e.get("travel_min_grade"), e.get("distance_m"),
                       e.get("dir_b_to_a") if flip else e.get("dir_a_to_b"),
                       e.get("dir_a_to_b") if flip else e.get("dir_b_to_a"),
                       e.get("note")))
    con.executemany("""INSERT INTO mob_line_edge
        (line,station_a,station_b,edge_source,grade,travel_min,travel_min_source,
         travel_min_grade,distance_m,dir_a_to_b,dir_b_to_a,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", ed)
    note(con, "mob_line_edge", len(ed), "line_station_order_v1.json", d.get("built_at"))
    return st, d


def load_station_coord(con, order_rows, order_doc):
    coords = load_json(MOB / "station_coords.json")
    by_key = coords.get("stations", {})
    rows = []
    for (key, ln, nm, nm_en, cd, fr, fo, spur, has_tt) in order_rows:
        c = by_key.get(key, {})
        rows.append((key, ln, nm, nm_en or c.get("station_nm_en"), cd or c.get("station_cd"),
                     fr, fo, spur, has_tt, c.get("lat"), c.get("lng"), c.get("operator"),
                     c.get("source"), c.get("fetched_at")))
    con.executemany("""INSERT OR REPLACE INTO mob_station_coord
        (station_key,line,station_nm,station_nm_en,station_cd,fr_code,fr_order,is_spur,
         has_timetable,lat,lng,operator,source,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    miss = sum(1 for r in rows if r[9] is None)
    if miss:
        print(f"    ※ 좌표 미확보 {miss}역 — NULL 로 둔다(FK 를 안 거는 이유)")
    note(con, "mob_station_coord", len(rows), "line_station_order_v1.json + station_coords.json",
         order_doc.get("built_at"))


def load_transfer_walk(con):
    d = load_json(MOB / "transfer_walk_v1.json")
    g = d.get("grade", {})
    grade = f"distance_m {g.get('distance_m','?').split('—')[0].strip()} · walk_min {g.get('walk_min','?').split('—')[0].strip()}"
    rows = [(v["station_nm"], v["from_line"], v["to_line"], v["distance_m"], v["walk_min"],
             grade, d.get("data_basis_date")) for v in d["pairs"].values()]
    con.executemany("""INSERT INTO mob_transfer_walk
        (station_nm,from_line,to_line,distance_m,walk_min,grade,checked_at)
        VALUES (?,?,?,?,?,?,?)""", rows)
    note(con, "mob_transfer_walk", len(rows), "transfer_walk_v1.json", d.get("built_at"))


def load_congestion(con):
    rows = []
    for r in jsonl(MOB / "congestion_v1.jsonl"):
        rows.append((r["station_key"], r.get("station_cd"), r["station_nm"], r["line"], r.get("branch"),
                     r["src_station_cd"], r["dir"], r.get("dir_raw"), r["day_type"],
                     to_min(r["slot"]), r.get("congestion"), r.get("reason"), r["grade"],
                     r["source"], r["source_id"], r.get("data_basis_date"), r["fetched_at"]))
    con.executemany("""INSERT INTO mob_congestion
        (station_key,station_cd,station_nm,line,branch,src_station_cd,dir,dir_raw,day_type,
         slot_min,congestion,reason,grade,source,source_id,data_basis_date,fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    note(con, "mob_congestion", len(rows), "congestion_v1.jsonl", None)


def load_bus(con):
    rows = []
    for r in jsonl(MOB / "bus_route_v1.jsonl"):
        term = r.get("term_min")
        rows.append((r["route_id"], r["route_nm"], r.get("route_type"), r.get("route_type_nm"),
                     r.get("route_type_grade"), r.get("corp_nm"), r.get("st_station_nm"),
                     r.get("ed_station_nm"), r.get("length_km"),
                     None if not term else term,          # ★ 0 = 배차 개념 없음 → NULL
                     to_min(r.get("first_time")), to_min(r.get("last_time")),
                     int(bool(r.get("crosses_midnight"))), r.get("grade_service_window"),
                     r.get("grade_wait"), r.get("source"), r.get("source_id"), r.get("fetched_at")))
    con.executemany("""INSERT INTO mob_bus_route
        (route_id,route_nm,route_type,route_type_nm,route_type_grade,corp_nm,st_station_nm,
         ed_station_nm,length_km,term_min,first_min,last_min,crosses_midnight,
         grade_service_window,grade_wait,source,source_id,fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    nullterm = sum(1 for r in rows if r[9] is None)
    if nullterm:
        print(f"    ※ term_min NULL {nullterm}노선 — 배차 0분이 아니라 배차 개념 없음")
    note(con, "mob_bus_route", len(rows), "bus_route_v1.jsonl", None)

    srows = []
    for r in jsonl(MOB / "bus_stops_v1.jsonl"):
        srows.append((r["route_id"], r["seq"], r["station_id"], r.get("ars_id"), r["station_nm"],
                      r.get("lat"), r.get("lng"), r.get("direction"), r.get("sect_dist_m"),
                      r.get("transfer_yn"), r.get("source"), r.get("source_id"), r.get("fetched_at")))
    con.executemany("""INSERT INTO mob_bus_stop
        (route_id,seq,station_id,ars_id,station_nm,lat,lng,direction,sect_dist_m,
         transfer_yn,source,source_id,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", srows)
    note(con, "mob_bus_stop", len(srows), "bus_stops_v1.jsonl", None)

    p = MOB / "bus_speed_v1.json"
    if p.exists():
        d = load_json(p)
        brows = [(k, v.get("route_type_nm"), v.get("speed_kmh"), v.get("stat_kmh"),
                  json.dumps(v.get("by_hour_kmh"), ensure_ascii=False), v.get("observed_km"),
                  v.get("observed_min"), d.get("grade", "추정"), d.get("source_id"), d.get("built_at"))
                 for k, v in d.get("routes", {}).items()]
        con.executemany("""INSERT OR REPLACE INTO mob_bus_speed
            (route_nm,route_type_nm,speed_kmh,stat_kmh,by_hour_kmh,observed_km,observed_min,
             grade,source_id,built_at) VALUES (?,?,?,?,?,?,?,?,?,?)""", brows)
        note(con, "mob_bus_speed", len(brows), "bus_speed_v1.json", d.get("built_at"))


def load_airport_bus(con):
    rows = []
    for r in jsonl(MOB / "airport_bus_v1.jsonl"):
        rows.append((r["route_no"], r["route_key"], r["terminal"], r["dir"], r["day_type"],
                     to_min(r.get("dep_time")), to_min(r.get("first")), to_min(r.get("last")),
                     r.get("area"), r.get("bus_class"), r.get("fare_adult"), r.get("operator"),
                     r.get("ride_location"), int(bool(r.get("timetable_available"))),
                     r["grade"], r["source"], r["fetched_at"]))
    con.executemany("""INSERT INTO mob_airport_bus_departure
        (route_no,route_key,terminal,dir,day_type,dep_min,first_min,last_min,area,bus_class,
         fare_adult,operator,ride_location,timetable_available,grade,source,fetched_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    note(con, "mob_airport_bus_departure", len(rows), "airport_bus_v1.jsonl", None)


def load_holiday(con):
    p = CONFIG / "holidays_2026_2027.json"
    d = load_json(p)
    days = d.get("holidays") or d.get("dates") or d.get("days") or []
    rows = []
    for h in days:
        if isinstance(h, str):
            rows.append((h, None, d.get("source"), d.get("fetched_at")))
        else:
            rows.append((h.get("date") or h.get("locdate"), h.get("name") or h.get("dateName"),
                         d.get("source"), d.get("fetched_at")))
    rows = [(str(a), b, c, e) for a, b, c, e in rows if a]
    con.executemany("INSERT OR REPLACE INTO mob_holiday VALUES (?,?,?,?)", rows)
    note(con, "mob_holiday", len(rows), p.name, d.get("fetched_at"))


# ── 적재 후 점검 ────────────────────────────────────────────────────────
def verify(con):
    """스키마가 지키기로 한 것이 실제로 지켜졌는지 데이터로 확인한다.
    통계만 찍지 않고 어긋난 표본을 같이 낸다(C4 에서 배운 것)."""
    print("\n── 적재 점검 ──")
    fail = []

    def chk(label, sql, want=0, sample=None):
        n = con.execute(sql).fetchone()[0]
        ok = (n == want) if isinstance(want, int) else want(n)
        print(f"  {'OK' if ok else 'X '} {label}: {n:,}")
        if not ok:
            fail.append(label)
            if sample:
                for row in con.execute(sample).fetchall()[:5]:
                    print("       ", row)
        return n

    chk("시각 컬럼에 24시 이상 값이 살아 있다(있어야 정상)",
        "SELECT COUNT(*) FROM mob_timetable WHERE dep_min >= 1440", want=lambda n: n > 0)
    chk("dep_min 이 NULL 인 행(시·종착역, 있어야 정상)",
        "SELECT COUNT(*) FROM mob_timetable WHERE dep_min IS NULL", want=lambda n: n > 0)
    chk("dep_min 이 0 인 행(있으면 '출발 없음'을 0시로 저장한 것)",
        "SELECT COUNT(*) FROM mob_timetable WHERE dep_min = 0")
    chk("행선지가 그 역 자신인 막차 후보(판정에서 걸러야 하는 행)",
        "SELECT COUNT(*) FROM mob_timetable WHERE dest_station_nm = station_nm AND dep_min IS NOT NULL",
        want=lambda n: n >= 0)
    chk("혼잡도 congestion 이 0.0 으로 채워진 행(NULL 이어야 한다)",
        "SELECT COUNT(*) FROM mob_congestion WHERE congestion = 0 AND reason IS NOT NULL")
    chk("버스 term_min 이 0 인 행(NULL 이어야 한다)",
        "SELECT COUNT(*) FROM mob_bus_route WHERE term_min = 0")
    chk("간선의 travel_min 이 있는데 출처가 없는 행",
        "SELECT COUNT(*) FROM mob_line_edge WHERE travel_min IS NOT NULL AND travel_min_source IS NULL")
    chk("간선 양끝이 역 목록에 없는 행",
        """SELECT COUNT(*) FROM mob_line_edge e WHERE NOT EXISTS
           (SELECT 1 FROM mob_station_coord s WHERE s.line=e.line AND s.station_nm=e.station_a)""")
    # DISTINCT 로 먼저 줄이고 LEFT JOIN 한다. 상관 서브쿼리로 두면 46만 행 × 277 키가 되어
    # 점검 하나가 몇 분씩 걸린다(실제로 그랬다).
    chk("시간표 station_key 가 역 목록에 없는 것",
        """SELECT COUNT(*) FROM (SELECT DISTINCT station_key k FROM mob_timetable) t
           LEFT JOIN mob_station_coord s ON s.station_key = t.k WHERE s.station_key IS NULL""")
    chk("행선지가 역 목록에 안 붙는 행(정규화가 놓친 표기)",
        """SELECT COUNT(*) FROM mob_timetable t WHERE t.dest_station_nm IS NOT NULL AND NOT EXISTS
           (SELECT 1 FROM mob_station_coord s WHERE s.line=t.line AND s.station_nm=t.dest_station_nm)""",
        sample="""SELECT DISTINCT t.line, t.dest_station_nm_src, t.dest_station_nm FROM mob_timetable t
           WHERE t.dest_station_nm IS NOT NULL AND NOT EXISTS
           (SELECT 1 FROM mob_station_coord s WHERE s.line=t.line AND s.station_nm=t.dest_station_nm)""")
    chk("혼잡도 station_key 가 시간표에 없는 것",
        """SELECT COUNT(*) FROM (SELECT DISTINCT station_key k FROM mob_congestion) c
           LEFT JOIN (SELECT DISTINCT station_key k FROM mob_timetable) t ON t.k = c.k
           WHERE t.k IS NULL""")

    # 판정기가 실제로 던질 질의를 한 번 돌려 본다 — 인덱스와 컬럼이 쓸모 있는지.
    print("\n── 판정 질의 시연 ──")
    row = con.execute("""
        SELECT dep_min, dest_station_nm FROM mob_timetable
        WHERE line='02호선' AND station_nm='강남' AND dir='D' AND day_type='weekday'
          AND dep_min IS NOT NULL AND dest_station_nm IS NOT NULL
          AND dest_station_nm <> station_nm
        ORDER BY dep_min DESC LIMIT 1""").fetchone()
    print(f"  2호선 강남 하행 평일 막차(종착열차 제외): {row[0]} 분 = "
          f"{row[0]//60:02d}:{row[0]%60:02d} · {row[1]}행")
    n = con.execute("""SELECT COUNT(*) FROM mob_line_edge
                       WHERE line='02호선' AND grade='확정'""").fetchone()[0]
    print(f"  2호선 확정 간선 {n}개 · travel_min 있는 간선 "
          f"{con.execute('SELECT COUNT(*) FROM mob_line_edge WHERE travel_min IS NOT NULL').fetchone()[0]}/"
          f"{con.execute('SELECT COUNT(*) FROM mob_line_edge').fetchone()[0]}")

    # CHECK 가 정말 막는지 — 카카오 근거를 넣어 본다.
    con.execute("""INSERT INTO mob_leg_verdict
        (verdict_id,case_id,leg_id,decided_at,verdict,evidence_grade)
        VALUES ('__probe__','c','L1','2026-01-01T00:00:00','ok','확정')""")
    try:
        con.execute("""INSERT INTO mob_verdict_evidence
            (evidence_id,verdict_id,source_type,source_id,claim,observed_at)
            VALUES ('e1','__probe__','tool_result','kakao_transit_route','소요 15분','2026-01-01')""")
        print("  X  source_type='tool_result'(카카오)가 들어갔다 — CHECK 가 안 걸린다")
        fail.append("source_type CHECK")
    except sqlite3.IntegrityError:
        print("  OK source_type='tool_result'(카카오) 저장 거부됨 — 약관 방침이 스키마로 강제된다")
    con.execute("DELETE FROM mob_leg_verdict WHERE verdict_id='__probe__'")
    return fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()
    db = Path(args.db) if args.db else (PROCESSED.parent / "mobility.sqlite")
    db.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))

    if not args.verify_only:
        print(f"참조 표 비우고 다시 넣는다 → {db}")
        con.execute("PRAGMA foreign_keys = OFF")
        for t in REF_TABLES:
            con.execute(f"DELETE FROM {t}")
        con.execute("PRAGMA foreign_keys = ON")
        t0 = datetime.now()
        load_timetable(con)
        order_rows, order_doc = load_line_order(con)
        load_station_coord(con, order_rows, order_doc)
        load_transfer_walk(con)
        load_congestion(con)
        load_bus(con)
        load_airport_bus(con)
        load_holiday(con)
        con.commit()
        print(f"  적재 {(datetime.now()-t0).total_seconds():.1f}초")

    fail = verify(con)
    con.commit()
    kept = con.execute("SELECT COUNT(*) FROM mob_leg_verdict").fetchone()[0]
    print(f"\n판정 이력 {kept:,}건은 그대로 둔다(재적재 대상 아님)")
    size = db.stat().st_size / 1024 / 1024
    print(f"DB {db} · {size:.1f} MB")
    if fail:
        print("\n점검 실패:", ", ".join(fail))
        sys.exit(1)
    print("\n점검 전부 통과")


if __name__ == "__main__":
    main()
