# -*- coding: utf-8 -*-
"""활동 재난문자 감시를 **한 회차** 돌린다 — `scripts/run_sweepers.py` 가 부른다.

★`TravelWatcher.tick_activities()`(watch.py)를 부르는 얇은 껍질이다. 스케줄러(sweepers 가 1분마다)가
  부르기만 하고, 「활동마다 5분 간격 · 시작 3시간 전부터」는 `watch.py` 의 상수와 쿼리가 지킨다.
★`[임시]` 감시 구조(몇 시간 전부터 / 몇 분 간격 / 무엇을 기준으로)는 멘토 검토 중이라 바뀔 수 있다.
  갈아끼울 때는 이 파일과 `watch.py` 의 상수 두 개만 고치면 되도록 로직을 여기에 모아 뒀다.
★입력은 `activities` 테이블(고객 일정)이다. 그 테이블에 쓰는 경로(승인된 `activity.submit` 반영)는
  아직 없어서 운영에서는 비어 있을 수 있다 — 그때는 `checked=0` 으로 돈다.
★소스를 못 쓰거나 한 회차가 죽어도 **다른 되잡기 작업을 막지 않는다**: 예외는 삼키지 않고 `fatal` 로 센다
  (`run_sweepers._report_errors` 가 stderr·exit 1 로 알린다).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .watch import ACTIVITY_GAP_MINUTES, ACTIVITY_WINDOW_HOURS, DEFAULT_BATCH, TravelWatcher

logger = logging.getLogger(__name__)


def run_activity_disaster(*, connection_factory: Callable[[], Any], tenant_id: str, sources: Any,
                          limit: int = DEFAULT_BATCH,
                          within_hours: float = ACTIVITY_WINDOW_HOURS,
                          min_gap_minutes: float = ACTIVITY_GAP_MINUTES) -> dict[str, int]:
    """한 회차. 세는 칸: checked · changes · unknown · no_source · fatal."""
    watcher = TravelWatcher(connection_factory=connection_factory, tenant_id=tenant_id, sources=sources)
    try:
        outcome = watcher.tick_activities(limit, within_hours=within_hours,
                                          min_gap_minutes=min_gap_minutes)
    except Exception:  # noqa: BLE001 — 삼키지 않고 센다. 소스·DB 실패를 「재난 없음」으로 읽지 않는다
        logger.exception("activity disaster watch failed")
        return {"checked": 0, "changes": 0, "unknown": 0, "no_source": 0, "fatal": 1}
    return {"checked": outcome.checked, "changes": len(outcome.changes), "unknown": outcome.unknown,
            # ★소스가 없으면 조용히 넘기지 않고 센다(`TickResult.note` 가 이유)
            "no_source": 1 if outcome.note else 0, "fatal": 0}


__all__ = ["run_activity_disaster"]
