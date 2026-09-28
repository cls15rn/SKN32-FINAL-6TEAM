# extract_claims_v0.py  —  블로그 요약 → 근거 원자 1층 추출 검증
import json, os, time, collections
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(r"C:\teamproject_test\.env")

PROVIDER = "gemini"          # "gemini" 또는 "openai"
MODEL = "gemini-2.5-flash"   # 수업에서 쓰던 모델명으로 바꿔도 됨 (openai면 예: "gpt-4o-mini")

RAW = Path(r"C:\teamproject_test\datasets\datasets\travel\raw\blog_samples_20260908.json")
OUT = Path(r"C:\teamproject_test\datasets\datasets\travel\processed"); OUT.mkdir(parents=True, exist_ok=True)

PROMPT = """너는 블로그 글 요약에서 '가게 운영 관련 주장'만 뽑는 추출기다. 추측하지 말고, 요약에 적힌 것만 쓴다.

[대상 가게] {place}
[제목] {title}
[요약] {desc}
[작성일] {postdate}

JSON만 출력한다. 스키마:
{{
  "same_place": true|false,          // 이 글이 대상 가게(같은 브랜드의 다른 지점은 false)를 실제로 다루는가
  "branch": string|null,             // 요약에 지점명이 있으면 (예: "본점","1호점","이태원점")
  "visit_date": "YYYY-MM-DD"|null,   // 요약에 방문 날짜가 명시된 경우만. 작성일로 추정 금지
  "claims": [
    {{
      "type": "waiting|reservation|hours|holiday|closed_or_moved|last_order|parking|crowd|other",
      "value": string,               // 주장 내용 짧게 (예: "주말 낮 30분", "월요일 정기휴무")
      "weekday": string|null,        // 요약에 요일·주말/평일 언급 있을 때만
      "time_slot": string|null,      // 요약에 시간대 언급 있을 때만 (예: "점심", "오픈 전", "18시 이후")
      "evidence": string             // 요약에서 그대로 복사한 근거 문장. 없으면 이 claim을 넣지 말 것
    }}
  ]
}}
same_place가 false면 claims는 빈 배열로 둔다."""

def call_llm(prompt: str) -> str:
    if PROVIDER == "gemini":
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        r = client.models.generate_content(model=MODEL, contents=prompt,
                                           config={"response_mime_type": "application/json", "temperature": 0})
        return r.text
    else:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        r = client.chat.completions.create(model=MODEL, temperature=0,
                                           response_format={"type": "json_object"},
                                           messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content

rows = json.loads(RAW.read_text(encoding="utf-8"))
results = []
for i, r in enumerate(rows, 1):
    p = PROMPT.format(place=r["place_query"], title=r["title"], desc=r["description"], postdate=r["postdate"])
    try:
        out = json.loads(call_llm(p))
    except Exception as e:
        out = {"error": str(e), "same_place": None, "claims": []}
    results.append({**r, "extract": out})
    print(f"{i}/100 {r['place_query']} same={out.get('same_place')} claims={len(out.get('claims', []))}")
    time.sleep(0.5)

(OUT / "blog_claims_v0.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

# ---- 집계 ----
same = [x for x in results if x["extract"].get("same_place") is True]
with_claims = [x for x in same if x["extract"].get("claims")]
types = collections.Counter(c["type"] for x in with_claims for c in x["extract"]["claims"])
wk = sum(1 for x in with_claims for c in x["extract"]["claims"] if c.get("weekday"))
ts = sum(1 for x in with_claims for c in x["extract"]["claims"] if c.get("time_slot"))
vd = sum(1 for x in same if x["extract"].get("visit_date"))
errs = sum(1 for x in results if "error" in x["extract"])

print("\n==== 결과 ====")
print(f"전체 {len(results)} / 대상 가게 일치 {len(same)} / 그중 주장 있음 {len(with_claims)} / 오류 {errs}")
print(f"주장 총 {sum(types.values())}건, 유형별 {dict(types)}")
print(f"요일 붙은 주장 {wk} / 시간대 붙은 주장 {ts} / 방문일 명시 글 {vd}")
for place in dict.fromkeys(r["place_query"] for r in rows):
    s = [x for x in same if x["place_query"] == place]
    c = sum(len(x["extract"]["claims"]) for x in s)
    print(f"  {place}: 일치 {len(s)}건, 주장 {c}건")