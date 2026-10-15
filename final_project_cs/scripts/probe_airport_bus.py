# -*- coding: utf-8 -*-
"""인천국제공항공사 공항버스 정보 **실제 호출** 탐침 — 응답 칸 · 지역별 줄 수 · 시간표 칸의 모양을 본다. `[2026-10-15]` 예약 모듈(공항리무진).

주소는 공공데이터포털 상세(15095045) 「요청주소」 그대로: `https://apis.data.go.kr/B551177/BusInformation/getBusInfo`
요청: serviceKey · numOfRows · pageNo · area(1 서울 · 2 경기 · 3 인천 · 4 강원 · 5 충청 · 6 경상 · 7 전라) · type=json.
문서상 출력: busnumber · toawfirst/toawlast(공항행 첫 · 막차) · t1endfirst/t2endfirst/t1endlast/t2endlast · adultfare · busclass · cpname ·
t1wdayt/t1wt/t2wdayt/t2wt(T1 · T2 평일 · 주말 시간표) · 노선 · 승차위치 — 칸 이름은 이 탐침으로 확인한다(「확인 안 함」을 지우는 근거).

응답은 저장하지 않는다 — 숫자 · 노선 번호 · 칸 이름만 출력. 호출 수: 기본 1회(서울 한 쪽), `--all` 이면 지역 7회. 개발 계정 하루 1,000.

실행 위치: final_project_cs (키는 .env.apikeys 의 공통 공공데이터 키 — 값은 출력하지 않는다)
  python -m scripts.probe_airport_bus            # 서울(area=1)
  python -m scripts.probe_airport_bus --all      # 7개 지역 줄 수만
"""
from __future__ import annotations

from datetime import datetime
import platform
import sys
import time
from typing import Any

import httpx

from app.core.settings import get_settings

URL = "https://apis.data.go.kr/B551177/BusInformation/getBusInfo"
AREAS = {1: "서울", 2: "경기", 3: "인천", 4: "강원", 5: "충청", 6: "경상", 7: "전라"}
TIME_FIELDS = ("t1wdayt", "t1wt", "t2wdayt", "t2wt")


def _rows(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], Any]:
    header = (payload.get("response") or {}).get("header") or {}
    body = (payload.get("response") or {}).get("body") or {}
    items = body.get("items")
    rows = items.get("item") if isinstance(items, dict) else items if isinstance(items, list) else []
    rows = [rows] if isinstance(rows, dict) else [r for r in rows or [] if isinstance(r, dict)]
    return f"{header.get('resultCode')} {header.get('resultMsg')}", rows, body.get("totalCount")


def call(key: str, area: int) -> tuple[str, list[dict[str, Any]], Any, float]:
    started = time.perf_counter()
    response = httpx.get(URL, params={"serviceKey": key, "numOfRows": "200", "pageNo": "1", "area": str(area), "type": "json"}, timeout=20)
    seconds = time.perf_counter() - started
    if response.status_code != 200:
        return f"HTTP {response.status_code}", [], None, seconds
    try:
        payload = response.json()
    except ValueError:
        return f"JSON 아님(앞 80자 {response.text[:80]!r})", [], None, seconds
    code, rows, total = _rows(payload)
    return code, rows, total, seconds


def main() -> None:
    settings = get_settings()
    key = settings.public_data_key("")
    if not key:
        print("공공데이터 키가 없어 멈춤")
        return
    print(f"기기 {platform.node()} · 시각 {datetime.now():%Y-%m-%d %H:%M}")
    areas = list(AREAS) if "--all" in sys.argv else [1]
    for area in areas:
        code, rows, total, seconds = call(key, area)
        with_times = [row for row in rows if any(str(row.get(name) or "").strip() for name in TIME_FIELDS)]
        print(f"\n== {AREAS[area]}(area={area}) · {seconds:.2f}초 · {code} · 전체 {total} · 받은 줄 {len(rows)} · 시간표 칸 있는 줄 {len(with_times)}")
        if area != 1 and "--all" in sys.argv:
            continue
        if rows:
            print("  칸 이름:", sorted(rows[0].keys()))
        for row in rows[:8]:
            times = {name: len([t for t in str(row.get(name) or "").replace(" ", "").split(",") if t]) for name in TIME_FIELDS}
            print(f"  {row.get('busnumber')} · {row.get('busclass')} · 성인 {row.get('adultfare')} · 공항행 {row.get('toawfirst')}~{row.get('toawlast')} · "
                  f"T1 {row.get('t1endfirst')}~{row.get('t1endlast')} · 시간표 개수 {times}")
        for row in rows[:3]:                                   # `[10-15]` 노선 정보 칸 모양 — 동네로 좁힐 수 있는지(앞 120자만)
            print(f"  노선 정보 {row.get('busnumber')}: {str(row.get('routeinfo') or '')[:120]}")
        sample = next((row for row in with_times), None)
        if sample:
            print("  시간표 칸 모양(첫 줄, 앞 5개):", {name: str(sample.get(name) or "")[:40] for name in TIME_FIELDS})


if __name__ == "__main__":
    main()
