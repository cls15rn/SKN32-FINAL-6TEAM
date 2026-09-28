# mobility_scripts/consistency_check.py — 소스 간 정합성 검증
#
# 실행:  python mobility_scripts/consistency_check.py
#
# 왜: 소스별 전처리(build_*.py)는 각 파일을 "그 소스 안에서" 옳게 만든다. 그런데 판정기는
#     여러 소스를 **같이** 읽는다 — 시간표에서 막차를 보고 혼잡도에서 그 시각 부하를 본다.
#     축이 어긋나 있어도 각 파일은 멀쩡해 보이고, 합성 여행도 그냥 통과한다. 값만 조용히 틀린다.
#     이 스크립트는 소스 사이의 접합부만 본다. 판정 로직은 보지 않는다.
#
# 검사 넷:
#   C1 혼잡도 dir(U/D) 매핑        ← 가장 중요. 틀리면 판정이 정반대가 된다
#   C2 혼잡도 station_key 가 시간표에 있는가
#   C3 혼잡도 '막차 이후' 슬롯이 시간표 막차와 맞는가
#   C4 열린데이터광장 첫차막차(OA-15492) ↔ 시간표 교차검증  (데이터소싱 §3-2 에 하겠다고 적어둔 것)
#   C5 막차 열차의 행선지 — 단축운행 확인
#   C6 행선지가 진행 방향에 있는가
#   C7 역 영문명 — 값 단위 결함 + **역 단위 일관성**(같은 한글 역명은 같은 영문명) (2026-09-13 추가)
#   C8 역 좌표 — 다른 역명끼리 50 m 안이면 원본 행 오류 의심 + 좌표 보정표가 살아 있는가 (2026-09-21 · 34번)
import collections
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "collect"))
from _paths import RAW_MOBILITY, PROCESSED          # noqa: E402

KST = timezone(timedelta(hours=9))
MOB = PROCESSED / "mobility"
TT = MOB / "timetable_v1.jsonl"
CG = MOB / "congestion_v1.jsonl"
FL = RAW_MOBILITY / "first_last_2to9.json"
REPORT = MOB / "consistency_report.md"

out = []
def say(s=""):
    print(s)
    out.append(s)

def to_min(t):
    """'HH:MM:SS' · 'HHMMSS' · 'HH:MM' → 분. 24 시 넘는 표기를 그대로 살린다."""
    if not t:
        return None
    d = re.sub(r"\D", "", str(t))
    if len(d) < 4:
        return None
    return int(d[:2]) * 60 + int(d[2:4])

def mmss(m):
    return None if m is None else f"{m // 60:02d}:{m % 60:02d}"


# 혼잡도 day_type 은 3종(weekday/saturday/sunday), 시간표는 2종(weekday/holiday)이다.
# 그대로 조인하면 토·일이 통째로 '조합 없음'이 되어 검사가 무력해진다.
CG2TT = {"weekday": ["weekday"], "saturday": ["saturday", "holiday"], "sunday": ["holiday"]}


def tt_get(agg, line, nm, d, cg_day):
    """혼잡도 day_type 으로 시간표 조합을 찾는다. 토요일은 별도 저장분이 있으면 그것을 먼저 쓴다."""
    for dt in CG2TT.get(cg_day, [cg_day]):
        a = agg.get((line, nm, d, dt))
        if a:
            return a, dt
    return None, None

if not TT.exists():
    sys.exit(f"시간표가 없다 → {TT}")

# ── 시간표 집계 (465,490행 스트리밍) ───────────────────────────────
# (line, station_nm, dir, day_type) → 편수·첫차·막차
tt = collections.defaultdict(lambda: {"n": 0, "first": None, "last": None, "last_dest": None})
tt_keys = set()          # station_key
tt_cd = {}               # station_cd → station_key
rows = 0
for line in TT.open(encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    rows += 1
    tt_keys.add(r["station_key"])
    if r.get("station_cd"):
        tt_cd[str(r["station_cd"]).zfill(4)] = r["station_key"]
    m = to_min(r.get("dep_time"))
    if m is None:                       # 시·종착역은 출발이 없다(dep_time '0'/None)
        continue
    a = tt[(r["line"], r["station_nm"], r["dir"], r["day_type"])]
    a["n"] += 1
    a["first"] = m if a["first"] is None else min(a["first"], m)
    if a["last"] is None or m > a["last"]:
        a["last"] = m
        a["last_dest"] = r.get("dest_nm")      # 막차가 어디까지 가는지 — C5 에서 쓴다

say("# 소스 간 정합성 검증")
say()
say(f"실행 {datetime.now(KST).isoformat(timespec='seconds')}")
say(f"시간표 {rows:,}행 · 조합 {len(tt):,} · station_key {len(tt_keys):,}")
say()

fail = 0

# ── C1. 혼잡도 dir 매핑 ────────────────────────────────────────────
say("## C1. 혼잡도 dir(U/D) 매핑")
say()
if not CG.exists():
    say(f"건너뜀 — 혼잡도가 없다({CG.name}). `congestion_build.py` 를 먼저 실행한다.")
    say()
    cg_rows = []
else:
    cg_rows = [json.loads(l) for l in CG.open(encoding="utf-8") if l.strip()]
    say(f"혼잡도 {len(cg_rows):,}행")
    say()
    say("혼잡도에서 **전 시간대 0** 인 조합은 '종착역의 종착 방향'이다 — 그 방향으로 출발하는 "
        "열차가 구조적으로 없다는 뜻이다. 시간표에서 같은 조합의 출발 편수가 0이어야 한다. "
        "이게 dir 매핑을 데이터로 확인하는 방법이다.")
    say()
    # 지선·순환이 있는 역은 뺀다 — 혼잡도는 본선과 지선을 branch 로 나누는데 시간표는 안 나눈다.
    # 축이 다르므로 편수를 대조해도 답이 안 나온다(성수·신도림·까치산·신설동·응암).
    branchy = {(r["line"], r["station_nm"]) for r in cg_rows if r.get("branch")}
    term = set()
    for r in cg_rows:
        if r.get("reason") == "terminus_direction" and (r["line"], r["station_nm"]) not in branchy:
            term.add((r["line"], r["station_nm"], r["dir"], r["day_type"]))
    say(f"지선·순환이 있어 제외한 역: {sorted({nm for _, nm in branchy})}")
    say()
    ok = mism = 0
    detail = []
    for (ln, nm, d, dt) in sorted(term):
        opp = "D" if d == "U" else "U"
        a_same, used = tt_get(tt, ln, nm, d, dt)
        a_opp, _ = tt_get(tt, ln, nm, opp, dt)
        n_same = (a_same or {}).get("n", 0)
        n_opp = (a_opp or {}).get("n", 0)
        if n_same == 0 and n_opp > 0:
            ok += 1
        elif n_same > 0 and n_opp == 0:
            mism += 1
            detail.append(f"| {ln} | {nm} | {d} | {dt}→{used} | {n_same} | {n_opp} | **뒤집힘 의심** |")
        else:
            detail.append(f"| {ln} | {nm} | {d} | {dt}→{used} | {n_same} | {n_opp} | 판단 불가 |")
    say(f"검사 대상 {len(term)} 조합 · 일치 **{ok}** · 뒤집힘 의심 **{mism}** · 판단 불가 {len(term) - ok - mism}")
    say()
    if detail:
        say("| 노선 | 역 | 혼잡도 dir | 요일 | 같은 dir 편수 | 반대 dir 편수 | 판정 |")
        say("|---|---|---|---|---|---|---|")
        for d_ in detail:
            say(d_)
        say()
    if term and mism > ok:
        say("→ **매핑이 뒤집혀 있다.** `congestion_build.py` 의 `DIR` 표를 반대로 고치고 다시 만든다.")
        fail += 1
    elif term and ok and mism == 0:
        say("→ **매핑이 맞다.** `rules.congestion.방향` 의 등급을 추정에서 확정으로 올릴 수 있다.")
    elif term:
        say("→ 섞여 있다. 위 표의 개별 조합을 확인한다.")
        fail += 1
    say()

# ── C2. 혼잡도 station_key ↔ 시간표 ────────────────────────────────
say("## C2. 혼잡도 station_key 가 시간표에 있는가")
say()
if cg_rows:
    cg_keys = {r["station_key"] for r in cg_rows}
    miss = sorted(cg_keys - tt_keys)
    say(f"혼잡도 station_key {len(cg_keys)} · 시간표에 없음 **{len(miss)}**")
    if miss:
        say()
        say(f"없는 키: {miss}")
        say()
        say("→ 이 역들은 혼잡도가 있어도 시간표가 없어 판정에 붙지 못한다.")
        fail += 1
    else:
        say()
        say("→ 전부 붙는다. 혼잡도 조회가 시간표 판정에 그대로 얹힌다.")
else:
    say("건너뜀 — 혼잡도 없음")
say()

# ── C3. 혼잡도 '막차 이후' ↔ 시간표 막차 ───────────────────────────
say("## C3. 혼잡도 '막차 이후' 슬롯이 시간표 막차와 맞는가")
say()
if cg_rows:
    # 조합별 마지막 '확정' 슬롯
    last_ok = {}
    for r in cg_rows:
        if r["grade"] != "확정":
            continue
        k = (r["line"], r["station_nm"], r["dir"], r["day_type"])
        m = to_min(r["slot"])
        if m is not None and (k not in last_ok or m > last_ok[k]):
            last_ok[k] = m
    checked = big = 0
    worst = []
    for k, cm in last_ok.items():
        a, _ = tt_get(tt, k[0], k[1], k[2], k[3])
        if not a or a["last"] is None:
            continue
        checked += 1
        gap = cm - a["last"]          # 혼잡도 마지막 슬롯 - 시간표 막차
        if abs(gap) > 60:
            big += 1
            worst.append((abs(gap), k, mmss(cm), mmss(a["last"]), gap))
    worst.sort(reverse=True)
    say(f"대조한 조합 {checked:,} · **1시간 넘게 어긋난 것 {big}**")
    if worst[:12]:
        say()
        say("| 노선 | 역 | dir | 요일 | 혼잡도 마지막 슬롯 | 시간표 막차 | 차이(분) |")
        say("|---|---|---|---|---|---|---|")
        for _, k, cm, lm, gap in worst[:12]:
            say(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {cm} | {lm} | {gap:+d} |")
    say()
    say("혼잡도는 30분 슬롯이고 24:30 에서 끊기므로 **음수(-30~-60분)는 정상**이다. "
        "양수로 크게 벌어지면 dir 이나 day_type 이 어긋난 것이다.")
else:
    say("건너뜀 — 혼잡도 없음")
say()

# ── C4. 열린데이터광장 첫차막차 ↔ 시간표 ───────────────────────────
say("## C4. 열린데이터광장 첫차막차(OA-15492) ↔ 시간표 교차검증")
say()
if not FL.exists():
    say(f"건너뜀 — {FL.name} 없음")
else:
    fl = json.load(FL.open(encoding="utf-8"))
    DOW = {"1": "weekday", "2": "saturday", "3": "holiday"}
    DIR = {"1": "U", "2": "D"}        # 1 상행/내선 → U (시간표와 같은 가정)
    agg = collections.Counter()
    diffs = collections.Counter()
    samples = []
    for r in fl:
        ln, cd = r["SBWY_ROUT_LN"], str(r["SUBW_CD"]).zfill(4)
        key = tt_cd.get(cd)
        if not key:
            agg["역 못 찾음"] += 1
            continue
        nm = key.split("|", 1)[1]
        dt = DOW.get(r["DOW"])
        d = DIR.get(r["UPLN_DNLN"])
        a = tt.get((ln, nm, d, dt))
        if a is None and dt == "saturday":
            a = tt.get((ln, nm, d, "holiday"))     # 시간표는 토요일을 휴일에 합쳐 저장했다
            dt = "holiday(토요일 대체)"
        if a is None or a["first"] is None:
            agg["시간표에 조합 없음"] += 1
            continue
        agg["대조함"] += 1
        for label, src, mine in (("첫차", to_min(r["FSTT_HRM"]), a["first"]),
                                 ("막차", to_min(r["LSTTM_HRM"]), a["last"])):
            if src is None:
                continue
            gap = mine - src
            b = "일치(±2분)" if abs(gap) <= 2 else "±10분 이내" if abs(gap) <= 10 else "10분 초과"
            diffs[(label, b)] += 1
            if abs(gap) > 10 and len(samples) < 12:
                dest = a["last_dest"] if label == "막차" else ""
                samples.append((ln, nm, d, dt, label, mmss(src), mmss(mine), gap, dest or ""))
    say(f"소스 {len(fl):,}행 · " + " · ".join(f"{k} {v:,}" for k, v in agg.items()))
    say()
    say("| 항목 | 일치(±2분) | ±10분 이내 | 10분 초과 |")
    say("|---|---|---|---|")
    for label in ("첫차", "막차"):
        say(f"| {label} | {diffs[(label,'일치(±2분)')]:,} | {diffs[(label,'±10분 이내')]:,} | {diffs[(label,'10분 초과')]:,} |")
    if samples:
        say()
        say("10분 초과 표본:")
        say()
        say("| 노선 | 역 | dir | 요일 | 항목 | 광장 | 시간표 | 차이(분) | 시간표 막차 행선지 |")
        say("|---|---|---|---|---|---|---|---|---|")
        # 2026-09-10: 표본을 모아 두고 출력하는 줄이 빠져 있었다. 헤더만 나오고
        # 47건이 그대로 버려졌다. C4 는 '무더기로 나오면 의심하라'는 검사인데
        # 정작 무엇이 어긋났는지 볼 수가 없었다. 07번 방 전처리 결과서의 재료이기도 하다.
        for ln, nm, d, dt, label, src, mine, gap, dest in samples:
            say(f"| {ln} | {nm} | {d} | {dt} | {label} | {src} | {mine} | {gap:+d} | {dest or '-'} |")

    say()
    say("두 소스는 **다른 기관이 만든 다른 시점의 시간표**다. 몇 분 차이는 정상이다. "
        "10분 초과가 무더기로 나오면 dir·day_type 매핑이나 **시각 정규화**를 의심한다 — "
        "차이가 큰 음수(시간표가 훨씬 이름)로 몰리면 자정 넘긴 열차가 24 시로 정규화되지 "
        "않아 min/max 가 뒤집힌 것이다.")
    say()
    for label in ("첫차", "막차"):
        tot = sum(diffs[(label, b)] for b in ("일치(±2분)", "±10분 이내", "10분 초과"))
        over = diffs[(label, "10분 초과")]
        if tot and over / tot > 0.2:
            say(f"→ **{label} 불일치 {over:,}/{tot:,} ({over / tot:.0%}) — 허용 20% 초과.**")
            fail += 1
    if fail == 0:
        say("→ 불일치 비율이 허용 범위 안이다.")
say()

# ── C5. 막차가 어디까지 가는가 ──────────────────────────────────────
say("## C5. 막차 열차의 행선지 — 단축운행 확인")
say()
say("두 소스의 '막차' 정의가 다를 수 있다. 열린데이터광장 첫차막차(OA-15492)는 **종착역까지 "
    "완주하는 마지막 열차**를 주고, 역별 시간표(OA-101·TAGO)는 **그 역을 떠나는 모든 열차**를 "
    "준다. 시간표 쪽이 더 늦으면 그 열차가 단축운행일 수 있다. "
    "판정기가 그 열차를 잡고 '탈 수 있다'고 말했는데 목적지 전에 내려주면 틀린 판정이다.")
say()
dest_ct = collections.Counter()
for k, a in tt.items():
    if a["last"] is not None:
        dest_ct[(k[0], a["last_dest"])] += 1
say("| 노선 | 막차 행선지 | 조합 수 |")
say("|---|---|---|")
for (ln, dest), c in sorted(dest_ct.items(), key=lambda x: (-x[1]))[:15]:
    say(f"| {ln} | {dest or '(없음)'} | {c:,} |")
say()
no_dest = sum(c for (ln, dest), c in dest_ct.items() if not dest)
say(f"행선지가 비어 있는 조합 {no_dest:,} / {sum(dest_ct.values()):,}")
say()
# 2026-09-11: 세기만 하고 **어느 조합인지 안 내고 있었다.** C4 에 있던 것과 같은 종류다
# (통계만 찍는 검사는 절반만 하는 것). 02번 방은 이 조합들을 근거없음으로 내려야 하는데
# 목록이 없으면 회귀 케이스로 못 쓴다. 노선별로 묶어 내고 표본을 보여 준다.
nd = collections.defaultdict(list)
for k, a in tt.items():
    if a["last"] is not None and not a["last_dest"]:
        nd[k[0]].append(f"{k[1]}·{k[2]}·{k[3]}")
if nd:
    say("행선지 없는 조합 — 노선별 (02번 방이 근거없음으로 내려야 하는 자리)")
    say()
    say("| 노선 | 조합 | 표본 |")
    say("|---|---|---|")
    for ln in sorted(nd, key=lambda x: -len(nd[x])):
        v = sorted(nd[ln])
        say(f"| {ln} | {len(v)} | {', '.join(v[:4])}{' …' if len(v) > 4 else ''} |")
    say()
# 위 표는 상위 15개만 잘랐다. 나머지가 몇 개인지 밝혀 둔다 — 잘린 줄 모르면 전부인 줄 안다.
if len(dest_ct) > 15:
    say(f"※ 위 표는 조합 수 상위 15개다. 전체 (노선, 행선지) 조합은 {len(dest_ct):,}개다.")
    say()
say("→ 판정기가 막차를 쓸 때 **`dest_nm` 이 목적지 방향인지 확인해야 한다.** "
    "행선지를 안 보면 단축운행 열차를 완주 열차로 오인한다. (02번 방)")
say()

# ── C6. 행선지가 진행 방향에 있는가 ─────────────────────────────────
say("## C6. `dest_nm` 이 정말 행선지인가 — 역 순서 표와 대조")
say()
say("소스가 주는 '행선지' 필드를 그대로 믿으면 안 된다는 것을 2026-09-11 에 알았다. "
    "**열린데이터광장 OA-101 의 `SUBWAYSNAME` 은 행선지가 아니라 시발역이다** — "
    "7호선 휴일 '석남행'으로 적힌 열차가 석남 05:28 → 부평구청 05:33 → … → 도봉산 07:29 로 "
    "석남에서 **출발**한다. 그대로 두면 2·7호선 휴일 34,387행의 막차 행선지 판정이 통째로 뒤집힌다. "
    "이 검사가 그걸 잡는다.")
say()
say("방법: 한 (소스, 노선, 방향, 요일, 행선지) 묶음 안에서 역들을 노선 순서(`fr_order`)로 세우고 "
    "출발 시각이 그 순서를 따라 오르는지 내리는지 본다. 그러면 진행 방향이 나온다. "
    "행선지가 진행 방향의 **끝**에 있어야 한다. **시작**에 있으면 그 필드는 시발역이다. "
    "순환선은 이 방법이 안 통하므로 뺀다.")
say()
ORDER = MOB / "line_station_order_v1.json"
if not ORDER.exists():
    say(f"→ `{ORDER.name}` 이 없어 건너뛴다. `build_line_station_order_v1.py` 를 먼저 돌린다.")
    say()
else:
    import statistics as _st
    _D = json.loads(ORDER.read_text(encoding="utf-8"))
    _pos = {ln: {x["station_nm"]: x["fr_order"] for x in v["stations"]} for ln, v in _D["lines"].items()}
    _alias = _D.get("dest_alias", {})
    _loop = {ln for ln, v in _D["lines"].items() if v["is_loop"]}

    def _rd(ln, d):
        if not d:
            return None
        return d if d in _pos.get(ln, {}) else _alias.get(ln, {}).get(d)

    _g = collections.defaultdict(lambda: collections.defaultdict(list))
    with TT.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["line"] in _loop:
                continue
            t = to_min(r.get("dep_time"))
            d = _rd(r["line"], r.get("dest_nm"))
            if t is None or not d:
                continue
            _g[(r["source"], r["line"], r["dir"], r["day_type"], d)][r["station_nm"]].append(t)

    _res = collections.Counter(); _bad = []
    for k, stmap in _g.items():
        src, ln, dr, day, dest = k
        pts = sorted((_pos[ln][s], _st.median(v)) for s, v in stmap.items()
                     if s in _pos[ln] and len(v) >= 3)
        if len(pts) < 4:
            continue
        ups = sum(1 for i in range(len(pts) - 1) if pts[i + 1][1] > pts[i][1])
        if max(ups, len(pts) - 1 - ups) < 0.8 * (len(pts) - 1):
            continue                                  # 단조가 아니면 판단 보류
        asc = ups > len(pts) - 1 - ups
        lo, hi = pts[0][0], pts[-1][0]
        dfr = _pos[ln].get(dest)
        if dfr is None:
            continue
        end, start = (hi, lo) if asc else (lo, hi)
        good = abs(dfr - end) <= abs(dfr - start)
        _res[(src, "행선지" if good else "시발역")] += 1
        if not good:
            _bad.append((src, ln, dr, day, dest, len(pts)))
    say("| 소스 | 판정 | 묶음 수 |")
    say("|---|---|---|")
    for kk, vv in sorted(_res.items()):
        say(f"| {kk[0]} | {kk[1]} | {vv} |")
    say()
    if _bad:
        say("**진행 방향의 시작에 행선지가 있는 묶음** (필드가 시발역일 가능성):")
        say()
        say("| 소스 | 노선 | dir | 요일 | 적힌 행선지 | 역 수 |")
        say("|---|---|---|---|---|---|")
        for b in _bad[:15]:
            say(f"| {b[0]} | {b[1]} | {b[2]} | {b[3]} | {b[4]} | {b[5]} |")
        say()
    _oa = _res[("seoul_opendata_OA-101", "시발역")]
    if _oa:
        fail += 1
        say(f"→ **실패.** 열린데이터광장 묶음 {_oa}개에서 행선지가 진행 방향의 시작에 있다. "
            "`build_timetable_v1.py` 의 행선지 복원(`seoul_dest_map`)이 동작하는지 확인할 것.")
    else:
        say("→ 소스별로 행선지가 전부 진행 방향의 끝에 있다. 남은 소수 예외는 분기 접합부에서 "
            "이 방법이 방향을 잘못 잡는 경우다(경의선 서울역 지선 등).")
    say()

# ── C7. 역 영문명 결함 ──────────────────────────────────────────────
# 왜 여기 있나: 영문명은 좌표와 **다른 소스**(열린데이터광장 OA-15442)에서 오고, 결함 29역을
#   보정표로 덮었다. station_coords 를 다시 빌드할 때 보정표를 못 읽으면 조용히 원본으로 돌아간다.
#   판정은 안 깨지고 answer 의 병기 역명만 틀린다 — 그래서 회귀에 넣는다.
say("## C7. 역 영문명 결함")
say()
COORDS = MOB / "station_coords.json"
if not COORDS.exists():
    say(f"→ 건너뜀 — `{COORDS.name}` 이 없다.")
    say()
else:
    # consistency_check.py 가 mobility_scripts/ 에 있든 mobility_scripts/collect/ 에 있든 mobility_scripts/ 를 찾도록 둘 다 넣는다
    _here = Path(__file__).resolve().parent
    for _p in (_here, _here.parent):
        if (_p / "check_station_names.py").exists():
            sys.path.insert(0, str(_p))
            break
    try:
        from check_station_names import (defects as _en_defects, conflicts as _en_conflicts,
                                         KNOWN as _en_known)
    except Exception as _e:                       # noqa: BLE001
        _en_defects = None
        say(f"→ 건너뜀 — `check_station_names.py` 를 불러올 수 없다: {_e}")
        say()
    if _en_defects:
        _st = json.loads(COORDS.read_text(encoding="utf-8"))["stations"]
        _kinds = collections.Counter()
        _hits = []
        for _k, _v in sorted(_st.items()):
            _d = _en_defects(_v.get("station_nm_en"))
            if not _d:
                continue
            for _x in _d:
                _kinds[_x] += 1
            _hits.append((_k, sorted(_d), _v.get("station_nm_en")))
        # 사람이 판정한 4건 — 보정이 걸려 있어야 한다(원본과 값이 달라야 한다)
        _lost = [_k for _k in _en_known
                 if _st.get(_k, {}).get("station_nm_en") == _st.get(_k, {}).get("station_nm_en_src")]
        _grade = collections.Counter(_v.get("station_nm_en_grade") or "(없음)" for _v in _st.values())
        say(f"역 {len(_st)}개 · 규칙 결함 **{len(_hits)}역** · 등급 "
            + " · ".join(f"{_k} {_v}" for _k, _v in _grade.most_common()))
        say()
        if _hits:
            say("| 역 | 결함 | 값 |")
            say("|---|---|---|")
            for _k, _ks, _val in _hits[:20]:
                say(f"| {_k} | {','.join(_ks)} | `{_val}` |")
            say()
        # ★ 축이 하나 더 있다 — 같은 한글 역명인데 노선별로 영문이 갈리는 것(환승역).
        #   값 하나씩 보면 둘 다 규칙을 통과하므로 위 검사로는 절대 안 잡힌다.
        _conf = _en_conflicts(_st)
        say(f"역 단위 일관성 — 같은 한글 역명인데 영문이 갈리는 역 **{len(_conf)}역**")
        say()
        if _conf:
            say("| 역 | 영문명 | 노선 |")
            say("|---|---|---|")
            for _nm, _m in _conf.items():
                for _en, _ks in sorted(_m.items()):
                    say(f"| {_nm} | `{_en}` | {', '.join(_k.split('|')[0] for _k in _ks)} |")
            say()
        if _hits or _lost or _conf:
            fail += 1
            if _hits:
                say(f"→ **실패.** 결함 {len(_hits)}역 — "
                    + ", ".join(f"{_k} {_v}" for _k, _v in _kinds.most_common()) + ". "
                    "`config/mobility/station_nm_en_fix.json` 이 읽혔는지, 소스에 새 결함이 들어왔는지 본다.")
            if _lost:
                say(f"→ **실패.** 사람이 판정한 보정이 사라졌다: {', '.join(_lost)}. "
                    "보정표를 못 읽은 채 재빌드됐을 가능성이 크다.")
            if _conf:
                say(f"→ **실패.** 같은 역인데 영문명이 갈린 역 {len(_conf)}개. 환승역은 같은 역이다 — "
                    "한 화면에 두 표기가 같이 나오면 고객은 다른 역으로 읽는다. 보정표 `역별` 로 하나를 고른다.")
        else:
            say("→ 규칙 결함 0역 · 역 단위 일관성 0건. 사람이 판정한 4건도 보정이 남아 있다.")
        say()

# ── C8. 역 좌표 — 인접역 겹침 · 보정표 ─────────────────────────────
# 왜: 원본 표준데이터에서 마곡 행이 발산 좌표(7 m), 이촌(4호선) 행이 신용산 좌표(14 m)를 들고 있었다(19번 발견 · 25번 원인).
#   값 하나씩 보면 멀쩡한 서울 좌표라 어떤 범위 검사에도 안 걸리고, 출구표에서 그 역만 출구 0개가 되어 조용히 역 좌표로 잰다.
#   9/21 전수: 정상 역 중 다른 역명끼리 가장 가까운 쌍도 200 m 밖 → 50 m 는 오탐 없이 잡는 선.
say("## C8. 역 좌표 — 다른 역명끼리 50 m 안 · 좌표 보정표")
say()
ADJ_M = 50
COORD_FIX = Path(__file__).resolve().parents[2] / "config" / "mobility" / "station_coord_fix.json"
if not COORDS.exists():
    say(f"→ 건너뜀 — `{COORDS.name}` 이 없다.")
    say()
else:
    import math as _m
    _st = json.loads(COORDS.read_text(encoding="utf-8"))["stations"]
    def _dist(a, b):
        dy = (b[0] - a[0]) * 111_320
        dx = (b[1] - a[1]) * 111_320 * _m.cos(_m.radians((a[0] + b[0]) / 2))
        return _m.hypot(dx, dy)
    _pt = {}
    for _v in _st.values():
        _pt.setdefault(_v["station_nm"], (_v["lat"], _v["lng"]))
    _nms = sorted(_pt)
    _adj = sorted((round(_dist(_pt[a], _pt[b])), a, b) for i, a in enumerate(_nms) for b in _nms[i + 1:]
                  if _dist(_pt[a], _pt[b]) <= ADJ_M)
    _fx = json.loads(COORD_FIX.read_text(encoding="utf-8")).get("역별", {}) if COORD_FIX.exists() else {}
    _lostc = [_k for _k, _f in _fx.items()
              if _k in _st and (abs(_st[_k]["lat"] - _f["lat"]) > 1e-6 or abs(_st[_k]["lng"] - _f["lng"]) > 1e-6)]
    say(f"역명 {len(_nms)}개 · 다른 역명끼리 {ADJ_M} m 안 **{len(_adj)}쌍** · 좌표 보정표 {len(_fx)}키 중 미반영 **{len(_lostc)}**")
    say()
    if _adj:
        say("| 거리 | 역 | 역 |")
        say("|---:|---|---|")
        for _d, _a, _b in _adj:
            say(f"| {_d} m | {_a} | {_b} |")
        say()
    if _adj or _lostc:
        fail += 1
        if _adj:
            say(f"→ **실패.** 서로 다른 역이 {ADJ_M} m 안에 겹친다 — 원본 행이 옆 역 좌표를 들고 있을 가능성이 크다. "
                "원본 다른 행·OSM 역 노드로 대조해 `config/mobility/station_coord_fix.json` 에 넣는다.")
        if _lostc:
            say(f"→ **실패.** 보정표 좌표가 결과에 없다: {', '.join(_lostc)}. 보정표를 못 읽은 채 재빌드됐을 가능성이 크다.")
    else:
        say(f"→ 겹침 0쌍 · 보정표 {len(_fx)}키 반영됨.")
    say()

# ── 마무리 ─────────────────────────────────────────────────────────
say("## 결론")
say()
say(f"실패한 검사 **{fail}건**" if fail else "**모든 검사 통과.**")
REPORT.parent.mkdir(parents=True, exist_ok=True)
REPORT.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"\n리포트 → {REPORT}")
sys.exit(1 if fail else 0)
