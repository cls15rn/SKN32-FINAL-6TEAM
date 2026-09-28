# mobility_scripts/collect/congestion_build.py
# 서울교통공사_지하철혼잡도정보(data.go.kr 15071311) CSV → processed/mobility/congestion_v1.jsonl
#
# 실행:
#   python mobility_scripts/collect/congestion_build.py
#   python mobility_scripts/collect/congestion_build.py --csv "C:\...\서울교통공사_지하철혼잡도정보_20260630.csv"
#
# 왜 CSV 인가: 이 데이터셋은 파일데이터이고 오픈API 는 odcloud 자동변환이라 분기마다
#   엔드포인트 uddi UUID 가 통째로 바뀐다(연도·분기별로 16개가 따로 있다). 1,671행짜리를
#   페이징으로 긁을 이유가 없어서 §3-7 국가철도공단 XLSX 와 같은 "수동 1회 다운로드" 로 간다.
#
# 하는 일: cp949 CSV 읽기 → 역번호/역명으로 station_coords 와 매칭 → 지선·순환 branch 분리
#          → 0 값을 "혼잡도 0" 이 아니라 근거없음으로 구분 → long 포맷 jsonl + 리포트
import argparse, csv, collections, io, json, re
from datetime import datetime, timezone, timedelta
from pathlib import Path

from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
OUT = PROCESSED / "mobility" / "congestion_v1.jsonl"
REPORT = PROCESSED / "mobility" / "congestion_v1_report.md"
COORDS = PROCESSED / "mobility" / "station_coords.json"

META_COLS = ("구분", "호선", "역번호", "역명", "상하구분")
DAY = {"평일": "weekday", "토요일": "saturday", "일요일": "sunday"}
# [추정] 상행/내선 → U. build_timetable_v1.py 의 열린데이터광장 분기와 같은 정의를 쓴다.
DIR = {"상선": "U", "내선": "U", "하선": "D", "외선": "D"}
# 지선·순환은 역번호 9xxx 로 온다. 본선과 혼잡도가 다르므로 합치지 않고 branch 로 남긴다.
BRANCH_BY_CD = {
    "9001": "성수지선", "9002": "성수지선", "9003": "신정지선",
    "9005": "마천지선", "9006": "응암순환",
}

ap = argparse.ArgumentParser()
ap.add_argument("--csv", help="혼잡도 CSV 경로 (기본: raw/mobility 에서 자동 탐색)")
args = ap.parse_args()

if args.csv:
    src = Path(args.csv)
else:
    cands = sorted(RAW_MOBILITY.glob("서울교통공사_지하철혼잡도정보_*.csv"))
    if not cands:
        raise SystemExit(f"CSV 를 못 찾았다 → {RAW_MOBILITY} 에 두거나 --csv 로 지정한다")
    src = cands[-1]

m = re.search(r"(\d{8})", src.name)
basis = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else None
fetched_at = datetime.fromtimestamp(src.stat().st_mtime, KST).date().isoformat()
source_id = f"seoul_metro_congestion@{basis or fetched_at}"

text = src.read_bytes().decode("cp949")          # 공공데이터포털 CSV 는 cp949 다
rows = list(csv.DictReader(io.StringIO(text)))
TIME_COLS = [c for c in rows[0] if c not in META_COLS]


def slot_hhmm(col):
    """'5시30분' → '05:30' · '00시30분' → '24:30'(익일). 막차 판정과 자리를 맞춘다."""
    h, mm = re.match(r"(\d{1,2})시(\d{2})분", col).groups()
    h = int(h)
    if h < 4:                                     # 00시00분·00시30분 은 익일
        h += 24
    return f"{h:02d}:{mm}"


coords = json.load(COORDS.open(encoding="utf-8"))["stations"]
cd2keys = collections.defaultdict(list)
for k, v in coords.items():
    if v.get("station_cd"):
        cd2keys[v["station_cd"]].append(k)


def line_key(l):
    m = re.match(r"(\d)호선", l)
    return f"0{m.group(1)}호선" if m else l


def resolve(line, cd, nm):
    """역번호 우선, 실패하면 역명(괄호·지선 접미사 제거). 283/283 확인됨."""
    lk = line_key(line)
    for k in cd2keys.get(cd.zfill(4), []):
        if k.startswith(lk + "|"):
            return k, "code"
    base = re.sub(r"\(.*?\)$", "", nm).strip()
    for cand in (nm, base, re.sub(r"[ES]$", "", base)):
        if f"{lk}|{cand}" in coords:
            return f"{lk}|{cand}", "name"
    return None, None


def branch_of(cd, nm):
    if cd in BRANCH_BY_CD:
        return BRANCH_BY_CD[cd]
    m = re.search(r"\((.+?)\)$", nm)
    if m and m.group(1) in ("마천", "하남검단산"):     # 5호선 강동 분기
        return m.group(1) + "방면"
    return None


out_rows = 0
how = collections.Counter()
unresolved = []
grade_count = collections.Counter()
reason_count = collections.Counter()
per_line = collections.defaultdict(set)

OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("w", encoding="utf-8") as g:
    for r in rows:
        line, cd, nm, dr = r["호선"], r["역번호"], r["역명"], r["상하구분"]
        key, how_ = resolve(line, cd, nm)
        how[how_ or "미해결"] += 1
        if key is None:
            unresolved.append((line, cd, nm))
            continue
        st = coords[key]
        per_line[line_key(line)].add(st["station_nm"])

        vals = [float(r[c]) if r[c].strip() else None for c in TIME_COLS]
        # 0 은 "혼잡도 0%" 가 아니라 그 30분에 승차 데이터가 없다는 뜻이다. 원인을 구분한다.
        all_zero = all(v == 0 for v in vals)
        last_nonzero = max((i for i, v in enumerate(vals) if v), default=-1)

        for i, col in enumerate(TIME_COLS):
            v = vals[i]
            if v is None:
                grade, reason, value = "근거없음", "missing_cell", None
            elif v == 0:
                if all_zero:
                    # 종착역의 종착 방향 — 그 방향으로 더 갈 곳이 없다(방화 상선·오금 하선 등 41행)
                    reason = "terminus_direction"
                elif i > last_nonzero:
                    reason = "after_last_train"
                else:
                    reason = "no_train_in_window"
                grade, value = "근거없음", None
            else:
                grade, reason, value = "확정", None, v
            grade_count[grade] += 1
            if reason:
                reason_count[reason] += 1

            g.write(json.dumps({
                "station_key": key,
                "station_cd": st.get("station_cd"),
                "station_nm": st["station_nm"],
                "station_nm_en": st.get("station_nm_en"),
                "line": line_key(line),
                "branch": branch_of(cd, nm),
                "src_station_nm": nm,               # 원본 표기 보존 ('성수E'·'강동(마천)')
                "src_station_cd": cd,
                "dir": DIR.get(dr),                 # [추정] 상행/내선 → U
                "dir_raw": dr,
                "day_type": DAY.get(r["구분"], r["구분"]),
                "slot": slot_hhmm(col),
                "congestion": value,                # % · 100 = 정원
                "grade": grade,
                "reason": reason,
                "source": "seoul_metro_congestion",
                "source_id": source_id,
                "data_basis_date": basis,
                "fetched_at": fetched_at,
                "fetched_at_precision": "day",
            }, ensure_ascii=False) + "\n")
            out_rows += 1

lines_out = [
    "# 혼잡도 v1 (서울교통공사 지하철혼잡도정보)", "",
    f"생성 {datetime.now(KST).isoformat(timespec='seconds')} · 원본 `{src.name}` · 기준일 {basis} · 받은 날 {fetched_at}",
    f"소스 행 {len(rows):,} → 출력 {out_rows:,} (역·방향·요일 × 30분 슬롯 {len(TIME_COLS)}개)", "",
    "## 매칭", "",
    f"- (호선·역번호·역명) 조합 {len({(r['호선'], r['역번호'], r['역명']) for r in rows})}개 · 역번호 매칭 {how['code']:,}행 · 역명 매칭 {how['name']:,}행 · 미해결 {len(unresolved)}",
    "- 역번호는 앞자리 0 이 빠져 온다(`150` ↔ station_coords `0150`) → `zfill(4)`",
    "- 지선·순환은 역번호 9xxx 로 오고 station_coords 에 없다 → 역명으로 본선 역에 붙이고 `branch` 로 구분",
    "", "## 커버리지", "",
    "| 노선 | 혼잡도 역 수 |", "|---|---|",
]
for ln in sorted(per_line):
    lines_out.append(f"| {ln} | {len(per_line[ln])} |")
lines_out += [
    "",
    "9호선·공항철도·코레일 구간(1·3·4호선 연장·경의중앙·수인분당)·신분당은 이 소스에 없다 → 그 구간 혼잡도는 **근거 없음**.",
    "", "## 값 등급", "",
    f"- 확정 {grade_count['확정']:,}셀 · 근거없음 {grade_count['근거없음']:,}셀",
    "- 0 을 혼잡도 0% 로 쓰지 않는다. 원인별: " + ", ".join(f"`{k}` {v:,}" for k, v in reason_count.most_common()),
    "  - `terminus_direction` 종착역의 종착 방향(방화 상선·오금 하선·마천 하선 등) — 그 방향 승차가 구조적으로 없다",
    "  - `after_last_train` 막차 이후 슬롯(00:00·00:30 에 몰려 있다)",
    "  - `no_train_in_window` 심야에 그 30분 안에 열차가 없던 경우",
    "", "## 주의", "",
    "- 값은 **30분 평균 · 역 단위**다. 그 안의 첨두는 더 높고, 구간 통과(재차) 인원이 아니라 그 역 기준이다.",
    "- 요일 구분이 평일/토요일/일요일 **3종**이다. 시간표는 평일/휴일 2종이라 자리가 다르다 → 공휴일은 일요일 값으로 본다 [추정].",
    "- `dir` U/D 는 상행·내선 → U 로 둔 [추정]이다. 시간표와 붙일 때 실제 방향이 맞는지 합성 여행으로 확인한다.",
]
REPORT.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
print(f"{out_rows:,}행 → {OUT}")
print(f"리포트 → {REPORT}")
if unresolved:
    print("미해결:", unresolved)
