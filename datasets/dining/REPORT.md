# Dining(식당) 데이터 목록

데이터 파일은 git 에 없다(README.md). 코드는 `final_project_cs/scripts/dining/` 에 있고,
모든 스크립트가 **`datasets/dining/processed/`** 를 읽는다. 다른 곳에 두려면 환경변수 `DINING_DATA` 로 바꾼다.

## 받는 법

1. 팀 드라이브 `processed/` 를 통째로 받아 `datasets/dining/processed/` 에 푼다(폴더 구조 그대로).
2. `python final_project_cs/scripts/dining/rebuild.py --check` — 빠진 파일을 알려 준다.
3. `python final_project_cs/scripts/dining/rebuild.py` — DB 를 새로 세운다. `_build/` 는 여기서 다시 생긴다.

## 파일

**가** = 스크립트로 다시 받을 수 있다(API 키 필요) · **사람** = 사람이 확인·수정했다, 드라이브 사본이 원본이다

| 파일 | 무엇 | 구분 | 읽는 코드 |
|---|---|---|---|
| `tourapi_서울_음식점_목록.json` | 한국관광공사 TourAPI 서울 음식점 목록 (ⓒ한국관광공사). 2026-09-21 의 990곳(`areaCode=1`) 뒤에 2026-10-01 법정동(`lDongRegnCd=11`)으로 다시 받아 새로 나온 611곳을 붙였다 — 1,601곳. ★서울은 **법정동 코드로 받는다**. 응답 대부분이 `areacode` 를 비워 `areaCode=1` 로는 서울 음식점의 약 60%만 온다(1,595곳 중 946곳 · 토속촌삼계탕이 빠져 있었다) | 가 | `make_load_sql.py` · `fetch_intro.py` · `make_michelin_sql.py` |
| `tourapi_서울_음식점_목록_법정동.json` | 위 재수집의 원본(법정동 1,595곳) | 가 | — |
| `tourapi_음식점_소개정보.json` | TourAPI 소개정보(영업시간·전화 원문) (ⓒ한국관광공사) | 가 (`fetch_intro.py`) | `make_load_sql.py` · `parse_hours.py` · `make_attribute_sql.py` |
| `holidays_2026_2027.json` | 공휴일 | 가 | `make_holiday_sql.py` |
| `michelin/미쉐린_서울_2026.csv` · `_가게.jsonl` | 미쉐린 가이드 서울 목록·가게 사실 | 사람 | `make_michelin_sql.py` |
| `michelin/미쉐린_영업시간_검수.csv` | 미쉐린 영업시간 검수 | 사람 | `make_michelin_sql.py` |
| `vegan/비건식당_*.csv` | 비건 식당 목록·근거·영업시간 검수 | 사람 | `make_vegan_sql.py` |
| `halal/할랄식당_검수.csv` | 할랄 식당 검수(인허가 대조) | 사람 | `make_halal_sql.py` |
| `kids/노키즈존_검수.csv` | 노키즈존 검수 | 사람 | `make_attribute_sql.py` |
| `truth/대조표100_검수_2026-09-21.csv` | 관광공사 원문이 실제와 맞는지 — 1차 무작위 100곳(네이버 지도 대조) | 사람 | `make_truth_sql.py` · `check_dining.py` |
| `truth/대조표2차_100_검수전_2026-09-30.*` | 2차 무작위 100곳(1차 제외, 씨앗 20260930). **검수 전** — 적재에 안 쓴다 | 가 (`make_audit_sheet.py`) → 사람 | — |
| `closure/폐업대조_*.csv` | 인허가 자료와 폐업 대조. 20261001 판은 새 가게 602곳을 서울시 일반·휴게음식점 인허가(2026-09-20 기준, `raw/서울시 *음식점 인허가 정보.csv`)와 대조하고 카카오맵으로 다시 본 것 | 사람 | `make_closure_sql.py` |
| `review/검토목록_*.csv` | 폐업·빈칸·구글 시트에서 사람이 볼 행을 모은 목록과 검수 결과(사람 확인 · 「사용자 지시 검수(Claude)」 구분). 적재에는 안 쓴다 | 사람 | — |
| `gaps/빈칸_검수_<날짜>.csv` | 원장 빈칸(영업시간·전화·좌표) 검수. 확인일 있는 행만 넣는다 | 사람 | `make_gap_sql.py` |
| `google/구글_연결.csv` | 구글 장소 연결(place_id·링크). 확인자·확인일이 있으면 valid | 가+사람 (`google_link.py` · `google_sheet.py`) | `google_link.py --to-sql` |
| `google/구글_미연결_<날짜>.csv` | (필요할 때만) 자동으로 못 붙인 가게에 사람이 링크·폐업을 적는 시트. 2026-09-30 분은 모두 옮겨 지금은 없다 | 사람 | `google_sheet.py` → 구글_연결.csv · 폐업대조 시트 |
| `similarity/*` | 유사 식당 판정 시트·지표 | 사람 | `ml/dining_similarity.py` |
| `export/요식_원장_*.csv` | DB 원장 내보내기(팀원 시험용). 원장을 고치면 다시 뽑는다 | 가 (`export_*.py`) | — |

## 개인정보

가게(사업장) 정보만 있다 — 상호·주소·전화·영업시간. 개인 이름·연락처는 없다.
그래도 README 규칙대로 git 에는 올리지 않는다.

※ 2026-09-30 까지는 `final_project_cs/data/dining/` 에 두고 git 에 올렸다. 이 날 여기로 옮겼다.
옛 파일은 git 기록에 남아 있다.
