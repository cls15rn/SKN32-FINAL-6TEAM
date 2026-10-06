# 이동 모듈 회귀 — 무엇을 넣고, 무엇을 기대하고, 무엇을 비교하나

이 폴더(`tests/unit/travel/mobility/`)의 회귀 시험 설명이다. 케이스 파일 `*_legs_v1.json` 과 `test_regression_cases.py` 를 읽는 법 · 깨졌을 때 읽는 순서.

> **지금 숫자(2026-10-05 · 마무리 1(105) · `plan-v2.8` · 클라우드 확인 — 노트북 전체 판 값은 닫힘 문서)**: 팀원 게이트 **591**(자료 기기 · 저장소 자료만이면 590 + 1 skip) · 전체층 **290** · 데이터 없이 돌리면 **520 + 71 skip** · 회귀 케이스 **205**(199 + `R-L9X-01~04` · `MULTI-XNAME-01·02`). 더해진 것: `test_close_105_v1.py`(게이트 24 + 전체층 4) — ㉠ 환승 거리표의 이름이 판정기 이름과 다른 6쌍을 자료 `transfer_name_map_v1.json` 으로 맞춘다(총신대입구 ↔ 이수 · 서울역 ↔ GTX-A 「서울」 환승 이음 · 수서 「국철」 · 석계 「경원선」) ㉡ 9호선 급행은 자료 `express_marks_v1.json` 이 표시한 편을 급행 정차역끼리만 쓴다(통과역 승차·하차에 안 쓴다 · 표시가 이 시간표의 것이 아니면 모른다) ㉢ 최단 후보가 소요 없는 간선(GTX-A 서울–수서처럼 구조만 있는 이음)을 탔으면 그 간선을 안 타는 최단도 같이 낸다. 바뀐 기대: `MIX-AB-03`(혼합 후보 1개 이상 → 0개 — 지하철만 후보가 환승 1회 20분이 돼 혼합이 앞서는 축이 없다) · `test_fare_ub_join_same_name_only`(요금 상한 그래프의 환승 쌍 = 판정기 이름으로 맞춘 거리표 쌍). 아래는 앞 판 기록 → **(합치기 3(103) · `plan-v2.7` 그대로 · 클라우드 확인)**: 팀원 게이트 **567**(자료 기기 · 저장소 자료만이면 566 + 1 skip) · 전체층 **280**(그대로) · 데이터 없이 돌리면 **496 + 71 skip** · 회귀 케이스 **199**(그대로). 더해진 것: 팀장 `role-manager` `f142e0f` 의 이동 자리 변경(따릉이 실시간 조회의 호출 한도 문 · 경로선 점 줄이기)을 받음 — `test_route_shape.py`(팀장 판 그대로 · +3) · `test_merge_103_v1.py`(17 — 한도에 걸리면 부르지 않고 「모름」 · 문이 없으면 예전대로 · 서버 배선 · 합치며 살아 있어야 하는 우리 것). 팀장 시험 `test_bike_live_gate.py` 는 코어 호출 한도 묶음(`infrastructure/travel/source_budget.py`)을 직접 써서 그 묶음이 develop 에 들어올 때 같이 받는다. 아래는 앞 판 기록 → **(합치기 2(102) · 클라우드 확인 — 노트북 전체 판 값은 닫힘 문서)**: 회귀 케이스 **199**(그대로) · 팀원 게이트 `pytest tests/unit/travel/mobility -q` **549**(자료 기기 · 저장소 자료만이면 548 + 1 skip) · 전체층 **280** · 데이터 없이 돌리면 **478 + 71 skip**. 더해진 것: `test_merge_102_v1.py`(게이트 28 + 전체층 3 — 걷기 길 기준의 되돌림 까닭·우회 경고·걷기 근거 봉투 · 환승·혼합 후보 걷기 · 요금 거리 원천 순서). ★**회귀 199건의 기대는 「길찾기 끔」 값이다**(본인 10/5) — 판정기의 정류장↔역·정류장↔정류장 환승 걸음이 길찾기를 켜면 길 기준이 되어 MIX-02 · BB-02 · J-WORST-02·03 네 건(7줄)이 달라진다. 그래서 명령줄 회귀는 `--road-graph none` 으로 돌리고(택시 대안 묶음 `alt` 만 켬 · `car` 는 합성 픽스처), 켠 값은 `test_merge_102_v1.py` 와 서버 경로 확인으로 본다. 아래는 앞 판 기록 →
> **(101 숫자 · 2026-10-05 · 합치기 1(101) · develop `2f3594a` 병합 + 팀장 이동 작업(`role-manager` `93a320a`) 받은 뒤 · 클라우드 확인 — 노트북 전체 판 값은 닫힘 문서)**: 회귀 케이스 **199** · 팀원 게이트 `pytest tests/unit/travel/mobility -q` **518**(자료 기기 · 저장소 자료만이면 517 + 1 skip) · 전체층 `-m "mobility_full and not live"` **277** · 데이터 없이 돌리면 **447 + 71 skip**. 10/3 뒤에 더해진 것: 택시로 메우기 `test_taxi_fallback_v1.py` · 고르는 기준·버스 환승 후보(`test_by_mode_v1.py` 등) · **팀장 길찾기 `test_graph_router.py`(21) · 접근 걷기 `test_plan_access_walk.py`(9) · 택시 후보 `test_plan_taxi.py`(7) · 경로선 `test_route_shape.py`(14) · 요금 추정 `test_fare_est_v1.py`(8)** · 합치기에서 더한 것 `test_merge_101_v1.py`(게이트 6 + 전체층 1) · 택시 서비스 `test_taxi_service_v1.py`(옛 `test_road_router_v1.py` 에서 옮김 — 우리 라우터를 내리고 팀장 길찾기 위로). **길찾기(걷기 길 기준·택시·자전거)는 pytest 기본에서 꺼져 있다**(`build_verifier(local_router=False)`) — 켠 판은 그 시험들이 `local_router=True` 로 직접 세운다. 아래는 10/3 판 기록 →
> **(10/3 숫자 · 묶음 PR 직전 전체 판 · develop `2449fa9` 병합 뒤)**: 회귀 케이스 **199** · 팀원 게이트 `pytest tests/unit/travel/mobility -q` **430** · 전체층 `-m "mobility_full and not live"` **265** · 데이터 없이 돌리면 **363 + 67 skip**. 10/1(회귀 186 · 게이트 287 · 전체층 214) 뒤에 더해진 것: 사고 출처·버스 정류장 무정차 `test_bus_stop_skip_v1.py`(게이트 59 + 전체층 18) · 역 순서 표 `test_line_order_v1.py` + 회귀 `real_legs_v1.json` R-BRANCH-01~05 · 급행 무정차역 `test_express_stop_v1.py`(게이트 16) + 회귀 R-EXPRESS-01~08 · 수단별 후보(환승 2회까지) `test_by_mode_v1.py` · 서버 없는 택시 소요 `test_road_router_v1.py` · 03:59 다음 운행일 첫차 `test_0359_next_day_first_v1.py`. 그 앞(85~87): 사고 대안 역 `test_station_fallback_v1.py` · 가장 이른 도착 `test_earliest_v1.py` · 시간표 오래됨 기준 `test_review_fixes_runtime.py` · 지하철+버스 혼합 후보 `test_mixed_v1.py` + 회귀 `multi_legs_v1.json` MIX-AB-01~09(`multi.mixed: true` · 기대 칸 `expect_mixed_min`·`expect_mixed_max`·`expect_dropped_why`). 아래 본문은 9/28 판(회귀 171) 기준으로 적었고 구조는 같다 — 늘어난 28건은 같은 묶음에 더해진 케이스다.

(원래 제목: 이동 모듈 회귀 171 — 무엇을 넣고, 무엇을 기대하고, 무엇을 비교하나 (v1 · 2026-09-28 · 71번 방 · §8 은 9/29))

작성 서유현 · 대상 = 나 자신(먼저) → 팀원(merge 때 이 회귀를 돌리는 사람). 판정기 코드는 이 문서로 한 줄도 안 바뀐다.

> **한 줄**: 회귀 171 은 「**이 입력을 넣으면 판정기는 반드시 이 값을 낸다**」를 171번 적어 둔 표다.
> 케이스 파일(JSON) 한 건 = **입력 칸**(날짜·출발 시각·구간) + **기대 칸**(`expect…` 로 시작하는 칸들).
> `--check-expect` 는 판정기가 낸 결과(`CaseResult`)에서 **기대 칸과 짝이 되는 칸**을 꺼내 하나씩 비교하고, 하나라도 다르면 `MISS` 로 울리고 종료코드 1 을 낸다.
> 그러니 회귀가 깨졌다 = **판정기 출력이 그때 잠가 둔 값에서 움직였다**는 뜻이지, 판정기가 틀렸다는 뜻이 아니다. 어느 쪽이 맞는지는 사람이 본다(§7).

---

## 1. 흐름 한 장

```
케이스 파일 (tests/unit/travel/mobility/<묶음>_legs_v1.json)
   └ cases[] — 한 건마다 { id, note, 입력 칸…, expect 칸… }
                    │
                    ▼  verify_time.main() 이 데이터(DATA_DIR/travel/processed/mobility/…)를 올리고
              Verifier.verify_case(case)  ──►  CaseResult (verdict · out{verdict, code, slack_min, margin_min, …}
                    │                                       · arrive_min · alternatives[] · warnings[] · candidates[] · taxi · relief · grade …)
                    ▼
              --check-expect : 케이스의 expect 칸 ↔ CaseResult 의 짝 칸 비교 (§3 표)
                    │
                    ▼
              "기대 대조 — 케이스 N건 중 어긋남 0건"  → 종료코드 0   (어긋남 ≥1 → MISS 목록 + 종료코드 1)
```

14 묶음을 한 번씩 돌려 어긋남이 전부 0 이면 **회귀 171 어긋남 0**. 담당자 기기에서는 저장소 밖 PowerShell 스크립트가 14번 부른다. **9/29 부터는 pytest 도 같은 파일·같은 대조 함수로 돈다**(`test_regression_cases.py`).

---

## 2. 케이스 한 건의 칸 — 입력

| 칸 | 뜻 | 예 |
|---|---|---|
| `id` | 케이스 이름. MISS 목록에 이 이름이 찍힌다 | `LAST-01` · `R-LOOP-01` |
| `note` | **이 케이스가 왜 있는지** 한 문장. 깨졌을 때 가장 먼저 읽는다 | 「24 시 넘김 — 24:30 출발이 막차 안이다」 |
| `date` | **운행일**(달력 날짜가 아님). 요일형(평일/토/휴일)이 여기서 정해진다. 04:00 전 시각은 이 운행일의 연장 | `2026-09-11`(금) |
| `depart_at` | 출발 시각. `24:30`·`25:05` 처럼 24 를 넘는 표기가 정상 | `24:30` |
| `arrive_by` | (선택) 도착 목표. 있으면 여유(`slack_min`)를 계산한다 | `09:00` |
| `legs[]` | 구간 열. 한 구간 = 지하철 `{line, from, to}` / 버스 `{mode:"bus", route, from, to}` / 택시·자동차 `{mode:"taxi"|"car", from, to}`(역명 또는 `"lat,lng"`) / 자전거 `{mode:"bike", from, to}` | `[{"line":"02호선","from":"강변","to":"잠실"}]` |
| `multi` | `legs` 대신 `{from, to}` 만 주면 판정기가 **후보를 스스로 만들어**(최단·최소환승·최소도보·버스 직행) 후보마다 판정한다 | `{"from":"사당","to":"왕십리"}` |
| `party` | 동행 조건(연령·인원·환승 상한 등). 상한을 넘으면 「성립하지만 탈락」 | |
| `disruptions[]` | 이슈(무정차·간선 중단 등). 코어 `current_state.replan` 이 보내는 모양을 흉내 낸다 | `{"kind":"edge_closed","line":"02호선","between":["성수","건대입구"]}` |
| `bike_live` | 자전거 실시간 거치 수 **픽스처**(실측 아님). 실제 호출은 회귀에서 안 한다 | `{"counts":{"ST-840":5}}` |
| `selfcheck` | `false` 면 자기점검 탐침 씨앗에서 뺀다(회귀 대조와는 무관) | |
| `no_alternatives` · `first_visit` | 대안 열거 끄기 · 첫 방문 여부(후보 생성 규칙) | |

## 2-2. 케이스 한 건의 칸 — 기대 (`expect…`)

기대 칸은 「**있어야 한다**」와 「**없어야 한다**」 두 종류다. 2026-09-14 까지는 「있어야 한다」만 있어서 투어버스가 대안에 끼어들어도 통과했다 — 그래서 `expect_alt_max`·`expect_warn_codes_absent` 같은 부정 축이 생겼다.

| 기대 칸 | 뜻 | 값 모양 |
|---|---|---|
| **`expect`** | **밖 판정** — 코어로 나가는 둘 중 하나 | `"feasible"`(성립) / `"infeasible"`(불가) |
| `expect_internal` | 내부 판정 넷(등급·접기용). 밖 판정이 「불가」여도 안에서는 「근거없음」인지 「탈락」인지 가른다 | `feasible` / `infeasible` / `unknown` / `rejected_by_limit` |
| `expect_reason` | 불가의 **이유 코드**(규칙 `judgment.reason_codes` 10종 — 문장은 바뀌어도 코드는 안 바뀐다) | `after_last` · `before_first` · `disruption` · `no_data` · `service_gap` · `no_service` · `transfer_walk` · `arrive_late` · `over_limit` · `mode_unavailable` |
| `expect_arrive` | 예정 도착 시각(best) | `"14:11"` |
| `expect_margin_min` | @(여유 폭 · 최악−예정 + 정책 버퍼) 의 [하한, 상한] | `[10, 10]` |
| `expect_slack_min` | 도착 목표 − 예정 도착 − @ 의 [하한, 상한] (`arrive_by` 있을 때) | `[0, 5]` |
| `expect_p90_eta` | 버스 p90 소요의 [하한, 상한]. **`null` 이면 「없어야 한다」** | `[71, 71]` / `null` |
| `expect_last_depart` / `expect_last_depart_none` | 「늦어도 이때는 출발」(worst 판정기로 역산) / 그 값이 **없어야 한다** | `"24:46"` / `true` |
| `expect_alt_min` · `expect_alt_max` · `…_by_mode` | 대안 개수 하한·상한(전체 / 수단별) | `1` · `0` · `{"bus":1,"bike":1}` |
| `expect_alt_axis` · `expect_alt_arrive` | 대안 중 이 축(`수단교체`·`노선교체`…)이 있어야 / 이 도착 시각이 있어야 | `"수단교체"` · `"25:38"` |
| `expect_taxi` | 택시 대안의 판정·도착·요금·등급·경고. 99(10/4)부터 저장소 안 차도 그래프(파이썬 라우터)로 **어느 기기에서나 대조한다** — 「라우터 없음 SKIP」 은 없어졌다 | `{"verdict":"feasible","arrive":"25:05","fare_won":12300}` |
| `expect_taxi_leg` | 택시·자동차 **구간**의 요금·심야·커버율 | |
| `expect_warn_codes` / `expect_warn_codes_absent`(=`expect_warn_absent`) | 이 경고 **코드**가 있어야 / 없어야 (케이스 경고 ∪ 대안 경고) | `["MOB_W_DEST_INFERRED"]` |
| `expect_relief_contains` · `expect_reason_contains` | 완화 문장 · 사유 문장에 이 글자가 있어야 | `"더 일찍"` · `"최악값"` |
| `expect_grade` | 케이스 등급(확정/추정/근거없음) | `"근거없음"` |
| `expect_candidates_min` · `expect_criteria` · `expect_candidate_legs` · `expect_candidate_arrive` · `expect_tie` · `expect_tie_axes` · `expect_feasible_max` · `expect_no_line_feasible` | `multi` 후보용 — 후보 개수·기준 이름·구간열·도착·동급 여부·성립 상한·이 노선은 성립하면 안 됨 | |

---

## 3. `--check-expect` 가 비교하는 칸 (기대 칸 → 실제 칸)

`verify_time.py` 의 `check_expect(c, r)` 함수가 전부다(9/29 에 `main()` 의 `--check-expect` 블록을 그대로 함수로 뺌 · CLI 와 pytest 가 같이 쓴다). 판정 경로와는 분리돼 있다.

| 기대 칸 | 실제(어디서 꺼내나) | 비교 |
|---|---|---|
| `expect` | `r.out["verdict"]` | 같아야. 어휘 밖 값(`unknown` 등)은 그 자체로 MISS |
| `expect_internal` | `r.verdict` | 같아야 |
| `expect_reason` | `r.out["code"]` | 같아야 |
| `expect_slack_min` · `expect_margin_min` | `r.out["slack_min"]` · `r.out["margin_min"]` | 범위 안 · None 이면 MISS |
| `expect_p90_eta` | `r.out["p90_eta_min"]` | 범위 안 · 기대 `null` 이면 실제도 None 이어야 |
| `expect_last_depart(_none)` | `r.out["last_feasible_depart_min"]` | 분→`HH:MM` 문자열 같아야 / None 이어야 |
| `expect_arrive` | `r.arrive_min` | 분 단위 정수 같아야(1분도 다르면 MISS) |
| `expect_alt_*` | `r.alternatives[]` (`label`·`mode`·`axis`·`arrive_min`) | 개수·축·도착 |
| `expect_taxi` | `r.taxi` | 도로 그래프 없이(`--road-graph none`) 돌리면 MISS 다(99 — SKIP 없음) |
| `expect_taxi_leg` | `r.legs[].car` | 요금·심야·커버율 |
| `expect_warn_codes(_absent)` | `r.warnings[].code` ∪ `r.alternatives[].warnings[].code` | 있어야 / 없어야 |
| `expect_relief_contains` · `expect_reason_contains` | `r.relief` · `r.reason` + 각 구간 `reason` | 부분 문자열 |
| `expect_grade` | `r.grade` | 같아야 |
| `expect_candidates_*` · `expect_criteria` · `expect_tie*` · `expect_feasible_max` · `expect_no_line_feasible` | `r.candidates[]` · `r.ties[]` | 개수·이름·구간열·도착·동급 |

**요약 줄** `기대 대조 — 케이스 N건 중 어긋남 M건 [· 라우터 없음 SKIP k건 (…)]` — M 이 0 이어야 초록. k 는 라우터 없는 기기에서 alt 묶음 4건이 항상 찍힌다(정상 · 노트북·클라우드 공통).

---

## 4. 14 묶음 — 무엇을 넣고 무엇을 잠그나

건수 = 171. 「데이터」열: **합성** = 저장소 안 파일만 / **실** = `DATA_DIR` 의 실데이터가 있어야(없으면 pytest 에서 skip). 「옵션」 = CLI 로 돌릴 때 붙는 인자(pytest 판은 같은 것을 함수 인자로 준다).

| # | 묶음(파일) | 건 | 데이터 | 옵션 | 무엇을 잠그나 | 대표 케이스 → **깨지면 무엇이 잘못된 것** |
|---|---|---|---|---|---|---|
| 1 | `synthetic_legs_v1` | 28 | **합성** `mini_timetable_v2.jsonl`(20 MB · git 안) + 역 순서·환승표(§5) | `--timetable mini_timetable_v2.jsonl` | 지하철 판정의 **기본 문법** — 첫차·막차·24시 넘김·짧은 구간·종착·행선지 없음·방향·순환·시발역·환승·동행 상한·토/일/공휴일·공백·역 없음 | `LAST-01` 24:30 강변→잠실 성립 · 늦어도 24:46 → **24시 넘김(분 단위 시각) 처리나 막차 선택이 깨졌다** |
| 2 | `real_legs_v1` | 32 | 실 시간표 | — | 1 과 같은 축을 **실제 시간표**로. 도착 시각 22건을 분까지 잠근다(2026-09-10 순환선 73분 버그가 판정은 「성립」이었고 도착만 틀렸다) | `R-LOOP-01` 성수→잠실 14:00 → 14:11 · 시발역 경고 → **순환선 시발역(592편 전부 「성수행」)을 종착 필터가 통째로 죽였다** |
| 3 | `issue_legs_v1` | 9 | 실 | — | 이슈(`disruptions`) — 무정차·간선 중단·환승역 무정차 · 대안 상한(`expect_alt_max`) | `ISSUE-02/03/03B` 같은 구간·시각 — 무정차=성립 · 한쪽 끊김=반대로 돌아 성립 · **양쪽** 끊김=불가 → **셋이 갈리지 않으면 이슈 모델(`rules.disruption`)이 무너졌다** |
| 4 | `alt_legs_v1` | 5 | 실 + 도로 그래프 `road_graph_v2`(git 안 · 팀장 파이썬 길찾기 · 101) | (기본 `--road-graph auto`) | 불가일 때 **대안 열거** — 규칙 순서로 후보를 만들고 **같은 판정기에 재통과** · 접근·이탈 도보를 시각에 넣는다 · 택시 요금(라우터) | `ALT-01` 24:55 잠실→성수 막차 이후 → 심야버스 대안 25:38 · 택시 25:05/12,300원 → **대안이 도보 157 m+209 m 를 시각에서 빼먹었거나 재판정을 안 한다** |
| 5 | `bus_legs_v1` | 11 | 실 (버스 717노선) | — | 버스 첫차·막차·배차·승차 소요 · 버스 불가 시 **수단교체(지하철)** | `BUS-03` 2016 22:40(막차 22:20) → 불가 `after_last` · 2호선 대안 22:47 → **버스 막차 판정 또는 버스→지하철 수단교체 축** |
| 6 | `mixed_legs_v1` | 6 | 실 | — | 지하철↔버스 **혼합 환승 도보**(정류장↔가장 가까운 출구 직선×우회계수) · 상한 ±20 m 근거없음 · 좌표 없음 | `MIX-01` 2016 하차→성수역 122 m → 11:31 → **환승 도보가 0분으로 돌아갔다(9/16 결함 재발)** |
| 7 | `multi_legs_v1` | 6 | 실 | — | **후보 생성기**(최단·최소환승·최소도보 + 버스 직행) · 동급(tie) · 노선별 불가 | `MULTI-01` 사당→왕십리 후보 ≥2 · 최단=4호선→이촌→경의선 · 최소환승=2호선 직행 → **후보 생성기(`candidates.py`)가 기준을 못 가른다** |
| 8 | `car_legs_v1` | 12 | 실 그래프(`graph/`) + **합성 경로 픽스처**(git 안 · 실제 경로 계산 결과 아님) | `--road-graph fixture:car_routes_fixture_v1.json` | 택시·자동차 소요·요금(병산·심야 할증·하한) · 도로급 커버율 → 등급·경고 · 라우터 다운 | `CAR-06` 골목 100% → 성립이지만 등급 **근거없음** + `MOB_W_CAR_SPEED_DEFAULT` · CLASS 경고는 없어야 → **커버율→등급 규칙이 깨졌거나 경고가 섞인다** |
| 9 | `bike_legs_v1` | 14 | 실 대여소 + 자전거 경로 요약 픽스처(`bike_gh_fixture_v1` · 거리·시간만 · 101 에서 되살림) | `--bike-fixture bike_gh_fixture_v1.json` | 따릉이 — 반경 안 대여소·LCD 제외·연령·실시간 거치(픽스처) · 승차 소요 없음 = 불가(no_data) + 이유 · 도착 없는 자전거는 **성립 대안에 안 선다** | `BIKE-01` 24:55 잠실→성수 → 대안은 N73 뿐 · 따릉이는 열거 기록에 근거없음 → **도착 없는 자전거가 성립 대안에 섞였다** · `BIKE-09` 대여소 없음 = 불가(확정) → **대여소 반경 규칙** |
| 10 | `judgment_legs_v1` | 17 | 실 | — | **@ 판정 계약 v0.8** — best·worst 이중 계산 · 판정은 worst · 밖 판정 둘 + 이유 코드 · @·여유 · 늦어도 출발 · 혼잡 @ | `J-WORST-02` N73→2호선 24:09: best 는 막차를 타지만 worst(대기 35분)면 놓친다 → 불가 + `MOB_W_BEST_OK_WORST_FAIL` · 늦어도 24:02 → **판정이 worst 가 아니라 best 로 나가고 있다** |
| 11 | `night_legs_v1` | 18 | 실 (버스) | — | 심야 N노선 — 자정 정규화(N26 첫차 24:00) · 심야A21 막차 익일 · `service_days` · 운행일 경계 04:00 | `NIGHT-01` N26 00:30(=24:30) 성립 → **자정 정규화가 풀려 첫차 00:00·막차 03:25 로 읽힌다** |
| 12 | `bus_profile_legs_v1` | 8 | 실 + `bus_seg_profile_v1.jsonl.gz` | — | 버스 **구간 통행시간 프로파일(v0.9)** — 진입 시각대 누적 · p90 · 대체 구간 · 승차 ≤ 막차 통과 상한 | `BP-01` 472 17:40 → 18:34 · @31 · p90 71 → **18시를 넘긴 뒤 구간이 17시 칸을 쓰거나 p90 누적이 틀렸다** |
| 13 | `bus_noprof_legs_v1` | 2 | 실 | `--bus-profile none` | 프로파일을 **끈** 판정기(표정속도 모델)도 기점 승차 막차 상한을 지킨다 · p90 은 없어야(`null`) | `NP-01` 2016 기점 22:15 → 승차 22:20(막차 상한) → 22:25 · p90 None → **프로파일 없는 경로가 상한 없이 22:26 승차로 돌아갔다** |
| 14 | `destfill_legs_v1` | 3 | 실 **채운 시간표**(`timetable_v1_meta.json` `dest_fill`) | — | 행선지 빈칸 채우기(28) + `passes()` 목적지까지 구간만 등급 · `MOB_W_DEST_INFERRED` | `D28-01` 사당→남태령 12:00 → 오이도행 12:02 → 12:05 + 경고 → **원본(안 채운) 시간표거나 `passes()` 가 목적지 너머 구간까지 본다** |

합계 28+32+9+5+11+6+6+12+14+17+18+8+2+3 = **171**.

각 묶음 파일 머리의 `note`·`주의` 에 그 묶음이 생긴 날과 「수정 전 판정기에서 MISS 가 나는 것을 먼저 봤다」가 적혀 있다 — 케이스는 **실패하는 장면을 한 번 본 뒤** 잠근 것이다(12번 규칙 7).

---

## 5. 합성 vs 실데이터 — 그리고 오늘 확인한 것 하나

- **합성**(`synthetic`): 축소 시간표 `mini_timetable_v2.jsonl`(20 MB · 저장소 안)로 돈다. 기대값은 이 축소 판 기준이라 **실제 시간표로 돌리면 맞지 않는다**(파일 note).
- **실데이터**(나머지 13 묶음): 판정기 입력 파일(시간표 `timetable_v1.jsonl.gz` 등)이 있어야 한다. 9/29 부터 저장소 `datasets/mobility/processed/mobility/` 에 있어 pull 만 하면 된다(`.env` `DATA_DIR` 이 있으면 그쪽이 이김 · `datasets/mobility/DATA_IN_GIT.md`). 없으면 판정기가 `RuntimeError: 판정기 입력이 없다` 로 멈춘다 — 코드가 깨진 게 아니라 데이터가 없는 것.
- 픽스처 2개(`car_routes_fixture_v1` · `mini_timetable_v2`)는 저장소 안이다. 자동차 픽스처는 손으로 이은 합성 경로다(실제 경로 계산 결과 아님). 자전거 경로 요약 픽스처 둘(`bike_gh_fixture_v1` · `plan_bike_gh_fixture_v1` — 9월에 기록한 거리·시간 요약 · 형상 없음)은 99(10/4)에서 지웠다가 101(10/5 · 자전거 승차 소요를 다시 낸다)에서 되살렸다.

**★ 확인(2026-09-28 · 클라우드에서 `DATA_DIR` 를 빈 폴더로 놓고 재현)**: 합성 묶음은 시간표만 바꿔 끼우고 **역 순서표(`line_station_order_v1.json` 780 KB)와 환승 거리표(`transfer_walk_v1.json` 59 KB)는 여전히 `DATA_DIR` 에서 읽는다**.
- 역 순서표가 없으면 `FileNotFoundError` 로 죽는다(28건 전부).
- 환승표만 없으면 폐기된 목록값(2분)으로 대체돼 `TRANS-01`·`LIMIT-02` 의 「늦어도 출발」이 10분 어긋난다(어긋남 2).
- 둘이 있으면 28건 어긋남 0.

즉 27 첫 메시지의 「합성 = 데이터 없이 돎」은 **지금은 반만 맞다**. 팀원 게이트(데이터 없는 CI)에서 합성 28건을 돌리려면 이 두 파일(840 KB · 스냅샷)을 `tests/unit/travel/mobility/fixtures/` 에 두어야 한다 — 데이터 배포 방식과 같이 정한다(§8 · 보류).

---

## 6. pytest 133 과의 관계 — 단위 vs 회귀

| | pytest 133 (`tests/unit/travel/mobility/test_*.py`) | 회귀 171 (`*_legs_v1.json` + `--check-expect`) |
|---|---|---|
| 무엇을 본다 | **함수 하나·규칙 하나**를 합성 값이나 골든으로(예: `_shift_out` 이 p90 을 도보만큼 미는가 · `plan()` 골든 · 버스 프로파일 단위 14 · 동시성 8) | **판정기 전체**가 입력 한 건에 대해 내는 **값**(판정·이유·시각·@·대안·경고)이 잠근 값과 같은가 |
| 데이터 | 73 은 데이터 없이 · 60 은 `DATA_DIR` 없으면 skip | 합성 28 외 143 은 실데이터 필수 |
| 누가 돈다 | 팀 CI(`pytest tests/unit`) · 팀원 merge 확인 | **지금은 우리 PowerShell 만** — CI·팀원은 안 돈다 ← **71 이 고치는 것** |
| 깨지면 | 그 함수·규칙이 바뀐 것 | 판정기 어딘가의 **출력이 움직인 것** — 어느 함수인지는 케이스 `note` 와 MISS 칸으로 좁힌다 |

둘은 겹치지 않는다. 단위가 초록이어도 회귀가 빨강일 수 있다(예: 9/16 혼합 환승 도보 0분 — 함수는 다 돌았고 값만 틀렸다).

---

## 7. MISS 가 나면 — 읽는 순서

1. 요약 줄의 `MISS <id>: 기대 X → Y` — **어느 칸**이 움직였나(판정? 도착 1분? 경고?).
2. 그 케이스의 `note` — 이 케이스가 **왜** 있는지. 여기에 「…면 이 케이스가 MISS 로 운다」가 적힌 것이 많다(예: `BUS-11` 노선별 실측값이 들어오면 운다 · `NIGHT-09` 승차 ≤ 막차를 고치면 운다).
3. 셋 중 하나다:
   - **데이터가 바뀌었다** — 시간표를 다시 받았거나 채움 판이 아니다(`real`·`destfill` 의 도착 시각은 그 판 기준). → 값을 다시 확인해 기대값을 갱신하고 그 이유를 `note` 에 적는다.
   - **판정기가 의도대로 바뀌었다** — 규칙 버전을 올린 수정(v0.9 → v0.9.1 처럼). → 기대값 갱신 + 규칙 `changelog` + 인계 문서.
   - **판정기가 잘못 바뀌었다(진짜 회귀)** — 위 둘이 아니면 이것. 케이스 `note` 가 가리키는 함수부터 본다.
4. `--case <id> --verbose` 로 한 건만 돌리면 구간별 값이 찍힌다:
   `python -m app.modules.travel_ops.mobility.engine.verify_time --cases final_project_cs/tests/unit/travel/mobility/real_legs_v1.json --case R-LOOP-01 --verbose`

**기대값을 「통과하게」 고치는 것은 회귀를 지우는 것과 같다.** 왜 움직였는지 모르면 갱신하지 않는다.

---

## 8. 이 문서 다음 — ① 에서 정한 것 (2026-09-29 · 본인)

- **게이트 목록의 공식 = 「주요 기능마다 케이스 하나 — 이게 깨지면 그 기능이 죽은 것」.** 「데이터 없이 도는 것」은 실행 조건이지 잠글 기준이 아니었다. 결과 21건(GPT 대조 ⑥ 뒤 · 처음 20) · `tests/unit/travel/mobility/regression_gate_v1.json`(기능 · 케이스 · 깨지면). 코어 경로에 지금 도달하지 않는 기능(동행 상한 · 택시 · 따릉이)과 데이터 판 검사(채운 시간표 `D28-*`)는 게이트에 넣지 않는다 — 「데이터가 맞는지는 우리 안에서 판단할 일이지 팀원이 볼 요소가 아니다」.
- 두 층: 팀원 게이트 = 기본 `pytest tests/unit/travel/mobility -q`(데이터 없이 도는 단위 110 + 회귀 게이트 21) / 우리 전체 = `-m "mobility_full and not live"`(회귀 나머지 150 + 실데이터 단위 23 — `test_plan_concurrency` · `test_plan_estimate` · `test_plan_bike` 의 DATA_DIR 축만 · 합성 단위는 게이트에 남긴다). pytest 는 라우터를 항상 끈다. 잠그는 것은 안 줄이고 층만 나눴다.
- 합성 묶음의 보조 파일 둘(§5)은 **아직 저장소에 넣지 않았다** — 데이터 배포 방식을 팀장님이 정하는 중이라 보류. 그때까지 합성 28건도 `DATA_DIR` 의 역 순서표·환승표가 있어야 돌고 없으면 skip 이다.
- 공유 적재(적재 조건이 같은 묶음의 케이스를 합쳐 판정기 하나) 대조: 171건 solo vs 합집합 vs 순서 섞기 — 판정·밖 판·시각·경고 동일. 다른 것은 **근거(evidence)의 시간표 날짜 표식**뿐(`timetable_v1@2026-09-09` vs `@09-11` — `Timetable.fetched_at` 이 「올라간 첫 행」의 수집일이라 적재 집합에 따라 갈린다 · CLI 도 같은 성질 · `--check-expect` 는 근거를 안 본다). 판정기 무수정이라 고치지 않고 27 에 넘김(표식은 meta `built_at` 로 두는 게 맞다).
- 대조 규칙은 한 곳: `verify_time.check_expect()`(CLI `--check-expect` 블록을 함수로 뺀 것 · 비교 칸·문구 동일 · 14묶음 CLI 출력 바이트 동일 확인). 적재도 한 곳: `verify_time.build_verifier_for_cases()`. pytest 판과 ps1 판이 같은 케이스 파일·같은 함수를 쓴다.

## 용어

- **밖 판정 / 내부 판정** — 코어로 나가는 둘(성립/불가) / 안에서 쓰는 넷(+근거없음·탈락).
- **best / worst** — 예정(중앙값) / 최악(배차 전부·p90). 판정은 worst, 표시는 best + @.
- **@ (`margin_min`)** — 최악 도착 − 예정 도착 + 정책 버퍼. **여유(`slack_min`)** — 도착 목표 − 예정 도착 − @.
- **늦어도 출발(`last_feasible_depart`)** — worst 판정기로 뒤에서 역산한 마지막 성립 출발 시각.
- **대안(alternatives)** — 불가일 때 규칙 순서(노선교체·수단교체·택시·자전거)로 만들어 **같은 판정기에 다시 통과시킨** 후보. 순위 없음.
- **후보(candidates)** — `multi` 입력에서 생성기가 만든 경로안(최단·최소환승·최소도보·버스 직행).
- **운행일** — 04:00 을 하루의 경계로 보는 날짜. `depart_at` 이 04:00 전이면 그 운행일의 연장(+24h).
