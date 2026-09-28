# mobility_scripts/collect/build_line_station_order_v1.py — 노선별 역 순서 표
# 실행: 저장소 루트에서  python mobility_scripts/collect/build_line_station_order_v1.py
# 출력: processed/mobility/line_station_order_v1.json
#       processed/mobility/line_station_order_v1_report.md
# API 호출 없음. stations_all.json + timetable_v1.jsonl 만 읽는다.
#
# ── 왜 필요한가 ──────────────────────────────────────────────────────────
# 02번 방 막차 판정이 여기 막혀 있었다. consistency_check.py 의 C5 에서
# 막차 단축운행이 광범위하다는 것이 나왔다(2호선 막차 113 조합이 성수행,
# 5호선 46 조합이 애오개행). "그 시각에 열차가 있는가"만 보면 성립인데
# 목적지 전에 내려준다. 그래서 "이 열차(dest_nm)가 내 목적지를 지나는가"를
# 판정해야 하는데, 역 순서 데이터가 없었다.
#
# ── 어떻게 만드는가 ──────────────────────────────────────────────────────
# 두 소스를 교차한다. 한 소스로는 등급을 확정으로 못 올린다.
#
#  ① 구조(정본) — 열린데이터광장 역사마스터의 FR_CODE = 공식 역번호.
#     노선 순서 그 자체다(3호선 309 대화 ~ 352 오금). 지선은 하이픈 서브코드
#     (2호선 211-1 용답 = 성수지선) 또는 별도 접두어(1호선 P142~ = 경부선,
#     5호선 P549~ = 마천지선)로 갈린다.
#  ② 관측(검증) — timetable_v1.jsonl 의 출발시각. 인접이라면 A 를 떠난 열차가
#     몇 분 뒤 B 를 떠난다. 두 역 출발시각 차의 히스토그램에서 봉우리가 서면
#     확인되고, 봉우리 위치가 소요시간, 부호가 방향이다.
#
#  ①만이면 '추정'. ②가 양방향에서 반대 부호로 확인해 주면 '확정'.
#  시간표 표본이 없으면 '근거없음'으로 남긴다 — 채우지 않는다.
#
# ── 안 한 것과 그 이유 ───────────────────────────────────────────────────
# * 인수인계 문서의 원래 안(첫차 출발시각 정렬)은 실제로 돌려 보니 틀린다.
#   열차가 여러 차량기지에서 따로 출발해서 min(출발시각)이 같은 열차의 것이
#   아니다. 3호선 하행에서 대곡 다음이 구파발로 튀고(화정·원당·원흥·삼송·지축이
#   통째로 밀림) 압구정이 9번째로 올라온다.
# * 전역 기준역 정렬도 2호선 순환선에서 깨진다. 한 바퀴가 약 84분이라 정렬이
#   주기만큼 어긋나 왕십리가 -80 분으로 잡힌다. 그래서 '인접쌍의 국소 시차'만
#   본다 — 순환선에서도 옆 역과의 2분은 흔들리지 않는다.
# * 봉우리 '크기'로는 판별이 안 된다. 출퇴근 배차가 3분이면 어떤 δ 를 잡아도
#   일치가 쏟아진다. 판별력은 '봉우리에 걸린 A 열차의 비율(frac)'에서 나온다.
#   인접이면 A 를 떠난 열차 전부가 같은 δ 뒤에 B 를 떠나므로 1.0 이 된다.
#   2호선 시청→사당(비인접)은 0.72, 시청→을지로입구는 1.00 으로 갈린다.
import json, re, csv, io, collections, bisect, argparse, statistics
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
OUT_DIR = PROCESSED / "mobility"
STATIONS = RAW_MOBILITY / "stations_all.json"
TIMETABLE = OUT_DIR / "timetable_v1.jsonl"
# 서울교통공사 역간거리 및 소요시간(공개자료). 1~8호선 구간의 공식 소요시간 = 세 번째 소스다.
OFFICIAL = RAW_MOBILITY / "서울교통공사 역간거리 및 소요시간_240810.csv"
TT_META = OUT_DIR / "timetable_v1_meta.json"
OUT = OUT_DIR / "line_station_order_v1.json"
REPORT = OUT_DIR / "line_station_order_v1_report.md"

# ── FR_CODE 로 못 잇는 연결. 노선이 갈라지거나 순환으로 닫히는 자리다. ──
# 전부 관측(②)으로 확인한다. 확인 못 하면 grade 가 추정으로 남는다.
EXTRA_EDGES = [
    ("02호선", "충정로", "시청",           "순환선 닫힘"),
    ("06호선", "새절",   "응암",           "응암순환 — 새절↔응암 본선"),
    ("06호선", "구산",   "응암",           "응암순환 — 구산 다음이 응암이다(CUT_EDGES 주석 참고)"),
    ("01호선", "구로",   "가산디지털단지", "경인선(141) ↔ 경부선(P142) 분기"),
    ("05호선", "강동",   "둔촌동",         "본선(548) ↔ 마천지선(P549) 분기"),
    ("경의선", "용산",   "효창공원앞",     "경원(K110) ↔ 경의(K826) 접합"),
    ("경의선", "효창공원앞", "공덕",       "경의(K826) ↔ 문산방면(K312) 접합"),
    ("경의선", "가좌",   "신촌",           "본선(K315) ↔ 서울역지선(P312) 분기"),
]
# FR_CODE 로 만들어지지만 실제 선로가 아닌 간선. 근거를 적고 끊는다.
CUT_EDGES = [
    ("06호선", "구산", "새절",
     "응암순환은 구산 다음이 응암이다. 공식 역간거리표가 구산→응암 2:00/1.5km · 응암→새절 1:20/0.9km 로 주고, "
     "시간표에서도 한 열차가 구산 05:47:30 → 응암 05:50:00 → 새절 05:51:40 으로 이어진다. "
     "FR 번호(615 구산 · 616 새절)만 보면 인접해 보이지만 그 사이에 응암(610)이 들어간다."),
]

# 공항철도 FR_CODE 는 A01..A11 사이에 A042(마곡나루)·A071(청라국제도시)·A072(영종)가 끼어 있다.
# 세 자리는 '앞 두 자리 + 서브'로 읽는다. 숫자로 통째로 읽으면 끝으로 밀린다.
THREE_DIGIT_AS_SUB = {"공항철도"}
# 위 세 역은 가지가 아니라 본선 사이에 끼어든 역이다(마곡나루는 디지털미디어시티와
# 김포공항 사이). 서브코드를 가지로 다루는 기본 규칙을 쓰면 지선이 되고
# 디지털미디어시티–김포공항 직결이 생겨 버린다. 이 노선만 순서대로 쭉 잇는다.
INLINE_SUB = {"공항철도"}

# 시·종착역의 '출발 없음'을 소스가 '000000' 으로 준다. build_timetable_v1.py 의
# 자정 넘김 정규화가 이걸 24:00:00 으로 바꿔 버린다(아래 리포트의 경고 참고).
# 관측에서는 빼야 한다 — 안 빼면 성수·까치산·온수·석남 같은 종착역이
# '자정에 다 같이 출발'한 것으로 잡혀 시차가 통째로 어긋난다.
PHANTOM_DEP = "24:00:00"

WIN_S = 15 * 60   # 시차 탐색 폭(초). 인접 역 사이는 1~8분이다.
BIN_S = 60        # 히스토그램 칸(초). ±1칸을 한 봉우리로 본다.
MAX_DELTA = 15    # 인접으로 인정할 시차 상한(분)
MIN_TRAINS = 20   # A 역 편수가 이보다 적으면 관측으로 안 친다
MIN_FRAC = 0.85   # A 를 떠난 열차 중 δ 뒤에 B 를 떠난 것의 비율
MIN_SEP_DIR = 1.25  # 행선지로 못 가르고 방향 전체로 볼 때만 요구하는 배경 분리
FR_GAP_MAX = 3    # 같은 접두어라도 번호가 이보다 벌어지면 다른 계통으로 본다


# ────────────────────────────── 구조 ──────────────────────────────
def parse_fr(line_num, fr):
    """FR_CODE → 정렬키 (접두어, 기본번호, 서브번호). '211-1' → ('', 211, 1)"""
    m = re.match(r"^([A-Za-z]*)(\d+)(?:-(\d+))?$", (fr or "").strip())
    if not m:
        return None
    pre, num, sub = m.group(1), m.group(2), m.group(3)
    if sub is None and line_num in THREE_DIGIT_AS_SUB and len(num) == 3:
        return (pre, int(num[:2]), int(num[2:]))
    return (pre, int(num), int(sub or 0))


def load_stations():
    st = json.loads(STATIONS.read_text(encoding="utf-8"))
    by_line = collections.defaultdict(list)
    for x in st:
        k = parse_fr(x["LINE_NUM"], x["FR_CODE"])
        if k is None:
            raise SystemExit(f"FR_CODE 해석 실패: {x['LINE_NUM']} {x['FR_CODE']} {x['STATION_NM']}")
        by_line[x["LINE_NUM"]].append((k, x))
    for ln in by_line:
        by_line[ln].sort(key=lambda z: z[0])
    return by_line


def fr_edges(line_num, seq):
    """FR 정렬에서 인접쌍을 만든다.

    서브코드는 기본코드에 매달린 가지다 — 기본코드끼리는 서브를 건너뛰고 직접 잇는다.
    1호선 북단이 그 예다: 소요산(100) ─ 동두천(101) 이 본선이고,
    청산(100-1)·전곡(100-2)·연천(100-3) 은 소요산 반대편으로 뻗은 가지다.
    순서대로 이으면 연천─동두천이라는 없는 연결이 생기고 소요산─동두천이 사라진다."""
    if line_num in INLINE_SUB:
        return ([(seq[i][1]["STATION_NM"], seq[i + 1][1]["STATION_NM"], "fr_seq")
                 for i in range(len(seq) - 1)], [])
    bases = [(k, x) for k, x in seq if k[2] == 0]
    edges, cut = [], []
    for i in range(len(bases) - 1):
        (pa, na, _), xa = bases[i]
        (pb, nb, _), xb = bases[i + 1]
        if pa != pb:
            continue                       # 접두어가 바뀌면 다른 계통 → EXTRA_EDGES 로만 잇는다
        if nb - na > FR_GAP_MAX:           # 경의선 지평(K138) ↔ 공덕(K312) 같은 자리
            cut.append((xa["STATION_NM"], xa["FR_CODE"], xb["STATION_NM"], xb["FR_CODE"]))
            continue
        edges.append((xa["STATION_NM"], xb["STATION_NM"], "fr_seq"))
    subs = collections.defaultdict(list)
    for k, x in seq:
        if k[2] != 0:
            subs[(k[0], k[1])].append((k[2], x))
    base_of = {(k[0], k[1]): x for k, x in seq if k[2] == 0}
    for key, items in subs.items():
        items.sort()
        chain = ([base_of[key]] if key in base_of else []) + [x for _, x in items]
        for i in range(len(chain) - 1):
            edges.append((chain[i]["STATION_NM"], chain[i + 1]["STATION_NM"], "fr_sub"))
    return edges, cut


# ────────────────────────────── 관측 ──────────────────────────────
def dest_to_station(dest, nameset):
    """시간표 dest_nm 표기를 역명으로 맞춘다. '삼성(무역센터)'→'삼성', '하남검단산역'→'하남검단산'."""
    if dest in nameset:
        return dest
    cand = re.sub(r"\s*\(.*?\)\s*", "", dest)
    if cand not in nameset and cand.endswith("역") and cand[:-1] in nameset:
        cand = cand[:-1]
    return cand if cand in nameset else None


def load_official(by_line, names_by_line, spur_junction):
    """서울교통공사 역간거리·소요시간 표를 (노선, 두 역) → (분, m) 로 읽는다.

    표는 노선마다 역을 한 줄로 늘어놓고 각 행에 **바로 앞 역으로부터의** 값을 적는다.
    그래서 두 가지를 처리해야 한다.

    ① 지선 블록의 첫 행은 앞 행이 아니라 **그 지선의 분기역**으로부터의 값이다.
       2호선에서 용답(3:00/2.3km)의 앞 행은 시청인데, 실제로는 성수→용답 값이다.
       성수지선 총연장 5.4km 가 2.3+1.0+0.9+1.2 로 맞아떨어진다. 도림천 1:30/1.0km 도
       신설동이 아니라 신도림에서 잰 값이고, 5호선 둔촌동 1:50/1.2km 도 강동에서 잰 값이다.
       → 앞 역과 인접이 아닌데 그 역이 지선 첫 역이면 분기역에서 잰 것으로 본다.
    ② 그러고도 우리 간선이 아닌 쌍은 블록 접합부라 버린다.

    공식값은 **주행시간**이라 정차시간이 빠져 있다(우리 관측보다 평균 0.5분 작다).
    그래서 관측이 있는 간선은 관측을 그대로 두고, **관측이 없는 간선만** 이 값으로 메운다."""
    if not OFFICIAL.exists():
        return {}, 0, []
    rename = {("04호선", "당고개"): "불암산", ("06호선", "신내역"): "신내"}
    txt = OFFICIAL.read_text(encoding="cp949")
    rows = list(csv.DictReader(io.StringIO(txt)))
    per_line = collections.defaultdict(list)
    for r in rows:
        per_line[f"{int(r['호선']):02d}호선"].append(r)

    def nm(ln, raw):
        v = (raw or "").strip()
        v = rename.get((ln, v), v)
        if v in names_by_line.get(ln, ()):
            return v
        v2 = re.sub(r"\s*\(.*?\)\s*", "", v)
        return v2 if v2 in names_by_line.get(ln, ()) else None

    def minutes(v):
        v = (v or "").strip()
        if ":" not in v:
            return None
        a, b = v.split(":")[:2]
        return int(a) + int(b) / 60

    edgeset = {(ln, frozenset((e[0], e[1]))) for ln, es in by_line.items() for e in es}
    out, seams = {}, []
    for ln, rs in per_line.items():
        for i in range(len(rs) - 1):
            a, b = nm(ln, rs[i]["역명"]), nm(ln, rs[i + 1]["역명"])
            t = minutes(rs[i + 1]["소요시간"])
            try:
                dist = float(rs[i + 1]["역간거리(km)"]) * 1000
            except (TypeError, ValueError):
                dist = None
            if not a or not b or not t:
                continue
            if (ln, frozenset((a, b))) not in edgeset:
                j = spur_junction.get((ln, b))          # ① 지선 첫 역이면 분기역에서 잰 값
                if j and (ln, frozenset((j, b))) in edgeset:
                    a = j
                else:
                    seams.append((ln, a, b)); continue  # ② 블록 접합부 — 버린다
            out[(ln, frozenset((a, b)))] = (round(t, 1), round(dist) if dist else None)
    return out, len(rows), seams


def load_timetable(names_by_line):
    """출발시각을 두 가지로 담는다.

    BY_DEST[(line, dir, day, dest)] — 행선지로 가른 것. 분기역에서 방향이 섞이지 않는다.
      1호선 구로를 통째로 보면 구일 방면과 가산디지털단지 방면이 반반이라 일치 비율이
      0.46 으로 떨어져 인접인데도 확인에 실패한다. dest 로 가르면 1.0 으로 돌아온다.
    BY_DIR[(line, dir, day)] — dest_nm 이 비어 있는 구간(2,985 중 144 조합, 3호선 일산
      구간·4호선 진접선 등)을 위한 대비책. 분기역이 아니면 이쪽으로도 충분히 잡힌다."""
    BY_DEST = collections.defaultdict(lambda: collections.defaultdict(list))
    BY_DIR = collections.defaultdict(lambda: collections.defaultdict(list))
    dest_rows, seen_st = collections.Counter(), collections.defaultdict(set)
    alias, unresolved = collections.defaultdict(dict), collections.Counter()
    n = phantom = 0
    with TIMETABLE.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            n += 1
            ln, st, dest, t = r["line"], r["station_nm"], r.get("dest_nm"), r.get("dep_time")
            if dest:
                dest_rows[(ln, dest)] += 1
            if not t:
                continue
            seen_st[ln].add(st)
            if t == PHANTOM_DEP:
                phantom += 1
                continue
            h, mi, sec = t.split(":")
            v = int(h) * 3600 + int(mi) * 60 + int(sec)
            BY_DIR[(ln, r["dir"], r["day_type"])][st].append(v)
            if not dest:
                continue
            key = dest
            if dest not in names_by_line.get(ln, ()):
                if dest not in alias[ln]:
                    hit = dest_to_station(dest, names_by_line.get(ln, set()))
                    if hit:
                        alias[ln][dest] = hit
                    else:
                        unresolved[(ln, dest)] += 1
                key = alias[ln].get(dest, dest)
            BY_DEST[(ln, r["dir"], r["day_type"], key)][st].append(v)
    for M in (BY_DEST, BY_DIR):
        for k in M:
            for s in M[k]:
                M[k][s].sort()
    return BY_DEST, BY_DIR, dest_rows, seen_st, alias, unresolved, n, phantom


def offset(ta, tb):
    """tb ≈ ta + δ 인 δ 를 찾는다. 반환 (δ분, 그 δ 로 이어진 A 열차 비율, 봉우리/배경)."""
    hits, raw, diffs = collections.defaultdict(set), collections.Counter(), collections.defaultdict(list)
    for i, a in enumerate(ta):
        lo = bisect.bisect_left(tb, a - WIN_S)
        hi = bisect.bisect_right(tb, a + WIN_S)
        for b in tb[lo:hi]:
            k = (b - a) // BIN_S
            hits[k].add(i); raw[k] += 1; diffs[k].append(b - a)
    if not hits:
        return None, 0.0, 0.0, None
    sm = {d: len(hits.get(d - 1, set()) | hits[d] | hits.get(d + 1, set())) for d in hits}
    # 동점이 나면(배차가 촘촘하면 여러 δ 가 다 1.0 이다) 평활 안 한 원 봉우리가 큰 쪽,
    # 그래도 같으면 |δ| 가 작은 쪽. 인접 역 주행시간은 열차마다 거의 일정해서 원 봉우리가 뾰족하다.
    best = max(sm, key=lambda d: (sm[d], raw[d], -abs(d)))
    med = statistics.median(sm.values())
    # 봉우리 칸 안의 실제 시차 중앙값(초). 분으로 반올림하면 역마다 최대 30초씩 부풀어
    # 21개 역을 더한 2호선 시청→강남이 42분이 된다(실제는 34분 안팎).
    sec = int(statistics.median(sum((diffs.get(best + o, []) for o in (-1, 0, 1)), [])))
    return round(best * BIN_S / 60), sm[best] / len(ta), sm[best] / max(med, 1), sec


def ok(c):
    """그 방향에서 '같은 열차가 A 다음 B 를 떠난다'가 관측되었는가"""
    if not c or not (1 <= abs(c["delta_min"]) <= MAX_DELTA) or c["frac"] < MIN_FRAC:
        return False
    return c["basis"] == "dest" or c["sep"] >= MIN_SEP_DIR


def observe(dest_keys, dir_keys, BY_DEST, BY_DIR, line, a, b):
    """인접 후보 (a,b) 를 방향별로 관측한다. 행선지로 가른 쪽을 먼저 보고, 없으면 방향 전체로 본다."""
    out = {}
    for d in ("U", "D"):
        cand = []
        for basis, keys, M in (("dest", dest_keys, BY_DEST), ("dir", dir_keys, BY_DIR)):
            for key in keys.get((line, d), ()):
                stmap = M[key]
                if a not in stmap or b not in stmap or len(stmap[a]) < MIN_TRAINS:
                    continue
                delta, frac, sep, sec = offset(stmap[a], stmap[b])
                if delta is None:
                    continue
                cand.append({"basis": basis, "day_type": key[2],
                             "dest_nm": key[3] if basis == "dest" else None,
                             "delta_min": delta, "delta_sec": sec, "frac": round(frac, 3),
                             "sep": round(sep, 2), "trains": len(stmap[a])})
            if any(ok(c) for c in cand):
                break                       # 행선지 기준으로 잡혔으면 방향 전체는 안 본다
        if cand:
            good = [c for c in cand if ok(c)]
            # 같은 dir 안에서 행선지에 따라 시차 부호가 갈리면 그 dir 라벨은 방향을 못 가른다.
            # 인천2호선·신림선이 그렇다 — 인천시청 D 에 운연행과 검단오류행(양 끝)이 섞여 있다.
            signs = {c["delta_min"] > 0 for c in good if c["basis"] == "dest"}
            best = max(good or cand, key=lambda c: (c["frac"], c["trains"]))
            best["dir_mixed"] = len(signs) > 1
            out[d] = best
    return out


def grade_edge(obs):
    u, dn = obs.get("U"), obs.get("D")
    if ok(u) and ok(dn):
        return "확정" if u["delta_min"] * dn["delta_min"] < 0 else "추정:방향라벨충돌"
    if ok(u) or ok(dn):
        return "추정:단방향관측"
    if u or dn:
        return "추정:구조만"
    return "근거없음"


# ────────────────────────────── 조립 ──────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-validate", action="store_true", help="시간표 검증 없이 구조만(전부 추정)")
    args = ap.parse_args()

    by_line = load_stations()
    names_by_line = {ln: {x["STATION_NM"] for _, x in seq} for ln, seq in by_line.items()}
    # 지선 역 → 분기역. 공식 표에서 지선 블록 첫 행의 기준점을 찾는 데 쓴다.
    spur_junction = {}
    for ln, seq in by_line.items():
        base_nm = {(k[0], k[1]): x["STATION_NM"] for k, x in seq if k[2] == 0}
        for k, x in seq:
            if k[2] != 0 and (k[0], k[1]) in base_nm:
                spur_junction[(ln, x["STATION_NM"])] = base_nm[(k[0], k[1])]
    if args.no_validate:
        BY_DEST = BY_DIR = {}
        dest_rows, seen_st, alias, unresolved, tt_rows, phantom = \
            collections.Counter(), {}, {}, collections.Counter(), 0, 0
    else:
        (BY_DEST, BY_DIR, dest_rows, seen_st, alias,
         unresolved, tt_rows, phantom) = load_timetable(names_by_line)
    dest_keys, dir_keys = collections.defaultdict(list), collections.defaultdict(list)
    for k in BY_DEST:
        dest_keys[(k[0], k[1])].append(k)
    for k in BY_DIR:
        dir_keys[(k[0], k[1])].append(k)

    # 공식 표는 간선 집합을 알아야 매칭되므로 간선을 먼저 만들어 둔다.
    edges_pre = {}
    for ln, seq in by_line.items():
        e0, _ = fr_edges(ln, seq)
        e0 = [(a, b, src) for a, b, src in e0
              if not any(l2 == ln and {a, b} == {x, y} for l2, x, y, _ in CUT_EDGES)]
        for l2, a, b, why in EXTRA_EDGES:
            if l2 == ln and a in names_by_line[ln] and b in names_by_line[ln]:
                e0.append((a, b, "manual:" + why))
        edges_pre[ln] = e0
    official, official_rows, official_seams = load_official(edges_pre, names_by_line, spur_junction)
    print(f"공식 역간거리표: {official_rows}행 → 간선 {len(official)}개 매칭 · 접합부 {len(official_seams)}쌍 버림")

    lines_out, warn = {}, []
    for ln, seq in by_line.items():
        names = [x["STATION_NM"] for _, x in seq]
        nameset, idx = set(names), {n: i for i, n in enumerate(names)}
        edges, cut = fr_edges(ln, seq)
        edges = [(a, b, src) for a, b, src in edges
                 if not any(l2 == ln and {a, b} == {x, y} for l2, x, y, _ in CUT_EDGES)]
        for l2, x, y, why in CUT_EDGES:
            if l2 == ln:
                warn.append(f"{ln}: {x}–{y} 는 FR 번호상 인접이지만 선로가 아니라 끊었다 — {why[:60]}…")
        for a, fa, b, fb in cut:
            warn.append(f"{ln}: FR 번호가 벌어져 {a}({fa})–{b}({fb}) 는 잇지 않았다")
        for l2, a, b, why in EXTRA_EDGES:
            if l2 != ln:
                continue
            if a not in nameset or b not in nameset:
                warn.append(f"{ln}: EXTRA_EDGES 의 {a}–{b} 를 역 목록에서 못 찾음")
                continue
            edges.append((a, b, "manual:" + why))
        seen_e, ded = set(), []
        for a, b, src in edges:
            k = tuple(sorted((a, b)))
            if k not in seen_e:
                seen_e.add(k); ded.append((a, b, src))
        edges = ded

        has_tt = {s: s in seen_st.get(ln, ()) for s in names}
        edge_out, asc, desc = [], collections.Counter(), collections.Counter()
        dir_mixed = collections.Counter()
        for a, b, src in edges:
            obs = {} if args.no_validate else observe(dest_keys, dir_keys, BY_DEST, BY_DIR, ln, a, b)
            g = grade_edge(obs)
            good = {d: c for d, c in obs.items() if ok(c)}
            e = {"a": a, "b": b, "source": src, "grade": g}
            # a→b 로 달리는 dir 라벨. 경로를 걸을 때 이것만 있으면 된다.
            # 지선은 본선과 U/D 방향이 반대라(2호선 성수지선) 노선 단위 direction 만으로는 안 된다.
            fwd = [d for d, c in good.items() if c["delta_min"] > 0]
            bwd = [d for d, c in good.items() if c["delta_min"] < 0]
            e["dir_a_to_b"] = fwd[0] if len(fwd) == 1 and not (set(fwd) & set(bwd)) else None
            e["dir_b_to_a"] = bwd[0] if len(bwd) == 1 else None
            # ── 소요시간 ──────────────────────────────────────────────
            # 2026-09-10 판: 확정 간선에만 붙였다. 그래서 777 중 141 이 비었고
            # 02번 방에서 '성립인데 도착 시각을 못 내는' 구간이 그만큼 생겼다.
            # 이제 셋을 순서대로 쓴다.
            #   ① 관측(확정)  — 양방향에서 확인된 출발시각 차. 정차시간이 들어 있어 판정에 맞다.
            #   ② 관측(추정)  — 한쪽 방향만 잡힌 것. 값이 1~8분이면 쓴다.
            #                   범위를 거는 이유: 6호선 응암–역촌이 순환 한 바퀴인 11분으로 잡힌다.
            #   ③ 공식 표     — 서울교통공사 역간거리·소요시간. 주행시간이라 우리 관측보다
            #                   평균 0.5분 작다. 관측이 없는 간선만 이걸로 메운다.
            off = official.get((ln, frozenset((a, b))))
            # 관측 후보: ok() 를 통과한 것 우선, 없으면 기준 미달 관측이라도 1~8분이면 쓴다
            # (FR 로 인접이 확인된 간선이라 시차 자체는 쓸 만하다. 등급은 추정으로 남는다.)
            obs_ok = [c for c in good.values() if 1 <= abs(c["delta_min"]) <= 8] or \
                     [c for c in obs.values() if 1 <= abs(c["delta_min"]) <= 8]
            if g == "확정" and good:
                e["travel_min"] = round(sum(abs(c["delta_sec"]) for c in good.values()) / len(good) / 60, 1)
                e["travel_min_source"] = "observed"
                e["travel_min_grade"] = "확정"
            elif obs_ok:
                e["travel_min"] = round(sum(abs(c["delta_sec"]) for c in obs_ok) / len(obs_ok) / 60, 1)
                e["travel_min_source"] = "observed"
                e["travel_min_grade"] = "추정"
            elif off:
                e["travel_min"] = off[0]
                e["travel_min_source"] = "official"
                e["travel_min_grade"] = "확정"
            if off:
                e["distance_m"] = off[1]
                e["official_travel_min"] = off[0]
            if obs:
                e["observed"] = {d: {kk: c[kk] for kk in
                                     ("delta_min", "delta_sec", "frac", "sep", "trains", "basis", "day_type", "dest_nm")}
                                 for d, c in obs.items()}
            if g in ("근거없음", "추정:구조만") and has_tt.get(a) and has_tt.get(b) and obs:
                e["note"] = "양쪽 다 시간표가 있는데 두 역을 잇는 열차를 못 찾았다 — 직결 운행이 없을 수 있다"
            edge_out.append(e)
            for d, c in obs.items():
                if c.get("dir_mixed"):
                    dir_mixed[d] += 1
            if src.startswith("fr") and idx[a] < idx[b]:
                for d, c in obs.items():
                    if ok(c):
                        (asc if c["delta_min"] > 0 else desc)[d] += 1
            if g == "근거없음" and has_tt.get(a) and has_tt.get(b):
                warn.append(f"{ln}: {a}–{b} 인접 확인 실패 (양쪽 다 시간표 있음, {src})")

        direction = {}
        for d in ("U", "D"):
            f, r = asc[d], desc[d]
            if f + r == 0:
                direction[d] = {"fr_order": None, "grade": "근거없음"}
            else:
                direction[d] = {"fr_order": "asc" if f > r else "desc",
                                "grade": "확정" if max(f, r) / (f + r) >= 0.95 else "추정",
                                "edges_forward": f, "edges_backward": r}
        conflict = sum(1 for e in edge_out if e["grade"] == "추정:방향라벨충돌")
        mixed = sum(dir_mixed.values())
        if conflict + mixed and conflict + mixed >= 0.3 * max(len(edge_out), 1):
            warn.append(f"{ln}: dir(U/D) 라벨이 방향을 못 가른다 — 같은 dir 에 양 끝 행선지가 섞여 있다. "
                        f"이 노선은 방향을 dest_nm 으로 정해야 한다 (충돌 간선 {conflict}/{len(edge_out)})")
        lines_out[ln] = {
            "n_stations": len(names),
            "dir_label": {"reliable": not (conflict + mixed >= 0.3 * max(len(edge_out), 1)),
                          "conflict_edges": conflict, "mixed_dir_observations": mixed,
                          "note": "reliable=false 면 U/D 로 방향을 가르면 안 된다. dest_nm 으로 정한다."},
            "is_loop": any("순환" in e["source"] for e in edge_out),
            "direction": direction,
            "stations": [{"station_nm": x["STATION_NM"], "station_key": f"{ln}|{x['STATION_NM']}",
                          "station_cd": x["STATION_CD"], "station_nm_en": x["STATION_NM_ENG"],
                          "fr_code": x["FR_CODE"], "fr_order": i, "is_spur": k[2] != 0,
                          "has_timetable": has_tt[x["STATION_NM"]]}
                         for i, (k, x) in enumerate(seq)],
            "edges": edge_out,
        }

    tt_meta = json.loads(TT_META.read_text(encoding="utf-8")) if TT_META.exists() else {}
    doc = {
        "schema": "line_station_order_v1",
        "built_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": {
            "official": {"file": OFFICIAL.name, "origin": "서울교통공사 역간거리 및 소요시간",
                         "matched_edges": len(official),
                         "note": "주행시간(정차 제외)이라 관측보다 평균 0.5분 작다. 관측이 없는 간선만 이걸로 메운다"},
            "structure": {"file": "stations_all.json", "field": "FR_CODE",
                          "origin": "서울열린데이터광장 역사마스터",
                          "note": "FR_CODE 는 공식 역번호이고 노선 순서 그 자체다"},
            "validation": {"file": "timetable_v1.jsonl", "rows": tt_rows,
                           "built_at": tt_meta.get("built_at"),
                           "tago_fetched_at": tt_meta.get("tago_fetched_at"),
                           "seoul_fetched_at": tt_meta.get("seoul_fetched_at"),
                           "excluded_phantom_2400": phantom},
        },
        "method": ("FR_CODE 정렬로 인접쌍을 만들고(구조), 두 역 출발시각 차의 히스토그램 봉우리로 확인한다(관측). "
                   f"탐색폭 ±{WIN_S//60}분·칸 {BIN_S}초·최소편수 {MIN_TRAINS}·일치비율 {MIN_FRAC}. "
                   "양방향이 반대 부호로 잡히면 확정, 한쪽만이면 추정, 표본이 없으면 근거없음."),
        "grades": {"확정": "구조 + 양방향 관측 일치", "추정:단방향관측": "한쪽 방향만 확인",
                   "추정:방향라벨충돌": "양방향이 같은 부호 — 종착역 dir 표기 문제일 수 있다",
                   "추정:구조만": "FR_CODE 로만 인접, 관측이 기준 미달",
                   "_travel_min": "travel_min_grade / travel_min_source 를 같이 본다. observed 는 정차시간 포함, official 은 주행시간만",
                   "_dir": "dir_a_to_b / dir_b_to_a 는 그 방향으로 달리는 열차의 dir 라벨. 지선은 본선과 반대일 수 있어 노선 단위 direction 보다 이쪽이 정확하다",
                   "근거없음": "시간표 표본이 없어 확인 불가"},
        "usage": ("판정기는 edges 를 무향 그래프로 읽는다. '이 열차가 목적지를 지나는가' = 출발역에서 "
                  "dest_nm(dest_alias 로 정규화) 까지의 경로에 목적지가 있는가. "
                  "★ 방향은 dir(U/D) 이 아니라 dest_nm 으로 정한다 — 열차의 행선지가 곧 방향이고, "
                  "인천2호선·신림선은 같은 dir 에 양 끝 행선지가 섞여 있어 dir 로는 못 가른다"
                  "(lines[*].dir_label.reliable 확인). 순환선(is_loop)은 두 역 사이 경로가 둘이므로 "
                  "행선지까지의 경로를 쓴다. grade 가 근거없음인 간선을 지나는 경로는 판정도 "
                  "근거없음으로 내린다 — 이어져 있다고 단정하지 않는다."),
        "direction_semantics": "fr_order='asc' 면 그 dir 의 열차가 FR_CODE 오름차순으로 진행한다.",
        "dest_alias": {k: dict(sorted(v.items())) for k, v in sorted(alias.items()) if v},
        "dest_unresolved": [{"line": k[0], "dest_nm": k[1], "rows": v} for k, v in unresolved.items()],
        "warnings": warn,
        "lines": lines_out,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 리포트 ──
    g_all = collections.Counter()
    g_detail = collections.Counter(e["grade"] for v in lines_out.values() for e in v["edges"])
    rows = []
    for ln, v in sorted(lines_out.items()):
        c = collections.Counter(e["grade"].split(":")[0] for e in v["edges"])
        g_all.update(c)
        nott = sum(1 for s in v["stations"] if not s["has_timetable"])
        du = {d: (v["direction"][d]["fr_order"] or "?") for d in ("U", "D")}
        rows.append(f"| {ln} | {v['n_stations']} | {len(v['edges'])} | {c['확정']} | {c['추정']} | "
                    f"{c['근거없음']} | U={du['U']} / D={du['D']} | {'예' if v['is_loop'] else ''} | {nott or ''} |")
    out = ["# 노선별 역 순서 v1", "",
           f"생성 {doc['built_at']} · 구조 FR_CODE({len(by_line)}개 노선) · 검증 시간표 {tt_rows:,}행", "",
           f"간선 등급 합계: 확정 {g_all['확정']} · 추정 {g_all['추정']} · 근거없음 {g_all['근거없음']}", "",
           "추정 내역: " + (" · ".join(f"{k} {v}" for k, v in sorted(g_detail.items()) if k.startswith("추정")) or "없음"), "",
           "| 노선 | 역 | 간선 | 확정 | 추정 | 근거없음 | 방향(FR 진행) | 순환 | 시간표 없는 역 |",
           "|---|---|---|---|---|---|---|---|---|"] + rows
    out += ["", "## 읽는 법", "",
            "- `edges` 를 무향 그래프로 읽는다. 두 역 사이 경로는 순환선을 빼면 하나뿐이라 '지나는가' 판정이 유일하게 정해진다.",
            "- `travel_min` 은 ① 양방향 관측(확정) ② 단방향 관측 1~8분(추정) ③ 공식 역간거리표(확정, 관측이 없을 때만) 순으로 정한다. `travel_min_source`·`travel_min_grade` 를 같이 본다. 관측값은 정차시간이 포함돼 공식값보다 평균 0.5분 크다 — 승객이 겪는 값은 관측 쪽이다.",
            "- 지선은 `is_spur` 로 표시한다(2호선 성수·신정지선, 5호선 마천지선, 1호선 광명·서동탄·연천).",
            "- `dest_alias` 는 시간표 `dest_nm` 표기 변형을 역명으로 맞춘 표다(`하남검단산역`→`하남검단산`).",
            "- 확정으로 못 올린 간선은 대개 그 구간 시간표 표본이 없는 곳이다(경춘선·경의선 일부).", ""]
    if phantom:
        out += ["## 관측에서 뺀 행", "",
                f"- `dep_time == 24:00:00` {phantom:,}행. 시·종착역의 '출발 없음'(`'000000'`)이 "
                "`build_timetable_v1.py` 의 자정 넘김 정규화에 걸려 24시로 바뀐 것이다. "
                "관측에 넣으면 종착역이 다 같이 자정에 출발한 것으로 잡혀 시차가 어긋난다.", ""]
    if warn:
        out += ["## 경고", ""] + [f"- {w}" for w in warn] + [""]
    if unresolved:
        out += ["## 역명으로 못 맞춘 dest_nm", ""] + \
               [f"- {k[0]} `{k[1]}` ({v}행)" for k, v in unresolved.items()] + [""]
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"노선 {len(lines_out)} · 간선 확정 {g_all['확정']} / 추정 {g_all['추정']} / 근거없음 {g_all['근거없음']} → {OUT}")
    print(f"리포트 → {REPORT}")
    for w in warn:
        print("  경고:", w)


if __name__ == "__main__":
    main()
