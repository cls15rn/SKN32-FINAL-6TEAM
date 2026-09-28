# mobility_scripts/collect/build_airport_bus_v1.py — 인천공항 버스 원본 → 판정기용 시간표
# 실행: 저장소 루트에서  python mobility_scripts/collect/build_airport_bus_v1.py   (API 호출 없음)
# 출력: processed/mobility/airport_bus_v1.jsonl        출발 1건 = 1행 (지하철 timetable_v1 과 같은 축)
#       processed/mobility/airport_bus_stops.jsonl     노선별 경유 정류장
#       processed/mobility/airport_bus_v1_report.md    노선 수·시간표 제공 여부·권역
#
# ★핵심: t1wdayt / t1wt 는 배차간격이 아니라 **출발 시각 전체 목록**이다("0540, 0610, 0630, …").
#   지하철과 같은 정밀도라 이 노선들은 대기 시간이 추정이 아니라 시각 자체가 확정 근거가 된다.
#   다만 일부 노선은 시각 목록 대신 첫차·막차만 들어 있다 — 그건 timetable_available=false 로 구분한다.
#
# ★2026-09-21 (35번 방) 공항행 첫차·막차 — 원본 toawfirst/toawlast 는 명세상 「공항행첫차/막차」인데
#   180노선 중 179개에서 공항 **출발** 첫차·막차(T2종점행, T2 없으면 T1종점행)와 값이 같다.
#   이 스크립트의 매핑(toaw* → to_airport_*)은 명세대로이고, 어긋난 건 원본 값이다.
#   → 출발 쪽과 같으면 to_airport_first/last = None · to_airport_grade = 근거없음. 원본 값은 to_airport_raw 에 남긴다.
#   다를 때(영종8 하나)만 값을 두고 추정 — 명세 뜻대로 읽을 수 있지만 교차 확인은 없다.
# ★2026-09-21 (35번 방) 노선 번호 — busnumber 에 괄호 행선지가 붙어 온다("6019(내방역/방배1,4동)").
#   route_no·route_key 는 그대로 두고(노선 174개가 전부 다름) route_base_no(괄호 앞) 를 더한다.
#   같은 route_base_no 에 노선이 둘 이상인 묶음 15개(4000·8829·5400 …) — 번호로 찾으면 **목록**을 돌려주고
#   하나로 자동 선택하지 않는다(route_base_shared=true). 고르기는 경유 정류장으로, 못 고르면 근거없음.
import json, re, collections
from datetime import datetime, timezone, timedelta
from _paths import RAW_MOBILITY, PROCESSED

KST = timezone(timedelta(hours=9))
SRC = RAW_MOBILITY / "incheon_airport_bus.json"
OUT_DIR = PROCESSED / "mobility"
OUT = OUT_DIR / "airport_bus_v1.jsonl"
STOPS = OUT_DIR / "airport_bus_stops.jsonl"
REPORT = OUT_DIR / "airport_bus_v1_report.md"
SOURCE = "incheon_airport_bus"

# 원본 필드 → (터미널, 요일). t1wt/t2wt 는 주말이며 지하철의 holiday 와 같은 축으로 둔다.
SLOTS = [("T1", "weekday", "t1wdayt", "t1endfirst", "t1endlast", "t1ridelo"),
         ("T1", "holiday", "t1wt", "t1endfirst", "t1endlast", "t1ridelo"),
         ("T2", "weekday", "t2wdayt", "t2endfirst", "t2endlast", "t2ridelo"),
         ("T2", "holiday", "t2wt", "t2endfirst", "t2endlast", "t2ridelo")]


def clean(v):
    """원본에 빈 문자열과 문자열 'None' 이 섞여 온다."""
    s = str(v).strip() if v is not None else ""
    return "" if s.lower() in ("", "none", "null", "-") else s


def hhmm(s):
    s = clean(s)
    return f"{s[:2]}:{s[2:4]}:00" if re.fullmatch(r"\d{4}", s) else None


def fare(raw):
    """요금은 등급별로 여러 값이 올 수 있다("13000, 13000"). 최저·최고만 남긴다."""
    vals = sorted({int(x) for x in re.findall(r"\d+", clean(raw))})
    if not vals:
        return None
    return {"min": vals[0], "max": vals[-1]} if len(vals) > 1 else vals[0]


def base_no(no):
    """"6019(내방역/방배1,4동)" → "6019" · "아산(온양)/천안경유" → "아산" · 괄호 없으면 그대로."""
    return re.split(r"[(（]", no, maxsplit=1)[0].strip() or no


def times_of(raw):
    return [t for t in (hhmm(x) for x in clean(raw).split(",")) if t]


doc = json.loads(SRC.read_text(encoding="utf-8"))
fetched = doc.get("fetched_at", "")[:10]
items = doc["items"]
base_count = collections.Counter(base_no(clean(it.get("busnumber"))) for it in items)
to_air = collections.Counter()

OUT_DIR.mkdir(parents=True, exist_ok=True)
rows = 0
stat = collections.Counter()
per_route = {}
with OUT.open("w", encoding="utf-8") as g, STOPS.open("w", encoding="utf-8") as gs:
    for it in items:
        no = clean(it.get("busnumber"))
        # 공항행 첫차·막차 — 원본이 공항 출발 쪽 값을 그대로 싣고 온다(머리글 ★). 같으면 버린다.
        aw = (hhmm(it.get("toawfirst")), hhmm(it.get("toawlast")))
        dep_pairs = [(hhmm(it.get(f"{t}endfirst")), hhmm(it.get(f"{t}endlast"))) for t in ("t1", "t2")]
        if aw == (None, None):
            aw_keep, aw_grade = (None, None), "근거없음"; to_air["원본 비어 있음"] += 1
        elif aw in dep_pairs:
            aw_keep, aw_grade = (None, None), "근거없음"
            to_air["T2 출발값과 같음" if aw == dep_pairs[1] else "T1 출발값과 같음"] += 1
        else:
            aw_keep, aw_grade = aw, "추정"; to_air["출발값과 다름(유지)"] += 1
        bn = base_no(no)
        base = {
            "route_no": no, "route_key": f"airport_bus|{no}",
            "route_base_no": bn or None, "route_base_shared": bool(bn) and base_count[bn] > 1,   # 번호 없는 7노선은 묶지 않는다
            "area": clean(it.get("area")), "bus_class": clean(it.get("busclass")),
            "fare_adult": fare(it.get("adultfare")),          # "13000, 13000" 처럼 등급별로 여러 값이 온다
            "operator": clean(it.get("cpname")),
            "to_airport_first": aw_keep[0], "to_airport_last": aw_keep[1], "to_airport_grade": aw_grade,
            "to_airport_raw": {"first": aw[0], "last": aw[1]},
            "source": SOURCE, "fetched_at": fetched, "fetched_at_precision": "day",
        }
        # 경유 정류장 — 중복이 섞여 오므로 순서를 지키며 중복 제거
        seen, stops = set(), []
        for nm in (clean(it.get("routeinfo")).split(",")):
            nm = nm.strip()
            if nm and nm not in seen:
                seen.add(nm); stops.append(nm)
        if stops:
            gs.write(json.dumps({"route_no": no, "stop_count": len(stops), "stops": stops,
                                 "source": SOURCE, "fetched_at": fetched}, ensure_ascii=False) + "\n")

        got_any = False
        for term, day, tf, ff, lf, rl in SLOTS:
            times = times_of(it.get(tf))
            first, last = hhmm(it.get(ff)), hhmm(it.get(lf))
            if not times and not (first or last):
                continue
            # 시각이 2개 이하이고 첫차·막차와 같으면 시간표가 아니라 첫차·막차만 실린 것이다
            has_tt = len(times) > 2 or (times and set(times) - {first, last})
            stat[f"{term}/{day}/{'시간표' if has_tt else '첫차막차만'}"] += 1
            got_any = True
            for t in (times or [x for x in (first, last) if x]):
                g.write(json.dumps({**base, "terminal": term, "day_type": day, "dep_time": t,
                                    "dir": "from_airport", "ride_location": clean(it.get(rl)) or None,
                                    "first": first, "last": last,
                                    "timetable_available": bool(has_tt),
                                    "grade": "확정" if has_tt else "첫차막차만",
                                    }, ensure_ascii=False) + "\n")
                rows += 1
        per_route[no] = got_any

lines = ["# 인천공항 버스 시간표 v1", "",
         f"생성 {datetime.now(KST).isoformat(timespec='seconds')} · 소스 {SOURCE} · 수집일 {fetched}",
         f"노선 {len(items)}개 · 출발 행 {rows:,}개 · 정류장 목록 {sum(1 for _ in STOPS.open(encoding='utf-8'))}개 노선", "",
         "## 터미널·요일별 제공 상태", "", "| 구분 | 노선 수 |", "|---|---|"]
lines += [f"| {k} | {v} |" for k, v in sorted(stat.items())]
lines += ["", "## 공항행 첫차·막차 (원본 toawfirst/toawlast)", "", "| 원본 값 | 노선 수 | to_airport_* |", "|---|---|---|"]
lines += [f"| {k} | {v} | {'유지 · 추정' if '유지' in k else 'None · 근거없음'} |" for k, v in sorted(to_air.items())]
shared = sorted(b for b, c in base_count.items() if b and c > 1)
lines += ["", "## 노선 번호", "",
          f"- 노선 {len(items)}개 · 괄호 붙은 번호 {sum(1 for it in items if base_no(clean(it.get('busnumber'))) != clean(it.get('busnumber')))}개 · "
          f"route_base_no 가 겹치는 묶음 {len(shared)}개: {' · '.join(shared)}",
          f"- 원본 busnumber 가 비어 있는(None) 노선 {base_count['']}개 — route_no 가 빈 문자열이다(번호로는 못 찾는다)"]
lines += ["", "## 읽는 법", "",
          "- `t1wdayt`·`t1wt` 는 **출발 시각 전체 목록**이다. 지하철과 같은 정밀도이므로 이 노선의 출발 시각은 `확정` 근거가 된다 — 버스라고 해서 대기 시간을 추정으로 두지 않는다.",
          "- 시각이 2개뿐이고 첫차·막차와 같으면 시간표가 아니라 첫차·막차만 실린 것이다. `timetable_available=false`, `grade=첫차막차만` 으로 구분하고, 그 노선의 중간 시각은 `근거 없음`으로 다룬다.",
          "- `dir=from_airport` — 이 데이터는 공항 출발편의 시각이다. **공항행 시각은 없다.** 원본 `toawfirst`·`toawlast`(명세: 공항행첫차·막차)는 거의 전부 공항 출발 첫차·막차와 같은 값이라 `to_airport_first`·`to_airport_last` 를 비우고 `to_airport_grade=근거없음` 으로 둔다(원본 값은 `to_airport_raw`).",
          "- `route_no` 는 원본 그대로(괄호 행선지 포함, 노선마다 다름). 번호로 찾을 때는 `route_base_no` 를 쓰고, `route_base_shared=true` 면 후보가 여럿이다 — 하나로 자동 선택하지 않는다(경유 정류장으로 고르고, 못 고르면 근거없음).",
          "- 요일 축은 지하철과 맞췄다: `weekday` / `holiday`(원본의 주말 필드).",
          "- 승차 위치(`ride_location`)는 판정에 쓰지 않고 안내 문구 소재로만 쓴다.",
          "- T2 값이 비어 있는 노선은 T1 만 운행한다. 인바운드 고객이 T2 도착이면 그 노선은 후보에서 뺀다."]
REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"출발 행 {rows:,}개 → {OUT}")
print(f"정류장 → {STOPS}")
print(f"리포트 → {REPORT}")
for k, v in sorted(to_air.items()):
    print(f"   공항행 {k}: {v}")
for k, v in sorted(stat.items()):
    print(f"   {k}: {v}")
