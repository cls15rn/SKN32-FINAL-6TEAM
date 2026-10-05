"""검수한 대조표를 정답셋 적재 SQL 로 바꾼다.

무엇을 재는가.
    metric = 'reality'. 관광공사 원문이 실제 가게와 맞는가를 잰다.
    파서가 원문을 제대로 읽었는가(metric='parse')는 다른 지표이며
    회귀 시험 34개가 맡는다. 섞어 세지 않는다.

정답의 출처.
    사람이 2026-09-21 에 100곳을 네이버 지도로 대조했다.
    표시가 없는 행은 원문과 실제가 같았다는 뜻이고, 표시가 있는 행은
    달랐다는 뜻이며 사람이 본 값이 적혀 있다.

    표시 없음을 「안 봤음」이 아니라 「보았고 같았음」으로 읽는 근거는
    검수자가 100행을 모두 확인했다고 밝혔기 때문이다. 그 사실을
    evidence 칸에 남긴다. 다음 사람이 이 전제를 확인할 수 있어야 한다.

사용법:  python scripts/dining/make_truth_sql.py
출력:    datasets/dining/processed/_build/truth.sql
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import uuid

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DINING_DATA = os.environ.get("DINING_DATA") or os.path.join(  # 데이터는 git 밖(datasets/dining/processed)
    os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "datasets", "dining", "processed")
ROOT = os.path.dirname(os.path.dirname(HERE))          # final_project_cs
DATA = DINING_DATA
OUT = os.path.join(DATA, "_build")

SHEET = os.path.join(DATA, "truth", "대조표100_검수_2026-09-21.csv")
NS = uuid.UUID("6f1c0d2e-0000-4000-8000-000000000001")

ENTERED_BY = "hwansj"
VERIFIED_AT = "2026-09-21 20:00+09"
EVIDENCE = "네이버 지도 대조 2026-09-21. 100행 전수 확인이며 표시 없는 행은 원문과 같았다는 뜻이다."

#: 검수자가 쓴 칸 이름과 우리 항목의 대응
FIELDS = {
    "시작": "open", "종료": "close", "브레이크": "break",
    "라스트오더": "last_order", "휴무": "closure",
}


def q(value) -> str:
    if value is None or value == "":
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def jq(obj) -> str:
    return "$j$" + json.dumps(obj, ensure_ascii=False) + "$j$::jsonb"


def main() -> None:
    rows = list(csv.DictReader(open(SHEET, encoding="utf-8-sig")))
    parsed = {r["content_id"]: r for r in json.load(
        open(os.path.join(OUT, "parsed_hours.json"), encoding="utf-8"))}
    # 상호명으로 place_uid 를 되찾는다. 대조표에 uid 가 없다.
    by_name = {}
    for cid, r in parsed.items():
        by_name.setdefault(r["title"].strip(), []).append(cid)

    lines = ["-- 검수 정답셋. 생성 파일이므로 직접 고치지 않는다.",
             f"-- 원본: {os.path.relpath(SHEET, ROOT)}",
             "BEGIN;", "",
             "-- 같은 검수를 다시 넣어도 쌓이지 않게 이 검수자의 것을 먼저 비운다.",
             f"DELETE FROM dining.dn_quality_result WHERE truth_id IN "
             f"(SELECT truth_id FROM dining.dn_truth WHERE entered_by = '{ENTERED_BY}');",
             f"DELETE FROM dining.dn_truth WHERE entered_by = '{ENTERED_BY}';", ""]

    values, skipped = [], []
    n_same = n_diff = 0

    for row in rows:
        name = (row.get("상호") or "").strip()
        cids = by_name.get(name, [])
        if len(cids) > 1:
            # 같은 상호의 다른 지점이 목록에 생기면(법정동으로 다시 받자 참숯골 · 부부횟집이 둘이 됐다)
            # 대조표에 옮겨 적은 원문과 같은 가게로 좁힌다. 그래도 둘이면 넣지 않는다.
            text = re.sub(r"\s+", "", row.get("원문(영업시간)") or "")
            cids = [c for c in cids if text and re.sub(r"\s+", "", parsed[c].get("hours_text") or "") == text]
        if len(cids) != 1:
            # 상호가 없거나 겹치면 넣지 않는다. 엉뚱한 집에 정답을 붙이면 되돌리기 어렵다.
            skipped.append((row.get("번호"), name, "상호를 특정하지 못함"))
            continue
        cid = cids[0]
        place_uid = str(uuid.uuid5(NS, f"place:tourapi:{cid}"))

        real = {ko: row[ko].strip() for ko in FIELDS if (row.get(ko) or "").strip()}
        direction = (row.get("틀린방향") or "").strip()
        note = (row.get("메모") or "").strip()
        matches = not real and not direction

        if matches:
            n_same += 1
        else:
            n_diff += 1

        expected = {"원문이_실제와_같다": matches}
        if real:
            expected["실제값"] = real
        if direction:
            expected["틀린방향"] = direction

        truth_id = str(uuid.uuid5(NS, f"truth:reality:{cid}:weekday_hours"))
        values.append(
            f"    ('{truth_id}', '{place_uid}', 'reality', 'weekday_hours', '', "
            f"{jq(expected)}, {q((row.get('원문(영업시간)') or '')[:500])}, "
            f"{q(EVIDENCE)}, '{ENTERED_BY}', '{VERIFIED_AT}', {q(note or None)})")

        # 폐업은 영업시간과 다른 항목이므로 행을 따로 만든다.
        if "폐업" in direction:
            tid = str(uuid.uuid5(NS, f"truth:reality:{cid}:record_status"))
            values.append(
                f"    ('{tid}', '{place_uid}', 'reality', 'record_status', '', "
                f"{jq({'record_status': 'closed'})}, NULL, "
                f"{q(EVIDENCE)}, '{ENTERED_BY}', '{VERIFIED_AT}', {q(note or None)})")

    lines.append("INSERT INTO dining.dn_truth "
                 "(truth_id, place_uid, metric, check_kind, check_key, expected, "
                 "source_text, evidence, entered_by, verified_at, note) VALUES")
    lines.append(",\n".join(values))
    lines.append("ON CONFLICT (truth_id) DO NOTHING;")
    lines += ["", "COMMIT;"]

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "truth.sql")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print(f"검수 {len(rows)}행")
    print(f"  원문이 실제와 같음  {n_same}")
    print(f"  달랐음              {n_diff}")
    print(f"  넣지 못함           {len(skipped)}")
    for no, name, why in skipped:
        print(f"    {no} {name} — {why}")
    print(f"\n만든 정답 {len(values)}건")
    print(f"저장: {path}")


if __name__ == "__main__":
    main()
