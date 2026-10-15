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
from typing import Any, Literal

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
    #: ★`[2026-10-01 사용자 지시]` **서버 배포용** OpenAI 키(서버를 도커 이미지로 만들어 아마존 등에 올릴 때 쓸 키). 값은 `.env` 에만 둔다.
    #:  ★`[2026-10-07 사용자 지시]` 이제 **한 곳만** 읽는다 — 모델 서버가 모두 장애일 때 넘기는 장치(`llm_failover.py`, 스위치 `llm_failover_enabled`).
    #:  개인 키(`openai_api_key`)로 대신하지 않는다. 그 밖의 코드 · 시험 · 측정은 이 값을 읽지 않는다.
    #:  선언한 까닭: `extra="forbid"` 라 `.env` 에 이름이 먼저 적히면 앱이 기동조차 못 한다.
    openai_api_key_server: str = ""
    llm_model: str
    embedding_model: str
    llm_temperature: float = 0.0
    llm_seed: int = 7
    local_ft_base_url: str = ""
    #: 활동 판정 LLM(D-CS-008 실험 → D-CS-015 develop 판).
    #:  `rule` = 지금 규칙 판정만 · `shadow` = 규칙으로 답하고 LLM 판정은 차이만 기록 · `llm` = LLM 판정으로 답한다.
    #:  ★`[2026-10-09]` 기본값을 `shadow` → `rule` 로 바꿨다(D-CS-015, 사용자 결정). develop 판 활동 팀에 붙이면 키가 있는
    #:    모든 환경의 성립 판정마다 백그라운드 LLM 호출(웹 검색 포함)이 나간다 — 섀도 기록을 모을 환경만 `.env` 로 켠다.
    #:    `llm` 으로 올리는 기준은 `wiki/teams/액티비티 LLM 연동 계획서.md` §8 에 수치로 적은 뒤에 정한다.
    activity_judge_mode: Literal["rule", "shadow", "llm"] = "rule"
    #:  Responses API 로 부른다(`app/infrastructure/llm/openai_responses.py`). `llm_model` 과 따로 둔다 —
    #:   다른 Team 의 모델을 바꾸지 않고 이 실험만 바꿀 수 있게.
    activity_judge_model: str = "gpt-5.4-nano"
    #:  ★`temperature`·`seed` 칸은 두지 않는다 — gpt-5.4-nano 는 `seed` 를 400 으로 거절하고
    #:   `temperature` 는 effort=none 일 때만 받는다(2026-10-06 스파이크 Q1).
    activity_judge_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh"] = "low"
    #:  웹 검색을 끄면 실시간 운영 상태 판정은 「모름」이 된다(지어내지 않는다).
    activity_judge_web_search: bool = True
    #: Ollama(로컬 LLM 서버) 주소 — 채우면 분류·추출·번역을 **Ollama 로** 한다. 비우면 OpenAI.
    #:  ★2026-09-14 OpenAI 크레딧 소진(429 실측) — Gemma 4 를 포트포워딩으로 붙여 쓴다.
    #:  ★OpenAI 호환 경로(`/v1`)는 쓰지 않는다 — 생각(thinking)이 본문에 섞여 나왔다(실측).
    #:    자체 API(`/api/chat`, `think:false`)는 `gemma4:12b` 가 1.3초에 JSON 을 냈다(2026-09 측정 당시 모델 — 지금 기본은 아래 `ollama_model`).
    #:  실제 주소는 `.env` 에만 적는다(공용 파일에 호스트를 적지 않는다).
    ollama_base_url: str = ""
    #: ★`[2026-09-30]` 더는 쓰지 않는다 — 시나리오(시연) 모드를 운영 앱에서 뗐다(`legacy/scenario_mode/`). 이름만 남겼다:
    #:  설정 검사가 모르는 이름을 거부(`extra="forbid"`)해서, 옛 `.env` 가 이 이름을 적고 있으면 앱이 못 뜬다.
    scenario_mode_enabled: bool = False
    #: ★`[2026-10-14 사용자 지시]` 기본 모델은 **우리 데이터로 가르친 E4B** `tripilot-pointer:e4b-ft`(Ollama `gemma4:e4b` Q4_K_M 에 일정 항목 짚기 자료로 LoRA 학습).
    #:  전에는 학습 없는 `gemma4:12b` 였다. 요청 해석(할 일 고르기)·일정 짚기를 이 모델 하나가 맡는다 — 12GB GPU 에서 모델 교체가 없고,
    #:  434 문장 재생(채점용 126)에서 할 일 85%·대상 78% 로 학습 없는 12B(74%·72%)보다 맞고 빠르다(`wiki/records/reports/2026-10-14_채팅해석_근본개선_1차.md`).
    #:  ★이 이름의 모델은 학습으로 만든 것이라 Ollama 에 올려 둔 곳에만 있다. 없는 곳(개발 PC 등)은 `ACOP_OLLAMA_MODEL` 로 있는 모델을 지정한다(예 `gemma4:12b`).
    #:  문맥 길이는 아래 `ollama_num_ctx`(8192)와 함께여야 한다(Ollama 기본 4,096 이면 해석 입력의 앞이 잘린다).
    ollama_model: str = "tripilot-pointer:e4b-ft"
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
    #: ★`[2026-09-28]` **개발용** — 웹 사용자 키 발급 한도(`security.web_session_issue_per_hour`)를 끈다.
    #:  화면 시험이 한 주소에서 새 사용자를 계속 만들다 429 에 걸렸다(ui 세션). 기본은 꺼짐(한도 적용) —
    #:  로컬 `.env` 에서만 켠다(`ACOP_WEB_SESSION_ISSUE_UNLIMITED=true`). 숫자는 가드레일 그대로다.
    web_session_issue_unlimited: bool = False
    #: ★`[2026-09-28]` 사람 확인(Cloudflare Turnstile) 비밀키 — 있으면 키 발급·계획 읽기에서 **늘 확인**한다(`web_guard.py`).
    #:  개발 시험 키 `1x0000000000000000000000000000000AA`(항상 통과) · `2x…AA`(항상 실패) — Cloudflare 공개 값.
    #:  실제 비밀키는 `.env` 에만 둔다. 비어 있으면 확인을 건너뛰고 응답에 `human_check: skipped` 를 싣는다(개발).
    turnstile_secret: str = ""
    #: 비밀키 없이는 **서버를 켜지 않는다**. `env=prod` 면 이 값과 무관하게 켜진 것으로 본다(운영에서 모르게 꺼지지 않게).
    turnstile_required: bool = False
    #: 쉼표로 여럿 — 있으면 Cloudflare 가 돌려준 `hostname` 이 이 중 하나여야 통과
    turnstile_hostnames: str = ""
    #: 역방향 프록시 주소(쉼표). 이 주소에서 온 요청만 `X-Forwarded-For` 의 원 주소를 믿는다. 비어 있으면 연결 주소
    trusted_proxies: str = ""
    # ── 소셜 로그인 `[2026-10-03 ui 세션 요청서 · 사용자 결정 「가장 쉬운 걸로 하나 먼저」 → 구글]` ──
    #:  업체 콘솔에서 사람이 만든 **클라이언트 ID · 비밀값**(`ACOP_GOOGLE_CLIENT_ID` · `ACOP_GOOGLE_CLIENT_SECRET`). git 밖 환경 파일에만 둔다.
    #:  ★둘 다 있어야 그 업체가 켜진다(`GET /v1/web/auth/providers` 에 나온다). 하나라도 비면 목록에서 빠진다 — 반쯤 켜진 채 뜨지 않게.
    google_client_id: str = ""
    google_client_secret: str = ""
    #:  `[2026-10-05]` 「디스코드로 연결」(알림 웹훅 자동 연결) — 디스코드 개발자 포털에서 만든 앱의 **클라이언트 ID · 비밀값**(`ACOP_DISCORD_CLIENT_ID` · `ACOP_DISCORD_CLIENT_SECRET`).
    #:  ★둘 다 있어야 켜진다(`GET /v1/web/profile` 의 `discord_connect.available`). 소셜 로그인용 디스코드 앱을 만들게 되면 같은 앱을 써도 된다. git 밖 환경 파일에만 둔다.
    discord_client_id: str = ""
    discord_client_secret: str = ""
    #:  `[2026-10-05]` 텔레그램으로 알림 받기 — @BotFather 에서 사람이 만든 봇의 **토큰 · 아이디(`…bot` 으로 끝남)** 와 웹훅 **비밀값**(`setWebhook` 의 `secret_token`: `A-Z a-z 0-9 _ -` 1~256자, 우리가 정한다).
    #:  ★셋 다 있어야 켜진다(`GET /v1/web/profile` 의 `telegram_connect.available`). 운영 봇과 시험 봇은 **환경 파일이 따로**다(시험 봇 토큰은 개발 환경에만). 토큰은 로그 · 응답 · 문서 · 커밋에 쓰지 않는다.
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""
    #:  로그인이 끝나면 브라우저를 돌려보낼 **웹 주소(출처만)**. ★서버 설정이고 요청 값으로 바꿀 수 없다(열린 리디렉션 금지).
    #:  비어 있으면 `web_allowed_origins` 의 첫 값.
    web_origin: str = ""
    # ── 운영 앱(운영자 콘솔) — 고객 API 앱과 다른 프로세스 · 다른 포트 `[2026-09-29]` 사용자 지시 ──
    #: 운영 앱 포트. 127.0.0.1 에만 묶는다(`app/ops_entrypoint.py`). 지금 쓰는 번호(3100·3102·3200·3300·8041·8042·8044·8060)와 겹치지 않게
    ops_port: int = 8070
    #: 운영 화면이 부르는 **고객 API** 주소(승인 · 위임 · 바깥함). 같은 프로세스 안 호출을 없앴다 — 실제 HTTP 로 부른다
    ops_api_base_url: str = "http://127.0.0.1:8042"
    #: 운영 앱이 고객 API 를 부를 scope 키 — JSON `{"action:approve": "…", "delegation:read": "…", "delegation:write": "…"}`.
    #:  ★운영 앱 서버의 비밀(`.env`)에만 둔다. 전에는 화면이 서버 비밀키로 **스스로 만들어** 썼다(D-CS-007) — 이제 만들지 않는다
    ops_api_keys: str = ""
    #: 운영 화면의 조회 전용 DB 주소(읽기 전용 계정). 비우면 본 DB 주소(개발)
    ops_read_database_url: str = ""
    embedding_provider: str = "openai"
    ollama_embedding_model: str = "bge-m3:latest"
    ollama_timeout_seconds: float = 60.0
    #: ★`[2026-09-29]` 모델을 붙잡아 둘 시간(Ollama `keep_alive`, 예 `30m` · `2h`). 비우면 Ollama 기본(5분).
    #:  길게 두면 잠든 모델의 느린 첫 호출(ui 세션 실측 29.8초)이 줄지만 원격 GPU 메모리를 그만큼 잡는다 — 값은 운영이 정한다
    ollama_keep_alive: str = ""
    #: ★`[2026-10-14]` Ollama 문맥 길이(`num_ctx`). 요청 해석 입력이 약 4,600 토큰이라 Ollama 기본(12~16GB GPU 에서 4,096)이면 앞이 잘린다
    #:  (`ollama_chat.OllamaChat.num_ctx`). 0 이면 Ollama 기본을 쓴다
    ollama_num_ctx: int = 8192
    #: ★`[2026-10-07 사용자 지시]` 일정 항목 짚기 **가르친 모델**(Ollama 에 올린 이름, 예 `tripilot-pointer:e4b-ft`). 비우면 낱말 규칙만 쓴다.
    #:  켜고 끄기는 가드레일 `travel.pointer.mode`. 모델이 못 부르면 규칙으로 돌아간다(`item_pointer.py`).
    ollama_pointer_model: str = ""
    ollama_pointer_timeout_seconds: float = 30.0
    #: ★`[2026-10-13 사용자 지시]` 공지 「끝났나」 **2차 확인** 모델(Ollama 에 올린 이름, 예 `tripilot-pointer:e4b-ft`). 비우면 규칙만 쓴다.
    #:  켜고 끄기는 가드레일 `travel.notice_end.mode`. 규칙과 모델이 둘 다 끝남일 때만 끝남이고, 모델을 못 부르면 규칙 결과를 쓴다(`notice_end_model.py`).
    ollama_notice_model: str = ""
    #:  ★첫 호출은 모델을 올리느라 길다(2026-10-13 x600 실측 53.7초, 이어지는 호출 약 1.3초) — 감시 루프는 고객을 기다리게 하지 않으므로 넉넉히 둔다
    ollama_notice_timeout_seconds: float = 90.0
    # ── 모델 서버가 죽거나 늦을 때 **서버용 OpenAI 키**로 넘기는 장치 `[2026-10-07 사용자 지시]` ──
    #:  「올라마나 외부 GPU 서버가 모두 장애면 서버키(개인키 말고)로 자동 전환돼서 동작하게」. 코드 `app/infrastructure/llm_failover.py`.
    #:  ★스위치는 **기본 꺼짐**이다 — 개발 PC 에서 모델 서버 연결이 끊겼을 때 시험 · 측정이 조용히 유료 API 로 새지 않게. 운영(배포) 환경 파일에서만 켠다.
    #:  켜져 있어도 `openai_api_key_server` 가 비어 있으면 넘기지 않는다(그때는 지금처럼 Ollama 만).
    llm_failover_enabled: bool = False
    #:  넘길 때 쓰는 OpenAI 모델. 비우면 `llm_model`.
    #:  ★`[2026-10-07 실측 — 결정 단위 재생 · 실제 대화 66문장]` `gpt-4o-mini` 52/66 · `gpt-4.1-mini` 55/66 · **`gpt-4.1` 64/66 = 97.0%**(Gemma 4 12B 와 같음). 그래서 기본은 `gpt-4.1` —
    #:  작은 모델은 엉뚱한 대상을 바꾸거나(4o-mini 1건) 쓸데없이 되묻는다(4.1-mini 9건)
    llm_failover_model: str = "gpt-4.1"
    #:  Ollama 를 기다리는 시간(초) — 고객 응답 SLA 15초 안에 API 까지 끝나게 `ollama_timeout_seconds`(60)보다 짧다. 서버가 식어 느린 첫 호출도 여기서 걸러진다(예열이 그 몫)
    #:  ★`[2026-10-14 사용자 지시]` 6 → 9초. 기본 모델이 가르친 E4B 로 바뀌었고(`ollama_model`), 그 해석 한 건이 5.1~7.3초(x600 실측 —
    #:    지시문 읽기 약 2초는 문장마다 다시 든다)라 6초면 8건 중 6건이 유료 대체로 넘어갔다. 9초면 8/8 로컬(운영이 이미 9초).
    #:    넘어가도 유료 대체(2~3초)까지 `llm_failover_total_seconds`(15) 안이다. 정책 검색의 임베딩 대기(`rag/retriever.py`)도 같은 값을 쓴다.
    llm_failover_primary_timeout_seconds: float = 9.0
    #:  OpenAI 를 기다리는 시간(초)
    llm_failover_api_timeout_seconds: float = 8.0
    #:  한 호출에 쓸 수 있는 전체 시간(초) — 이 안에 다시 시도(429·5xx 1회)와 넘김이 다 들어가야 한다
    llm_failover_total_seconds: float = 15.0
    #:  연속 실패가 이만큼이면 서킷을 연다(그 서버를 건너뛰고 다음 경로로 바로 간다)
    llm_failover_failures: int = 3
    #:  서킷이 열려 있는 시간(초) — 지나면 한 번만 시험해서(반쯤 열림) 성공하면 닫는다
    llm_failover_open_seconds: float = 60.0
    #:  API 로 넘긴 호출의 일 · 월 상한(`external_call_budget` 의 `openai_failover` 줄). 넘으면 더 안 부르고 실패로 올린다 — 비용 폭주 방어
    llm_failover_daily_cap: int = 2000
    llm_failover_monthly_cap: int = 30000
    #:  사진·스캔 받아쓰기(이미지)를 API 로 넘길지. ★이미지에는 이름 · 연락처 · 예약번호가 그대로 있어 **가릴 수 없다** — 그래서 따로 켠다(기본 꺼짐).
    llm_failover_vision: bool = False
    #:  Ollama 가 둘 이상일 때(예: 오라클 무료 서버 + GPU 서버) 쉼표로 이어 적는다 — 앞에서부터 시도하고 **모두** 안 되면 API 로 간다
    ollama_fallback_base_urls: str = ""
    ollama_pointer_keep_alive: str = ""
    #: 일정이 바뀐 첫 문장은 고객을 기다리게 하지 않고 규칙으로 답하며 모델은 뒤에서 그 일정을 읽어 둔다(`item_pointer.py` 머리). 끄면 첫 문장도 모델을 기다린다.
    ollama_pointer_background_warm: bool = True
    ollama_pointer_warm_timeout_seconds: float = 180.0

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
    #: Jev(TypeSafe AI 의 판정용 모델) API 키 — `[2026-09-30]` `.env` 에 키가 먼저 적혀 앱이 기동 못 했다(`extra="forbid"`).
    #:  ★자리만 선언했다 — 아직 어느 코드도 읽지 않는다(질의 해석 개선 후보로 조사 중). 값은 `.env` 에만 둔다.
    jev_api_key: str = ""
    #: Anthropic Claude API 키 — `[2026-10-15 사용자 지시]` `.env` 에 `ACOP_CLAUDE_API_KEY` 를 먼저 넣어 자리를 선언했다(`extra="forbid"`).
    #:  ★자리만 선언했다 — 아직 어느 코드도 읽지 않는다. 값은 `.env` 에만 둔다.
    claude_api_key: str = ""
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
    #: 행정안전부 지진옥외대피장소(전국) — 재난 일정 정지 때 가까운 대피 장소 안내  data.go.kr/data/15138868
    #:  ★`[2026-10-06]` 예시 파일에 이름이 먼저 들어가 `extra="forbid"` 라 이 PC 에서 설정이 통째로 거부됐다(시험 수집 78개 파일 · 재생 도구 불통). 비우면 서울시 자료(DB 에 적재한 곳)로만 안내한다.
    safetydata_shelter_api_key: str = ""
    #: 행정안전부 도로명주소 「도로명주소 검색 API」 승인키 — 장소 주소 글자 → 정식 주소 번호(행정구역 · 도로 · 건물 번호)  business.juso.go.kr
    #:  ★`[2026-10-14]` 자리만 선언했다 — 아직 어느 코드도 읽지 않는다. 아래 좌표제공 API 와 짝으로 건물 출입구 좌표를 얻어
    #:  걷는 길을 출입구에서 시작하게 할 예정(이동). 공공데이터포털 공통 키와 다른 별도 승인키이고, 신청서의 URL(IP)(x600 서버 주소)에 묶인다.
    juso_search_api_key: str = ""
    #: 행정안전부 도로명주소 「좌표제공 검색 API」 승인키 — 주소 번호 → 건물 출입구 좌표(UTM-K). 위 검색 API 와 별개 승인키.
    juso_coord_api_key: str = ""
    #: 서울교통공사 지하철알림정보 — 무정차 통과 감시(문제목록 #36)  data.go.kr/data/15144070
    #:  ★공통 키로 충분하다. 이 칸은 다른 계정을 쓸 때만 채운다.
    subway_notice_api_key: str = ""
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
    #:  비워 둔다. 어댑터(`app/domains/travel_ops/ports/data_sources/heritage.py`)가 인증
    #:  파라미터 없이 부른다. 공공데이터포털 쪽 문화재 공간정보
    #:  (data.go.kr/data/3070426)를 따로 쓸 때만 채운다.
    heritage_api_key: str = ""
    #: 한국공항공사 실시간 운항 — Flight 결항·지연  data.go.kr/data/15113771
    #: `[미확보]` 2026-09-09 엔드포인트 경로를 확인하지 못했다(400 NO_OPENAPI_SERVICE_ERROR)
    airport_api_key: str = ""
    #: 외교부 국가·지역별 여행경보 — 해외 확장 시   data.go.kr/data/15076237
    mofa_api_key: str = ""

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
    # ★관광공사 장소 목록(`place_catalog`) 수집·사용 스위치 — **기본 켜짐**(`[2026-09-28]` 사용자 결정으로 되돌림).
    #   ☆`[2026-09-27]` 콘텐츠랩 저작권 정책의 한 줄 「콘텐츠 캐싱(로컬서버 저장방식) 금지」를 「아무것도 저장 금지」로
    #     넓게 읽어 끄고 8,015행을 지웠다. 약관(구속력 있는 계약)에는 저장 금지가 없고(제10조 유·무료 활용 · 제3조
    #     파일 내려받기 제공), 저작권법 제93조는 DB 의 「전부·상당 부분」만 막으며 사실(소재)엔 미치지 않고,
    #     공공데이터법 제3조④는 영리 이용 제한까지 금지한다. 게다가 학업용이다. 사진·소개글은 저장하지 않는다.
    #   근거 정리: `../program/plan/A-COP_고객계획_읽기_설계_2026-09-26.md` §4-6 「2026-09-28 재판단」.
    tour_catalog_enabled: bool = True
    google_maps_api_key: str = ""            # 구글 Maps Platform(Places) console.cloud.google.com
    # ★`[2026-10-08]` LiteAPI(Nuitee) — 서비스 안 숙소 예약 시험(샌드박스 키 `sand_`). 비어 있으면 부르지 않는다.
    #   지금은 `scripts/probe_liteapi_booking.py` 만 읽는다.
    liteapi_api_key: str = ""
    # ★`[2026-10-08]` Ignav — 항공 검색·예약 링크(MCP https://ignav.com/mcp). 비어 있으면 부르지 않는다.
    #   지금은 `scripts/probe_ignav_mcp.py` 만 읽는다.
    ignav_api_key: str = ""
    # ★[2026-10-09] SerpApi Google Flights — 구글 항공권 한국 설정(gl=kr · hl=ko · KRW) 검색. 무료 월 250회. 비면 쓰지 않는다
    serpapi_api_key: str = ""
    # ★`[2026-10-01]` 네이버 검색(블로그 · 카페글) — 대체 후보에 후기 몇 건을 곁들인다. 비어 있으면 부르지 않는다.
    #   NAVER API HUB(네이버 클라우드) 「검색」 키 — 2026-07-31 부터 개발자센터 신규 발급이 끝나 HUB 로 옮겨 갔다.
    #   주소 naverapihub.apigw.ntruss.com/search/v1/{blog,cafearticle}, 헤더 X-NCP-APIGW-API-KEY-ID · X-NCP-APIGW-API-KEY.
    #   네이버 지도 키(웹 NEXT_PUBLIC_NAVER_MAP_CLIENT_ID)와 다른 키다.
    #   결과는 저장하지 않는다(보여 줄 때만 불러온다).
    naver_search_client_id: str = ""
    naver_search_client_secret: str = ""
    # ── 이동 계산기(app/domains/travel_ops/instances/mobility/engine) — `[2026-09-29 이동 계산기 문제목록 #48]` ──
    #   계산기가 저장소 맨 위 `.env` 를 import 때 직접 읽던 것을 여기로 모은다. 서버는 기동 때 이 값을 계산기에 넘긴다.
    #: 시간표·역 순서·환승 거리 등 가공 자료가 있는 폴더(이동 담당의 DATA_DIR · git 밖 · 약 195MB).
    #:  비우면 이동 계산기를 쓰지 않는다(연결부가 대체 경로로 간다). 실제 경로는 `.env` 에만 적는다.
    mobility_data_dir: str = ""
    #: 자전거·도보 경로 서버(GraphHopper) 주소. 비우면 자전거 소요는 근거없음으로 낸다. 실제 주소는 `.env` 에만.
    mobility_gh_url: str = ""
    #: ☆`[2026-10-04]` GraphHopper 주소가 없을 때 저장소 안 도로 그래프(`road_graph_v2` 걸음 길 포함 · 없으면 `v1`)로 파이썬이 직접 길을 찾는다 — 택시·자전거·장소 사이 도보.
    #:  서버를 따로 띄우지 않는다(첫 길 묻기 때 자료를 한 번 올린다 · 약 7~16초 · 250~350MB). 끄려면 false — 도보는 직선×우회계수, 택시·자전거는 근거없음.
    mobility_local_router: bool = True
    #: 서울 열린데이터광장 키(따릉이 실시간 거치 대수). 비우면 거치 대수는 근거없음. ★제공처가 http 만 받는다(평문 전송)
    seoul_openapi_key: str = ""
    #: 서울 열린데이터광장 「실시간 지하철 인증키」(일반 키와 **별개** · 하루 1,000건 · 활용사례 등록 심사 뒤 해제) — 실시간 도착정보
    #:  (`travel/seoul_subway.py`, `sources.subway_arrival`). ★설정에 없는 이름으로 넣으면 기동이 거부된다(`extra="forbid"` · 2026-10-05 실제로 났다).
    seoul_metro_api_key: str = ""
    #: 서울 버스 도착정보 키. 비우면 승인된 공공데이터포털 공통 키를 쓴다.
    seoul_bus_api_key: str = ""
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
    #: ★ITS 는 응답이 「월간 API 호출 한도 초과」(4001)다 — 월 한도의 크기는 포털(국가교통정보센터 인증키 상세정보)에서 확인해 넣는다.
    #:  0 = 모른다(세기만 하고 월로는 막지 않는다 — 하루 줄로만 막는다). 2026-10-05 기준 `[미확인]`.
    rate_its_per_month: int = 0
    rate_kma_earthquake_per_day: int = 1000  # 미확인 - 보수적(지진정보 상세 한도 안 봄)
    rate_open_meteo_air_per_day: int = 2000  # 미확인 - 보수적(Open-Meteo 한도를 API 끼리 나눠 쓰는지 안 봄)
    rate_disaster_msg_per_day: int = 1000    # 확인: 사용자 제공(2026-09-15) 재난문자 하루 1,000
    #                                          ☆그전 값 100 은 같은 플랫폼 다른 API 사용기에서 옮긴 추정이었다
    rate_utic_per_day: int = 1000            # 미확인 - 보수적(UTIC 한도 문서 못 봄)
    #: ★확인(2026-10-04): ODsay LAB 운영정책 「Basic 서비스 30 / 일 · 기간 제한 없음」 — 이 계정으로 30회 안팎에서 `Daily quota exceeded` 를 실측했다.
    #:  ☆앞 값 1000 은 근거 없는 추정이었다(「미확인」). 어댑터가 없어 지금은 호출 코드가 없다 — 붙일 때 이 한도를 그대로 쓴다.
    rate_odsay_per_day: int = 30
    rate_kakao_per_day: int = 1000           # 미확인 - 보수적
    rate_naver_search_per_day: int = 12500   # 확인: API HUB 검색 월 775,000건(2026-10 무료) — 블로그 · 카페글 합쳐 절반 아래(하루 12,500 × 31 ≈ 39만)
    #: 확인(2026-10-04): 서울교통공사 지하철알림정보 개발계정 하루 10,000건(공식 페이지) → 절반만 쓴다(여유 2배). 매 1분 갱신 · 한 번에 최신 100건
    rate_subway_notice_per_day: int = 5000
    #: 확인(2026-10-04 공식 안내): 서울 「실시간 지하철 인증키」 하루 최대 1,000건 → 절반만(여유 2배). 도착정보는 지금 값이라 캐시를 짧게 둔다
    rate_seoul_subway_arrival_per_day: int = 500
    #: 확인(2026-10-08): 버스도착정보 개발계정 1,000/일. 절반을 운영 한도로 둔다.
    rate_seoul_bus_arrival_per_day: int = 500
    #: 서울 열린데이터광장 **일반 인증키**(따릉이 실시간 거치 대수 `bikeList`) 하루 상한 — ★공식 하루 한도를 못 찾았다(안내에 숫자 없음 · 「이용 제약 없이 쓰려면 활용사례 등록」)
    #:  `[미확인]` 그래서 **우리가 고른 보수적 값**이다. 이 값에서 막히면 따릉이 거치 대수가 「근거없음」이 되고(후보는 유지) 서비스는 계속된다. 0 = 제한 없음
    rate_seoul_bike_per_day: int = 1000
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
    #  ★`[2026-10-13 사용자 지시]` SerpApi(구글 호텔 · 구글 항공권 — 한 계정) — **무료 월 250회**. 월 줄 250 · 하루 줄 250 / 31 = 8.
    #    DB 로 센다(가드레일 `travel.source_budget_sources` 의 `serpapi`) — 프로세스 · 재시작을 건너 같은 줄이다. 월 · 일 중 하나라도 차면 부르지 않는다
    #    (「바꿔 줘」 한 번 = 목록 1 + 상세 최대 3, 고를 때 재확인 1).
    rate_serpapi_per_day: int = 8
    rate_serpapi_per_month: int = 250

    #: 간격이 안 찼을 때 기다려 볼 최대 시간(초). 고객 요청이 여기서 멈춘다.
    #:  넘으면 기다리지 않고 거부하고, Team 은 「모름」으로 넘어간다.
    rate_max_wait_seconds: float = 5.0

    def source_monthly_limits(self) -> dict[str, int]:
        """`TravelSource.name` -> 월 한도(알려진 것만). DB 예산(`source_budget.py`)이 월 줄로 쓴다."""
        return {name: value for name, value in {"its": self.rate_its_per_month,
                                                "serpapi": self.rate_serpapi_per_month}.items() if value and value > 0}

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
            "subway_notice": self.rate_subway_notice_per_day,
            "seoul_subway_arrival": self.rate_seoul_subway_arrival_per_day,
            "seoul_bus_arrival": self.rate_seoul_bus_arrival_per_day,
            "seoul_bike": self.rate_seoul_bike_per_day,
            "kakao": self.rate_kakao_per_day,
            "naver_search": self.rate_naver_search_per_day,
            "google_places": self.rate_google_places_per_day,
            "google_routes": self.rate_google_routes_per_day,
            "serpapi": self.rate_serpapi_per_day,
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


def dining_agent_shadow() -> dict[str, Any] | None:
    """요식 판정 에이전트 그림자 운행 설정 — 가드레일 `travel.dining.agent_shadow`. 켜져 있으면 `{model, deadline_s}`, 아니면 None. ★기본 꺼짐.

    `[2026-10-13]` 접수 확인의 규칙 검사와 나란히 판정해 기록만 한다(고객 결과는 그대로). 가드레일을 못 읽어도 **꺼짐**으로 본다.
    """
    try:
        section = get_guardrails().get("travel.dining.agent_shadow")
    except ConfigError:
        return None
    if not isinstance(section, dict) or section.get("enabled") is not True:
        return None
    return {"model": str(section.get("model") or "openai:gpt-4o-mini"), "deadline_s": float(section.get("deadline_s") or 120)}


def activity_live_check_enabled() -> bool:
    """활동 장소 임시휴무 · 폐업 새벽 웹 확인을 돌리나 — 가드레일 `travel.activity_judge.live_status_batch_enabled`. ★기본 꺼짐.

    `[2026-10-14 사용자 지시]` 유료 웹 검색이라 관리 콘솔에서 켤 때만 돈다. 가드레일을 못 읽어도 **꺼짐**으로 본다.
    """
    try:
        return get_guardrails().get("travel.activity_judge.live_status_batch_enabled") is True
    except ConfigError:
        return False


def google_price_enabled() -> bool:
    """식당 대안의 구글 가격 조회를 켰나 — 가드레일 `travel.dining.google_price_enabled`. ★기본 꺼짐.

    `[2026-10-05]` 팀 코드는 가격 조회가 켜져 있는 것을 기본으로 가정하지만, 우리는 식당을 가격으로 순위 매기지 않는다
    (9/28 결정)고 꺼 둔다. 키가 없거나 가드레일을 못 읽어도 **꺼짐**으로 본다(켜는 것은 명시한 경우뿐).
    """
    try:
        return get_guardrails().get("travel.dining.google_price_enabled") is True
    except ConfigError:
        return False


def taxi_call_product_enabled() -> bool:
    """확인 화면의 택시 호출 상품 고르기를 켰나 — 가드레일 `travel.mobility.taxi_call_product_enabled`. ★기본 꺼짐.

    `[2026-10-14 사용자 지시 · 핫픽스]` 택시 요금 계산이 믿을 만해질 때까지 끈다. 가드레일을 못 읽어도 **꺼짐**이다(켜는 것은 명시한 경우뿐).
    """
    try:
        return get_guardrails().get("travel.mobility.taxi_call_product_enabled") is True
    except ConfigError:
        return False


def trip_now_label_enabled() -> bool:
    """여행 화면의 「지금 HH:MM」 시각 꼬리표를 보이나 — 가드레일 `travel.trip_screen.now_label_enabled`. ★기본 켜짐.

    `[2026-10-15 사용자 결정 — ST4F-494]` 시험 기간에 확인하려고 켜 두고 배포 때 관리 콘솔에서 끈다. 보이는 글자 하나라
    가드레일을 못 읽으면 정한 기본(켜짐)으로 둔다 — 끄는 것은 `false` 로 명시한 경우뿐이다.
    """
    try:
        return get_guardrails().get("travel.trip_screen.now_label_enabled") is not False
    except ConfigError:
        return True
