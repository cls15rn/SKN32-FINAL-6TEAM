# mobility_scripts/check_line_station_order.py — line_station_order_v1.json 회귀 검증
# 실행: 저장소 루트에서  python mobility_scripts/check_line_station_order.py
# build_line_station_order_v1.py 를 고칠 때마다 돌린다. 실패하면 종료코드 1.
import json, collections, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "collect"))
from _paths import PROCESSED
D = json.loads((PROCESSED / "mobility" / "line_station_order_v1.json").read_text(encoding="utf-8"))
fail = []

def graph(ln):
    g = collections.defaultdict(set)
    for s in D['lines'][ln]['stations']:
        g[s['station_nm']]
    for e in D['lines'][ln]['edges']:
        g[e['a']].add(e['b']); g[e['b']].add(e['a'])   # 구조는 전부 넣는다. 등급은 판정 때 붙인다.
    return g

def comps(g):
    seen, out = set(), []
    for n in g:
        if n in seen: continue
        st, c = [n], []
        seen.add(n)
        while st:
            x = st.pop(); c.append(x)
            for y in g[x]:
                if y not in seen: seen.add(y); st.append(y)
        out.append(sorted(c))
    return out

def path(g, a, b, avoid=None):
    prev = {a: None}; q = collections.deque([a])
    while q:
        x = q.popleft()
        if x == b: break
        for y in sorted(g[x]):
            if y not in prev and y != avoid:
                prev[y] = x; q.append(y)
    if b not in prev: return None
    p, x = [], b
    while x is not None: p.append(x); x = prev[x]
    return p[::-1]

print("=== 1. 노선별 연결 성분 / 차수 ===")
for ln in sorted(D['lines']):
    g = graph(ln); cs = comps(g)
    deg = collections.Counter(len(v) for v in g.values())
    ends = [n for n, v in g.items() if len(v) == 1]
    junc = [n for n, v in g.items() if len(v) >= 3]
    iso = [n for n, v in g.items() if len(v) == 0]
    flag = ""
    if len(cs) > 1: flag = f"  ← 성분 {len(cs)}개: " + " / ".join(f"{len(c)}역({c[0]}~{c[-1]})" for c in cs)
    print(f"{ln:8s} 역{len(g):3d} 성분{len(cs)} 차수{dict(sorted(deg.items()))} 종단{len(ends)}{ends if len(ends)<=8 else ''} 분기{junc}{flag}")
    if iso: fail.append(f"{ln}: 고립역 {iso}")

print()
print("=== 1b. 근거없음 간선(판정에서 '근거없음'으로 내려야 하는 구간) ===")
for ln in sorted(D['lines']):
    ne=[f"{e['a']}–{e['b']}" for e in D['lines'][ln]['edges'] if e['grade']=='근거없음']
    if ne: print(f"  {ln}: {len(ne)}개  {' '.join(ne[:12])}{' …' if len(ne)>12 else ''}")

print()
print("=== 2. 알려진 경로 대조 ===")
CASES = [
    # (노선, 출발, 도착, 반드시 지나는 역, 지나면 안 되는 역)
    ("01호선", "서울역", "인천",  ["구로", "구일", "부평"], ["가산디지털단지", "수원"]),
    ("01호선", "서울역", "신창",  ["구로", "가산디지털단지", "수원", "천안"], ["구일", "부평"]),
    ("01호선", "서울역", "광명",  ["구로", "가산디지털단지", "금천구청"], ["석수"]),
    ("01호선", "소요산", "연천",  ["청산", "전곡"], ["동두천"]),
    ("03호선", "대화",   "오금",  ["대곡", "종로3가", "교대", "수서"], []),
    ("05호선", "방화",   "마천",  ["강동", "둔촌동", "오금"], ["상일동", "하남검단산"]),
    ("05호선", "방화",   "하남검단산", ["강동", "상일동", "미사"], ["둔촌동", "마천"]),
    ("02호선", "성수",   "신설동", ["용답", "신답", "용두"], ["건대입구", "시청"]),
    ("02호선", "신도림", "까치산", ["도림천", "양천구청", "신정네거리"], ["문래"]),
    ("04호선", "불암산", "오이도", ["동대문", "사당", "금정", "산본", "수리산", "대야미"], []),
    ("07호선", "장암",   "석남",  ["도봉산", "건대입구", "고속터미널", "온수", "부평구청"], []),
    ("09호선", "개화",   "중앙보훈병원", ["여의도", "고속터미널", "종합운동장"], []),
    ("경의선", "문산",   "용문",  ["행신", "공덕", "효창공원앞", "용산", "왕십리", "청량리"], ["신촌", "지평"]),
    ("경의선", "서울역", "가좌",  ["신촌"], ["공덕"]),
    ("수인분당선", "왕십리", "인천", ["선릉", "수서", "죽전", "수원", "오이도", "원인재"], []),
    ("08호선", "별내",   "모란",  ["구리", "천호", "잠실", "가락시장", "복정"], []),
    # 6호선 응암순환 — 구산 다음은 새절이 아니라 응암이다(2026-09-11 수정).
    # FR 번호(615 구산 · 616 새절)만 보면 인접해 보이지만 그 사이에 응암(610)이 들어간다.
    # 공식 역간거리표(구산→응암 2:00/1.5km · 응암→새절 1:20/0.9km)와 시간표의 한 열차
    # (구산 05:47:30 → 응암 05:50:00 → 새절 05:51:40)가 둘 다 그렇게 말한다.
    ("06호선", "응암",   "신내",  ["새절", "증산", "석계", "봉화산"], ["역촌", "구산"]),
]
# 간선 자체를 못 박는다. 순환선은 경로가 둘이라 경로 대조로는 이걸 고정할 수 없다.
EDGE_MUST = [("06호선", "구산", "응암"), ("06호선", "새절", "응암"), ("06호선", "연신내", "구산")]
EDGE_MUST_NOT = [("06호선", "구산", "새절")]
for ln, a, b, must, never in CASES:
    g = graph(ln); p = path(g, a, b)
    if p is None:
        fail.append(f"{ln} {a}→{b}: 경로 없음"); print(f"  X {ln} {a}→{b} 경로 없음"); continue
    ps = set(p)
    miss = [m for m in must if m not in ps]
    hit  = [n for n in never if n in ps]
    tag = "OK" if not miss and not hit else "X "
    if miss or hit: fail.append(f"{ln} {a}→{b}: 빠짐{miss} 잘못들어옴{hit}")
    print(f"  {tag} {ln} {a}→{b} ({len(p)}역)" + (f"  빠짐{miss} 오포함{hit}" if miss or hit else ""))

print()
print("=== 2b. 있어야 할 / 없어야 할 간선 ===")
for ln, a, b in EDGE_MUST:
    ok_ = any({e['a'], e['b']} == {a, b} for e in D['lines'][ln]['edges'])
    print(f"  {'OK' if ok_ else 'X '} {ln} {a}–{b} 있어야 한다")
    if not ok_: fail.append(f"{ln} {a}–{b} 간선이 없다")
for ln, a, b in EDGE_MUST_NOT:
    bad_ = any({e['a'], e['b']} == {a, b} for e in D['lines'][ln]['edges'])
    print(f"  {'X ' if bad_ else 'OK'} {ln} {a}–{b} 없어야 한다")
    if bad_: fail.append(f"{ln} {a}–{b} 간선이 남아 있다")

print()
print("=== 3. 순환선 — 방향에 따라 경로가 달라야 한다 ===")
g = graph("02호선")
for a, b in [("강남", "시청"), ("성수", "잠실")]:
    p1 = path(g, a, b)
    # 첫 걸음을 막아 반대 방향 경로를 낸다
    p2 = path(g, a, b, avoid=p1[1])
    print(f"  02호선 {a}→{b}: 경로1 {len(p1)}역 (경유 {p1[1]}) / 경로2 {len(p2) if p2 else '-'}역 (경유 {p2[1] if p2 else '-'})")
    if not p2: fail.append(f"02호선 {a}→{b}: 반대 방향 경로가 없다(순환이 안 닫혔다)")

print()
print("=== 4. 방향 표기 ===")
print("  지선 예외(노선 direction 과 어긋나는 간선) — dir_a_to_b 로 흡수되는지 확인")
for ln in ["02호선","05호선","01호선"]:
    order={s['station_nm']:s['fr_order'] for s in D['lines'][ln]['stations']}
    maj=D['lines'][ln]['direction']
    for e in D['lines'][ln]['edges']:
        if not e.get('dir_a_to_b'): continue
        asc = order[e['a']] < order[e['b']]
        exp = 'asc' if asc else 'desc'
        got = maj[e['dir_a_to_b']]['fr_order']
        if got != exp:
            print(f"    {ln} {e['a']}→{e['b']} dir={e['dir_a_to_b']} (노선 표기 {got}, 이 간선은 {exp})")
for ln in ["01호선", "02호선", "03호선", "04호선", "05호선"]:
    print(f"  {ln} {D['lines'][ln]['direction']}")

print()
print("=== 5. FR 순서 스팟 체크 ===")
for ln, expect in [("03호선", ["대화","주엽","정발산","마두","백석","대곡","화정","원당","원흥","삼송","지축","구파발"]),
                   ("02호선", ["시청","을지로입구","을지로3가","을지로4가","동대문역사문화공원","신당","상왕십리","왕십리","한양대","뚝섬","성수"]),
                   ("04호선", ["금정","산본","수리산","대야미","반월","상록수","한대앞"])]:
    got = [s['station_nm'] for s in D['lines'][ln]['stations']]
    i = got.index(expect[0]); seg = got[i:i+len(expect)]
    okk = seg == expect
    print(f"  {'OK' if okk else 'X '} {ln}: {' '.join(seg)}")
    if not okk: fail.append(f"{ln} FR 순서 불일치: {seg} != {expect}")

print()
print("=== 6. 소요시간 관측치 상식 대조 ===")
tm = [e['travel_min'] for v in D['lines'].values() for e in v['edges'] if 'travel_min' in e]
nodir = [(ln,e['a'],e['b'],e['grade']) for ln,v in D['lines'].items() for e in v['edges'] if e.get('dir_a_to_b') is None and e['grade']!='근거없음']
print(f"  방향 라벨을 못 정한 간선(근거없음 제외): {len(nodir)}  {nodir[:8]}")
print(f"  간선 소요시간: 최소 {min(tm)} 중앙값 {sorted(tm)[len(tm)//2]} 최대 {max(tm)} (n={len(tm)})")
big = [(ln, e['a'], e['b'], e['travel_min']) for ln, v in D['lines'].items() for e in v['edges'] if e.get('travel_min', 0) >= 8]
print(f"  8분 이상: {big}")

print()
print()
print("=== 7. travel_min 커버리지 ===")
tmn = [e for v in D['lines'].values() for e in v['edges'] if 'travel_min' in e]
tot = sum(len(v['edges']) for v in D['lines'].values())
srcc = collections.Counter(e.get('travel_min_source') for e in tmn)
grc  = collections.Counter(e.get('travel_min_grade') for e in tmn)
print(f"  {len(tmn)}/{tot} 간선에 소요시간이 있다 (없음 {tot-len(tmn)})")
print(f"  출처 {dict(srcc)} · 등급 {dict(grc)}")
nom = collections.Counter(ln for ln, v in D['lines'].items() for e in v['edges'] if 'travel_min' not in e)
print(f"  없는 곳: {dict(nom.most_common())}")
# 2026-09-11 기준 731/777. 이보다 줄면 어딘가 회귀한 것이다.
if len(tmn) < 725:
    fail.append(f"travel_min 커버리지가 {len(tmn)} 로 줄었다(기준 725)")
else:
    print("  OK 기준(725) 이상")

print()
print("=== 결과 ===")
if fail:
    for f in fail: print("  실패:", f)
    sys.exit(1)
print("  전부 통과")
