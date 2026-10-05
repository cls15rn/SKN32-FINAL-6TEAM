"""멈춘 Case 를 되잡는다: python -m scripts.run_sweepers --once

    --once      한 번 돌고 끝난다(cron·수동 실행용). 기본값이다.
    --interval  N 초마다 되돌린다(상주 실행용).
    --only      classifying | routing | trip | trip_cases | trip_dawn | trip_reminders | trip_places 중 하나만 돌린다.

★`trip_dawn` 은 **새벽 식당 영업 확인**이다(`[2026-09-25]`, D-020). 03:00~08:00 창 안에서만 구글 장소로
  그날 식사 일정이 계획한 시각에 여는지 보고, 안 열면 같은 판정 문으로 바꾸거나 묻는다. 항목·날짜마다
  한 번만 부른다. ★키(`ACOP_GOOGLE_MAPS_API_KEY`)가 비어 있으면 `disabled` 로 답한다. 하루 시작 알림
  **앞에** 돈다 — 새벽에 고친 것을 하루 시작 알림이 싣는다.

★`trip_reminders` 는 **일정 안내**(v11 §6-B ②하루 시작 · ③항목 출발)다(`[2026-09-18]`).
  Case 를 만들지 않고 LLM 을 부르지 않는다. 최신 일정 버전을 읽어 때가 된 안내를 바깥함에
  넣는다 — 같은 안내는 `outbox` UNIQUE 가 한 번만 받는다. 감시 뒤에 돈다.

★`trip_cases` 는 **Case 버전의 감시 루프**다(`[2026-09-17]`). 점검은 `trip` 과 같고, 깨진
  항목을 직접 고치지 않고 **시스템 Case 를 열어** Controller → Team → 코어 적용으로 보낸다
  (v11 §6-A ① 「되잡기 작업은 Case 를 만들기만 한다」). `trip` 과 **함께 돌리지 않는다** —
  같은 사건을 두 경로가 다룬다.
  ★`[결정 2026-09-18]` **`--only` 없이 돌 때 들어가는 감시는 이쪽이다**(전에는 `trip`).
  실제 gemma4:12b 로 하루를 3회 돌려 감시 Case 3건이 매번 `resolved` 였다
  (wiki `records/evidence/CASE-VERSION-ITINERARY_이식검증.md` §7). `trip` 은 `--only trip` 으로 남는다.

★`trip` 은 v11 §6-A 의 **감시 루프**의 시나리오용 여행 버전이다(되잡기 작업 넷째, `--only trip`). 앞으로 90분 안에 시작할
  일정 항목을 실제 소스(기상·특보·재난문자·교통·대기)로 점검하고, 깨졌으면 새 일정
  버전 + 통지를 한 트랜잭션으로 쓴다. 보내는 일은 배달 루프(`run_outbox_worker`)다.
  `fatal`(결정 15 — 대체 소스까지 실패)은 고치지 않고 세어 알린다 → `--once` 면 exit 1.
  경로 사건: **도로 통제는 UTIC 가 답한다**(2026-09-14). `[미구현]` 지하철 무정차는
  실시간 소스가 없다 — 그 대상은 `unchecked` 로 센다(「사건 없음」이라 하지 않는다).

★두 sweeper 는 경계를 나누며 생긴 틈을 막는 장치다:

    분류를 Case 생성 트랜잭션 밖으로  →  `classifying` 잔류
    실행을 접수 응답 뒤로              →  `routing` 잔류

  자세한 경위는 `wiki/records/reports/2026-09-03_경계를_나누며_생긴_틈과_승인이_막혀있던_제안.md`.

★**돌리는 주기는 이 파일이 정하지 않는다.** 임계값은
  `config/guardrails.yaml` 의 `reliability.*_stuck_after_seconds` 이고, 얼마나
  자주 부를지는 운영이 정한다. 기본 `--once` 인 이유가 그것이다 — 상주 루프를
  기본으로 두면 "언제 도는지" 가 코드에 숨는다.

★출력은 JSON 한 줄이다. `run_daily_feedback` 과 같은 관례이며, 세는 칸을 그대로
  낸다 — 특히 `errored` 는 **아무것도 기록하지 못한** 수라서 다음 회차에 또
  걸린다. 0 이 아니면 사람이 봐야 한다.

★**그 "사람이 봐야 한다" 를 실제로 전달한다**(2026-09-07). 전에는 세어서 찍기만
  하고 **exit 0** 이었다 — cron 에 걸어 두면 실패가 로그 속에만 남아 아무도 안
  본다. 세는 것과 알리는 것은 다르다(`CLAUDE.md` §3).

    --once      `errored` 가 있으면 **exit 1**. cron 이 실패로 본다
    --interval  **죽지 않는다.** 상주 sweeper 가 첫 실패에 멈추면 되잡기 자체가
                멈춘다 — 대신 stderr 로 알리고 계속 돈다

  어느 쪽이든 사유는 **stderr** 로 나간다. stdout 은 JSON 한 줄이라는 계약을
  지켜야 파이프로 받아 쓰는 쪽이 안 깨진다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from app.application.classification_sweeper import sweep_stuck_classifying
from app.application.routing_sweeper import sweep_stuck_routing
from app.core.settings import get_settings
from app.infrastructure.db.session import get_connection


def _run_once(tenant_id: str, only: str | None) -> dict[str, dict[str, int]]:
    from app import composition

    result: dict[str, dict[str, int]] = {}
    if only in (None, "classifying"):
        classifier = composition.build_classifier()
        with get_connection() as conn:
            result["classifying"] = sweep_stuck_classifying(
                conn, tenant_id=tenant_id, classifier=classifier, actor_id="sweeper")
    if only in (None, "routing"):
        controller = composition.build_controller()

        def run_case(*, tenant_id: str, case_id, actor_id: str):
            # ★Controller 의 run_case 는 coroutine 이다. sweeper 는 동기 루프라
            #   여기서 돌려 준다 — sweeper 가 asyncio 를 알 필요가 없다.
            import asyncio

            return asyncio.run(controller.run_case(
                tenant_id=tenant_id, case_id=case_id, actor_id=actor_id))

        with get_connection() as conn:
            result["routing"] = sweep_stuck_routing(
                conn, tenant_id=tenant_id, run_case=run_case, actor_id="sweeper")
    # ★`[결정 2026-09-18]` 기본 감시는 **Case 버전**(`trip_cases`)이다. 시나리오용 여행 버전(`trip`)은
    #   `--only trip` 으로만 돈다. 둘을 함께 돌리지 않는다 — 같은 사건을 두 경로가 다룬다.
    if only == "trip":
        result["trip"] = _run_trip_watch(tenant_id)
    if only in (None, "trip_cases"):
        result["trip_cases"] = _run_trip_watch_cases(tenant_id)
    # ★새벽 식당 확인 — 창(03:00~08:00) 밖이면 아무것도 안 부른다. 하루 시작 알림보다 **먼저** 돈다
    if only in (None, "trip_dawn"):
        result["trip_dawn"] = _run_trip_dawn(tenant_id)
    # ★감시 **뒤에** 돈다 — 변경 통지가 먼저 나가고, 안내는 바뀐 최신 일정으로 계산된다(v11 §6-B).
    if only in (None, "trip_reminders"):
        result["trip_reminders"] = _run_trip_reminders(tenant_id)
    # ★끝난 여행의 전용 장소 행(029)에서 외부 서비스 값(좌표·식별자)을 비운다 — 약관, `trip_places.py` 머리
    if only in (None, "trip_places"):
        result["trip_places"] = _run_trip_places(tenant_id)
    # ★활동 재난문자 감시(`activities` 테이블 기반, 시작 3시간 전부터 활동마다 5분 간격) — 방향 검토 중인 임시 배선
    if only in (None, "activity_disaster"):
        result["activity_disaster"] = _run_activity_disaster(tenant_id)
    return result


def _run_activity_disaster(tenant_id: str) -> dict[str, int]:
    from app.infrastructure.travel.base import build_travel_sources
    from app.modules.travel_ops.activity.watch_runner import run_activity_disaster

    return run_activity_disaster(connection_factory=get_connection, tenant_id=tenant_id,
                                 sources=build_travel_sources(get_settings()))


def _run_trip_places(tenant_id: str) -> dict[str, object]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.core.settings import get_guardrails
    from app.modules.travel_ops.trip_places import scrub_ended

    with get_connection() as conn:
        return scrub_ended(conn, tenant_id=tenant_id, now=datetime.now(ZoneInfo("Asia/Seoul")),
                           retention_hours=float(get_guardrails().get("travel.trip_place_retention_hours")))


def _run_trip_dawn(tenant_id: str) -> dict[str, object]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.core.settings import get_guardrails
    from app.infrastructure.travel.google_places import GooglePlaces
    from app.modules.travel_ops.dawn_check import DawnCheck
    from app.modules.travel_ops.itinerary import TripStore

    from app.infrastructure.travel.base import build_travel_sources

    settings = get_settings()
    key = settings.google_maps_api_key
    # ★하루 호출 상한(`ACOP_RATE_GOOGLE_PLACES_PER_DAY`)을 **같은 제한기**로 건다 — 넘으면 부르지 않고
    #   `rate_limited` 로 세며, 그 항목은 `fatal` 로 남아 창 안에서 다시 시도된다(요금이 새지 않게).
    from app.infrastructure.travel.call_budget import CallBudget, google_caps

    # ★무료 한도 보호 — DB 예산(027)을 **반드시** 건다. 프로세스 안 제한기는 매분 새로 차서 못 지킨다
    budget = CallBudget(connection_factory=get_connection, caps=google_caps())
    # ★`[2026-09-30]` 대체 식당의 가격대 조회는 상한 없이 부른다 — 무료 한도를 넘는 첫 호출에 운영자에게 알린다
    from app.infrastructure.notify.ops_alert import google_over_free_alert

    source = (GooglePlaces(api_key=key, budget=budget, limiter=build_travel_sources(settings).limiter,
                           match_radius_m=float(get_guardrails().get("travel.dawn_check.match_radius_m")),
                           on_over_free=google_over_free_alert)
              if key else None)
    outcome = DawnCheck(store=TripStore(tenant_id), connection_factory=get_connection,
                        clock=lambda: datetime.now(ZoneInfo("Asia/Seoul")), source=source).tick()
    return outcome.counts()


def _run_trip_reminders(tenant_id: str) -> dict[str, int]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.infrastructure.travel.base import build_travel_sources
    from app.modules.travel_ops.itinerary import TripStore
    from app.modules.travel_ops.trip_api import plan_url
    from app.modules.travel_ops.trip_reminders import TripReminders

    sources = build_travel_sources(get_settings())
    reminders = TripReminders(store=TripStore(tenant_id), connection_factory=get_connection,
                              clock=lambda: datetime.now(ZoneInfo("Asia/Seoul")),
                              route_events=sources.route_events,
                              link=lambda trip_id: plan_url(tenant_id, trip_id))
    outcome = reminders.tick()
    return {"trips": outcome.trips, "sent": len(outcome.sent), "already": outcome.already,
            "held": len(outcome.held), "fatal": len(outcome.fatal), "no_route": outcome.no_route}


def _run_trip_watch_cases(tenant_id: str) -> dict[str, int]:
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app import composition
    from app.infrastructure.db import repository
    from app.infrastructure.travel.base import build_travel_sources
    from app.infrastructure.travel.disruptions import DisruptionCheck
    from app.modules.travel_ops.itinerary import TripStore
    from app.modules.travel_ops.trip_watch_cases import TripWatchCaseOpener

    sources = build_travel_sources(get_settings())
    controller = composition.build_controller()

    def run_case(**kwargs):
        # ★Controller 의 run_case 는 coroutine 이다 — sweeper 는 동기 루프라 여기서 돌린다.
        return asyncio.run(controller.run_case(**kwargs))

    opener = TripWatchCaseOpener(
        store=TripStore(tenant_id), check=DisruptionCheck(sources).check,
        connection_factory=get_connection, clock=lambda: datetime.now(ZoneInfo("Asia/Seoul")),
        repository=repository, run_case=run_case, route_events=sources.route_events)
    outcome = opener.tick()
    escalated = sum(1 for run in outcome.ran if run.get("status") == "escalated")
    return {"checked": outcome.checked, "opened": len(outcome.opened), "existing": len(outcome.existing),
            "ran": len(outcome.ran), "escalated": escalated, "fatal": len(outcome.fatal),
            "unhandled": len(outcome.unhandled), "pinned": len(outcome.pinned),
            "unchecked": len(outcome.unchecked)}


def _run_trip_watch(tenant_id: str) -> dict[str, int]:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.infrastructure.travel.base import build_travel_sources
    from app.infrastructure.travel.disruptions import DisruptionCheck
    from app.modules.travel_ops.itinerary import TripStore
    from app.modules.travel_ops.trip_watch import TripWatcher

    sources = build_travel_sources(get_settings())
    watcher = TripWatcher(
        store=TripStore(tenant_id),
        check=DisruptionCheck(sources).check,
        connection_factory=get_connection,
        clock=lambda: datetime.now(ZoneInfo("Asia/Seoul")),
        # ★도로 통제는 UTIC 가 답한다. 지하철 무정차는 소스가 없어 `unchecked` 로 센다.
        route_events=sources.route_events)
    outcome = watcher.tick()
    return {"checked": outcome.checked, "adjusted": len(outcome.adjusted),
            "fatal": len(outcome.fatal), "unresolved": len(outcome.unresolved),
            "unhandled": len(outcome.unhandled), "pinned": len(outcome.pinned),
            "unchecked": len(outcome.unchecked)}


def _report_errors(result: dict[str, dict[str, int]]) -> int:
    """`errored` 를 stderr 로 알리고 총합을 돌려준다.

    ★`errored` 와 `failed` 는 다르다. `failed` 는 실패를 **기록까지 한** 것이라
      Case 가 escalated 로 넘어가 사람 손에 들어간다. `errored` 는 아무것도
      기록하지 못한 것이라 Case 가 그 상태에 그대로 남고 **다음 회차에 또 걸린다** —
      아무도 안 보면 영원히 돈다.
    """
    total = 0
    for name, counts in sorted(result.items()):
        # ★결정 15 — 대체 소스까지 실패한 치명은 **사람이 봐야 한다.** 세기만 하고 넘기지 않는다.
        fatal = int(counts.get("fatal", 0))
        if fatal:
            total += fatal
            print(f"★{name} sweeper: fatal={fatal} — 소스가 대체까지 실패해 판정하지 못한 "
                  f"일정 항목이 있다(결정 15)", file=sys.stderr, flush=True)
        errored = int(counts.get("errored", 0))
        if errored:
            total += errored
            print(f"★{name} sweeper: errored={errored} · scanned={counts.get('scanned')} "
                  f"— 아무것도 기록하지 못했다. 다음 회차에 또 걸린다",
                  file=sys.stderr, flush=True)
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", default=True)
    parser.add_argument("--interval", type=int, default=None,
                        help="N 초마다 반복한다. 주면 --once 를 덮는다")
    parser.add_argument("--only", choices=("classifying", "routing", "trip", "trip_cases", "trip_dawn",
                                           "trip_reminders", "trip_places", "activity_disaster"),
                        default=None)
    args = parser.parse_args()

    tenant_id = get_settings().tenant_id
    if args.interval is None:
        result = _run_once(tenant_id, args.only)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        # ★한 번 돌고 끝나는 모드는 exit code 가 유일한 신호다. cron 이 이걸 본다.
        return 1 if _report_errors(result) else 0

    # ★상주 모드에서도 한 회차의 결과를 그때그때 낸다. 다 끝나고 모아 내면
    #   중간에 죽었을 때 아무 기록도 안 남는다.
    while True:
        result = _run_once(tenant_id, args.only)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        # ★여기서는 **끝내지 않는다.** 상주 sweeper 가 첫 실패에 멈추면 되잡기
        #   자체가 멈춘다 — 멈춘 Case 를 되잡는 장치가 멈추는 것이 더 나쁘다.
        _report_errors(result)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
