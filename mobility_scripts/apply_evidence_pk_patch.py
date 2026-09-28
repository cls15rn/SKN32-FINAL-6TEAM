# -*- coding: utf-8 -*-
"""mob_verdict_evidence PK 를 (verdict_id, evidence_id) 복합키로 바꾼다. 2026-09-13.

왜: evidence_id 'mob:L3:timetable:1' 은 Case 가 달라도 같은 문자열이다.
    단독 PK 로 두면 **두 번째 Case 의 적재가 PK 충돌로 터진다.**
안전장치: 표에 행이 있으면 멈춘다. 바꾼 뒤 실제로 두 Case 를 넣어 보고 되돌린다.

    python mobility_scripts/apply_evidence_pk_patch.py            # 적용
    python mobility_scripts/apply_evidence_pk_patch.py --dry-run  # 현재 모양만 본다
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mobility_scripts.collect._paths import TRAVEL  # noqa: E402  — .env 의 DATA_DIR 을 따른다

REPO = Path(__file__).resolve().parents[1]
DB = TRAVEL / "mobility.sqlite"
PATCH = REPO / "sql" / "mobility_evidence_pk_patch_20260913.sql"
DRY = "--dry-run" in sys.argv


def ddl(con):
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='mob_verdict_evidence'"
    ).fetchone()
    return row[0] if row else None


def main():
    print("DB    :", DB)
    print("패치  :", PATCH)
    if not DB.exists():
        sys.exit("DB 가 없다. .env 의 DATA_DIR 을 확인할 것.")

    con = sqlite3.connect(DB)
    con.execute("PRAGMA foreign_keys = ON")

    before = ddl(con)
    print("\n── 지금 모양 ──")
    print(before)

    n = con.execute("SELECT count(*) FROM mob_verdict_evidence").fetchone()[0]
    print(f"\n행 수: {n}")
    if n:
        sys.exit("행이 있다. DROP 하면 판정 근거를 잃는다 — 여기서 멈춘다.")

    if DRY:
        print("\n--dry-run: 바꾸지 않았다.")
        return

    con.executescript(PATCH.read_text(encoding="utf-8"))
    con.commit()

    after = ddl(con)
    print("\n── 바뀐 모양 ──")
    print(after)
    ok_pk = "PRIMARY KEY (verdict_id, evidence_id)" in (after or "")
    print("\nPK 복합키:", "OK" if ok_pk else "FAIL")

    # ── 실제로 안 터지는지 넣어 본다. 전부 되돌린다 ──
    print("\n── 두 Case 가 같은 evidence_id 를 써도 되는가 (넣고 되돌린다) ──")
    try:
        con.execute("BEGIN")
        for cid in ("ZZ-TEST-1", "ZZ-TEST-2"):
            vid = f"mob:{cid}:L3:2026-01-01T00:00:00"
            con.execute(
                "INSERT INTO mob_leg_verdict (verdict_id, case_id, leg_id, decided_at,"
                " verdict, evidence_grade) VALUES (?,?,?,?,?,?)",
                (vid, cid, "L3", "2026-01-01T00:00:00", "ok", "확정"),
            )
            con.execute(
                "INSERT INTO mob_verdict_evidence (verdict_id, evidence_id, source_type,"
                " source_id, claim, value_json, confidence, observed_at) VALUES (?,?,?,?,?,?,?,?)",
                (vid, "mob:L3:timetable:1", "db", "tago_subway@2026-09-09",
                 "테스트", '{"v":1,"kind":"timetable"}', 0.95, "2026-09-09"),
            )
        print("  OK  두 Case 가 같은 evidence_id 를 나눠 가진다")
    except sqlite3.IntegrityError as e:
        print("  FAIL", e)
    finally:
        con.rollback()

    left = con.execute("SELECT count(*) FROM mob_verdict_evidence").fetchone()[0]
    left_v = con.execute("SELECT count(*) FROM mob_leg_verdict").fetchone()[0]
    print(f"되돌린 뒤 남은 행: 근거 {left} · 판정 {left_v}  (둘 다 0 이어야 한다)")
    con.close()


if __name__ == "__main__":
    main()
