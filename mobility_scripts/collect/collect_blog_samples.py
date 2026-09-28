# collect_blog_samples.py
import json, re, time, requests
from pathlib import Path
from datetime import date

ID = "62bqet9os2"
SECRET = "VeksJcZ5D1mpPn8YHXi0AtCFemBybZZvZjNy4Cy1"
H = {"X-NCP-APIGW-API-KEY-ID": ID, "X-NCP-APIGW-API-KEY": SECRET}
PLACES = ["명동교자", "런던베이글뮤지엄 안국", "진미평양냉면", "우래옥", "을지면옥"]
OUT = Path(r"C:\teamproject_test\datasets\datasets\travel\raw")
OUT.mkdir(parents=True, exist_ok=True)

def clean(s):  # <b></b>, &quot; 제거
    return re.sub(r"</?b>", "", s).replace("&quot;", '"').replace("&amp;", "&").strip()

rows = []
for p in PLACES:
    r = requests.get("https://naverapihub.apigw.ntruss.com/search/v1/blog",
                     headers=H, params={"query": p, "display": 20, "sort": "date"})
    r.raise_for_status()
    for it in r.json()["items"]:
        rows.append({
            "place_query": p,
            "title": clean(it["title"]),
            "description": clean(it["description"]),
            "link": it["link"],
            "bloggername": it["bloggername"],
            "postdate": it["postdate"],
        })
    print(p, r.json()["total"], "건 중 20건")
    time.sleep(0.3)

f = OUT / f"blog_samples_{date.today():%Y%m%d}.json"
f.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(rows), "건 저장 →", f)