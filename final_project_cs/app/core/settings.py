"""triPilot 설정 — .env + config/guardrails.yaml 의 유일한 진입점.

규칙 (RULE.md §3.1, §3.2):
  - 하드코딩 금지. API 키·모델명·경로·가드레일 수치를 코드에 직접 쓰지 않는다.
  - 폴백 금지. 값이 없으면 명시적 예외로 실패한다. 기본값으로 조용히 대체하지 않는다.

가드레일 수치는 config/guardrails.yaml 이 유일한 정의처다.
같은 숫자를 코드 두 곳에 쓰면 그 자체가 결함이다(wiki/records/handoff/06_가드레일_수치.md).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """설정이 없거나 잘못됐다. 폴백하지 않고 여기서 멈춘다."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ACOP_",
        # ★두 파일을 읽는다. **뒤에 오는 쪽이 이긴다**(2026-09-10 실측).
        #   `.env`          — DB·LLM·앱 설정. 예전부터 있던 파일
        #   `.env.apikeys`  — 바깥 데이터 소스 키만. 둘 다 커밋하지 않는다
        #   키를 따로 둔 이유: 팀원마다 발급 상태가 다르고, 승인 대기 중인
        #   키가 섞이면 `.env` 전체를 주고받게 된다. 키만 갈아끼울 수 있어야 한다.
        # ★★`extra="forbid"` 라 **선언하지 않은 이름을 파일에 적으면 앱이
        #   기동조차 못 한다**(ValidationError). 키를 새로 넣을 때는 아래에
        #   필드부터 만든다. 이 함정은 `.env.apikeys.example` 첫 줄에도 적어 뒀다.
        env_file=(REPO_ROOT / ".env", REPO_ROOT / ".env.apikeys"),
        env_file_encoding="utf-8",
        extra="forbid",
    )

    # DB
    database_url: str

    # LLM
    llm_provider: str = "openai"
    openai_api_key: str
    llm_model: str
    embedding_model: str
    llm_temperature: float = 0.0
    llm_seed: int = 7
    local_ft_base_url: str = ""
    #: Ollama(로컬 LLM 서버) 주소 — 채우면 분류·추출·번역을 **Ollama 로** 한다. 비우면 OpenAI.
    #:  ★2026-09-14 OpenAI 크레딧 소진(429 실측) — Gemma 4 를 포트포워딩으로 붙여 쓴다.
    #:  ★OpenAI 호환 경로(`/v1`)는 쓰지 않는다 — 생각(thinking)이 본문에 섞여 나왔다(실측).
    #:    자체 API(`/api/chat`, `think:false`)는 `gemma4:12b` 가 1.3초에 JSON 을 냈다.
    #:  실제 주소는 `.env` 에만 적는다(공용 파일에 호스트를 적지 않는다).
    ollama_base_url: str = ""
    #: 시나리오 모드(운영콘솔 스위치로 확정 시나리오 하루를 실제 시스템으로 돌리는 시연 기능).
    #:  ★기본은 꺼짐 — 릴리즈에 `/scenario/*`·`/tripilot` 이 열리지 않게. 로컬 `.env` 에서만 켠다.
    scenario_mode_enabled: bool = False
    ollama_model: str = "gemma4:12b"
    #: 정책 검색(RAG)의 임베딩을 어디서 만드나 — `openai` | `ollama`.
    #:  ★`[2026-09-22]` 크레딧 없이 돌릴 길을 **명시적으로** 둔다. 조용한 폴백이 아니다 —
    #:    고른 쪽의 벡터가 없으면 검색이 예외를 낸다(RULE.md §3.2 「신호 없는 축소 금지」).
    #:  ★모델을 바꾸면 차원이 바뀐다(OpenAI 1536 · bge-m3 1024). 그래서 **칸을 따로** 두고
    #:    둘을 공존시킨다(CLAUDE.md §1 「덮어쓰지 않고 공존시킨다」).
    #: 운영 화면(`/ui/delegations`)에서 **위임을 주고 거둘 수 있나**. 기본은 꺼짐.
    #:  ★`[2026-09-22]` `/ui/*` 에는 **로그인이 없다**. 2026-08-18 에 같은 이유로 Composer 화면을
    #:    이 앱에서 **삭제**했다(D-CS-001) — 인증 없이 닿는 화면에 권한 있는 조작을 두지 않는다.
    #:    위임은 「한 건 승인」이 아니라 **서 있는 권한**이라 그 기준이 더 강하게 걸린다.
    #:    화면은 기본적으로 **읽기 전용**이고, 주고 거두는 것은 scope 가 걸린 REST 로 한다.
    #: 상시 작업이 연속 실패했을 때 운영자에게 알릴 곳. 비우면 `discord_webhook_url` 을 쓴다.
    #:  ★고객 통지와 **다른 곳으로 보낼 수 있게** 자리를 나눠 둔다 — 운영 알림이 고객 채널로 가면
    #:    고객이 우리 장애를 본다. 실제 주소는 `.env` 에만 적는다.
    ops_alert_webhook_url: str = ""
    # ★`[2026-09-23]` 운영 화면 로그인 계정 — JSON 목록 [{id, password_hash, scopes}].
    #   **비어 있으면 아무도 못 들어온다**(기본이 닫힘). `python -m scripts.ui_operator` 가 한 줄을 만든다.
    #   원문 비밀번호는 어디에도 두지 않는다. D-CS-007.
    ui_operators: str = ""
    # ★`[2026-09-24]` 웹(`frontend/apps/web`, 포트 3100)이 이 API 를 부르는 출처. 쉼표로 여럿.
    #   서버용 scope 키가 아니라 사용자 식별 키(`X-User-Key`)만 받는다(D-020 · 025).
    web_allowed_origins: str = "http://127.0.0.1:3100,http://localhost:3100"
    embedding_provider: str = "openai"
    ollama_embedding_model: str = "bge-m3:latest"
    ollama_timeout_seconds: float = 60.0

    # ── 여행 외부 소스 ─────────────────────────────────────────
    # ★기본값이 빈 문자열이다 = **그 소스를 안 붙인다.** 가짜로 채우지 않는다.
    #   무엇이 왜 빠졌는지는 `build_travel_sources()` 의 `unavailable` 이 들고
    #   있고 `/ui/admin` 이 그대로 보여 준다.
    # ★값은 `.env.apikeys` 에 넣는다(커밋 안 함). 형식은 `.env.apikeys.example`.
    weather_provider: str = "open_meteo"     # open_meteo | kma

    # 공공데이터포털 — ★계정마다 인증키가 **하나**고, 활용신청을 승인받은
    #   서비스 전부에 같은 키를 쓴다. 그래서 이것만 채우면 아래가 전부 산다.
    #   아래 서비스별 필드는 **다른 계정을 쓸 때만** 채우는 덮어쓰기 자리다.
    data_go_kr_key: str = ""                 # https://www.data.go.kr 공통 인증키

    #: 한국관광공사 국문 관광정보 — 장소·운영시간   data.go.kr/data/15101578
    tour_api_key: str = ""
    #: 기상청 단기예보 — Open-Meteo 대안            data.go.kr/data/15084084
    kma_api_key: str = ""
    #: 기상청 기상특보 — ★지속관리 루프의 트리거    data.go.kr/data/15000415
    kma_warning_api_key: str = ""
    #: 기상청 지진정보 — 최근 지진(규모·진앙)          data.go.kr/data/15000420
    #:  ★조회는 오늘 기준 3일 전까지(2026-09-14 실측). 비우면 공통 키를 쓴다.
    kma_earthquake_api_key: str = ""
    #: 행정안전부 긴급재난문자 — 호우·통제·화재 등 지역 재난문자       data.go.kr/data/15134001
    #:  ★2026-09-14 키 발급. 그전까지는 샘플 CSV 판(`disaster_msg.py`)으로 돌았다.
    disaster_msg_api_key: str = ""
    #: 한국천문연구원 특일 정보 — ★공휴일 휴무 판정 data.go.kr/data/15012690
    holiday_api_key: str = ""
    #: 국토교통부 TAGO — 버스·지하철·열차 운행      data.go.kr/data/15098530
    tago_api_key: str = ""
    #: 한국환경공단 에어코리아 — 야외활동 대기질    data.go.kr/data/15073861
    airkorea_api_key: str = ""
    #: 국립해양조사원 — 수상레저 활동 성립 판정
    #:  ★쓸 서비스는 **서핑지수(15142490)** 와 **TideBED 예측조위(15156026)** 둘이다.
    #:    서핑지수는 기관이 성립 여부를 판정해 주므로 우리가 기준을 만들지 않아도
    #:    되고, TideBED 는 **위도·경도로 조회된다** — 우리 `places` 에는 좌표만
    #:    있고 관측소 코드가 없다(다른 조석 API 는 전부 관측소 코드를 받는다).
    #:  ★(구)바다누리 자체 OpenAPI 는 종료 예정이라 공통 키로 충분하다.
    khoa_api_key: str = ""
    #: 국가유산청 — ★**키가 필요 없다**(2026-09-10 실호출 확인). 그래서 이 칸은
    #:  비워 둔다. 어댑터(`app/infrastructure/travel/heritage.py`)가 인증
    #:  파라미터 없이 부른다. 공공데이터포털 쪽 문화재 공간정보
    #:  (data.go.kr/data/3070426)를 따로 쓸 때만 채운다.
    heritage_api_key: str = ""
    #: 한국공항공사 실시간 운항 — Flight 결항·지연  data.go.kr/data/15113771
    #: `[미확보]` 2026-09-09 엔드포인트 경로를 확인하지 못했다(400 NO_OPENAPI_SERVICE_ERROR)
    airport_api_key: str = ""
    #: 외교부 국가·지역별 여행경보 — 해외 확장 시   data.go.kr/data/15076237
    mofa_api_key: str = ""
    #: 행정안전부 긴급재난문자 — data.go.kr/data/15134001 에 목록만 있고
    #:  실제 호출은 재난안전데이터 공유플랫폼(safetydata.go.kr)에서 한다.
    #:  `[미확보 2026-09-20]` 이 키가 공통 키(`data_go_kr_key`)와 같은 계정인지,
    #:  safetydata.go.kr 에 별도 가입·키 발급이 필요한지 확인 안 됐다 —
    #:  `app/infrastructure/travel/disaster_msg.py` 참고.
    disaster_api_key: str = ""

    # 정부 교통정보 — ★공공데이터포털 공통 키와 **다른 키**다(각 기관이 따로 발급).
    #: 국토교통부 ITS 국가교통정보센터 — 돌발상황  its.go.kr/opendata
    #:  ★2026-09-14 발급·실호출 확인. 고속도로·국도만이 아니라 **시내 도로(시군도)도
    #:    온다**(서울 122건 중 시군도 10·국도 6·지방도 2). 공공데이터포털 15040465 는
    #:    LINK 형이라 키가 ITS 에서 나온다.
    its_api_key: str = ""
    #: 경찰청 UTIC 도시교통정보센터 — 도로위험상황예보·돌발(행사·집회 포함)  utic.go.kr
    #:  ★**등록한 IP 에서만** 호출된다. 키가 **둘**이다 — 키마다 등록 IP 가 다르다(2026-09-14 발급).
    #:    key 1 = 바로 부르는 자리의 IP   key 2 = 고정 IP 경유 서버의 IP
    #:    ★실제 IP·서버 주소는 공용 파일에 적지 않는다 — `.env.apikeys` 에만.
    #:  ☆처음엔 `.env.apikeys` 에 같은 이름을 두 줄 적었는데, 그러면 **뒤의 것만** 읽힌다.
    utic_api_key_1: str = ""
    utic_api_key_2: str = ""
    #: ★IP 에 묶인 API 를 **고정 IP 서버를 거쳐** 부른다(2026-09-14). UTIC 는 등록한 IP 에서만
    #:  된다 — 개발 장소가 바뀌어도 되게 서버로 나간다. SSH SOCKS 터널을 열고 이 값을
    #:  `socks5://127.0.0.1:<포트>` 로 둔다. 비우면 경유 안 함. ★서버 주소는 공용에 적지 않는다.
    outbound_proxy_url: str = ""
    #: 경유시킬 소스 이름(쉼표). ★기본은 IP 에 묶인 것만 — 전부 경유시키면 터널이 끊길 때
    #:  모든 소스가 한꺼번에 실패해 치명이 된다. `*` 는 전부.
    outbound_proxy_sources: str = "utic"

    # 민간 — ★공공데이터포털 키와 **다른 키**다. 공통 키가 대신하지 않는다.
    odsay_api_key: str = ""                  # ODsay 대중교통 길찾기 lab.odsay.com
    kakao_rest_api_key: str = ""             # 카카오 지도 — 주소→좌표 developers.kakao.com
    vworld_api_key: str = ""                 # 브이월드 지오코더 — 주소→좌표(장소 데이터 좌표 보완) vworld.kr
    # ★`[2026-09-24]` 자리만 만들었다 — 새벽 3시 하루 점검에 쓴다(D-020). 비어 있으면 부르지 않는다.
    # ★`[2026-09-27]` 관광공사 장소 목록(`place_catalog`) 수집·사용 스위치 — **기본 꺼짐.**
    #   콘텐츠랩 저작권 정책의 「콘텐츠 캐싱(로컬서버 저장방식) 금지」 해석을 관광공사에 묻는 중이라, 답을 받기
    #   전까지 쌓지도 읽지도 않는다. 장소는 필요할 때 실시간으로 조회한다(`TourApiPlace.find`·`area_page`).
    tour_catalog_enabled: bool = False
    google_maps_api_key: str = ""            # 구글 Maps Platform(Places) console.cloud.google.com
    # ★`[2026-10-01]` 네이버 검색(블로그 · 카페글) — 대체 후보에 후기 몇 건을 곁들인다. 비어 있으면 부르지 않는다.
    #   NAVER API HUB(네이버 클라우드) 「검색」 키 — 2026-07-31 부터 개발자센터 신규 발급이 끝나 HUB 로 옮겨 갔다.
    #   주소 naverapihub.apigw.ntruss.com/search/v1/{blog,cafearticle}, 헤더 X-NCP-APIGW-API-KEY-ID · X-NCP-APIGW-API-KEY.
    #   네이버 지도 키(웹 NEXT_PUBLIC_NAVER_MAP_CLIENT_ID)와 다른 키다.
    #   결과는 저장하지 않는다(보여 줄 때만 불러온다).
    naver_search_client_id: str = ""
    naver_search_client_secret: str = ""
    # ── 이동 계산기(app/modules/travel_ops/mobility/engine) — `[2026-09-29 이동 계산기 문제목록 #48]` ──
    #   계산기가 저장소 맨 위 `.env` 를 import 때 직접 읽던 것을 여기로 모은다. 서버는 기동 때 이 값을 계산기에 넘긴다.
    #: 시간표·역 순서·환승 거리 등 가공 자료가 있는 폴더(이동 담당의 DATA_DIR · git 밖 · 약 195MB).
    #:  비우면 이동 계산기를 쓰지 않는다(연결부가 대체 경로로 간다). 실제 경로는 `.env` 에만 적는다.
    mobility_data_dir: str = ""
    #: 자전거·도보 경로 서버(GraphHopper) 주소. 비우면 자전거 소요는 근거없음으로 낸다. 실제 주소는 `.env` 에만.
    mobility_gh_url: str = ""
    #: 서울 열린데이터광장 키(따릉이 실시간 거치 대수). 비우면 거치 대수는 근거없음. ★제공처가 http 만 받는다(평문 전송)
    seoul_openapi_key: str = ""
    #: 디스코드 웹훅 — 고객 알림 채널(v11 §6-A). ★비어 있으면 알림을 **보내지 않았다고**
    #:  기록한다(dead_letter). 보낸 것처럼 `delivered` 로 찍지 않는다.
    discord_webhook_url: str = ""
    #: 알림 문구틀의 언어별 생성 캐시 파일(저장소 기준 상대 경로, `var/` 는 커밋되지 않는다).
    #:  ★배달 루프는 `--once` 로도 돌아 프로세스가 매번 죽는다 — 파일로 남겨야 다음 실행이
    #:  이어 쓴다. 담기는 것은 **틀**뿐이고 시각·장소·금액 같은 값은 들어가지 않는다
    #:  (`app/infrastructure/notify/phrase.py`). 비우면 그 프로세스 안에서만 재사용한다.
    notice_phrasebook_path: str = "var/notice_phrasebook.json"
    #: 여행계획서 링크의 앞부분(v11 §1 접점 ②). 알림에 **누를 수 있는 주소**로 싣는다.
    #:  ★비밀이 아니다 — 링크의 보호는 여행별 토큰(HMAC)이 맡는다.
    #:  기본값은 로컬 개발 서버(`.claude/launch.json` 의 `acop-cs-ui`, 8042).
    public_base_url: str = "http://127.0.0.1:8042"

    # 호출 속도 제한 (2026-09-10 신설)
    # 하루 한도를 하루에 걸쳐 쓴다.  최소 간격(초) = 86400 / 하루 한도
    # 시험한다고 빨리 두들기면 그날치 한도를 태우고 차단당한다.
    # 0 을 넣으면 그 소스는 제한 없음이다(권하지 않는다).
    #
    # 값의 출처를 갈라 적는다 - 「확인」은 공급자 문서/상세페이지에서 본 수,
    # 「미확인」은 data.go.kr 개발계정 통상값으로 보수적으로 잡은 수다.
    rate_open_meteo_per_day: int = 10000     # 확인: 비상업 하루 10,000
    rate_kma_warning_per_day: int = 10000    # 확인: 기상특보 개발계정
    rate_khoa_per_day: int = 10000           # 확인: 서핑지수/TideBED 개발계정
    rate_airkorea_per_day: int = 500         # 확인: 에어코리아 개발계정(낮다)
    rate_tour_api_per_day: int = 1000        # 미확인 - 보수적
    rate_kasi_holiday_per_day: int = 1000    # 미확인 - 보수적
    rate_tago_per_day: int = 1000            # 미확인 - 보수적
    rate_kma_per_day: int = 1000             # 미확인 - 보수적
    rate_airport_per_day: int = 1000         # 미확인 - 보수적
    rate_mofa_per_day: int = 1000            # 미확인 - 보수적
    rate_its_per_day: int = 1000             # 미확인 - 보수적(ITS 공개 한도 못 찾음)
    rate_kma_earthquake_per_day: int = 1000  # 미확인 - 보수적(지진정보 상세 한도 안 봄)
    rate_open_meteo_air_per_day: int = 2000  # 미확인 - 보수적(Open-Meteo 한도를 API 끼리 나눠 쓰는지 안 봄)
    rate_disaster_msg_per_day: int = 1000    # 확인: 사용자 제공(2026-09-15) 재난문자 하루 1,000
    #                                          ☆그전 값 100 은 같은 플랫폼 다른 API 사용기에서 옮긴 추정이었다
    #                                          ☆안전데이터 공유플랫폼 V2 공통 일일 1,000건과도 일치(2026-09-20 웹조사로 재확인)
    rate_utic_per_day: int = 1000            # 미확인 - 보수적(UTIC 한도 문서 못 봄)
    rate_odsay_per_day: int = 1000           # 미확인 - 무료 구간 한도 못 찾음
    rate_kakao_per_day: int = 1000            # 미확인 - 보수적
    rate_naver_search_per_day: int = 12500   # 확인: API HUB 검색 월 775,000건(2026-10 무료) — 블로그 · 카페글 합쳐 절반 아래(하루 12,500 × 31 ≈ 39만)
    #: 국가유산청은 키가 없고 공개된 한도도 못 찾았다. 그래도 스스로 조인다 -
    #: 한도를 모른다는 것이 마음껏 두들겨도 된다는 뜻은 아니다.
    rate_heritage_khs_per_day: int = 1000

    # 유료 전환 가능 소스 (2026-09-15 결정) — 무료 한도의 **절반**만 쓴다(여유 2배).
    #  ★Google 은 하루가 아니라 **월** 무료 한도다. 하루로 나눌 때 31일로 나눈다 —
    #    짧은 달 기준으로 나누면 31일 달에 넘친다.
    #  ★어댑터는 아직 없다. 이름을 먼저 잡아 두는 것은 붙이는 날 제한이 조용히 빠지지
    #    않게 하려는 것이다(`test_every_configured_source_name_matches_a_real_adapter`).
    #  확인: Google Maps Platform 가격표·필드표 (2026-09-15 조회)
    #    Place Details — 영업시간(currentOpeningHours·regularOpeningHours)을 요청하면
    #      **Enterprise** 등급. 무료 월 1,000건, 초과 $20/1,000 → 1,000 / 31 / 2 = 16
    #    Compute Routes — 교통 반영(TRAFFIC_AWARE)은 **Pro** 등급. 무료 월 5,000건,
    #      초과 $10/1,000 → 5,000 / 31 / 2 = 80.  TRANSIT 모드의 등급은 문서에 없다(미확인)
    rate_google_places_per_day: int = 16
    rate_google_routes_per_day: int = 80

    #: 간격이 안 찼을 때 기다려 볼 최대 시간(초). 고객 요청이 여기서 멈춘다.
    #:  넘으면 기다리지 않고 거부하고, Team 은 「모름」으로 넘어간다.
    rate_max_wait_seconds: float = 5.0

    def source_rate_limits(self) -> dict[str, int]:
        """`TravelSource.name` -> 하루 한도. 어댑터 이름과 정확히 맞춘다.

        이름이 어긋나면 제한이 조용히 안 걸린다. 그래서 한 곳에 모아 둔다.
        """
        return {
            "open_meteo": self.rate_open_meteo_per_day,
            "heritage_khs": self.rate_heritage_khs_per_day,
            "kasi_holiday": self.rate_kasi_holiday_per_day,
            "tour_api": self.rate_tour_api_per_day,
            "kma_warning": self.rate_kma_warning_per_day,
            "kma": self.rate_kma_per_day,
            "airkorea": self.rate_airkorea_per_day,
            "khoa": self.rate_khoa_per_day,
            "tago": self.rate_tago_per_day,
            "airport": self.rate_airport_per_day,
            "mofa": self.rate_mofa_per_day,
            "its": self.rate_its_per_day,
            "kma_earthquake": self.rate_kma_earthquake_per_day,
            "open_meteo_air": self.rate_open_meteo_air_per_day,
            "disaster_msg": self.rate_disaster_msg_per_day,
            "utic": self.rate_utic_per_day,
            "odsay": self.rate_odsay_per_day,
            "kakao": self.rate_kakao_per_day,
            "naver_search": self.rate_naver_search_per_day,
            "google_places": self.rate_google_places_per_day,
            "google_routes": self.rate_google_routes_per_day,
        }

    def utic_key(self, *, proxied: bool) -> str:
        """UTIC 호출에 쓸 키 — **나가는 IP 에 등록된 키**를 고른다.

        서버를 거쳐 나가면 key 2, 바로 나가면 key 1.
        ★다른 쪽 키로 대신하지 않는다 — 등록 IP 가 달라 어차피 거절된다. 비었으면 빈 문자열.
        """
        return (self.utic_api_key_2 if proxied else self.utic_api_key_1).strip()

    def public_data_key(self, override: str = "") -> str:
        """공공데이터포털 서비스 하나가 쓸 키. 둘 다 비었으면 빈 문자열.

        ★빈 문자열을 그대로 돌려준다 — 「키 없음」은 부르는 쪽이 보고 판단한다.
          여기서 예외를 던지면 키 하나 없다고 앱 전체가 안 뜬다.

        ★★**URL 디코드해서 돌려준다**(2026-09-10 실측으로 확정).
          포털이 인증키를 **일반(Decoding)** 과 **Encoding** 두 벌로 준다.
          Encoding 쪽은 `=` 가 `%3D` 로 들어 있는데, 그대로 httpx `params=` 에
          넘기면 `%` 가 다시 인코딩돼 `%253D` 가 되고 인증이 깨진다.

              키 그대로 + params    → HTTP 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR
              unquote() + params    → HTTP 200  ★

          ☆**오류 문구가 사실을 잘못 전한다.** "등록되지 않은 키"라고 하지만
            원인은 인코딩이다. 그 말을 믿고 키를 재발급하러 가면 시간을 버린다.

          `unquote()` 는 Decoding 키에도 안전하다 — `%XX` 가 없으면 그대로 둔다.
        """
        from urllib.parse import unquote

        return unquote(override or self.data_go_kr_key)

    # 앱
    env: str = "dev"
    tenant_id: str
    secret_key: str
    composer_jwt_secret: str
    composer_issuer_secret: str

    # 경로
    guardrails_path: str = "config/guardrails.yaml"


class Guardrails:
    """config/guardrails.yaml 을 읽기 전용으로 감싼다.

    점 경로로 읽는다: guardrails.get("context.token_budget") -> 12000
    없는 키를 조용히 None 으로 돌려주지 않는다 (조용한 스킵 금지, CLAUDE.md §3).
    """

    def __init__(self, data: dict[str, Any], source: Path) -> None:
        self._data = data
        self._source = source

    @property
    def source(self) -> Path:
        return self._source

    def get(self, dotted_key: str) -> Any:
        node: Any = self._data
        walked: list[str] = []
        for part in dotted_key.split("."):
            walked.append(part)
            if not isinstance(node, dict) or part not in node:
                raise ConfigError(
                    f"guardrails 키 없음: '{dotted_key}' "
                    f"({'.'.join(walked)} 에서 끊김, 출처={self._source})"
                )
            node = node[part]
        return node

    def as_dict(self) -> dict[str, Any]:
        return self._data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigError(
            "필수 환경변수가 없거나 잘못됐다. .env.example 를 .env 로 복사해서 채운다.\n"
            f"{exc}"
        ) from exc


@lru_cache(maxsize=1)
def get_guardrails() -> Guardrails:
    path = (REPO_ROOT / get_settings().guardrails_path).resolve()
    if not path.is_file():
        raise ConfigError(f"guardrails 파일 없음: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"guardrails 파일이 매핑이 아니다: {path}")
    return Guardrails(data, path)
