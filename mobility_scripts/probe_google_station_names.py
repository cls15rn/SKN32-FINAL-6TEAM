# -*- coding: utf-8 -*-
"""구글의 역명 표기를 확인한다. **응답은 저장하지 않는다 — 화면에만 찍는다.**

    python mobility_scripts/probe_google_station_names.py --live                  # ① 경로 1건(싸다)
    python mobility_scripts/probe_google_station_names.py --live --sample 40      # ② 노선을 고르게 섞은 표본
    python mobility_scripts/probe_google_station_names.py --live --stations all   # ③ 793역 전수

★ `--stations N` 은 파일 앞에서 N개를 자른다 = **1호선만 본다.** 2026-09-13 에 그렇게 해서
  한글 0% 가 나왔는데, 화면에서 한글로 뜬 역(여의도·여의나루·서대문)은 5호선이었다.
  **표본을 고르는 필터가 곧 편향이다.** 비율을 보려면 `--sample`(노선 고르게) 또는 `--stations all` 을 쓴다.

왜: 2026-09-13 폰 실측에서 구글맵 화면의 역명이 영어·한글로 섞여 나왔다
    (여의도→잠실 22개 중 한글 4). 우리 `station_nm_en`(국가철도공단 표준)과 표기 체계도 다르다
    (`Gongdeog` vs 표준 `Gongdeok`). 그 비율과 어긋남을 숫자로 확인한다.

★ 저장 금지. 구글 유래 문자열을 파일·DB에 남기지 않는다(9/10 Routes 검토서 캐싱 금지 · 모듈 방침).
  남기는 것은 **숫자와 결론**뿐이다. 화면 출력을 그대로 문서에 붙이지 말 것.

★ 먼저 ①로 화면과 대조한다. API 응답이 화면 표기와 다르면 ②③은 **다른 것을 재는 것**이므로 하지 않는다.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mobility_scripts.collect._paths import PROCESSED  # noqa: E402

COORDS = PROCESSED / "mobility" / "station_coords.json"
HANGUL = re.compile(r"[가-힣]")


def read_key():
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        return key
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name.strip() == "GOOGLE_MAPS_API_KEY":
            return value.strip().strip('"').strip("'")
    raise SystemExit("GOOGLE_MAPS_API_KEY is missing")


def post(url, body, key, fields, timeout=25):
    req = Request(url, data=json.dumps(body).encode(), method="POST",
                  headers={"Content-Type": "application/json", "X-Goog-Api-Key": key,
                           "X-Goog-FieldMask": fields})
    with urlopen(req, timeout=timeout) as res:
        return json.load(res)


def stations():
    return json.loads(COORDS.read_text(encoding="utf-8"))["stations"]


def mark(name):
    if not name:
        return "미검색"
    return "한글" if HANGUL.search(name) else "영어"


def norm(x):
    return re.sub(r"[^a-z0-9]", "", (x or "").lower().replace("station", ""))


def classify(got, ours):
    """구글 표기를 네 갈래로 가른다. ★ '미검색'을 '다름'으로 세면 숫자가 거짓이 된다."""
    if not got:
        return "미검색"
    if HANGUL.search(got):
        return "한글"
    g, o = norm(got), norm(ours)
    if g == o:
        return "일치"
    # 로마자 변형인가(Chang-dong/Changdong), 아예 다른 말인가(종합운동장/Sports Complex)
    if g[:3] == o[:3] or g in o or o in g:
        return "로마자차이"
    return "의미번역의심"


# ── ① 경로 1건 — 화면과 대조할 표본 ────────────────────────────────────
def probe_route(key, a_key, b_key):
    S = stations()
    a, b = S[a_key], S[b_key]
    pt = lambda s: {"location": {"latLng": {"latitude": s["lat"], "longitude": s["lng"]}}}
    body = {"origin": pt(a), "destination": pt(b), "travelMode": "TRANSIT", "languageCode": "en"}
    fields = "routes.legs.steps.travelMode,routes.legs.steps.transitDetails"
    print(f"■ {a['station_nm']}({a['station_nm_en']}) → {b['station_nm']}({b['station_nm_en']})  languageCode=en")
    try:
        data = post("https://routes.googleapis.com/directions/v2:computeRoutes", body, key, fields)
    except HTTPError as e:
        print(f"  HTTP {e.code} — Routes API 가 막혔거나 키 권한이 없다"); return []
    except (URLError, TimeoutError):
        print("  NETWORK_ERROR — 기기 네트워크에서 돌릴 것"); return []

    seen = []
    for r_i, route in enumerate(data.get("routes", [])[:1], 1):
        for st in (s for leg in route.get("legs", []) for s in leg.get("steps", [])):
            td = st.get("transitDetails")
            if not td:
                continue
            line = (td.get("transitLine") or {})
            sd = td.get("stopDetails") or {}
            dep = (sd.get("departureStop") or {}).get("name")
            arr = (sd.get("arrivalStop") or {}).get("name")
            head = td.get("headsign")
            ln = line.get("nameShort") or line.get("name")
            print(f"  · 노선 {ln!r} [{mark(ln)}] · 방면 {head!r} [{mark(head)}]")
            print(f"    승차 {dep!r} [{mark(dep)}] → 하차 {arr!r} [{mark(arr)}]")
            seen += [x for x in (dep, arr) if x]
    # ★ 주의: Routes API 는 **중간 정거장을 주지 않는다.** 승·하차역과 노선·방면까지다.
    #   화면의 22개 목록과 1:1 대조는 안 되고, 겹치는 역만 비교할 수 있다.
    return seen


# ── ②③ 역별 표기 — Places API (New) ──────────────────────────────────
def pick_sample(S, n):
    """노선별로 돌아가며 뽑는다 — 앞에서 자르면 1호선만 본다."""
    by_line = {}
    for k, v in S.items():
        by_line.setdefault(v["line"], []).append(k)
    out, i = [], 0
    while len(out) < n and any(len(v) > i for v in by_line.values()):
        for ln in sorted(by_line):
            if len(by_line[ln]) > i and len(out) < n:
                out.append(by_line[ln][i])
        i += 1
    return out


def probe_places(key, how_many, sample, keys_csv=None, only=None, summary=None):
    S = stations()
    if keys_csv:
        keys = [k.strip() for k in keys_csv.split(",") if k.strip() in S]
        missing = [k.strip() for k in keys_csv.split(",") if k.strip() not in S]
        if missing:
            print(f"  ⚠ 없는 키: {missing}")
    elif sample:
        keys = pick_sample(S, int(sample))
    elif how_many == "all":
        keys = list(S)
    else:
        keys = list(S)[:int(how_many)]
    fields = "places.displayName"
    tally = {}
    for i, k in enumerate(keys, 1):
        s = S[k]
        body = {"includedTypes": ["subway_station", "train_station"], "maxResultCount": 1,
                "languageCode": "en", "locationRestriction": {"circle": {
                    "center": {"latitude": s["lat"], "longitude": s["lng"]}, "radius": 300.0}}}
        try:
            data = post("https://places.googleapis.com/v1/places:searchNearby", body, key, fields)
        except HTTPError as e:
            print(f"  HTTP {e.code} — Places API (New) 를 콘솔에서 켜야 한다")
            return
        except (URLError, TimeoutError):
            print("  NETWORK_ERROR"); return
        got = ((data.get("places") or [{}])[0].get("displayName") or {}).get("text") or ""
        ours = s.get("station_nm_en") or ""
        kind = classify(got, ours)
        tally[kind] = tally.get(kind, 0) + 1
        flag = "" if kind == "일치" else f"   ← {kind}"
        if not only or kind in only:
            print(f"  {i:>3}/{len(keys)} {k:<22} 구글 {got!r:<34} 우리 {ours!r}{flag}")
        time.sleep(0.05)
    n = len(keys)
    found = n - tally.get("미검색", 0)
    print("\n── 숫자만 남긴다 ──")
    print(f"  조회 {n}역 · 노선 {len({S[k]['line'] for k in keys})}종 · 검색됨 {found} · 미검색 {tally.get('미검색', 0)}")
    for kind in ("일치", "로마자차이", "한글", "의미번역의심"):
        c = tally.get(kind, 0)
        print(f"    {kind:<8} {c:>4}  ({c/found*100:.1f}% of 검색됨)" if found else f"    {kind} {c}")
    print("  ★ 역별 매핑은 저장하지 않는다. 위 숫자와 결론만 문서에 남긴다.")
    print("  ★ '의미번역의심' 은 자동 분류다 — 그 줄만 눈으로 확인할 것.")
    if summary:
        # ★ 숫자만 남긴다. 역명·갈래별 목록은 저장하지 않는다 —
        #   "구글이 한글로 주는 역 목록"은 응답에서 파생된 데이터이고, 그건 캐시와 같다.
        lines = [f"# 구글 역명 표기 조사 — 숫자만 ({time.strftime('%Y-%m-%d %H:%M')})", "",
                 f"- 조회 {n}역 · 노선 {len({S[k]['line'] for k in keys})}종 · 검색됨 {found} · 미검색 {tally.get('미검색', 0)}"]
        for kind in ("일치", "로마자차이", "한글", "의미번역의심"):
            c = tally.get(kind, 0)
            lines.append(f"- {kind} {c} ({c/found*100:.1f}%)" if found else f"- {kind} {c}")
        lines += ["", "★ 역별 매핑은 저장하지 않는다(구글 응답 파생 데이터). 이 파일에는 숫자만 있다.",
                  "★ 재현: python mobility_scripts/probe_google_station_names.py --live --stations all"]
        Path(summary).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  숫자 요약 → {summary}")


def main():
    ap = argparse.ArgumentParser(description="구글 역명 표기 확인 (저장 안 함)")
    ap.add_argument("--live", action="store_true", help="실제 호출. 없으면 아무것도 안 한다")
    ap.add_argument("--route", nargs=2, metavar=("FROM", "TO"),
                    default=["05호선|여의도", "02호선|잠실"])
    ap.add_argument("--stations", help="개수 또는 all. ★ 개수를 주면 파일 앞에서 자른다(1호선 편향)")
    ap.add_argument("--sample", help="노선을 고르게 섞어 N개. 비율을 볼 때는 이쪽")
    ap.add_argument("--keys", help="station_key 를 쉼표로. 특정 역만 확인할 때")
    ap.add_argument("--only", help="이 갈래만 화면에 찍는다 — 한글 / 로마자차이 / 의미번역의심 / 미검색")
    ap.add_argument("--summary", help="**숫자만** 이 경로에 저장한다. 역명은 저장하지 않는다")
    a = ap.parse_args()
    if not a.live:
        print("--live 를 줘야 호출한다. 무엇을 부를지 먼저 읽어 볼 것."); return
    key = read_key()
    if a.stations or a.sample or a.keys:
        probe_places(key, a.stations, a.sample, a.keys,
                     only=set(a.only.split(",")) if a.only else None, summary=a.summary)
    else:
        probe_route(key, *a.route)
        print("\n먼저 이 출력을 2026-09-13 폰 실측 화면과 대조할 것.")
        print("겹치는 역의 표기가 화면과 같으면 --stations 로 넓힌다. 다르면 넓히지 않는다.")


if __name__ == "__main__":
    main()
