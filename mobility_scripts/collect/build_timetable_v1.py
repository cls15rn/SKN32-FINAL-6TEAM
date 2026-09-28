# mobility_scripts/collect/build_timetable_v1.py — raw 시간표(TAGO + 열린데이터광장 보충)를 판정기용 통합 시간표로
# 실행: 저장소 루트에서  python mobility_scripts/collect/build_timetable_v1.py [--tago-date 2026-09-09] [--seoul-date 2026-09-09]
# 출력: processed/mobility/timetable_v1.jsonl (열차 1건 = 1행, fetched_at·source 부여)
#       processed/mobility/timetable_v1_meta.json · timetable_v1_coverage.md (노선별 커버리지·빈 곳·제외 내역)
# 이 스크립트가 tago_postprocess.py 를 대체한다. API 호출 없음.
import json, re, argparse, collections
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
OUT_DIR = PROCESSED / "mobility"
OUT = OUT_DIR / "timetable_v1.jsonl"
META = OUT_DIR / "timetable_v1_meta.json"
COVER = OUT_DIR / "timetable_v1_coverage.md"

STATIONS = RAW_MOBILITY / "stations_all.json"
ID_MAP = RAW_MOBILITY / "tago_station_ids.json"
TAGO = RAW_MOBILITY / "tago_timetable.jsonl"
TAGO_DONE = RAW_MOBILITY / "tago_timetable_done.json"
# 열린데이터광장 보충분 — 2·7호선 휴일(seoul_fill_holiday.py) + 빈 곳 메움(seoul_fill_gaps.py)
SEOUL_FILLS = sorted(RAW_MOBILITY.glob("seoul_timetable_*.jsonl"))

SRC_TAGO = "tago_subway"                       # Evidence.source_id = f"{source}@{fetched_at}"
SRC_SEOUL = "seoul_opendata_OA-101"
EXCLUDE_PREFIX = ("MTRBS", "MTRDG", "MTRDJ", "MTRGJ", "MTRIAM")   # 부산·대구·대전·광주·인천공항자기부상(폐선)
EXCLUDE_ROUTE = {"동해"}                                            # 부산 동해선(코레일 접두어라 접두어로 못 거름)
# TAGO 노선 표기 → 열린데이터광장 LINE_NUM
ROUTE2LINE = {"1호선": "01호선", "2호선": "02호선", "3호선": "03호선", "4호선": "04호선", "5호선": "05호선",
              "6호선": "06호선", "7호선": "07호선", "8호선": "08호선", "9호선": "09호선",
              "공항": "공항철도", "경의중앙": "경의선", "수인분당": "수인분당선", "경춘": "경춘선", "경강": "경강선",
              "신분당": "신분당선", "우이신설": "우이신설경전철", "에버라인": "용인경전철", "의정부": "의정부경전철",
              "김포골드라인": "김포도시철도", "인천1호선": "인천선", "인천2호선": "인천2호선", "서해선": "서해선",
              "신림선": "신림선", "GTX-A": "GTX-A"}
DAY = {"01": "weekday", "02": "saturday", "03": "holiday"}   # 수도권 TAGO 는 토·일·공휴일 = 03


def hhmmss(s):
    """'235200' → '23:52:00' · '0' → None · 'HH:MM:SS' 형태도 그대로 받는다.

    ★ 자정을 넘긴 열차를 24 시 이후로 정규화한다 (2026-09-10 추가).
      소스는 00:05 출발 열차를 '000500' 으로 준다. 그대로 저장하면 그 행이 하루의
      **가장 이른 시각**이 되어 min() 이 첫차로, max() 가 막차로 잡는다. 둘 다 틀린다.
      `consistency_check.py` C4(열린데이터광장 교차검증)에서 발견했다 —
      2호선 강변 평일이 첫차 00:05 / 막차 23:53 으로 나왔는데 실제는 05:36 / 24:50 이다.
      막차 판정이 이 모듈의 본체이므로 조용히 틀리면 안 된다.
      지하철 첫차는 05 시대이므로 04 시 이전은 익일 운행분으로 본다. 이미 24 이상이면 둔다.
      단 '000000'(출발 없음)은 시각이 아니므로 보정 전에 걸러낸다 — 아래 참고."""
    if not s or s == "0":
        return None
    d = re.sub(r"\D", "", str(s))
    if len(d) < 6:
        return None
    if int(d) == 0:               # ★ '000000' = 출발 없음(시·종착역). 2026-09-10 추가.
        return None               #   아래 +24 보정에 걸리면 24:00:00 이 되어 종착역마다
                                  #   가짜 막차가 생긴다. 실제로 2,527 행이 그렇게 됐고
                                  #   51개 (노선,역,방향,요일) 조합의 막차가 24:00 으로 잡혔다
                                  #   (2호선 성수·까치산, 7호선 온수·석남·도봉산·장암, 6호선 응암 등).
                                  #   '소스가 0 으로 주는데 0시로 저장하면 막차 판정이 뒤집힌다'는
                                  #   기존 규칙이 자정 넘김 보정 뒤에 새 모습으로 되살아난 것이다.
    h = int(d[:2])
    if h < 4:                     # 04 시 이전 = 전날 시간표의 익일 운행분
        h += 24
    return f"{h:02d}:{d[2:4]}:{d[4:6]}"



def zero(v):
    """열린데이터광장의 '없음' 표기. '000000' · '0' · 빈 값."""
    d = re.sub(r"\D", "", str(v or ""))
    return (not d) or int(d) == 0


def seoul_dest_map(files):
    """열린데이터광장 OA-101 의 진짜 행선지를 열차 단위로 복원한다.

    ★ `SUBWAYSNAME` 은 행선지가 아니라 **시발역**이다 (2026-09-11 확인).
      역 순서 표(line_station_order_v1)로 대조해 확정했다 — 7호선 휴일 '석남'행으로
      적힌 열차가 석남 05:28 → 부평구청 05:33 → 온수 05:51 → … → 도봉산 07:29 로
      석남에서 **출발**한다. TRAIN_NO 로 열차를 복원해 세어 보면
      SUBWAYSNAME == 첫 역 2,079건 · == 끝 역 **0건**이다.
      그대로 두면 2·7호선 휴일(34,387행)의 막차 행선지 판정이 통째로 뒤집힌다.

      진짜 행선지는 같은 TRAIN_NO 안에서 **출발 시각이 없고 도착 시각만 있는 행**,
      즉 종착 행의 역이다. 이 신호는 소스 자체에서 나오므로 우리 역 순서 표에
        기대지 않는다. 열차의 운행이 일부만 수집된 경우(빈 곳 메움 분)에는
      종착 행이 없거나 둘 이상이라 **행선지를 None 으로 둔다 → 판정에서 근거없음**.
    """
    rows_by_train = collections.defaultdict(list)
    for path in files:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                tn = r.get("TRAIN_NO")
                if not tn:
                    continue
                updn = r.get("_updn", r.get("INOUT_TAG"))
                rows_by_train[(r["LINE_NUM"], r.get("WEEK_TAG"), updn, tn)].append(r)
    dest, stats = {}, collections.Counter()
    for k, rs in rows_by_train.items():
        term = [r for r in rs if zero(r.get("LEFTTIME")) and not zero(r.get("ARRIVETIME"))]
        # ★ 종착 '행'이 몇 개인가가 아니라 종착 '역'이 하나로 정해지는가를 본다 (2026-09-12).
        #   지선 보충분과 기존 수집분에 **같은 행이 두 번** 들어온다 — 5501 열차(02호선 휴일)의
        #   신도림 종착 행이 seoul_timetable_branch 와 seoul_timetable_holiday_2_7 양쪽에
        #   같은 시각으로 존재한다. 행 수로 세면 그 중복이 '모호함'으로 읽혀 복원이 통째로 막히고,
        #   행선지가 None 이 되어 판정이 근거없음으로 떨어진다.
        #   실측(입력 3파일 기준): 행 수 기준으로 막혀 있던 487열차(2,262행)가 **전부** 단일 종착역이고
        #   종착역이 실제로 둘 이상인 열차는 **0개**였다. 그래서 느슨하게 해도 오복원 위험이 없다.
        #   되돌리는 조건: 종착역이 실제로 둘 이상인 열차가 나오면(회차·분리 편성) 다시 좁힌다.
        names = {r["STATION_NM"] for r in term}
        if len(names) == 1:
            dest[k] = next(iter(names))
            stats["복원"] += len(rs)
        else:
            stats["복원못함"] += len(rs)
    # 슬롯 맵 — (노선, 역, 방향, 요일, 출발시각) → 복원한 행선지.
    # TAGO 쪽에 같은 슬롯이 있는데 행선지가 비어 있으면 이쪽을 남기려고 미리 만들어 둔다.
    slot = {}
    for k, rs in rows_by_train.items():
        d = dest.get(k)
        if not d:
            continue
        for r in rs:
            t = hhmmss(r.get("LEFTTIME"))
            if not t:
                continue
            updn = r.get("_updn", r.get("INOUT_TAG"))
            slot[(r["LINE_NUM"], r["STATION_NM"], "U" if updn == "1" else "D",
                  {"1": "weekday", "2": "saturday", "3": "holiday"}.get(r.get("WEEK_TAG"), r.get("WEEK_TAG")),
                  t)] = d
    return dest, slot, stats


ap = argparse.ArgumentParser()
ap.add_argument("--tago-date"); ap.add_argument("--seoul-date")
args = ap.parse_args()
tago_date = args.tago_date or datetime.fromtimestamp(TAGO.stat().st_mtime, KST).strftime("%Y-%m-%d")
seoul_date = args.seoul_date or (datetime.fromtimestamp(max(p.stat().st_mtime for p in SEOUL_FILLS), KST).strftime("%Y-%m-%d") if SEOUL_FILLS else None)

# ── 참조: 열린데이터광장 역 목록, TAGO 역 ID → (열린데이터광장 역명, 노선) ──
stations = json.loads(STATIONS.read_text(encoding="utf-8"))
by_line_name = {(s["LINE_NUM"], s["STATION_NM"]): s for s in stations}
by_cd = {s["STATION_CD"]: s for s in stations}
id_map = json.loads(ID_MAP.read_text(encoding="utf-8"))
# ★한 역 ID를 여러 역명이 가리킬 수 있다(이수/총신대입구, 서울/서울역).
#   그 노선의 열린데이터광장 역명과 일치하는 쪽을 고른다 — 아무거나 쓰면 station_key 가 어긋나
#   그 역이 통째로 빈 역으로 보인다.
tago_ref = {}
for nm, its in id_map.items():
    for it in its:
        sid = it["subwayStationId"]; route = it.get("subwayRouteName")
        line_num = ROUTE2LINE.get(route)
        exact = line_num is not None and (line_num, nm) in by_line_name
        prev = tago_ref.get(sid)
        if prev is None or (exact and not prev[2]):
            tago_ref[sid] = (nm, route, exact)
tago_ref = {sid: (nm, route) for sid, (nm, route, _) in tago_ref.items()}
unmatched_names = sorted(nm for nm, its in id_map.items() if not its)

seoul_dest, seoul_slot, seoul_dest_stats = seoul_dest_map(SEOUL_FILLS)
print(f"열린데이터광장 행선지 복원: 열차 {len(seoul_dest):,} · "
      f"행 {seoul_dest_stats['복원']:,} 복원 / {seoul_dest_stats['복원못함']:,} 못함(→ 행선지 없음)")

def base_nm(v):
    """행선지 표기 변형을 비교용으로 맞춘다. '신창(순천향대)'·'하남검단산역' → '신창'·'하남검단산'."""
    if not v:
        return None
    v = re.sub(r"\s*\(.*?\)\s*", "", v).strip()
    return v[:-1] if v.endswith("역") and len(v) > 1 else v


tago_slot = collections.defaultdict(set)   # (노선,역,방향,요일,출발시각) → {행선지}
cross_dup = 0
cross_conflict = []
tago_thin = 0

OUT_DIR.mkdir(parents=True, exist_ok=True)
rows = 0; dup = 0; excluded = collections.Counter(); seen = set()
cover = collections.defaultdict(lambda: collections.defaultdict(set))   # line → day → {station_nm}
src_count = collections.Counter()
unknown_route = collections.Counter()


def emit(g, rec, key):
    global rows, dup
    if key in seen:
        dup += 1; return
    seen.add(key)
    g.write(json.dumps(rec, ensure_ascii=False) + "\n"); rows += 1
    cover[rec["line"]][rec["day_type"]].add(rec["station_nm"])
    src_count[rec["source"]] += 1


with OUT.open("w", encoding="utf-8") as g:
    # ① TAGO
    with TAGO.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            sid = r["station_id"]
            nm, route = tago_ref.get(sid, (r.get("station_nm"), None))
            # route 가 None = 지금 id_map 에 없는 역 ID. 보정 과정에서 수도권 밖 역이 서울 역으로
            # 교체되면 옛 행이 jsonl 에 남는다(교대 부산·동해선 등). 노선을 모르는 행은 넣지 않는다.
            if sid.startswith(EXCLUDE_PREFIX) or route in EXCLUDE_ROUTE or route is None:
                excluded[route or ("stale:" + sid[:5])] += 1; continue
            line_num = ROUTE2LINE.get(route)
            if not line_num:
                unknown_route[route] += 1
            st = by_line_name.get((line_num, nm)) if line_num else None
            rec = {
                "station_key": f"{line_num or route}|{nm}", "station_cd": st["STATION_CD"] if st else None,
                "station_nm": nm, "station_nm_en": st["STATION_NM_ENG"] if st else None,
                "line": line_num or route, "dir": r["updown"], "day_type": DAY.get(r["daily_type"], r["daily_type"]),
                "dep_time": hhmmss(r.get("dep_time")), "arr_time": hhmmss(r.get("arr_time")),
                "dest_nm": r.get("end_station_nm"), "orig_nm": None, "train_no": None, "express": None,
                "source": SRC_TAGO, "source_station_id": sid,
                "fetched_at": r.get("fetched_at", tago_date)[:10], "fetched_at_precision": "day" if "fetched_at" not in r else "second",
            }
            # ★ 같은 슬롯을 열린데이터광장이 **행선지까지 갖고** 갖고 있는데 이 행은 행선지가 비어 있다면,
            #   같은 열차를 정보가 적은 쪽으로 두 번 싣는 것이다. 이쪽을 버린다 (2026-09-11).
            #   7호선 상봉 평일 83편이 그렇다 — TAGO 는 행선지가 없고 광장은 '온수'를 준다.
            #   양쪽 다 행선지가 있는데 서로 다르면(병점 상행 12편) 판단 근거가 없어 둘 다 남긴다.
            if rec["dep_time"] and rec["dest_nm"] is None:
                if seoul_slot.get((rec["line"], rec["station_nm"], rec["dir"],
                                   rec["day_type"], rec["dep_time"])):
                    tago_thin += 1
                    continue
            if rec["dep_time"]:
                tago_slot[(rec["line"], rec["station_nm"], rec["dir"], rec["day_type"], rec["dep_time"])].add(
                    base_nm(rec["dest_nm"]))
            emit(g, rec, ("T", sid, r["daily_type"], r["updown"], r.get("dep_time"), r.get("arr_time"), r.get("end_station_id"), r.get("route_id")))
    # ② 열린데이터광장 보충 (2·7호선 휴일 + 빈 곳 메움 + 지선 접속역)
    for SEOUL_FILL in SEOUL_FILLS:
        with SEOUL_FILL.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                st = by_cd.get(r["STATION_CD"], {})
                rec = {
                    "station_key": f"{r['LINE_NUM']}|{r['STATION_NM']}", "station_cd": r["STATION_CD"],
                    "station_nm": r["STATION_NM"], "station_nm_en": st.get("STATION_NM_ENG"),
                    "line": r["LINE_NUM"], "dir": "U" if r.get("_updn", r.get("INOUT_TAG")) == "1" else "D",   # 1 상행/내선 → U [추정: TAGO U/D 와 같은 방향 정의]
                    "day_type": {"1": "weekday", "2": "saturday", "3": "holiday"}.get(r.get("WEEK_TAG"), r.get("WEEK_TAG")),
                    "dep_time": hhmmss(r.get("LEFTTIME")), "arr_time": hhmmss(r.get("ARRIVETIME")),   # 자정 넘김 정규화(위 hhmmss 주석)
                    # SUBWAYSNAME 은 시발역이다 — orig_nm 으로 담고 행선지는 열차 단위로 복원한다
                    "orig_nm": r.get("SUBWAYSNAME"),
                    "dest_nm": seoul_dest.get((r["LINE_NUM"], r.get("WEEK_TAG"),
                                               r.get("_updn", r.get("INOUT_TAG")), r.get("TRAIN_NO"))),
                    "train_no": r.get("TRAIN_NO"), "express": r.get("EXPRESS_YN"),
                    "source": SRC_SEOUL, "source_station_id": r["STATION_CD"],
                    "fetched_at": seoul_date, "fetched_at_precision": "day",
                }
                # ★ 소스 간 중복 제거 (2026-09-11 추가)
                #   지선 접속역을 열차번호로 걸러 오면서 **TAGO 가 이미 가진 열차가 같이 딸려온다.**
                #   1호선 금천구청·병점·소요산이 그렇다 — 그 지선(광명·서동탄·연천)은 코레일 광역전철이라
                #   TAGO 가 이미 전부 갖고 있다. 같은 열차가 두 번 들어가면 편수가 부풀고
                #   소요산 상행은 21편이 42편이 된다(배차·막차 판정이 흔들린다).
                #   반대로 2호선 성수·신정지선은 TAGO 에 아예 없어서 이 보충이 꼭 필요하다.
                #   그래서 '같은 (노선, 역, 방향, 요일, 출발시각)에 TAGO 행이 이미 있고,
                #   이 행이 보태는 행선지가 없거나 같을 때'만 버린다.
                #   신도림 상행에는 본선 성수행과 지선 까치산행이 **같은 분에** 다른 승강장에서 출발하는
                #   경우가 6건 있다 — 행선지가 다르므로 이 규칙은 그건 남긴다.
                if rec["dep_time"]:
                    seen_dest = tago_slot.get(
                        (rec["line"], rec["station_nm"], rec["dir"], rec["day_type"], rec["dep_time"]))
                    if seen_dest is not None:
                        mine = base_nm(rec["dest_nm"])
                        if mine is None or mine in seen_dest:
                            cross_dup += 1
                            continue
                        # 같은 초에 출발하는데 행선지가 다르다. 둘 중 하나다 —
                        #  ① 본선과 지선이 다른 승강장에서 동시에 떠난다(신도림 상행 6건. 남겨야 한다)
                        #  ② 두 소스가 같은 열차의 행선지를 다르게 적었다
                        #     (병점 상행 11건: TAGO '제물포' vs 열린데이터광장 '청량리')
                        # 구분할 근거가 없어 남긴다. 편수가 그만큼 부풀 수 있으니 세어서 보여 준다.
                        cross_conflict.append((rec["line"], rec["station_nm"], rec["dir"], rec["day_type"],
                                               rec["dep_time"], sorted(x for x in seen_dest if x), mine))
                emit(g, rec, ("S", r["STATION_CD"], r.get("WEEK_TAG"), rec["dir"], r.get("TRAIN_NO"), r.get("LEFTTIME"), r.get("ARRIVETIME")))

# ── 커버리지 ──
seoul_lines = collections.defaultdict(set)
for s in stations:
    seoul_lines[s["LINE_NUM"]].add(s["STATION_NM"])
done = set(json.loads(TAGO_DONE.read_text(encoding="utf-8"))) if TAGO_DONE.exists() else set()
lines_out = ["# 통합 시간표 v1 커버리지", "",
             f"생성 {datetime.now(KST).isoformat(timespec='seconds')} · TAGO 수집일 {tago_date} · 열린데이터광장 보충 수집일 {seoul_date} · 행 {rows:,} (중복 제거 {dup:,})", "",
             "| 노선 | 역(열린데이터광장) | 평일 역 | 휴일 역 | 토요일 역 | 평일 빈 역 | 휴일 빈 역 |", "|---|---|---|---|---|---|---|"]
gaps = {}
for ln in sorted(seoul_lines):
    allst = seoul_lines[ln]; c = cover.get(ln, {})
    wd, hd, sa = c.get("weekday", set()), c.get("holiday", set()), c.get("saturday", set())
    gw, gh = sorted(allst - wd), sorted(allst - hd)
    gaps[ln] = {"weekday": gw, "holiday": gh}
    lines_out.append(f"| {ln} | {len(allst)} | {len(wd & allst)} | {len(hd & allst)} | {len(sa & allst)} | "
                     f"{', '.join(gw) if len(gw) <= 12 else f'{len(gw)}개'} | {', '.join(gh) if len(gh) <= 12 else f'{len(gh)}개'} |")
lines_out += ["", "## 읽는 법", "",
              "- 수도권 TAGO는 토요일(02)이 비어 있고 토·일·공휴일을 03(holiday)로 준다(우이신설만 02 별도). 판정기는 토요일을 holiday로 본다 [추정 — 서울교통공사 공식 시간표가 평일/토·공휴일 2종인 것과 일치].",
              "- 빈 역 = 열린데이터광장 역 목록에는 있는데 그 요일 시간표 행이 0개인 역. 원인은 셋 중 하나: ① TAGO 역명 미매칭(tago_fix_unmatched.py) ② 수집 실패(tago_retry_empty.py) ③ 소스에 없음(예: 경춘선 대부분, 6호선 응암순환 상행) → 판정에서 '근거 없음'.",
              f"- 미매칭 역명 {len(unmatched_names)}개: {', '.join(unmatched_names)}" if unmatched_names else "- 미매칭 역명 없음",
              f"- 수도권 밖 운영사 행 제외: {dict(excluded)} (부산·대구·대전·광주 동명 역이 '1호선' 같은 노선명으로 잡힘)",
              f"- 같은 시각·다른 행선지라 양쪽 다 남긴 행 {len(cross_conflict)}개 — 본선/지선 동시 출발(신도림 상행)과 소스 간 행선지 불일치(병점 상행)가 섞여 있다. meta 의 `cross_source_conflicts_kept` 참고.",
              f"- 소스 간 중복 제거 {cross_dup:,}행 — 지선 접속역 보충분 중 TAGO 가 이미 가진 열차(1호선 금천구청·병점·소요산). 같은 출발시각이어도 행선지가 다르면 남긴다(신도림 본선/지선 동시 출발 6건).",
              f"- 소스별 행: {dict(src_count)} · 열린데이터광장 파일: {[p.name for p in SEOUL_FILLS]}",
              f"- 노선명 매핑 실패(있으면 ROUTE2LINE 보강): {dict(unknown_route) or '없음'}",
              f"- 열린데이터광장 `SUBWAYSNAME` 은 행선지가 아니라 **시발역**이다(2026-09-11 확인). `orig_nm` 으로 담고, 진짜 행선지는 TRAIN_NO 로 열차를 복원해 종착 행에서 얻는다. 복원 {seoul_dest_stats['복원']:,}행 · 못함 {seoul_dest_stats['복원못함']:,}행(행선지 없음 → 판정에서 근거없음).",
              "- station_cd 가 null 인 행은 열린데이터광장 역 목록과 (노선, 역명)으로 못 이은 것. 링크 장소명·영문명은 station_cd 로 잇는다."]
COVER.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
meta = {"built_at": datetime.now(KST).isoformat(timespec="seconds"), "tago_fetched_at": tago_date, "seoul_fetched_at": seoul_date,
        "rows": rows, "duplicates_removed": dup, "cross_source_duplicates_removed": cross_dup, "tago_rows_dropped_for_richer_seoul": tago_thin,
        "cross_source_conflicts_kept": [{"line": c[0], "station_nm": c[1], "dir": c[2], "day_type": c[3],
                                         "dep_time": c[4], "tago_dest": c[5], "seoul_dest": c[6]}
                                        for c in cross_conflict], "sources": dict(src_count), "excluded_rows": dict(excluded),
        "unmatched_names": unmatched_names, "gaps": gaps,
        "seoul_dest_recovered_rows": seoul_dest_stats["복원"], "seoul_dest_unrecovered_rows": seoul_dest_stats["복원못함"], "tago_combos_done": len(done), "output": str(OUT)}
META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"소스 간 중복 제거(TAGO 가 이미 가진 열차): {cross_dup:,}행")
print(f"  반대로 버린 TAGO 행(같은 슬롯을 광장이 행선지까지 갖고 있음): {tago_thin:,}행")
if cross_conflict:
    _cc = collections.Counter((c[0], c[1], c[2]) for c in cross_conflict)
    print(f"  같은 시각·다른 행선지로 양쪽 다 남긴 행: {len(cross_conflict)}  {dict(_cc.most_common(5))}")
print(f"행 {rows:,} (중복 {dup:,}, 제외 {sum(excluded.values()):,}) → {OUT}")
print(f"커버리지 → {COVER}")
