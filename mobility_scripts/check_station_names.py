# mobility_scripts/check_station_names.py — station_coords.json 의 역 영문명 결함을 센다
# 실행: python mobility_scripts/check_station_names.py
#       python mobility_scripts/check_station_names.py --strict   (결함이 있으면 종료코드 1 — 회귀용)
#
# 왜: answer 의 역명을 영문·한글 병기로 내기로 했는데(구글맵이 86역·10.8%를 한글로 띄운다)
#     station_nm_en 자체에 결함이 있었다. 고친 뒤 다시 새는 것을 막는 회귀 검사다.
#
# 외부 호출을 하지 않는다. station_coords.json 만 읽는다 — 언제 돌려도 같은 답이 나와야 한다.
#
# 규칙으로 잡는 것
#   (가) 값 단위 — 개행 · 이중공백 · 구두점(백틱·굽은따옴표) · 대소문자 · 붙어쓰기 · 부역명 괄호
#   (나) ★ 역 단위 일관성 — 같은 한글 역명인데 노선별로 영문이 갈리는 것 (2026-09-13 추가)
#        환승역은 같은 역이다. 한 화면에 'Gimpo Intl. Airport' 와 "Gimpo Int'l Airport" 가 같이 나오면
#        고객은 다른 역으로 읽는다. 값 하나씩 보면 둘 다 멀쩡해서 (가) 로는 절대 안 잡힌다.
# 규칙으로 못 잡는 것(★ 아래 KNOWN 참고): 두문자 대소문자(Lrt), 이웃역 이름 혼입(Sutgogae),
#     옛 로마자 표기(Inchon). 사람이 찾아 보정표에 넣었고, 여기서는 "다시 생기면" 잡지 못한다.
import argparse, json, re, collections, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "collect"))
from _paths import PROCESSED

COORDS = PROCESSED / "mobility" / "station_coords.json"

# 국어의 로마자 표기법: 행정구역 단위는 하이픈 뒤 소문자가 맞다 (Sinseol-dong, Yangcheon-gu Office)
ADMIN = {"dong", "gu", "ga", "ri", "eup", "myeon", "gun", "si", "do", "ro", "gil", "o", "ang", "mal"}
SMALL = {"of", "the", "for", "and", "at", "in", "on", "to"}
# 종로3(sam)가 — 읽는 법 병기다. 부역명이 아니다.
READING = re.compile(r"^\((il|i|sam|sa|o|yuk|chil|pal|gu|sip)\)$", re.I)
BAD_PUNCT = re.compile(r"[`´‘’“”]")

# ★ 규칙으로 못 잡아 사람이 판정한 것. 값이 바뀌면 여기도 같이 본다.
KNOWN = {
    "의정부경전철|경전철의정부": "두문자 Lrt → LRT",
    "신림선|서원": "이웃역 Sutgogae 혼입 → Seowon",
    "인천2호선|인천시청": "옛 표기 Inchon → Incheon",
    "공항철도|청라국제도시": "Station 접미 제거",
}


def defects(s):
    """한 영문명에서 규칙으로 잡히는 결함 종류를 돌려준다."""
    if not s:
        return {"결측"}
    d = set()
    if re.search(r"[\n\r\t]", s):
        d.add("개행")
    if s != s.strip() or "  " in s:
        d.add("공백")
    if BAD_PUNCT.search(s):
        d.add("구두점")
    for m in re.finditer(r"\([^)]*\)", s.replace("\n", " ")):
        if not READING.match(m.group(0).replace(" ", "")):
            d.add("부역명")
    for tok in s.split():
        for i, p in enumerate(tok.split("-")):
            core = re.sub(r"[^A-Za-z]", "", p)
            if not core:
                continue
            if re.search(r"[a-z][A-Z]", core) and not core.isupper():
                d.add("붙어쓰기")          # MedicalCenter — 두 단어가 붙었다
            if not core[0].islower():
                continue
            if core.lower() in SMALL:
                continue
            if i > 0 and core.lower() in ADMIN:
                continue                   # -dong, -gu
            if p[0].isdigit():
                continue                   # 19th
            d.add("대소문자")
    return d


def conflicts(stations):
    """같은 한글 역명인데 영문명이 갈리는 것 → {역명: {영문: [station_key]}}.

    ★ (가) 와 축이 다르다. 값 하나만 보면 둘 다 규칙을 통과하므로 **모아 놓고 비교해야만** 보인다.
    """
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for k, v in stations.items():
        en = v.get("station_nm_en")
        if en:
            by[v.get("station_nm")][en].append(k)
    return {nm: dict(m) for nm, m in sorted(by.items()) if len(m) > 1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="결함이 있으면 종료코드 1")
    ap.add_argument("--json", help="결함 목록을 JSON 으로 저장")
    args = ap.parse_args()

    data = json.loads(COORDS.read_text(encoding="utf-8"))
    stations = data["stations"]

    cnt = collections.Counter()
    hits = []
    grade = collections.Counter()
    for key, v in sorted(stations.items()):
        en = v.get("station_nm_en")
        grade[v.get("station_nm_en_grade") or "(없음)"] += 1
        d = defects(en)
        if not d:
            continue
        for x in d:
            cnt[x] += 1
        hits.append({"key": key, "kinds": sorted(d), "value": en, "src": v.get("station_nm_en_src")})

    n = len(stations)
    print(f"역 {n}개 · 영문명 결함 {len(hits)}역 ({len(hits) / n * 100:.1f}%)")
    print("등급: " + " · ".join(f"{k} {v}" for k, v in grade.most_common()))
    if cnt:
        print("\n종류별(역 수, 중복 포함):")
        for k, v in cnt.most_common():
            print(f"  {k:5s} {v:3d}")
        print("\n목록:")
        for h in hits:
            print(f"  {h['key']:22s} {','.join(h['kinds']):14s} {h['value']!r}")
    else:
        print("규칙으로 잡히는 결함 없음.")

    conf = conflicts(stations)
    print(f"\n역 단위 일관성 — 같은 한글 역명인데 영문이 갈리는 역 **{len(conf)}역**")
    for nm, m in conf.items():
        print(f"  {nm}")
        for en, ks in sorted(m.items()):
            print(f"      {en!r:38s} ← {', '.join(ks)}")

    print(f"\n※ 규칙 밖 {len(KNOWN)}건은 사람이 판정해 보정표에 넣었다 — 여기서는 다시 못 잡는다:")
    for k, why in KNOWN.items():
        cur = stations.get(k, {})
        mark = "ok" if cur.get("station_nm_en_src") and cur.get("station_nm_en") != cur.get("station_nm_en_src") else "★확인"
        print(f"  [{mark}] {k} — {why}")

    if args.json:
        open(args.json, "w", encoding="utf-8").write(json.dumps(hits, ensure_ascii=False, indent=1))
        print(f"\n→ {args.json}")

    if args.strict and (hits or conf):
        sys.exit(1)


if __name__ == "__main__":
    main()
