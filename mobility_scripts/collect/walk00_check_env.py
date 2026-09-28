# mobility_scripts/collect/walk00_check_env.py — 30번 방(걷기 좋은 길) 시작 전 환경 점검. 노트북·집 PC 공용.
# 실행:  cd C:\final_project\SKN32-FINAL-6TEAM ; ..\.venv\Scripts\python mobility_scripts\collect\walk00_check_env.py [--no-md5] [--no-net]
# 보는 것: ①.env 키 이름(값은 안 찍음) ②PBF+md5 ③graph-cache ④GH 서버(8989) foot ⑤백업 zip ⑥파이썬 패키지 ⑦소스 사이트 접속 ⑧API 활용신청(1행 호출)
# 산출: raw\mobility\walk_courses\walk00_env_<시각>.json (키 값 없음). 경로 응답 저장 없음 — /info 만 부른다.
import hashlib, importlib, json, os, re, sys, time, urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import REPO_ROOT, DATA_DIR, RAW_MOBILITY, PROCESSED  # noqa: E402

A = sys.argv[1:]
R = {"at": datetime.now().isoformat(timespec="seconds"), "host": os.environ.get("COMPUTERNAME", "?"), "checks": {}}
BAD = []

def put(k, ok, **kv):
    R["checks"][k] = {"ok": ok, **kv}
    print(("OK  " if ok else "!!  ") + k, json.dumps(kv, ensure_ascii=False, default=str)[:300])
    if not ok:
        BAD.append(k)

# ① .env — 이름만
envp = REPO_ROOT / ".env"
names = []
if envp.exists():
    for line in envp.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if m:
            names.append((m.group(1), len(m.group(2).strip().strip('"\'')) > 0))
put("env_file", envp.exists(), path=str(envp), keys=[n for n, v in names if v], empty=[n for n, v in names if not v])
KEY = os.environ.get("DATA_GO_KR_KEY", "")
put("DATA_GO_KR_KEY", bool(KEY), note="TourAPI·두루누비 공용 serviceKey")
seoul = [n for n, v in names if v and "SEOUL" in n.upper()]
put("seoul_key_name", bool(seoul), names=seoul, note="열린데이터광장 두드림길용(없어도 파일 다운로드로 대체 가능)")
put("DATA_DIR", DATA_DIR.exists(), path=str(DATA_DIR))

# ② PBF
pbf = RAW_MOBILITY / "osm" / "south-korea-latest.osm.pbf"
md5f = pbf.with_name(pbf.name + ".md5")
if pbf.exists():
    kv = {"MB": round(pbf.stat().st_size / 2**20, 1), "mtime": datetime.fromtimestamp(pbf.stat().st_mtime).isoformat(timespec="minutes")}
    ok = True
    if md5f.exists() and "--no-md5" not in A:
        want = md5f.read_text().split()[0].lower()
        h = hashlib.md5()
        with open(pbf, "rb") as f:
            for b in iter(lambda: f.read(1 << 22), b""):
                h.update(b)
        kv["md5_match"] = ok = (h.hexdigest() == want)
    put("pbf", ok, **kv)
else:
    put("pbf", False, path=str(pbf))

# ③ graph-cache
gh = PROCESSED / "mobility" / "graph" / "gh"
gc = gh / "graph-cache"
need = ["edges", "geometry", "nodes", "nodes_ch_foot", "shortcuts_foot", "properties"]
if gc.exists():
    miss = [n for n in need if not (gc / n).exists()]
    mb = round(sum(p.stat().st_size for p in gc.iterdir() if p.is_file()) / 2**20)
    put("graph_cache", not miss, path=str(gc), MB=mb, missing=miss, jar=(gh / "graphhopper-web-11.0.jar").exists())
else:
    put("graph_cache", False, path=str(gc))

# ④ GH 서버
try:
    import urllib.request
    info = json.load(urllib.request.urlopen("http://localhost:8989/info", timeout=3))
    prof = [p["name"] for p in info.get("profiles", [])]
    put("gh_server", "foot" in prof, version=info.get("version"), profiles=prof, data_date=info.get("data_date"))
except Exception as e:
    put("gh_server", False, err=type(e).__name__, hint="graph\\gh 에서 .\\gh_build.ps1 (캐시 있으면 1~2분)")

# ⑤ 백업 zip
dl = Path.home() / "Downloads"
bk = DATA_DIR.parent / "_backup"
zips = {}
for d in (dl, bk):
    if d.exists():
        for p in sorted(d.glob("*.zip")):
            if re.search(r"mobility|graph|data_travel|walk", p.name):
                zips[str(p)] = round(p.stat().st_size / 2**20, 1)
put("backup_zips", bool(zips), found=zips)

# ⑥ 패키지
pk = {}
for m in ["requests", "dotenv", "pyproj", "shapely", "osmium", "gpxpy", "pandas"]:
    try:
        pk[m] = getattr(importlib.import_module(m), "__version__", "ok")
    except Exception:
        pk[m] = None
miss = [m for m, v in pk.items() if v is None]
put("packages", not [m for m in miss if m in ("requests", "pyproj", "shapely")], have=pk, missing=miss,
    hint=("pip install " + " ".join(miss)) if miss else "")

# ⑦ 접속 · ⑧ API 1행 호출
if "--no-net" not in A:
    import requests
    for name, url in [("gil_seoul", "https://gil.seoul.go.kr/"), ("data_seoul", "https://data.seoul.go.kr/"),
                      ("durunubi", "https://www.durunubi.kr/"), ("visitkorea", "https://korean.visitkorea.or.kr/"),
                      ("apis_data_go", "https://apis.data.go.kr/"), ("overpass", "https://overpass-api.de/api/status")]:
        try:
            r = requests.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            put("net_" + name, r.status_code < 500, status=r.status_code)
        except Exception as e:
            put("net_" + name, False, err=type(e).__name__)
    if KEY:
        k = urllib.parse.unquote(KEY)  # 인코딩 키가 들어 있어도 한 번만 인코딩되게
        base = {"serviceKey": k, "MobileOS": "ETC", "MobileApp": "ACOP", "_type": "json", "numOfRows": 1, "pageNo": 1}
        for name, url, extra in [
            ("api_durunubi_course", "https://apis.data.go.kr/B551011/Durunubi/courseList", {}),
            ("api_tour_kor2_c0115", "https://apis.data.go.kr/B551011/KorService2/areaBasedList2", {"areaCode": 1, "contentTypeId": 25, "cat2": "C0115"}),
        ]:
            try:
                r = requests.get(url, params={**base, **extra}, timeout=10)
                t = r.text[:400]
                try:
                    j = r.json()
                    hd = j.get("response", {}).get("header", {})
                    tot = j.get("response", {}).get("body", {}).get("totalCount")
                    put(name, hd.get("resultCode") == "0000", status=r.status_code, code=hd.get("resultCode"), msg=hd.get("resultMsg"), totalCount=tot)
                except Exception:
                    put(name, False, status=r.status_code, body=re.sub(r"serviceKey=[^&\"<]+", "serviceKey=***", t))
            except Exception as e:
                put(name, False, err=type(e).__name__)

# 저장 — 산출 폴더
out = RAW_MOBILITY / "walk_courses"
out.mkdir(parents=True, exist_ok=True)
R["bad"] = BAD
fp = out / f"walk00_env_{time.strftime('%Y%m%d_%H%M')}.json"
fp.write_text(json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print("\n실패:", BAD or "없음", "\n기록:", fp)
