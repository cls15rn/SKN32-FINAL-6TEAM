# mobility_scripts/collect/seoul_fill_gaps.py — 통합 시간표의 빈 곳을 서울 열린데이터광장으로 메운다
# 실행: 저장소 루트에서  python mobility_scripts/collect/seoul_fill_gaps.py   (선행: build_timetable_v1.py 로 meta 생성)
#       python mobility_scripts/collect/seoul_fill_gaps.py --only branch     (지선만)
#
# 두 가지를 채운다.
#
# ① 빈 곳(gaps) — TAGO 에 없는 조합. meta 의 gaps 를 그대로 따라간다.
#
# ② ★ 지선 접속역 — **빈 곳 판정과 무관하게** 채운다 (2026-09-10 추가).
#    신도림은 TAGO 로 본선(성수행 475편)이 이미 차 있어 "빈 곳"이 아니었다. 그래서 한 번도 조회되지
#    않았고, **신정지선 승강장이 통째로 누락됐다** — 신도림→도림천이 0편인데 도림천에는 110편이 있다.
#    성수도 같다(신설동행 0편, 신답 113편). 매핑 문제가 아니라 **수집 범위 문제**다.
#    "빈 곳만 채운다"는 규칙이 이미 찬 역의 다른 승강장을 놓친 것이다.
#
#    지선은 하드코딩하지 않고 **열린데이터광장 역 목록의 FR_CODE 로 유도**한다 —
#    '234-1'(도림천)은 접속역 '234'(신도림)의 지선이라는 뜻이다. 새 지선이 생기면 자동으로 잡힌다.
#
#    ★ 접속역을 통째로 받으면 TAGO 가 이미 가진 **본선 열차가 중복 적재**된다(두 소스는 dedup 키가
#      다르다). 그래서 지선 열차만 골라 남긴다.
#
#    ★★ 2026-09-11 수정 — 고르는 기준이 틀려서 **필요한 절반을 버리고 있었다.**
#      원래 기준은 "SUBWAYSNAME(행선지)이 지선 역인 행"이었는데, **OA-101 의 SUBWAYSNAME 은
#      행선지가 아니라 시발역**이다. 그래서 이 기준은 *지선에서 출발해 접속역에 도착하는* 열차만
#      남기고(도착이라 출발 시각이 없다), 정작 필요한 *접속역에서 출발해 지선으로 들어가는* 열차를
#      버렸다 — 그 행의 SUBWAYSNAME 은 접속역 자신이기 때문이다.
#      그래서 신도림→도림천이 평일 0편이었다(도림천→신도림은 110편). 지선 승강장을 받아 왔는데
#      필터가 도로 지운 것이다.
#      고친 기준: **지선 역 시간표에 나오는 TRAIN_NO 를 먼저 모으고, 접속역 행 중 그 열차번호를
#      가진 행을 남긴다.** 방향과 무관하게 지선 열차를 정확히 집는다.
#
# 호출: gaps 는 빈 역 × 방향 2 × 요일. 지선은 접속역 × 방향 2 × 요일 2 (지금 기준 5접속역 = 20회).
import argparse, os, json, time, collections, requests
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY, PROCESSED

KEY = os.environ["SEOUL_OPENAPI_KEY"]
BASE = f"http://openapi.seoul.go.kr:8088/{KEY}/json"
SVC = "SearchSTNTimeTableByIDService"
KST = timezone(timedelta(hours=9))

META = PROCESSED / "mobility" / "timetable_v1_meta.json"
STATIONS = RAW_MOBILITY / "stations_all.json"
OUT_GAPS = RAW_MOBILITY / "seoul_timetable_fill.jsonl"
OUT_BRANCH = RAW_MOBILITY / "seoul_timetable_branch.jsonl"     # 덮어쓴다 — 재실행이 안전하도록
REPORT = RAW_MOBILITY / "seoul_fill_gaps_report.json"

SEOUL_LINES = {f"{i:02d}호선" for i in range(1, 10)}      # 서울교통공사 관할만
WEEK = {"weekday": "1", "holiday": "3"}                   # 열린데이터광장 요일 태그 (2 = 토요일)

ap = argparse.ArgumentParser()
ap.add_argument("--only", choices=["gaps", "branch"], help="한쪽만 실행")
args = ap.parse_args()

stations = json.loads(STATIONS.read_text(encoding="utf-8"))
cd_of = {(s["LINE_NUM"], s["STATION_NM"]): s["STATION_CD"] for s in stations}
nm_of_fr = {(s["LINE_NUM"], str(s.get("FR_CODE") or "")): s["STATION_NM"] for s in stations}


def fetch(cd, wk, updn):
    """역코드·요일·방향으로 시간표 행을 받는다."""
    try:
        j = requests.get(f"{BASE}/{SVC}/1/1000/{cd}/{wk}/{updn}/", timeout=20).json()
    except Exception as e:
        print(f"    호출 실패 {e}")
        return []
    body = j.get(SVC) or {}
    time.sleep(0.15)
    return body.get("row") or []


report = {"filled_at": datetime.now(KST).isoformat(timespec="seconds"), "source": "seoul_opendata_OA-101"}

# ── ① 빈 곳 ─────────────────────────────────────────────────────────
if args.only != "branch":
    if not META.exists():
        raise SystemExit(f"{META} 가 없다. build_timetable_v1.py 를 먼저 돌린다.")
    meta = json.loads(META.read_text(encoding="utf-8"))
    targets = []
    for line, g in meta.get("gaps", {}).items():
        if line not in SEOUL_LINES:
            continue
        for day_key, names in g.items():
            if day_key not in WEEK:
                continue
            for nm in names:
                cd = cd_of.get((line, nm))
                if cd:
                    targets.append((line, nm, cd, day_key, WEEK[day_key]))
                else:
                    print(f"  역코드 없음: {line} {nm}")
    print(f"① 빈 곳 {len(targets)}건 (역×요일) — 호출 {len(targets) * 2}회 예상")
    got, empty, rows = [], [], 0
    with OUT_GAPS.open("a", encoding="utf-8") as f:
        for i, (line, nm, cd, day_key, wk) in enumerate(targets, 1):
            n = 0
            for updn in ("1", "2"):
                for r in fetch(cd, wk, updn):
                    f.write(json.dumps({**r, "_updn": updn, "_dow": wk}, ensure_ascii=False) + "\n")
                    n += 1
            rows += n
            (got if n else empty).append(f"{line}|{nm}|{day_key}")
            print(f"  [{i}/{len(targets)}] {line} {nm} {day_key}: {n}건")
    report["gaps"] = {"targets": len(targets), "rows": rows, "filled": got, "still_empty": empty}
    print(f"  → 행 {rows}개 · 채움 {len(got)} · 여전히 빈 곳 {len(empty)}")

# ── ② 지선 접속역 ───────────────────────────────────────────────────
if args.only != "gaps":
    # FR_CODE 로 지선을 유도한다: '234-1' → 접속역 '234'
    branch = collections.defaultdict(set)          # (line, 접속역명) → {지선 역명}
    for s in stations:
        fr = str(s.get("FR_CODE") or "")
        if "-" not in fr:
            continue
        junction = nm_of_fr.get((s["LINE_NUM"], fr.split("-")[0]))
        if junction:
            branch[(s["LINE_NUM"], junction)].add(s["STATION_NM"])

    fr_of = {(s["LINE_NUM"], s["STATION_NM"]): str(s.get("FR_CODE") or "") for s in stations}

    def fr_base_of(ln, dests):
        """지선 역들의 공통 기본 FR 번호('234-1' → '234')."""
        return {fr_of[(ln, d)].split("-")[0] for d in dests if (ln, d) in fr_of}

    todo = [(ln, j, dests) for (ln, j), dests in sorted(branch.items()) if ln in SEOUL_LINES]
    print(f"\n② 지선 접속역 {len(todo)}곳 — 호출 {len(todo) * 4}회 예상")
    for ln, j, dests in todo:
        print(f"  {ln} {j} ← {'·'.join(sorted(dests))}")

    brows, bgot, bempty = 0, [], []
    with OUT_BRANCH.open("w", encoding="utf-8") as f:
        for ln, j, dests in todo:
            cd = cd_of.get((ln, j))
            if not cd:
                print(f"  역코드 없음: {ln} {j}")
                continue
            # 접속역 바로 옆 지선 역(FR '<base>-1')의 시간표에서 지선 열차번호를 모은다.
            # ★ fr_base_of 는 set 이라 원소가 둘 이상이면 next(iter(...)) 가 PYTHONHASHSEED 에 따라 흔들린다.
            #   2026-09-21(35번 방) stations_all.json 기준 서울 접속역 5곳 모두 원소 1개(금천구청 P144 · 병점 P157 ·
            #   소요산 100 · 성수 211 · 신도림 234) → 지금 산출은 영향 없음. 둘 이상이 생기면 sorted(...) 로 고정할 것.
            first = nm_of_fr.get((ln, f"{next(iter(fr_base_of(ln, dests)), '')}-1"))
            probe_cd = cd_of.get((ln, first)) if first else None
            for day_key, wk in WEEK.items():
                branch_trains = set()
                if probe_cd:
                    for updn in ("1", "2"):
                        for r in fetch(probe_cd, wk, updn):
                            if r.get("TRAIN_NO"):
                                branch_trains.add(str(r["TRAIN_NO"]).strip())
                n = kept = by_name = by_train = 0
                for updn in ("1", "2"):
                    for r in fetch(cd, wk, updn):
                        n += 1
                        nm_hit = (r.get("SUBWAYSNAME") or "").strip() in dests
                        tn_hit = str(r.get("TRAIN_NO") or "").strip() in branch_trains
                        if not (nm_hit or tn_hit):
                            continue
                        f.write(json.dumps({**r, "_updn": updn, "_dow": wk}, ensure_ascii=False) + "\n")
                        kept += 1; by_name += nm_hit; by_train += tn_hit and not nm_hit
                brows += kept
                (bgot if kept else bempty).append(f"{ln}|{j}|{day_key}")
                print(f"  {ln} {j} {day_key}: 받은 {n}건 중 지선 {kept}건 "
                      f"(시발역명 {by_name} · 열차번호 {by_train}, 지선 열차 {len(branch_trains)}개)")
    report["branch"] = {"junctions": [f"{ln}|{j}" for ln, j, _ in todo], "rows": brows,
                        "filled": bgot, "still_empty": bempty,
                        "note": ("지선 역 시간표의 TRAIN_NO 로 지선 열차를 집었다(2026-09-11). "
                                 "이전 기준(SUBWAYSNAME 이 지선 역)은 접속역→지선 출발 행을 통째로 버렸다 — "
                                 "SUBWAYSNAME 은 행선지가 아니라 시발역이기 때문이다.")}
    print(f"  → 지선 행 {brows}개 → {OUT_BRANCH.name}")
    if bempty:
        print(f"  지선 0건: {', '.join(bempty)}  (코레일 위탁 구간이면 이 API 에 없다)")

REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\n리포트 → {REPORT}")
print("다음: python mobility_scripts/collect/build_timetable_v1.py  (보충분을 합쳐 커버리지 재계산)")
print("      python mobility_scripts/consistency_check.py           (소스 간 회귀)")
