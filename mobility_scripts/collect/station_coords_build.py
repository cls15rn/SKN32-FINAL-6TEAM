# mobility_scripts/collect/station_coords_build.py — 역 좌표표 (국가철도공단 표준데이터 → 우리 station_key)
# 실행: 저장소 루트에서  python mobility_scripts/collect/station_coords_build.py [--src <파일경로>]
# 선행(수동 1회): 공공데이터포털 "전국도시철도역사정보표준데이터"(15013205) XLSX 를 raw/mobility/ 에 저장.
#   오픈API 없음·연 1회 갱신·저장 자유. 카카오 좌표는 저장 금지라 링크 조립용 좌표는 이 소스여야 한다.
#
# ★매핑은 노선명이 아니라 **역명**으로 한다.
#   표준데이터는 코레일 구간을 운영 노선이 아니라 철도 노선명으로 부른다 —
#   '1호선'은 서울교통공사 구간 10개역뿐이고 나머지는 경부선·경인선·경원선·안산과천선·일산선으로 흩어진다.
#   '수인선'+'분당선'이 수인분당선, '인천국제공항선'이 공항철도다. 노선명으로 이으면 절반이 깨진다(9/9 확인: 322/799).
#   환승역은 물리적으로 같은 위치이므로 역명이 같으면 노선이 달라도 같은 좌표를 쓴다 —
#   링크 조립과 도보 거리 개략에는 충분하다(출구별 차이는 [추정] 범위 안).
#   대신 동명 역(부산 서면·대구 중앙로 …)을 막기 위해 **수도권 운영기관·노선만** 남긴다.
#
# ★출처가 둘이다 (2026-09-13 명시). 좌표만 표준데이터에서 오고, **역명(한글·영문)은 열린데이터광장
#   OA-15442(stations_all.json)** 에서 온다. 필드 이름이 양쪽 다 STATION_NM_ENG 라 한 소스처럼 보였고,
#   레코드의 source 가 kric_station_standard 하나여서 영문명 결함을 표준데이터 탓으로 오인했다.
#   이제 레코드에 coord_source / name_source 를 따로 적는다.
#
# ★영문명 보정 (2026-09-13). 원본 영문명에 결함이 18역 있었다 — 대소문자 9·붙어쓰기 6·이중공백 3·
#   부역명 1(중복 포함), 그리고 규칙으로 못 잡는 4건(Lrt·Sutgogae·Inchon·Station 접미).
#   config/mobility/station_nm_en_fix.json 으로 보정하고 원본은 station_nm_en_src 에 남긴다.
#   2026-09-13 추가: 약어 정책(Int'l 통일)과 역 단위 일관성(같은 한글 역명은 같은 영문명).
#   보정한 역은 station_nm_en_grade='추정'. 검사는 mobility_scripts/check_station_names.py.
#
# ★조인 키 = 노선+역명 (2026-09-27 · 57번 방). 역명만으로 붙이면 동명이역이 섞였다 —
#   경의선|양평 에 5호선 양평(영등포구 · 53.6 km) 좌표, 경의선|신촌 에 2호선 신촌(702 m) 좌표가 들어갔다.
#   이제 우리 노선(LINE_NUM) → 원자료 노선명 집합(LINE_SRC)으로 **같은 노선 행 안에서** 역명을 찾고,
#   그 노선 행이 없을 때만 예전 역명 전체 조인으로 떨어진다(coord_join 에 어느 쪽인지 남긴다).
#   위 「노선명으로 이으면 절반이 깨진다」는 우리 노선명 = 원자료 노선명 으로 이을 때 얘기다 — 여기선 표로 옮겨 잇는다.
# ★좌표 보정 (2026-09-21 · 34번 방). 원본 표준데이터 행 자체가 옆 역 좌표를 든 경우가 있다 —
#   마곡(5호선) 행 = 발산 좌표(7 m) · 이촌(4호선) 행 = 신용산 좌표(14 m). 우리 처리 탓이 아니다.
#   config/mobility/station_coord_fix.json 으로 덮고 원본은 coord_src_lat/lng 에 남긴다(coord_source=station_coord_fix).
#   빌드 때 원본 좌표가 옆 역과 ADJ_WARN_M 넘게 떨어져 있으면 '원본이 고쳐진 듯' 을 찍는다 — 그때 보정표 항목을 지운다.
#   결과 파일 쪽 검사(다른 역명끼리 ADJ_WARN_M 안)는 consistency_check C8.
import json, math, re, argparse, collections, unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
OUT_DIR = PROCESSED / "mobility"
OUT = OUT_DIR / "station_coords.json"
REPORT = OUT_DIR / "station_coords_report.md"
STATIONS = RAW_MOBILITY / "stations_all.json"
SOURCE = "kric_station_standard"          # 좌표 출처
NAME_SOURCE = "seoul_opendata_OA-15442"   # 역명(한글·영문) 출처 — 좌표와 다르다
FIXES = Path(__file__).resolve().parents[2] / "config" / "mobility" / "station_nm_en_fix.json"
COORD_FIXES = Path(__file__).resolve().parents[2] / "config" / "mobility" / "station_coord_fix.json"
# 57번(GPT 3): 역명 폴백은 보정표 「역명폴백허용」 키만 — 노선 안에서 못 찾은 이유가 행 누락·이름 불일치여도 동명이역을 집지 않게
_FALLBACK_OK = set(json.loads(COORD_FIXES.read_text(encoding="utf-8")).get("역명폴백허용", {})) if COORD_FIXES.exists() else set()
ADJ_WARN_M = 50        # 다른 역명끼리 이 안이면 원본 좌표 오류 의심 — 9/21 전수: 정상 최근접쌍은 200 m 밖

_fx = json.loads(FIXES.read_text(encoding="utf-8")) if FIXES.exists() else {"치환": {"규칙": []}, "역별": {}}
# 규칙마다 등급이 다르다 — 오탈자 교정은 확정 유지, 약어 통일은 우리가 고른 것이라 추정으로 내린다
_SUBS = [(r["from"], r["to"], r.get("grade", "확정")) for r in _fx.get("치환", {}).get("규칙", [])]
_BY_ST = {k: v["to"] for k, v in _fx.get("역별", {}).items() if isinstance(v, dict) and "to" in v}


def fix_en(key, en):
    """영문명 보정 → (값, 등급, 사유). 원본은 호출한 쪽에서 station_nm_en_src 로 남긴다.

    역별 표가 우선이다. 없으면 기계적 문자 치환(백틱·굽은따옴표·이중공백)만 건다 —
    치환은 의미가 안 바뀌므로 확정을 유지하고, 역별 보정은 판단이 들어가므로 추정으로 내린다.
    """
    if not en:
        return None, "근거없음", "원본 없음"
    if key in _BY_ST:
        return _BY_ST[key], "추정", "역별 보정표"
    out, grade, kinds = en, "확정", []
    for a, b, g in _SUBS:
        if a not in out:
            continue
        while a in out:
            out = out.replace(a, b)
        kinds.append("약어 통일" if g == "추정" else "문자 치환")
        if g == "추정":
            grade = "추정"
    if out != en:
        return out, grade, " + ".join(dict.fromkeys(kinds))
    return en, "확정", None


def meters(lat1, lng1, lat2, lng2):
    dy = (lat2 - lat1) * 111_320
    dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def nearest_other(coords, key, lat, lng):
    """다른 역명 중 가장 가까운 것 → (거리 m, 역명). 환승역(같은 역명)은 뺀다."""
    nm = coords[key]["station_nm"]
    best = (float("inf"), None)
    for k, v in coords.items():
        if v["station_nm"] == nm:
            continue
        d = meters(lat, lng, v["lat"], v["lng"])
        if d < best[0]:
            best = (d, v["station_nm"])
    return best


def adjacent_pairs(coords, limit=ADJ_WARN_M):
    """다른 역명끼리 limit m 안인 쌍 — 역명 단위(환승역은 한 점)."""
    by_nm = {}
    for v in coords.values():
        by_nm.setdefault(v["station_nm"], (v["lat"], v["lng"]))
    nms = sorted(by_nm)
    out = []
    for i, a in enumerate(nms):
        for b in nms[i + 1:]:
            d = meters(*by_nm[a], *by_nm[b])
            if d <= limit:
                out.append((round(d), a, b))
    return sorted(out)


# 수도권만 남긴다 ── 한국철도공사는 전국이라 노선명으로 한 번 더 거른다
KEEP_OPERATOR = re.compile(r"서울교통공사|한국철도공사|코레일|공항철도|인천교통공사|신분당|의정부|용인|김포|"
                           r"우이신설|메트로9호선|경기철도|새서울|남서울|서부광역|국가철도공단|에스알|SR|"
                           r"남양주도시공사|구리도시공사|하남도시공사|성남도시개발")   # 별내선·진접선 등 지자체 운영
DROP_OPERATOR = re.compile(r"부산|대구|대전|광주|김해")
KORAIL_SEOUL = {"경부선", "경인선", "경원선", "경의중앙선", "경춘선", "경강선", "분당선", "수인선",
                "안산과천선", "일산선", "장항선", "진접선", "서해선", "중앙선"}
KORAIL_DROP = {"동해선", "대경선"}          # 부산·대구권


# 57번: 우리 노선 → 원자료 노선명(표준데이터 '노선명'). 코레일 구간은 철도 노선명으로 흩어져 있다.
LINE_SRC = {
    "01호선": {"1호선", "경부선", "경인선", "경원선", "장항선"},
    "02호선": {"2호선"}, "03호선": {"3호선", "일산선"}, "04호선": {"4호선", "안산과천선", "진접선"},
    "05호선": {"5호선"}, "06호선": {"6호선"}, "07호선": {"7호선", "도시철도 7호선"},
    "08호선": {"8호선", "수도권 광역철도 8호선"},
    "09호선": {"서울 도시철도 9호선", "수도권 도시철도 9호선"},
    "수인분당선": {"분당선", "수인선"}, "경의선": {"경의중앙선", "경원선", "중앙선"},
    "경춘선": {"경춘선"}, "경강선": {"경강선"}, "서해선": {"서해선"}, "공항철도": {"인천국제공항선"},
    "신분당선": {"신분당선"}, "용인경전철": {"에버라인"}, "의정부경전철": {"의정부"},
    "우이신설경전철": {"우이신설선"}, "신림선": {"수도권 경량도시철도 신림선"}, "김포도시철도": {"김포도시철도"},
    "인천선": {"인천지하철 1호선"}, "인천2호선": {"인천지하철 2호선"},
}
_SRC2OURS = collections.defaultdict(set)
for _our, _srcs in LINE_SRC.items():
    for _s in _srcs:
        _SRC2OURS[_s].add(_our)


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"[\s·.\-()（）]", "", s)


def base_name(s):                            # "이촌(국립중앙박물관)" → "이촌"
    return re.sub(r"\(.*?\)", "", str(s or "")).strip()


def keys_of(nm):
    """매칭 후보 키들.

    ★코레일은 역명 끝에 '역'을 붙이고("용산역", "수원역(분당)") 서울교통공사는 붙이지 않는다("제기동").
      '서울역'처럼 '역'이 이름의 일부인 경우도 있어 무조건 떼면 안 된다 — 양쪽을 다 후보로 둔다.
      지하철 시간표 매칭에서 겪은 것과 같은 함정이다(소싱 문서 3-1 표).
    """
    base = base_name(nm)
    # ★순서가 있는 목록이다 (2026-09-21 · 34번). 예전엔 set 이라 `next(...)` 가 PYTHONHASHSEED 에 따라
    #   다른 키를 먼저 집었다 — '서울역' 4키가 실행마다 1호선 행(0133)과 공항철도 행(A01, 377 m)을 오갔다.
    out = [norm(nm), norm(base), norm(re.sub(r"역$", "", base)), norm(base + "역")]
    return [k for k in dict.fromkeys(out) if k]


def find_src():
    for p in sorted(RAW_MOBILITY.glob("*")):
        if p.suffix.lower() in (".xlsx", ".xls", ".csv") and any(
                k in p.name for k in ("역사", "station", "KRIC", "kric", "도시철도")):
            return p
    return None


def load_rows(path):
    if path.suffix.lower() == ".csv":
        import csv
        for enc in ("utf-8-sig", "cp949"):
            try:
                with path.open(encoding=enc, newline="") as f:
                    return list(csv.DictReader(f))
            except UnicodeDecodeError:
                continue
        raise SystemExit("CSV 인코딩을 읽지 못했다 (utf-8/cp949 시도)")
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise SystemExit("pip install openpyxl 또는 CSV 로 저장해서 다시 실행")
    ws = load_workbook(path, read_only=True, data_only=True).active
    it = ws.iter_rows(values_only=True)
    header = [str(c or "").strip() for c in next(it)]
    return [dict(zip(header, r)) for r in it]


def pick(row, *keys):
    for k in row:
        kk = norm(k)
        for want in keys:
            if norm(want) in kk:
                return row[k]
    return None


ap = argparse.ArgumentParser()
ap.add_argument("--src", help="표준데이터 파일 경로 (미지정 시 raw/mobility 에서 자동 탐색)")
args = ap.parse_args()
src = Path(args.src) if args.src else find_src()
if not src or not src.exists():
    raise SystemExit(f"표준데이터 파일이 없다. data.go.kr 15013205 에서 받아 {RAW_MOBILITY} 에 두고 다시 실행.")
raw = load_rows(src)
print(f"표준데이터 {len(raw)}행 ← {src.name}")

# ── 표준데이터 → 역명별 좌표 ─────────────────────────────────────
coords_by_name, basis, dropped = {}, set(), collections.Counter()
exact_by_line, by_line_name = {}, {}   # 57번: (우리 노선, 역명 키) → 원자료 행
dup_by_line = {}                       # 57번(GPT 4): (노선, 역명) → 모든 행
exact_by_name = {}   # 34번: '서울역' 이 공항철도 '서울' 행의 파생 키('서울'+'역')에 먼저 잡히지 않도록 원본 이름 그대로인 키를 먼저 본다
for row in raw:
    nm, line = pick(row, "역사명", "역명"), str(pick(row, "노선명") or "")
    lat, lng = pick(row, "역위도", "위도"), pick(row, "역경도", "경도")
    op = str(pick(row, "운영기관명", "운영기관") or "")
    d = pick(row, "데이터기준일자", "기준일자")
    if d:
        basis.add(str(d)[:10])
    if not (nm and lat and lng):
        dropped["좌표없음"] += 1; continue
    if line in KORAIL_DROP or DROP_OPERATOR.search(op) or not KEEP_OPERATOR.search(op):
        dropped["수도권밖"] += 1; continue
    if "철도공사" in op and line not in KORAIL_SEOUL:
        dropped[f"코레일 수도권밖:{line}"] += 1; continue
    rec = {"lat": float(lat), "lng": float(lng), "src_name": str(nm), "operator": op, "src_line": line}
    ks = keys_of(nm)
    exs = [k for k in dict.fromkeys([norm(nm), norm(base_name(nm))]) if k]   # 원본 역명 그대로(괄호 포함·제외) — '역' 붙이기/떼기 없음
    for k in exs:
        exact_by_name.setdefault(k, rec)
    for k in ks:
        coords_by_name.setdefault(k, rec)
    for our in _SRC2OURS.get(re.sub(r"\s+", " ", line).strip(), ()):      # 57번: 노선+역명 키
        for k in exs:
            exact_by_line.setdefault((our, k), rec)
            dup_by_line.setdefault((our, k), []).append(rec)              # 57번(GPT 4): 같은 노선 안 중복 행 — 첫 행(파일 순서)을 쓰고 기록
        for k in ks:
            by_line_name.setdefault((our, k), rec)
print(f"수도권 역명 키 {len(coords_by_name)}개 (제외 {sum(dropped.values())}행)")

# ── 우리 역 목록에 붙이기 ───────────────────────────────────────
stations = json.loads(STATIONS.read_text(encoding="utf-8"))
fetched = sorted(basis)[-1] if basis else None
coords, missing = {}, []
fixed = collections.Counter()
joins = collections.Counter()   # 57번
blocked = []                    # 57번: 폴백이 동명이역이라 막은 키 (key, 역명 행 좌표 최대 간격 m)
for s in stations:
    key = f"{s['LINE_NUM']}|{s['STATION_NM']}"
    _ks = keys_of(s["STATION_NM"])
    _ex = [k for k in dict.fromkeys([norm(s["STATION_NM"]), norm(base_name(s["STATION_NM"]))]) if k]
    ln = s["LINE_NUM"]
    hit = next((exact_by_line[(ln, k)] for k in _ex if (ln, k) in exact_by_line), None) \
        or next((by_line_name[(ln, k)] for k in _ks if (ln, k) in by_line_name), None)
    join = "노선+역명"
    if not hit:   # 그 노선 안에서 역명을 못 찾았다 — 역명 전체 조인으로 떨어지되, 동명이역이면 막는다(57번 · GPT 3)
        why = "노선 행 없음" if ln not in LINE_SRC else "노선 안 역명 불일치"
        hit = next((exact_by_name[k] for k in _ex if k in exact_by_name), None) \
            or next((coords_by_name[k] for k in _ks if k in coords_by_name), None)
        if hit and key not in _FALLBACK_OK:   # 허용 목록 밖 — 같은 역명 다른 역(동명이역) 좌표일 수 있어 안 붙인다
            blocked.append((key, hit["src_line"])); missing.append(key); continue
        join = f"역명({why})"
    if not hit:
        missing.append(key); continue
    joins[join] += 1
    en_src = s.get("STATION_NM_ENG")
    en, en_grade, en_why = fix_en(key, en_src)
    if en_why:
        fixed[en_why] += 1
    coords[key] = {"station_key": key, "line": s["LINE_NUM"], "station_nm": s["STATION_NM"],
                   "station_nm_en": en, "station_nm_en_src": en_src,
                   "station_nm_en_grade": en_grade, "station_nm_en_fix": en_why,
                   "station_cd": s["STATION_CD"],
                   "lat": hit["lat"], "lng": hit["lng"], "operator": hit["operator"],
                   "src_name": hit["src_name"], "src_line": hit["src_line"],
                   "source": SOURCE, "coord_source": SOURCE, "coord_join": join, "name_source": NAME_SOURCE,
                   "fetched_at": fetched, "fetched_at_precision": "day"}

# ── 좌표 보정 (34번 방) ─────────────────────────────────────────
_cfx = json.loads(COORD_FIXES.read_text(encoding="utf-8")).get("역별", {}) if COORD_FIXES.exists() else {}
coord_fixed, coord_fix_stale, coord_fix_missing = [], [], []
adj_before = adjacent_pairs(coords)
for key, fx in _cfx.items():
    if key not in coords:
        coord_fix_missing.append(key); continue
    r = coords[key]
    d_src, nb = nearest_other(coords, key, r["lat"], r["lng"])
    if fx.get("kind", "원본오류") == "원본오류" and d_src > ADJ_WARN_M:
        coord_fix_stale.append((key, round(d_src), nb))
    moved = meters(r["lat"], r["lng"], fx["lat"], fx["lng"])
    r.update({"coord_src_lat": r["lat"], "coord_src_lng": r["lng"],
              "lat": fx["lat"], "lng": fx["lng"],
              "coord_source": "station_coord_fix", "coord_grade": fx.get("grade", "추정"),
              "coord_fix": fx.get("why"), "coord_fix_src": fx.get("source"),
              "coord_fix_checked_at": fx.get("checked_at")})
    coord_fixed.append((key, round(d_src), nb, round(moved), f'{fx.get("kind", "원본오류")}·{fx.get("grade")}'))
adj_after = adjacent_pairs(coords)
for key, d, nb, mv, g in coord_fixed:
    print(f"좌표 보정 {key}: 매핑된 원본 행이 {nb} 와 {d} m → {mv} m 옮김 [{g}]")
for key, d, nb in coord_fix_stale:
    print(f"  ! {key}: 원본 좌표가 옆 역({nb})과 {d} m — 원본이 고쳐진 듯. 보정표 항목 삭제 검토")
for key in coord_fix_missing:
    print(f"  ! 보정표 키 {key} 가 역 목록에 없다")
print(f"인접역 {ADJ_WARN_M} m 안(다른 역명): 보정 전 {len(adj_before)}쌍 → 후 {len(adj_after)}쌍"
      + (f" — {adj_after}" if adj_after else ""))

OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({"source": SOURCE, "src_file": src.name, "data_basis_date": fetched,
                           "built_at": datetime.now(KST).isoformat(timespec="seconds"),
                           "count": len(coords), "stations": coords}, ensure_ascii=False, indent=1), encoding="utf-8")

by_line = collections.Counter(k.split("|")[0] for k in missing)
lines = ["# 역 좌표표 커버리지", "",
         f"소스 {SOURCE} ({src.name}, 기준일자 {fetched}) · 생성 {datetime.now(KST).isoformat(timespec='seconds')}",
         f"좌표 확보 **{len(coords)} / {len(stations)}** · 미확보 {len(missing)}", "",
         "| 노선 | 좌표 없는 역 |", "|---|---|"]
lines += [f"| {l} | {c}개 — {', '.join(n.split('|')[1] for n in missing if n.startswith(l))[:120]} |"
          for l, c in sorted(by_line.items())]
lines += ["", "## 읽는 법", "",
          "- 매핑 키는 **노선+역명**이다(57번 · 2026-09-27). 표준데이터가 코레일 구간을 철도 노선명(경부선·경인선·안산과천선 …)으로 불러, 우리 노선 → 원자료 노선명 표(`LINE_SRC`)로 옮겨 같은 노선 행 안에서 역명을 찾는다.",
          f"- 조인 결과: {dict(joins)} — `coord_join` 필드. 「역명(노선 행 없음)」은 원자료에 그 노선 행이 없어 예전처럼 역명 전체에서 찾은 것이다.",
          f"- 역명 폴백은 보정표 「역명폴백허용」 {len(_FALLBACK_OK)}키만 — 막은 키(좌표 없음으로 둠): " + (", ".join(f"{k}(역명으로는 {sl} 행)" for k, sl in blocked) or "없음"),
          "- 같은 노선 안 같은 역명 행이 둘 이상이면 **파일 순서 첫 행**을 쓴다 — 좌표가 다른 것: " + (", ".join(
              f"{o}|{k}({len(v)}행·{round(max(meters(a['lat'], a['lng'], b['lat'], b['lng']) for a in v for b in v))} m)"
              for (o, k), v in sorted(dup_by_line.items())
              if len({(r['lat'], r['lng']) for r in v}) > 1) or "없음"),
          "- 예전(역명만)엔 동명이역이 섞였다 — 경의선 양평·신촌이 5호선·2호선 좌표를 들었다. 환승역은 노선마다 제 행 좌표를 쓴다(수백 m 차이 가능).",
          "- 동명 역(부산 서면·대구 중앙로 …)은 운영기관과 코레일 노선 화이트리스트로 걸렀다.",
          "- **코레일은 역명 끝에 '역'을 붙이고**('용산역', '수원역(분당)') 서울교통공사는 붙이지 않는다('제기동'). '서울역'처럼 '역'이 이름의 일부인 경우도 있어 양쪽을 다 후보 키로 둔다.",
          f"- 제외 내역: {dict(dropped)}",
          "- 좌표가 없는 역은 카카오맵 링크를 장소명만으로 조립하고, 그 구간의 도보 거리 판정은 '근거 없음'으로 둔다.",
          "", "## 출처가 둘이다", "",
          f"- 좌표 `coord_source` = {SOURCE} (표준데이터 XLSX)",
          f"- 역명(한글·영문) `name_source` = {NAME_SOURCE} (stations_all.json)",
          "- 필드 이름이 양쪽 다 `STATION_NM_ENG` 라 한 소스로 오인했다. 레코드의 `source` 는 좌표 출처다.",
          "", "## 영문명 보정", "",
          f"- 보정 {sum(v for k, v in fixed.items() if k)}역 — " + (", ".join(f"{k} {v}역" for k, v in fixed.most_common() if k) or "없음"),
          "- 원본은 `station_nm_en_src` 에 남는다. 등급: 역별 보정·약어 통일 = `추정`, 문자 치환(오탈자) = 확정 유지.",
          "- 표: `config/mobility/station_nm_en_fix.json` · 검사: `python mobility_scripts/check_station_names.py`",
          "", "## 좌표 보정 (원본 행 오류)", "",
          f"- 보정 {len(coord_fixed)}키 — " + (" · ".join(f"{k}(원본이 {nb} 와 {d} m · {mv} m 이동 · {g})" for k, d, nb, mv, g in coord_fixed) or "없음"),
          f"- 인접역 {ADJ_WARN_M} m 안(다른 역명): 보정 전 {len(adj_before)}쌍 → 후 {len(adj_after)}쌍"
          + (" — " + ", ".join(f"{a}/{b} {d} m" for d, a, b in adj_after) if adj_after else ""),
          ("- **원본이 고쳐진 듯한 항목**: " + ", ".join(f"{k}({nb} {d} m)" for k, d, nb in coord_fix_stale)) if coord_fix_stale else "- 원본 오류 그대로(보정표 유효)",
          "- 원본은 `coord_src_lat/lng`, 표는 `config/mobility/station_coord_fix.json`, 결과 검사는 `consistency_check` C8."]
REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"좌표 {len(coords)}/{len(stations)}역 → {OUT}")
print(f"미확보 {len(missing)}역 · 리포트 → {REPORT}")
print(f"조인 {dict(joins)} · 폴백 막음 {blocked or '없음'}")
