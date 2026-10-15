---
type: guide
title: REST API 계약 안내
description: 분리한 REST 계약으로 이동한다. 기존 절 링크를 보존한다
status: draft
domain: travel
---

# REST API 계약 안내

## 이번 개정의 승인된 기준 — 2026-10-12

고객 웹 앱·개인 AI 연동(API/MCP)·공유 여행계획서 링크의 이용 범위는 [제품 범위](../../../wiki/product/scope.md)를 따릅니다. 항로 지킴이·미응답 기본값·고정 항목·재난 정지의 우선순위는 [현재 설문 결정](../../../wiki/decisions/D-020-trip-survey-and-ask-first.md)에 있습니다. 정책 결정과 코드 구현·배포 상태는 구분합니다. 본문은 아래 기능별 계약의 입구이며 같은 규칙을 중복 소유하지 않습니다.


현재 계약은 [REST API 계약 지도](rest-endpoints/index.md)에서 호출 주체별로 찾습니다. 한 파일에 섞였던 Case·여행·인증·접수·위임 계약을 분리했으며 요청·응답 본문은 보존했습니다.

## 기존 절 연결

<a id="post-v1cases-요청응답-필드"></a> [`POST /v1/cases` 요청·응답 필드](rest-endpoints/cases.md#post-v1cases-요청응답-필드)
<a id="subject_ref--case-가-가리키는-대상-결정-2026-09-17"></a> [`subject_ref` — Case 가 가리키는 대상 `[결정 2026-09-17]`](rest-endpoints/cases.md#subject_ref--case-가-가리키는-대상-결정-2026-09-17)
<a id="get-v1cases-쿼리-계약"></a> [`GET /v1/cases` 쿼리 계약](rest-endpoints/cases.md#get-v1cases-쿼리-계약)
<a id="get-v1casescase_id-응답-계약"></a> [`GET /v1/cases/{case_id}` 응답 계약](rest-endpoints/cases.md#get-v1casescase_id-응답-계약)
<a id="추가-메시지-resume-제약"></a> [추가 메시지 resume 제약](rest-endpoints/cases.md#추가-메시지-resume-제약)
<a id="승인-요청감사-계약"></a> [승인 요청·감사 계약](rest-endpoints/cases.md#승인-요청감사-계약)
<a id="post-v1outboxmessage_idresolve-계약"></a> [`POST /v1/outbox/{message_id}/resolve` 계약](rest-endpoints/cases.md#post-v1outboxmessage_idresolve-계약)
<a id="여행-api--v1trips--plantrip_id"></a> [여행 API — `/v1/trips/*` · `/plan/{trip_id}`](rest-endpoints/trips.md#여행-api--v1trips--plantrip_id)
<a id="post-v1tripsplan--일정을-우리가-만든다-2026-09-22"></a> [★`POST /v1/trips/plan` — 일정을 **우리가 만든다** `[2026-09-22]`](rest-endpoints/trips.md#post-v1tripsplan--일정을-우리가-만든다-2026-09-22)
<a id="get-booking-changebooking_id--업체-건을-넘기는-링크-2026-09-22"></a> [`GET /booking-change/{booking_id}` — 업체 건을 넘기는 링크 `[2026-09-22]`](rest-endpoints/trips.md#get-booking-changebooking_id--업체-건을-넘기는-링크-2026-09-22)
<a id="constraintssurvey--여행-시작-설문-2026-09-24"></a> [`constraints.survey` — 여행 시작 설문 `[2026-09-24]`](rest-endpoints/trips.md#constraintssurvey--여행-시작-설문-2026-09-24)
<a id="routes키optionsuses--이동-수단이-지나는-대상의-표기-결정-2026-09-23"></a> [`routes{<키>}.options[].uses` — 이동 수단이 지나는 대상의 표기 `[결정 2026-09-23]`](rest-endpoints/trips.md#routes키optionsuses--이동-수단이-지나는-대상의-표기-결정-2026-09-23)
<a id="일정-응답의-warnings--살펴볼-점-2026-10-03"></a> [일정 응답의 `warnings[]` — 「살펴볼 점」 `[2026-10-03]`](rest-endpoints/trips.md#일정-응답의-warnings--살펴볼-점-2026-10-03)
<a id="보류-제안--v1tripstrip_idproposals-2026-09-24"></a> [보류 제안 — `/v1/trips/{trip_id}/proposals*` `[2026-09-24]`](rest-endpoints/trips.md#보류-제안--v1tripstrip_idproposals-2026-09-24)
<a id="웹고객-브라우저--v1web-2026-09-24"></a> [웹(고객 브라우저) — `/v1/web/*` `[2026-09-24]`](rest-endpoints/web-access.md#웹고객-브라우저--v1web-2026-09-24)
<a id="남용-방어--횟수-제한--사람-확인-2026-09-28"></a> [남용 방어 — 횟수 제한 · 사람 확인 `[2026-09-28]`](rest-endpoints/web-access.md#남용-방어--횟수-제한--사람-확인-2026-09-28)
<a id="소셜-로그인--v1webauth-2026-10-03-구글-먼저"></a> [소셜 로그인 — `/v1/web/auth/*` `[2026-10-03]` (구글 먼저)](rest-endpoints/web-auth.md#소셜-로그인--v1webauth-2026-10-03-구글-먼저)
<a id="브라우저-세션-쿠키--v1webauthsession-결정-2026-10-04-사용자"></a> [브라우저 세션 쿠키 — `/v1/web/auth/session*` `[결정 2026-10-04 사용자]`](rest-endpoints/web-auth.md#브라우저-세션-쿠키--v1webauthsession-결정-2026-10-04-사용자)
<a id="게스트로그인-안-한-사용자--여행-삭제--게스트-정리-결정-2026-10-04-사용자"></a> [게스트(로그인 안 한 사용자) · 여행 삭제 · 게스트 정리 `[결정 2026-10-04 사용자]`](rest-endpoints/web-auth.md#게스트로그인-안-한-사용자--여행-삭제--게스트-정리-결정-2026-10-04-사용자)
<a id="에이전트-키--v1webagent-keys-결정-2026-10-04-사용자--d-cs-012"></a> [에이전트 키 — `/v1/web/agent-keys*` `[결정 2026-10-04 사용자 — D-CS-012]`](rest-endpoints/web-auth.md#에이전트-키--v1webagent-keys-결정-2026-10-04-사용자--d-cs-012)
<a id="약관-동의-기록--사용-조건--v1webconsents-2026-10-05-사용자-지시--ui-세션-요청서"></a> [약관 동의 기록 · 사용 조건 — `/v1/web/consents` `[2026-10-05 사용자 지시 — ui 세션 요청서]`](rest-endpoints/web-auth.md#약관-동의-기록--사용-조건--v1webconsents-2026-10-05-사용자-지시--ui-세션-요청서)
<a id="약관의-보관-기간--파기--v1weblegalretention--adminretention-2026-10-07-사용자-결정--uiux-전달"></a> [약관의 보관 기간 · 파기 — `/v1/web/legal/retention` · `/admin/retention` `[2026-10-07 사용자 결정 — uiux 전달]`](rest-endpoints/web-auth.md#약관의-보관-기간--파기--v1weblegalretention--adminretention-2026-10-07-사용자-결정--uiux-전달)
<a id="계획-읽기--v1webtrip-intakes-2026-09-27"></a> [계획 읽기 — `/v1/web/trip-intakes` `[2026-09-27]`](rest-endpoints/web-intakes.md#계획-읽기--v1webtrip-intakes-2026-09-27)
<a id="등록한-여행-고치기수정-모드"></a> [등록한 여행 고치기(수정 모드) — `POST /v1/web/trips/{trip_id}/reopen` · 수정 접수의 `/confirm` · `/cancel` `[2026-10-13]`](rest-endpoints/web-intakes.md#등록한-여행-고치기수정-모드--post-v1webtripstrip_idreopen--수정-접수의-confirm-2026-10-13-사용자-결정)
<a id="설문-질문--questions--post-v1webtrip-intakesintake_idsurvey-2026-10-06-사용자-지시--uiux-인계"></a> [설문 질문 — `questions[]` · `POST /v1/web/trip-intakes/{intake_id}/survey` `[2026-10-06 사용자 지시 · uiux 인계]`](rest-endpoints/web-intakes.md#설문-질문--questions--post-v1webtrip-intakesintake_idsurvey-2026-10-06-사용자-지시--uiux-인계)
<a id="항로-지킴이--post-v1webtripstrip_idguardian--여행-조회의-guardian--알림-결정-2026-10-06-사용자"></a> [항로 지킴이 — `POST /v1/web/trips/{trip_id}/guardian` · 여행 조회의 `guardian` · 알림 `[결정 2026-10-06 사용자]`](rest-endpoints/web-trips.md#항로-지킴이--post-v1webtripstrip_idguardian--여행-조회의-guardian--알림-결정-2026-10-06-사용자)
<a id="일정-위험-점검--이동-판정--get-v1webtripstrip_idrisks--post-v1webmovesjudge-2026-10-06-사용자-요청--mcp-읽기-도구"></a> [일정 위험 점검 · 이동 판정 — `GET /v1/web/trips/{trip_id}/risks` · `POST /v1/web/moves/judge` `[2026-10-06 사용자 요청 — MCP 읽기 도구]`](rest-endpoints/web-trips.md#일정-위험-점검--이동-판정--get-v1webtripstrip_idrisks--post-v1webmovesjudge-2026-10-06-사용자-요청--mcp-읽기-도구)
<a id="재난-시-일정-정지--post-v1webtripstrip_idsafetyresume--여행-조회의-safety--안전-알림-결정-2026-10-06-사용자"></a> [재난 시 일정 정지 — `POST /v1/web/trips/{trip_id}/safety/resume` · 여행 조회의 `safety` · 안전 알림 `[결정 2026-10-06 사용자]`](rest-endpoints/web-trips.md#재난-시-일정-정지--post-v1webtripstrip_idsafetyresume--여행-조회의-safety--안전-알림-결정-2026-10-06-사용자)
<a id="지도-경로선--v1webtripstrip_idroute-shapes--v1webtrip-intakesintake_idroute-shapes-2026-10-04"></a> [지도 경로선 — `/v1/web/trips/{trip_id}/route-shapes` · `/v1/web/trip-intakes/{intake_id}/route-shapes` `[2026-10-04]`](rest-endpoints/web-trips.md#지도-경로선--v1webtripstrip_idroute-shapes--v1webtrip-intakesintake_idroute-shapes-2026-10-04)
<a id="여행-화면-지금-시각-표시--여행-조회의-now_label-2026-10-15-사용자-결정"></a> [여행 화면 「지금」 시각 표시 — 여행 조회의 `now_label` `[2026-10-15 사용자 결정]`](rest-endpoints/web-trips.md#여행-화면-지금-시각-표시--여행-조회의-now_label-2026-10-15-사용자-결정)
<a id="위임--v1delegations-2026-09-22"></a> [위임 — `/v1/delegations/*` `[2026-09-22]`](rest-endpoints/delegations.md#위임--v1delegations-2026-09-22)

## 관계

- [외부 연동 안내](index.md) — API·MCP 문서 입구
- [REST 계약 지도](rest-endpoints/index.md) — 상세 계약과 읽기 순서
