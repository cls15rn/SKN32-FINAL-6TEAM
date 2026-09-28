# mobility_scripts/collect/build_bus_seg_profile_v1.py — 41 버스 구간 통행시간 프로파일
#
# 소스: 서울시 노선별 정류장 구간별 평균 운행시간 정보(OA-21217 · TOPIS BMS · 공공누리 1유형)
#       https://data.seoul.go.kr/dataList/OA-21217/F/1/datasetView.do  (확인 2026-09-24 23:25)
#       파일 = 하루 × 구간 한 줄, 운행시간_00시~23시 24칸(초 · 그 시간대 평균) · 0 = 그 시간 운행 없음
# 산출: processed/mobility/bus_seg_profile_v1.jsonl.gz — 우리 717노선 연속 정류장 구간마다
#       요일형(weekday/holiday) × 시간(0~23) 의 n(날 수)·p10·p50·p90 (초)
#       + processed/mobility/bus_seg_profile_v1_report.md
#
# ★ 값의 뜻: p10/p50/p90 은 「날짜별 시간대 평균」의 분포다. 개별 운행의 p90 이 아니다(날 안 편차가 평균으로 눌린다).
# ★ 요일형은 판정기 timeutil 과 같은 자 — 평일이고 공휴일 아님 = weekday · 토·일·공휴일 = holiday
#   (공휴일 = rules/holidays_2026_2027.json · 천문연 특일정보).
# ★ 여러 파일에 같은 날짜가 있으면 먼저 읽은 파일(이름 순) 것만 쓴다(월 파일 ↔ 주 파일 겹침).
# ★ 머리줄이 예상과 다르면 멈춘다 — 조용히 다른 칸을 읽지 않게.
#
# 실행(저장소 루트):  python mobility_scripts/collect/build_bus_seg_profile_v1.py
#   --raw-dir  기본 DATA_DIR/travel/raw/mobility/bus_speed
#   --dry-run  산출 파일을 쓰지 않고 보고서 숫자만 찍는다
import argparse, collections, datetime as dt, glob, gzip, json, sys, zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _paths import RAW_MOBILITY, PROCESSED, REPO_ROOT
except Exception:  # 클라우드 시험용
    RAW_MOBILITY = PROCESSED = REPO_ROOT = None

HOURS = [f"운행시간_{h:02d}시" for h in range(24)]
HEADER = ["기준_날짜", "노선_ID", "출발_정류장_ID", "도착_정류장_ID", "운행시간", *HOURS,
          "출발_정류장_순서", "도착_정류장_순서"]
QS = (0.10, 0.50, 0.90)
SOURCE_ID = "seoul_OA-21217"
SOURCE_URL = "https://data.seoul.go.kr/dataList/OA-21217/F/1/datasetView.do"


def load_sections(stops_path):
    by = collections.defaultdict(list)
    names = {}
    with open(stops_path, encoding="utf-8") as f:
        for line in f:
            s = json.loads(line)
            by[s["route_id"]].append(s)
            names[s["route_id"]] = s["route_nm"]
    rows = []
    for rid, L in by.items():
        L.sort(key=lambda s: s["seq"])
        for a, b in zip(L, L[1:]):
            rows.append((rid, names[rid], a["station_id"], b["station_id"], a["seq"], b["seq"], b.get("sect_dist_m")))
    S = pd.DataFrame(rows, columns=["route_id", "route_nm", "from_id", "to_id", "from_seq", "to_seq", "dist_m"])
    S["sec"] = np.arange(len(S), dtype=np.int32)
    return S


def day_type_fn(holidays):
    hol = set(holidays)
    cache = {}

    def f(yyyymmdd):
        if yyyymmdd not in cache:
            d = dt.datetime.strptime(str(yyyymmdd), "%Y%m%d").date()
            cache[yyyymmdd] = "holiday" if (d.weekday() >= 5 or d.isoformat() in hol) else "weekday"
        return cache[yyyymmdd]
    return f


def read_long(raw_dir, S):
    """원천 행 → (구간, 날짜, 시간, 초). GPT 대조 6·7 반영(2026-09-25).
    ★ 조인: (노선, 출발 ID, 도착 ID) 쌍이 우리 노선에서 한 번뿐이면 그 구간, 두 번 이상(순환·왕복 공유 구간)이면
      원천 `출발_정류장_순서` 가 우리 `from_seq` 와 같은 것만. 순서가 안 맞는 반복 쌍은 버린다(짐작 안 함).
    ★ 같은 (구간, 날짜) 가 두 번 오면 24칸이 같으면 하나만, 다르면 그 (구간, 날짜) 를 통째로 버리고 센다.
    """
    pc = S.groupby(["route_id", "from_id", "to_id"]).sec.transform("size")
    J = S.assign(pair_n=pc)[["route_id", "from_id", "to_id", "from_seq", "sec", "pair_n"]]
    owner = {}
    stat = collections.Counter()
    files = sorted(glob.glob(str(Path(raw_dir) / "tpss_route_section_speedh_*.zip")))
    if not files:
        sys.exit(f"[중단] {raw_dir} 에 tpss_route_section_speedh_*.zip 이 없다")
    frames = []
    for z in files:
        fn = Path(z).name
        zf = zipfile.ZipFile(z)
        for n in zf.namelist():
            reader = pd.read_csv(zf.open(n), encoding="cp949", chunksize=400_000,
                                 dtype={"기준_날짜": str, "노선_ID": str, "출발_정류장_ID": str, "도착_정류장_ID": str})
            for ch in reader:
                if list(ch.columns) != HEADER:
                    sys.exit(f"[중단] {fn}/{n} 머리줄이 예상과 다르다: {list(ch.columns)[:8]}…")
                stat["raw_rows"] += len(ch)
                d = ch["기준_날짜"].astype(np.int32)
                for x in np.unique(d):
                    owner.setdefault(int(x), fn)
                keep = d.map(lambda x: owner[int(x)] == fn).to_numpy()
                stat["dup_date_rows"] += int((~keep).sum())
                ch = ch[keep].rename(columns={"노선_ID": "route_id", "출발_정류장_ID": "from_id", "도착_정류장_ID": "to_id"})
                m = ch.merge(J, on=["route_id", "from_id", "to_id"], how="inner")
                amb = m.pair_n > 1
                bad = amb & (m["출발_정류장_순서"] != m["from_seq"])
                stat["repeat_pair_rows_kept"] += int((amb & ~bad).sum())
                stat["repeat_pair_rows_dropped"] += int(bad.sum())
                stat["seq_mismatch_unique_pair"] += int((~amb & (m["출발_정류장_순서"] != m["from_seq"])).sum())
                m = m[~bad]
                frames.append(pd.DataFrame({"sec": m.sec.to_numpy(np.int32),
                                            "date": m["기준_날짜"].astype(np.int32).to_numpy(),
                                            **{f"h{h:02d}": m[HOURS[h]].to_numpy(np.float32) for h in range(24)}}))
        stat["files"] += 1
    W = pd.concat(frames, ignore_index=True)
    hcols = [f"h{h:02d}" for h in range(24)]
    v = W[hcols].to_numpy()
    stat["nonfinite_cells"] = int((~np.isfinite(v)).sum())
    stat["neg_cells"] = int((v < 0).sum())
    dup = W.duplicated(["sec", "date"], keep=False)
    if dup.any():
        D = W[dup]
        same = D.groupby(["sec", "date"])[hcols].nunique(dropna=False).max(axis=1) == 1
        conflict = same[~same].index
        stat["dup_sec_date_identical"] = int(same.sum())
        stat["dup_sec_date_conflict_dropped"] = len(conflict)
        W = W.drop_duplicates(["sec", "date"])
        if len(conflict):
            ci = pd.MultiIndex.from_frame(W[["sec", "date"]]).isin(conflict)
            W = W[~ci]
    v = W[hcols].to_numpy()
    ok = np.isfinite(v) & (v > 0)
    ii, hh = np.nonzero(ok)
    A = {"sec": W.sec.to_numpy()[ii], "date": W.date.to_numpy()[ii], "hour": hh.astype(np.int8), "t": v[ii, hh]}
    if len(A["t"]) == 0:
        sys.exit("[중단] 유효한 양수 값이 하나도 없다")
    return A, owner, stat, files


def grouped_quantiles(g, t, ngroups):
    """g: 그룹 번호(0..ngroups-1), t: 값. 그룹마다 n · QS 분위(선형 보간). 완전 벡터화."""
    o = np.lexsort((t, g))
    g, t = g[o], t[o]
    n = np.bincount(g, minlength=ngroups)
    start = np.concatenate([[0], np.cumsum(n)[:-1]])
    out = {"n": n}
    has = n > 0
    for q in QS:
        pos = start + q * (np.maximum(n, 1) - 1)
        lo = np.floor(pos).astype(np.int64)
        hi = np.minimum(lo + 1, start + np.maximum(n, 1) - 1)
        lo_c = np.clip(lo, 0, len(t) - 1); hi_c = np.clip(hi, 0, len(t) - 1)
        val = t[lo_c] + (t[hi_c] - t[lo_c]) * (pos - lo)
        out[q] = np.where(has, val, np.nan)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", default=str(RAW_MOBILITY / "bus_speed") if RAW_MOBILITY else None)
    ap.add_argument("--stops", default=str(PROCESSED / "mobility" / "bus_stops_v1.jsonl") if PROCESSED else None)
    ap.add_argument("--routes", default=str(PROCESSED / "mobility" / "bus_route_v1.jsonl") if PROCESSED else None)
    ap.add_argument("--holidays", default=str(REPO_ROOT / "final_project_cs/app/modules/travel_ops/mobility/engine/rules/holidays_2026_2027.json") if REPO_ROOT else None)
    ap.add_argument("--out", default=str(PROCESSED / "mobility" / "bus_seg_profile_v1.jsonl.gz") if PROCESSED else None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    S = load_sections(a.stops)
    routes = {}
    with open(a.routes, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line); routes[r["route_id"]] = r
    S["route_type_nm"] = S.route_id.map(lambda r: routes.get(r, {}).get("route_type_nm"))
    hol = json.load(open(a.holidays, encoding="utf-8"))["holidays"]
    dtf = day_type_fn(hol)

    A, owner, stat, files = read_long(a.raw_dir, S)
    dates = sorted(owner)
    span = pd.date_range(str(dates[0]), str(dates[-1])).strftime("%Y%m%d").astype(int)
    missing = [int(x) for x in span if int(x) not in owner]
    dt_of = {d: dtf(d) for d in dates}
    days_by_dt = collections.Counter(dt_of.values())
    hol_years = set(json.load(open(a.holidays, encoding="utf-8")).get("years") or [])
    out_years = sorted({d // 10000 for d in dates} - hol_years)
    if out_years:  # GPT 9 — 마지막 공휴일이 아니라 표가 덮는 해(years)로 본다
        sys.exit(f"[중단] 공휴일 표가 덮지 않는 해: {out_years}")

    DT = {"weekday": 0, "holiday": 1}
    dmap = {d: DT[dt_of[d]] for d in dates}
    dts = pd.Series(A["date"]).map(dmap).to_numpy(np.int8)
    nsec = len(S)
    g = (A["sec"].astype(np.int64) * 2 + dts) * 24 + A["hour"]
    Q = grouped_quantiles(g, A["t"], nsec * 48)
    n = Q["n"].reshape(nsec, 2, 24)
    P = {q: Q[q].reshape(nsec, 2, 24) for q in QS}

    # ── 보고 숫자 ──
    covered = n.sum(axis=(1, 2)) > 0
    S["covered"] = covered
    by_type = S.groupby("route_type_nm").covered.agg(["sum", "size"])
    route_cov = S.groupby("route_id").covered.mean()
    # GPT 15 — 「어느 요일·시간이든 한 번이라도 관측」 기준이다. 요청 시간대에 다 있다는 뜻이 아니다.
    last = S.to_seq == S.groupby("route_id").to_seq.transform("max")
    chain_full = S.groupby("route_id").covered.all()
    chain_excl_last = S[~last].groupby("route_id").covered.all()
    night = S.route_type_nm == "심야"
    night_cells = n[night.to_numpy()][:, :, [23, 0, 1, 2, 3, 4]]
    ratio = np.where(P[0.50] > 0, P[0.90] / P[0.50], np.nan)
    rep = {
        "source_id": SOURCE_ID, "source_url": SOURCE_URL, "checked_at": "2026-09-24T23:25+09:00",
        "files": [Path(f).name for f in files], "dates": [dates[0], dates[-1]], "n_dates": len(dates),
        "missing_dates_in_span": missing, "days_by_day_type": dict(days_by_dt),
        **{k: int(v) for k, v in stat.items()},
        "our_sections": nsec, "sections_covered": int(covered.sum()),
        "routes_total": int(S.route_id.nunique()), "routes_any": int((route_cov > 0).sum()),
        "routes_chain_full_any_obs": int(chain_full.sum()),
        "routes_chain_excl_last_any_obs": int(chain_excl_last.sum()),
        "coverage_meaning": "구간이 어느 요일형·시간이든 5일 미만 포함 한 번이라도 관측되면 covered — 요청 시간대 가용성 아님",
        "routes_none": sorted(routes[r]["route_nm"] for r in route_cov[route_cov == 0].index),
        "by_type": {k: [int(v["sum"]), int(v["size"])] for k, v in by_type.iterrows()},
        "night_route_cells_23_04_with_ge5_days": float((night_cells >= 5).mean()) if night.any() else None,
        "p90_over_p50_median": float(np.nanmedian(ratio)),
        "cells_n_ge5": int((n >= 5).sum()), "cells_n_1_4": int(((n >= 1) & (n < 5)).sum()),
    }
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if a.dry_run:
        return

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        meta = {"_meta": True, **rep, "unit": "sec", "value": "날짜별 시간대 평균 운행시간의 p10/p50/p90 (개별 운행 분위 아님)",
                "day_type_rule": "weekday = 평일·공휴일 아님 · holiday = 토·일·공휴일 (timeutil 과 같은 자)",
                "grade": "추정", "built_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for i, s in enumerate(S.itertuples(index=False)):
            if not s.covered:
                continue
            rec = {"route_id": s.route_id, "route_nm": s.route_nm, "from_id": s.from_id, "to_id": s.to_id,
                   "from_seq": int(s.from_seq), "to_seq": int(s.to_seq),
                   "dist_m": None if pd.isna(s.dist_m) else int(s.dist_m)}
            for k, j in DT.items():
                nn = n[i, j]
                if nn.sum() == 0:
                    continue
                rec[k] = {"n": nn.tolist(),
                          "p10": [None if x == 0 else round(float(v), 1) for x, v in zip(nn, P[0.10][i, j])],
                          "p50": [None if x == 0 else round(float(v), 1) for x, v in zip(nn, P[0.50][i, j])],
                          "p90": [None if x == 0 else round(float(v), 1) for x, v in zip(nn, P[0.90][i, j])]}
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    rp = out.with_name("bus_seg_profile_v1_report.md")
    rp.write_text("# bus_seg_profile_v1 — 41 산출 보고\n\n```json\n" + json.dumps(rep, ensure_ascii=False, indent=1) + "\n```\n",
                  encoding="utf-8")
    print("wrote", out, out.stat().st_size, "bytes ·", rp)


if __name__ == "__main__":
    main()
