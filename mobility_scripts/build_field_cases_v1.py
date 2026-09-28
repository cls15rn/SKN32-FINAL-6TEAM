#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""25 실측(티머니 태그) → 판정 케이스 (28번 방 · 2026-09-27)

왜: 40 로그의 `run_id` 는 실행 묶음 id 라 25 정답(`R01-09-22` …)과 조인이 안 된다. 실측 여정은
판정기에 들어간 적이 없다. 그래서 태그 한 여정 = 케이스 한 건으로 만들어 지금 판정기로 돌리고
로그에 `source=field` 로 쌓는다(source 별 · 합치지 않음).

  python mobility_scripts/build_field_cases_v1.py            # → DATA_DIR/travel/processed/mobility/ground_truth/field_legs_v1.json

케이스 규칙
  · 출발 = 첫 승차 태그(지하철 = 개찰 · 대기 포함 / 버스 = 차내 태그 · 정류장 대기 끝난 뒤)
    → 버스 첫 구간은 판정기가 배차 절반 대기를 더하므로 **예정이 실제보다 늦게 나오는 쪽으로 기운다**(보고서에 적는다)
  · 정답 도착 = 마지막 하차 태그 · 구간 정답 = 각 하차 태그(지하철 3→7 환승은 태그가 없어 끝만)
  · `expect: feasible` — 실제로 탄 여정이다(2×2 에서 불가 판정 = 거짓 음성)
  · 버스 노선은 태그에 없다 → 아래 ROUTE 표(정류장 쌍 → 노선 · 근거). 표에 없는 쌍은 **케이스를 만들지 않고** 제외 목록에
  · H01(T29) 은 지하철 구간만(휴일 시간표 대조 · 버스 노선 모름)
  · 좌표·경로는 넣지 않는다(역명·정류장명만 · 로그 차단 목록과 같은 원칙)
"""
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine.paths import PROCESSED, RAW_MOBILITY   # noqa: E402

GT = PROCESSED / "mobility" / "ground_truth"
TAGS = RAW_MOBILITY / "ground_truth" / "tmoney_tags_202609.csv"
TRIPS = GT / "commute_trips_202609.csv"
FIELD = GT / "r13_field_log_v1.csv"
OUT = GT / "field_legs_v1.json"

# 정류장 쌍 → (노선, 근거). 방향은 판정기가 정류장 순서로 고른다(bus_stops_v2 seq 로 확인).
ROUTE = {
    ("남성역", "이수역5번출구"): ("742", "742·040 같은 구간(교대역 방면 55→60) · 25 R01-09-23 메모 「742 가능성 높음」"),
    ("남성역", "이수역9번출구"): ("752", "commute_trips route_variant 「752(9번출구 하차)」 · 구산동 방면 67→71"),
    ("이수역5번출구", "서일초등학교"): ("4319", "잠실역 방면 4→15"),
    ("이수역5번출구", "국제전자센터"): ("4319", "잠실역 방면 4→14 · T02 하차 태그 조기(본인 추정)"),
    ("서일초등학교", "내방역5번출구"): ("4319", "남태령역 방면 62→71"),
    ("서일초등학교", "방일초교"): ("4319", "남태령역 방면 62→70 · 25 R02-09-22·23"),
    ("서일초등학교", "이수역"): ("4319", "남태령역 방면 62→73"),
    ("남성역골목시장", "남성초등학교"): ("742", "742·040 같은 구간(교대역 방면 56→58)"),
    ("이수역10번출구", "남성역골목시장"): ("742", "구산동 방면 73→75 · 040 같은 구간"),
    ("남성역", "남성초등학교"): ("742", "교대역 방면 55→58 · 040 같은 구간"),
    ("내방역5번출구", "남성역"): ("742", "구산동 방면 70→76 · 040 같은 구간(62→68)"),
}
# 역 쌍 → 지하철 구간(태그는 개찰·하차뿐 — 환승은 우리 표 경로)
SUBWAY = {
    ("남부터미널", "남성"): [("03호선", "남부터미널", "고속터미널"), ("07호선", "고속터미널", "남성")],
    ("남성", "남부터미널"): [("07호선", "남성", "고속터미널"), ("03호선", "고속터미널", "남부터미널")],
    ("내방", "남성"): [("07호선", "내방", "남성")],
    ("봉천", "낙성대"): [("02호선", "봉천", "낙성대")],
}
# 25 실측 run 과 태그 여정의 짝(25 전달문 · 태그 시각 일치로 확인)
RUN_OF = {"T23": "R02-09-21", "T24": "R01-09-22", "T25": "R02-09-22", "T26": "R01-09-23",
          "T27": "R02-09-23", "T29": "H01-09-26"}
PATTERN_EXTRA = {"T23": "퇴근_지하철", "T24": "출근_지하철", "T25": "퇴근_버스지하철", "T26": "출근_버스버스",
                 "T27": "퇴근_버스지하철", "T28": "기타", "T29": "휴일_지하철"}


def norm_stop(s):
    s = s.replace("(추정)", "").strip()
    s = re.sub(r"\(예술의전당\)|\(강감찬\)", "", s)
    return s.strip()


def read_csv(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main():
    tags = read_csv(TAGS)
    trips = {r["trip_id"]: r for r in read_csv(TRIPS)}
    by = {}
    for t in tags:
        by.setdefault(t["trip_id"], []).append(t)
    cases, skipped = [], []
    for tid in sorted(by, key=lambda x: int(x[1:])):
        rows = sorted(by[tid], key=lambda r: int(r["tag_seq"]))
        tr = trips.get(tid, {})
        pattern = tr.get("pattern") or PATTERN_EXTRA.get(tid, "기타")
        if tr.get("status") == "제외_개인":
            skipped.append({"trip": tid, "why": "제외_개인(25 표시)"})
            continue
        # board/alight 쌍으로 자른다
        pairs, i = [], 0
        while i + 1 < len(rows):
            a, b = rows[i], rows[i + 1]
            if a["tag_role_inferred"] != "board" or b["tag_role_inferred"] != "alight" or a["mode_inferred"] != b["mode_inferred"]:
                break
            pairs.append((a, b))
            i += 2
        if i != len(rows):
            skipped.append({"trip": tid, "why": "태그 짝이 안 맞음"})
            continue
        if tid == "T29":                               # H01 — 지하철만(휴일 시간표 대조)
            pairs = [p for p in pairs if p[0]["mode_inferred"] == "subway"]
        legs, leg_truth, why = [], [], None
        for a, b in pairs:
            fr, to = norm_stop(a["stop_name"]), norm_stop(b["stop_name"])
            if a["mode_inferred"] == "bus":
                hit = ROUTE.get((fr, to))
                if not hit:
                    why = f"버스 노선 모름({fr}→{to})"
                    break
                legs.append({"mode": "bus", "route": hit[0], "from": fr, "to": to})
                leg_truth.append({"n_legs": 1, "board": a["time"], "alight": b["time"], "route_basis": hit[1]})
            else:
                seq = SUBWAY.get((fr, to))
                if not seq:
                    why = f"지하철 경로 모름({fr}→{to})"
                    break
                legs += [{"line": ln, "from": x, "to": y} for ln, x, y in seq]
                leg_truth.append({"n_legs": len(seq), "board": a["time"], "alight": b["time"]})
        if why:
            skipped.append({"trip": tid, "why": why})
            continue
        mode = "+".join("bus" if a["mode_inferred"] == "bus" else "subway" for a, _ in pairs)
        c = {
            "id": f"FIELD-{tid}",
            "note": f"25 실측 태그 {tid} · {pattern}" + (f" · run {RUN_OF[tid]}" if tid in RUN_OF else ""),
            "date": rows[0]["date"],
            "depart_at": pairs[0][0]["time"],
            "legs": legs,
            "expect": "feasible",
            "field": {
                "trip_id": tid, "run_id": RUN_OF.get(tid), "pattern": pattern, "mode": mode,
                "weekday": rows[0]["weekday"],
                "actual_first_board": pairs[0][0]["time"], "actual_last_alight": pairs[-1][1]["time"],
                "legs_truth": leg_truth,
                "d2d_min_lo": tr.get("d2d_min_lo") or None, "d2d_min_hi": tr.get("d2d_min_hi") or None,
                "status": tr.get("status") or None,
            },
        }
        cases.append(c)
    doc = {
        "note": "28 — 25 실측 티머니 태그를 판정 케이스로(출발 = 첫 승차 태그 · 정답 = 하차 태그). expect=feasible 은 「실제로 탔다」. "
                "개인 동선이라 git 에 안 올린다(DATA_DIR).",
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sources": [str(TAGS.name), str(TRIPS.name), str(FIELD.name)],
        "주의": "표본 = 본인 출퇴근 노선(742·040·752·4319·3·7호선 · 2호선 1) — 지표는 「이 노선들에서」로만 읽는다",
        "skipped": skipped,
        "cases": cases,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"케이스 {len(cases)} · 제외 {len(skipped)} → {OUT}")
    for s in skipped:
        print(f"  제외 {s['trip']}: {s['why']}")


if __name__ == "__main__":
    main()
