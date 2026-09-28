# mobility_scripts/collect/bus_speed_observe.py — 버스 표정속도 실측 (관측 + 집계)
#
#   관측:  python mobility_scripts/collect/bus_speed_observe.py --observe --routes 01A 01B --interval 120
#   집계:  python mobility_scripts/collect/bus_speed_observe.py --build --interval 120
#
# 왜: 노선유형별 표정속도를 교통카드 통계에서 산출해 뒀지만(rules.bus.표정속도),
#     **순환 표본이 전체의 0.13%(4,617건) 뿐**이고 하필 시연의 남산 구간(01A·01B)이 순환이다.
#     그리고 그 통계는 하루 평균이라 시간대가 안 갈린다 — 판정이 가장 아픈 자리다.
#     직접 관측하면 둘을 같이 푼다.
#
# 방법: 노선의 모든 차량 위치를 주기적으로 받아 **같은 차량(plainNo)이 정류소 순번을 넘어간 시간**을
#     잰다. 거리는 우리가 이미 가진 sect_dist_m 누적합에서 뽑는다.
#     중간 정차는 그대로 포함된다 — 표정속도 정의가 그렇고 승객도 그걸 겪는다.
#
# ★ 집계에서 조심할 것: **순번이 안 늘어난 관측(= 그 사이 느렸다)을 버리면 안 된다.**
#   버리면 빠른 구간만 남아 속도가 부풀려진다. 합성 관측(진짜 18.0 km/h)으로 확인했더니
#   그 방식은 22.1 km/h 를 냈다 — 23% 과대. 그래서 차량의 연속 관측을 run 으로 묶고
#   run 안의 모든 간격을 **거리 0 까지 포함해** 누적한 뒤 총거리÷총시간으로 낸다.
#
# 선행: data.go.kr **15000332 서울특별시_버스위치정보조회** 활용신청(자동승인, 개발계정 1,000/일).
#   15000193(노선정보조회)과 **다른 서비스**다. 인증키는 같은 DATA_GO_KR_KEY 를 쓴다.
#   오퍼레이션 표시명은 getBusPosByRtidList 인데 실제 경로는 다를 수 있다(--op 로 바꾼다).
#
# 쿼터: 01A+01B 를 2분 간격으로 06:30~23:00 관측하면 496회/일. 1,000/일 안에 든다.
import argparse
import collections
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from _paths import RAW_MOBILITY, PROCESSED

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "final_project_cs", REPO):          # 31번 방 — 판정 패키지가 final_project_cs 아래로 갔다
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
try:
    # ★ 요일축은 판정기와 **같은 자**를 써야 한다. 여기서 따로 정의하면
    #   실측값은 holiday 로 쌓였는데 판정은 weekday 로 찾는 어긋남이 난다.
    from app.modules.travel_ops.mobility.engine.timeutil import day_type_of
    _HOL = set(json.loads((REPO / "final_project_cs" / "app" / "modules" / "travel_ops" / "mobility" / "engine" / "rules" / "holidays_2026_2027.json")
                          .read_text(encoding="utf-8"))["holidays"])
except Exception as _e:                                    # 관측만 할 때는 없어도 된다
    day_type_of, _HOL = None, set()

KST = timezone(timedelta(hours=9))
BASE = "http://ws.bus.go.kr/api/rest/buspos"
KEY = os.environ.get("DATA_GO_KR_KEY")
ROUTES = PROCESSED / "mobility" / "bus_route_v1.jsonl"
STOPS = PROCESSED / "mobility" / "bus_stops_v1.jsonl"
OUT = PROCESSED / "mobility" / "bus_speed_v1.json"
REPORT = PROCESSED / "mobility" / "bus_speed_v1_report.md"

# rules.bus.표정속도 의 통계값 — 실측과 대조해 보여 준다
STAT = {"간선": 15.8, "지선": 14.4, "마을": 12.4, "광역": 29.7, "순환": 23.5, "심야": 15.8}

ap = argparse.ArgumentParser()
ap.add_argument("--observe", action="store_true")
ap.add_argument("--build", action="store_true")
ap.add_argument("--routes", nargs="+", default=[], help="노선번호 (관측 대상)")
ap.add_argument("--interval", type=int, default=120, help="폴링 간격(초)")
ap.add_argument("--until", default="23:10", help="관측 종료 시각 HH:MM")
ap.add_argument("--op", default="getBusPosByRtid", help="오퍼레이션 경로")
ap.add_argument("--obs-file", nargs="+",
                help="집계할 관측 파일. 기본은 raw 의 bus_pos_obs_*.jsonl **전부** — "
                     "하루치만 집계하면 다른 요일 실측이 통째로 덮인다")
ap.add_argument("--min-min", type=int, default=20,
                help="요일축별 실측을 규칙 후보로 낼 최소 관측 분")
args = ap.parse_args()

TODAY = datetime.now(KST).date().isoformat()


def mask(s):
    return re.sub(r"(?i)(serviceKey)=[^&\s'\"]+", r"\1=***", str(s))


def load_routes():
    return {r["route_nm"]: r for r in
            (json.loads(l) for l in ROUTES.open(encoding="utf-8") if l.strip())}


# ── 관측 ────────────────────────────────────────────────────────────
if args.observe:
    if not KEY:
        raise SystemExit(".env 의 DATA_GO_KR_KEY 가 없다")
    rt = load_routes()
    targets = []
    for nm in args.routes:
        if nm not in rt:
            raise SystemExit(f"{nm} 이 bus_route_v1.jsonl 에 없다 — seoul_bus_collect.py 로 먼저 수집한다")
        targets.append((nm, rt[nm]["route_id"]))
    obs = RAW_MOBILITY / f"bus_pos_obs_{TODAY}.jsonl"
    eh, em = (int(x) for x in args.until.split(":"))
    calls = 0
    existed = obs.exists()
    print(f"관측 시작 · {[n for n, _ in targets]} · {args.interval}초 간격 · {args.until} 까지 → {obs.name}")
    # 첫 호출이 실패하면 빈 파일이 남아, 그걸로 --build 를 돌리면 결과가 빈 값으로 덮인다.
    # 그래서 종료 시 아무것도 안 썼고 원래 없던 파일이면 지운다.
    try:
        g = obs.open("a", encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"관측 파일을 못 연다: {e}")
    try:
        while True:
            now = datetime.now(KST)
            if (now.hour, now.minute) >= (eh, em):
                break
            for nm, rid in targets:
                try:
                    r = requests.get(f"{BASE}/{args.op}",
                                     params={"ServiceKey": KEY, "busRouteId": rid, "resultType": "json"},
                                     timeout=20)
                    body = r.json()
                except Exception as e:
                    print(f"  {now:%H:%M:%S} {nm} 실패 — {type(e).__name__}: {mask(e)}")
                    continue
                calls += 1
                if "status" in body and body.get("status") != 0:
                    raise SystemExit(f"API 오류 {body.get('status')}: {body.get('message')}  "
                                     f"→ 15000332 활용신청과 --op 경로를 확인한다")
                head = body.get("msgHeader", {})
                cd = str(head.get("headerCd"))
                if cd not in ("0", "4", "None"):
                    raise SystemExit(f"API 오류 {cd}: {head.get('headerMsg')}")
                items = (body.get("msgBody") or {}).get("itemList") or []
                ts = now.isoformat(timespec="seconds")
                for it in items:
                    so = str(it.get("sectOrd", "")).strip()
                    g.write(json.dumps({
                        "ts": ts, "route_nm": nm, "route_id": rid,
                        "plain_no": (it.get("plainNo") or "").strip(),
                        "veh_id": (it.get("vehId") or "").strip(),
                        "sect_ord": int(so) if so.isdigit() else None,
                        "stop_flag": (it.get("stopFlag") or "").strip(),
                    }, ensure_ascii=False) + "\n")
                print(f"  {now:%H:%M:%S} {nm} 차량 {len(items)}대 (누적 호출 {calls})")
                g.flush()
            time.sleep(args.interval)
    finally:
        g.close()
        if not existed and obs.stat().st_size == 0:
            obs.unlink()
            print("  (아무것도 기록하지 못해 빈 관측 파일을 지웠다)")
    print(f"관측 종료 · 호출 {calls}회 → {obs}")
    sys.exit()

if not args.build:
    raise SystemExit("--observe 또는 --build 중 하나가 필요하다")

# ── 집계 ────────────────────────────────────────────────────────────
if args.obs_file:
    obs_paths = [Path(x) for x in args.obs_file]
else:
    # ★ 기본을 '오늘 파일 하나'로 두면, 토요일에 집계한 순간 평일 실측이 사라진다.
    #   원본은 날짜별로 남아 있으므로 전부 읽어 요일축으로 나눠 담는 쪽이 안전하다.
    obs_paths = sorted(RAW_MOBILITY.glob("bus_pos_obs_*.jsonl"))
missing = [x for x in obs_paths if not x.exists()]
if missing:
    raise SystemExit(f"관측 파일이 없다 → {', '.join(str(x) for x in missing)}")
if not obs_paths:
    raise SystemExit(f"관측 파일이 하나도 없다 → {RAW_MOBILITY}/bus_pos_obs_*.jsonl")
if day_type_of is None:
    raise SystemExit("요일축을 못 읽었다 — final_project_cs/app/modules/travel_ops/mobility/engine/rules/holidays_2026_2027.json 과 PYTHONPATH 를 확인한다")
print("집계 대상 " + ", ".join(x.name for x in obs_paths))

# 노선별 정류장 누적거리 (sect_dist_m 는 직전 정류장으로부터의 거리)
cum = collections.defaultdict(dict)
last_seq = {}
tmp = collections.defaultdict(list)
for line in STOPS.open(encoding="utf-8"):
    if line.strip():
        s = json.loads(line)
        tmp[s["route_nm"]].append(s)
for nm, ss in tmp.items():
    ss.sort(key=lambda x: x["seq"])
    acc = 0
    for s in ss:
        acc += s.get("sect_dist_m") or 0
        cum[nm][s["seq"]] = acc
    last_seq[nm] = ss[-1]["seq"]

rows = []
for x in obs_paths:
    rows += [json.loads(l) for l in x.open(encoding="utf-8") if l.strip()]

def _dtype(ts):
    return day_type_of(datetime.fromisoformat(ts).date(), _HOL)

# 차량 묶음에 요일축을 넣는다 — 날짜가 다른 관측이 한 run 으로 이어지면 안 된다
by_veh = collections.defaultdict(list)
dates_by_dt = collections.defaultdict(set)
for r in rows:
    if r.get("sect_ord") and r.get("plain_no"):
        dt = _dtype(r["ts"])
        by_veh[(r["route_nm"], r["plain_no"], dt, r["ts"][:10])].append(r)
        dates_by_dt[dt].add(r["ts"][:10])

# 노선 → 요일축 → 시 → [m, s]
bucket = collections.defaultdict(lambda: collections.defaultdict(
    lambda: collections.defaultdict(lambda: [0.0, 0.0])))
drop = collections.Counter()
for (nm, veh, dtype, _d), rs in by_veh.items():
    rs.sort(key=lambda x: x["ts"])
    runs, cur, prev = [], [], None
    for r in rs:
        if prev is not None:
            gap = (datetime.fromisoformat(r["ts"]) - datetime.fromisoformat(prev["ts"])).total_seconds()
            if r["sect_ord"] < prev["sect_ord"] or gap > args.interval * 3:
                runs.append(cur)
                cur = []
        cur.append(r)
        prev = r
    runs.append(cur)
    for run in runs:
        # 기·종점은 회차 대기가 섞이므로 뺀다
        core = [x for x in run if 1 < x["sect_ord"] < last_seq.get(nm, 10 ** 9)]
        if len(core) < 2:
            drop["기·종점 제외 후 관측 1개 이하"] += 1
            continue
        for a, b in zip(core, core[1:]):
            d1, d2 = cum[nm].get(a["sect_ord"]), cum[nm].get(b["sect_ord"])
            if d1 is None or d2 is None or d2 < d1:
                drop["거리 없음"] += 1
                continue
            t1 = datetime.fromisoformat(a["ts"])
            dt = (datetime.fromisoformat(b["ts"]) - t1).total_seconds()
            if dt <= 0:
                drop["시간 0 이하"] += 1
                continue
            bk = bucket[nm][dtype][t1.hour]
            bk[0] += (d2 - d1)          # 0 인 간격도 그대로 넣는다 — 버리면 속도가 부풀려진다
            bk[1] += dt

rt = load_routes()
dates = sorted({d for ds in dates_by_dt.values() for d in ds})
result = {
    "source": "bus_pos_observed",
    "source_id": f"bus_speed_observed@{dates[-1] if dates else TODAY}",
    "observed_files": [x.name for x in obs_paths],
    "observed_dates": {k: sorted(v) for k, v in sorted(dates_by_dt.items())},
    "built_at": datetime.now(KST).isoformat(timespec="seconds"),
    "method": "같은 차량(plainNo)의 연속 관측을 run 으로 묶고, run 안의 모든 간격을 거리 0 까지 포함해 "
              "누적한 뒤 총거리÷총시간. 중간 정차 포함, 기·종점 구간 제외. "
              "순번이 안 늘어난 간격을 버리면 빠른 구간만 남아 속도가 부풀려지므로 버리지 않는다. "
              "요일축(weekday/holiday)은 app.modules.travel_ops.mobility.engine.timeutil.day_type_of 와 같은 자로 나눈다.",
    "grade": "추정", "routes": {},
}

def _agg(hours):
    td = sum(v[0] for v in hours.values())
    tt = sum(v[1] for v in hours.values())
    if tt <= 0:
        return None
    return {"speed_kmh": round(td / tt * 3.6, 1),
            "by_hour_kmh": {f"{h:02d}": round(v[0] / v[1] * 3.6, 1)
                            for h, v in sorted(hours.items()) if v[1] > 0},
            "observed_km": round(td / 1000, 1), "observed_min": round(tt / 60)}

lines = ["# 버스 표정속도 실측 v1", "",
         f"관측 {', '.join(x.name for x in obs_paths)} · 관측 행 {len(rows):,} · 생성 {result['built_at']}", "",
         "| 노선 | 유형 | 요일축 | 관측 km | 관측 분 | **표정속도** | 통계값 | 차이 |",
         "|---|---|---|---|---|---|---|---|"]
for nm, by_dt in sorted(bucket.items()):
    tp = rt.get(nm, {}).get("route_type_nm")
    st = STAT.get(tp)
    merged = collections.defaultdict(lambda: [0.0, 0.0])
    per_dt = {}
    for dtype, hours in sorted(by_dt.items()):
        a = _agg(hours)
        if a:
            per_dt[dtype] = a
        for h, v in hours.items():
            merged[h][0] += v[0]
            merged[h][1] += v[1]
    allv = _agg(merged)
    if not allv:
        continue
    result["routes"][nm] = {"route_type_nm": tp, "stat_kmh": st,
                            "by_day_type": per_dt, **allv}
    def _diff(v):
        return f"{v - st:+.1f}" if st else "—"
    for dtype, a in sorted(per_dt.items()):
        lines.append(f"| {nm} | {tp} | {dtype} | {a['observed_km']} | {a['observed_min']} | "
                     f"**{a['speed_kmh']}** | {st} | {_diff(a['speed_kmh'])} |")
    if len(per_dt) > 1:
        lines.append(f"| {nm} | {tp} | *합계* | {allv['observed_km']} | {allv['observed_min']} | "
                     f"*{allv['speed_kmh']}* | {st} | {_diff(allv['speed_kmh'])} |")

# ★ 판정기는 rules.bus.표정속도.노선별.value 가 {노선: {weekday: x, holiday: y}} 모양이면
#   요일축별로 골라 쓴다(verify_time.Verifier.bus_speed). 그 모양을 여기서 바로 만들어 둔다.
ready = {}
for nm, v in result["routes"].items():
    got = {dt: a["speed_kmh"] for dt, a in v["by_day_type"].items()
           if a["observed_min"] >= args.min_min}
    if got:
        ready[nm] = got
result["rules_ready"] = ready
result["rules_ready_note"] = (f"관측 {args.min_min}분 이상인 요일축만 담았다. "
                              f"rules.bus.표정속도.노선별.value 에 그대로 넣으면 "
                              f"통계_금지_노선 이 자동으로 풀린다.")

lines += ["", "## 시간대별 (km/h)", "",
          "| 노선 | 요일축 | " + " | ".join(f"{h:02d}" for h in range(6, 24)) + " |",
          "|---" * 20 + "|"]
for nm, v in sorted(result["routes"].items()):
    for dtype, a in sorted(v["by_day_type"].items()):
        lines.append(f"| {nm} | {dtype} | "
                     + " | ".join(str(a["by_hour_kmh"].get(f"{h:02d}", "")) for h in range(6, 24)) + " |")

lines += ["", "## 규칙에 넣을 값", "",
          "```json", json.dumps(ready, ensure_ascii=False, indent=1), "```", "",
          "`rules.bus.표정속도.노선별.value` 에 그대로 넣는다 — 판정기가 요일축별로 골라 쓰고,",
          f"`통계_금지_노선` 이 자동으로 풀린다. 관측 {args.min_min}분 미만인 요일축은 담지 않았다.", ""]
lines += ["## 버린 표본", ""] + ([f"- {k}: {v:,}" for k, v in drop.most_common()] or ["- 없음"]) + [
    "", "## 읽는 법", "",
    "- **표정속도는 총거리÷총시간**이다. 정의상 중간 정차가 포함된다.",
    "- 기·종점 구간은 회차 대기가 섞여 제외했다.",
    "- 통계값(`rules.bus.표정속도`)과 크게 다르면 그 유형 값을 의심한다 — 특히 순환은 표본이 0.13% 뿐이다.",
    "- **요일축을 섞지 않는다.** 토요일 관측을 평일 값으로 쓰면 남산 같은 관광 노선에서 크게 어긋난다.",
    "- 등급은 **추정**이다(우리 관측). 거리 `sect_dist_m` 만 확정이다.",
]

if not result["routes"]:
    # 빈 결과로 덮으면 이전 관측 결과가 사라진다. 원인을 알려주고 아무것도 쓰지 않는다.
    raise SystemExit(
        f"쓸 표본이 없다 — 관측 행 {len(rows):,}, 버린 표본 {sum(drop.values()):,}. "
        f"기존 {OUT.name} 을 덮지 않고 끝낸다."
        + ("\n  관측 파일이 비어 있다. --observe 가 실제로 응답을 받았는지 확인한다." if not rows else "")
    )

OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"노선 {len(result['routes'])} · 요일축 {sorted(dates_by_dt)} · "
      f"버린 표본 {sum(drop.values()):,}")
print(f"규칙 후보 {json.dumps(ready, ensure_ascii=False)}")
print(f"→ {OUT}\n→ {REPORT}")
