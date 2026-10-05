# datasets/mobility/scripts/build_station_gap_v1.py — 지하철 역간거리 표 v1 (54-2 · 2026-10-05)
#
# 무엇: 공표된 역간거리를 간선(노선 + 역A + 역B) 단위 표 하나로 모은다. 역 순서 표(line_station_order_v1.json)는
#       읽기만 하고 고치지 않는다. 요금 계산이 이 표를 읽게 잇는 일은 이 스크립트 밖(합치기 방)이다.
#
# 원천 ① 공공데이터포털 「국가철도공단_…역간거리」 CSV 17개 — 수동 다운로드 · cp949 · 이용허락범위 「제한 없음」
#          자리: DATA_DIR\travel\raw\mobility\station_gap\  (git 밖 · 파일 이름은 받은 그대로)
#          포털 페이지 확인 시각 2026-10-04 22:30 KST · 데이터셋 번호는 아래 DATASET
#       ② 김포골드라인 — 운영사 「이용안내 > 노선안내」 누적 km(값만 옮겨 적음 · 원본 저장 안 함 · 기준일·이용 조건 표기 없음)
#          https://gimpogoldline.com/?page_id=478 · 확인 2026-10-04
#
# ★「역간거리」 칸의 뜻이 파일마다 다르다(앞 역까지 / 다음 역까지 — 1호선 파일은 한 파일 안에서도 갈린다).
#   그래서 줄 순서를 믿지 않고 간선마다 고른다:
#     · 이웃한 두 줄의 값 둘을 후보로 둔다(둘 중 하나가 그 간선의 거리다).
#     · 두 파일에 같은 후보가 있고 좌표 직선거리와 어긋나지 않으면 → 「두 원천 일치」
#     · 그런 후보가 여럿이면 파일별로 적어 둔 칸의 뜻(CONV)으로 하나를 고른다 → 「두 원천 일치(칸 뜻으로 고름)」
#     · 파일이 하나뿐이면 칸의 뜻으로 고르고 직선거리 점검을 통과해야 한다 → 「단일 원천」
#     · 그 밖은 값을 내지 않는다(못 정함 — 조용히 고르지 않는다 · 실행하면 목록을 찍는다).
#   직선거리 점검: 직선 − 500 m ≤ 값 ≤ 직선 × 3 + 1 km. 좌표를 못 믿는 역(BADCOORD)이 낀 간선은 점검을 건너뛴다.
#   역 순서 표에 이미 거리가 있고 100 m 넘게 다르면 싣지 않는다(겹침 불일치 — 목록으로 찍는다).
#
# 등급: 원천 ① = 확정(공표 값) · n_sources 로 한 파일인지 두 파일인지 남긴다. 원천 ② = 추정(누적 0.1 km 반올림의 차 → ±0.1 km).
# 싣지 않는 것: 운영기관 경계 간선(두 줄이 한 묶음에 이웃하지 않아 위 규칙으로 못 정함) · 값이 빈 구간(9호선 언주~중앙보훈병원).
#
# 출력: station_gap_v1.jsonl — 한 줄 = 간선 하나. 칸: line · a · b(역 순서 표의 a·b 순서 그대로) · distance_m · grade ·
#       basis · n_sources · source(URL 목록) · source_file · as_of(원천 기준일 중 늦은 것) · checked_at · order_table_m(역 순서 표 값 · 없으면 null)
# 실행(저장소 루트): python datasets/mobility/scripts/build_station_gap_v1.py            → 정본 DATA_DIR\travel\processed\mobility\
#                    python datasets/mobility/scripts/build_station_gap_v1.py --out <파일> [--raw <폴더>] [--ref <폴더>]
import argparse
import collections
import csv
import io
import json
import math
import re
import sys
from pathlib import Path

CHECKED_AT = "2026-10-04T22:30+09:00"          # 포털 데이터셋 페이지(이용허락·수정일)를 본 시각
PORTAL = "https://www.data.go.kr/data/{}/fileData.do"
DATASET = {                                    # 파일 이름 조각 → 공공데이터포털 번호
    "코레일 역간거리": 15081858, "서울교통공사 역간거리": 15081860, "수도권1호선": 15041460, "수도권4호선": 15041350,
    "수도권5호선": 15041348, "수도권6호선": 15041297, "수도권8호선": 15041299, "수도권9호선": 15041298,
    "공항철도": 15041310, "경춘선": 15041295, "경강선": 15041296, "우이신설": 15081853, "인천1호선": 15081855,
    "인천2호선": 15081856, "인천교통공사": 15081857, "의정부경전철": 15081852, "에버라인": 15081850}

LINE = {"경의중앙": "경의선", "수인분당": "수인분당선", "경춘": "경춘선", "경강": "경강선", "서해선": "서해선", "공항": "공항철도",
        "에버라인": "용인경전철", "우이신설": "우이신설경전철", "의정부": "의정부경전철", "인천1호선": "인천선", "인천2호선": "인천2호선"}
ALIAS = {("우이신설경전철", "4.19민주묘지"): "4·19민주묘지", ("인천2호선", "서구청"): "서해구청", ("07호선", "총신대입구"): "이수"}
BADCOORD = {"양평", "신촌", "이촌"}             # 같은 이름의 다른 역 좌표가 섞인 역 — 직선 점검에서 뺀다

GIMPO = {"line": "김포도시철도", "url": "https://gimpogoldline.com/?page_id=478", "checked_at": "2026-10-04",
         "cum_km": [("양촌", 0.0), ("구래", 1.4), ("마산", 3.0), ("장기", 5.6), ("운양", 7.3), ("걸포북변", 10.7),
                    ("사우", 12.5), ("풍무", 14.1), ("고촌", 17.7), ("김포공항", 23.6)]}   # 페이지 표기 「사우(김포시청)역」 = 사우


def line_of(s):
    m = re.match(r"^(\d)호선", s)
    return f"0{m.group(1)}호선" if m else LINE.get(s)


def bare(s):
    return re.sub(r"\(.*?\)", "", s).strip()


def conv_of(fn, op, sn):
    """그 파일·묶음의 「역간거리」 칸이 가리키는 쪽 — prev(앞 줄 역까지) / next(다음 줄 역까지) / None(적어 둘 수 없음)."""
    if "서울교통공사 역간거리_2023" in fn:
        return "next" if sn == "2호선" else "prev"
    if "수도권4호선" in fn:
        return "next"
    if "수도권1호선" in fn:
        return "prev" if op == "서울교통공사" else None
    if any(x in fn for x in ("수도권5호선", "수도권6호선", "수도권8호선", "경춘선_", "공항철도_", "경강선_", "인천1호선 ")):
        return "prev"
    return "next"


def dataset_of(fn):
    for k, v in DATASET.items():
        if k in fn:
            return v
    return None


def meters(a, b, c, d):
    return math.hypot((c - a) * 111320, (d - b) * 111320 * math.cos(math.radians((a + c) / 2)))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def plausible(v, s):
    return s is None or (s - 500 <= v <= 3 * s + 1000)


def build(raw_dir: Path, ref_dir: Path):
    """(rows, undecided, conflicts) — 파일을 쓰지 않는다."""
    lo = json.loads((ref_dir / "line_station_order_v1.json").read_text(encoding="utf-8"))
    sc = json.loads((ref_dir / "station_coords.json").read_text(encoding="utf-8"))["stations"]
    adj, names = {}, {}
    for ln, d in lo["lines"].items():
        adj[ln] = {frozenset((e["a"], e["b"])): e for e in d["edges"]}
        names[ln] = {bare(s["station_nm"]): s["station_nm"] for s in d["stations"]}

    def straight(ln, a, b):
        if a in BADCOORD or b in BADCOORD:
            return None
        pa, pb = sc.get(f"{ln}|{a}"), sc.get(f"{ln}|{b}")
        if not pa or not pb or pa.get("lat") is None or pb.get("lat") is None:
            return None
        return meters(pa["lat"], pa["lng"], pb["lat"], pb["lng"])

    files = sorted(p for p in raw_dir.iterdir() if p.suffix.lower() == ".csv")
    if not files:
        sys.exit(f"원자료가 없다: {raw_dir}\\*.csv — 머리말의 데이터셋을 받아 둔다")
    cand = collections.defaultdict(dict)        # (노선, 간선) → {파일: {"vals": 후보 m, "decl": 칸 뜻으로 고른 m}}
    meta = {}
    for f in files:
        fn = f.name
        ds = dataset_of(fn)
        day = re.search(r"(\d{8})", fn)
        meta[fn] = {"url": PORTAL.format(ds) if ds else None,
                    "as_of": f"{day.group(1)[:4]}-{day.group(1)[4:6]}-{day.group(1)[6:]}" if day else None}
        rows = list(csv.reader(io.StringIO(f.read_bytes().decode("cp949"))))[1:]
        groups = collections.OrderedDict()
        for r in rows:
            if len(r) >= 4:
                groups.setdefault((r[0], r[1]), []).append(r)
        for (op, sn), rs in groups.items():
            ln = line_of(sn)
            if ln not in adj:
                continue
            st = []
            for r in rs:
                n = bare(r[2])
                n = n[:-1] if n.endswith("역") and n[:-1] in names[ln] else n
                st.append((ALIAS.get((ln, n)) or names[ln].get(n), num(r[3])))
            conv = conv_of(fn, op, sn)
            for (a, da), (b, db) in zip(st, st[1:]):
                if a is None or b is None:
                    continue
                k = frozenset((a, b))
                if k not in adj[ln]:
                    continue
                c = cand[(ln, k)].setdefault(fn, {"vals": set(), "decl": None})
                for v in (da, db):
                    if v is not None and v > 0:
                        c["vals"].add(round(v * 1000))
                dv = {"prev": db, "next": da}.get(conv)
                if dv:
                    c["decl"] = round(dv * 1000)

    rows_out, undecided, conflicts = [], [], []
    for (ln, k), fs in cand.items():
        e = adj[ln][k]
        s = straight(ln, e["a"], e["b"])
        seen = collections.Counter(v for c in fs.values() for v in c["vals"])
        multi = [v for v, n in seen.items() if n >= 2 and plausible(v, s)]
        decl = {c["decl"] for c in fs.values() if c["decl"]}
        val, basis = None, None
        if len(fs) >= 2 and len(multi) == 1:
            val, basis = multi[0], "두 원천 일치"
        elif len(fs) >= 2 and len(multi) > 1:
            d = [v for v in multi if v in decl]
            if len(d) == 1:
                val, basis = d[0], "두 원천 일치(칸 뜻으로 고름)"
        elif len(fs) == 1:
            d = [v for v in decl if plausible(v, s)]
            if len(d) == 1:
                val, basis = d[0], "단일 원천"
        detail = {fn: {"후보_m": sorted(c["vals"]), "칸뜻_m": c["decl"]} for fn, c in sorted(fs.items())}
        if val is None:
            undecided.append({"line": ln, "a": e["a"], "b": e["b"], "직선_m": None if s is None else round(s), "원천": detail})
            continue
        old = e.get("distance_m")
        if old is not None and abs(old - val) > 100:
            conflicts.append({"line": ln, "a": e["a"], "b": e["b"], "역순서표_m": old, "새원천_m": val, "basis": basis, "원천": detail})
            continue
        used = sorted(fn for fn, c in fs.items() if val in c["vals"])
        rows_out.append({"line": ln, "a": e["a"], "b": e["b"], "distance_m": val, "grade": "확정", "basis": basis,
                         "n_sources": len(used), "source": [meta[fn]["url"] for fn in used], "source_file": used,
                         "as_of": max((meta[fn]["as_of"] or "") for fn in used) or None, "checked_at": CHECKED_AT,
                         "order_table_m": old})

    g = GIMPO                                   # 누적 km 의 차 — 식: 역간 = 누적(뒤) − 누적(앞)
    for (a, ca), (b, cb) in zip(g["cum_km"], g["cum_km"][1:]):
        k = frozenset((a, b))
        if k not in adj.get(g["line"], {}):
            sys.exit(f"김포골드라인 간선이 역 순서 표에 없다: {a}–{b}")
        e = adj[g["line"]][k]
        rows_out.append({"line": g["line"], "a": e["a"], "b": e["b"], "distance_m": round((cb - ca) * 1000), "grade": "추정",
                         "basis": f"누적 km 의 차({cb} − {ca}) · 0.1 km 반올림 → ±0.1 km", "n_sources": 1, "source": [g["url"]],
                         "source_file": [], "as_of": None, "checked_at": g["checked_at"], "order_table_m": e.get("distance_m")})

    key = lambda r: (r["line"], r["a"], r["b"])  # noqa: E731
    return sorted(rows_out, key=key), sorted(undecided, key=key), sorted(conflicts, key=key)


def main(argv=None):
    ap = argparse.ArgumentParser(description="지하철 역간거리 표 v1 을 만든다")
    ap.add_argument("--raw", help="원자료 폴더(기본 DATA_DIR/travel/raw/mobility/station_gap)")
    ap.add_argument("--ref", help="line_station_order_v1.json · station_coords.json 이 있는 폴더(기본 정본 processed/mobility)")
    ap.add_argument("--out", help="출력 파일(기본 정본 processed/mobility/station_gap_v1.jsonl)")
    a = ap.parse_args(argv)
    import _paths                               # 경로는 실행할 때만 읽는다(.env)
    raw = Path(a.raw) if a.raw else _paths.RAW_MOBILITY / "station_gap"
    ref = Path(a.ref) if a.ref else _paths.PROCESSED / "mobility"
    rows, undecided, conflicts = build(raw, ref)
    if a.out:
        out = Path(a.out)
    else:
        _paths.ensure_dirs()                    # 정본에 쓸 때만 — DATA_DIR 이 없으면 여기서 멈춘다
        out = _paths.PROCESSED / "mobility" / "station_gap_v1.jsonl"
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    by = collections.Counter((r["grade"], r["basis"].split(" · ")[0].split("(")[0]) for r in rows)
    print(f"[station_gap_v1] {len(rows)}간선 → {out}")
    for (grade, basis), n in sorted(by.items()):
        print(f"  {grade} · {basis}: {n}")
    print(f"  역 순서 표에 거리가 없던 간선: {sum(1 for r in rows if r['order_table_m'] is None)}")
    print(f"못 정함 {len(undecided)}")
    for u in undecided:
        print("  ", json.dumps(u, ensure_ascii=False))
    print(f"겹침 불일치(역 순서 표와 100 m 넘게 다름 · 싣지 않음) {len(conflicts)}")
    for c in conflicts:
        print("  ", json.dumps(c, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
