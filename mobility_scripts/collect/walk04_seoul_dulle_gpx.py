# mobility_scripts/collect/walk04_seoul_dulle_gpx.py — 30번 방. 서울둘레길 2.0 21코스 상세 페이지에서
# 거리·소요·「코스자료 다운로드」 링크(download.do?enc=...)를 읽고 파일을 받는다. 파일 형식(GPX/zip/PDF)을 먼저 확인하는 단계.
# 실행: ..\.venv\Scripts\python mobility_scripts\collect\walk04_seoul_dulle_gpx.py
# 저장: walk_courses\seoul_dulle\ (원본 파일 그대로) + seoul_dulle_index_<날짜>.json. 요청 간 1초.
import html, io, json, re, sys, time, zipfile
from pathlib import Path
from urllib.parse import unquote, urljoin
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import RAW_MOBILITY  # noqa: E402

OUT = RAW_MOBILITY / "walk_courses" / "seoul_dulle"
OUT.mkdir(parents=True, exist_ok=True)
KEYS = {1: 2406040003, 2: 2407100001, 3: 2406040002, 4: 2405210001, 5: 2407100014, 6: 2406040001, 7: 2407100002,
        8: 2407100003, 9: 2407100015, 10: 2407100007, 11: 2407100008, 12: 2407100009, 13: 2407100004, 14: 2407100005,
        15: 2407100006, 16: 2407100016, 17: 2407100010, 18: 2407100011, 19: 2407100012, 20: 2407100013, 21: 2407090001}
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (ACOP course project; 30 walk courses)"

def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()

def gpx_stats(b):
    t = b.decode("utf-8", "ignore")
    pts = re.findall(r'<(?:trkpt|rtept)[^>]*lat="([\d.]+)"[^>]*lon="([\d.]+)"', t) or \
          [(a, o) for o, a in re.findall(r'<(?:trkpt|rtept)[^>]*lon="([\d.]+)"[^>]*lat="([\d.]+)"', t)]
    import math
    km = 0.0
    for (a1, o1), (a2, o2) in zip(pts, pts[1:]):
        a1, o1, a2, o2 = map(float, (a1, o1, a2, o2))
        km += 6371.0 * 2 * math.asin(math.sqrt(math.sin(math.radians(a2 - a1) / 2) ** 2 +
              math.cos(math.radians(a1)) * math.cos(math.radians(a2)) * math.sin(math.radians(o2 - o1) / 2) ** 2))
    return {"pts": len(pts), "km_haversine": round(km, 2), "trk": t.count("<trk>") + t.count("<trk "), "wpt": t.count("<wpt")}

IDX = []
for no, key in KEYS.items():
    url = f"https://gil.seoul.go.kr/gil/view.do?key={key}&sc_gilNo={no}"
    r = S.get(url, timeout=30)
    h = r.text
    title = text((re.search(r"<title>(.*?)</title>", h, re.S) or [None, ""])[1])
    km = re.findall(r"(\d+(?:\.\d+)?)\s*km", text(h))
    tm = re.findall(r"약\s*\d+\s*시간(?:\s*\d+\s*분)?|약\s*\d+\s*분", text(h))
    links = []
    for m in re.finditer(r'<a[^>]+href="([^"]*download\.do\?[^"]+)"[^>]*>(.*?)</a>', h, re.S):
        links.append({"href": urljoin(url, html.unescape(m.group(1))), "label": text(m.group(2))})
    rec = {"no": no, "page": url, "status": r.status_code, "title": title, "km_on_page": km[:6], "time_on_page": tm[:3], "files": []}
    for L in links:
        time.sleep(1)
        f = S.get(L["href"], timeout=60)
        cd = f.headers.get("content-disposition", "")
        fn = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
        fn = unquote(fn.group(1)) if fn else f"course{no:02d}_{len(rec['files'])}"
        try:
            fn = fn.encode("latin-1").decode("utf-8")
        except Exception:
            pass
        safe = f"c{no:02d}_" + re.sub(r'[\\/:*?"<>|]', "_", fn)
        (OUT / safe).write_bytes(f.content)
        info = {"label": L["label"], "file": safe, "ctype": f.headers.get("content-type"), "bytes": len(f.content), "head": f.content[:8].hex()}
        if f.content[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(f.content))
            info["zip"] = []
            for n in z.namelist():
                try:
                    nn = n.encode("cp437").decode("cp949")
                except Exception:
                    nn = n
                e = {"name": nn, "bytes": z.getinfo(n).file_size}
                if nn.lower().endswith(".gpx"):
                    e |= gpx_stats(z.read(n))
                info["zip"].append(e)
        elif b"<gpx" in f.content[:500]:
            info |= gpx_stats(f.content)
        rec["files"].append(info)
    IDX.append(rec)
    print(f"{no:2d} {r.status_code} {title[:30]:30s} km={rec['km_on_page'][:3]} 시간={rec['time_on_page'][:1]} 파일={[(x['label'][:12], x['file'][-30:], x['bytes'], x.get('pts'), x.get('km_haversine'), [(z['name'][-25:], z.get('pts'), z.get('km_haversine')) for z in x.get('zip', [])]) for x in rec['files']]}")
    time.sleep(1)

fp = RAW_MOBILITY / "walk_courses" / f"seoul_dulle_index_{time.strftime('%Y%m%d')}.json"
fp.write_text(json.dumps(IDX, ensure_ascii=False, indent=1), encoding="utf-8")
print("\n기록:", fp, "· 파일 폴더:", OUT)
