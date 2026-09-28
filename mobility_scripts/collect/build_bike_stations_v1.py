# mobility_scripts/collect/build_bike_stations_v1.py — 따릉이 운영 대여소 목록 (22번 방 · 2026-09-20)
#
# 입력: raw/mobility/bike/_check/bike_station_active_<checked_at>.json  (17번 방 산출 — 마스터 ∩ 실시간 ID 집합)
# 출력: processed/mobility/bike_stations_v1.jsonl (+ _report.md)
#
# ★ 실시간 거치 수(parkingBikeTotCnt)는 **입력에도 없고 출력에도 없다** — 조회만 하고 저장하지 않는다(17번 §1 S3).
#   여기 담는 것은 정적 속성뿐이다: stationId · no · name · lat · lon · rack · mode(QR/LCD, xlsx 반기 → 추정) · gu.
# ★ 위치·존재 정본 = 마스터 ∩ 실시간 ID(확정 · checked_at 부착). mode/gu 는 xlsx(26.6월) 라 추정.
# 실행: python mobility_scripts/collect/build_bike_stations_v1.py [--src <json>] [--out <jsonl>]
import argparse, json, re, sys, collections
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SOURCE_ID_FMT = "seoul_bike_station@{date}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--out")
    a = ap.parse_args()
    if not (a.src and a.out):
        from mobility_scripts.collect._paths import RAW_MOBILITY, PROCESSED
        if not a.src:
            hits = sorted((RAW_MOBILITY / "bike" / "_check").glob("bike_station_active_*.json"))
            if not hits:
                raise SystemExit("bike_station_active_*.json 이 없다 — 17번 bike_check.py 를 먼저 돌린다")
            a.src = str(hits[-1])
        a.out = a.out or str(PROCESSED / "mobility" / "bike_stations_v1.jsonl")

    doc = json.loads(Path(a.src).read_text(encoding="utf-8"))
    rows = doc["rows"]
    checked = doc.get("checked_at", "")                       # '20260919_2155 KST'
    m = re.match(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})", checked)
    checked_iso = f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}+09:00" if m else checked
    date = checked_iso[:10]
    src_id = SOURCE_ID_FMT.format(date=date)

    out, modes = [], collections.Counter()
    bad = 0
    for r in rows:
        if r.get("lat") in (None, 0) or r.get("lon") in (None, 0):
            bad += 1
            continue
        mode = r.get("mode")                                    # 'QR' | 'LCD' | 'LCD,QR' | None(신설 — xlsx 에 없음)
        modes[mode] += 1
        out.append({"stationId": r["stationId"], "no": r.get("no"), "name": r.get("name"),
                    "lat": r["lat"], "lon": r["lon"], "rack": r.get("rack"),
                    "mode": mode, "gu": r.get("gu"),
                    "checked_at": checked_iso, "source_id": src_id,
                    "grade": {"position": "확정", "mode": "추정" if mode else "근거없음"}})
    out.sort(key=lambda x: int(x["stationId"].split("-")[1]) if "-" in x["stationId"] else 0)
    op = Path(a.out)
    op.parent.mkdir(parents=True, exist_ok=True)
    with open(op, "w", encoding="utf-8", newline="\n") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rep = op.with_name(op.stem + "_report.md")
    rep.write_text(
        f"# bike_stations_v1 — 따릉이 운영 대여소\n\n"
        f"- 입력: `{Path(a.src).name}` (17번 방 · {doc.get('source')})\n"
        f"- 확인 시각: {checked_iso} · source_id `{src_id}`\n"
        f"- 행: **{len(out):,}** (좌표 없음 제외 {bad})\n"
        f"- 운영방식: " + " · ".join(f"{k or '미상(신설)'} {v:,}" for k, v in modes.most_common()) + "\n"
        "- 등급: 존재·위치·거치대수 **확정**(마스터=실시간 좌표, 카카오 10/10) · 운영방식·자치구 **추정**(xlsx 26.6월 반기)\n"
        "- 실시간 거치 수는 담지 않는다 — 판정 시점에 `bikeList?stationId=` 로 조회만 한다.\n",
        encoding="utf-8", newline="\n")
    print(f"{op} — {len(out):,}행 · {dict(modes)} · 좌표 없음 {bad}")
    print(f"{rep}")


if __name__ == "__main__":
    main()
