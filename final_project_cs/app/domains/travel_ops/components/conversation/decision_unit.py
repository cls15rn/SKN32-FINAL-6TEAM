# -*- coding: utf-8 -*-
"""결정 단위 — 고객 문장 하나를 **여행 상태를 보며** 한 번에 읽고 「할 일 + 대상」을 고른다. `[2026-09-29]`

★왜(사용자 지시 — 「규칙 땜질 말고 근본 해결」, Codex 두 계정 1회차 합의). 전에는 분류기 · 추출기 모델이 **문장만** 보고,
  대상은 낱말 규칙(끼니 · 서수 · 종류 · 날)이 메웠다. 모델이 일정 · 변경 이력 · 앞 대화 · 화면 선택을 몰라 「그 식당」
  「원래대로」「첫 식당」「이전에 요청한 거」를 못 풀었고, 새 표현마다 규칙이 늘었다.
  계획: `wiki/records/plans/2026-09-29_1640_채팅_결정단위_실행계획.md`.

★모델은 **고르기만** 한다 — 일정 목록의 짧은 id(i1 …) · 변경 id(c2 …) · 정해진 할 일 · 사실 종류 중에서(Ollama `format` 의
  JSON 스키마 enum — 목록 밖 값을 낼 수 없다). 값(시각 · 장소 · 버전 번호)은 만들지 않는다. 늦는 분은 **문장에 있는 숫자**만 받는다.
★검증 · 실행은 서버다(`trip_messages._run_decision`) — id 가 가리키는 항목의 종류 · 보호 · 버전을 보고 기존 실행 함수를 부른다.
  확정이 안 되면 되묻는다. ★모델 실패 · 검증 실패에 옛 규칙 경로로 넘기지 않는다(옛 오처리를 되살린다 — Codex 지적).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

ACTIONS = ("apply_change", "propose_alternatives", "rollback", "redo", "answer_fact", "ask_policy", "answer_service",
           "report_delay", "report_closed", "change_time", "add_item", "remove_item", "clarify", "other")
#: ★`[2026-10-13]` 알아듣지만 **아직 채팅으로 실행하지 않는** 요청 — 할 일 목록에 없으면 모델이 가장 가까운 「장소 바꾸기」 되묻기로
#:  억지로 끼웠다(시간 변경 58건 실행 0 · 엉뚱한 되묻기 29건, 추가·삭제 26건 정상 0 — x600 서버 웹 채팅 시험 2026-10-13).
#:  실행 기능이 생기기 전까지 늘 같은 정직한 안내로 답한다. 되묻기 후보(`choices`)로는 내지 않는다(실행할 수 없는 것을 고르게 하지 않는다).
UNSUPPORTED = {
    "change_time": "시간이나 날짜를 옮기는 일은 아직 채팅에서 하지 못해 일정은 바꾸지 않았어요. 계획서의 일정표에서 직접 수정해 주세요.",
    #  `[2026-10-14 활동 회신 10번]` 넣기 · 빼기도 다음에 할 일을 말한다 — 등록한 여행의 「일정 수정」(수정 모드)에서 ＋ 로 넣고 삭제로 뺄 수 있다.
    #  채팅으로 넣는 기능(이름 확인 → 그날 시간 · 이동 · 식사 다시 맞춤 → 먼저 물어보기)은 최종발표(10-26) 뒤로 미뤘다.
    "add_item": "일정에 새 곳을 넣는 일은 아직 채팅에서 하지 못해 일정은 바꾸지 않았어요. 계획서 위쪽 연필 단추(「일정 수정」)를 눌러 ＋ 로 직접 넣어 주세요.",
    "remove_item": "일정을 빼는 일은 아직 채팅에서 하지 못해 일정은 바꾸지 않았어요. 계획서 위쪽 연필 단추(「일정 수정」)를 눌러 직접 빼 주세요.",
}
FACTS = ("detail", "address", "phone", "hours", "time", "next", "booking", "day", "move",
         "route_here", "nearby_dining", "nearby_activity", "schedule", "changeability",
         "description", "duration", "price", "entry_booking", "outdoor", "food", "none")
#: 고객의 **현재 위치**가 있어야 답할 수 있는 사실 — 요청에 위치가 없으면 서버가 「현재 위치를 알려 주세요」로 답한다(`trip_here`)
LOCATION_FACTS = ("route_here", "nearby_dining", "nearby_activity")
KIND_KO = {"dining": "식당", "activity": "활동", "lodging": "숙소", "flight": "항공편"}
#: 변경 이력에 싣는 최근 변경 수 — ★우리가 고른 값. 고객이 가리키는 변경은 대개 최근이다
RECENT_CHANGES = 8
#: 일정 밖 일반 질문·추천의 답은 모델이 쓴 문장이다 — 여행 자료로 확인한 것이 아니라는 말을 코드가 붙인다(모델이 빼먹지 못하게)
GENERAL_NOTE = "※ 일반 안내예요. 이 여행의 일정·조회 자료로 확인한 내용은 아니에요."

SYSTEM = """너는 여행 일정 서비스의 요청 해석기다. 고객 문장 하나를 읽고 할 일(action)과 대상을 고른다. 추측으로 채우지 말고, 할 일이나 대상이 분명하지 않으면 clarify.
할 일(action):
- apply_change: 일정을 다른 곳으로 바꿔 달라(「바꿔」「변경해」「다른 데로」「다른 걸로」).
- propose_alternatives: 현재 일정 대신 갈 새로운 장소 후보를 찾아 달라. 후보 장소를 요청한 경우에만 쓴다. 기존 일정의 목록·상태·변경 가능 여부 조회에는 쓰지 않는다.
- rollback: 앞서 바뀐 것을 원래대로 되돌려 달라(「원래대로」「되돌려」「이전 걸로」). target_change 에 되돌릴 변경 id — 문장이 어느 변경인지 말하지 않으면 (가장 최근) 표시가 붙은 변경.
- redo: 되돌렸던 변경을 다시 적용해 달라(「다시 진행」「다시 바꿔」). target_change 에 다시 적용할 변경 id.
- answer_fact: 이 여행의 사실을 묻는다 — fact 에 **물은 것 하나**를 고른다(가장 좁은 칸):
  address(위치 — 「어디 있어」「어딨어」「어디야」「어디쯤」「위치」「주소」) · phone(전화·연락처) ·
  hours(운영시간 — 「몇 시까지 해」「언제 열어·닫아」「쉬는 날」) · time(그 일정에 몇 시에 가나) · next(다음 일정) ·
  booking(예약) · day(하루 일정·요약) · move(이동 — 「어떻게 가」「얼마나 걸려」「가는 길」) ·
  detail(무엇을 묻는지 **특정하지 않을 때만** — 「알려줘」「자세히」「세부정보」「어떤 곳이야」).
  ★현재 위치 — 출발지가 **「여기」「지금 위치」「내 위치」「지금 있는 곳」**이면: route_here(거기서 목적지까지 가는 길 · 걸리는 시간, target_item = 도착지 ·
  말하지 않으면 none) · nearby_dining(근처 식당) · nearby_activity(근처 볼거리 · 갈 곳). 예: 「지금 있는 곳에서 금용문 가는 길」「지금 위치에서 다음 일정까지 얼마나 걸려」는 route_here.
  출발지도 일정의 곳 이름(「경복궁에서 금용문까지」)이면 move 다.
  애매하면 detail 이 아니라 가장 좁은 칸을 고른다 — 「그건 어딧는거야」는 앞 대화의 장소를 가리키는 address 다.
  이름만 대고 「알려줘」면(「경복궁 알려줘」) 앞 대화와 상관없이 detail 이다.
- ask_policy: 여행의 규정·이용 조건·장소 선택 기준·확인 절차를 묻는다(취소·위약금·환불·동반·날씨 기준, 식당이 미정일 때 장소를 정하는 방법).
  일반 여행 질문도 검색으로 확인한다. 특정 일정이나 가게 이름이 없어도 target_item=none으로 ask_policy를 쓴다.
  장소를 고르는 기준을 물은 것은 실제 후보 추천이나 일정 변경 실행이 아니다. 여행과 관계없는 일반 상식·투자·잡담은 other다.
- answer_service: triPilot 자체의 기능·사용법·지원 범위·정체를 묻는다. 「너가 할 수 있는 게 뭐야?」「사용법 알려줘」「뭘 부탁할 수 있어?」「예약도 대신 해 줘?」「너는 누구야」.
  서비스 사용법 질문에는 일정 대상이 필요 없다: target_item=none, target_change=none, fact=none, choices=[].
  「식당을 바꿀 수 있어?」처럼 기능 지원을 물으면 answer_service다. 「이 식당 바꿔 줘」처럼 실제 실행 요청은 apply_change다.
  「경복궁이 뭐야?」는 장소 질문 answer_fact/detail이며, 「이 예약 취소 위약금은?」은 규정 질문 ask_policy다.
- report_delay: 늦는다고 알린다 — minutes 에 문장에 적힌 분(없으면 0).
- report_closed: 장소가 닫았다고 알린다.
- change_time: 일정의 시각·날짜·머무는 시간을 바꾸거나 일정을 앞뒤·다른 날로 옮겨 달라(장소는 그대로).
- add_item: 일정에 새 장소·활동·식사·쉬는 시간을 넣어 달라.
- remove_item: 일정을 빼거나 취소해 달라(다른 곳으로 바꾸라는 말이 아니다).
- clarify: 무엇을 할지나 어느 일정인지 분명하지 않다 — choices 에 **이해한 범위 안의 해석 후보**
  1~3개(가장 그럴듯한 것부터 — 각각 action · target_item · target_change · fact). 「이거」「그거」가 무엇인지 화면 선택이나
  직전 대화로 정할 수 없으면 clarify.
- other: 여행 일정과 무관한 말 · 인사. 서비스 기능·사용법·정체 질문은 answer_service로 구분한다.
먼저 고객이 묻는 객체를 구분한다: 기존 일정의 항목·상태를 물으면 읽기, 새로운 대체 장소를 찾으라면 후보 추천, 실제로 바꾸라면 변경 실행이다.
모든 문장에서 change_scope를 함께 분류한다: place(장소), time(시각), date(날짜), mixed(둘 이상), none(변경 요청 아님), unclear(불명확).
destination_text: 고객이 바꿀 곳을 직접 이름이나 조건으로 말했으면 그 부분을 원문 그대로 복사한다(장소 이름을 고치거나 지어내지 않는다). 말하지 않았으면 빈 문자열이다.
condition_text: 요청이 어떤 일이 생길 때만 하라는 조건부라면 그 조건 부분을 원문 그대로 복사한다. 조건이 없으면 빈 문자열이다.
시간·날짜만 바꾸라는 말은 장소 교체가 아니다 — change_time 이다. action=apply_change 또는 propose_alternatives는 change_scope=place일 때만 쓴다.
목록 조회: 일정 여러 개나 시간대의 항목을 묻는 말은 answer_fact다. 일정 목록은 fact=schedule,
변경할 수 있는 항목·고정·예약 때문에 바꿔도 되는지를 묻는 말은 fact=changeability다.
변경 가능한 기존 활동들을 알려 달라는 말도 answer_fact/changeability이며, 대체 장소 추천이나 실제 변경으로 읽지 않는다.
「오후 일정 중 바꿀 수 있는 일정이 뭐가 있어?」는 변경 실행이나 다른 장소 추천이 아니다.
이 두 사실은 target_item=none, choices=[]와 query={day,period,kind,position}를 반드시 낸다.
day는 all(날짜 미지정·전체 여행), today, tomorrow, d1…(여행 N일차).
period는 all, morning(00~12시), afternoon(12~18시), evening(18~24시).
kind는 all, activity, dining, lodging. 항목이 없어도 범위를 그대로 내며 다른 시간대 대상을 고르지 않는다.
그 밖의 사실·실제 변경 요청에는 query를 내지 않는다. 예: 「오후 일정을 바꿔 줘」는 실제 변경 요청이다.
대상(target_item)은 일정 목록의 id 중 하나이거나 none. 목록의 「그날 식당 N번째」「그날 활동 N번째」「끼니」를 그대로 써라 —
「둘째 날 두 번째 활동」은 2일차 줄 중 「그날 활동 2번째」인 줄이다. 날을 말하지 않은 「첫/두 번째」는 1일차에서 찾는다.
아침·점심·저녁은 그 끼니 표시가 붙은 식당이다. 「마지막 일정」은 목록의 마지막 줄이다.
확실하지 않은 채로 바꾸지 마라 — 할 일은 분명한데 대상이 둘 이상으로 읽히면 clarify 로 후보를 내라.
「그 식당」「거기」「방금 바꾼 거」처럼 가리키는 말은 직전 대화와 변경 이력에서 찾는다.
화면에서 고른 일정은 문장이 대상을 말하지 않을 때만 쓴다. 문장이 식당을 말했는데 고른 일정이 활동이면 쓰지 않는다.
추가 경계 규칙:
- 기존 여행 일정의 끼니·항목을 지칭한 장소 후보 추천은 그 항목을 target_item으로 고르는 propose_alternatives다. 실제 변경 적용은 명시적으로 변경을 실행해 달라고 한 경우에만 한다.
- nearby_dining과 nearby_activity는 고객이 현재 위치·여기·근처·주변을 기준으로 찾는다고 명시한 경우에만 고른다. 기존 일정의 대체 후보 요청을 현재 위치 주변 검색으로 바꾸지 않는다.
- 범위 조회의 날짜를 고객이나 앞 대화가 지정하지 않았다면 query.day는 all이다. today나 tomorrow를 추측하지 않는다.
- 맨 위 「지금」 줄이 있으면 오늘·내일·지금·몇 분 뒤 같은 상대적인 때는 그 줄의 날짜·시각을 기준으로 푼다. 「오늘」은 그 줄의 날짜다.
"""

# 읽기 질문의 범위를 실행 단계까지 보존한다. 예문을 외우는 대신 같은 계약으로 모든 표현을 해석한다.
SYSTEM += """
읽기 질문은 reads에 요청한 질문을 모두 순서대로 담는다. 앞의 '물은 것 하나'는 각 reads 항목 안에서 적용한다.
reads 항목: fact, target_item, reference(explicit/previous/selection/none), origin_item, query, text.
text는 해당 질문 부분의 원문이다. target_item은 답할 대상이며 부정한 대상은 고르지 않는다.
reference: 새 이름·별칭·날짜·종류를 명시하면 explicit, 지시어·생략으로 직전 대상을 이어 물으면 previous,
화면 선택만 근거이면 selection, 전체 조회·잡담·대상 불명은 none. previous는 구조화된 직전 답변 대상을 사용한다.
이동의 target_item은 도착지, origin_item은 출발지다. 출발지가 여러 일정에 해당하면 none으로 두고 되묻는다.
day/schedule/changeability/outdoor는 query={day,period,kind,position}를 반드시 낸다. N일차는 dN이며 영어 day N도 같다.
position은 조회 범위의 전체(all), 첫 일정(first), 마지막 일정(last)이다. 첫/마지막만 물으면 해당 한 항목만 답하도록 position을 보존한다.
다른 사실의 query는 null이다. 하루 조회는 target_item=none이며 명시한 날을 버리지 않는다.
추가 사실: description(장소 소개), duration(머무는 시간), price(이용 요금), entry_booking(방문 예약 필요 여부),
food(식단·먹을거리 조언), outdoor(비·야외 여부). booking은 고객 본인의 예약 기록이며 entry_booking과 다르다.
한 문장에서 소개와 시각, 가격과 방문 예약을 함께 물으면 각각 reads 항목으로 보존한다.
잡담이나 인사와 여행 질문이 섞여 있어도 여행 질문을 빼지 않는다. 바꾸지 말라는 요청은 실행 행동으로 해석하지 않는다.
social에는 현재 문장의 인사·감정에만 짧게 응답한다. 의미 없는 오타 조각을 그대로 되풀이하지 않는다. 없으면 빈 문자열이다.
general_requests에는 현재 문장의 일정 외 지식·추천·대화 질문을 각각 {question, answer}로 담는다. question은 그 요청의 원문, answer는 바로 보여 줄 실질적인 답이다. 요청이 없으면 []이다.
요청을 인사·감정 social로 대신 처리하지 않는다. 추천을 요청하면 실제 추천 대상을 하나 제시하고 이유를 짧게 답한다. 공감이나 일정 변경 여부 안내만으로 answer를 채우지 않는다.
하나의 문장에 일정 질문과 일반 질문이 섞여도 reads와 general_requests 모두 빠짐없이 만든다. 일반 요청의 답은 여행 일정 변경으로 이어지지 않는다.
general_requests에는 여행의 장소·가격·영업·예약·날씨·이동 사실을 넣지 않는다. 검증이 필요한 여행 사실은 반드시 reads로 보낸다. 고객이 부탁하지 않은 일반 답을 추가하지 않는다.
social로 여행의 장소·가격·영업·예약·날씨·이동에 관한 사실을 답하지 마라. 그 내용은 반드시 reads로 보낸다.
food의 advice는 고객이 말한 식단 조건에 맞는 일반 조언만 짧게 쓴다. 특정 점포의 메뉴·재료·안전성을 확인했다고 쓰지 마라.
다른 fact의 advice는 빈 문자열이다. reads가 있으면 choices=[]로 두며, 최상위 action/fact/target_item/query는 첫 reads와 일치시킨다.
일반 취향·영화 등 잡담에 자연스럽게 답할 수 있지만 실시간 인기·상영·가격은 주장하지 않는다.
읽기 이외 행동에는 reads=[]를 낸다. 여러 사실을 물었는데 첫 질문만 남기지 않는다.
현재 위치가 필요한 route_here/nearby_dining/nearby_activity는 기존 단일 요청 계약을 사용하므로 reads=[]로 둔다.
이동에는 origin_text에 고객이 적은 출발지 이름 부분만 그대로 복사한다. 예컨대 출발지를 넓은 종류로 부르면 그 표현을 유지하며 일정의 긴 이름으로 바꾸지 않는다. 출발지를 말하지 않으면 빈 문자열이다.
transport_mode는 any(수단 미지정), transit(대중교통), subway(지하철), bus(버스), walk(도보), taxi(택시)다. 지정한 수단을 버리지 않는다. 이동 외 질문은 origin_text="", transport_mode=any다.
앞뒤 · 시각 질문은 **답할 일정을 고르지 말고 기준만** 아래 칸에 적는다 — 답은 서버가 일정표로 계산한다.
anchor_kind: 고객이 무엇을 기준으로 물었나 — item(이름이나 끼니로 가리킨 일정) · time(말한 시각) · now(지금, 「다음 일정」「곧」) · previous(앞 답의 일정, 「그 전에」「거기 다음」) · none(기준 없음).
anchor_item: item 일 때 **고객이 가리킨 그 일정**의 별칭 — 답할 일정이 아니다. 끼니(아침·점심·저녁)는 일정 줄의 끼니 표시로 고른다. 그 밖에는 none.
anchor_time: time 일 때 고객이 말한 시각을 24시간 HH:MM 으로(「오후 3시」 15:00, 「정오」 12:00, 「두시 반」 낮이면 14:30, 「30분 뒤」는 「지금」 줄 시각+30분). 그 밖에는 "".
at_day: 고객이 말한 날(today/tomorrow/dN). 말하지 않았으면 none.
relation: 기준 그 자체면 at, 기준 바로 앞(「바로 앞·그 전·직전·~ 전에」)이면 before, 기준 바로 뒤(「다음·직후·~ 먹고 나서·다다음」)면 after. steps 는 몇 번째(다다음 2), 보통 1.
target_item 은 앞뒤 · 시각 질문에서 none 으로 둔다(서버가 계산한다).
예: 「오후 3시에 뭐 해?」 time 15:00 at / 「국립현대미술관 바로 앞 일정」 item=미술관 별칭 before / 「점심 먹고 나서 뭐 해?」 item=점심 일정 별칭 after /
「저녁 먹기 전에 어디 가?」 item=저녁 일정 별칭 before / 「다음 일정 뭐야?」 now after / 「그럼 그 전에는?」 previous before / 「미술관 몇 시야?」 기준 없음, target_item=미술관.
"""

# 일정 밖 장소의 역사·유래와 추천 이유는 모델 기억만으로 사실처럼 말하지 않는다. 확인 자료가 있는 경로(규정·여행 자료 검색)로 보내거나, 근거를 밝힌다.
SYSTEM += """
일정 목록에 없는 장소·유적·건물의 역사·유래·시기·인물을 사실로 묻는 말(reads가 없는 단독 질문)은 general_requests가 아니라
action=ask_policy, target_item=none으로 보낸다. 서버가 여행 자료 검색으로 확인하고 자료가 없으면 못 찾았다고 답한다. 일정 목록에 있는 장소의 소개는 description이다.
general_requests의 answer에는 확인할 수 없는 연도·수치·인명·순위·「가장 유명한」「인기 많은」 같은 평가를 쓰지 않는다. 모르면 모른다고 쓴다.
추천의 이유는 이 프롬프트에 실제로 있는 근거만 쓴다: 일정 목록(시각·종류·이미 정해진 장소), 직전 대화에서 고객이 직접 한 말.
그 밖의 이유는 「일반적인 제안」이라고 밝힌다. 영업시간·요금·혼잡·평점·날씨는 추천 이유로 쓰지 않는다.
고객의 취향을 기억하거나 저장한다고 말하지 않는다. 이번 여행 대화에 있는 말만 참고했다고 쓴다.
"""


@dataclass
class Stop:
    alias: str
    item: Any


@dataclass
class Change:
    alias: str
    version: int
    reason: str
    target_item_id: UUID | None
    line: str


@dataclass
class Decision:
    action: str
    item: Any | None
    change: Change | None
    fact: str
    minutes: int | None
    question: str
    raw: dict[str, Any] = field(default_factory=dict)
    #: 되물을 때 **이해한 범위 안의 해석 후보** — [{action, item, change, fact}] (목록 밖 값은 서버가 버린다)
    choices: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    query: dict[str, str] | None = None
    reads: list[dict[str, Any]] = field(default_factory=list)
    social: str = ""

    def record(self) -> dict[str, Any]:
        """Case 에 남기는 모양 — 모델이 낸 그대로(`raw`)와 서버가 푼 값."""
        return {"raw": self.raw, "action": self.action, "item": getattr(self.item, "title", None),
                "item_id": str(self.item.item_id) if self.item is not None else None,
                "change": self.change.alias if self.change else None, "fact": self.fact, "minutes": self.minutes,
                "choices": [{"action": c["action"], "item": getattr(c["item"], "title", None),
                             "change": c["change"].alias if c["change"] else None, "fact": c["fact"]}
                            for c in self.choices],
                "seconds": round(self.seconds, 2), **({"query": self.query} if self.query else {}),
                **({"reads": self.reads} if self.reads else {}), **({"social": self.social} if self.social else {})}


class DecisionFailed(RuntimeError):
    """모델을 못 불렀거나 답이 스키마 밖이다 — 부르는 쪽은 **바꾸지 않고** 묻는다."""


def _local(moment: datetime) -> datetime:
    return moment.astimezone(KST)


def stops_of(items: list[Any]) -> list[Stop]:
    """이동을 뺀 일정 — 시각 순으로 i1 … 을 붙인다."""
    stops = sorted((i for i in items if i.kind != "mobility"), key=lambda i: (i.starts_at, i.seq))
    return [Stop(f"i{n}", item) for n, item in enumerate(stops, start=1)]


def _day_no(item: Any, first: datetime | None) -> int:
    return (_local(item.starts_at).date() - _local(first).date()).days + 1 if first else 1


def changes_of(store: Any, conn: Any, trip_id: UUID, items: list[Any]) -> list[Change]:
    """버전마다 **장소가 바뀐 항목**(이동 제외)을 「N일차 HH:MM 종류: 전 → 후 (이유)」로. 최근 `RECENT_CHANGES` 개."""
    labels = {"customer_request": "고객 요청", "rollback": "되돌림", "customer_choice": "고객 선택",
              "auto_adjusted": "자동 변경", "customer_report": "고객 신고"}
    history = store.versions(conn, trip_id)[-(RECENT_CHANGES + 1):]     # ★최근 판만 — 판마다 항목을 읽는다
    current_ids = {i.item_id for i in items}
    first = min((i.starts_at for i in items), default=None)
    out: list[Change] = []
    previous = None
    for row in history:
        version = row["version"]
        now = [i for i in store.items(conn, trip_id, version) if i.kind != "mobility"]
        if previous is not None and row["reason"] != "created":    # 첫 줄은 비교 기준으로만 쓴다
            before_ids = {i.item_id for i in previous}
            added = {i.seq: i for i in now if i.item_id not in before_ids}
            removed = {i.seq: i for i in previous if i.item_id not in {x.item_id for x in now}}
            for seq, new in sorted(added.items()):
                old = removed.get(seq)
                old_name = (old.place or {}).get("name", old.title) if old else "(없음)"
                new_name = (new.place or {}).get("name", new.title)
                out.append(Change(f"c{version}", version, row["reason"],
                                  new.item_id if new.item_id in current_ids else None,
                                  f"{_day_no(new, first)}일차 {_local(new.starts_at):%H:%M} "
                                  f"{KIND_KO.get(new.kind, new.kind)}: {old_name} → {new_name} "
                                  f"({labels.get(row['reason'], row['reason'])})"))
        previous = now
    return out[-RECENT_CHANGES:]


def _meal(item: Any) -> str:
    hour = _local(item.starts_at).hour
    return "아침" if hour < 11 else "점심" if hour < 16 else "저녁"


def item_lines(stops: list[Stop], selected: str = "none") -> list[str]:
    """★모델이 **세지 않게** 서버가 센다 — 날 · 그날 같은 종류 안의 순번 · 끼니를 줄마다 적는다.
    ☆`[2026-09-29 재생 시험]` 7일 · 42항목 여행에서 「둘째 날 두 번째 활동」「셋째 날 세 번째 식당」을 모델이 세다 틀렸다
      (엉뚱한 대상 3건). 세는 일은 계산이지 해석이 아니다 — 모델은 적힌 표시를 고르기만 한다."""
    first = stops[0].item.starts_at if stops else None
    counts: dict[tuple[int, str], int] = {}
    out = []
    last_day = _day_no(stops[-1].item, first) if stops else 1
    for s in stops:
        day = _day_no(s.item, first)
        kind = KIND_KO.get(s.item.kind, s.item.kind)
        counts[(day, kind)] = counts.get((day, kind), 0) + 1
        mark = f"그날 {kind} {counts[(day, kind)]}번째" + (f" · {_meal(s.item)}" if s.item.kind == "dining" else "")
        tag = "(첫날)" if day == 1 else "(마지막 날)" if day == last_day else ""
        out.append(f"{s.alias} | {day}일차{tag} {_local(s.item.starts_at):%m-%d %H:%M} | {kind} · {mark} | "
                   f"일정 제목: {s.item.title} | 연결 장소: {(s.item.place or {}).get('name') or '(없음)'}"
                   + (" | (화면에서 고름)" if s.alias == selected else ""))
    return out


_WEEK = "월화수목금토일"


def now_line(stops: list[Stop], now: datetime | None) -> str:
    """★`[2026-10-13]` 해석기에 주는 「지금」 — 한국 시각과 그날이 여행 몇 일차인지. 없으면 빈 문자열.

    전에는 이 줄이 없어 모델이 「오늘 15시」「다음 일정」「지금」을 풀 기준이 없었다 — 「오늘 15시」에 내일 15:00 일정을 골랐다
    (x600 서버 웹 채팅 시험 2026-10-13, 처리 기록 331번). 일정 짚기 모델(`item_pointer.py`)은 이미 「오늘」을 받고 있다."""
    if now is None:
        return ""
    local = _local(now)
    stamp = f"{local:%Y-%m-%d}({_WEEK[local.weekday()]}) {local:%H:%M}"
    if not stops:
        return f"지금(한국 시각): {stamp}"
    first, last = _local(stops[0].item.starts_at).date(), _local(stops[-1].item.starts_at).date()
    if local.date() < first:
        where = f"여행 시작 전 — 1일차는 {first:%m-%d}"
    elif local.date() > last:
        where = "여행이 끝난 뒤"
    else:
        where = f"오늘은 여행 {(local.date() - first).days + 1}일차"
    return f"지금(한국 시각): {stamp} · {where}"


def _prompt(stops: list[Stop], changes: list[Change], history: list[dict[str, Any]], selected: str,
            message: str, now: datetime | None = None) -> str:
    lines = item_lines(stops, selected)
    change_lines = [f"{c.alias} | 대상 {next((s.alias for s in stops if s.item.item_id == c.target_item_id), 'none')}"
                    f" | {c.line}" + (" (가장 최근)" if c is changes[-1] else "")
                    # ★`[2026-09-29 ui 세션 지적]` 그 변경의 결과가 지금 일정에 없으면(이미 되돌렸거나 다시 바뀜) 적는다 —
                    #   모델이 이미 되돌린 변경을 또 되돌리려 했다
                    + (" (지금 일정에 없음 — 이미 되돌렸거나 다시 바뀜)" if c.target_item_id is None else "")
                    for c in changes]
    from app.domains.travel_ops.components.conversation.chat_log import TURN_CHARS

    talk = "\n".join(f"{'고객' if t['role'] == 'customer' else 'triPilot'}: {t['text'][:TURN_CHARS]}"
                     for t in history) or "(없음)"
    previous = next((t for t in reversed(history) if t.get("role") == "assistant"), {})
    focus = (previous.get("reply") or {}).get("focus") or {}
    focus_alias = next((s.alias for s in stops if str(s.item.item_id) == str(focus.get("item_id"))), "none")
    head = now_line(stops, now)
    return ((head + "\n\n" if head else "") + "일정 목록:\n" + ("\n".join(lines) or "(없음)")
            + "\n\n바뀐 이력(아래가 최근):\n" + ("\n".join(change_lines) or "(없음)")
            + f"\n\n직전 대화:\n{talk}\n구조화된 직전 답변 대상: {focus_alias}\n\n화면에서 고른 일정: {selected}\n\n고객 문장: {message}")


def _schema(stops: list[Stop], changes: list[Change]) -> dict[str, Any]:
    from .schedule_query import query_days, query_schema
    items = [s.alias for s in stops] + ["none"]
    change_ids = sorted({c.alias for c in changes}) + ["none"]
    choice = {"type": "object", "properties": {
        "action": {"type": "string", "enum": [a for a in ACTIONS if a != "clarify" and a not in UNSUPPORTED]},
        "target_item": {"type": "string", "enum": items},
        "target_change": {"type": "string", "enum": change_ids},
        "fact": {"type": "string", "enum": list(FACTS)}},
        "required": ["action", "target_item", "target_change", "fact"]}
    read = {"type": "object", "properties": {
        "fact": {"type": "string", "enum": [f for f in FACTS if f != "none" and f not in LOCATION_FACTS]},
        "target_item": {"type": "string", "enum": items},
        "origin_item": {"type": "string", "enum": items},
        "origin_text": {"type": "string"},
        "transport_mode": {"type": "string", "enum": ["any", "transit", "subway", "bus", "walk", "taxi"]},
        "reference": {"type": "string", "enum": ["explicit", "previous", "selection", "none"]},
        "query": {"anyOf": [query_schema(stops), {"type": "null"}]},
        "text": {"type": "string"}, "advice": {"type": "string"},
        # ★`[2026-10-14]` 고객이 말한 날 · 시각 · 앞뒤 — 서버가 일정표로 대상을 계산한다(`time_anchor.py`). 반드시 낸다(없으면 none · "" · at · 1)
        "anchor_kind": {"type": "string", "enum": ["none", "item", "time", "now", "previous"]},
        "anchor_item": {"type": "string", "enum": items},
        "anchor_time": {"type": "string"},
        "at_day": {"type": "string", "enum": [*query_days(stops), "none"]},
        "relation": {"type": "string", "enum": ["at", "before", "after"]},
        "steps": {"type": "integer"}},
        "required": ["fact", "target_item", "origin_item", "origin_text", "transport_mode", "reference", "query", "text", "advice",
                     "anchor_kind", "anchor_item", "anchor_time", "at_day", "relation", "steps"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "target_item": {"type": "string", "enum": items},
        "target_change": {"type": "string", "enum": change_ids},
        "fact": {"type": "string", "enum": list(FACTS)},
        "change_scope": {"type": "string", "enum": ["place", "time", "date", "mixed", "none", "unclear"]},
        "destination_text": {"type": "string"},
        "condition_text": {"type": "string"},
        # ★`[2026-10-14]` 읽기 항목 없이 맨 위 칸으로만 답할 때도 날 · 시각 · 앞뒤를 낸다(읽기 항목과 같은 뜻)
        "anchor_kind": {"type": "string", "enum": ["none", "item", "time", "now", "previous"]},
        "anchor_item": {"type": "string", "enum": items},
        "anchor_time": {"type": "string"},
        "at_day": {"type": "string", "enum": [*query_days(stops), "none"]},
        "relation": {"type": "string", "enum": ["at", "before", "after"]},
        "steps": {"type": "integer"},
        "minutes": {"type": "integer"},
        "query": query_schema(stops),
        "reads": {"type": "array", "items": read},
        "social": {"type": "string"},
        "general_requests": {"type": "array", "maxItems": 8, "items": {
            "type": "object", "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
            "required": ["question", "answer"], "additionalProperties": False}},
        "choices": {"type": "array", "items": choice, "maxItems": 3}},
        # ★되묻는 문장은 모델이 쓰지 않는다 — 서버가 후보로 만든다(답이 길어져 p95 가 7초였다, 재생 시험 2026-09-29)
        # ★`[2026-10-14]` 목적지 · 조건 칸은 **반드시 낸다**(없으면 빈 문자열) — 선택 칸이면 학습 E4B 가 아예 내지 않았다(재생 434건 중 목적지 0/43)
        "required": ["action", "target_item", "target_change", "fact", "minutes", "choices", "reads", "social", "general_requests",
                     "destination_text", "condition_text", "anchor_kind", "anchor_item", "anchor_time", "at_day", "relation", "steps"]}


def decide(chat: Any, *, stops: list[Stop], changes: list[Change], history: list[dict[str, Any]],
           selected_item_id: UUID | None, message: str, num_predict: int = 400, now: datetime | None = None) -> Decision:
    """모델 한 번. 스키마 밖 · 목록 밖 값이면 `DecisionFailed`."""
    from app.domains.travel_ops.components.core_hooks.feedback import masked

    selected = next((s.alias for s in stops if selected_item_id and s.item.item_id == selected_item_id), "none")
    started = time.monotonic()
    try:
        raw = chat.structured(SYSTEM, _prompt(stops, changes, history, selected, masked(message), now),
                              _schema(stops, changes), num_predict=num_predict)
    except Exception as exc:                          # noqa: BLE001 — 모델 장애는 부르는 쪽이 「바꾸지 않고 묻기」로 답한다
        raise DecisionFailed(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
    seconds = time.monotonic() - started
    action = raw.get("action")
    if action not in ACTIONS:
        raise DecisionFailed(f"할 일이 목록 밖이다: {action!r}")
    # ★`[2026-10-13]` change_scope 는 응답 형식의 필수 항목이 아니다 — 모델(또는 이전 형식의 답)이 빼면 이 칸이 생기기 전처럼
    #   변경 요청은 장소 변경으로 본다. 모델이 time · date · mixed · unclear 라고 분명히 말한 경우만 장소 교체를 막는다.
    #   (전에는 빼면 「해석하지 못했어요」로 떨어져 일정 변경 요청 전체가 막혔다 — CI e2e 18건)
    change_scope = raw.get("change_scope", "place" if action in {"apply_change", "propose_alternatives"} else "none")
    if change_scope not in {"place", "time", "date", "mixed", "none", "unclear"}:
        raise DecisionFailed("변경할 대상이 유효하지 않습니다")
    scope_blocked = action in {"apply_change", "propose_alternatives"} and change_scope != "place"
    if scope_blocked:
        # 변경 범위를 확인하지 못했거나 시간·날짜 요청이면 팀의 장소 교체 실행기로 보내지 않는다.
        raw = {**raw, "scope_guard": {"requested": change_scope, "blocked_action": action}}
        action = "other"
    if action in UNSUPPORTED:
        raw = {**raw, "unsupported": action}
        action = "other"
    # ★`[2026-10-13]` 조건부 변경(「비 오면 바꿔 줘」)은 미리 걸어 둘 수 없다 — 지금 실행하지 않는다.
    #   전에는 조건을 담을 칸이 없어 비가 안 왔는데도 바로 바꿨다(x600 서버 웹 채팅 시험 2026-10-13, 처리 기록 416번).
    #   ★`[2026-10-14]` 막지 않고 **바로 바꾸지만 않는다** — 바꾸는 할 일을 「다른 안 보여 주기」로 돌려 고객이 고르게 한다.
    #   조건 칸을 모델이 조건이 아닌 말로 채우는 일이 많았다(학습 E4B 재생 434건: 막힌 10건 중 진짜 조건은 「내일 비 오면」 1건,
    #   나머지는 「다른 식당으로」「더 싼 데로」 같은 원하는 곳 설명이나 「비 오니까」 같은 지금의 이유). 통째로 막으면 정상 요청까지
    #   「조건이 붙은 변경은 미리 걸어 둘 수 없다」는 엉뚱한 답이 됐다(x600 서버 웹 채팅 재시험 2026-10-14). 다른 안 보여 주기는
    #   일정을 바꾸지 않으므로 진짜 조건부 요청도 미리 바뀌지 않는다.
    condition = str(raw.get("condition_text") or "").strip()[:200]
    if action in {"apply_change", "report_closed"} and condition:
        raw = {**raw, "condition_guard": {"condition": condition, "blocked_action": action}}
        action = "propose_alternatives"
    # ★`[2026-10-13]` 고객이 바꿀 곳을 직접 말했는데(「통인시장으로」) 그곳으로 바로 바꾸는 실행은 아직 없다. 전에는 근처 첫 후보로
    #   바꾸고 「요청하신 대로」라고 답했다(16건 중 14건). 이제 바꾸지 않고 후보를 보여 고르게 한다 — 말한 곳이 후보에 있으면 거기서 고른다.
    destination = str(raw.get("destination_text") or "").strip()[:200]
    if action == "apply_change" and destination:
        raw = {**raw, "named_destination": {"text": destination, "blocked_action": action}}
        action = "propose_alternatives"
    by_alias = {s.alias: s.item for s in stops}
    target = raw.get("target_item") or "none"
    if target != "none" and target not in by_alias:
        raise DecisionFailed(f"대상이 목록 밖이다: {target!r}")
    changes_by = {c.alias: c for c in changes}
    change_alias = raw.get("target_change") or "none"
    if change_alias != "none" and change_alias not in changes_by:
        raise DecisionFailed(f"변경이 목록 밖이다: {change_alias!r}")
    fact = raw.get("fact") if raw.get("fact") in FACTS else "none"
    from .schedule_query import QUERY_FACTS, query_days, valid_query
    from .time_anchor import clean as clean_time

    query = raw.get("query")
    general = raw.get("general_requests", [])
    if not isinstance(general, list) or len(general) > 8:
        raise DecisionFailed("일반 요청 목록이 유효하지 않습니다")
    general_answers = []
    dropped_general = 0
    for request in general:
        if (not isinstance(request, dict) or set(request) != {"question", "answer"}
                or not all(isinstance(request.get(k), str) for k in ("question", "answer"))
                or len(request["question"]) > 1000 or len(request["answer"]) > 2000):
            raise DecisionFailed("일반 요청의 질문·답이 유효하지 않습니다")
        if not (request["question"].strip() and request["answer"].strip()):
            handled = raw.get("unsupported") or raw.get("scope_guard") or raw.get("condition_guard")
            if action in ("other", "answer_service") and not handled:
                # 일반 질문 자체가 고객 요청인데 답이 비었다 — 조용히 넘기지 않고 실패로 드러낸다
                raise DecisionFailed("일반 요청의 질문·답이 유효하지 않습니다")
            # ★`[2026-10-14]` 빈 답이 든 일반 질문 칸은 그 칸만 버린다(일정과 무관한 덧붙임 답이다). 전에는 해석 전체를 버렸다
            #   (학습 E4B 재생 434건 중 19건 — 예: 「카페 하나 추가해줘」를 일반 질문으로도 적고 답은 비움)
            dropped_general += 1
            continue
        general_answers.append(request["answer"].strip())
    if general_answers:
        general_answers.append(GENERAL_NOTE)
    social ="\n\n".join([str(raw.get("social") or "")[:500], *general_answers]).strip()
    if raw.get("scope_guard"):
        social = "시간이나 날짜 변경 요청을 장소 교체로 실행하지 않았습니다. 이 채팅에서는 시간·날짜 변경을 적용하지 못하므로 일정표에서 직접 수정해 주세요."
    if raw.get("unsupported"):
        social = UNSUPPORTED[raw["unsupported"]]
    # ★`[2026-10-13]` 고른 할 일에 **쓰지 않는 칸**(읽기 질문 · 조회 범위)이 채워져 있어도 해석 전체를 버리지 않는다 — 그 칸만 무시하고 남긴다.
    #   전에는 할 일은 맞게 골라도(예: 「점심 1시간 당겨줘」→ 시간 변경) 군더더기 읽기 칸 때문에 「해석 실패」가 났다
    #   (x600 서버 웹 채팅 시험 2026-10-13: 해석 실패 13건 중 9건 · 같은 입력 재생에서 gemma4:12b 56건 중 33건).
    #   단 일정을 **실제로 바꾸는** 할 일은 그대로 막는다 — 조회 범위가 붙은 변경은 조회를 원했는지 변경을 원했는지 애매해서다(잘못 바꾸지 않게).
    ignored: dict[str, Any] = {}
    if action not in ("answer_fact", "answer_service", "apply_change", "rollback", "redo", "report_closed"):
        if raw.get("reads"):
            ignored["reads"] = len(raw["reads"])
        if query is not None:
            ignored["query"], query = query, None
        if fact in (*QUERY_FACTS, "day", "outdoor"):
            ignored["fact"], fact = fact, "none"
    if action == "answer_fact" and query is not None and fact not in (*QUERY_FACTS, "day", "outdoor") and not raw.get("reads"):
        # ★`[2026-10-14]` 한 일정을 묻는 사실(다음 · 시각 · 이동 · 영업시간 …)에 붙은 조회 범위는 쓰지 않는 칸이다 — 버린다.
        #   전에는 해석 전체를 버렸다(학습 E4B 재생 434건 중 30건). 일정을 바꾸지 않는 읽기라 안전하다
        ignored["query"], query = query, None
    if dropped_general:
        ignored["general_requests"] = dropped_general
    if ignored:
        raw = {**raw, "ignored_for_action": ignored, **({"reads": []} if "reads" in ignored else {})}
    reads = []
    for read in raw.get("reads") or []:
        if (action != "answer_fact" or not isinstance(read, dict) or read.get("fact") not in FACTS
                or read.get("fact") in (*LOCATION_FACTS, "none") or read.get("target_item") not in {*by_alias, "none"}
                or read.get("origin_item") not in {*by_alias, "none"}
                or read.get("reference") not in {"explicit", "previous", "selection", "none"}):
            raise DecisionFailed("읽기 질문의 대상·종류가 유효하지 않습니다")
        scoped = read["fact"] in (*QUERY_FACTS, "day", "outdoor")
        if (read.get("transport_mode", "any") not in {"any", "transit", "subway", "bus", "walk", "taxi"}
                or not isinstance(read.get("origin_text", ""), str)):
            raise DecisionFailed("이동의 출발지 표현·수단이 유효하지 않습니다")
        if scoped and not valid_query(read.get("query"), stops):
            raise DecisionFailed("읽기 질문의 범위가 유효하지 않습니다")
        if not scoped and read.get("query") is not None:
            # ★`[2026-10-14]` 한 일정 사실(시각 · 주소 …) 읽기에 붙은 조회 범위는 그 칸만 버린다 — 읽기라 아무것도 바꾸지 않는다.
            #   전에는 해석 전체를 버려 「오후 3시에 뭐 해?」가 「해석하지 못했어요」로 끝났다(재생 107건 중 9건)
            read = {**read, "query": None, "ignored_query": read.get("query")}
        # ★`[2026-10-14]` 날 · 시각 · 앞뒤 칸은 틀려도 해석을 버리지 않고 그 칸만 비운다(`time_anchor.clean`)
        reads.append(clean_time(dict(read), [*query_days(stops), "none"], [*by_alias, "none"]))
    top_time = clean_time(raw, [*query_days(stops), "none"], [*by_alias, "none"])
    if (action == "answer_fact" and not reads and fact not in (*QUERY_FACTS, "day", "outdoor", *LOCATION_FACTS)
            and (top_time["anchor_kind"] != "none" or top_time["relation"] != "at")):
        # ★`[2026-10-14]` 읽기 항목 없이 맨 위 칸에만 날 · 시각 · 앞뒤를 적었으면 읽기 항목 하나로 옮긴다 — 그래야 서버가 일정표로 계산한다
        #   (`time_anchor.py`). 날 · 시각이 없는 맨 위 답은 그대로 둔다. 재생 107건 v2 에서 「다음 일정 뭐야?」 「30분 뒤에 뭐 해?」
        #   「오늘 18시 30분에 뭐 해?」 등 14건이 이 경로로 와서 계산을 거치지 못했다.
        reads = [{"fact": fact if fact != "none" else "time", "target_item": target, "origin_item": "none", "origin_text": "",
                  "transport_mode": "any", "reference": "explicit" if target != "none" else "none", "query": None,
                  "text": message or "", "advice": "", **{k: top_time[k] for k in ("anchor_kind", "anchor_item", "anchor_time", "at_time", "at_day", "relation", "steps")},
                  "from_top_level": True}]
    if reads:
        first_read = reads[0]
        fact, target, query = first_read["fact"], first_read["target_item"], first_read.get("query")
    if action == "answer_service" and (target != "none" or change_alias != "none" or raw.get("fact") != "none"
                                        or raw.get("choices") or query is not None):
        raise DecisionFailed("서비스 기능 안내에 일정 대상·변경·사실 조회를 섞을 수 없습니다")
    # A validated first/last scope determines one current itinerary item. Redundant
    # in-trip model item hints cannot turn that read into an invalid write request.
    duplicates = raw.get("choices")
    canonical_scope = (action == "answer_fact" and change_alias == "none"
                       and fact in (*QUERY_FACTS, "day", "outdoor")
                       and valid_query(query, stops) and query.get("position") in ("first", "last")
                       and isinstance(duplicates, list) and all(
                           isinstance(c, dict) and c.get("action") == "answer_fact" and c.get("fact") == fact
                           and c.get("target_item") in {*by_alias, "none"}
                           and c.get("target_change") == "none" for c in duplicates))
    if canonical_scope:
        target = "none"
        duplicates = [{**c, "target_item": "none"} for c in duplicates]
        for read in reads:
            if read["fact"] in (*QUERY_FACTS, "day", "outdoor") and read.get("query") == query:
                read.update(target_item="none", reference="none")
    if fact in (*QUERY_FACTS, "day", "outdoor") and (reads or fact in QUERY_FACTS):
        identical_reads = isinstance(duplicates, list) and all(
            isinstance(c, dict) and c.get("action") == "answer_fact" and c.get("fact") == fact
            and c.get("target_item") == "none" and c.get("target_change") == "none" for c in duplicates)
        if action != "answer_fact" or target != "none" or change_alias != "none" or not identical_reads or not valid_query(query, stops):
            raise DecisionFailed("목록 조회의 범위·행동이 유효하지 않습니다")
    elif query is not None:
        raise DecisionFailed("읽기 범위를 다른 행동에 사용할 수 없습니다")
    minutes = raw.get("minutes")
    # ★늦는 분은 **문장에 적힌 숫자**만 — 모델이 만든 수를 받지 않는다
    if not isinstance(minutes, int) or minutes <= 0 or str(minutes) not in re.findall(r"\d+", message or ""):
        minutes = None
    choices = []
    # 같은 읽기 해석의 반복은 모호성이 아니다. 원본 출력은 남기고 중복 표시 후보만 뺀다.
    for option in ([] if fact in QUERY_FACTS or canonical_scope else (raw.get("choices") or [])):
        if (not isinstance(option, dict) or option.get("action") not in ACTIONS or option.get("action") == "clarify"
                or option.get("action") in UNSUPPORTED):
            continue                                   # ★목록 밖 후보는 버린다(지어낸 해석을 보이지 않는다)
        item = by_alias.get(option.get("target_item") or "none")
        change = changes_by.get(option.get("target_change") or "none")
        if (option.get("target_item") or "none") != "none" and item is None:
            continue
        choices.append({"action": option["action"], "item": item, "change": change,
                        "fact": option.get("fact") if option.get("fact") in FACTS else "none"})
    decision = Decision(action=action, item=None if scope_blocked else by_alias.get(target),
                        change=None if scope_blocked else changes_by.get(change_alias), fact=fact,
                        minutes=minutes, question=str(raw.get("question") or "").strip()[:200], raw=raw, seconds=seconds,
                        choices=choices[:3], query=query, reads=reads, social=social)
    return guard_target(decision, stops=stops, changes=changes, history=history, selected=selected, message=message or "")


#: 문장이 대상을 **가려 주는 말** — 날 · 순서 · 때(오늘 · 다음 · 방금). 이것이 있으면 모델이 센 순번 표시(`item_lines`)를 믿는다.
_PINPOINT = re.compile(r"\d+\s*일\s*차|\d+\s*일\s*째|\d+\s*번째|\d+\s*월\s*\d+\s*일|(?:첫|둘|셋|넷|다섯|여섯|일곱|두|세|네)\s*(?:째|번째)|첫|처음|마지막|내일|모레|오늘|어제|글피|다음|이전|방금|아까|그날|이틀|사흘")
#: 이름이 겹치는지 볼 때 **빼는** 두 글자 — 일정 이름이 아니라 요청 문장의 흔한 말이다
_COMMON_WORDS = ("식당", "활동", "일정", "점심", "저녁", "아침", "숙소", "카페", "맛집", "다른", "바꿔", "바꿀", "변경", "추천", "알아", "알려", "휴무", "문을", "닫았", "열었", "오늘", "내일",
                 "있는", "없는", "으로", "에서", "해줘", "주세", "세요", "어요", "아요", "거기", "여기", "저기", "그거", "이거", "말고", "걸로", "데로", "하는", "해서", "래요", "대요")
_CHANGING = ("apply_change", "propose_alternatives", "report_closed")


def _grams(text: str) -> set[str]:
    """한 낱말 안의 두 글자 조각 — 이름이 문장에 나오는지 가늠한다(낱말 사이를 잇는 조각은 만들지 않는다)."""
    out: set[str] = set()
    for token in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()):
        out.update(token[i:i + 2] for i in range(len(token) - 1))
    return out - set(_COMMON_WORDS)


def _name_of(item: Any) -> str:
    return str((item.place or {}).get("name") or item.title or "")


def guard_target(decision: Decision, *, stops: list[Stop], changes: list[Change], history: list[dict[str, Any]], selected: str, message: str) -> Decision:
    """★`[2026-10-06 사용자 지시 — 엉뚱한 대상 변경 0건]` **문장이 가리키지 않은 일정을 모델이 짐작으로 바꾸게 두지 않는다.**

    ☆실제 사고(재생 시험 2026-09-30, 7일 여행): 「저녁 식당 바꿔 줘」 — 날을 말하지 않았고 저녁 식당이 7곳인데 모델이 3일차 저녁 식당을 골라 바꾸기로 했다(정답은 화면에서 고른 날의 것이거나 되묻기).
    모델은 **고르기만** 한다는 약속(위 머리말)대로, 바꾸는 할 일(`_CHANGING`)에 낸 대상이 **근거 있는지**를 서버가 따로 본다. 근거가 하나도 없으면 바꾸지 않고 **되묻는다**(후보 = 같은 종류 · 같은 끼니 일정, 화면에서 고른 날 가까운 순 3곳).
    근거 = ①화면에서 고른 일정이다 ②문장 속 이름이 그 일정 이름과 **가장 많이** 겹치고 그렇게 겹치는 일정이 그것 하나다(여럿이면 날 · 순서가 있어야 한다) ③직전 대화에 그 일정 이름이 나왔거나 가장 최근 변경의 대상이다 ④문장에 날 · 순서 · 때를 가리키는 말이 있다(`_PINPOINT`)
    ⑤같은 종류(식당이면 같은 끼니)의 일정이 여행에 **하나뿐**이다 ⑥날을 말하지 않은 약속대로다(화면에서 고른 일정이 있으면 그 날 · 없으면 1일차에 같은 종류가 하나뿐이고 그것을 골랐다).
    되묻는 쪽으로만 바꾼다 — 대상을 대신 정해 주지 않는다."""
    item = decision.item
    if decision.action not in _CHANGING or item is None:
        return decision
    target = next((s for s in stops if s.item is item), None)
    if target is None:
        return decision
    # ⑤ 같은 종류 · 같은 끼니가 하나뿐이면 가리킬 것이 하나다
    peers = [s for s in stops if s.item.kind == item.kind and (item.kind != "dining" or _meal(s.item) == _meal(item))]
    if len(peers) <= 1:
        return decision
    if target.alias == selected:                                                      # ①
        return decision
    if _PINPOINT.search(message):                                                     # ④
        return decision
    said = _grams(message)
    overlap = {s.alias: len(said & _grams(_name_of(s.item))) for s in stops}
    best = max(overlap.values(), default=0)
    leaders = [s for s in stops if best and overlap[s.alias] == best]                  # 문장과 이름이 **가장 많이** 겹치는 일정 — 「한강」 두 글자만 겹치는 다른 곳에 밀리지 않는다
    if target in leaders and len(leaders) == 1:                                       # ②
        return decision
    talk = " ".join(str(t.get("text") or "") for t in history[-4:])
    if (changes and changes[-1].target_item_id == item.item_id) or (talk and _grams(talk) & _grams(_name_of(item))):   # ③
        return decision
    # ⑥ 날을 말하지 않았을 때의 **약속**(머리말 · `SYSTEM` — 「날을 말하지 않은 첫/두 번째는 1일차」 · 화면에서 고른 일정은 그 날): 화면에서 고른 일정이 있으면 그 날, 없으면 1일차.
    #   그 날에 같은 종류(식당이면 같은 끼니)가 **하나뿐**이고 그것을 골랐으면 약속대로다. 그 밖의 날을 골랐거나 그 날에 여럿이면 짐작이다 → 되묻는다
    first = stops[0].item.starts_at if stops else None
    picked = next((s.item for s in stops if s.alias == selected), None)
    ref_day = _day_no(picked, first) if picked is not None else 1
    on_that_day = [s for s in peers if _day_no(s.item, first) == ref_day]
    if len(on_that_day) == 1 and on_that_day[0] is target:
        return decision
    ordered = sorted(peers, key=lambda s: (abs(_day_no(s.item, first) - ref_day), s.item.starts_at))[:3]
    if target not in ordered:                                                         # 모델이 본 해석도 후보 하나로 남긴다(맞았을 수도 있다)
        ordered = ordered[:2] + [target]
    raw = {**decision.raw, "guard": {"ambiguous_target": target.alias, "asked": [s.alias for s in ordered]}}
    return replace(decision, action="clarify", item=None, change=None, raw=raw,
                   choices=[{"action": decision.action, "item": s.item, "change": None, "fact": "none"} for s in ordered])


_FACT_KO = {"detail": "자세히", "address": "주소", "phone": "전화번호", "hours": "운영시간", "time": "몇 시에 가는지",
            "next": "다음 일정", "booking": "예약", "day": "하루 일정", "move": "가는 길",
            "route_here": "지금 위치에서 가는 길", "nearby_dining": "가까운 식당", "nearby_activity": "가까운 볼거리"}


#: 사실 답 뒤에 「혹시 이런 뜻이었나요?」로 붙이는 가까운 질문 종류 — ★우리가 고른 짝(2026-09-29 사용자 제안). 앞의 것부터
NEAR_FACTS = {"address": ("move", "detail", "hours"), "hours": ("time", "detail", "address"),
              "detail": ("address", "hours", "next"), "phone": ("address", "detail"), "time": ("hours", "detail"),
              "next": ("day", "detail"), "day": ("next", "booking"), "booking": ("detail", "day"),
              "move": ("address", "time")}


def fact_first(decision: Decision) -> Decision:
    """★`[2026-09-29 사용자 지시]` 되묻는 대신 **먼저 답한다** — 모델이 되묻기를 골랐어도 후보가 **모두 사실 질문**이면
    (「2026-09-30 예약 표시를 알려 주세요」가 두 일정 중 어느 것인지 되물었다) 첫 후보로 답하고 나머지는 「혹시 이런 뜻이었나요?」로
    붙인다. 사실 답은 일정을 바꾸지 않아 잘못 짚어도 잃는 것이 없다. **바꾸기 · 되돌리기는 지금처럼 묻는다**(잘못 짚으면 일정이 바뀐다)."""
    choices = decision.choices
    if decision.action != "clarify" or not choices or any(c["action"] != "answer_fact" for c in choices):
        return decision
    first = choices[0]
    return replace(decision, action="answer_fact", item=first["item"], change=None,
                   fact=first["fact"] if first["fact"] != "none" else "detail", choices=choices[1:])


def maybe_meant(decision: Decision, fact: str, first: datetime | None, limit: int = 3) -> list[dict[str, str]]:
    """사실 답에 붙일 「혹시 이런 뜻이었나요?」 — 모델이 낸 다른 해석 먼저, 모자라면 같은 일정의 가까운 질문 종류. 버튼 모양."""
    options = [c for c in decision.choices if c["action"] == "answer_fact"]
    options += [{"action": "answer_fact", "item": decision.item, "change": None, "fact": near}
                for near in NEAR_FACTS.get(fact, ())]
    out: list[dict[str, str]] = []
    for option in options:
        if option["item"] is decision.item and option["fact"] == fact:
            continue
        text = choice_message(option, first)
        if text and all(text != c["message"] for c in out):
            out.append({"label": text, "message": text,
                        **({"item_id": str(option["item"].item_id)} if option.get("item") is not None else {})})
        if len(out) >= limit:
            break
    return out


def choice_message(choice: dict[str, Any], first: datetime | None) -> str | None:
    """해석 후보 → **그대로 보내면 한 번에 알아듣는 문장**(웹 버튼 · 「1번」 답의 뜻). 만들 수 없으면 None."""
    item, change, action = choice.get("item"), choice.get("change"), choice["action"]
    # ★`[2026-10-13]` 고객이 아는 이름은 **일정 제목**이다 — 전에는 연결 장소 이름을 써서 「경복궁 관람」 일정을 「황학정」으로,
    #   「명동교자」를 「성수망치버거」로 되물어 무슨 일정인지 알아보기 어려웠다(x600 서버 웹 채팅 시험 2026-10-13)
    where = (f"{_day_no(item, first)}일차 {_local(item.starts_at):%H:%M} {item.title}") if item is not None else None
    if action == "apply_change" and where:
        return f"{where} 다른 곳으로 바꿔 줘"
    if action == "propose_alternatives" and where:
        return f"{where} 대신 갈 곳 알아봐 줘"
    if action == "rollback" and change is not None:
        return f"「{change.line}」 변경을 되돌려 줘"
    if action == "redo" and change is not None:
        return f"「{change.line}」 변경을 다시 적용해 줘"
    if action == "answer_fact":
        what = _FACT_KO.get(choice.get("fact") or "detail", "자세히")
        return f"{where} {what} 알려 줘" if where else f"{what} 알려 줘"
    if action == "report_closed" and where:
        return f"{where} 문 닫았어요"
    return None


def labels(decision: Decision) -> dict[str, str]:
    """Case 분류 표지 — 할 일과 대상 종류에서 **정해진 규칙으로** 만든다(모델을 또 부르지 않는다).

    요청 종류(intent): 바꾸기 · 알아보기 · 되돌리기 · 다시 적용 → 조정 거부(adjust_reject) / 사실 · 규정 → 확인 요청 /
    늦음 · 휴무 → 사건 신고 / 되묻기 · 그 밖 → other. 사건 코드 접두는 **대상의 실제 종류**(없으면 other — 여행 창구).
    """
    intent = {"apply_change": "adjust_reject", "propose_alternatives": "adjust_reject", "rollback": "adjust_reject",
              "redo": "adjust_reject", "answer_fact": "confirm_request", "ask_policy": "confirm_request",
              "answer_service": "confirm_request",
              "report_delay": "incident_report", "report_closed": "incident_report"}.get(decision.action, "other")
    kind = getattr(decision.item, "kind", None)
    if decision.action == "report_delay":
        code = "dining_hours"                  # 늦음은 다음 식사가 그 시각에 되는지를 본다(식당 팀)
    elif kind == "dining":
        code = "dining_hours" if decision.action in ("report_closed",) or decision.fact == "hours" else "dining_other"
    elif kind == "activity":
        code = "activity_cancel_or_change" if intent == "adjust_reject" else "activity_other"
    elif kind == "mobility":
        code = "mobility_other"
    else:
        code = "other"
    sentiment = "negative" if intent == "incident_report" else "neutral"
    return {"intent": intent, "issue_code": code, "sentiment": sentiment}


def stands_alone(decision: "Decision | None") -> bool:
    """★`[2026-10-13]` 이 해석이 **앞의 되묻기 없이도 처리할 수 있는 완결된 요청**인가 — 할 일에 필요한 값이 다 있나.

    대기 중인 되묻기가 있을 때 새 문장이 「그 질문의 답」인지 「새 요청」인지 가르는 데 쓴다(`case_dispatch.resume_pending`).
    전에는 웹이 대기 건 번호를 붙이면 무엇을 말해도 대기 건의 답으로만 처리해, 새 요청에도 같은 되묻기만 반복됐다
    (x600 서버 웹 채팅 시험 2026-10-13: 20건). 판단은 문장 낱말이 아니라 **모델이 고른 할 일과 서버가 확인한 값**으로 한다."""
    if decision is None:
        return False
    if decision.raw.get("scope_guard") or decision.raw.get("condition_guard") or decision.raw.get("unsupported"):
        return True                                   # 시간·날짜 변경으로 알아들었다 — 그 요청에 대한 안내가 답이다
    action = decision.action
    if action in ("clarify", "other"):
        return False
    if action in ("apply_change", "propose_alternatives", "report_closed"):
        return decision.item is not None
    if action in ("rollback", "redo"):
        return decision.change is not None
    if action == "report_delay":
        return bool(decision.minutes)
    if action == "answer_fact":
        return bool(decision.reads) or decision.fact != "none"
    return action in ("ask_policy", "answer_service")


__all__ = ["ACTIONS", "UNSUPPORTED", "stands_alone", "Change", "Decision", "DecisionFailed", "FACTS", "LOCATION_FACTS", "NEAR_FACTS", "fact_first", "guard_target", "maybe_meant", "Stop", "SYSTEM", "changes_of", "choice_message",
           "decide", "item_lines", "labels", "stops_of"]
