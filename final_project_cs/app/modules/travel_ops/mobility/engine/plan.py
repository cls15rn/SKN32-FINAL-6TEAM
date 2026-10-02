# -*- coding: utf-8 -*-
"""이동 값 내놓기 — 32번 방(2026-09-25). 팀장이 꽂을 수 있는 **함수 하나 + CLI**.

    from app.modules.travel_ops.mobility.engine.plan import plan
    out = plan(places, items, party_size=2, constraints={"first_visit": True})
    body["items"] = out["items"]                                     # CreateTrip 의 기존 칸 그대로
    body["routes"] = {**body.get("routes", {}), **out["routes"]}     # 합친다(남겨 둔 입력 이동 항목의 route 보존)

    python -m app.modules.travel_ops.mobility.engine.plan --in trip_in.json --out trip_out.json

입력 = CreateTrip 의 기존 칸(`places[]`·`items[]`·`party_size`·`constraints`) — 새 칸 없음.
출력 = 출력 스펙 v1.4 의 기존 칸만 — 새 키 0. (23번 방 · 2026-09-25 · plan-v2)
  items[kind=mobility]  seq · kind · title · starts_at · ends_at · route
  routes{<키>}          from · to · planned · options[{id, label, eta_min, walk_m?, fare_krw?, uses}]
                        id 접두 = 수단 태그(subway_ · bus_ · walk · bike) · label = 경로 + 이유(축별 사실 · 순위 없음)
                        walk_m·fare_krw 는 팀 route_def 기존 칸 — 모르면 키를 뺀다(요금은 규칙 fare 절 · options.py · 54)
  그 밖에 봉투에 `skipped`(이동 항목을 못 만든 구간과 이유)·`left_out`(싣지 않은 후보와 이유)·`basis` 를 같이 준다 —
  **코어 몸통에는 `items`·`routes` 두 칸만 옮긴다**(CreateTrip 은 extra=forbid).

값의 뜻 (39 인계 §1-2 · 스펙 v1.3 §5)
  starts_at = 도착 목표(다음 항목 starts_at) − (eta_min + margin_min) − slack_min   (slack_min ≥ 0)
              = **그 구간이 성립하는 마지막 출발**(판정기의 worst 역산 · 버퍼 포함).
              slack_min 은 시간표가 분 단위로 연속이 아니라서 남는 몫이다 — 지하철은 「그 다음 편」이 늦으면
              출발을 더 미룰 수 없다. slack 을 빼고 식 그대로 쓰면 편성을 놓치는 시각이 나온다(결정 1).
  ends_at   = starts_at + eta_min
  eta_min   = 문→문 예정 소요(중앙값 · 버퍼 안 섞음 — 팀 density.py 가 자기 버퍼를 더한다)
  다음 항목까지 남는 간격(= margin_min + slack_min)이 곧 여유다. 여유 분은 따로 안 낸다(v1.3).

안 내는 것: last_feasible_depart_min · 후보별 출발 시각(23 결정 1) · 등급 · 내부 판정 넷 · p90·slack·verdict·reason·modes 등
            상세 값(계획서 작성 콜이 오면 `out` 에서 뽑는다 — 스펙 v1.3).

시각·날짜 (45 계약)
  `case.date` 는 **운행일**이다 — 04:00 전 시각은 그 운행일의 연장(00:30 → 전날 24:30).
  입력 시각은 ISO 8601. 오프셋이 없으면 서울 시각으로 읽는다(trip_api._seoul 과 같은 규칙).
  출력 시각은 항상 `+09:00`, 초 `:00`.
"""
from __future__ import annotations

import argparse
import collections
import copy
import json
import math
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import options as O
from .candidates import (MIX_CUTS_PER_ROUTE_PROPOSED, MIX_MAX_PROPOSED, ChainGenerator, MixedGenerator, interleave, mix_rule,
                         ride_estimator, xfer_rule)
from .options import line_name, station_name  # noqa: F401 — 32 시험·호출 쪽이 plan 에서 가져간다
from .geo import same_station
from .timeutil import MIN_DAY, SERVICE_DAY_START_MIN
from .verify_time import leg_mode

KST = timezone(timedelta(hours=9))
PLAN_VERSION = "plan-v2.4"   # (93 — 수단별 대표 후보 by_mode 는 켤 때만 · 기본 호출 결과가 같아 판 번호 유지) 87 — 지하철+버스 혼합 후보(환승 1회 · A 버스→지하철 · B 지하철→버스) · 지하철만·버스만 후보는 v2.3 과 같다
# (v2.3 · 86) 가장 이른 도착 모드(Planner.earliest · earliest_on_late) 추가 · 기본 호출 결과는 v2.2 와 같다
# (v2.2 · 58) modes 에 bike 를 주면 자전거 후보를 싣는다 · 기본(bike 없음)은 v2.1 과 같다 · 모양 무변경
# 56 (2026-09-27 · 본인) — modes 를 안 주면 지하철·버스·도보. 자전거는 modes 에 "bike" 를 줄 때만(48 결정 8 · ◆선호 「요청 시만」).
#   뺄 때는 **후보 생성 전에** 끊는다(아래 Planner — 따릉이 실시간·GraphHopper 호출 0).
DEFAULT_MODES = ("subway", "bus", "walk")
KNOWN_MODES = frozenset(DEFAULT_MODES) | {"bike"}
#: ☆`[2026-09-30 83 E1 · 85]` 장소마다 볼 역 개수 — 규칙 candidates.장소_역_후보_최대 **변경안** 값(27 규칙 32: 규칙 파일은
#:   모아서 한 번에 고친다). 규칙에 들어가면 규칙 값이 이긴다(Planner._station_k).
STATION_K_PROPOSED = 3
#: 역 짝을 어디까지 보나 — "first_feasible"(가까운 짝부터 · 대중교통 후보가 성립한 짝에서 멈춤) / "all"(짝 전부)
STATION_PAIR_MODE = "first_feasible"
#: 도보 상한 안 역이 모두 사고로 막혔을 때 이유 코드
STATION_BLOCKED_CODE = "no_service"
#: 역 짝 순번별 후보 번호(_n) 간격 — 동률 깨기 순서만(가까운 짝 우선). 식별은 _key 가 한다
PAIR_N_STEP = 1000
#: ☆`[2026-10-01 83 E2 · 86]` 가장 이른 도착(Planner.earliest) — 앞 일정 끝에 바로 떠났을 때 수단 후보마다 나온
#:   「성립하는 가장 이른 도착 목표」를 이른 순으로 몇 개까지 leg() 로 다시 확인하나. 확인은 기존 leg() 그대로라
#:   값·식(starts_at = 목표 − (eta + @) − slack)이 역산 모드와 같다. 규칙 값이 아니라 호출 횟수 상한(성능)이다.
#:   (GPT 86 Q2) 목표 사이 틈도 본다 — 목표 t 마다 t, t+1, …, t+EARLIEST_FILL_MIN 을 이른 순으로 확인하고, 확인 호출은
#:   EARLIEST_TRIES 번까지. 다 쓰고도 못 찾으면 「불성립」이 아니라 「탐색 한도로 미확인」(EARLIEST_UNCONFIRMED)으로 낸다.
EARLIEST_TRIES = 6
EARLIEST_FILL_MIN = 2
#: (GPT 86 Q1) 앞 일정 끝에 바로 떠나면 불가지만 **기다리면** 되는 판정기 이유 — 첫차 전 · 다음 편까지 공백 상한 초과 ·
#:   막차 뒤(다음 운행일 첫차). 이 소스는 출발을 뒤로 옮겨 본다(_after_wait).
EARLIEST_WAIT_CODES = frozenset({"before_first", "service_gap", "after_last"})
#: 출발을 뒤로 옮겨 보는 첫 칸(분)과 상한(분) — 두 배씩(15·30·…·480). 상한 8시간 = 막차 뒤(00:30 무렵) 끝나도 다음
#:   첫차(05:30 무렵)까지 닿는다. 규칙 값이 아니라 탐색 범위(성능)다. 넘으면 「탐색 한도로 미확인」.
EARLIEST_WAIT_STEP = 15
EARLIEST_WAIT_MAX_MIN = 480
#: 못 찾았지만 불성립을 확인한 것은 아닐 때의 이유 코드 — 탐색 한도·중단·생성 목표 밖(불성립과 구분 · GPT 86 Q2·R2)
EARLIEST_UNCONFIRMED = "earliest_unconfirmed"
#: leg(earliest_on_late=True) 가 earliest 를 붙이는 실패 이유 — **시각 때문에** 못 맞춘 것만(GPT 86 Q7 · 적용 범위).
#:   역·후보·데이터가 없어서(no_data · no_service 등) 못 만든 구간은 기다려도 안 되므로 붙이지 않는다.
EARLIEST_ON_CODES = frozenset({"arrive_late"}) | EARLIEST_WAIT_CODES
#: ☆`[2026-10-01 87 · 12 전달]` 지하철+버스 혼합 후보(⑤ · _mixed). 판정은 판정기(verify_case)가 하고 여기 값은 **얼마나 판정에
#:   넣나**(성능)만 정한다 — 규칙 값이 아니다. 상한(혼합_최대)·노선당 끊는 지점(혼합_끊는_지점_최대)은 규칙 변경안(candidates.py).
#:   · 생성 추정(est)이 지하철만·버스만 후보의 가장 좋은 값보다 소요 MIX_EST_TOL_MIN 분 · 도보 MIX_WALK_TOL_MIN 분 넘게 뒤지고 환승도
#:     적지 않으면 판정에 넣지 않는다(추정으로도 앞설 축이 없다). 판정에 넣는 혼합 후보는 구간마다 MIX_VERIFY_MAX 개까지(추정 소요 순).
MIX_VERIFY_MAX = 3
MIX_EST_TOL_MIN = 3
MIX_WALK_TOL_MIN = 1
#: 버스 운행 시간 거르기 여유(분) — 구간 [도착 목표 − 추정 소요, 도착 목표] 가 그 노선 첫차~막차 ±이 값과 안 겹치면 판정하지 않는다
MIX_WINDOW_TOL_MIN = 60
#: 혼합 후보 마지막 성립 출발 찾기(_mix_latest) 판정 호출 상한(성능)
MIX_LFD_STEPS = 10
#: 빠른 길 첫 출발이 이 이유로 불가면 판정기 역산(느린 길)으로 넘긴다 — 출발을 당기면 될 수 있는 이유(GPT 87 #1)
MIX_SLOW_CODES = frozenset({"after_last", "service_gap"})
#: 첫 성립 출발의 판정 소요로 「앞설 축 없음」을 미리 볼 때의 여유(분) — 마지막 성립 출발에서는 대기만큼 소요가 줄 수 있다
MIX_PROBE_TOL_MIN = 10
#: 혼합 후보 _n 시작(동률 깨기 순서 — 도보 0 · 지하철 짝 · 버스 100 · 자전거 200 다음)
MIX_N_BASE = 300
#: ☆`[2026-10-02 93 · 본인 요구]` 수단별 대표 후보(봉투 `by_mode` · leg/plan(by_mode=True)) — 지하철만 · 버스만 · 지하철+버스 · 택시
#:   칸마다 **하나씩**. 판정은 안 바꾼다 — 이미 성립을 확인한 후보에서 대표를 골라 출발·도착·소요를 같이 낸다(options[] 는 그대로).
#:   대표 = 다음 일정 시작에 맞추려면 **가장 늦게 떠나도 되는** 후보(성립 확인된 출발이 가장 늦은 것 · 계획 수단과 같은 규칙).
#:   ★예정 소요가 가장 짧거나 가장 일찍 도착하는 후보라는 뜻이 아니다 — 여유(@)·남는 분이 후보마다 달라 더 늦게 떠나도 소요는 더
#:   길 수 있다(GPT 93 #3). 「같은 시각에 떠나 가장 일찍 도착」은 공통 출발에서 다시 판정해야 나온다(이 칸은 하지 않는다). 단 환승이 더
#:   적은 후보가 이 분 안으로 따라오면(더 일찍 떠나야 하는 폭 ≤ 이 값) 그쪽을 대표로 한다(본인 10/2 — 「가장 이르지만 환승이 많은
#:   경로」를 막는다). 규칙 candidates.대표_환승_양보_분 **변경안** 값(27 규칙 32) — 규칙에 들어가면 규칙 값이 이긴다. grade 추정:
#:   무작위 80구간에서 환승이 더 적은 후보와의 차가 1~10분에 몰려 있다(지하철만 5·6 · 혼합 1·5·5·5·5·10 · 그 밖은 23분 이상).
BY_MODE_YIELD_MIN_PROPOSED = 5
#: by_mode 칸 이름(수단 키) — 순서는 표시 순서지 우열이 아니다
BY_MODE_KEYS = ("subway", "bus", "subway_bus", "taxi")
#: 수단을 호출 쪽이 뺐을 때(modes)의 이유 코드 — 후보가 없는 것과 구분한다
BY_MODE_NOT_REQUESTED = "not_requested"
#: 판정한 후보는 모두 불성립인데 **판정하지 않은 후보가 남아 있을 때**(혼합 판정 상한 · 추정 기반 생략)의 이유 코드 — 「없다」가
#:   아니라 「확인 못 함」(GPT 93 #1). 대표를 찾았어도 같은 사정이면 칸에 search_limited: true 를 붙인다.
BY_MODE_UNCONFIRMED = "unconfirmed"
#: 혼합 생성이 「판정하지 않고 넘긴 후보」를 접어 적는 코드
BY_MODE_LIMIT_CODES = frozenset({"mix_cap", "mix_skipped", "xfer_cap", "xfer_skipped"})
#: ☆`[2026-10-02 94 · 본인 「버스만이어도 환승은 있을 수밖에 없다」]` 환승 2회까지 후보 — **수단별 칸(by_mode)에서만** 만든다
#:   (options[]·계획 수단은 그대로 · 켜지 않으면 호출 0).
#:   · 「버스만」 칸: 직행 · 버스→버스(1회) · 버스→버스→버스(2회) · 「지하철+버스」 칸: 1회 혼합(87 A·B) · 2회(버스→지하철→버스 ·
#:     지하철→버스→지하철).
#:   ☆`[2026-10-03 94-2 · 본인 「환승 횟수가 적다고 추천 우선이 되면 안 된다 · 2회여도 더 빠르면 그쪽이 위」]` 단계에서 멈추지 않는다 —
#:     모든 단계의 성립 후보를 **한 목록**으로 대표 고르기(_by_mode_pick)에 넣는다: 대표 = 가장 늦게 떠나도 되는 후보 · 환승이 더 적은
#:     후보는 양보 분(candidates.대표_환승_양보_분 · 5) 안으로 따라올 때만 대표. 94 의 「앞 단계가 성립하면 다음 단계를 안 찾는다」는
#:     크게 돌아가는 1회 환승(170분)을 대표로 냈다(더 빠른 2회 85분이 있는데도).
#:   · 다음 단계를 **판정하는 조건**(본인 10/3 「추정이 더 빠를 때만」 · GPT 94-2 #3 으로 축을 출발 시각에 맞춤) = 앞 단계까지 대표
#:     자격 후보가 없거나, 추정으로 본 출발(도착 목표 − 가장 짧은 추정 소요 − 단계 버퍼)이 앞 단계 후보의 가장 늦은 출발보다 늦다.
#:     아니면 판정하지 않고 `xfer_skipped` 로 적는다(→ 칸 search_limited — 추정은 하한이 아니다 · 불가 확정 아님). 새 숫자는 없다.
#:   · 만남 반경을 넓히는 조건 = 그 반경에서 **후보가 하나도 없다**(생성 결과 · 본인 10/2). 전체 환승 수는 limits.transfers(동행 조건별)
#:     안 — 넘는 단계는 만들지 않고 이유를 남긴다.
#:   · 막는 값(노선쌍당 끊는 지점 · 가운데 노선 · 단계마다 판정에 넣는 수 · 소요 배수)은 규칙 변경안(candidates.py · XFER_*).
#:     단계 안에서 추정 소요가 그 단계 가장 짧은 후보 × candidates.허용_소요_배수(1.5 · 기존 규칙)를 넘는 후보는 판정하지 않는다.
#: 환승 후보 _n 시작(동률 깨기 순서 — 혼합 300 다음) · 단계(환승 수)마다 XFER_N_STEP
XFER_N_BASE = 400
XFER_N_STEP = 50
#: 만남 반경으로 차례로 쓰는 기존 규칙 칸(alternatives.*) — 새 숫자를 만들지 않는다
XFER_MEET_KEYS = ("정류장_동일_반경_m", "정류장_반경_m")
# 58 (2026-09-27 · ◆테마 ① 자전거 살림 · 본인) — 자전거 후보의 출발 시각 규칙(_bike_direct).
#   판정기는 시간표 없는 수단의 마지막 성립 출발(lfd)을 None 으로 낸다(verify_time._last_feasible_depart — 무수정).
#   따릉이는 24시간(rules bike.ddareungi.no_timetable · 확정)이라 「마지막 편」이 없고 소요가 출발 시각에 안 달린다 →
#     lfd = 도착 목표 − (eta + @)      @ = 판정기가 낸 margin_min(자전거는 스프레드 없음 → 단계 정책 버퍼)
#   그 lfd 에서 판정기로 **다시 봐서** 성립한 것만 싣는다(다른 후보와 같은 확인 · 식 start+eta+@+slack = 목표).
#   · 장소 좌표 기준(버스 직행 23 결정 4 와 같은 이유 — 역 경유면 장소→역→대여소 이중 도보). 판정기 multi 의 역 기준
#     자전거 후보는 싣지 않는다.
#   · 계획 단계(stage=planning)는 따릉이 실시간 거치를 **안 본다** — 지금 거치 대수는 계획한 출발 시각의 값이 아니다.
#     판정기가 「거치 미상 · 가용 근거없음」 경고로 내고, label 에 「대여 가능 여부는 출발 때 확인」을 붙인다.
#   · **자전거는 추천하지 않는다**(본인 9/27) — 여행자가 「자전거로 이동한다」고 했을 때만 호출 쪽이 modes=["bike","walk"]
#     (도보는 짧은 구간용)로 부른다. 지하철·버스와 섞어 주면 계획 수단 규칙(가장 늦게 떠나도 되는 후보)이 그대로라
#     더 일찍 떠나야 하는 자전거는 봉투 left_out 에 이유만 남는다. 자전거가 안 되는 구간은 skipped + 이유
#     (대중교통으로 몰래 바꾸지 않는다 — 입력 이동 항목은 그대로 남는다).

# ── 시각 ────────────────────────────────────────────────────────────────
def _parse_dt(v):
    """ISO 문자열·datetime → 서울 시각 aware datetime. 오프셋 없으면 서울로 읽는다."""
    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def service_day(dt):
    """서울 시각 → (운행일, 운행일 분). 04:00 전은 전날 운행일의 24:xx(45 계약)."""
    m = dt.hour * 60 + dt.minute
    d = dt.date()
    if m < SERVICE_DAY_START_MIN:
        return d - timedelta(days=1), m + MIN_DAY
    return d, m


def iso_of(service_date, minute):
    """(운행일, 운행일 분) → 'YYYY-MM-DDTHH:MM:00+09:00'. 24:xx 는 다음 날 벽시계로."""
    dt = datetime.combine(service_date, time(0, 0), tzinfo=KST) + timedelta(minutes=int(minute))
    return dt.strftime("%Y-%m-%dT%H:%M:00+09:00")


# ── 표기 ── (노선명·역명은 options.py — 23 이 잡는다. 여기서는 다시 내보내기만)
def uses_of(legs):
    """탄 역·갈아탄 역·내린 역만(환승역은 두 노선 각각) · 버스는 `버스:<노선번호>` · 도보·자전거는 안 적는다.
    ★ 버스가 지나는 `도로:` 는 아직 못 채운다 — 노선별 경유 도로 표가 없다(확인 안 한 것)."""
    out = []
    for leg in legs:
        m = leg_mode(leg)
        if m == "subway":
            ln = line_name(leg["line"])
            out += [f"{ln}:{station_name(leg['from'])}", f"{ln}:{station_name(leg['to'])}"]
        elif m == "bus":
            out.append(f"버스:{leg['route']}")
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def label_of(legs):
    parts = []
    for leg in legs:
        m = leg_mode(leg)
        if m == "subway":
            parts.append(f"{line_name(leg['line'])} {leg['from']}→{leg['to']}")
        elif m == "bus":
            parts.append(f"버스 {leg['route']} {leg['from']}→{leg['to']}")
        else:
            parts.append(f"{m} {leg.get('from')}→{leg.get('to')}")
    return " → ".join(parts)


# ── 입력 정리 ────────────────────────────────────────────────────────────
def _as_dict(x):
    if hasattr(x, "model_dump"):
        return x.model_dump()
    return dict(x)


def party_of(party_size, constraints):
    """판정기 party — 기존 칸에서만 만든다.
    constraints.mobility_ease == "needs_rest"(팀 density.py 가 읽는 값) → fatigue_high(환승 상한 1).
    party_size 는 지금 판정에 쓰는 자리가 없다(따릉이 인원 등은 22 규칙이 party 플래그로만 본다) — 그대로 싣기만 한다."""
    c = constraints or {}
    p = {}
    if party_size:
        p["size"] = int(party_size)
    if c.get("mobility_ease") == "needs_rest":
        p["fatigue_high"] = True
    # ☆`[2026-09-29 문제목록 #9]` 설문(constraints.survey)에서 판정에 쓸 수 있는 값을 옮긴다.
    #   domestic(내국인 여부) → foreign. 없으면 넣지 않는다. (73 후속 · v0.9.2 — 판정기는 이제 foreign 으로 외국인 안내를
    #   붙이지 않는다 · 본인 9/29 「자전거 따로 안내 안 함」. 값은 판정 로그 문맥으로만 실린다)
    #   party(여행자 구성)는 자유 문장이라 나이·유아를 **짐작해 뽑지 않는다**(지어내지 않는다).
    survey = c.get("survey") or {}
    if isinstance(survey, dict) and survey.get("domestic") is not None:
        p["foreign"] = not bool(survey["domestic"])
    return p


# ── 본체 ─────────────────────────────────────────────────────────────────
def _is_bus(o):
    return bool(o["_legs"]) and all(leg_mode(x) == "bus" for x in o["_legs"])


def _is_mixed(o):
    """지하철과 버스가 섞인 후보(87 혼합)."""
    ms = {leg_mode(x) for x in o.get("_legs") or []}
    return {"bus", "subway"} <= ms



def _multi(oa, ob):
    """장소→역 결과 둘로 verify_multi 의 multi 칸. 동명이역이면 노선군을 같이 싣는다(55 ④)."""
    m = {"from": oa[0], "to": ob[0]}
    if len(oa) > 2 and oa[2]:
        m["from_lines"] = list(oa[2])
    if len(ob) > 2 and ob[2]:
        m["to_lines"] = list(ob[2])
    return m


class Planner:
    def __init__(self, runtime, *, stage="planning", modes=None, display=False):
        self.rt = runtime
        # 후보 수단 거르기(예: {"subway"}) — None 이면 DEFAULT_MODES(자전거 뺌 · 56).
        #   빈 목록·모르는 수단은 거절한다(GPT 56 #9 — 빈 목록이 조용히 기본으로 넓어지지 않게).
        self.modes = set(DEFAULT_MODES) if modes is None else set(modes)
        if not self.modes or not self.modes <= KNOWN_MODES:
            raise ValueError(f"modes 는 {sorted(KNOWN_MODES)} 중 하나 이상 — 받은 값 {sorted(self.modes)}")
        self.trace = None                                 # 시험·대조용 — 리스트를 주면 구간마다 내부 값을 적는다
        # 56 ① — 판정기 싱글턴을 요청마다 **얕은 복사본**으로 쓴다(48 plan_estimate 와 같은 방식).
        #   verify_case 가 건마다 재할당하는 상태(_case_date · disr · _leg_cache · lfd_capped)가 복사본에만 남는다 —
        #   sync 엔드포인트(스레드풀)에서 두 요청이 같은 객체를 동시에 쓰면 섞이던 자리(24 ①).
        #   시간표·표·캐시(_passes/_origin/_dominant · 후보 그래프)는 공유한다 — 키가 입력 전부라 값이 요청과 무관하다.
        self.v = copy.copy(runtime._v)
        # 56 ② — 자전거를 안 볼 때는 복사본에서 따릉이 대여소 표를 뗀다 → verify_multi 가 자전거 후보를 **만들지 않는다**
        #   (따릉이 실시간·라우터 호출 0). 종전에는 modes 거르기가 후보 생성 뒤(leg() 의 후보 루프)라 빼도 호출이 났다.
        if "bike" not in self.modes:
            self.v.bk = None
        elif stage == "planning":
            self.v.bike_live = None       # 58 — 계획 단계는 지금 거치 대수를 안 본다(가용 근거없음 · 실시간 호출 0)
        self.stage = stage
        self.speed = self.v.R["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        self.detour = self.v.R["transfer"]["stop_station_walk"]["detour_factor"]["value"]
        # 표시 전용 필드(◆칸 — 답 전엔 만들어만 둔다). 켜면 transfer_car 를 options 에 싣는다(새 키 · 스펙 밖).
        self.display = display
        self._tc = O.TransferCar.load() if display else None

    #: ☆`[2026-09-29 문제목록 #38]` 사고 조건 — plan(disruptions=…) 로 받는다. 판정기 어휘(kind: line_closed ·
    #:   station_skip · edge_closed · route_closed)로 준다. 앞 판은 plan() 에 사고 입력이 없어 사고를 반영한 후보를
    #:   다시 만들 수 없었다. 모든 판정 호출에 같은 조건이 실린다.
    disruptions = ()

    def _vc(self, case):
        if self.disruptions:
            case = dict(case, disruptions=list(self.disruptions))
        return self.v.verify_case(case)

    def _vcq(self, case):
        """마지막 성립 출발 역산(lfd)을 끈 판정기 사본으로 판정 — 혼합 후보 전용(87). 판정·이유·소요·@·여유는 같고
        last_feasible_depart_min 만 비운다. 버스가 첫 구간인 후보는 판정기 역산이 도착 목표부터 1분씩 내려가며 전 구간을
        다시 판정해(구간당 수 초) 여기서 직접 찾는다(_mix_latest · plan_estimate 가 lfd 를 끄는 것과 같은 방식)."""
        if getattr(self, "_vq", None) is None:
            q = copy.copy(self.v)
            if q is self.v:
                self._vq = False          # 복사본을 못 만들면(시험의 복사 끔 등) 공유 판정기를 바꾸지 않는다 — lfd 켠 채로
            else:
                q.lfd_enabled = False
                self._vq = q
        if self._vq is False:
            return self._vc(case)
        if self.disruptions:
            case = dict(case, disruptions=list(self.disruptions))
        return self._vq.verify_case(case)

    def _walk_limit(self, party):
        return self.v._walk_limit(party)

    def _walk(self, straight_m):
        """장소 도보 분 = 직선 × 우회계수 ÷ 1.04 m/s (rules transfer.stop_station_walk — 정류장↔역과 같은 식)."""
        return math.ceil(straight_m * self.detour / self.speed / 60) if straight_m else 0

    def _walk_net(self, a_place, b_place, straight_m):
        """장소↔장소 도보 거리(m) — 보행망 라우터 foot 거리 → 없으면 직선 × 우회계수. (거리, 길 없음 여부).

        「길 없음」은 라우터가 **경로가 없다**고 답했을 때만이다. 라우터가 없거나 못 닿으면(no_router·router_down·
        router_error·bad_response) 길이 없다는 근거가 아니다 — 직선 식으로 낸다(verify_time._bike_walk 와 같은 규칙)."""
        br = getattr(self.v, "bike_router", None)
        if br is not None and br.available():
            prof = ((self.v.R.get("bike") or {}).get("ddareungi") or {}).get("ride", {}).get("walk_profile", "foot")
            r = br.route(prof, a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"])
            if r:
                return float(r["distance_m"]), False
            if (br.last_error or {}).get("kind") == "no_path":
                return None, True
        return straight_m * self.detour, False

    def _blocked_station(self, rec):
        """사고 조건(self.disruptions)으로 **그 물리적 역의 모든 노선**이 서지 않거나 운행하지 않으면 True.
        노선 하나만 막힌 환승역(예: 2호선만 무정차인 건대입구)은 막힌 역이 아니다 — 판정기가 그 노선만 뺀다."""
        if not self.disruptions:
            return False
        lines = set(self.v.sc.group_lines(rec)) - {None}
        if not lines:
            return False
        nm = rec["station_nm"]
        dead = set()
        for d in self.disruptions:
            k = d.get("kind")
            if k == "line_closed" and d.get("line") in lines:
                dead.add(d["line"])
            elif k == "station_skip" and d.get("station") == nm and d.get("line") in lines:
                dead.add(d["line"])
        return dead >= lines

    def _stop_skip_pred(self, lo, hi):
        """☆84 — 버스 정류장 무정차(stop_skip) 사고가 있으면 「그 노선이 그 정류장에 [lo, hi] 사이 서지 않나」 판별 함수.
        없으면 None(앞 판과 같은 정류장 짝). 탐색 폭(도착 목표 3시간 전 ~ 도착 목표)과 시간대가 걸치는 것만 — 이 함수는
        **다른 정류장 짝을 더 찾는 데**만 쓴다(기본 짝을 빼지 않는다 · _bus_direct). 걸리는지는 판정기가 실제 승하차 시각으로 본다."""
        from .timeutil import to_service_min
        sk = []
        for d in self.disruptions:
            if d.get("kind") != "stop_skip":
                continue
            w = d.get("window")
            if w and (to_service_min(w[1]) < lo or to_service_min(w[0]) > hi):
                continue
            sk.append((self.v.ars_norm(d.get("ars")), d.get("route") or None))
        if not sk:
            return None

        def pred(row):
            a = self.v.ars_norm(row.get("ars_id"))
            return a is not None and any(a == s and r in (None, row.get("route_nm")) for s, r in sk)
        return pred

    def _near_stations(self, place, limit_m, k=None):
        """장소 → 도보 상한 안 역 **역 좌표 기준 가까운 순 최대 k 개**(E1 · 문제목록 #38). 사고로 막힌 역(_blocked_station)은 뺀다.
        순서·상한은 역 좌표 직선 거리로 정하고(앞 판 near[0] 과 같은 기준), 돌려주는 거리 값은 그 역에서 장소에 가장
        가까운 출구까지(출구표가 없으면 역 좌표) — 정류장↔역 환승(19번)과 같은 방식. 그래서 목록이 출구 거리순은 아닐 수 있다.
        [(역명, 직선 m, 노선군)]. 노선군은 **동명이역(양평·신촌)일 때만** 그 물리적 역의 노선 목록(55 ④) — 아니면 None.
        ☆`[2026-09-30 83 E1]` 앞 판은 가장 가까운 역 하나(near[0])만 봐서 그 역이 무정차면 다음 역을 안 봤다."""
        sc = self.v.sc
        if sc is None:
            return []
        k = self._station_k() if k is None else k
        out = []
        for d, rec in sc.stations_near(place["lat"], place["lon"], limit_m):
            if self._blocked_station(rec):
                continue
            nm = rec["station_nm"]
            lines = (sorted(sc.group_lines(rec)) if getattr(sc, "is_ambiguous", None) and sc.is_ambiguous(nm) else None)
            if self.v.ex is not None:
                e = self.v.ex.nearest(nm, place["lat"], place["lon"], rec.get("line"))
                if e is not None:
                    d = e[0]
            out.append((nm, d, lines))
            if len(out) >= k:
                break
        return out

    def _near_station(self, place, limit_m):
        """장소 → 가장 가까운 (막히지 않은) 역 하나. (역명, 직선 m, 노선군) 또는 None — plan_estimate·시험이 쓴다."""
        near = self._near_stations(place, limit_m, 1)
        return near[0] if near else None

    def _station_k(self):
        """장소마다 볼 역 개수 상한 — 규칙 candidates.장소_역_후보_최대. ★규칙 파일 수정은 모아서 한 번에(27 규칙 32) —
        그 전까지는 변경안 값(STATION_K_PROPOSED)을 쓴다. 규칙에 들어가면 규칙 값이 이긴다."""
        n = (self.v.R.get("candidates") or {}).get("장소_역_후보_최대")
        k = n["value"] if n and n.get("value") is not None else STATION_K_PROPOSED
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ValueError(f"candidates.장소_역_후보_최대 는 1 이상 정수 — 받은 값 {k!r}")    # GPT 85 #5
        return k

    def _bus_direct(self, a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id, wlim, left=None):
        """장소 → 장소 한 노선 버스 후보. 후보마다 마지막 성립 출발에서 다시 판정해 성립한 것만 — 도보 짧은 순(상한은 leg())."""
        v = self.v
        if v.bus is None or (self.modes is not None and "bus" not in self.modes):
            return []
        radius = v.rv("alternatives", "정류장_반경_m")
        excluded = v.rv("bus", "route_type_제외") or []
        found = []
        pairs = list(enumerate(v.bus.routes_between(
            a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"], radius)))
        # ☆84 · GPT #6 — 정류장 무정차 사고가 있으면 **기본 짝은 그대로 두고**(실제 승하차가 사건 시간대 밖이면 유효하다 —
        #   미리 빼면 그 경로가 사라진다) 무정차 정류장을 뺀 다른 짝을 **더한다**. 어느 쪽이 성립하는지는 판정기가 실제
        #   시각으로 본다. 그때는 구간에 정류장 순번(from_seq·to_seq)을 실어 판정기가 **같은 행**을 쓰게 한다(같은 이름의
        #   정류장이 한 노선에 여러 번 있으면 이름만으로는 빼 둔 행을 다시 집는다). 사고가 없으면 앞 판과 같은 입력.
        pred = self._stop_skip_pred(arrive_by - 180, arrive_by)
        if pred is not None:
            seen = {(r.route_nm, x["seq"], y["seq"]) for _i, (r, x, y, *_r) in pairs}
            alt = [t for t in v.bus.routes_between(a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"],
                                                   radius, skip=pred)
                   if (t[0].route_nm, t[1]["seq"], t[2]["seq"]) not in seen]
            pairs += [(50 + k, t) for k, t in enumerate(alt)]
        for i, (r, x, y, _span, da, db) in pairs:
            if r.route_type_nm in excluded or max(da, db) > wlim:
                continue
            legs = [{"mode": "bus", "route": r.route_nm, "from": x["station_nm"], "to": y["station_nm"],
                     **({"from_seq": x["seq"], "to_seq": y["seq"]} if pred is not None else {})}]
            wi, wo = self._walk(da), self._walk(db)
            st_date, by_stop = service_day(arrive_dt - timedelta(minutes=wo))
            off = (st_date - sdate).days * MIN_DAY
            base = {"id": f"{case_id}/bus{i}", "date": st_date.isoformat(), "stage": self.stage, "legs": legs,
                    "arrive_by": by_stop, "party": party, "first_visit": first_visit, "no_alternatives": True}
            r1 = self._vc(dict(base, depart_at=max(SERVICE_DAY_START_MIN, by_stop - 180)))
            lfd = (r1.out or {}).get("last_feasible_depart_min")
            # ☆#29 — 판정한 뒤 버리는 버스 직행도 이유를 남긴다(정류장 반경·유형·도보 상한으로 거른 것은 후보가 아니라 안 남긴다)
            ref = {"_legs": legs, "_walk_m": (da + db) * self.detour, "_n": 100 + i, "_key": ("bus", 0, i),
                   "_route": label_of(legs)}

            def drop(code, reason):
                if left is not None:
                    left.append({"_o": ref, "label": ref["_route"], "code": code, "reason": reason})
            if lfd is None:
                drop("no_last_departure", "마지막 성립 출발을 역산하지 못했다 — " + ((r1.out or {}).get("reason") or r1.reason or ""))
                continue
            r2 = self._vc(dict(base, depart_at=lfd))
            o2 = r2.out or {}
            if o2.get("verdict") != "feasible" or o2.get("eta_min") is None or (o2.get("slack_min") or 0) < 0:
                drop("not_confirmed", "역산 출발로 다시 판정하니 성립이 아니다 — " + (o2.get("reason") or r2.reason or ""))
                continue
            eta = int(wi + o2["eta_min"] + wo)
            start = lfd - wi + off
            margin, slack = o2.get("margin_min") or 0, o2.get("slack_min") or 0
            if start + eta + margin + slack != arrive_by:
                drop("formula_mismatch", f"출발+소요+여유+남는 시간이 도착 목표 {arrive_by} 와 맞지 않는다")
                continue
            found.append((da + db, i, {
                "eta_min": eta, "uses": uses_of(legs), "_legs": legs, "_route": label_of(legs), "_start": start,
                "_transfers": 0, "_n": 100 + i, "_key": ("bus", 0, i), "_margin": margin, "_slack": slack,
                "_walk_min": wi + wo, "_walk_m": (da + db) * self.detour,
                "_fare": O.fare_of(v, legs, r2.legs),
                "_severe": [], "_covered": False, "_lr": r2.legs, "_day_type": r2.day_type,
                "_check": {"date": st_date.isoformat(), "legs": legs, "off": off, "walk_place_in": wi,
                           "walk_place_out": wo, "walk_stop_in": 0, "walk_stop_out": 0, "by_station": by_stop}}))
        found.sort(key=lambda t: (t[0], t[1]))
        return [o for _w, _i, o in found]          # 상한은 leg() 가 자격 검사 뒤에 자른다(GPT 23 #2)

    def _bike_direct(self, a_place, b_place, sdate, arrive_by, party, first_visit, case_id):
        """장소 → 장소 따릉이 후보(58). ([후보], None) 또는 ([], 이유 dict). modes 에 bike 가 없으면 ([], None).
        ① 출발 시각 없이 한 번 판정해 소요(eta)·@(margin)를 얻는다 → lfd = 도착 목표 − (eta + @)
        ② lfd 에서 도착 목표를 걸고 다시 판정. **소요·@ 가 ① 과 같을 때만** 싣는다(그때 slack = 0 — 마지막 성립 출발).
           다르면(실시간 대여소 선택이 바뀐 출발 전·진행 중 단계 등) 새 값으로 lfd 를 한 번 더 셈 — 최대 2회 ·
           수렴 안 하면 뺀다(GPT 58 #2·#3 — 「24시간」은 운행 근거일 뿐, 소요의 출발 시각 독립은 **같은 소요가 두 번
           나왔다**는 확인으로만 쓴다).
        대여소 도보·대여·반납은 판정기 자전거 구간 안에 있다(verify_leg_bike) — 장소 도보를 따로 안 더한다.
        ★ 한계(확인 안 한 것): lfd < 04:00 은 운행일 축 경계라 안 본다(GPT 58 #4 — 운행 불가가 아니라 표현 축 제한) ·
          인원수만큼의 대수는 판정기가 안 본다(거치 ≥1 · GPT 58 #6) — label 에 필요 대수만 적는다."""
        if "bike" not in self.modes:
            return [], None
        v = self.v
        legs = [{"mode": "bike",
                 "from": {"lat": a_place["lat"], "lng": a_place["lon"], "name": a_place["name"]},
                 "to": {"lat": b_place["lat"], "lng": b_place["lon"], "name": b_place["name"]}}]
        base = {"id": f"{case_id}/bike", "date": sdate.isoformat(), "stage": self.stage, "legs": legs,
                "party": party, "first_visit": first_visit, "no_alternatives": True}
        r1 = self._vc(dict(base, depart_at=arrive_by))
        o1 = r1.out or {}
        if o1.get("verdict") != "feasible" or o1.get("eta_min") is None or o1.get("margin_min") is None:
            return [], {"code": o1.get("code") or "no_data", "reason": "자전거 — " + (o1.get("reason") or r1.reason or "소요를 못 냈다")}
        prev = (o1["eta_min"], o1["margin_min"])
        for _ in range(2):
            lfd = arrive_by - sum(prev)
            if lfd < SERVICE_DAY_START_MIN:
                # 04:00 전 출발은 앞 운행일 축이다 — 정수 분으로 넘기면 판정기가 +24h 로 읽는다(leg() 주석). 안 본다.
                return [], {"code": "no_data", "reason": "자전거 — 출발이 04:00 전(운행일 경계)이라 보지 않는다(표현 축 제한)"}
            r2 = self._vc(dict(base, depart_at=lfd, arrive_by=arrive_by))
            o2 = r2.out or {}
            if o2.get("eta_min") is None or o2.get("margin_min") is None:
                return [], {"code": o2.get("code") or "no_data",
                            "reason": "자전거 — 역산 출발에서 다시 보니 소요를 못 냈다: " + (o2.get("reason") or r2.reason or "")}
            got = (o2["eta_min"], o2["margin_min"])
            if got != prev:
                prev = got                      # 소요·@ 가 바뀌었다 — 새 값으로 한 번 더(성립 여부는 그 뒤에 본다)
                continue
            break
        else:
            return [], {"code": "no_data", "reason": "자전거 — 판정마다 소요가 달라 출발 시각을 정하지 못했다"}
        if o2.get("verdict") != "feasible" or (o2.get("slack_min") or 0) != 0:
            return [], {"code": o2.get("code") or "no_data",
                        "reason": "자전거 — 역산 출발에서 다시 보니 성립이 아니다: " + (o2.get("reason") or r2.reason or "")}
        eta, margin = int(prev[0]), prev[1]
        if lfd + eta + margin != arrive_by:
            return [], {"code": "no_data", "reason": "자전거 — 역산 식이 맞지 않는다(내지 않는다)"}
        walk_min = sum((lr.walk_min or 0) for lr in r2.legs)
        n = int(party.get("size") or 1)
        return [{"eta_min": eta, "uses": [], "_legs": legs,
                 "_route": f"자전거(따릉이) {a_place['name']}→{b_place['name']}"
                           + (f" · {n}명 — {n}대 필요" if n > 1 else "")
                           + (" · 대여 가능 여부는 출발 때 확인" if self.stage == "planning" else ""),
                 "_start": lfd, "_transfers": 0, "_n": 200, "_key": ("bike", 0, 0), "_margin": margin, "_slack": 0,
                 "_walk_min": walk_min, "_walk_m": None,           # 대여소 도보 m 은 판정기 밖으로 안 나온다 — 키를 뺀다
                 "_fare": O.fare_of(v, legs, r2.legs), "_severe": [], "_covered": False,
                 "_lr": r2.legs, "_day_type": r2.day_type,
                 "_check": {"date": sdate.isoformat(), "legs": legs, "off": 0, "walk_place_in": 0,
                            "walk_place_out": 0, "walk_stop_in": 0, "walk_stop_out": 0, "by_station": arrive_by}}], None

    # ── ⑤ 지하철+버스 혼합(87 · 12 전달 · 85 E1 2단) ─────────────────────────────
    def _mix_gen(self, party, first_visit):
        """혼합 후보 생성기 — 버스·역 좌표·지하철 그래프가 다 있고 modes 에 지하철·버스가 둘 다 있을 때만. 사고 조건 중 운행
        중단 노선은 지하철 탐색에서 피하고(avoid_lines), 무정차 역은 그 노선으로 끊지 않는다(skip_at) — 판정은 판정기가 한다."""
        v = self.v
        if v.bus is None or v.sc is None or not {"subway", "bus"} <= self.modes:
            return None
        lim, _ = v._party_limit(party)
        avoid = {d.get("line") for d in self.disruptions if d.get("kind") == "line_closed" and d.get("line")}
        skip = {(d.get("line"), d.get("station")) for d in self.disruptions
                if d.get("kind") == "station_skip" and d.get("line") and d.get("station")}

        ride = ride_estimator(v)
        self._ride_est = ride
        radius = v.rv("alternatives", "정류장_반경_m")
        return MixedGenerator(
            v.candidate_graph(first_visit), v.bus, v.sc, v.ex, radius_m=radius, near_m=radius,
            cuts=mix_rule(v.R, "혼합_끊는_지점_최대", MIX_CUTS_PER_ROUTE_PROPOSED), tlim=lim,
            excluded=v.rv("bus", "route_type_제외") or [], ride_min=ride,
            wayfinding=v.R["transfer"]["wayfinding_addition_min"]["value"] if first_visit else 0,
            walk_speed=self.speed, detour=self.detour, avoid_lines=avoid, skip_at=skip)

    def _mixed_one(self, mc, i, a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id, best=None,
                   key=None, n=None, tag=None):
        ratio = self.v.R["candidates"]["허용_소요_배수"]["value"]
        """혼합 후보 하나를 판정기로 — 마지막 성립 출발(lfd) 역산 → 그 출발로 다시 판정 → 식 확인(버스 직행 _bus_direct 와 같은 순서).
        (후보 dict, None) 또는 (None, 뺀 이유 dict)."""
        v = self.v
        legs = mc.legs
        wi, wo = self._walk(mc.walk_in_m), self._walk(mc.walk_out_m)
        st_date, by_stop = service_day(arrive_dt - timedelta(minutes=wo))
        off = (st_date - sdate).days * MIN_DAY
        # ☆94 — 환승 후보(버스만 BB·BBB · 혼합 2회 BSB·SBS)도 같은 순서로 판정한다: key·n·tag 를 부르는 쪽이 준다(87 기본값 그대로)
        key = key or ("mix", 0 if mc.shape == "A" else 1, i)
        n_ = MIX_N_BASE + i if n is None else n
        tag = tag or f"혼합 {mc.shape}"
        ref = {"_legs": legs, "_walk_m": None, "_n": n_, "_key": key, "_route": label_of(legs)}
        # ☆94 — 같은 leg() 안에서 같은 혼합 후보를 두 번 판정하지 않는다(options 용 판 + 수단별 칸의 조건 없는 판 · 93 의 켬 +26~44%
        #   출발점). 「앞설 축 없음」으로 일찍 멈춘 답(비교 대상 best 에 달린 답)은 담지 않는다 — 그 밖의 답은 best 와 무관하다.
        #   번호(_n·_key)는 부를 때의 순번으로 다시 붙여 재사용 여부가 결과를 바꾸지 않는다.
        memo = getattr(self, "_mix_memo", None)
        #   (GPT 94 #7) 키에 **판정에 드는 값 전부** — 구간열 · 접근·이탈·환승 도보 · 환승 수 · 도착 목표 · 단계. 구간열 이름이 같아도
        #   접근 정류장·역이 달라 도보가 다르면 다른 칸이다(동행 조건·첫 방문·사고 조건은 같은 leg() 안에서 고정).
        mk = ((mc.key(), mc.walk_in_m, mc.walk_out_m, mc.link_m, mc.sub_walk_min, mc.transfers, arrive_by, self.stage)
              if memo is not None else None)
        if mk is not None and mk in memo:
            o_, why_ = memo[mk]
            if o_ is not None:
                return dict(o_, _n=n_, _key=key), None
            return None, dict(why_, _o=ref)

        def drop(code, reason):
            return None, {"_o": ref, "label": ref["_route"], "code": code, "reason": f"{tag} — {reason}"}
        if by_stop < SERVICE_DAY_START_MIN:
            return drop("no_last_departure", "도착 목표가 04:00 전(운행일 경계)이라 보지 않는다")
        base = {"id": f"{case_id}/mix{i}", "date": st_date.isoformat(), "stage": self.stage, "legs": legs,
                "arrive_by": by_stop, "party": party, "first_visit": first_visit, "no_alternatives": True}
        link_min = math.ceil(mc.link_m * self.detour / self.speed / 60)
        walk_min = wi + wo + link_min + (mc.sub_walk_min or 0)

        def hopeless(o):
            """첫 성립 출발의 판정 소요로 봐도 지하철만·버스만 후보보다 앞설 축이 없다(소요는 마지막 성립 출발에서 대기만큼 줄 수
            있어 MIX_PROBE_TOL_MIN 을 둔다) — 마지막 성립 출발을 끝까지 찾지 않는다(성능)."""
            if best is None or o.get("eta_min") is None:
                return False
            eta = wi + o["eta_min"] + wo - MIX_PROBE_TOL_MIN
            return eta > best[0] * ratio or (eta >= best[0] and mc.transfers >= best[1] and walk_min >= best[2])

        def drop_keep(code, reason):
            """best(비교 대상)와 무관한 탈락 — 담아 둔다."""
            out = drop(code, reason)
            if mk is not None:
                memo[mk] = out
            return out
        lfd, r2, why = self._mix_latest(base, by_stop, mc.est_min - wi - wo, hopeless)
        if lfd is None and why == "hopeless":
            o = r2.out or {}
            return drop("mix_dominated", f"지하철만·버스만 후보보다 소요·환승·도보 어느 축에서도 앞서지 않는다(첫 성립 출발 판정: "
                                         f"소요 {wi + o['eta_min'] + wo}분 vs {best[0]} · 환승 {mc.transfers} vs {best[1]} · "
                                         f"도보 {walk_min}분 vs {best[2]})")
        if lfd is None:
            return drop_keep("no_last_departure", "마지막 성립 출발을 찾지 못했다 — " + why)
        o2 = r2.out or {}
        if o2.get("verdict") != "feasible" or o2.get("eta_min") is None or (o2.get("slack_min") or 0) < 0:
            return drop_keep("not_confirmed", "역산 출발로 다시 판정하니 성립이 아니다 — " + (o2.get("reason") or r2.reason or ""))
        eta = int(wi + o2["eta_min"] + wo)
        start = lfd - wi + off
        margin, slack = o2.get("margin_min") or 0, o2.get("slack_min") or 0
        if start + eta + margin + slack != arrive_by:
            return drop_keep("formula_mismatch", f"출발 {start} + 소요 {eta} + 여유 {margin} + 남는 {slack} ≠ 도착 목표 {arrive_by}")
        # 도보 — 장소↔정류장/역(직선×우회) + 정류장↔역 환승(판정기와 같은 식 · 같은 출구 기준 직선 · 위 walk_min) + 지하철 안 환승(거리표 m)
        #   ☆94 — 지하철 구간이 버스로 끊겨 있으면(지하철→버스→지하철) **이어진 묶음마다** 따로 잰다 · 버스만이면 지하철 안 환승 0
        runs, cur = [], []
        for x in legs:
            if leg_mode(x) == "subway":
                cur.append(x)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        inner = 0.0
        for run in runs:
            if len(run) > 1:
                w_ = O.transfer_walk_m(v, run)
                inner = None if (w_ is None or inner is None) else inner + w_
        walk_m = None if inner is None else (mc.walk_in_m + mc.walk_out_m + mc.link_m) * self.detour + inner
        got = {"eta_min": eta, "uses": uses_of(legs), "_legs": legs, "_route": label_of(legs), "_start": start,
                "_transfers": mc.transfers, "_n": n_, "_key": key, "_margin": margin, "_slack": slack,
                "_walk_min": walk_min, "_walk_m": walk_m,
                "_fare": O.fare_of(v, legs, r2.legs), "_severe": O.severe_hits(r2.warnings),
                "_covered": O.congestion_checked(v, legs, r2.legs, st_date, r2.day_type),
                "_lr": r2.legs, "_day_type": r2.day_type, "_shape": mc.shape,
                "_check": {"date": st_date.isoformat(), "legs": legs, "off": off, "walk_place_in": wi,
                           "walk_place_out": wo, "walk_stop_in": 0, "walk_stop_out": 0, "by_station": by_stop,
                           "fast": True}}
        if mk is not None:
            memo[mk] = (got, None)
        return got, None

    def _mix_latest(self, base, by, est, hopeless=None):
        """혼합 후보의 **마지막 성립 출발**(판정기 lfd 와 같은 뜻 — 그 출발에서 최악 도착 + 버퍼 ≤ 도착 목표인 가장 늦은 분).
        (lfd, 그 출발의 판정 답, 못 찾은 이유) · 이유 "hopeless" = 부르는 쪽 판단으로 끝까지 찾지 않음.

        빠른 길(판정기 사본 _vcq · lfd 끔): 추정 소요로 첫 출발을 잡고 → 성립이면 여유(slack)만큼 늦춰 불성립 자리를 찾고 →
        불성립이면 넘친 만큼 당긴다 → 성립·불성립 이웃(1분 차)이 될 때까지 이분. 이 길은 **성립이 출발 시각에 단조**(늦게 떠나면
        늦게 닿는다)일 때만 판정기 역산과 같은 분을 낸다 — 판정기 역산은 위에서부터 1분씩(지하철 첫 구간은 편성마다) 내려와
        **처음 성립하는 분**을 고르므로 비단조 구간에서도 「가장 늦은」 것이 맞고, 이분은 그 보장이 없다(GPT 87 #1).
        ☆ 그래서 빠른 길이 결론을 못 내면 **판정기 역산(느린 길 · _vc)으로 넘긴다**: 첫 출발이 늦어서가 아닌 이유로 불가(막차 뒤 ·
          첫차 전 · 공백 · 근거없음 — 당기면 될 수도 있다) · 호출 한도(MIX_LFD_STEPS) 안에 이웃을 못 만듦 · 04:00 경계.
          빠른 길이 이웃을 찾았을 때만 그 값을 쓴다(비단조로 더 늦은 성립이 위에 있으면 놓칠 수 있다 — 낸 출발은 재판정으로
          성립 확인된 값이라 늦게 떠나는 일은 없다 · 일찍 떠나는 쪽으로만 틀린다 · 시험: 실데이터 A·B 에서 판정기 lfd 와 같음)."""
        n = [0]

        def at(t):
            n[0] += 1
            r = self._vcq(dict(base, depart_at=t))
            o = r.out or {}
            ok = o.get("verdict") == "feasible" and o.get("eta_min") is not None and (o.get("slack_min") or 0) >= 0
            return ok, r, o

        def slow(why):
            """판정기 역산 — 위에서부터 첫 성립(판정기 lfd 그대로). 그 출발을 사본으로 다시 판정해 같은 모양으로 돌려준다."""
            r1 = self._vc(dict(base, depart_at=max(SERVICE_DAY_START_MIN, by - 180)))
            lfd = (r1.out or {}).get("last_feasible_depart_min")
            if lfd is None:
                return None, None, (r1.out or {}).get("reason") or r1.reason or why
            ok, r, o = at(lfd)
            return (lfd, r, "") if ok else (None, None, o.get("reason") or r.reason or why)

        t = max(SERVICE_DAY_START_MIN, int(by - math.ceil(max(est, 1))))
        good, bad, why = None, None, ""
        while n[0] < MIX_LFD_STEPS:                         # ① 성립하는 출발 하나 — 넘친 만큼 당긴다
            ok, r, o = at(t)
            if ok:
                good = (t, r, o)
                if hopeless is not None and hopeless(o):
                    return None, r, "hopeless"              # 끝까지 찾아도 실리지 않는다(부르는 쪽 판단) — 여기서 멈춘다
                break
            bad, why = t, (o.get("reason") or r.reason or "")
            w = o.get("arrive_worst_min")
            over = None if w is None else int(w + (o.get("buffer_min") or 0) - by)
            if over is None or over <= 0:
                # 늦어서가 아니다. 당기면 될 수 있는 이유(막차 뒤 · 다음 편 공백)만 판정기 역산으로 넘긴다 — 환승 도보 상한 ·
                #   근거없음 · 첫차 전(당기면 더 이르다) 등은 출발 시각을 바꿔도 같아서 느린 길을 타지 않는다
                return slow(why) if o.get("code") in MIX_SLOW_CODES else (None, None, why)
            t -= over
            if t < SERVICE_DAY_START_MIN:
                return slow(why)
        if good is None:
            return slow(why or "탐색 한도 안에서 성립 출발 없음")
        while bad is None and n[0] < MIX_LFD_STEPS:         # ② 그 위 불성립 자리 — 여유만큼 늦춘다
            t2 = good[0] + max(1, int(good[2].get("slack_min") or 0))
            ok, r, o = at(t2)
            if ok:
                good = (t2, r, o)
            else:
                bad = t2
        while bad is not None and bad - good[0] > 1 and n[0] < MIX_LFD_STEPS:   # ③ 이분
            mid = (good[0] + bad) // 2
            ok, r, o = at(mid)
            if ok:
                good = (mid, r, o)
            else:
                bad = mid
        if bad is None or bad - good[0] > 1:
            return slow("")                                 # 한도 안에 이웃을 못 만듦 — 「가장 늦은」 확인이 안 됐다
        return good[0], good[1], ""

    def _phys_set(self, stations):
        """장소 쪽 역 목록 [(역명, m, 노선군)] → 물리적 역 키 — 혼합이 끊지 않을 역(지하철 후보가 이미 그 역에서 타고 내린다)."""
        out = set()
        for nm, _d, ls in stations or []:
            rec = self.v.sc.resolve(nm, ls)
            out.add(self.v.sc.phys_key(rec) if rec is not None else nm)
        return out

    def _in_service(self, mc, lo, hi):
        """그 버스를 [lo, hi] 무렵 탈 수 있어 보이나(탐색 순서용 추정 — 판정 아님 · GPT 87 #4). 승차 정류장 통과 시각 =
        기점 첫차·막차 + 기점→승차 정류장 승차 추정(ride_estimator) · ±MIX_WINDOW_TOL_MIN · 다음 운행일(+1440)도 본다."""
        bus = self.v.bus
        r = bus.by_id.get(getattr(mc, "route_id", None)) if bus is not None else None
        if r is None or r.first_min is None or r.last_min is None:
            return True
        rows = bus.stops.get(r.route_id, [])
        off = 0.0
        ride = getattr(self, "_ride_est", None)
        if ride is not None and rows and getattr(mc, "board", None) is not None:
            off = ride(r, rows[0], mc.board) or 0.0
        for day in (0, MIN_DAY):
            first = r.first_min + off + day - MIX_WINDOW_TOL_MIN
            last = r.last_min + off + day + MIX_WINDOW_TOL_MIN
            if not (first > hi or last < lo):
                return True
        return False

    def _mixed(self, a_place, b_place, sa, sb, arrive_dt, sdate, arrive_by, party, first_visit, case_id, wlim, opts, left):
        """⑤ 혼합 후보(87). A = 장소 a 근처 정류장 → 버스 → 역 앞에서 내려 → 지하철 → 장소 b 도보 상한 안 역(sb) ·
        B = 장소 a 도보 상한 안 역(sa) → 지하철 → 역 앞 정류장 → 버스 → 장소 b 근처 정류장. 끊는 역은 지하철 후보가 그 장소에서
        이미 본 역(A = sa · B = sb)만 뺀다(GPT 87 #6). **지하철만·버스만 후보(opts)보다 소요·환승·도보 중 하나라도 앞설 때만 싣고**,
        뒤지면 left(`mix_dominated`) · 상한(혼합_최대)을 넘으면 left(`mix_cap`). 사고로 장소 b 쪽 걸어갈 역이 모두 막히면(sb 빈 목록)
        B 가 그 구간의 대안이 된다(85 E1 2단)."""
        gen = self._mix_gen(party, first_visit)
        if gen is None:
            return []
        ca = (gen.bus_to_subway(a_place["lat"], a_place["lon"], [(nm, ls, d) for nm, d, ls in sb], wlim,
                                excl_st=self._phys_set(sa)) if sb else [])
        cb = (gen.subway_to_bus([(nm, ls, d) for nm, d, ls in sa], b_place["lat"], b_place["lon"], wlim,
                                excl_st=self._phys_set(sb)) if sa else [])
        cands = interleave(ca, cb)          # 두 모양을 번갈아 — 판정 상한 안에서 한 모양만 보지 않게
        # 운행 시간 추정 밖(심야버스 낮 구간 등)은 **뒤로 미룬다**(GPT 87 #4 — 판정 전 확정 탈락이 아니라 탐색 순서) — 안쪽 후보가
        #   하나도 없을 때만 판정한다(밖 후보는 판정기가 대개 첫차 전·막차 뒤로 내고, 그 마지막 성립 출발 찾기는 느린 길이다)
        win = [mc for mc in cands if self._in_service(mc, arrive_by - mc.est_min - MIX_WINDOW_TOL_MIN, arrive_by)]
        out_win = [mc for mc in cands if mc not in win] if win else []
        cands = win or cands
        if gen.skipped_airport:
            left.append({"_o": {"_legs": []}, "label": "공항버스(공항행) 혼합", "code": "no_data",
                         "reason": f"공항으로 가는 공항버스({', '.join(sorted(set(gen.skipped_airport))[:5])}) — 공항행 시각 근거가 "
                                   "판정기에 없어(37) 혼합 후보로 만들지 않는다"})
        if not cands:
            return []
        base = [o for o in opts if o["_legs"] and not _is_mixed(o) and not any(leg_mode(x) == "bike" for x in o["_legs"])]
        best = ((min(o["eta_min"] for o in base), min(o["_transfers"] for o in base), min(o["_walk_min"] for o in base))
                if base else None)
        mmax = mix_rule(self.v.R, "혼합_최대", MIX_MAX_PROPOSED)
        ratio = self.v.R["candidates"]["허용_소요_배수"]["value"]
        kept, n_ver, n_est, n_cap, n_none = [], 0, 0, 0, 0
        n_win = len(out_win)
        for i, mc in enumerate(cands):
            if len(kept) >= mmax or n_ver >= MIX_VERIFY_MAX:
                n_cap += 1
                continue
            if not gen.materialize(mc):
                n_none += 1           # 환승 상한 안에서 지하철 구간열을 못 찾았다(생성기 추정 표와 실제 탐색이 다른 드문 경우)
                continue
            if best is not None:
                est_walk = (mc.walk_in_m + mc.walk_out_m + mc.link_m) * self.detour / self.speed / 60 + (mc.sub_walk_min or 0)
                if mc.est_min > best[0] * ratio + MIX_EST_TOL_MIN or not (
                        mc.est_min < best[0] + MIX_EST_TOL_MIN or mc.transfers < best[1]
                        or est_walk < best[2] + MIX_WALK_TOL_MIN):
                    n_est += 1
                    continue
            n_ver += 1
            o, why = self._mixed_one(mc, i, a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id, best)
            if o is None:
                left.append(why)
                continue
            if best is not None and o["eta_min"] > best[0] * ratio:
                left.append({"_o": o, "label": o["_route"], "code": "mix_dominated",
                             "reason": f"혼합 {mc.shape} — 소요 {o['eta_min']}분이 가장 짧은 지하철만·버스만 후보 {best[0]}분 × "
                                       f"허용_소요_배수 {ratio:g} 를 넘는다(환승·도보가 적어도 대표안이 아니다)"})
                continue
            if best is not None and not (o["eta_min"] < best[0] or o["_transfers"] < best[1] or o["_walk_min"] < best[2]):
                left.append({"_o": o, "label": o["_route"], "code": "mix_dominated",
                             "reason": f"혼합 {mc.shape} — 지하철만·버스만 후보보다 소요·환승·도보 어느 축에서도 앞서지 않는다 "
                                       f"(소요 {o['eta_min']}분 vs {best[0]} · 환승 {o['_transfers']} vs {best[1]} · "
                                       f"도보 {o['_walk_min']}분 vs {best[2]})"})
                continue
            kept.append(o)
        if n_none:
            left.append({"_o": {"_legs": []}, "label": f"그 밖 혼합 후보 {n_none}개", "code": "mix_no_subway",
                         "reason": "환승 상한 안에서 지하철 쪽 구간열을 찾지 못했다"})
        # ☆(GPT 87 #5) 판정 전에 건너뛴 후보는 「뒤진다」로 확정하지 않는다 — 추정(est · 운행 시간)은 하한이 아니다. 코드도 따로.
        if n_win:
            left.append({"_o": {"_legs": []}, "label": f"그 밖 혼합 후보 {n_win}개", "code": "mix_skipped",
                         "reason": f"추정 기반 탐색 생략 — 승차 정류장 운행 시간(추정 ±{MIX_WINDOW_TOL_MIN}분) 밖이라 안쪽 후보를 먼저 판정했다(불가 확정 아님)"})
        if n_est:
            left.append({"_o": {"_legs": []}, "label": f"그 밖 혼합 후보 {n_est}개", "code": "mix_skipped",
                         "reason": "추정 기반 탐색 생략 — 생성 추정으로 지하철만·버스만 후보보다 앞설 축이 안 보여 판정하지 않았다(뒤진다고 확정한 것 아님)"})
        if n_cap:
            left.append({"_o": {"_legs": []}, "label": f"그 밖 혼합 후보 {n_cap}개", "code": "mix_cap",
                         "reason": f"혼합 상한 {mmax}개(싣는 것) · 판정 {MIX_VERIFY_MAX}개(구간마다)를 넘어 판정하지 않았다 — A·B 번갈아 생성 추정 소요 순(불가 확정 아님)"})
        return kept

    def _mixed_sources(self, a_place, b_place, sa, sb, nb, party, first_visit, wlim):
        """가장 이른 도착(86) 목표 생성용 혼합 후보 — 앞 일정 끝(nb) 무렵 운행하는 노선 · 구간열이 나오는 것 · 추정 소요 순
        MIX_VERIFY_MAX 개. 생성기가 없으면 []."""
        gen = self._mix_gen(party, first_visit)
        if gen is None:
            return []
        ca = (gen.bus_to_subway(a_place["lat"], a_place["lon"], [(nm, ls, d) for nm, d, ls in sb], wlim,
                                excl_st=self._phys_set(sa)) if sb else [])
        cb = (gen.subway_to_bus([(nm, ls, d) for nm, d, ls in sa], b_place["lat"], b_place["lon"], wlim,
                                excl_st=self._phys_set(sb)) if sa else [])
        cands = interleave(ca, cb)
        # (GPT 87 #4) 거르지 않고 순서만 — 앞 일정 끝(nb)부터 대기 상한까지(다음 운행일 첫차 포함) 탈 수 있어 보이는 노선을 먼저
        hi = nb + EARLIEST_WAIT_MAX_MIN
        cands = ([mc for mc in cands if self._in_service(mc, nb, hi + mc.est_min)]
                 + [mc for mc in cands if not self._in_service(mc, nb, hi + mc.est_min)])
        out = []
        for mc in cands:
            if len(out) >= MIX_VERIFY_MAX:
                break
            if gen.materialize(mc):
                out.append(mc)
        return out

    # ── 환승 2회까지(94 · 수단별 칸에서만) ─────────────────────────────────────────
    def _chain_gen(self, party, first_visit):
        """환승 후보 생성기(candidates.ChainGenerator) — 버스 자료가 있을 때만. 지하철 쪽 조각(역 좌표·그래프)은 혼합 2회 모양만 쓴다.
        (생성기, 환승 상한, 상한의 조건 이름)."""
        v = self.v
        if v.bus is None:
            return None, None, None
        lim, which = v._party_limit(party)
        avoid = {d.get("line") for d in self.disruptions if d.get("kind") == "line_closed" and d.get("line")}
        skip = {(d.get("line"), d.get("station")) for d in self.disruptions
                if d.get("kind") == "station_skip" and d.get("line") and d.get("station")}
        ride = ride_estimator(v)
        self._ride_est = ride
        radius = v.rv("alternatives", "정류장_반경_m")
        mg = MixedGenerator(
            v.candidate_graph(first_visit), v.bus, v.sc, v.ex, radius_m=radius, near_m=radius,
            cuts=mix_rule(v.R, "혼합_끊는_지점_최대", MIX_CUTS_PER_ROUTE_PROPOSED), tlim=lim,
            excluded=v.rv("bus", "route_type_제외") or [], ride_min=ride,
            wayfinding=v.R["transfer"]["wayfinding_addition_min"]["value"] if first_visit else 0,
            walk_speed=self.speed, detour=self.detour, avoid_lines=avoid, skip_at=skip)
        return (ChainGenerator(mg, cuts=xfer_rule(v.R, "cuts"), mid=xfer_rule(v.R, "mid"), cap=xfer_rule(v.R, "max")),
                lim, which)

    def _chain_in_service(self, c, lo, hi):
        """후보의 버스 구간이 모두 [lo, hi] 무렵 운행해 보이나(_in_service 와 같은 추정 — 탐색 순서용 · 판정 아님)."""
        class _One:
            pass
        for rid, board in c.rides:
            one = _One()
            one.route_id, one.board = rid, board
            if not self._in_service(one, lo, hi):
                return False
        return True

    def _verify_chain(self, gen, cands, kind, stage, what, a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit,
                      case_id, left, seen):
        """환승 후보 한 묶음을 판정기로(_mixed_one 과 같은 순서 — 마지막 성립 출발 → 그 출발로 다시 판정 → 식 확인).
        성립한 후보 목록. 판정하지 않고 넘긴 것은 left 에 접어 적는다: `xfer_cap`(상한) · `xfer_skipped`(추정 기반 생략 —
        운행 시간 밖 · 소요 배수 밖). 둘 다 「불가 확정」이 아니다."""
        ratio = self.v.R["candidates"]["허용_소요_배수"]["value"]
        n_cap = max(0, gen.n_generated - len(cands))
        n_cut = getattr(gen, "n_pruned", 0)
        cands = [c for c in cands if c.key() not in seen]
        if not cands:
            return []
        win = [c for c in cands if self._chain_in_service(c, arrive_by - c.est_min - MIX_WINDOW_TOL_MIN, arrive_by)]
        n_win = len(cands) - len(win) if win else 0
        cands = win or cands
        floor = min(c.est_min for c in cands)
        kept, n_far, n_none = [], 0, 0
        for i, c in enumerate(cands):
            seen.add(c.key())
            if c.est_min > floor * ratio + MIX_EST_TOL_MIN:
                n_far += 1
                continue
            if not gen.materialize(c):
                n_none += 1
                continue
            self._xfer_verified = getattr(self, "_xfer_verified", 0) + 1          # 판정기에 넣은 수(GPT 94-2 #5)
            o, why = self._mixed_one(c, i, a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id,
                                     key=(kind, stage, i), n=XFER_N_BASE + XFER_N_STEP * stage + i, tag=what)
            if o is None:
                left.append(why)
            else:
                kept.append(o)

        def fold(k, code, reason):
            if k:
                left.append({"_o": {"_legs": []}, "label": f"그 밖 {what} 후보 {k}개", "code": code, "reason": reason})
        fold(n_none, "mix_no_subway", "환승 상한 안에서 지하철 쪽 구간열을 찾지 못했다")
        fold(n_win, "xfer_skipped", f"추정 기반 탐색 생략 — 승차 정류장 운행 시간(추정 ±{MIX_WINDOW_TOL_MIN}분) 밖이라 안쪽 후보를 먼저 "
                                    "판정했다(불가 확정 아님)")
        fold(n_far, "xfer_skipped", f"추정 기반 탐색 생략 — 추정 소요가 이 단계 가장 짧은 후보({floor:g}분)의 허용_소요_배수 {ratio:g} 를 "
                                    "넘어 판정하지 않았다(불가 확정 아님)")
        fold(n_cap, "xfer_cap", f"단계마다 판정에 넣는 상한 {gen.cap}개를 넘어 판정하지 않았다 — 추정 소요 순(불가 확정 아님)")
        # (GPT 94 #1) 생성 단계에서 추정으로 접은 것(같은 노선쌍의 다른 끊는 자리 · 다른 가운데 노선)도 「판정하지 않은 후보」다
        fold(n_cut, "xfer_skipped", "추정 기반 탐색 생략 — 같은 노선 짝의 다른 갈아타는 자리·다른 가운데 노선은 추정 소요가 짧은 것만 "
                                    "남겼다(불가 확정 아님)")
        return kept

    @staticmethod
    def _slot_ok(o, lim):
        """수단별 칸의 대표가 될 자격 — uses 표기 검사 통과 · 환승 상한 안(_by_mode 의 put() 과 같은 조건 · GPT 94-2 #1)."""
        return not O.uses_problems(o["uses"]) and o["_transfers"] <= lim

    def _stage_wanted(self, gen, cands, pool, what, left, arrive_by):
        """다음 단계 후보(cands)를 판정할까(94-2). 앞 단계까지 대표 자격이 있는 성립 후보(pool)가 없으면 판정한다. 있으면
        **대표 고르기와 같은 축(출발 시각)** 으로 본다(GPT 94-2 #3): 환승이 더 많은 후보는 앞 단계 후보 중 **가장 늦은 출발보다 늦게**
        떠날 수 있을 때만 대표 고르기(_by_mode_pick)에 영향을 준다(더 늦지 않으면 맨 위가 못 되고, 환승이 더 많아 양보로도 안 뽑힌다).
        → 추정으로 본 출발(도착 목표 − 가장 짧은 추정 소요 − 단계 버퍼)이 그 출발보다 늦을 때만 판정한다. 아니면 판정하지 않고 left 에
        `xfer_skipped` 로 접어 적는다(추정은 하한이 아니다 — 불가 확정 아님).
        ★1판은 「추정 소요 < 앞 대표의 예정 소요」였다 — 대표는 소요가 아니라 출발 시각으로 고르므로(여유·남는 분이 후보마다 다르다)
          추정이 정확해도 대표가 될 후보를 건너뛰었다. 양보 분을 여기서 더하지 않는다 — 더하면 「2회가 맨 위가 되고 그 5분 안의 1회가
          대표」인 경우의 1회 단계를 건너뛴다(GPT 94-2 #4). 버퍼는 기존 값(buffer.by_stage) — 새 숫자 없음."""
        if not pool:
            return True
        top = max(o["_start"] for o in pool)
        est = min(c.est_min for c in cands)
        buf = self.v.rv("buffer", "by_stage", self.stage)
        if arrive_by - est - buf > top:
            return True
        n = max(gen.n_generated, len(cands))
        left.append({"_o": {"_legs": []}, "label": f"그 밖 {what} 후보 {n}개", "code": "xfer_skipped",
                     "reason": f"추정 기반 탐색 생략 — 가장 짧은 추정 소요 {est:g}분으로는 앞 단계까지의 성립 후보보다 늦게 떠날 수 없어 "
                               "판정하지 않았다(불가 확정 아님)"})
        return False

    def _bus_transfer(self, a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id, wlim, base=()):
        """「버스만」 환승 후보 — 버스→버스(1회) · 버스→버스→버스(2회)를 **단계에서 멈추지 않고** 환승 상한까지(94-2). base = 직행 성립
        후보(대표 비교용 — 돌려주지는 않는다). 다음 단계는 앞 단계까지 대표 자격 후보가 없거나 추정으로 앞 단계 후보보다 늦게
        떠날 수 있을 때만 판정(_stage_wanted). (성립 후보(모든 단계), left, note): note = {"stages": [{transfers, meet_m, generated
        (만든 수), verified(판정기에 넣은 수), feasible(성립 수), judged(단계를 판정했나)}], "limit", "which", "blocked": 환승 상한
        때문에 만들지 않은 단계(환승 수) 또는 None}."""
        gen, lim, which = self._chain_gen(party, first_visit)
        note = {"stages": [], "limit": lim, "which": which, "blocked": None}
        left, seen, out = [], set(), []
        if gen is None:
            return [], left, note
        radii = [self.v.rv("alternatives", k) for k in XFER_MEET_KEYS]
        pool = [o for o in base if self._slot_ok(o, lim)]
        for n in (1, 2):
            if n > lim:
                note["blocked"] = n
                break
            what = "버스→버스" if n == 1 else "버스→버스→버스"
            for meet in radii:
                cands = gen.bus_chain(n, a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"], wlim, meet)
                if cands:
                    break
            n_gen = gen.n_generated if cands else 0
            judged = bool(cands) and self._stage_wanted(gen, cands, pool, what, left, arrive_by)
            self._xfer_verified = 0
            kept = self._verify_chain(gen, cands, "busx", n, what, a_place, b_place, arrive_dt, sdate, arrive_by, party,
                                      first_visit, f"{case_id}~x{n}", left, seen) if judged else []
            note["stages"].append({"transfers": n, "meet_m": meet, "generated": n_gen, "verified": self._xfer_verified,
                                   "feasible": len(kept), "judged": judged})
            out += kept
            pool += [o for o in kept if self._slot_ok(o, lim)]
        return out, left, note

    def _mixed2(self, a_place, b_place, sa, sb, arrive_dt, sdate, arrive_by, party, first_visit, case_id, wlim, left,
                base=()):
        """「지하철+버스」 2회 환승 후보 — 버스→지하철→버스(BSB) · 지하철→버스→지하철(SBS). base = 1회 혼합 성립 후보(대표 비교용).
        1회 혼합이 성립해도 부른다(94-2) — 판정은 1회 성립 후보가 없거나 추정으로 1회 후보보다 늦게 떠날 수 있을 때만(_stage_wanted).
        환승 상한이 2 미만이면 만들지 않는다((후보, True)). 두 모양을 번갈아 추정 소요 순으로 판정한다."""
        gen, lim, _which = self._chain_gen(party, first_visit)
        if gen is None or self.v.sc is None or lim < 2:
            return [], gen is not None and self.v.sc is not None and lim < 2
        ea, eb = self._phys_set(sa), self._phys_set(sb)
        bsb = gen.bus_subway_bus(a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"], wlim, excl_a=ea, excl_b=eb)
        n_gen, n_cut = gen.n_generated, gen.n_pruned
        sbs = []
        if sa and sb:
            sbs = gen.subway_bus_subway([(nm, ls, d) for nm, d, ls in sa], [(nm, ls, d) for nm, d, ls in sb],
                                        excl_a=ea, excl_b=eb)
            n_gen, n_cut = n_gen + gen.n_generated, n_cut + gen.n_pruned
        cands = interleave(bsb, sbs)[:gen.cap]
        gen.n_generated, gen.n_pruned = n_gen, n_cut
        pool = [o for o in base if self._slot_ok(o, lim)]
        if not cands or not self._stage_wanted(gen, cands, pool, "혼합 2회 환승", left, arrive_by):
            return [], False
        kept = self._verify_chain(gen, cands, "mix2", 2, "혼합 2회 환승", a_place, b_place, arrive_dt, sdate, arrive_by, party,
                                  first_visit, f"{case_id}~2", left, set())
        return kept, False

    # ── 수단별 대표 후보(93 · 봉투 by_mode) ──────────────────────────────────────
    def _yield_min(self):
        """환승이 더 적은 후보에게 대표를 내주는 폭(분) — 규칙 candidates.대표_환승_양보_분, 없으면 변경안 값. 0 이상 정수."""
        n = (self.v.R.get("candidates") or {}).get("대표_환승_양보_분")
        k = n["value"] if n and n.get("value") is not None else BY_MODE_YIELD_MIN_PROPOSED
        if isinstance(k, bool) or not isinstance(k, int) or k < 0:
            raise ValueError(f"candidates.대표_환승_양보_분 은 0 이상 정수 — 받은 값 {k!r}")
        return k

    @staticmethod
    def _by_mode_pick(cands, yield_min):
        """대표 하나 — 성립 확인된 출발(_start)이 가장 늦은 후보(가장 이른 도착·최단 소요가 아니다 · 동률은 환승 적은 · 소요 짧은 · 생성 순 = _rank). 그보다 환승이 적은
        후보 중 _start 가 yield_min 분 안으로 따라오는 것이 있으면 그중 환승이 가장 적은 것(동률은 _rank)으로 바꾼다."""
        top = max(cands, key=Planner._rank)
        fewer = [o for o in cands if o["_transfers"] < top["_transfers"] and top["_start"] - o["_start"] <= yield_min]
        if fewer:
            least = min(o["_transfers"] for o in fewer)
            top = max((o for o in fewer if o["_transfers"] == least), key=Planner._rank)
        return top

    def _by_mode_found(self, o, sdate, rf):
        """대표 후보 → 내보내는 칸. 시각은 벽시계 ISO(+09:00) · 경로는 우리 데이터의 노선·역·정류장 이름만(좌표·외부 응답 없음)."""
        legs = []
        for x in o["_legs"]:
            m = leg_mode(x)
            if m == "subway":
                legs.append({"mode": "subway", "line": line_name(x["line"]), "from": x["from"], "to": x["to"]})
            elif m == "bus":
                legs.append({"mode": "bus", "route": x["route"], "from": x["from"], "to": x["to"]})
        e = {"status": "found", "label": o["_route"], "legs": legs, "uses": list(o["uses"]),
             "depart_at": iso_of(sdate, o["_start"]), "arrive_at": iso_of(sdate, o["_start"] + o["eta_min"]),
             "eta_min": int(o["eta_min"]), "worst_min": int(o["eta_min"] + (o.get("_margin") or 0)),
             "transfers": int(o["_transfers"])}
        if o.get("_walk_m") is not None:
            e["walk_m"] = int(round(o["_walk_m"]))
        if o.get("_fare") is not None:
            e["fare_krw"] = int(o["_fare"])
        elif o["_legs"] and o.get("_lr") is not None:
            up = O.fare_upper_of(self.v, o["_legs"], o["_lr"])      # options[] 와 같은 규칙(#20) — 환승 할인 전 상한
            if up is not None:
                e["fare_krw"] = int(up)
                e["label"] += " · 요금은 환승 할인 전 상한"
        # 범위 안 = 이 출발이 앞 일정 시작보다 **뒤**(앞 10:00 시작 · 다음 11:00 시작 → 10:01 뒤에 떠나면 59분 안). 앞 일정 시작을
        #   모르면 판단하지 않는다(None). 범위 밖이어도 시각은 낸다 — 띄울지는 받는 쪽이 이 표시로 정한다.
        e["within_range"] = None if rf is None else bool(o["_start"] > rf)
        return e

    def _by_mode(self, opts, bus_opts, left, why, r, a_place, b_place, sa, sb, arrive_dt, sdate, arrive_by, party,
                 first_visit, case_id, wlim, range_from_dt, visited=()):
        """수단별 대표 후보 한 벌 {range_from, range_to, range_min, modes{subway, bus, subway_bus, taxi}} (93).
        후보는 leg() 가 이미 성립을 확인한 것(지하철만 = ② · 버스만 = ③ 한 노선 직행)과, 혼합은 「지하철만·버스만보다 낫고 1.5배 안」
        조건 **없이** 다시 만든 것(_mixed 에 비교 대상 없이 — 이 칸에서만 · options[] 의 조건은 그대로). 앞 일정 끝(not_before)은
        보지 않는다 — 범위는 앞 일정 **시작** ~ 다음 일정 시작이고, 범위 밖 후보도 시각을 낸다.
        ★depart_at 은 「성립을 확인한 출발」이다. 지하철만·버스만은 판정기 역산(마지막 성립 출발)이고, 혼합은 _mix_latest 의 빠른
          길이라 성립 구간이 끊겨 있으면 더 늦은 성립 출발을 놓칠 수 있다(더 이르게 내는 쪽 · 늦게 떠나게 하지는 않는다 · GPT 93 #4).
        ★각 칸의 「없음 이유」는 그 수단의 탐색 결과만으로 쓴다 — leg() 의 대표 이유(why)는 버스·혼합까지 묶은 문장이라 옮기지
          않는다(GPT 93 #2)."""
        lim, which = self.v._party_limit(party)
        ymin = self._yield_min()
        rf = None
        if range_from_dt is not None:
            rd, rm = service_day(range_from_dt.astimezone(KST))
            rf = rm + (rd - sdate).days * MIN_DAY
        modes = {}

        def put(key, cands, none_code, none_reason, limited=False):
            ok = [o for o in cands if not O.uses_problems(o["uses"])]
            over = [o for o in ok if o["_transfers"] > lim]          # 생성기가 상한을 지키므로 없어야 한다 — 있으면 싣지 않는다
            ok = [o for o in ok if o["_transfers"] <= lim]
            if ok:
                modes[key] = self._by_mode_found(self._by_mode_pick(ok, ymin), sdate, rf)
                if limited:
                    modes[key]["search_limited"] = True       # 판정하지 않은 후보가 남아 있다 — 제한된 탐색에서 고른 대표
            elif limited:
                # (GPT 94-2 #2) 대표 자격이 있는 후보가 없는데 판정하지 않은 후보가 남았다 — 「없다」가 아니라 「확인 못 함」이 먼저다
                dropped = ([f"환승 상한 {lim}회({which})를 넘는 성립 후보 {len(over)}개"] if over else []) + (
                    [f"uses 표기 검사 불통과 성립 후보 {len(cands) - len(over) - len(ok)}개"] if len(cands) > len(over) + len(ok) else [])
                base_r = none_reason if none_code == BY_MODE_UNCONFIRMED else (
                    "판정하지 않은 후보가 남아 있다 — 성립 후보가 없다고 확인한 것은 아니다")
                modes[key] = {"status": "none", "code": BY_MODE_UNCONFIRMED,
                              "reason": base_r + (f" · 대표에서 뺀 것: {' · '.join(dropped)}" if dropped else "")}
            elif over:
                modes[key] = {"status": "none", "code": "transfer_limit",
                              "reason": f"성립 후보가 환승 상한 {lim}회({which})를 넘는다"}
            elif cands:
                modes[key] = {"status": "none", "code": "uses_format", "reason": "uses 표기 검사 불통과 후보뿐이다"}
            else:
                modes[key] = {"status": "none", "code": none_code, "reason": none_reason}

        def first_left(pred, fallback):
            for e in left_all:
                if pred(e):
                    return e["code"], e["reason"]
            return fallback

        left_all = list(left)
        # ① 지하철만
        if "subway" not in self.modes:
            modes["subway"] = {"status": "none", "code": BY_MODE_NOT_REQUESTED, "reason": "고른 수단에 지하철이 없다"}
        else:
            rail = [o for o in opts if o["_key"][0] == "rail"]
            if not sa or not sb:
                gone = [(p, s_) for p, s_ in ((a_place, sa), (b_place, sb)) if not s_]
                blocked = [p["name"] for p, _s in gone if self.disruptions and self._any_station_near(p, wlim)]
                if blocked:
                    code, reason = STATION_BLOCKED_CODE, f"걸어갈 지하철역이 모두 사고로 막혔다({', '.join(blocked)})"
                else:
                    code, reason = "no_data", f"도보 상한 안에 지하철역이 없다({', '.join(p['name'] for p, _s in gone)})"
            elif not visited and why is not None:
                code, reason = why["code"], why["reason"]         # 두 장소의 가장 가까운 역이 같다 — 지하철 탐색만의 이유
            else:
                code, reason = first_left(lambda e: e["_o"].get("_key", ("",))[0] == "rail",
                                          ((r.out or {}).get("code") or "no_data",
                                           (r.out or {}).get("reason") or r.reason or "성립하는 지하철 후보가 없다")
                                          if r is not None else ("no_data", "성립하는 지하철 후보가 없다"))
                if visited:
                    reason = f"검토한 역 짝 {len(visited)}개({' · '.join(visited)})에서 성립하는 지하철 후보 없음 — {reason}"
            put("subway", rail, code, reason)
        # ② 버스만 — 한 노선 직행 + 버스→버스 + 버스→버스→버스를 한 목록으로(94-2 · 환승이 적다고 우선하지 않는다 · _bus_transfer)
        if "bus" not in self.modes:
            modes["bus"] = {"status": "none", "code": BY_MODE_NOT_REQUESTED, "reason": "고른 수단에 버스가 없다"}
        elif self.v.bus is None:
            modes["bus"] = {"status": "none", "code": "no_data", "reason": "버스 자료가 없다"}
        else:
            radius = self.v.rv("alternatives", "정류장_반경_m")
            tried = [e for e in left if _is_bus(e["_o"])]
            code = reason = None
            ok0 = [o for o in bus_opts if self._slot_ok(o, lim)]
            xo, xleft, note = self._bus_transfer(a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit,
                                                 case_id, wlim, base=ok0)
            bus_all = list(bus_opts) + xo
            n_skip = sum(1 for e in xleft if e["code"] in BY_MODE_LIMIT_CODES)
            limited = bool(n_skip)
            xtried = [e for e in xleft if e["code"] not in BY_MODE_LIMIT_CODES]
            # (GPT 94-2 #5) 만든 수와 실제로 판정기에 넣은 수를 따로 적는다 — 단계를 통째로 생략했으면 판정 0개
            made = [f"환승 {s_['transfers']}회 {s_['generated']}개 중 판정 {s_.get('verified', 0)}개(만남 반경 {s_['meet_m']:g} m)"
                    for s_ in note["stages"] if s_["generated"]]
            block = ("" if note["blocked"] is None else
                     f" · 환승 {note['blocked']}회 후보는 환승 상한 {note['limit']}회({note['which']})라 만들지 않았다")
            if not any(self._slot_ok(o, lim) for o in bus_all):
                if tried or xtried:
                    first = (tried or xtried)[0]
                    parts = ([f"한 노선 직행 {len(tried)}개"] if tried else []) + made
                    if n_skip:
                        code = BY_MODE_UNCONFIRMED
                        reason = (f"버스 후보({' · '.join(parts)}) 중 판정한 것은 이 도착 목표에 성립하지 않고, 판정하지 않은 "
                                  f"후보가 남아 있다 — 성립 후보가 없다고 확인한 것은 아니다 · {first['label']}: {first['reason']}{block}")
                    else:
                        code = first["code"]
                        reason = (f"버스 후보({' · '.join(parts)})가 있지만 이 도착 목표에 성립하는 것이 없다 — "
                                  f"{first['label']}: {first['reason']}{block}")
                elif n_skip:
                    code = BY_MODE_UNCONFIRMED
                    reason = f"판정하지 않은 버스 환승 후보가 남아 있다({' · '.join(made)}) — 성립 후보가 없다고 확인한 것은 아니다{block}"
                else:
                    meets = "→".join(f"{self.v.rv('alternatives', k):g}" for k in XFER_MEET_KEYS)
                    code = "no_service"
                    reason = (f"두 장소 근처 정류장(반경 {radius} m)을 버스로 잇는 경로가 없다 — 한 노선 직행 · 갈아타는 후보"
                              f"(환승 {min(2, note['limit']) if note['limit'] is not None else 0}회까지 · 만남 반경 {meets} m) 모두 없음{block}")
            put("bus", bus_all, code, reason, limited=limited)
        # ③ 지하철+버스 — 「낫고 1.5배 안」 조건 없이 대표 하나
        if not {"subway", "bus"} <= self.modes:
            modes["subway_bus"] = {"status": "none", "code": BY_MODE_NOT_REQUESTED, "reason": "고른 수단에 지하철·버스가 둘 다 있지 않다"}
        elif self.v.bus is None or self.v.sc is None:
            modes["subway_bus"] = {"status": "none", "code": "no_data", "reason": "혼합 후보를 만들 자료(버스·역 좌표)가 없다"}
        else:
            mleft = []
            mixed = self._mixed(a_place, b_place, sa, sb, arrive_dt, sdate, arrive_by, party, first_visit,
                                f"{case_id}~m", wlim, [], mleft)
            block = ""
            # 94-2 — 1회 혼합이 성립해도 2회(버스→지하철→버스 · 지하철→버스→지하철)를 같이 본다(한 목록에서 대표)
            ok1 = [o for o in mixed if self._slot_ok(o, lim)]
            m2, capped = self._mixed2(a_place, b_place, sa, sb, arrive_dt, sdate, arrive_by, party, first_visit,
                                      f"{case_id}~m", wlim, mleft, base=ok1)
            mixed = list(mixed) + m2
            if capped and not mixed:
                block = f" · 환승 2회 혼합은 환승 상한 {lim}회({which})라 만들지 않았다"
            left_all = mleft
            n_skip = sum(1 for e in mleft if e["code"] in BY_MODE_LIMIT_CODES)
            code, reason = first_left(lambda e: e["code"] not in BY_MODE_LIMIT_CODES,
                                      ("no_service", "버스와 지하철을 한 번 또는 두 번 갈아타 잇는 경로 후보가 없다(역 앞 정류장 기준)"))
            reason += block
            if n_skip and not mixed:
                # 판정한 것은 모두 불성립이지만 판정하지 않은 후보가 남았다 — 「없다」가 아니라 「확인 못 함」(GPT 93 #1)
                tried = "" if code == "no_service" else f" · 판정한 후보의 이유: {reason}"
                code = BY_MODE_UNCONFIRMED
                reason = (f"판정 상한({MIX_VERIFY_MAX}개)·추정 기반 생략으로 판정하지 않은 혼합 후보가 남아 있다 — "
                          f"성립 후보가 없다고 확인한 것은 아니다{tried}")
            put("subway_bus", mixed, code, reason, limited=bool(n_skip))
        # ④ 택시 — 자리와 이유만(서비스 경로에 택시 소요를 내는 도로 경로 계산이 연결돼 있지 않다)
        modes["taxi"] = {"status": "none", "code": "no_data",
                         "reason": "택시 소요 근거가 없다 — 도로 경로 계산이 서비스 경로에 연결돼 있지 않다"}
        out = {"range_from": None if range_from_dt is None else iso_of(sdate, rf), "range_to": iso_of(sdate, arrive_by),
               "range_min": None if rf is None else int(arrive_by - rf),
               "modes": {k: modes[k] for k in BY_MODE_KEYS}}
        return out

    @staticmethod
    def _rank(o):
        """계획 수단 순서 — **가장 늦게 떠나도 되는 후보**(동률은 환승 적은 · 소요 짧은 · 생성 순). 순위가 아니라 「일정대로
        움직이게」 하나를 고르는 규칙이다. 나머지는 options 에 순위 없이 남는다."""
        return (o["_start"], -o["_transfers"], -o["eta_min"], -o["_n"])

    @classmethod
    def _choose_planned(cls, opts, nb, recheck):
        """(계획 수단, nb 재판정으로 살아난 후보들) 또는 (None, []). 한 무리 안에서는 앞 판 그대로 — 자격(_start ≥ nb) 후보가 있으면
        그중 _rank 최대, 없으면 무리 전부를 nb 에서 재판정(recheck(o) → 갱신 후보 또는 None)해 살아난 것 중 최대.
        무리 순서: ① 지하철만·버스만·도보·자전거(앞 판 후보) ② 혼합(87) — ① 에서 하나라도 나오면 ② 는 안 본다(추가만).
        앞 일정 끝보다 이른 _start 후보도 **버리지 않고** 재판정까지 둔다(GPT 23 2차 #1 — 역산이 실제보다 이르게 나왔을 수 있다)."""
        for group in ([o for o in opts if not _is_mixed(o)], [o for o in opts if _is_mixed(o)]):
            if not group:
                continue
            eligible = [o for o in group if nb is None or o["_start"] >= nb]
            if eligible:
                return max(eligible, key=cls._rank), []
            revived = [g for g in (recheck(o) for o in group) if g is not None]
            if revived:
                return max(revived, key=cls._rank), revived
        return None, []

    def _fold_left(self, left):
        """뺀 후보 목록 정리 — 버스 직행은 도보 짧은 순으로 상한(버스_직행_최대)개만 한 줄씩 적고 나머지는 코드별 개수 한 줄로
        접는다(판정기 bus_rejected 와 같은 뜻 · 역 앞 노선이 많은 곳에서 목록이 덮이지 않게). 내부 참조(_o)는 뺀다."""
        bmax = self.v.R["candidates"]["버스_직행_최대"]["value"]
        buses = sorted((e for e in left if _is_bus(e["_o"])),
                       key=lambda e: (e["_o"]["_walk_m"] if e["_o"]["_walk_m"] is not None else float("inf"), e["_o"]["_n"]))
        drop = {id(e) for e in buses[bmax:]}
        out = [{k: v for k, v in e.items() if k != "_o"} for e in left if id(e) not in drop]
        if drop:
            codes = collections.Counter(e["code"] for e in buses[bmax:])
            out.append({"label": f"그 밖 버스 직행 {len(drop)}개", "code": "bus_more",
                        "reason": "도보 긴 쪽 — " + " · ".join(f"{c} {n}" for c, n in sorted(codes.items()))})
        return out

    def _recheck_at(self, o, start, arrive_by, party, first_visit, case_id):
        """후보 o 를 계획 출발 start(도착 목표 축 분)에서 판정기로 다시 본다. (갱신된 후보, None) 또는 (None, verdict).
        도보 직행은 판정기 밖이라 다시 안 본다(식으로 정해진다 — 더 늦게 떠나면 늦는다)."""
        ck = o.get("_check")
        if ck is None:
            return None, "infeasible"
        dep = start + ck["walk_place_in"] - ck["off"] + ck["walk_stop_in"]
        vc = self._vcq if ck.get("fast") else self._vc          # 87 — 혼합 후보는 lfd 를 끈 사본(결과 칸은 같다)
        r = vc({"id": f"{case_id}/at{start}", "date": ck["date"], "stage": self.stage,
                                "legs": ck["legs"], "depart_at": dep,
                                "arrive_by": ck["by_station"] - ck["walk_stop_out"],
                                "party": party, "first_visit": first_visit, "no_alternatives": True})
        out = r.out or {}
        if out.get("verdict") != "feasible":
            return None, ("unknown" if r.verdict == "unknown" else "infeasible")
        if out.get("eta_min") is None or (out.get("slack_min") or 0) < 0:
            return None, "unknown"
        eta = int(ck["walk_place_in"] + ck["walk_stop_in"] + out["eta_min"] + ck["walk_stop_out"] + ck["walk_place_out"])
        margin, slack = out.get("margin_min") or 0, out.get("slack_min") or 0
        if start + eta + margin + slack != arrive_by:
            return None, "unknown"
        sd = date.fromisoformat(ck["date"])
        n = dict(o, eta_min=eta, _start=start, _margin=margin, _slack=slack, _lr=r.legs, _day_type=r.day_type,
                 _severe=O.severe_hits(r.warnings), _fare=O.fare_of(self.v, ck["legs"], r.legs),
                 _covered=O.congestion_checked(self.v, ck["legs"], r.legs, sd, r.day_type))
        return n, None

    def _pair_settles(self, got, nb, arrive_by, party, first_visit, case_id):
        """역 짝 탐색을 여기서 멈춰도 되나 — 이 짝의 후보 중 **실을 수 있는 것**(uses 표기 검사 통과 · GPT 85 #2)이
        앞 일정 끝(nb) 뒤에 떠나도 되거나(_start ≥ nb), nb 에서 다시 판정해 살아나면(_recheck_at · 뒤 선택 로직과 같은
        기준) 멈춘다. 그래서 첫 짝으로 답이 나오던 구간은 앞 판과 같은 짝·같은 선택이다.
        ★ 멈춘 뒤 고르는 계획 수단은 **본 짝들 중** 가장 늦게 떠나도 되는 후보다 — 모든 역 짝의 최적은 아니다(성능 정책)."""
        ok = [o for o in got if not O.uses_problems(o["uses"])]
        if not ok:
            return False
        if nb is None or any(o["_start"] >= nb for o in ok):
            return True
        return any(self._recheck_at(o, nb, arrive_by, party, first_visit, case_id)[0] is not None for o in ok)

    def _any_station_near(self, place, limit_m):
        sc = self.v.sc
        return bool(sc is not None and sc.stations_near(place["lat"], place["lon"], limit_m))

    def _transit_pair(self, oa, ob, pi, arrive_dt, sdate, arrive_by, party, first_visit, case_id, left):
        """역 짝 하나(oa → ob)의 대중교통 후보. (성립 후보들, 판정기 답, 고른 수단 안 후보 수).
        pi = 짝 순번 — 0 은 앞 판과 같은 번호(_n)·case id. _n 은 계획 수단 동률 깨기(순서)에만 쓰고, 후보 식별(재판정
        되돌림)은 _key = ("rail", pi, 판정기 후보 번호) 로 한다(GPT 85 #4 — 번호 영역 겹침에 기대지 않는다)."""
        opts = []
        tag = case_id if pi == 0 else f"{case_id}~{pi}"
        nbase = PAIR_N_STEP * pi
        wa, wb = self._walk(oa[1]), self._walk(ob[1])
        st_date, by_station = service_day(arrive_dt - timedelta(minutes=wb))
        off = (st_date - sdate).days * MIN_DAY            # 역 운행일 축 → 도착 목표 축 (0 또는 −1440)
        probe = {"id": tag, "date": st_date.isoformat(), "stage": self.stage,
                 "depart_at": max(SERVICE_DAY_START_MIN, by_station - 180), "arrive_by": by_station,
                 "multi": _multi(oa, ob), "party": party, "first_visit": first_visit}
        r = self._vc(probe)
        n_mode = 0
        for c in r.candidates or []:
            if any(leg_mode(x) == "bike" for x in c["legs"]):
                continue          # 판정기 multi 의 자전거 후보는 역 기준 — 장소 기준 ④ 로 따로 본다(58)
            if self.modes is not None and any(leg_mode(x) not in self.modes for x in c["legs"]):
                continue
            n_mode += 1
            if any(leg_mode(x) == "bus" for x in c["legs"]):
                continue          # 버스 직행은 ③ 에서 **장소 기준**으로 다시 찾는다(역 경유 이중 도보를 없앤다 · 23 결정 4)
            lfd = (c.get("out") or {}).get("last_feasible_depart_min")
            sub_by = by_station - c["walk_out_min"]
            # ☆`[2026-09-29 문제목록 #29]` 여기서부터 버리는 후보는 이유를 봉투 left_out 에 남긴다 — 앞 판은 조용히 continue 했다
            ref = {"_legs": c["legs"], "_walk_m": None, "_n": nbase + c["n"], "_key": ("rail", pi, c["n"]),
                   "_route": label_of(c["legs"])}
            if lfd is None or sub_by < SERVICE_DAY_START_MIN:
                left.append({"_o": ref, "label": ref["_route"], "code": "no_last_departure",
                             "reason": ("마지막 성립 출발을 역산하지 못했다 — " + (c.get("reason") or "")) if lfd is None
                             else "역 도착 목표가 04:00 전(운행일 경계)이라 보지 않는다"})
                continue
            sub = {"id": f"{tag}/{c['n']}", "date": st_date.isoformat(), "stage": self.stage,
                   "legs": c["legs"], "depart_at": lfd + c["walk_in_min"], "arrive_by": sub_by,
                   "party": party, "first_visit": first_visit, "no_alternatives": True}
            r2 = self._vc(sub)
            o2 = r2.out or {}
            if o2.get("verdict") != "feasible" or o2.get("eta_min") is None or (o2.get("slack_min") or 0) < 0:
                left.append({"_o": ref, "label": ref["_route"], "code": "not_confirmed",
                             "reason": "역산 출발로 다시 판정하니 성립이 아니다 — " + (o2.get("reason") or r2.reason or "")})
                continue          # 역산 시각으로 다시 봐도 성립이 아니면 싣지 않는다(모르면 뺀다)
            eta = int(wa + c["walk_in_min"] + o2["eta_min"] + c["walk_out_min"] + wb)
            start = lfd - wa + off
            margin, slack = o2.get("margin_min") or 0, o2.get("slack_min") or 0
            if start + eta + margin + slack != arrive_by:
                left.append({"_o": ref, "label": ref["_route"], "code": "formula_mismatch",
                             "reason": f"출발 {start} + 소요 {eta} + 여유 {margin} + 남는 {slack} ≠ 도착 목표 {arrive_by} — 축이 어긋나 싣지 않는다"})
                continue          # 식이 안 맞으면 어딘가 축이 어긋난 것 — 내지 않는다(늦은 출발을 조용히 내지 않게)
            # 도보 m — 장소↔역(직선×우회) + 환승 거리표 m. 모르는 조각(거리표 밖 환승 · 자전거 대여소 도보)이 있으면 None
            inner = O.transfer_walk_m(self.v, c["legs"])
            walk_m = None if inner is None else (oa[1] + ob[1]) * self.detour + inner
            opts.append({"eta_min": eta, "uses": uses_of(c["legs"]), "_legs": c["legs"],
                         "_route": label_of(c["legs"]), "_start": start,
                         "_transfers": c.get("transfers") or 0, "_n": nbase + c["n"], "_key": ("rail", pi, c["n"]),
                         "_margin": margin, "_slack": slack,
                         "_walk_min": wa + wb + (c.get("walk_min") or 0), "_walk_m": walk_m,
                         "_fare": O.fare_of(self.v, c["legs"], r2.legs),
                         "_severe": O.severe_hits(r2.warnings),
                         "_covered": O.congestion_checked(self.v, c["legs"], r2.legs, st_date, r2.day_type),
                         "_lr": r2.legs, "_day_type": r2.day_type,
                         "_check": {"date": st_date.isoformat(), "legs": c["legs"], "off": off,
                                    "walk_place_in": wa, "walk_place_out": wb, "walk_stop_in": c["walk_in_min"],
                                    "walk_stop_out": c["walk_out_min"], "by_station": by_station}})
        return opts, r, n_mode

    @staticmethod
    def _floor_min(not_before_dt):
        """앞 일정 끝 → (운행일, 운행일 분) · 분 **올림**(09:47:30 에 끝나면 09:48 부터 · leg() 와 같은 규칙)."""
        up = not_before_dt.astimezone(KST)
        if up.second or up.microsecond:
            up = up.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return service_day(up)

    def _station_pairs(self, a_place, b_place, wlim):
        """(GPT 86 Q3) 역 목록·역 짝 — leg() 와 earliest 가 같이 쓴다. (sa, sb, pairs).
        pairs = [(ia, ib, 역a, 역b)] 가까운 짝부터((0,0) 이 늘 먼저) · 역이 없거나 양쪽 첫 역이 같으면 [](이유는 leg() 가 만든다).
        ★ 탐색 중단 정책은 쓰는 쪽이 정한다 — leg() 는 실을 수 있는 짝에서 멈추고(STATION_PAIR_MODE), 가장 이른 도착
          목표 생성(_earliest_targets)은 짝 전부를 본다(목표 후보를 넓게 · 확인은 leg() 가 한다)."""
        sa, sb = self._near_stations(a_place, wlim), self._near_stations(b_place, wlim)
        if not sa or not sb or same_station(sa[0][0], sa[0][2], sb[0][0], sb[0][2]):
            return sa, sb, []
        pairs = [(ia, ib, x, y) for ia, x in enumerate(sa) for ib, y in enumerate(sb)
                 if not same_station(x[0], x[2], y[0], y[2])]
        pairs.sort(key=lambda t: (t[0] + t[1], t[2][1] + t[3][1], t[0]))   # (0,0) 이 늘 먼저 — 앞 판과 같은 첫 짝
        return sa, sb, pairs

    def _earliest_targets(self, a_place, b_place, not_before_dt, party, first_visit, case_id):
        """앞 일정 끝(not_before_dt)에 **바로 떠났을 때** 수단 후보마다 성립하는 가장 이른 도착 목표(분 · 앞 일정 끝의
        운행일 축). 도착 목표 = 예정 도착 + @(margin — 버퍼 + 최악 폭) — leg() 의 식에서 slack 0 인 자리다.
        후보 공간은 leg() 와 같다(도보 직행 · 역 짝마다 다목적 후보(버스 섞인 것·자전거 뺌) · 장소 기준 버스 직행 ·
        modes 에 bike 가 있으면 자전거). 판정은 판정기가 한다(depart_at 만 주고 arrive_by 없이). 이른 순 정렬 · 중복 뺌.
        ☆(GPT 86 #1) 앞 일정 끝이 **첫차 전**이면 판정기는 후보를 `before_first`(불가)로 낸다 — 성립만 받으면 「첫차까지
        기다렸다 떠나는」 경로를 놓친다. 그 소스는 출발을 뒤로 옮겨(_after_first) 첫 성립 출발을 찾아 그 도착을 쓴다
        (첫차 전에 떠나도 첫차를 타므로 도착이 같다)."""
        from .geo import meters
        v = self.v
        sdate, nb = self._floor_min(not_before_dt)
        wlim = self._walk_limit(party)
        buf = v.rv("buffer", "by_stage", self.stage)
        base = datetime.combine(sdate, time(0, 0), tzinfo=KST)

        def at(m, walk):
            """앞 일정 끝 축의 출발 분 m(+접근 도보) → (판정 날짜, 그 운행일 분, 축 차이). 04:00 을 넘으면 다음 운행일."""
            d2, m2 = service_day(base + timedelta(minutes=int(m + walk)))
            return d2.isoformat(), m2, (d2 - sdate).days * MIN_DAY

        rec = {"on": False, "fails": []}       # (R3) 불가 이유는 소스마다 앞 일정 끝 출발(f(nb)) 한 번만 모은다

        def note(o):
            if rec["on"] and o.get("code"):
                rec["fails"].append((o["code"], o.get("reason") or ""))

        def got(o, add):
            """판정기 밖 판 → (도착 목표 분 또는 None, 기다리면 될 수 있는 불가인가)."""
            if o.get("verdict") == "feasible" and o.get("arrive_min") is not None and o.get("margin_min") is not None:
                return int(o["arrive_min"] + add + o["margin_min"]), False
            note(o)
            return None, o.get("code") in EARLIEST_WAIT_CODES

        sources = []
        direct = meters(a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"])
        if direct <= wlim and "walk" in self.modes:
            wm, no_path = self._walk_net(a_place, b_place, direct)
            if not no_path:
                walk_t = max(1, math.ceil(wm / self.speed / 60)) + buf
                sources.append(lambda m: ([m + walk_t], False))
        sa, sb, pairs = self._station_pairs(a_place, b_place, wlim)
        for pi, (_ia, _ib, xa, xb) in enumerate(pairs):
            def rail(m, pi=pi, xa=xa, xb=xb):
                wa, wb = self._walk(xa[1]), self._walk(xb[1])
                day, dm, off = at(m, wa)
                r = self._vc({"id": f"{case_id}~e{pi}@{m}", "date": day, "stage": self.stage, "depart_at": dm,
                              "multi": _multi(xa, xb), "party": party, "first_visit": first_visit})
                ts, wait = [], False
                for c in r.candidates or []:
                    if any(leg_mode(x) in ("bike", "bus") for x in c["legs"]):
                        continue          # leg() ② 와 같다 — 버스는 장소 기준(아래) · 자전거는 장소 기준(아래)
                    if any(leg_mode(x) not in self.modes for x in c["legs"]):
                        continue
                    t, w = got(c.get("out") or {}, wb)
                    if t is not None:
                        ts.append(t + off)
                    wait = wait or w
                return ts, wait
            sources.append(rail)
        if v.bus is not None and "bus" in self.modes:
            radius = v.rv("alternatives", "정류장_반경_m")
            excluded = v.rv("bus", "route_type_제외") or []
            for i, (r_, x, y, _span, da, db) in enumerate(v.bus.routes_between(
                    a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"], radius)):
                if r_.route_type_nm in excluded or max(da, db) > wlim:
                    continue
                def bus(m, i=i, legs=[{"mode": "bus", "route": r_.route_nm, "from": x["station_nm"], "to": y["station_nm"]}],
                        wi=self._walk(da), wo=self._walk(db)):
                    day, dm, off = at(m, wi)
                    rr = self._vc({"id": f"{case_id}~ebus{i}@{m}", "date": day, "stage": self.stage, "legs": legs,
                                   "depart_at": dm, "party": party, "first_visit": first_visit, "no_alternatives": True})
                    t, w = got(rr.out or {}, wo)
                    return ([t + off] if t is not None else []), w
                sources.append(bus)
        # ☆`[87]` 혼합(⑤) — leg() 와 같은 생성기 · 같은 거르기(운행 시간 · 구간열) · 추정 소요 순 MIX_VERIFY_MAX 개만 소스로
        #   (앞설 축 거르기는 하지 않는다 — 목표 후보를 넓게, 확인은 leg() 가 한다 · 86 의 짝 전부 보기와 같은 뜻)
        for i, mc in enumerate(self._mixed_sources(a_place, b_place, sa, sb, nb, party, first_visit, wlim)):
            def mix(m, i=i, mc=mc, wi=self._walk(mc.walk_in_m), wo=self._walk(mc.walk_out_m)):
                day, dm, off = at(m, wi)
                rr = self._vcq({"id": f"{case_id}~emix{i}@{m}", "date": day, "stage": self.stage, "legs": mc.legs,
                                "depart_at": dm, "party": party, "first_visit": first_visit, "no_alternatives": True})
                t, w = got(rr.out or {}, wo)
                return ([t + off] if t is not None else []), w
            sources.append(mix)
        if "bike" in self.modes:
            legs = [{"mode": "bike",
                     "from": {"lat": a_place["lat"], "lng": a_place["lon"], "name": a_place["name"]},
                     "to": {"lat": b_place["lat"], "lng": b_place["lon"], "name": b_place["name"]}}]

            def bike(m):
                day, dm, off = at(m, 0)
                rb = self._vc({"id": f"{case_id}~ebike@{m}", "date": day, "stage": self.stage, "legs": legs,
                               "depart_at": dm, "party": party, "first_visit": first_visit, "no_alternatives": True})
                o = rb.out or {}
                if o.get("verdict") == "feasible" and o.get("eta_min") is not None and o.get("margin_min") is not None:
                    return [int(m + o["eta_min"] + o["margin_min"])], False
                note(o)
                return [], o.get("code") in EARLIEST_WAIT_CODES
            sources.append(bike)
        ts, info = self._run_sources(sources, nb, rec)
        return sdate, ts, info

    @classmethod
    def _run_sources(cls, sources, nb, rec):
        """소스마다 앞 일정 끝(nb) 출발로 목표를 내고, 기다리면 될 수 있는 소스는 대기 탐색(_after_wait).
        (정렬된 목표, {causes, stops}). rec = {"on", "fails"} — 소스 f 가 불가 이유를 rec["on"] 동안 rec["fails"] 에 적는다."""
        ts, causes, stops, ranges = [], [], collections.Counter(), []
        for f in sources:
            rec["on"], rec["fails"] = True, []
            got_ts, wait = f(nb)
            rec["on"] = False
            if not got_ts and rec["fails"]:
                # (R3 · 3차 #4) 소스 하나 = 원인 한 줄 — 코드마다 그 코드의 첫 이유를 짝으로(대표 코드와 이유가 어긋나지 않게)
                per = {}
                for c, r in rec["fails"]:
                    per.setdefault(c, r)
                causes.append(per)
            if wait:
                # (R5② · 3차 S1·S3) 지금 성립하는 후보가 있어도 대기 탐색을 한다. 지금 가장 이른 목표보다 늦게 떠나서는 더
                #   일찍 못 닿으므로 상한을 (그 목표 − nb) 로 줄이되 **짧아도 건너뛰지 않는다**(_after_wait 가 상한 칸까지 본다).
                had = bool(got_ts)
                horizon = EARLIEST_WAIT_MAX_MIN if not had else min(EARLIEST_WAIT_MAX_MIN, min(got_ts) - nb)
                if horizon >= 1:
                    more, stop = cls._after_wait(f, nb, horizon, until_clear=had, known=min(got_ts) if had else None)
                    got_ts = got_ts + more
                    if stop != "done":
                        stops[stop] += 1      # 섞인 소스도 지운다고 남긴다(3차 S3 — 상한 앞을 다 봤다는 뜻이 아니다)
                        ranges.append(int(horizon))   # (4차 #3) 그 소스가 실제로 본 대기 범위(분)
            ts += got_ts
        return sorted(set(ts)), {"causes": causes, "stops": stops, "wait_ranges": ranges}

    @staticmethod
    def _after_wait(f, nb, horizon=None, until_clear=False, known=None):
        """(GPT 86 Q1) 기다리면 될 수 있는 이유(EARLIEST_WAIT_CODES)로 불가인 소스 f — 출발을 nb 뒤로 EARLIEST_WAIT_STEP 분
        부터 두 배씩(상한 horizon · 기본 EARLIEST_WAIT_MAX_MIN · **마지막 칸은 상한 그 자리** · 상한이 첫 칸보다 짧으면 상한
        한 칸) 옮겨 「멈춤 조건」을 만족하는 첫 칸을 찾고, 그 앞 칸과 사이를 이분 탐색한다.
        멈춤 조건: 목표가 나왔다(until_clear=False) / 목표가 있고 기다리는 후보가 없다(until_clear=True · 섞인 소스 · R5②).
        ★ (3차 S1) **관측한 성립 목표는 전부 모아 낸다** — 멈춤 조건은 탐색을 계속할지만 정한다(목표를 버리는 조건이 아니다).
          그리고 관측한 가장 이른 목표보다 늦게 떠날 칸은 보지 않는다(상한을 그 자리로 줄인다). 아직 기다리는 후보가
          남은 칸에서 더 이른 목표가 새로 나오면 그 칸 사이를 이분 탐색해 새 후보의 첫 성립 출발 목표까지 본다(4차 T1).
        돌려주는 것: (관측한 목표들, 멈춘 까닭)
          "done"        멈춤 조건 칸을 찾고 이분 탐색을 끝까지 했다
          "incomplete"  기다릴 이유가 아닌 불가를 만나 멈췄다(R1 — 관측한 목표는 유효하지만 탐색을 끝내지 못했다)
          "wait_limit"  상한까지 멈춤 조건 칸을 못 찾았다
        ★ 사각지대(R5①): 두 배 간격이라 칸 사이의 **짧은 성립 구간**은 건너뛸 수 있다 — 관측한 목표가 없으면 "wait_limit"
          (미확인)으로 끝난다. 이분 탐색은 칸 사이에서 조건이 한 번만 바뀐다고 가정한다. 근본 개선은 시간표 출발·첫차 시각을
          직접 쓰는 것(판정기 밖 판에 첫 성립 시각 칸이 없어 이번엔 안 함)."""
        horizon = EARLIEST_WAIT_MAX_MIN if horizon is None else horizon
        seen = []

        def probe(m):
            ts, wait = f(m)
            seen.extend(ts)
            return (bool(ts) and not (until_clear and wait)), bool(ts), wait

        def cap():
            return min(horizon, min(seen) - nb) if seen else horizon
        lo, step = nb, EARLIEST_WAIT_STEP
        before = known if known is not None else float("inf")   # 직전 칸까지 관측한 가장 이른 목표(섞인 소스는 nb 의 목표)
        broke = False
        while lo - nb < cap():
            hi = min(nb + step, nb + cap())
            ok, any_ts, wait = probe(hi)
            if ok:
                stop = "done"
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    ok2, any2, w2 = probe(mid)
                    if ok2:
                        hi = mid
                    elif w2 or any2:
                        lo = mid
                    else:
                        stop = "incomplete"
                        break
                return sorted(set(seen)), stop
            if not wait and not any_ts:
                return sorted(set(seen)), "incomplete"
            if until_clear and seen and min(seen) < before and lo < hi - 1:
                # (4차 T1) 아직 기다리는 후보가 남았지만 이 칸에서 더 이른 목표가 새로 나왔다 — 그 후보가 처음 성립하는 칸을
                #   이분 탐색해 그 첫 성립 출발의 목표까지 본다(앞 판은 이 칸의 늦은 목표를 그대로 썼다)
                a, z = lo, hi
                while z - a > 1:
                    mid = (a + z) // 2
                    t2, w2 = f(mid)
                    seen.extend(t2)
                    if not t2 and not w2:
                        broke = True          # (4차 #2) 비대기 불가 — 탐색은 잇되 「끝내지 못함」으로 남긴다
                    if t2 and min(t2) < before:
                        z = mid
                    else:
                        a = mid
            before = min(seen) if seen else before
            lo, step = hi, step * 2
        return sorted(set(seen)), ("incomplete" if broke else "wait_limit")

    def earliest(self, a_place, b_place, not_before_dt, party, first_visit, case_id, tries=None):
        """☆`[2026-10-01 83 E2 · #42 보조 · 86]` **가장 이른 도착 목표** — 앞 일정이 not_before_dt 에 끝날 때 그 뒤 떠나서
        성립하는 가장 이른 도착 목표(= 다음 일정이 시작할 수 있는 가장 이른 시각)와 그 목표의 이동 값.
        기존 leg()(도착 목표 역산 · 마지막 성립 출발)와 둘 다 쓴다.

        ① 앞 일정 끝에 바로 떠났을 때 수단 후보마다 「예정 도착 + @」를 판정기로 낸다(_earliest_targets). 기다리면 되는
           불가(첫차 전 · 공백 · 막차 뒤 — EARLIEST_WAIT_CODES)는 출발을 뒤로 옮겨 첫 성립 출발을 찾는다(GPT 86 Q1).
           시간표가 띄엄띄엄이라 역산 출발과 앞 일정 끝의 차이만큼 밀어서는 다음 편을 타게 되어 또 늦는다
           (83: N서울타워→남대문 13분 밀고도 실패) — 그래서 「몇 분 밀까」가 아니라 **닿는 시각**을 낸다.
        ② 목표마다 t, t+1, …, t+EARLIEST_FILL_MIN 을 이른 순으로 leg(목표, not_before_dt) 로 확인한다(호출 tries 번까지 ·
           GPT 86 Q2). 값·식·후보 규칙이 역산 모드와 같아진다. 성립한 첫 목표가 답이다.
        ★ 보장 범위(GPT 86 Q1·Q2): 「위 탐색 범위에서 leg() 재판정으로 성립을 확인한 가장 이른 목표」다 — 생성하지 않은
          시각까지 전부 검증한 전역 최소는 아니다(@ 가 출발 시각에 따라 바뀌는 버스 등). 출발(starts_at)은 그 목표에 맞는
          **마지막 성립 출발**(leg() 규칙)이지 가장 이른 출발이 아니다 — 반환한 출발 시각에서 성립을 확인했을 뿐이다.
        돌려주는 것: ((route, 시작 분, 끝 분, 운행일, 뺀 후보, 도착 목표 분), None) — 분은 그 운행일 축(leg() 와 같음) —
          또는 (None, 이유 dict + searched). 실패 code 의 뜻(GPT 86 R2):
            · 판정기 이유(no_service · no_data 등) — **생성·검사한 소스에서 목표를 얻지 못했다**(status not_found ·
              3차 S2). 불가능의 증명이 아니다(no_data 는 자료가 없다는 뜻) — 원인은 searched.causes(소스마다 코드→이유).
            · EARLIEST_UNCONFIRMED — 그 밖 전부: 목표를 만들었지만 확인한 시각(목표 + 0~2분)이 모두 불성립 ·
              확인 호출 한도 · 대기 탐색 상한 · 대기 탐색 중단. 「그 뒤에도 경로가 없다」는 뜻이 아니다.
          searched = {checked(확인한 도착 목표 HH:MM), calls, stop(멈춘 까닭: confirm_limit · targets_checked ·
          wait_limit · wait_incomplete · no_targets), causes}.
        ★ 다음 일정을 미는 것은 코어 몫이다(83 C3 · 66건 문서) — 여기서는 값만 낸다."""
        tries = EARLIEST_TRIES if tries is None else tries
        if isinstance(tries, bool) or not isinstance(tries, int) or tries < 1:
            raise ValueError(f"tries 는 1 이상 정수 — 받은 값 {tries!r}")      # GPT 86 Q7(0 · 음수 슬라이스)
        if not_before_dt is None:
            raise ValueError("earliest() 는 not_before_dt(앞 일정 끝)가 필요하다")
        sdate, ts, info = self._earliest_targets(a_place, b_place, not_before_dt, party, first_visit, case_id)
        end_hm = f"{not_before_dt.astimezone(KST):%H:%M}"
        causes = info["causes"]
        searched = {"checked": [], "calls": 0, "causes": [{"codes": sorted(per), "reasons": dict(per)} for per in causes]}

        def wait_note():
            parts = []
            if info["stops"].get("wait_limit"):
                # (4차 #3) 실제로 본 범위로 적는다 — 섞인 소스는 지금 목표까지로 줄여 본다(최대 EARLIEST_WAIT_MAX_MIN)
                rg = info.get("wait_ranges") or [EARLIEST_WAIT_MAX_MIN]
                span = f"{min(rg)}분" if min(rg) == max(rg) else f"{min(rg)}~{max(rg)}분"
                parts.append(f"대기 탐색 범위(앞 일정 끝 뒤 {span} · 두 배 간격 · 최대 {EARLIEST_WAIT_MAX_MIN}분) 안에서 "
                             f"기다리던 후보의 성립 출발을 다 찾지 못한 후보 {info['stops']['wait_limit']}곳")
            if info["stops"].get("incomplete"):
                parts.append(f"대기 탐색을 끝내지 못한 후보 {info['stops']['incomplete']}곳(중간에 다른 불가)")
            return " · ".join(parts)
        if not ts:
            stop = ("wait_limit" if info["stops"].get("wait_limit") else
                    "wait_incomplete" if info["stops"].get("incomplete") else "no_targets")
            searched["stop"] = stop
            if stop != "no_targets":
                return None, {"code": EARLIEST_UNCONFIRMED, "searched": searched,
                              "reason": f"앞 항목이 끝난 뒤({end_hm}) 떠나서 성립하는 후보를 찾지 못했다 — {wait_note()} "
                                        f"(탐색 한도 · 불성립 확인 아님)"}
            if causes:
                per = collections.Counter(c for src in causes for c in src)
                code = per.most_common(1)[0][0]
                lines = " · ".join(f"{c} {n}곳" for c, n in per.most_common())
                return None, {"code": code, "searched": searched,
                              "reason": f"앞 항목이 끝난 뒤({end_hm}) 검사한 후보에서 다음 사유로 찾지 못했다 — {lines} "
                                        f"(첫 사유: {next(src[code] for src in causes if code in src)})"}
            return None, {"code": "no_data", "searched": searched,
                          "reason": f"앞 항목이 끝난 뒤({end_hm}) 떠날 후보(도보·역·버스)가 없다"}
        order = sorted({t + k for t in ts for k in range(EARLIEST_FILL_MIN + 1)})
        last = None
        for t in order[:tries]:
            target = datetime.fromisoformat(iso_of(sdate, t))
            searched["calls"] += 1
            got, why = self.leg(a_place, b_place, target, party, first_visit, f"{case_id}~at{t}", not_before_dt=not_before_dt)
            if got is not None:
                route, start, end, sd, left = got
                return (route, start, end, sd, left, t - (sd - sdate).days * MIN_DAY), None
            searched["checked"].append(iso_of(sdate, t)[11:16])
            last = why or {}
        searched["stop"] = "confirm_limit" if len(order) > tries else "targets_checked"
        why_more = (f"확인 호출 한도 {tries}번이라 더 늦은 목표는 보지 않았다" if len(order) > tries
                    else f"생성한 목표와 보충 {EARLIEST_FILL_MIN}분까지만 봤다")
        extra = wait_note()
        return None, {"code": EARLIEST_UNCONFIRMED, "searched": searched,
                      "reason": f"도착 목표 {', '.join(searched['checked'])} 를 다시 판정해도 성립하지 않는다 — {why_more}"
                                + (f" · {extra}" if extra else "") + " (불성립 확인 아님)"
                                + f" · 마지막 {searched['checked'][-1]}: {last.get('code')} {last.get('reason')}"}

    @staticmethod
    def earliest_summary(got, arrive_dt=None):
        """earliest() 결과 → 봉투용 dict(GPT 86 Q5 — 상태값으로 성공·실패를 가른다).
          status "found" · starts_at(그 목표에 맞는 마지막 성립 출발) · ends_at(예정 도착) · **arrive_by(다음 일정이 시작할
          수 있는 가장 이른 시각 — 여유 @ 포함 · 코어는 이 값으로 민다)** · eta_min · planned · label · uses · route(그대로
          채택할 수 있는 경로 정의) · arrive_dt 를 주면 requested_arrive_by(원래 도착 목표 — **분 내림**, 입력 원문 시각이
          아니다 · leg() 가 목표를 분 내림으로 판정하는 것과 같은 기준)와 slack_min = requested_arrive_by − arrive_by
          (**이 제안의 여유가 아니라 원래 목표 대비** · 음수 = 그만큼 늦다).
          ★ 코어는 원래 시작에 −slack_min 을 더하지 말고 **arrive_by 를 다음 일정의 새 시작 시각으로** 쓴다(R4 — 초가 있는
          일정에서는 두 방식이 갈린다)."""
        route, start, end, sd, _left, t = got
        planned = next(o for o in route["options"] if o["id"] == route["planned"])
        out = {"status": "found", "starts_at": iso_of(sd, start), "ends_at": iso_of(sd, end), "arrive_by": iso_of(sd, t),
               "eta_min": int(planned["eta_min"]), "planned": route["planned"], "label": planned.get("label"),
               "uses": list(planned.get("uses") or []), "route": route}
        if arrive_dt is not None:
            req = arrive_dt.astimezone(KST)
            out["requested_arrive_by"] = req.strftime("%Y-%m-%dT%H:%M:00+09:00")
            out["slack_min"] = int((req.replace(second=0, microsecond=0)
                                    - datetime.fromisoformat(out["arrive_by"])).total_seconds() // 60)
        return out

    def _earliest_or_reason(self, a_place, b_place, not_before_dt, party, first_visit, case_id, arrive_dt):
        e, e_why = self.earliest(a_place, b_place, not_before_dt, party, first_visit, case_id)
        if e is not None:
            return self.earliest_summary(e, arrive_dt)
        return {"status": "unconfirmed" if e_why["code"] == EARLIEST_UNCONFIRMED else "not_found",
                "code": e_why["code"], "reason": e_why["reason"], "searched": e_why.get("searched")}

    def leg(self, a_place, b_place, arrive_dt, party, first_visit, case_id, not_before_dt=None, earliest_on_late=False,
            by_mode=False, range_from_dt=None):
        """장소 a → 장소 b, 도착 목표 arrive_dt. ((route_def, 시작 분, 끝 분, 운행일, 뺀 후보), None) 또는 (None, 이유 dict).

        분은 **도착 목표의 운행일 축**이다. 역 도착 목표가 04:00 을 넘어 앞 운행일로 넘어가면(04:00 목표 − 도보 2분)
        판정기에는 그 운행일·그 분으로 넘기고 결과를 이 축으로 되돌린다 — 정수 분을 그대로 넘기면 판정기가
        240 미만을 +24h 로 읽어 그날 밤 막차로 간다(자체 대조 #1).
        not_before_dt: 앞 항목이 끝나는 시각. 이보다 먼저 떠나야 하는 후보는 싣지 않는다(겹치면 코어 등록이 거절한다).
        뺀 후보(left): 성립은 하지만 이동 항목 starts_at 보다 먼저 떠나야 하는 후보 · uses 표기 검사 불통과 후보 —
        [{label, reason}]. 코어로는 안 나간다(봉투 `left_out` · 23 결정 1).
        earliest_on_late: (86 · E2) True 면 **시각 때문에** 못 맞춘 구간(EARLIEST_ON_CODES — 앞 일정 끝 뒤 출발로는 늦음
        arrive_late · 첫차 전 · 공백 · 막차 뒤)의 이유 dict 에 `earliest`(earliest_summary · status found / not_found /
        unconfirmed)를 붙인다. 역·후보·데이터가 없어 못 만든 구간(no_data · no_service 등)에는 안 붙인다 — 기다려도
        안 된다(GPT 86 Q7 · 적용 범위). 기본 False — 결과·호출 횟수가 앞 판과 같다.
        by_mode: (93) True 면 수단별 대표 후보 한 벌을 `self.last_by_mode` 에 둔다(_by_mode · 돌려주는 값의 모양은 그대로 —
        이동을 못 만든 구간에도 남는다). range_from_dt = 앞 일정 **시작**(범위의 앞 끝 · 없으면 범위 판단 없음). 기본 False —
        결과·호출 횟수가 앞 판과 같다. ★last_by_mode 는 **바로 앞 leg() 호출 하나**의 값이다(호출마다 처음에 비운다) — Planner 는
        요청마다 새로 만들어 순서대로 쓴다(56 ①). 한 인스턴스를 여러 스레드가 같이 쓰면 남의 값을 읽는다(GPT 93 #6)."""
        from .geo import meters
        self.last_by_mode = None
        self._mix_memo = {}                 # 94 — 이 호출 안에서만(혼합 후보 판정 답 재사용)
        sdate, arrive_by = service_day(arrive_dt)
        nb = None
        if not_before_dt is not None:
            # 출발 하한은 분 **올림**(09:47:30 에 끝나면 09:48 부터) — 도착 목표는 내림이라 둘 다 안전 쪽(GPT #3)
            nd, nm_ = self._floor_min(not_before_dt)
            nb = nm_ + (nd - sdate).days * MIN_DAY
        wlim = self._walk_limit(party)
        buf = self.v.rv("buffer", "by_stage", self.stage)
        opts, left = [], []

        # ① 도보 직행 — 두 장소 직선이 도보 상한 안이면 후보. 여유는 정책 버퍼(수단 무관 · 39 결정 2).
        direct = meters(a_place["lat"], a_place["lon"], b_place["lat"], b_place["lon"])
        if direct <= wlim and (self.modes is None or "walk" in self.modes):
            # ☆`[2026-09-29 문제목록 #13]` 앞 판은 직선 × 우회계수만 봐서 하천·철도 건너편 두 점도 「걸어서 n분」이었다.
            #   보행망 라우터(GraphHopper foot — 자전거와 같은 서버)가 있으면 그 거리를 쓰고, 라우터가 「길 없음」이라
            #   하면 도보 후보를 싣지 않는다. 라우터가 없거나 닿지 않으면 종전 식(직선 × 계수)으로 낸다(대체 소스).
            wm, no_path = self._walk_net(a_place, b_place, direct)
            if no_path:
                left.append({"_o": {"_legs": []}, "label": "도보", "code": "no_walk_path",
                             "reason": "보행망에 두 장소를 잇는 길이 없다(직선으로는 도보 상한 안)"})
            else:
                eta = max(1, math.ceil(wm / self.speed / 60))
                opts.append({"eta_min": eta, "uses": [], "_legs": [], "_route": "도보",
                             "_start": arrive_by - eta - buf, "_transfers": 0, "_n": 0, "_key": ("walk", 0, 0),
                             "_margin": buf, "_slack": 0, "_walk_min": eta,
                             "_walk_m": wm, "_fare": 0, "_severe": [], "_covered": False})

        # ② 대중교통 — 장소마다 도보 상한 안 역 **가까운 순 여럿**(막힌 역 뺌 · E1) → 역 짝마다 다목적 후보(판정기
        #   verify_multi) → 후보마다 마지막 성립 출발로 다시 판정. 짝은 가까운 짝부터 보고, **실을 수 있는 대중교통 후보가
        #   앞 일정 끝 뒤에 떠날 수 있는 짝에서 멈춘다**(_pair_settles · STATION_PAIR_MODE) — 그런 짝이 첫 짝이면 앞 판과
        #   같다. ★같은 역 조기 종료(아래)는 앞 판 정책 그대로 — 막힌 역을 뺀 뒤 양쪽 첫 역이 같아도 도보만 본다(사각지대 ·
        #   85 닫힘 전달에 적음).
        sa, sb, pairs = self._station_pairs(a_place, b_place, wlim)
        oa, ob = (sa[0] if sa else None), (sb[0] if sb else None)
        why, r, n_mode = None, None, 0
        if oa is None or ob is None:
            blocked = [p["name"] for p, s_ in ((a_place, sa), (b_place, sb))
                       if not s_ and self.disruptions and self._any_station_near(p, wlim)]
            if blocked:
                # 87 — 이 이유는 아래 버스 직행(③)·혼합(⑤) 후보도 없을 때만 나간다 → 그 뜻으로 좁힌다
                why = {"code": STATION_BLOCKED_CODE,
                       "reason": f"걸어갈 역이 모두 사고로 막혔고({', '.join(blocked)}) 버스 직행·혼합 후보도 없다"}
            else:
                why = {"code": "no_data", "reason": "도보 상한 안에 지하철역이 없다"
                       + (f"({a_place['name']})" if oa is None else f"({b_place['name']})")}
        elif same_station(oa[0], oa[2], ob[0], ob[2]):          # 55 GPT #2 — 동명이역은 역명이 같아도 다른 역
            why = {"code": "no_data", "reason": f"두 장소의 가장 가까운 역이 같다({oa[0]}) — 도보만 본다"}
        visited = []
        for pi, (_ia, _ib, xa, xb) in enumerate(pairs):
            got, r_, nm_ = self._transit_pair(xa, xb, pi, arrive_dt, sdate, arrive_by, party, first_visit, case_id, left)
            if r is None:
                r = r_                                             # 대표 이유의 바탕은 가장 가까운 짝의 판정기 답(앞 판과 같다)
            visited.append(f"{xa[0]}→{xb[0]}")
            n_mode += nm_
            opts.extend(got)
            if STATION_PAIR_MODE == "first_feasible" and self._pair_settles(got, nb, arrive_by, party, first_visit, case_id):
                break
        # ③ 버스 직행 — **장소 좌표 기준**(23 결정 4). 판정기 verify_multi 의 버스 후보는 역 좌표 기준이라
        #   장소→역→정류장 이중 도보가 붙었다(32 자체 대조 #4 보류). 같은 규칙(정류장_반경_m · route_type_제외 ·
        #   도보 상한은 직선 · 버스_직행_최대 · 성립 후보를 도보 짧은 순)으로 장소에서 바로 찾는다. 판정은 판정기가 한다.
        bus_opts = self._bus_direct(a_place, b_place, arrive_dt, sdate, arrive_by, party, first_visit, case_id, wlim, left)
        opts.extend(bus_opts)
        # ④ 자전거 — modes 에 bike 를 줄 때만 · 장소 좌표 기준 · lfd = 목표 − (eta+@)(58 · _bike_direct)
        bike_opts, bike_why = self._bike_direct(a_place, b_place, sdate, arrive_by, party, first_visit, case_id)
        opts.extend(bike_opts)
        # ⑤ 지하철+버스 혼합(87) — 지하철만·버스만 후보(① ② ③)보다 한 축이라도 앞설 때만 · 사고로 걸어갈 역이 막히면 대안 2단
        mix_opts = self._mixed(a_place, b_place, sa, sb, arrive_dt, sdate, arrive_by, party, first_visit, case_id,
                               wlim, opts, left)
        opts.extend(mix_opts)
        if by_mode:
            # 93 — 수단별 대표(options[] 를 고르기 **전** 후보에서 · 아래 흐름은 건드리지 않는다)
            self.last_by_mode = self._by_mode(opts, bus_opts, left, why, r, a_place, b_place, sa, sb, arrive_dt, sdate,
                                              arrive_by, party, first_visit, case_id, wlim, range_from_dt, visited)
        if pairs:
            if not any(o["_legs"] for o in opts):
                if r.candidates and n_mode == 0 and not bus_opts:
                    why = {"code": "no_data", "reason": f"고른 수단({', '.join(sorted(self.modes))}) 안의 후보가 없다"}
                else:
                    why = {"code": (r.out or {}).get("code") or "no_data",
                           "reason": (r.out or {}).get("reason") or r.reason}
                    if len(visited) > 1:
                        # GPT 85 #3 — 첫 짝의 이유를 전체 원인으로 일반화하지 않는다. 본 짝을 밝히고 첫 짝 이유는 그 짝 이름을 붙여
                        why["reason"] = (f"검토한 역 짝 {len(visited)}개({' · '.join(visited)} · 장소마다 가까운 역 최대 "
                                         f"{self._station_k()}개)에서 성립 후보 없음 — {visited[0]}: {why['reason']}")
        if bike_why is not None and not any(o["_legs"] for o in opts) and not (self.modes & {"subway", "bus"}):
            why = bike_why            # 자전거(·도보)만 고른 구간 — 자전거가 왜 안 됐는지를 이유로

        # uses 자가 검사(41 넘김 · 팀 route_uses.problem) — 불통과 후보는 코어 등록이 통째로 거절하므로 싣지 않는다
        keep = []
        for o in opts:
            bad = O.uses_problems(o["uses"])
            if bad:
                left.append({"_o": o, "label": o["_route"], "code": "uses_format",
                             "reason": "uses 표기 검사 불통과 — " + " · ".join(f"`{u}`: {p}" for u, p in bad)})
            else:
                keep.append(o)
        opts = keep
        if not opts:
            # #29 — 뺀 후보가 이유와 함께 skipped 항목에 실린다(코어로는 안 나간다). 대표 이유는 종전대로:
            #   표기 불통과만 있으면 그 이유, 아니면 판정기 이유(why)
            uf = [e for e in left if e["code"] == "uses_format"]
            base = ({"code": "no_data", "reason": uf[0]["reason"]} if uf
                    else why or {"code": "no_data", "reason": "성립하는 후보가 없다"})
            out = dict(base, left_out=self._fold_left(left)) if left else dict(base)
            if earliest_on_late and not_before_dt is not None and out.get("code") in EARLIEST_ON_CODES:
                # (GPT 86 Q1·Q7) 시각 때문에 못 맞춘 구간(첫차 전 · 공백 · 막차 뒤) — 기다려 닿는 가장 이른 도착을 붙인다
                out["earliest"] = self._earliest_or_reason(a_place, b_place, not_before_dt, party, first_visit, case_id, arrive_dt)
            return None, out
        if bike_why is not None:      # 58 — 자전거를 요청했는데 못 실은 이유를 봉투 left_out 에(코어로는 안 나감)
            left.append({"_o": {"_legs": []}, "label": "자전거(따릉이)", "code": bike_why["code"], "reason": bike_why["reason"]})
        # 계획 수단 — **가장 늦게 떠나도 되는 후보**(동률은 환승 적은 · 소요 짧은 · 생성 순). 순위가 아니라
        #   「일정대로 움직이게」 하나를 고르는 규칙이다. 나머지는 options 에 순위 없이 남는다.
        #   앞 일정 끝(nb)보다 이른 _start 후보도 **버리지 않고** 공통 출발 재판정까지 둔다(GPT 23 2차 #1) —
        #   역산이 실제보다 이르게 나왔을 수 있다. 자격(_start ≥ nb)이 되는 후보가 하나도 없으면 nb 에서 다시 본다.
        # ☆`[87 · GPT 87 #2]` 혼합 후보는 **추가만** — 계획 수단은 먼저 지하철만·버스만·도보 후보에서 **앞 판과 같은 순서**(자격 →
        #   nb 재판정)로 고르고, 거기서 하나도 안 나올 때만 혼합으로 같은 순서를 밟는다(사고로 걸어갈 역이 모두 막힌 구간 등 —
        #   85 E1 2단). 앞 판은 「자격 있는 후보가 하나라도 있으면 재판정 안 함」이라, 혼합이 자격을 가지면 기존 후보의 재판정을
        #   건너뛰어 계획이 바뀔 수 있었다(GPT 87 #2).
        planned, revived = self._choose_planned(opts, nb, lambda o: self._recheck_at(o, nb, arrive_by, party, first_visit, case_id)[0])
        if planned is None:
            for o in opts:
                left.append({"_o": o, "label": o["_route"], "code": "before_prev_end",
                             "reason": f"앞 일정이 {iso_of(sdate, nb)[11:16]}에 끝나는데 그 시각 출발은 재판정에서 불성립 · "
                                       f"{iso_of(sdate, o['_start'])[11:16]} 출발은 성립 확인(역산) · "
                                       f"환승 {o['_transfers']}회 · 소요 {o['eta_min']}분"})
            late = {"code": "arrive_late",
                    "reason": f"앞 항목이 끝난 뒤({not_before_dt.astimezone(KST):%H:%M}) 떠나서는 "
                              f"{arrive_dt.astimezone(KST):%H:%M} 도착에 맞는 후보가 없다"}
            if earliest_on_late:
                # 86 · E2 — 몇 분 밀면 되는지가 아니라 **가장 이른 도착**을 붙인다(코어가 한 번에 정확히 민다)
                late["earliest"] = self._earliest_or_reason(a_place, b_place, not_before_dt, party, first_visit,
                                                            case_id, arrive_dt)
            return None, late
        if revived:
            back = {g["_key"]: g for g in revived}          # 식별은 _key(종류·짝·번호) — _n 은 동률 깨기용(GPT 85 #4)
            opts = [back.get(o["_key"], o) for o in opts]
        start = planned["_start"]
        end = start + planned["eta_min"]
        # ★ options 는 **이동 항목 starts_at 에 떠나도 성립하는 후보만** 싣는다(32 GPT #1 · 23 결정 1).
        #   후보별 출발 시각 칸을 두지 않는다 — 코어는 후보를 바꿀 때 이동 항목 starts_at 을 그대로 쓴다
        #   (itinerary_changes.py:170 `depart=item.starts_at`). 더 일찍 떠나야 하는 후보는 봉투 `left_out` 에 이유와 함께.
        # ★ 더 이른 _start 후보는 **계획 출발(start)에서 판정기로 다시 본다**(GPT 23 #3) — 성립하면 그 값으로 싣고,
        #   불성립이면 「start 출발은 불성립 · _start 출발은 성립 확인」(그 사이 마지막 성립 시각은 찾지 않는다 · 2차 #2),
        #   판정기가 모르면 「그 시각에서 성립 확인 안 됨」.
        listed = []
        at = iso_of(sdate, start)[11:16]
        for o in opts:
            if o is planned or o["_start"] >= start:
                listed.append(o)
                continue
            got, verdict = self._recheck_at(o, start, arrive_by, party, first_visit, case_id)
            if got is not None:
                listed.append(got)
                continue
            facts = [f"환승 {o['_transfers']}회", f"소요 {o['eta_min']}분"]
            if o["_severe"]:
                facts.append("극심 혼잡 구간 있음")
            need = iso_of(sdate, o["_start"])[11:16]
            if verdict == "unknown":
                left.append({"_o": o, "label": o["_route"], "code": "not_confirmed",
                             "reason": f"이동 출발 {at}에서 성립이 확인되지 않아 뺐다(판정기 재판정 결과 모름) · "
                                       f"{need} 출발은 성립 확인(역산) · " + " · ".join(facts)})
            elif nb is not None and o["_start"] < nb:
                left.append({"_o": o, "label": o["_route"], "code": "before_prev_end",
                             "reason": f"앞 일정이 {iso_of(sdate, nb)[11:16]}에 끝나고, 이동 출발 {at} 출발은 재판정에서 불성립 · "
                                       f"{need} 출발은 성립 확인(역산) · " + " · ".join(facts)})
            else:
                left.append({"_o": o, "label": o["_route"], "code": "earlier_departure",
                             "reason": f"이동 출발 {at} 출발은 재판정에서 불성립 · {need} 출발은 성립 확인(역산 · "
                                       f"그 사이 마지막 성립 시각은 안 찾음 · 후보별 출발 시각 칸 없음) · " + " · ".join(facts)})
        opts = listed
        # 버스 직행 상한(rules candidates.버스_직행_최대) — **실을 자격을 다 본 뒤에** 자른다(GPT 23 #2).
        #   계획 버스는 유지하고, 남은 자리를 도보 짧은 순으로(2차 #6 · 상한이 0 이어도 계획 수단은 남긴다).
        bmax = self.v.R["candidates"]["버스_직행_최대"]["value"]
        buses = [o for o in opts if _is_bus(o)]
        buses.sort(key=lambda o: (o is not planned, o["_walk_m"] if o["_walk_m"] is not None else float("inf"), o["_n"]))
        n_keep = max(bmax, 1 if any(o is planned for o in buses) else 0)
        for o in buses[n_keep:]:
            left.append({"_o": o, "label": o["_route"], "code": "bus_cap",
                         "reason": f"버스 직행 상한 {bmax}개 — 계획 버스 유지 후 남은 자리를 도보 짧은 순으로(순위 아님)"})
        cut = {id(o) for o in buses[n_keep:]}
        opts = [o for o in opts if id(o) not in cut]
        taken = set()
        for o in opts:
            o["id"] = O.make_id(o["_legs"], taken)
            taken.add(o["id"])
        O.add_reasons(opts)
        for o in opts:
            if o["_walk_m"] is not None:
                o["walk_m"] = int(round(o["_walk_m"]))
            if o["_fare"] is not None:
                o["fare_krw"] = int(o["_fare"])
            elif o["_legs"] and o.get("_lr") is not None:
                # ☆#20 — 버스가 섞인 환승은 합성 요금 근거가 없다 → 확정 규칙(탈것별 요금의 합 상한)으로 상한을 싣고 밝힌다
                up = O.fare_upper_of(self.v, o["_legs"], o["_lr"])
                if up is not None:
                    o["fare_krw"] = int(up)
                    o["label"] = o["label"] + " · 요금은 환승 할인 전 상한"
            if self.display and o["_legs"]:
                tc = O.transfer_cars(self.v, self._tc, o["_legs"], o.get("_lr"), o.get("_day_type"))
                if tc:
                    o["transfer_car"] = tc
        left_checks = [{"code": e["code"], "check": e["_o"].get("_check")} for e in left]   # 시험·대조용(trace 에만)
        left = self._fold_left(left)
        if self.trace is not None:
            self.trace.append({"case": case_id, "date": sdate.isoformat(), "arrive_by_min": arrive_by,
                               "planned": planned["id"],
                               "options": [{"id": o["id"], "start_min": o["_start"], "eta_min": o["eta_min"],
                                            "margin_min": o.get("_margin"), "slack_min": o.get("_slack"),
                                            "transfers": o["_transfers"], "check": o.get("_check")}
                                           for o in opts],
                               "left_out": left, "left_checks": left_checks, "start_min": start})
        keys = ("id", "label", "eta_min", "walk_m", "fare_krw", "uses", "transfer_car")
        route = {"from": a_place["name"], "to": b_place["name"], "planned": planned["id"],
                 "options": [{k: o[k] for k in keys if k in o} for o in opts]}
        return (route, start, end, sdate, left), None


CORE_ROUTE_KEYS = ("from", "to", "planned", "options")
CORE_OPTION_KEYS = ("id", "label", "eta_min", "walk_m", "fare_krw", "uses")


def core_routes(routes):
    """코어(CreateTrip)로 옮길 routes — 계약 칸만 남긴다. `display=True` 로 뽑은 표시 전용 필드(transfer_car 등)를 뺀다
    (GPT 23 #10). 기본 호출(display=False)이면 그대로와 같다.
    ★ **이번에 plan() 이 만든 routes 에만** 쓴다 — 기존 팀 routes 와 합친 뒤에 쓰면 `eta_min_if_controlled`·`walk_note`
    같은 팀 칸까지 지운다(2차 #7). 순서: `body["routes"] = {**body.get("routes", {}), **core_routes(out["routes"])}`."""
    return {k: {**{f: r[f] for f in CORE_ROUTE_KEYS if f in r},
                "options": [{f: o[f] for f in CORE_OPTION_KEYS if f in o} for o in r.get("options", [])]}
            for k, r in (routes or {}).items()}


def _key_time(it):
    return (_parse_dt(it["starts_at"]), it.get("seq", 0))


def plan(places, items, party_size=None, constraints=None, *, runtime=None, stage="planning",
         modes=None, trace=None, routes=None, display=False, disruptions=None, earliest_on_late=False,
         by_mode=False):
    """places·items(·party_size·constraints) → {"items", "routes", "skipped", "basis"}.

    items : 입력 항목 중 이동이 아닌 것을 시각 순으로 두고, **장소가 다른 이웃 둘 사이마다** 이동 항목을 끼운다.
            그 구간에 입력 이동 항목(kind=mobility)이 있었으면 우리 값으로 바꾼다 — 이동 시각의 정본은 우리다.
            우리가 그 구간을 못 만들면(skipped) 입력 이동 항목을 그대로 남긴다.
            seq 는 합친 순서대로 1부터 다시 매긴다. 입력 항목의 다른 칸은 손대지 않는다.
            장소가 없는 항목(자유 시간 등)을 사이에 두면 그 앞뒤는 잇지 않는다 — 어디서 떠나는지 모른다.
            앞 항목이 끝나기 전에 떠나야 하는 후보는 싣지 않는다(코어 등록의 overlap 을 미리 피한다).
    routes: 이번에 만든 이동 항목의 경로 정의만 — **호출 쪽 routes 에 합친다**(update). 통째로 바꾸면
            남겨 둔 입력 이동 항목의 route 키가 사라진다.
    skipped: 이동 항목을 못 만든 구간 [{from, to, code, reason}] — 그 구간은 **값을 빼고** 낸다(모르면 뺀다).
    modes  : 후보 수단 거르기 — None(기본)이면 DEFAULT_MODES(지하철·버스·도보 · 자전거는 "bike" 를 줄 때만 · 56).
             bike 를 주면 장소→장소 따릉이 후보를 싣는다(58 · 출발 = 목표 − (eta+@) · 계획 단계는 실시간 거치 안 봄).
             자전거 테마는 modes=["bike", "walk"] — 여행자가 자전거로 이동한다고 했을 때만(추천하지 않는다 · 58).
             예시 파일은 {"subway", "walk"} 로 뽑았다(노트북 버스 데이터가 옛 판).
    trace  : 리스트를 주면 구간마다 내부 값(시작 분·@·slack·환승)을 적는다 — 코어로는 안 나간다.
    routes : (선택) 호출 쪽이 이미 가진 routes — 그 키와 입력 항목이 참조하는 route 키는 **새 키로 쓰지 않는다**(GPT #2).
    left_out: (봉투) 만든 구간에서 **싣지 않은 후보**와 이유 {route 키: [{label, code, reason}]} — 코어로 안 나간다.
            code `earlier_departure` = 성립하지만 이동 항목 starts_at 보다 먼저 떠나야 한다(후보별 출발 칸 없음 · 23 결정 1)
            · `uses_format` = 팀 route_uses 표기 검사 불통과(코어 등록이 거절한다).
            · (58) label 「자전거(따릉이)」 = bike 를 요청했지만 못 실은 이유(판정기 code 그대로 · 다른 후보는 실렸을 때).
    display: 표시 전용 필드(◆칸 — transfer_car)를 싣는다. 기본 off(답 전엔 만들어만 둔다 · 스펙 v1.4 밖 새 키).
    earliest_on_late: (86 · 83 E2) True 면 **시각 때문에** 못 맞춘 구간(skipped code `arrive_late` · 첫차 전 · 공백 ·
            막차 뒤 — EARLIEST_ON_CODES)에 `earliest` 를 붙인다(Planner.earliest · earliest_summary):
            status "found" → {starts_at, ends_at, arrive_by, eta_min, planned, label, uses, route, requested_arrive_by,
            slack_min} — arrive_by = 다음 항목이 시작할 수 있는 가장 이른 시각(여유 포함 · 이 값으로 민다) · slack_min =
            원래 목표 − arrive_by(음수 = 그만큼 민다) / status "not_found"·"unconfirmed"(탐색 한도) → {code, reason}.
            봉투(skipped)에만 실린다 — 일정을 미는 것은 코어 몫. 기본 False(결과 v2.2 와 같다).
    by_mode: (93) True 면 봉투에 `by_mode` 를 더한다 — 구간마다 **수단별 대표 후보 하나씩**(사용자가 고를 수 있게):
            [{from, to, from_place, to_place(장소 키 — 구간 식별), route(만든 이동의 routes 키 · 못 만든 구간은 None),
              range_from(앞 일정 시작), range_to(다음 일정 시작), range_min, modes{subway, bus, subway_bus, taxi}}].
            **이동을 이으려 한 구간마다 한 줄**(장소·좌표가 없어 못 만든 구간은 네 칸 모두 no_data · 운행일이 바뀌는 구간과
            같은 장소 구간은 줄이 없다 — 이동 자체를 만들지 않는다). 수단 칸은 status "found" → {label, legs[{mode, line|route,
            from, to}], uses, depart_at, arrive_at, eta_min(예정), worst_min(예정+여유), transfers, walk_m?, fare_krw?,
            within_range, search_limited?(혼합 — 판정하지 않은 후보가 남았다)} / status "none" → {code, reason}(code
            `unconfirmed` = 없다고 확인한 것이 아니라 탐색 상한으로 못 본 것). 시각은 **다음 일정 시작에 맞춰 성립을 확인한
            그 수단의 출발**(계획 이동과 같은 식 · 혼합은 더 늦은 성립 출발이 있을 수 있다) · 대표 = 가장 늦게 떠나도 되는
            후보(가장 이른 도착이 아니다) · within_range = 그 출발이 앞 일정 시작보다 뒤 · **범위 밖이어도 시각은 낸다**(띄울지는 받는 쪽).
            버스만 = 한 노선 직행뿐 · 택시 = 자리와 이유만. 코어 몸통(items·routes)으로는 안 나간다. 기본 False — 칸 자체가
            없고 결과·호출 횟수가 앞 판과 같다.
    """
    if runtime is None:
        from .runtime import get_verifier
        runtime = get_verifier(quiet=True)
    P = Planner(runtime, stage=stage, modes=modes, display=display)
    P.trace = trace
    if disruptions:
        from .errors import CaseInputError
        from .verify_time import Verifier
        bad = [d for d in disruptions if (d or {}).get("kind") not in Verifier.DISR_KINDS]
        if bad:
            raise CaseInputError(f"모르는 사고 kind: {[(d or {}).get('kind') for d in bad]} "
                                 f"(쓸 수 있는 것: {', '.join(Verifier.DISR_KINDS)})")
        P.disruptions = tuple(dict(d) for d in disruptions)
    constraints = dict(constraints or {})
    party = party_of(party_size, constraints)
    first_visit = constraints.get("first_visit", True)

    pl = {p["key"]: p for p in (_as_dict(x) for x in places)}
    its = [_as_dict(x) for x in items]
    stay = sorted((it for it in its if it.get("kind") != "mobility"), key=_key_time)
    moves_in = sorted((it for it in its if it.get("kind") == "mobility"), key=_key_time)

    def moves_between(a, b):
        """입력에 있던 이동 항목 중 a 시작 ~ b 시작 사이의 것 — 우리가 그 구간을 못 만들면 그대로 남긴다."""
        lo, hi = _parse_dt(a["starts_at"]), _parse_dt(b["starts_at"])
        return [dict(m) for m in moves_in if lo <= _parse_dt(m["starts_at"]) < hi]

    reserved = set(routes or {}) | {str(it["route"]) for it in its if it.get("route")}
    merged, routes, skipped, left_out, not_linked = [], {}, [], {}, []
    by_mode_out = []

    def bm_row(a, b, pa, pb, body):
        """by_mode 한 줄 — 구간 식별은 장소 키(from_place·to_place)와 range_to(다음 항목 시작)로 한다(배열 위치에 기대지 않게 ·
        GPT 93 #5). 이름은 장소가 없으면 항목 제목."""
        return {"from": (pa or {}).get("name") or a.get("title"), "to": (pb or {}).get("name") or b.get("title"),
                "from_place": a.get("place"), "to_place": b.get("place"), "route": None, **body}

    def bm_blank(a, b, pa, pb, code, reason):
        """leg() 를 부르기 전에 못 만든 구간(장소·좌표 없음) — 네 칸 모두 같은 이유로 비운 줄(93 · GPT 93 #5)."""
        if not by_mode:
            return
        lo, hi = _parse_dt(a["starts_at"]), _parse_dt(b["starts_at"])
        sd, hi_m = service_day(hi)
        ld, lo_m = service_day(lo)
        lo_m += (ld - sd).days * MIN_DAY
        by_mode_out.append(bm_row(a, b, pa, pb, {
            "range_from": iso_of(sd, lo_m), "range_to": iso_of(sd, hi_m), "range_min": int(hi_m - lo_m),
            "modes": {k: {"status": "none", "code": code, "reason": reason} for k in BY_MODE_KEYS}}))
    kept_unverified = []

    def unverified(ms, why):
        # 73 후속(GPT 대조 78 Q5) — 체류 항목 사이가 아니어서 우리가 판정하지 않은 입력 이동도 「검증 안 됨」으로 드러낸다
        ms = [dict(m) for m in ms]
        kept_unverified.extend({"title": m.get("title"), "route": m.get("route"), "starts_at": m.get("starts_at"),
                                "why": why} for m in ms)
        merged.extend(ms)

    if not stay:                                   # 이동 항목만 온 입력 — 그대로 돌려준다(GPT #4)
        unverified(moves_in, "no_stay")
    else:                                          # 첫 비이동 항목보다 앞선 입력 이동 항목은 그대로 앞에 둔다(GPT #4)
        first = _parse_dt(stay[0]["starts_at"])
        unverified([m for m in moves_in if _parse_dt(m["starts_at"]) < first], "before_first_stay")

    def skip(a, b, entry):
        # ☆`[2026-09-29 문제목록 #26]` 못 채운 구간의 입력 이동 항목을 남길 때 **검증 안 됐다는 것을 드러낸다** — 앞 판은
        #   조용히 남겨, 호출 안내대로 items·routes 만 옮기면 검증 안 된 경로가 우리 값처럼 등록됐다.
        kept = moves_between(a, b)
        entry = dict(entry, kept_input_moves=len(kept))
        skipped.append(entry)
        kept_unverified.extend({"title": m.get("title"), "route": m.get("route"), "starts_at": m.get("starts_at"),
                                "why": entry.get("code")} for m in kept)
        merged.extend(kept)

    def keep_as_is(a, b, why):
        # ☆`[73 후속 · 3-10]` 운행일 경계(day_boundary)·같은 장소(same_place)로 **잇지 않고** 남긴 입력 이동도 검증 안 된 것이다 —
        #   앞 판은 skip() 을 거치지 않아 kept_unverified 에 안 실렸다(items 만 옮기면 우리 값처럼 보인다).
        kept = moves_between(a, b)
        kept_unverified.extend({"title": m.get("title"), "route": m.get("route"), "starts_at": m.get("starts_at"),
                                "why": why} for m in kept)
        merged.extend(kept)

    for i, a in enumerate(stay):
        merged.append(dict(a))
        if i + 1 >= len(stay):
            unverified([m for m in moves_in if _parse_dt(m["starts_at"]) >= _parse_dt(a["starts_at"])], "after_last_stay")
            break
        b = stay[i + 1]
        # ☆`[2026-09-29 문제목록 #45]` 운행일이 바뀌는 두 항목(1일차 저녁 → 2일차 아침) 사이는 잇지 않는다 — 그 사이에
        #   숙소로 가지만 숙소 위치를 모른다. 앞 판은 날짜를 가리지 않아 전날 마지막 장소 → 다음 날 첫 장소 이동을 만들었다.
        if service_day(_parse_dt(a.get("ends_at") or a["starts_at"]))[0] != service_day(_parse_dt(b["starts_at"]))[0]:
            not_linked.append({"from": a.get("title"), "to": b.get("title"), "code": "day_boundary",
                               "reason": "운행일이 바뀐다 — 사이에 숙소로 가지만 숙소 위치를 모른다"})
            keep_as_is(a, b, "day_boundary")
            continue
        pa, pb = pl.get(a.get("place")), pl.get(b.get("place"))
        if pa is None or pb is None:
            skip(a, b, {"from": a.get("title"), "to": b.get("title"), "code": "no_data",
                        "reason": "장소가 없는 항목이다 — 어디서 떠나는지(어디로 가는지) 모른다"})
            bm_blank(a, b, pa, pb, "no_data", "장소가 없는 항목이다 — 어디서 떠나는지(어디로 가는지) 모른다")
            continue
        if a.get("place") == b.get("place"):
            keep_as_is(a, b, "same_place")
            continue
        if pa.get("lat") is None or pb.get("lat") is None:
            skip(a, b, {"from": pa["name"], "to": pb["name"], "code": "no_data", "reason": "좌표가 없다"})
            bm_blank(a, b, pa, pb, "no_data", "좌표가 없다")
            continue
        got, why = P.leg(pa, pb, _parse_dt(b["starts_at"]), party, first_visit,
                         case_id=f"{a.get('place')}_to_{b.get('place')}",
                         not_before_dt=_parse_dt(a.get("ends_at") or a["starts_at"]),
                         earliest_on_late=earliest_on_late,
                         by_mode=by_mode, range_from_dt=_parse_dt(a["starts_at"]) if by_mode else None)
        bm = P.last_by_mode if by_mode else None
        if bm is not None:
            bm = bm_row(a, b, pa, pb, bm)
            by_mode_out.append(bm)
        if got is None:
            skip(a, b, {"from": pa["name"], "to": pb["name"], **why})
            continue
        route, start, end, sdate, left = got
        key = f"{a.get('place')}_to_{b.get('place')}"
        n = 2
        while key in routes or key in reserved:
            key = f"{a.get('place')}_to_{b.get('place')}_{n}"
            n += 1
        routes[key] = route
        if bm is not None:
            bm["route"] = key
        if left:
            left_out[key] = left
        if trace is not None and trace:
            trace[-1]["route"] = key
        new = {"seq": 0, "kind": "mobility", "title": f"{pa['name']} → {pb['name']}",
               "starts_at": iso_of(sdate, start), "ends_at": iso_of(sdate, end), "route": key}
        # ☆`[2026-09-29 문제목록 #45]` 입력 이동 항목을 우리 값으로 바꿀 때 그 항목의 다른 칸(detail·id 등)을 잃지 않는다
        olds = moves_between(a, b)
        if olds:
            new = {**{k: v for k, v in olds[0].items() if k not in new}, **new}
        merged.append(new)
    for n, it in enumerate(merged, 1):
        it["seq"] = n
    out = {"items": merged, "routes": routes, "skipped": skipped, "left_out": left_out,
           "not_linked": not_linked, "kept_unverified": kept_unverified,
           "basis": {"timetable_built_at": runtime.timetable_built_at, "rules_version": runtime.rules_version,
                     "plan_version": PLAN_VERSION,
                     "decided_at": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:00+09:00")}}
    if by_mode:
        out["by_mode"] = by_mode_out          # 93 — 켰을 때만 칸이 생긴다(기본 출력 모양 무변경)
    return out


def plan_doc(doc, *, runtime, stage="planning", modes=None, trace=None, display=False, earliest_on_late=False,
             by_mode=False):
    """CLI 가 읽는 입력 JSON 한 벌 → plan(). 기존 `routes` 도 넘긴다(그 키를 새 키로 안 쓰게 · GPT 2차 #1)."""
    return plan(doc.get("places") or [], doc.get("items") or [], doc.get("party_size"), doc.get("constraints"),
                runtime=runtime, stage=stage, modes=modes, trace=trace, routes=doc.get("routes"),
                display=display, earliest_on_late=earliest_on_late, by_mode=by_mode)


def main(argv=None):
    from . import paths as _paths_cli
    _paths_cli.load_cli_env()           # #48 — 명령줄은 저장소 맨 위 .env 의 DATA_DIR 을 쓴다(서버는 configure)
    ap = argparse.ArgumentParser(description="이동 값 내놓기 — places·items → 이동 항목 + routes (출력 스펙 v1.3)")
    ap.add_argument("--in", dest="inp", required=True, help="입력 JSON {places, items, party_size?, constraints?, routes?}")
    ap.add_argument("--out", help="출력 JSON 경로(없으면 표준출력)")
    ap.add_argument("--stage", default="planning", choices=("planning", "pre_departure", "in_progress"))
    ap.add_argument("--no-basis", action="store_true", help="basis 를 빼고 낸다(예시 파일을 판 바뀔 때마다 안 흔들리게)")
    ap.add_argument("--modes", nargs="*", help="후보 수단 거르기(subway bus walk bike · 콤마도 됨) — 없으면 subway bus walk(자전거 뺌 · 56)")
    ap.add_argument("--display", action="store_true",
                    help="표시 전용 필드(transfer_car · ◆칸)를 싣는다 — 기본 off, 코어 계약(v1.4) 밖")
    ap.add_argument("--earliest-on-late", action="store_true",
                    help="앞 항목이 끝난 뒤 떠나서는 못 맞추는 구간에 가장 이른 출발·도착(skipped[].earliest)을 붙인다(86)")
    ap.add_argument("--by-mode", action="store_true",
                    help="수단별 대표 후보(지하철만·버스만·지하철+버스·택시 하나씩)를 봉투 by_mode 에 더한다(93)")
    ap.add_argument("--trace", help="구간마다 내부 값(시작 분·@·slack)을 이 JSON 에 적는다 — 대조용")
    a = ap.parse_args(argv)
    if a.modes is not None:           # 58 — `--modes subway,bus,walk,bike` 도 받는다(띄어쓰기와 같다)
        a.modes = [m for x in a.modes for m in x.split(",") if m]
    doc = json.loads(Path(a.inp).read_text(encoding="utf-8"))
    from .runtime import build_verifier
    rt = build_verifier(quiet=True)
    tr = [] if a.trace else None
    res = plan_doc(doc, runtime=rt, stage=a.stage, modes=a.modes, trace=tr, display=a.display,
                   earliest_on_late=a.earliest_on_late, by_mode=a.by_mode)
    if a.trace:
        Path(a.trace).write_text(json.dumps(tr, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.no_basis:
        res.pop("basis", None)
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(txt + "\n", encoding="utf-8")
        print(f"[plan] 이동 {sum(1 for x in res['items'] if x['kind'] == 'mobility')} · "
              f"못 만든 구간 {len(res['skipped'])} → {a.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
