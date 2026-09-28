# mobility_scripts/collect/tago_fix_unmatched.py — 매칭 0개 역명 79개 재검색 (표기 차이 보정)
# 실행: 저장소 루트에서  python mobility_scripts/collect/tago_fix_unmatched.py   → 끝나면 tago_subway_collect.py 다시 실행 (새 역 ID의 시간표 수집)
# 호출 ≈ 80회 (안 걸리는 역만 검색어를 넓혀 몇 번 더).
# 원인: TAGO 역명은 병기 괄호가 붙는다("경복궁(정부서울청사)", "잠실(송파구청)") — v1의 정확 일치(==)에 걸러졌다.
#       "서울"은 TAGO "서울역", "하남검단산"은 "하남검단산역". 보정 규칙: 정확 → 정규화 → 괄호 앞 → 괄호 안 → 별칭 → 역 접미사.
#       검색어가 0건이면(4·19민주묘지 등) 문장부호 변형·부분 검색으로 넓힌다. 결과는 tago_name_map.json(보정표)에 남긴다.
import os, json, re, time, requests
from _paths import RAW_MOBILITY

KEY = os.environ["DATA_GO_KR_KEY"]
BASE = "https://apis.data.go.kr/1613000/SubwayInfo"
COMMON = {"serviceKey": KEY, "_type": "json", "numOfRows": 200, "pageNo": 1}
ID_MAP = RAW_MOBILITY / "tago_station_ids.json"
NAME_MAP = RAW_MOBILITY / "tago_name_map.json"      # 보정표: 열린데이터광장 역명 → TAGO 역명·ID·노선·매칭 규칙
UNMATCHED = RAW_MOBILITY / "tago_unmatched.json"    # 그래도 못 찾은 역명 → 검색 후보 원본

ALIAS = {"서울": ["서울역"], "이수": ["총신대입구(이수)", "이수"], "총신대입구": ["총신대입구(이수)", "총신대입구"]}
# 수도권 밖 운영사 — 같은 역명(교대·증산·죽전 …)이 부산·대구·대전·광주에서 잡힌다.
# ★이들이 exact 로 먼저 걸리면 서울 역("교대(법원.검찰청)")이 가려진다. 그래서 매칭 전에 걸러낸다.
EXCLUDE_PREFIX = ("MTRBS", "MTRDG", "MTRDJ", "MTRGJ", "MTRIAM")
EXCLUDE_ROUTE = {"동해"}          # 부산 동해선 — 코레일 접두어라 접두어로 못 거른다
STATIONS = RAW_MOBILITY / "stations_all.json"
ROUTE2LINE = {"1호선": "01호선", "2호선": "02호선", "3호선": "03호선", "4호선": "04호선", "5호선": "05호선",
              "6호선": "06호선", "7호선": "07호선", "8호선": "08호선", "9호선": "09호선",
              "공항": "공항철도", "경의중앙": "경의선", "수인분당": "수인분당선", "경춘": "경춘선", "경강": "경강선",
              "신분당": "신분당선", "우이신설": "우이신설경전철", "에버라인": "용인경전철", "의정부": "의정부경전철",
              "김포골드라인": "김포도시철도", "인천1호선": "인천선", "인천2호선": "인천2호선", "서해선": "서해선",
              "신림선": "신림선", "GTX-A": "GTX-A"}


def is_outside(it):
    return (it.get("subwayStationId", "").startswith(EXCLUDE_PREFIX)
            or it.get("subwayRouteName") in EXCLUDE_ROUTE)


def norm(s):
    return re.sub(r"[\s·.\-]", "", s or "")


def base_name(s):        # "경복궁(정부서울청사)" → "경복궁"
    return re.sub(r"\(.*?\)", "", s or "").strip()


def paren_name(s):       # "총신대입구(이수)" → "이수"
    m = re.search(r"\((.*?)\)", s or "")
    return m.group(1).strip() if m else ""


def call(**p):
    for attempt in range(5):
        r = requests.get(f"{BASE}/GetKwrdFndSubwaySttnList", params={**COMMON, **p}, timeout=30)
        if r.status_code == 200:
            try:
                body = r.json()["response"]["body"]
            except (KeyError, ValueError):
                return []
            items = body.get("items", {}).get("item", []) if isinstance(body.get("items"), dict) else []
            return [items] if isinstance(items, dict) else list(items)
        if "PER_SECOND" in r.text or r.status_code >= 500:
            time.sleep(2 + attempt * 2); continue
        print("  HTTP", r.status_code, r.text[:120]); return []
    return []


def dedupe(items):
    """역 ID 기준 중복 제거 — 별칭으로 두 번 검색하면 같은 역이 두 번 들어온다."""
    seen, out = set(), []
    for it in items:
        sid = it.get("subwayStationId")
        if sid and sid not in seen:
            seen.add(sid); out.append(it)
    return out


def query_variants(nm):
    """검색어를 넓힌다. TAGO 키워드 검색이 특수문자에 약하고(4·19민주묘지 → 0건),
    긴 이름은 부분 검색이 더 잘 걸린다(신길온천 → '신길')."""
    v = [nm]
    for a, b in (("·", "."), (".", "·")):
        if a in nm:
            v.append(nm.replace(a, b))
    stripped = re.sub(r"[·.\-\s]", "", nm)
    v.append(stripped)
    v += [base_name(x) for x in ALIAS.get(nm, [])]
    runs = re.findall(r"[가-힣]+", nm)                 # "4·19민주묘지" → "민주묘지"
    if runs:
        longest = max(runs, key=len)
        v.append(longest)
        if len(longest) >= 4:                          # "신길온천" → "신길"
            v.append(longest[:2])
    out, seen = [], set()
    for x in v:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out


def match(nm, items):
    """[(항목, 규칙)] — 모든 규칙의 합집합. 수도권 밖 운영사는 뺀다.

    ★첫 규칙에서 멈추면 안 된다. "이촌"은 경의중앙선에 exact 로 있어서 거기서 끝나면
      4호선 "이촌(국립중앙박물관)"을 영영 못 찾는다(환승역이 한 노선만 잡히는 원인).
      규칙은 엄격한 순서로 보되, 역 ID 가 다르면 느슨한 규칙의 결과도 받는다.
    """
    items = [it for it in dedupe(items) if not is_outside(it)]
    rules = [
        ("exact", lambda t: t == nm),
        ("norm", lambda t: norm(t) == norm(nm)),
        ("base", lambda t: norm(base_name(t)) == norm(nm)),
        ("paren", lambda t: norm(paren_name(t)) == norm(nm)),
        ("alias", lambda t: t in ALIAS.get(nm, [])),
        # TAGO 가 "하남검단산역"처럼 '역'을 붙여 부르는 경우, 그 반대도
        ("suffix", lambda t: norm(t) == norm(nm + "역") or norm(base_name(t)) == norm(nm + "역")
                             or norm(t.rstrip("역")) == norm(nm.rstrip("역"))),
    ]
    hits = {}
    for rule, ok in rules:
        for it in items:
            sid = it.get("subwayStationId")
            if sid and sid not in hits and ok(it.get("subwayStationName", "")):
                hits[sid] = (it, rule)
    return list(hits.values())


id_map = json.loads(ID_MAP.read_text(encoding="utf-8"))
want = {}                                   # 역명 → 열린데이터광장 기준 소속 노선들
for st in json.loads(STATIONS.read_text(encoding="utf-8")):
    want.setdefault(st["STATION_NM"], set()).add(st["LINE_NUM"])


def lines_of(its):
    return {ROUTE2LINE.get(it.get("subwayRouteName")) for it in its} - {None}


# 재검색 대상 셋: ① 매칭 0개 ② 수도권 밖만 잡힘(서울 역이 가려짐) ③ 환승역인데 일부 노선만 잡힘
todo, why = [], {}
for nm, its in id_map.items():
    if not its:
        todo.append(nm); why[nm] = "매칭0"
    elif all(is_outside(it) for it in its):
        todo.append(nm); why[nm] = "수도권밖만"
    elif want.get(nm, set()) - lines_of(its):
        todo.append(nm); why[nm] = "노선부족:" + ",".join(sorted(want[nm] - lines_of(its)))
name_map = json.loads(NAME_MAP.read_text(encoding="utf-8")) if NAME_MAP.exists() else {}
unmatched = {}
import collections as _c
_by = _c.Counter(why[nm].split(":")[0] for nm in todo)
print(f"재검색 {len(todo)}개 — {dict(_by)}")
for nm in todo:
    if why[nm].startswith("노선부족"):
        print(f"    {nm}: {why[nm]}")
for nm in todo:
    items, used = [], []
    need = want.get(nm, set())
    for q in query_variants(nm):                     # 원본 → 문장부호 변형 → 별칭 → 부분 검색 순으로 넓힌다
        items += call(subwayStationName=q)
        used.append(q)
        hits = match(nm, items)
        if hits and not (need - lines_of([it for it, _ in hits])):
            break                                    # 필요한 노선을 다 찾으면 그만 (호출 절약)
    hits = match(nm, items)
    if hits:
        # 검색 결과의 역명 필드를 시간표 응답 형식(subwayStationNm)으로도 두어 collect 스크립트와 호환
        id_map[nm] = [{**it, "subwayStationNm": it.get("subwayStationName")} for it, _ in hits]
        name_map[nm] = [{"tago_name": it.get("subwayStationName"), "id": it.get("subwayStationId"),
                         "route": it.get("subwayRouteName"), "rule": rule} for it, rule in hits]
        shown = ", ".join("{}[{}]({})".format(it.get("subwayStationName"), it.get("subwayRouteName"), rule)
                          for it, rule in hits)
        miss = need - lines_of([it for it, _ in hits])
        print(f"  {nm:12} → {shown}{'   ※ 여전히 없는 노선 ' + ','.join(sorted(miss)) if miss else ''}")
    else:
        unmatched[nm] = [{"id": it.get("subwayStationId"), "name": it.get("subwayStationName"),
                          "route": it.get("subwayRouteName")} for it in dedupe(items)]
        print(f"  {nm:12} → 없음 (검색어 {'/'.join(used)}, 후보 {len(dedupe(items))}개)")
    time.sleep(0.15)

ID_MAP.write_text(json.dumps(id_map, ensure_ascii=False, indent=1), encoding="utf-8")
NAME_MAP.write_text(json.dumps(name_map, ensure_ascii=False, indent=1), encoding="utf-8")
UNMATCHED.write_text(json.dumps(unmatched, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"보정 {len(name_map)}개 → {NAME_MAP.name} / 남은 미매칭 {len(unmatched)}개 → {UNMATCHED.name}")
print("다음: python mobility_scripts/collect/tago_subway_collect.py  (새 역 ID의 시간표만 이어서 수집)")
