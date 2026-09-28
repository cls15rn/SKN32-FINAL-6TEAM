"""ODsay 첫 확인 — 딱 2호출 (Basic 30/일 중 2건)

확인할 것
  1) searchPubTransPathT 1회에 경로 후보가 몇 개 오는지
  2) lang=1(영어)이 Basic 에서 되는지, 어느 필드가 영어로 오는지
  3) loadLane 이 경로 1개당 1호출인지, 응답 구조

규칙 (선택근거 D5)
  - 응답을 파일·로그로 저장하지 않는다. 콘솔에 요약만 찍는다
  - 이 스크립트의 출력도 복사해 문서에 붙이지 않는다 — 개수·예/아니오 같은 관측값만 메모

실행: python odsay_probe.py
필요: pip install requests python-dotenv  /  .env 에 ODSAY_API_KEY=...
엔드포인트·파라미터 이름은 [추정] — 에러가 나면 메시지를 그대로 알려줄 것
"""
import os
import sys
from urllib.parse import quote

import requests
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("ODSAY_API_KEY")
if not KEY:
    sys.exit("ODSAY_API_KEY 가 .env 에 없습니다")

BASE = "https://api.odsay.com/v1/api"
# 명동역 → 강남역 (9/14 링크 실측과 같은 쌍, 좌표는 공공데이터)
SX, SY = 126.986271, 37.560955
EX, EY = 127.02787893753, 37.4973854397871

TT = {1: "SUBWAY", 2: "BUS", 3: "WALK"}


def call(api, params):
    # apiKey 는 특수문자가 있을 수 있어 직접 인코딩
    url = f"{BASE}/{api}?apiKey={quote(KEY, safe='')}"
    r = requests.get(url, params=params, timeout=10)
    print(f"[{api}] HTTP {r.status_code}")
    data = r.json()
    if "error" in data:
        print("  ERROR:", data["error"])
        return None
    return data.get("result")


# ── 호출 1: 대중교통 길찾기 (영어) ─────────────────────────
res = call("searchPubTransPathT", {"SX": SX, "SY": SY, "EX": EX, "EY": EY, "lang": 1})
if res is None:
    sys.exit("길찾기 실패 — 위 에러 확인 (IP 미등록이면 인증 오류)")

paths = res.get("path", [])
print(f"\n후보 개수: {len(paths)}")
print(f"result 최상위 키: {sorted(res.keys())}")

for i, p in enumerate(paths[:5]):  # 앞 5개만 요약
    info = p.get("info", {})
    legs = []
    for s in p.get("subPath", []):
        t = TT.get(s.get("trafficType"), s.get("trafficType"))
        if t == "WALK":
            continue
        lane = (s.get("lane") or [{}])[0]
        name = lane.get("name") or lane.get("busNo") or "?"
        legs.append(f"{t}:{name} {s.get('startName')}→{s.get('endName')}")
    print(f"  #{i} {info.get('totalTime')}min | " + " / ".join(legs))

# 영어 여부는 위 줄에서 눈으로 확인: 역명·노선명이 영어인지 한글인지

# ── 호출 2: 노선 그래픽 (첫 후보만) ────────────────────────
map_obj = paths[0].get("info", {}).get("mapObj") if paths else None
print(f"\nmapObj 있음: {bool(map_obj)}")
if map_obj:
    lane_res = call("loadLane", {"mapObject": f"0:0@{map_obj}"})
    if lane_res:
        lanes = lane_res.get("lane", [])
        pts = sum(len(sec.get("graphPos", []))
                  for ln in lanes for sec in ln.get("section", []))
        print(f"  lane 개수: {len(lanes)} · 좌표점 합계: {pts}")
        print(f"  result 최상위 키: {sorted(lane_res.keys())}")

print("\n끝 — 오늘 남은 호출: 28건 (다른 곳에서 안 불렀다면)")