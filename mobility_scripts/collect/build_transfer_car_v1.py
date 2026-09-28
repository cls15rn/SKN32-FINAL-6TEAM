# mobility_scripts/collect/build_transfer_car_v1.py — 환승 칸(빠른 환승 칸-문) 표 v1 (46번 방 층1 · 2026-09-25)
#
# 소스: 국토교통부_철도역 빠른 환승 정보 (공공데이터포털 15151816 · 이용허락범위 제한 없음 · 연 1회)
#       raw/mobility/car_position/molit_fast_transfer_*.csv  (cp949 · 10컬럼)
#       · 국가철도공단 노선별 「환승정보」(15041087·15041072·15041064·15041088 …)는 **같은 행에서 종착역명만 뺀 판**이다
#         (행 수 477·12·75·34 가 통합본 운영기관별 행 수와 같다) → 보충 소스가 아니다. 받지 않는다.
#       · 보충: 서울교통공사_서울 도시철도 환승정보 (15098252 · 1~8호선 역 · 2026-09-01 · 방면 = 인접역)
#         raw/mobility/car_position/seoulmetro_transfer_*.csv 가 있으면 합친다(아래 「보충」 절의 병합 규칙). 없으면 통합본만.
#
# ★ 칸 값은 **표시 전용** — 판정에 안 쓴다. 표에 없는 쌍은 값을 안 낸다(가까운 칸·계단 근접 칸으로 대체 금지).
# ★ 방향 키는 「다음 역」이다. 우리 legs 는 {line, from, to} 라 방향 = 그 노선에서 환승역 다음 역(진행 방향)으로
#   바꿔 둬야 조회가 된다. 원천의 종착역명은 종착역(1호선 「소요산」)과 인접역(2호선 「대림(구로구청)」)이 섞여 있어
#   line_station_order_v1 의 간선으로 환승역 → 종착역 경로의 첫 칸을 구한다. 순환선에서 인접역이 아닌 종착역은
#   방향이 둘 다 가능하므로 값을 안 낸다(dir_ambiguous_loop).
# ★ 차량순서 1 이 **진행 방향 맨 앞 칸**이라는 해석은 원천에 명시가 없다 → 확인 안 한 것(25 층3 검수로).
#
# 출력: processed/mobility/transfer_car_v1.json · transfer_car_v1_report.md
import csv, json, re, sys, collections, hashlib
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
RAW = RAW_MOBILITY / "car_position"
OUT_DIR = PROCESSED / "mobility"
OUT = OUT_DIR / "transfer_car_v1.json"
REPORT = OUT_DIR / "transfer_car_v1_report.md"

SRC_URL = "https://www.data.go.kr/data/15151816/fileData.do"
SRC_REGISTERED = "2025-11-10"          # 포털 등록일(2026-09-25 확인)
PORTAL_CHECKED_AT = "2026-09-25"       # 포털 메타(행 수·갱신 주기·이용허락)를 본 날

# 수도권 운영기관 — 원천·환승 쪽 둘 다 이 안이어야 한다(부산·대구·김해·자기부상은 우리 역 표 밖)
CAP_OPS = {"서울교통공사", "코레일", "공항철도주식회사", "네오트랜스주식회사", "서울시메트로9호선주식회사",
           "인천교통공사", "우이신설경전철주식회사", "의정부경량전철주식회사", "용인경량전철주식회사"}
# 원천 노선명 → 우리 노선 코드(station_coords · line_station_order)
LINE = {"1호선": "01호선", "2호선": "02호선", "3호선": "03호선", "4호선": "04호선", "5호선": "05호선",
        "6호선": "06호선", "7호선": "07호선", "8호선": "08호선", "9호선": "09호선",
        "경의중앙": "경의선", "수인분당": "수인분당선", "경춘": "경춘선", "경강": "경강선",
        "신분당선": "신분당선", "공항철도": "공항철도", "인천1호선": "인천선", "인천2호선": "인천2호선",
        "우이신설": "우이신설경전철", "의정부경전철": "의정부경전철", "용인에버라인": "용인경전철"}
# 원천 역명 → 우리 역명 (노선별로 이름이 다른 역) — 괄호 부역명은 norm() 이 먼저 뗀다
ST_ALIAS = {("총신대입구", "07호선"): "이수", ("이수", "04호선"): "총신대입구"}
# 2호선 순환: 인접역이 아닌 방면 표기는 **시청–충정로 이음매를 넘지 않는 쪽**으로 읽는다(= 역 순번 fr_order 증감).
#   근거는 원천 자신 — 왕십리 「시청」·「충정로」 두 표기가 서로 다른 칸을 가진다(최단으로 읽으면 둘 다 상왕십리로 접힌다).
#   이음매 규칙으로 읽으면 2호선 방향 접힘이 0 이 된다(리포트 「방향 접힘」 절). 지선(성수·신정) 쪽은 최단이 반대쪽의 1/3 이하일 때만.
# 6호선 응암 순환은 **한 방향 운행**이라 방면 표기(연신내 「불광」·「디지털미디어시티」)가 진행 방향이 아니다 → 안 읽는다.
LOOP_RATIO = 3
# (GPT 대조 1) 6호선 응암 순환 구간 역은 인접역 표기여도 안 읽는다 — 한 방향 운행이라 우리 무방향 간선으로는 진행 방향을 보장 못 한다.
# 타고 온 노선·환승 노선 어느 쪽이든 이 구간 6호선이면 그 행을 뺀다(두 원천 모두).
LOOP6 = {"응암", "역촌", "불광", "독바위", "연신내", "구산"}
# 편성 량수 상한 — **빼는 데만** 쓴다(차량순서 > 량수면 그 행은 틀린 값이라 안 낸다). 값을 만드는 데는 안 쓴다.
# 근거 등급 추정(널리 알려진 편성 · 운영사 공지 미대조). 인천2 검암 「6-4」처럼 이웃 노선 값을 옮겨 적은 행을 잡는다.
FORMATION = {"01호선": 10, "02호선": 10, "03호선": 10, "04호선": 10, "05호선": 8, "06호선": 8, "07호선": 8,
             "08호선": 6, "09호선": 8, "공항철도": 6, "신분당선": 6, "수인분당선": 8, "경의선": 10, "경춘선": 8,
             "경강선": 4, "인천선": 8, "인천2호선": 2, "우이신설경전철": 2, "의정부경전철": 2, "용인경전철": 2,
             "서해선": 8, "신림선": 3, "김포도시철도": 2, "GTX-A": 8}


def norm(s):
    """괄호 부역명 제거 + 공백 정리. '총신대입구(이수)' → '총신대입구', '신창(순천향대)' → '신창'."""
    if s is None:
        return None
    s = re.sub(r"\(.*?\)", "", s.strip()).strip()
    return s or None


def blank(s):
    s = (s or "").strip()
    return s or None


# ── 입력 ───────────────────────────────────────────────────────────
srcs = sorted(RAW.glob("molit_fast_transfer_*.csv"))
if not srcs:
    sys.exit(f"소스가 없다: {RAW}/molit_fast_transfer_*.csv")
src = srcs[-1]
raw_bytes = src.read_bytes()
src_md5 = hashlib.md5(raw_bytes).hexdigest()
text = raw_bytes.decode("cp949")
rows = list(csv.DictReader(text.splitlines()))
HEAD = ["철도운영기관명", "노선명", "역명", "종착역명", "환승철도운영기관명", "환승선", "환승이후역명", "환승기점역명",
        "차량순서", "차량출입문번호"]
if list(rows[0].keys()) != HEAD:
    sys.exit(f"컬럼이 다르다: {list(rows[0].keys())}")
downloaded_at = datetime.fromtimestamp(src.stat().st_mtime, KST).isoformat(timespec="seconds")

sc = json.loads((OUT_DIR / "station_coords.json").read_text(encoding="utf-8"))
lo = json.loads((OUT_DIR / "line_station_order_v1.json").read_text(encoding="utf-8"))

on_line = collections.defaultdict(set)            # 역명 → 노선들 (34 역 표)
for v in sc["stations"].values():
    on_line[v["station_nm"]].add(v["line"])
for _ln, _L in lo["lines"].items():                # 좌표 없는 역(신길온천 등)도 노선 순서표에 있으면 역으로 본다
    for _x in _L["stations"]:
        on_line[_x["station_nm"]].add(_ln)

adj = {}                                          # 노선 → 역 → 인접역들 (line_station_order 간선)
loop = {}
for ln, L in lo["lines"].items():
    g = collections.defaultdict(set)
    for e in L["edges"]:
        g[e["a"]].add(e["b"]); g[e["b"]].add(e["a"])
    adj[ln] = g
    loop[ln] = bool(L.get("is_loop"))
MAIN2 = {x["station_nm"]: x["fr_order"] for x in lo["lines"]["02호선"]["stations"] if not x.get("is_spur")}


def our_station(nm, line):
    """원천 역명 → 우리 역 표의 이름. 그 노선에 없으면 None."""
    n = norm(nm)
    if n is None:
        return None
    n = ST_ALIAS.get((n, line), n)
    return n if line in on_line.get(n, ()) else None


def next_toward(line, st, toward):
    """line 위에서 st 에서 toward(종착역 또는 인접역) 쪽 다음 역. (다음역, 사유)"""
    g = adj.get(line)
    t = norm(toward)
    if g is None or st not in g:
        return None, "no_line_graph"
    if t is None:
        return None, "dir_missing"
    t = ST_ALIAS.get((t, line), t)
    if t == st:
        return None, "dir_is_self"                # 이 역이 종착 — 진행 방향이 없다
    if t not in g:
        return None, "dir_not_on_line"
    if t in g[st]:
        return t, "adjacent"
    # BFS — 첫 칸별 최단 거리
    dist = {}
    for first in g[st]:
        seen = {st, first}; q = collections.deque([(first, 1)])
        while q:
            u, d = q.popleft()
            if u == t:
                dist[first] = d; break
            for w in g[u]:
                if w not in seen:
                    seen.add(w); q.append((w, d + 1))
    if not dist:
        return None, "dir_unreachable"
    if len(dist) > 1 and loop[line]:
        if line == "02호선":
            if st in MAIN2 and t in MAIN2:
                want = MAIN2[st] + (1 if MAIN2[t] > MAIN2[st] else -1)
                hit = [n for n in g[st] if MAIN2.get(n) == want]
                if len(hit) == 1:
                    return hit[0], "loop_seam_rule"
            best = sorted(dist.items(), key=lambda x: x[1])
            if best[0][1] * LOOP_RATIO <= best[1][1]:
                return best[0][0], "loop_shortest"
        return None, "dir_ambiguous_loop"         # 순환선 — 양쪽 다 닿는다
    if len(dist) > 1:
        # 트리(지선)에서는 한 첫 칸만 닿는다. 둘 이상이면 그래프에 고리가 있다 — 최단이 유일할 때만 쓴다
        best = sorted(dist.items(), key=lambda x: x[1])
        if best[0][1] == best[1][1]:
            return None, "dir_ambiguous_tie"
        return best[0][0], "path_shortest"
    return next(iter(dist)), "path"


# ── 기점 해석 대조(1차 패스) ─────────────────────────────────────
# 환승기점역명은 노선마다 뜻이 다르다(대부분 「그 방향 열차가 가는 쪽 끝」, 코레일·우이신설 일부는 「출발 쪽 끝」).
# 이후역명과 기점이 둘 다 있는 행에서 「가는 쪽」으로 읽은 다음역이 이후역과 **전부 맞는 환승선만** 기점을 단독으로 쓴다.
base_check = collections.defaultdict(collections.Counter)   # 환승선 → {True: n, False: n}
for r in rows:
    if blank(r["철도운영기관명"]) not in CAP_OPS or blank(r["환승철도운영기관명"]) not in CAP_OPS:
        continue
    tl = LINE.get(blank(r["환승선"]))
    if tl is None or not blank(r["환승이후역명"]) or not blank(r["환승기점역명"]):
        continue
    stt = our_station(r["역명"], tl)
    if stt is None:
        continue
    na, _ = next_toward(tl, stt, r["환승이후역명"])
    nb, _ = next_toward(tl, stt, r["환승기점역명"])
    if na is None or nb is None:
        base_check[tl]["uncomparable"] += 1
        continue
    base_check[tl][na == nb] += 1
# (GPT 대조 6) 기점 단독 사용은 **하지 않는다** — 일치율은 비교 가능한 행에서만 잰 값이라 기점만 있는 행의 뜻을 보장 못 한다.
# 위 집계는 진단용으로만 리포트에 남긴다.
BASE_OK = set()


# ── 행 단위 변환 ───────────────────────────────────────────────────
excluded = collections.Counter()
excluded_rows = collections.defaultdict(list)
cands = []
max_car = collections.defaultdict(int)
for i, r in enumerate(rows, start=2):             # 2 = CSV 줄 번호(머리 1줄)
    op, top = blank(r["철도운영기관명"]), blank(r["환승철도운영기관명"])
    ln_raw, tln_raw = blank(r["노선명"]), blank(r["환승선"])
    def drop(reason, note=""):
        excluded[reason] += 1
        excluded_rows[reason].append({"csv_line": i, "역명": r["역명"], "노선명": r["노선명"], "환승선": r["환승선"],
                                      "종착역명": r["종착역명"], "note": note})
    if op not in CAP_OPS or top not in CAP_OPS:
        drop("out_of_capital"); continue
    if ln_raw is None or blank(r["역명"]) is None:
        drop("station_or_line_missing"); continue
    line, to_line = LINE.get(ln_raw), LINE.get(tln_raw)
    if line is None or to_line is None:
        drop("line_unmapped", f"{ln_raw}/{tln_raw}"); continue
    car, door = blank(r["차량순서"]), blank(r["차량출입문번호"])
    if car is None or door is None:
        drop("car_door_missing"); continue
    if not (car.isdigit() and door.isdigit()):
        drop("car_door_not_int", f"{car}-{door}"); continue
    if int(car) > FORMATION.get(line, 99) or int(door) > 4 or int(car) < 1 or int(door) < 1:
        drop("car_door_out_of_range", f"{ln_raw} {car}-{door} (편성 {FORMATION.get(line)})"); continue
    st = our_station(r["역명"], line)
    if st is None:
        drop("station_not_on_line", f"{norm(r['역명'])}@{line}"); continue
    st_to = our_station(r["역명"], to_line)
    if st_to is None:
        drop("station_not_on_transfer_line", f"{norm(r['역명'])}@{to_line}"); continue
    if (line == "06호선" and st in LOOP6) or (to_line == "06호선" and st_to in LOOP6):
        drop("loop6_excluded", f"{st}@{line}→{to_line}"); continue
    nxt, why = next_toward(line, st, r["종착역명"])
    if nxt is None and why != "dir_is_self":
        drop(why, f"{st}@{line}→{r['종착역명']}"); continue
    # (GPT 대조 2) 종착 행은 그 역이 노선 끝(이웃 하나)일 때만 — 중간역에서 leg 가 끝난다고 열차가 종착하는 게 아니다
    if why == "dir_is_self" and len(adj[line][st]) != 1:
        drop("terminating_internal", f"{st}@{line}"); continue
    # 환승선 방향: 환승이후역명(인접역)만 쓴다. 기점 단독은 안 쓴다(GPT 대조 6).
    # (GPT 대조 3) 방향이 비어 있는 것은 「어느 방향이든 같은 칸」의 근거가 아니다 → `*` 폴백을 없앤다.
    #   단 환승역이 환승선의 끝(이웃 하나)이면 갈 방향이 하나뿐이라 그 이웃으로 정한다(노선 위상).
    a_raw, b_raw = blank(r["환승이후역명"]), blank(r["환승기점역명"])
    if a_raw:
        to_next, to_why = next_toward(to_line, st_to, a_raw)
        if to_next is None:
            drop("to_" + to_why, f"{st_to}@{to_line}→{a_raw}"); continue
    elif len(adj[to_line][st_to]) == 1:
        to_next, to_why = next(iter(adj[to_line][st_to])), "to_line_end"
    elif b_raw:
        drop("to_base_only_not_used", f"{st_to}@{to_line} 기점 {b_raw}"); continue
    else:
        drop("to_dir_missing", f"{st_to}@{to_line}"); continue
    # 들어오는 쪽 앞 역(prev) — legs 에서 바로 나오는 값. 이웃이 둘인 역에서만 정해진다(분기역·종착은 None)
    nb_all = adj[line][st]
    if nxt is not None:
        rest = nb_all - {nxt}
        prv = next(iter(rest)) if len(rest) == 1 else None
    else:
        prv = next(iter(nb_all)) if len(nb_all) == 1 else None
    max_car[line] = max(max_car[line], int(car))
    cands.append({
        "station_nm": st, "line": line, "prev_nm": prv, "next_nm": nxt, "arrive_terminating": why == "dir_is_self",
        "to_line": to_line, "to_station_nm": st_to, "to_next_nm": to_next,
        "car": int(car), "door": int(door),
        "src": {"csv_line": i, "operator": op, "toward_raw": blank(r["종착역명"]),
                "to_operator": top, "to_after_raw": blank(r["환승이후역명"]), "to_base_raw": blank(r["환승기점역명"]),
                "dir_how": why, "to_dir_how": to_why},
    })

# ── 키 병합 · 중복 · 충돌 ──────────────────────────────────────────
def key_of(c):
    return "|".join([c["station_nm"], c["line"], c["next_nm"] or "(종착)", c["to_line"], c["to_next_nm"] or "*"])

# 방향 접힘 — 한 (역, 노선)에서 서로 다른 원천 방면 표기 둘이 같은 다음역으로 읽힐 때.
#   · 같은 환승(환승선·환승 다음역)에서 표기끼리 **값이 다르면** 두 표기가 실제로는 다른 방향이라는 증거다
#     (경의중앙 용산 동쪽 역의 「문산」·「용산」 — 둘 다 서쪽으로 읽히는데 칸이 다르다) → 우리 환산으로 가를 수 없다
#     → 그 (역, 노선)의 **그 표기 행 전부** 뺀다. 한쪽 표기만 있는 환승도 방향을 틀리게 붙였을 수 있어서다.
#   · 값이 전부 같으면 같은 방향의 다른 이름이다(양재 3호선 「수서」·「오금」 → 매봉) → 합친다.
lab = collections.defaultdict(lambda: collections.defaultdict(set))   # (역,노선) → 다음역 → {표기}
vals_by = collections.defaultdict(lambda: collections.defaultdict(set))  # (역,노선,다음역,환승선,환승다음) → 표기 → 값
for c in cands:
    lab[(c["station_nm"], c["line"])][c["next_nm"]].add(c["src"]["toward_raw"])
    vals_by[(c["station_nm"], c["line"], c["next_nm"], c["to_line"], c["to_next_nm"])][c["src"]["toward_raw"]].add(
        (c["car"], c["door"]))
fold_bad, fold_list = set(), []
for sl, m in lab.items():
    for n, ts in m.items():
        if len(ts) < 2:
            continue
        diff = any(len({frozenset(v) for v in vb.values()}) > 1
                   for k, vb in vals_by.items() if k[:3] == (sl[0], sl[1], n) and len(vb) > 1)
        fold_list.append(f"{sl[0]}@{sl[1]}: {sorted(ts)} → {n} · {'값 다름 → 뺌' if diff else '값 같음 → 합침'}")
        if diff:
            fold_bad |= {(sl, t) for t in ts}
kept = []
for c in cands:
    if ((c["station_nm"], c["line"]), c["src"]["toward_raw"]) in fold_bad:
        excluded["dir_label_fold"] += 1
        excluded_rows["dir_label_fold"].append({"csv_line": c["src"]["csv_line"], "역명": c["station_nm"],
                                                "노선명": c["line"], "환승선": c["to_line"],
                                                "종착역명": c["src"]["toward_raw"], "note": f"→{c['next_nm']}"})
    else:
        kept.append(c)
cands = kept
fold_list.sort()

groups = collections.defaultdict(list)
for c in cands:
    groups[key_of(c)].append(c)

# 같은 키에 값이 여럿일 때 둘로 가른다.
#   · 원천 방면 표기(종착역명)가 서로 다른데 같은 다음역으로 모였다 → **우리 환산이 두 방향을 한 방향으로 접은 것**
#     (경의중앙 용산 동쪽 역의 「문산」·「용산」 — 둘 다 서쪽으로 읽힌다) → 값을 안 낸다(dir_label_collision)
#   · 원천 표기가 같은데 칸이 둘 → 운영기관이 **두 위치를 같이 공표**한 것(정자 신분당 1-2 · 6-4 — 계단 둘) → 둘 다 싣는다
entries, conflicts, dup_rows, multi = {}, [], 0, 0
for k, cs in groups.items():
    vals = sorted({(c["car"], c["door"]) for c in cs})
    labels = {(c["src"]["toward_raw"], c["src"]["to_after_raw"], c["src"]["to_base_raw"]) for c in cs}
    if len(vals) > 1:
        # (GPT 대조 7) 표기가 같아도 값이 둘이면 「계단 두 곳」인지 「정정 전후」인지 행만으로 못 가른다 → 뺀다
        conflicts.append({"key": k, "values": [f"{a}-{b}" for a, b in vals], "raw_labels": sorted("/".join(x or "-" for x in l) for l in labels),
                          "csv_lines": [c["src"]["csv_line"] for c in cs],
                          "kind": "label_differs" if len(labels) > 1 else "same_label_multi_value"})
        excluded["dir_label_collision" if len(labels) > 1 else "same_label_multi_value"] += len(cs)
        continue
    dup_rows += len(cs) - len(vals)
    multi += len(vals) > 1
    c = cs[0]
    entries[k] = {kk: c[kk] for kk in ("station_nm", "line", "prev_nm", "next_nm", "arrive_terminating", "to_line",
                                        "to_station_nm", "to_next_nm")}
    entries[k]["positions"] = [{"car": a, "door": b, "car_door": f"{a}-{b}"} for a, b in vals]
    entries[k]["src"] = {**{kk: vv for kk, vv in c["src"].items() if kk != "csv_line"},
                         "csv_lines": [x["src"]["csv_line"] for x in cs]}
    ops_ = {x["src"]["operator"] for x in cs}
    if len(ops_) > 1:                               # (GPT 대조 8) 운영기관이 섞이면 병합 규칙의 기준이 없다
        entries[k]["src"]["operator"] = None; entries[k]["src"]["operators"] = sorted(ops_)
excluded["duplicate_same_value(merged)"] = dup_rows

# ── 보충: 서울교통공사_서울 도시철도 환승정보(15098252) ──────────────────
# 1~8호선 역에서의 환승(상대 노선은 서해선·신림선·경춘 등 포함). 방면 = **인접역 이름**, 환승종료역 = 환승선 **다음 역 코드**라
# 방향이 곧게 나온다. 「하차위치(호차/문)」 = 타고 온 열차에서 내릴 칸(= 우리 환승 칸). 「All」 = 어느 칸이든.
# 환승 승차위치·소요시간은 이 표에 안 싣는다(승차 칸은 층 밖 · 소요시간은 19 transfer_walk 대조용).
# 병합(2026-09-25 본인 결정 「운영기관 자기 노선 우선」):
#   두 소스 키가 같고 값이 같다 → 둘 다 출처로 · 다르면 타고 온 노선(A)의 그 역 운영기관이 서울교통공사면 이 파일(2026-09 · 자기 노선 · 최신),
#   아니면 통합본(그 노선 운영기관 공표). 어긋남은 전부 `merge_disagreements` 에 남긴다. 한쪽에만 있으면 그대로.
SM_URL = "https://www.data.go.kr/data/15098252/fileData.do"
SM_LINE = {**{str(i): f"0{i}호선" for i in range(1, 10)}, "인천2": "인천2호선"}
sm_src = sorted(RAW.glob("seoulmetro_transfer_*.csv"))
sm_meta, sm_entries, sm_excl, sm_excl_rows = None, {}, collections.Counter(), []
merge_dis, merge_stat = [], collections.Counter()
if sm_src:
    f = sm_src[-1]
    b = f.read_bytes()
    try:
        t = b.decode("cp949")
    except UnicodeDecodeError:
        t = b.decode("utf-8-sig")
    srows = list(csv.DictReader(t.splitlines()))
    SH = ["고유번호", "환승시작역", "환승시작역 코드", "환승시작 호선", "하차 열차 방면", "하차위치(호차)", "하차위치(문)",
          "환승종료역", "환승 열차 방면", "환승 승차위치(호차)", "환승 승차위치(문)", "소요시간"]
    if list(srows[0].keys()) != SH:
        sys.exit(f"15098252 컬럼이 다르다: {list(srows[0].keys())}")
    sm_meta = {"name": "서울교통공사_서울 도시철도 환승정보", "url": SM_URL, "portal_modified": "2026-09-02",
               "portal_checked_at": PORTAL_CHECKED_AT, "license": "이용허락범위 제한 없음", "file": f.name,
               "file_md5": hashlib.md5(b).hexdigest(),
               "file_downloaded_at": datetime.fromtimestamp(f.stat().st_mtime, KST).isoformat(timespec="seconds"),
               "rows": len(srows)}
    by_cd = collections.defaultdict(set)             # 역 코드 → {(노선, 역명)}
    for ln, Lx in lo["lines"].items():
        for x in Lx["stations"]:
            if x.get("station_cd"):
                by_cd[x["station_cd"]].add((ln, x["station_nm"]))
    for v in sc["stations"].values():
        by_cd[v["station_cd"]].add((v["line"], v["station_nm"]))
    sgroups = collections.defaultdict(list)
    for r in srows:
        def sdrop(reason, note=""):
            sm_excl[reason] += 1
            sm_excl_rows.append({"고유번호": r["고유번호"], "reason": reason, "note": note})
        A = SM_LINE.get(r["환승시작 호선"].strip(), r["환승시작 호선"].strip())
        hit = [nm for ln, nm in by_cd.get(r["환승시작역 코드"].strip(), ()) if ln == A]
        if A not in adj or len(hit) != 1:
            sdrop("start_unmapped", f"{r['환승시작역']}@{A}"); continue
        S = hit[0]
        end = by_cd.get(r["환승종료역"].strip(), set())
        if len(end) != 1:
            sdrop("end_code_unmapped", r["환승종료역"]); continue
        (B, bn), = end
        S_B = our_station(S, B)
        if S_B is None or B not in adj:
            sdrop("station_not_on_transfer_line", f"{S}@{B}"); continue
        if (A == "06호선" and S in LOOP6) or (B == "06호선" and S_B in LOOP6):
            sdrop("loop6_excluded", f"{S}@{A}→{B}"); continue
        nxt, why = next_toward(A, S, r["하차 열차 방면"].replace("방면", ""))
        if nxt is None:
            sdrop(why, f"{S}@{A}→{r['하차 열차 방면']}"); continue
        tnx, twhy = next_toward(B, S_B, bn)
        if tnx is None:
            sdrop("to_" + twhy, f"{S_B}@{B}→{bn}"); continue
        if norm(r["환승 열차 방면"].replace("방면", "")) != bn:
            sdrop("to_label_ne_code", f"{r['환승 열차 방면']} vs {bn}"); continue
        car, door = r["하차위치(호차)"].strip(), r["하차위치(문)"].strip()
        if car == "All" and door == "All":
            pos = (None, None)
        elif car.isdigit() and door.isdigit():
            if not (1 <= int(car) <= FORMATION.get(A, 99) and 1 <= int(door) <= 4):
                sdrop("car_door_out_of_range", f"{A} {car}-{door}"); continue
            pos = (int(car), int(door))
        else:
            sdrop("car_door_not_int", f"{car}-{door}"); continue
        rest = adj[A][S] - {nxt}
        prv = next(iter(rest)) if len(rest) == 1 else None
        k = "|".join([S, A, nxt, B, tnx])
        sgroups[k].append({"station_nm": S, "line": A, "prev_nm": prv, "next_nm": nxt, "arrive_terminating": False,
                           "to_line": B, "to_station_nm": S_B, "to_next_nm": tnx, "pos": pos,
                           "id": r["고유번호"], "raw": (r["하차 열차 방면"], r["환승 열차 방면"]),
                           "how": (why, twhy)})
    for k, cs in sgroups.items():
        vals = sorted({c["pos"] for c in cs}, key=lambda x: (x[0] or 0, x[1] or 0))
        if len(vals) > 1:
            sm_excl["conflict_same_key"] += len(cs)
            sm_excl_rows.append({"고유번호": [c["id"] for c in cs], "reason": "conflict_same_key",
                                 "note": f"{k} {vals}"}); continue
        c = cs[0]
        e = {kk: c[kk] for kk in ("station_nm", "line", "prev_nm", "next_nm", "arrive_terminating", "to_line",
                                   "to_station_nm", "to_next_nm")}
        a_, d_ = vals[0]
        e["positions"] = ([{"car": None, "door": None, "car_door": "All", "any_car": True}] if a_ is None
                          else [{"car": a_, "door": d_, "car_door": f"{a_}-{d_}"}])
        e["src_sm"] = {"ids": [x["id"] for x in cs], "toward_raw": c["raw"][0], "to_toward_raw": c["raw"][1],
                       "dir_how": c["how"][0], "to_dir_how": c["how"][1]}
        sm_entries[k] = e
    # 병합
    drop_keys = []
    for k, e in sm_entries.items():
        if k not in entries:
            e["sources"] = ["seoulmetro_15098252"]
            entries[k] = e; merge_stat["seoulmetro_only"] += 1; continue
        m = entries[k]
        mv = sorted(p["car_door"] for p in m["positions"]); sv = sorted(p["car_door"] for p in e["positions"])
        m["src_sm"] = e["src_sm"]
        if mv == sv:
            m["sources"] = ["molit_15151816", "seoulmetro_15098252"]; merge_stat["both_agree"] += 1; continue
        if m["src"]["operator"] is None:
            merge_dis.append({"key": k, "molit": mv, "seoulmetro": sv, "line_operator": m["src"].get("operators"),
                              "chosen": None})
            drop_keys.append(k); merge_stat["disagree_operator_ambiguous_dropped"] += 1; continue
        own = m["src"]["operator"] == "서울교통공사"
        m["chosen_by_rule"] = "operator_own_line"
        merge_dis.append({"key": k, "molit": mv, "seoulmetro": sv, "line_operator": m["src"]["operator"],
                          "chosen": "seoulmetro" if own else "molit"})
        if own:
            m["positions_alt"] = {"molit_15151816": m["positions"]}
            m["positions"] = e["positions"]; m["sources"] = ["seoulmetro_15098252"]
            merge_stat["disagree_take_seoulmetro"] += 1
        else:
            m["positions_alt"] = {"seoulmetro_15098252": e["positions"]}
            m["sources"] = ["molit_15151816"]; merge_stat["disagree_take_molit"] += 1
    for k in drop_keys:
        entries.pop(k, None)
    for k, m in entries.items():
        m.setdefault("sources", ["molit_15151816"])
        if m["sources"] == ["molit_15151816"] and "positions_alt" not in m:
            merge_stat["molit_only"] += 1

# (GPT 대조 5) 2호선 이음매·최단 규칙으로 방향을 정한 통합본 값은 **다른 원천이 같은 키·같은 값을 낼 때만** 남긴다.
#   다른 원천 값으로 바뀐 키는 그 원천(방면 = 인접역)의 방향이라 남는다. 통합본만 있는 키는 뺀다.
loop_unverified = []
for k in list(entries):
    m = entries[k]
    for kk in ("sources",):
        m.setdefault(kk, ["molit_15151816"])
    hows = {m.get("src", {}).get("dir_how"), m.get("src", {}).get("to_dir_how")}
    if hows & {"loop_seam_rule", "loop_shortest"} and m["sources"] == ["molit_15151816"]:
        loop_unverified.append(k); entries.pop(k)
excluded["loop_rule_unverified"] = len(loop_unverified)

# (GPT 대조 11) 방향 환산 등급을 칸 값 등급과 따로 단다
#   원천 인접역 = 원천이 다음역을 인접역 이름으로 적었다 · 노선 위상 = 종착역 이름·노선 끝에서 간선으로 유일하게 정했다
#   두 원천 일치 = 방향 규칙이 들어갔지만 다른 원천이 같은 키에서 같은 값을 낸다
for k, m in entries.items():
    if m["sources"] == ["molit_15151816", "seoulmetro_15098252"]:
        m["map_grade"] = "두 원천 일치"
    elif m["sources"] == ["seoulmetro_15098252"]:
        m["map_grade"] = "원천 인접역"
    else:
        hs = {m["src"]["dir_how"], m["src"]["to_dir_how"]}
        m["map_grade"] = "원천 인접역" if hs <= {"adjacent"} else "노선 위상"
shadowed = 0
# ── 커버리지(34 역 표의 환승 쌍 대비) ─────────────────────────────
pairs_all = set()
for nm, ls in on_line.items():
    for a in ls:
        for b in ls:
            if a != b:
                pairs_all.add((nm, a, b))
covered = {(e["station_nm"], e["line"], e["to_line"]) for e in entries.values()}
missing = sorted(pairs_all - covered)
miss_by_line = collections.Counter(a for _, a, _ in missing)

now = datetime.now(KST).isoformat(timespec="seconds")
doc = {
    "schema": "transfer_car_v1",
    "built_at": now,
    "source_id": "molit_fast_transfer@15151816" + ("+seoulmetro_transfer@15098252" if sm_meta else ""),
    "source": {"name": "국토교통부_철도역 빠른 환승 정보", "url": SRC_URL, "portal_registered": SRC_REGISTERED,
               "portal_checked_at": PORTAL_CHECKED_AT, "update_cycle": "연 1회",
               "license": "이용허락범위 제한 없음", "file": src.name, "file_md5": src_md5,
               "file_downloaded_at": downloaded_at, "rows": len(rows)},
    "source_supplement": sm_meta,
    "merge_rule": ("같은 키 값 다름 → 타고 온 노선의 그 역 운영기관이 서울교통공사면 15098252(최신·자기 노선), "
                   "아니면 15151816(그 노선 운영기관 공표) · 다른 값은 positions_alt 에 · 2026-09-25 본인 결정"),
    "grade": "칸 값 = 확정(운영기관 공표) · 방향 환산 = 항목별 map_grade",
    "grade_value": "확정(운영기관 공표)",
    "checked_at": PORTAL_CHECKED_AT,
    "usage": ("표시 전용 — 판정에 안 쓴다. 조회 키 = 역|노선|다음역|환승선|환승선 다음역 · 조회는 **판정기가 고른 실제 역열**로만 한다"
              "(from/to 끝점만으로 경로를 다시 고르지 않는다 — 순환선·분기역에서 다른 길이 나온다). "
              "다음역 = 타고 온 경로에서 환승역 바로 뒤에 올 역이 아니라 **그 열차가 환승역 다음에 설 역** — 실제 역열의 환승역 직전 역 p 로 "
              "prev_nm == p 인 항목을 찾는다(prev_nm 이 None 인 분기역 항목은 조회하지 않는다). 환승선 다음역 = 환승 leg 역열의 둘째 역. "
              "'(종착)' 키는 노선 끝 역에서만 있다. 정확 일치가 없으면 **값을 안 낸다**(폴백 없음 · 가까운 칸·계단 근접 칸 대체 금지)."),
    "semantics": {"positions": "원천이 같은 방향에 칸을 둘 이상 공표하면 전부 싣는다(순서 = 칸 번호순 · 우열 아님) · car_door 「All」 = 어느 칸이든(15098252)",
                  "map_grade": "방향 환산 근거 — 원천 인접역 / 노선 위상 / 두 원천 일치",
                  "chosen_by_rule": "두 원천 값이 달라 병합 규칙(운영기관 자기 노선)으로 고른 것 — 정확성 검증이 아니다",
                  "formation_check": "편성 상한은 명백한 오기를 빼는 데만 쓴다 — 요청 열차 편성 적합성 확인이 아니다",
                  "sources": "값을 낸 원천 · 둘이면 두 원천 값이 같다 · positions_alt = 병합 규칙으로 버린 다른 원천 값(표시 안 함)",
                  "car": "차량순서(원천 그대로). 1 = 진행 방향 맨 앞 칸이라는 해석은 원천 미명시 — 확인 안 한 것",
                  "door": "차량출입문번호(원천 그대로 · 칸 안 문 번호)",
                  "prev_nm": "그 노선에서 환승역 바로 앞 역(들어오는 쪽) · 이웃이 둘인 역에서만 · 분기역은 None",
                  "next_nm": "그 노선에서 환승역 다음 역(진행 방향) · 원천 종착역명을 line_station_order_v1 간선으로 환산",
                  "arrive_terminating": "true 면 이 역이 종착인 열차(다음역 없음 · 키의 다음역 자리 '(종착)')",
                  "to_next_nm": "환승선에서 환승역 다음 역 · 원천 환승이후역명(인접역) · 환승역이 환승선 끝이면 그 이웃 · 기점 단독은 안 씀"},
    "merge": {**dict(merge_stat), "supplement_excluded": dict(sm_excl)},
    "stats": {"rows": len(rows), "used_rows": len(cands) - sum(len(c["csv_lines"]) for c in conflicts),
              "entries": len(entries), "multi_position_entries": multi, "excluded": dict(excluded),
              "collisions": len(conflicts),
              "map_grade": dict(collections.Counter(e["map_grade"] for e in entries.values())),
              "coverage_station_line_pairs_undirected": {"our_transfer_pairs": len(pairs_all), "covered": len(pairs_all) - len(missing),
                                 "missing": len(missing)}},
    "max_car_by_line": dict(sorted(max_car.items())),
    "loop_rule_unverified": loop_unverified,
    "base_semantics_check": {tl: {"agree": c[True], "disagree": c[False], "uncomparable": c["uncomparable"],
                                  "base_usable_alone": False}
                             for tl, c in sorted(base_check.items())},
    "missing_pairs": [f"{s}|{a}→{b}" for s, a, b in missing],
    "missing_by_line": dict(miss_by_line.most_common()),
    "dir_label_folds": fold_list,
    "conflicts": conflicts,
    "merge_disagreements": merge_dis,
    "supplement_excluded_rows": sm_excl_rows,
    "excluded_rows": {k: v for k, v in excluded_rows.items() if k != "out_of_capital"},
    "entries": dict(sorted(entries.items())),
}
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

# ── 리포트 ─────────────────────────────────────────────────────────
ops = collections.Counter((r["철도운영기관명"], r["노선명"]) for r in rows)
L = ["# 환승 칸 표 v1 (transfer_car_v1)", "",
     f"소스 {doc['source']['name']} ({SRC_URL}) · 포털 등록 {SRC_REGISTERED} · 확인 {PORTAL_CHECKED_AT} · "
     f"파일 `{src.name}` md5 `{src_md5}` · 받은 시각 {downloaded_at} · 생성 {now}", "",
     "등급 — 칸 값 **확정(운영기관 공표)** · 방향 환산은 항목별 `map_grade` · 병합 규칙으로 고른 값은 `chosen_by_rule`(정확성 검증 아님) · 표시 전용(판정에 안 씀)", "",
     f"표 {len(entries)}키 · 방향 환산 {doc['stats']['map_grade']} · 폴백(*) 없음", "",
     "## 운영기관·노선 집계(원천 그대로)", "", "| 운영기관 | 노선 | 행 |", "|---|---|---|"]
L += [f"| {o} | {l} | {n} |" for (o, l), n in sorted(ops.items(), key=lambda x: (x[0][0], str(x[0][1])))]
if sm_meta:
    L += ["", "## 보충 · 병합(15098252)", "",
          f"`{sm_meta['file']}` md5 `{sm_meta['file_md5']}` · {sm_meta['rows']}행 · 받은 시각 {sm_meta['file_downloaded_at']} → 키 {len(sm_entries)} · 뺀 행 {dict(sm_excl)}", "",
          "| 병합 | 키 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(merge_stat.items())]
    L += ["", f"겹치는 키에서 값이 다른 것 {len(merge_dis)} — 규칙: 타고 온 노선 운영기관이 서울교통공사면 15098252, 아니면 15151816. "
          "목록은 JSON `merge_disagreements`(다른 값은 `positions_alt`)."]
L += ["", "## 뺀 행(통합본)", "", "| 사유 | 행 |", "|---|---|"]
L += [f"| {k} | {v} |" for k, v in excluded.most_common()]
L += ["", "## 기점 해석 대조", "",
      "환승이후역명·환승기점역명이 둘 다 있는 행에서 기점을 「그 방향 열차가 가는 쪽 끝」으로 읽은 다음역이 이후역과 맞는지(환승선별). "
      "**진단용** — 기점은 단독으로 쓰지 않는다(GPT 대조 6: 비교 불능 행이 빠진 일치율이라 뜻을 보장 못 함).", "",
      "| 환승선 | 일치 | 불일치 | 비교 불능 |", "|---|---|---|---|"]
L += [f"| {tl} | {c[True]} | {c[False]} | {c['uncomparable']} |" for tl, c in sorted(base_check.items())]
L += ["", "## 34 역 표의 환승 쌍 대비", "",
      "※ **역·노선 쌍 커버(방향 무관)** — 방향별 조회 성공률이나 정확도가 아니다.", "",
      f"우리 표 환승 쌍(같은 역명·다른 노선, 방향 구분 없이) {len(pairs_all)} · 칸 값 있음 {len(pairs_all) - len(missing)} · "
      f"없음 {len(missing)}", "", "| 타는 노선 | 없는 쌍 |", "|---|---|"]
L += [f"| {k} | {v} |" for k, v in miss_by_line.most_common()]
L += ["", "없는 쌍은 **값을 안 낸다.** 목록은 JSON `missing_pairs`.", "",
      "## 노선별 최대 차량순서(눈검수용)", "", "| 노선 | 최대 칸 |", "|---|---|"]
L += [f"| {k} | {v} |" for k, v in sorted(max_car.items())]
L += ["", "## 방향 표기 접힘(값을 안 냄)", "", "한 역·노선에서 다른 방면 표기 둘이 같은 다음역으로 읽힌 것. 같은 환승에서 값이 다르면 그 표기 행 전부 뺐고, 같으면 합쳤다.", ""]
L += [f"- {x}" for x in fold_list]
if conflicts:
    L += ["", "## 남은 충돌(같은 키 · 원천 표기 다름 · 값 다름 — 값을 안 냄)", ""] + [
        f"- `{c['key']}` {c['values']} · 원천 표기(종착/이후/기점) {c['raw_labels']} (줄 {c['csv_lines']})" for c in conflicts]
REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"[transfer_car_v1] 원천 {len(rows)} → 키 {len(entries)} · 방향 {doc['stats']['map_grade']} · 뺀 행 {dict(excluded)}")
if sm_meta:
    print(f"  보충 15098252 {sm_meta['rows']}행 → 키 {len(sm_entries)} · 뺀 행 {dict(sm_excl)} · 병합 {dict(sorted(merge_stat.items()))}")
print(f"  커버 {len(pairs_all) - len(missing)}/{len(pairs_all)} · 출력 {OUT}")
