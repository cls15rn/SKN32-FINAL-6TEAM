# -*- coding: utf-8 -*-
"""지속 감시 루프 — **멈추지 않고 보되, 부하는 교대로 나눈다.**

★★**설계 원칙 (2026-09-10 정정).**

  처음엔 「장소 정보가 8개월째 안 바뀌니 하루 1회면 된다」로 잡았다.
  **그것은 API 예산 논리였지 제품 요구가 아니었다.** 이 제품의 존재 이유는
  「8개월 만의 한 번」을 잡는 것이고, 그 한 번이 났을 때 아무도 안 보고
  있었으면 고객의 여행이 망한다. **드물수록 감시가 없으면 아무도 모른다.**

  예산은 제약이지 목표가 아니다. 그래서 순서를 뒤집는다 —
  **감시는 계속한다. 대신 부하를 줄이는 방법을 찾는다.**

★부하를 줄이는 방법 둘. 주기를 늦추는 것과 **교대(stagger)** 다.

  ① 무엇을 보는가를 좁힌다
     카탈로그 2,063건 전체를 볼 필요가 없다. **고객 계획서에 실제로 걸린
     대상**만 보면 되고 그건 훨씬 적다. 감시 대상은 예약에서 나온다.

  ② 한 바퀴를 시간에 걸쳐 나눈다
     매 틱마다 **가장 오래전에 본 것부터 N개**만 본다. 대상이 100개고 틱당
     5개면 한 바퀴가 40분(2분 틱 기준)이다. 부하는 1/20 인데 감시는 끊기지
     않는다. 자연히 교대가 되고, 대상이 늘거나 줄어도 스스로 균형이 맞는다.

★**변화는 지문으로 판정한다.** 관측 전체를 해시하면 공급자가 무관한 필드를
  건드릴 때마다 오탐이 난다. **판정에 쓰는 부분만** 뽑아 해시한다.

★★**왜 `app/modules/` 에 있나** (2026-09-10 정정).
  처음엔 `app/infrastructure/travel/watch.py` 에 뒀는데
  `tests/architecture/test_basement_is_domain_free.py` 가 잡았다 —
  이 파일은 `bookings`·`lodging`·`dining` 같은 **도메인 어휘를 쓴다.**
  basement(`app/core`·`app/domain`·`app/application`·`app/infrastructure`)는
  도메인을 몰라야 한다(`CLAUDE.md` §6).

  **가드를 끄지 않고 파일을 옮겼다.** 「무엇을 감시할지」는 도메인 지식이고
  (다가오는 예약, 장소 종류), 「어떻게 관측·비교할지」는 아니다. 후자를 쓰는
  부품(`TravelSource`·`fingerprint`)은 그대로 infrastructure 에 있고 이쪽이
  그것을 **쓴다.** 층이 거꾸로 되지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: 한 틱에 볼 대상 수. ★크게 잡으면 한 바퀴가 빨라지고 부하가 는다.
#:  소스의 하루 한도와 틱 주기에서 역산해 정한다.
DEFAULT_BATCH = 5

#: 활동 재난문자 감시 규칙 — 활동 시작 몇 시간 전부터 보나 / 같은 활동을 몇 분 간격으로 다시 보나.
#:  ★방향이 바뀌면(멘토 검토 중) 여기 두 값과 `activity/watch_runner.py` 만 고치면 된다.
ACTIVITY_WINDOW_HOURS = 3.0
ACTIVITY_GAP_MINUTES = 5.0


@dataclass
class Change:
    """감지한 변화 하나."""

    target_kind: str
    target_id: str
    source: str
    before: dict[str, Any] | None
    after: dict[str, Any]
    summary: str
    affected_bookings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        who = f" · 예약 {len(self.affected_bookings)}건" if self.affected_bookings else " · 걸린 예약 없음"
        return f"[{self.target_kind}:{self.target_id}] {self.summary}{who}"


@dataclass
class TickResult:
    checked: int = 0
    changes: list[Change] = field(default_factory=list)
    unknown: int = 0          # 소스가 「모름」을 준 것
    note: str = ""

    def __str__(self) -> str:
        return (f"검사 {self.checked} · 변화 {len(self.changes)} · 모름 {self.unknown}"
                + (f" · {self.note}" if self.note else ""))


def fingerprint(values: dict[str, Any]) -> str:
    """판정에 쓰는 값들만으로 지문을 만든다.

    ★`confirmed_at` 같은 **조회 시각은 넣지 않는다.** 넣으면 매번 달라져서
      모든 관측이 「변화」가 된다.
    """
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class TravelWatcher:
    """활성 예약에 걸린 대상을 교대로 돌며 변화를 찾는다."""

    def __init__(self, *, connection_factory: Callable[[], Any], tenant_id: str,
                 sources: Any) -> None:
        self._connect, self.tenant_id, self.sources = connection_factory, tenant_id, sources

    # ── 대상 고르기 ─────────────────────────────────────────────
    def due_places(self, limit: int = DEFAULT_BATCH) -> list[dict[str, Any]]:
        """가장 오래전에 본 장소부터. ★한 번도 안 본 것이 먼저 온다.

        ★**다가오는 예약이 걸린 장소만** 본다. 지난 여행의 장소를 보는 것은
          고객에게 아무 쓸모가 없고 한도만 태운다.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.place_id::text, p.name, p.kind,
                       p.source_content_id, p.source_content_type_id,
                       array_agg(DISTINCT b.booking_no) AS bookings,
                       max(o.observed_at) AS last_seen
                  FROM bookings b
                  JOIN places p ON p.place_id = b.place_id
                  LEFT JOIN watch_observations o
                         ON o.tenant_id = b.tenant_id
                        AND o.target_kind = 'place'
                        AND o.target_id = p.place_id::text
                 WHERE b.tenant_id = %s
                   AND b.starts_at > now()
                   AND b.status <> 'cancelled'
                 GROUP BY p.place_id, p.name, p.kind,
                          p.source_content_id, p.source_content_type_id
                 -- ★NULLS FIRST: 한 번도 안 본 것을 맨 앞에 둔다
                 ORDER BY max(o.observed_at) ASC NULLS FIRST
                 LIMIT %s
                """, (self.tenant_id, limit))
            columns = ("place_id", "name", "kind", "content_id", "content_type_id",
                       "bookings", "last_seen")
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def due_activities(self, limit: int = DEFAULT_BATCH, *,
                       within_hours: float = ACTIVITY_WINDOW_HOURS,
                       min_gap_minutes: float = ACTIVITY_GAP_MINUTES) -> list[dict[str, Any]]:
        """가장 오래전에 본 활동부터. ★한 번도 안 본 것이 먼저 온다.

        ★**활동 시작 `within_hours`(기본 3시간) 이내인 것만** 본다(wiki/teams/activity.md 「조회
          시점·재검토 주기」의 결정 그대로). 「가까운 일정만 재검토한다」는
          `due_places`의 원칙(다가오는 예약만)과 같은 이유이고, 재난문자는
          거기서 한 번 더 좁힌다 — 하루 내내 모든 활동을 5분마다 볼 수는 없다.

        ★**마지막 관측이 `min_gap_minutes`(기본 5분) 안인 활동은 건너뛴다.** 스케줄러가 몇 분마다
          부르든 활동마다 이 간격이 지켜진다(호출 시각에 기대지 않는다). 0 이면 거르지 않는다.

        ★**`place_id`가 해소된 활동만** 본다. 재난문자는 지역(좌표) 기준으로
          찾는데, `place_id IS NULL`(015 — 아직 `places`로 안 해소됨)이면
          어느 지역을 볼지 모른다. 이것도 「모름」이지 「재난 없음」이 아니라서
          여기서 조용히 걸러진다 — `tick_activities`가 `unknown`으로 센다.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT a.id::text, a.name, a.activity_time,
                       p.latitude, p.longitude,
                       max(o.observed_at) AS last_seen
                  FROM activities a
                  JOIN places p ON p.place_id = a.place_id
                  LEFT JOIN watch_observations o
                         ON o.tenant_id = a.tenant_id
                        AND o.target_kind = 'activity'
                        AND o.target_id = a.id::text
                 WHERE a.tenant_id = %s
                   AND a.activity_time > now()
                   AND a.activity_time <= now() + %s * interval '1 hour'
                 GROUP BY a.id, a.name, a.activity_time, p.latitude, p.longitude
                 HAVING max(o.observed_at) IS NULL
                     OR max(o.observed_at) <= now() - %s * interval '1 minute'
                 -- ★NULLS FIRST: 한 번도 안 본 것을 맨 앞에 둔다
                 ORDER BY max(o.observed_at) ASC NULLS FIRST
                 LIMIT %s
                """, (self.tenant_id, float(within_hours), float(min_gap_minutes), limit))
            columns = ("activity_id", "name", "activity_time", "latitude", "longitude",
                       "last_seen")
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    #: 우리 장소 종류 → 신분류체계 대분류(lclsSystm1) 집합.
    #:  `tour_api.find(allowed_large_classes=...)` 클라이언트 필터로 쓴다.
    #:
    #:  [2026-09-21 개편] 구분류(contenttypeid) 기반 KIND_TO_CONTENT_TYPES를
    #:  신분류체계로 교체. contenttypeid=12(관광지) 하나가 HS·NA·VE에 걸치므로
    #:  구분류로는 「경복궁(HS)」과 「한라산(NA)」을 구분하지 못했다.
    #:
    #:  Activity 담당: NA·HS·VE·LS·EX·SH (wiki/teams/activity.md §범위)
    KIND_TO_LARGE_CLASSES = {
        "activity": {"NA", "HS", "VE", "LS", "EX", "SH"},
        "dining": {"FD"},
        "lodging": {"AC"},
        "flight": set(),
    }

    def _resolve(self, source: Any, target: dict[str, Any]) -> dict[str, Any] | None:
        """공급자 쪽 그 장소를 집는다. 신원을 이미 알면 그걸 쓴다.

        ★★2026-09-10 결함. 매번 **이름으로** 찾았더니 「경복궁」이 타입 12/39
          둘로 나와 `ambiguous` 로 떨어졌고, 그 장소는 **영영 감시되지 않았다.**
          안전 가드(정확일치 1건일 때만 확정)가 감시 자체를 막고 있던 것이다.

          가드를 푸는 것이 답이 아니다 — 풀면 울산 음식점 좌표로 서울 궁궐의
          날씨를 답하게 된다. 답은 **신원을 한 번 해소해서 저장하고 그 다음부터
          id 로 보는 것**이다. 애매함도 없고 콜도 준다.
        """
        content_id = target.get("content_id")
        if content_id:
            # ★이미 해소된 장소. 이름이 바뀌어도 같은 것을 계속 본다.
            return source.by_content_id(
                str(content_id), str(target.get("content_type_id") or ""))

        allowed = self.KIND_TO_LARGE_CLASSES.get(str(target.get("kind") or ""))
        # ★신분류체계(lclsSystm1) 클라이언트 필터. 서버는 넓게 받는다 —
        #   lclsSystm1으로 서버 필터링이 되는지 미확인.
        found = source.find(str(target["name"]),
                            allowed_large_classes=allowed or None)
        if found is not None:
            self._remember_identity(target["place_id"], source.name, found)
        return found

    def _remember_identity(self, place_id: str, source_name: str,
                           found: dict[str, Any]) -> None:
        """해소한 신원을 남긴다. ★다음 틱부터 이름으로 헤매지 않는다."""
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "UPDATE places SET source_name=%s, source_content_id=%s, "
                "source_content_type_id=%s, source_resolved_at=now() "
                "WHERE tenant_id=%s AND place_id=%s",
                (source_name, found.get("content_id"), found.get("content_type_id"),
                 self.tenant_id, place_id))

    # ── 한 틱 ───────────────────────────────────────────────────
    def tick_places(self, limit: int = DEFAULT_BATCH) -> TickResult:
        """장소를 `limit` 개만 본다. ★한 바퀴를 시간에 걸쳐 나누는 자리다."""
        result = TickResult()
        source = getattr(self.sources, "place", None)
        if source is None:
            result.note = "장소 소스가 안 붙어 있다(키 없음). 감시할 수 없다"
            return result

        for target in self.due_places(limit):
            result.checked += 1
            found = self._resolve(source, target)
            if found is None:
                # ★「모름」을 「없어졌다」로 읽지 않는다. 이름이 애매하거나
                #   속도 제한에 걸린 것일 수도 있다. 변화로 만들지 않는다.
                result.unknown += 1
                continue

            watched = {
                "title": found.get("matched_title"),
                "latitude": found.get("latitude"),
                "longitude": found.get("longitude"),
                "address": found.get("address"),
                "content_type_id": found.get("content_type_id"),
                "large_class_code": found.get("large_class_code"),
            }
            change = self._record(
                kind="place", target_id=str(target["place_id"]),
                source=source.name, watched=watched, payload=found,
                bookings=[b for b in (target.get("bookings") or []) if b])
            if change is not None:
                result.changes.append(change)
        return result

    def tick_activities(self, limit: int = DEFAULT_BATCH, *,
                        within_hours: float = ACTIVITY_WINDOW_HOURS,
                        min_gap_minutes: float = ACTIVITY_GAP_MINUTES) -> TickResult:
        """활동 시작 3시간 이내인 것을 `limit`개만 본다. 재난문자 관련성을 본다.

        ★`[2026-09-28 병합 후속]` `DisasterMsgApi`/`DisasterMsgCsv`(`disaster_msg.py`)가
          실구현체로 붙는다. `.near()` 래퍼가 `.active()`에 위임하고 아래 모양으로 답한다:

              source.name                                    -> str
              source.near(lat, lng, *, within: datetime)      -> dict | None
                  {"for_region": [{"serial": ..., "step": ..., ...}, ...]}

          ★`serial` = 구 `SN`, `step` = 구 `EMRG_STEP_NM` — `active()` 판정 출력 필드명.
        """
        result = TickResult()
        source = getattr(self.sources, "disaster", None)
        if source is None:
            result.note = "재난문자 소스가 안 붙어 있다(키 없음). 감시할 수 없다"
            return result

        for target in self.due_activities(limit, within_hours=within_hours,
                                          min_gap_minutes=min_gap_minutes):
            result.checked += 1
            lat, lng = target.get("latitude"), target.get("longitude")
            if lat is None or lng is None:
                # ★장소는 해소됐지만 좌표가 없다 — 모름이지 재난 없음이 아니다.
                result.unknown += 1
                continue
            found = source.near(float(lat), float(lng), within=target["activity_time"])
            if found is None:
                # ★소스가 「모름」을 줬다(속도 제한 등). 재난 없음으로 넘기지 않는다.
                result.unknown += 1
                continue

            # ★지문은 메시지 신원+긴급단계만 본다. 발령·해제뿐 아니라
            #   안전안내→위급재난 같은 단계 변경도 변화로 잡는다.
            watched = {
                "active": sorted(
                    f"{m.get('serial')}:{m.get('step')}"
                    for m in found.get("for_region", [])),
            }
            # `[미확보]` 이 활동에 걸린 예약을 찾아 `affected_bookings`에 채우는
            #   일은 아직 안 한다 — 무예약 활동은 애초에 예약이 없고, 예약이
            #   있는 경우의 조회 방법(장소·시각으로 역추적)은 정해지지 않았다.
            change = self._record(
                kind="activity", target_id=str(target["activity_id"]),
                source=source.name, watched=watched, payload=found, bookings=[])
            if change is not None:
                result.changes.append(change)
        return result

    # ── 관측 저장과 비교 ────────────────────────────────────────
    def _record(self, *, kind: str, target_id: str, source: str,
                watched: dict[str, Any], payload: dict[str, Any],
                bookings: list[str]) -> Change | None:
        stamp = fingerprint(watched)
        previous = self._latest(kind, target_id)

        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watch_observations (tenant_id, target_kind, target_id, "
                "source, fingerprint, payload) VALUES (%s,%s,%s,%s,%s,%s)",
                (self.tenant_id, kind, target_id, source, stamp,
                 # ★★감시 대상(`watched`)과 원본(`raw`)을 **나눠서** 저장한다.
                 #   전에는 원본만 저장하고 다음 번에 `watched` 와 비교해서,
                 #   비교 대상이 서로 달라 무관한 필드가 전부 「→ None」으로
                 #   나왔다(2026-09-10). 요약이 못 읽을 것이 되면 사람이
                 #   무엇이 바뀌었는지 알 수 없고, 그러면 알림이 쓸모없다.
                 json.dumps({"watched": watched, "raw": payload},
                            ensure_ascii=False, default=str)))

        if previous is None:
            return None       # ★첫 관측은 변화가 아니다. 기준선일 뿐이다
        if previous["fingerprint"] == stamp:
            return None

        stored = previous["payload"] or {}
        # ★옛 형식(원본만 저장)도 읽을 수 있게 둔다 — 이미 쌓인 관측이 있다.
        before = stored.get("watched") if isinstance(stored, dict) else None
        if before is None:
            before = stored if isinstance(stored, dict) else {}
        summary = self._describe(before, watched)
        change = Change(kind, target_id, source, before, payload, summary, bookings)
        with self._connect() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watch_changes (tenant_id, target_kind, target_id, source, "
                "before_json, after_json, summary, affected_bookings) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (self.tenant_id, kind, target_id, source,
                 json.dumps(before, ensure_ascii=False, default=str),
                 json.dumps(payload, ensure_ascii=False, default=str),
                 summary, json.dumps(bookings, ensure_ascii=False)))
        logger.warning("watch change: %s", change)
        return change

    def _latest(self, kind: str, target_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT fingerprint, payload FROM watch_observations "
                "WHERE tenant_id=%s AND target_kind=%s AND target_id=%s "
                "ORDER BY observed_at DESC LIMIT 1",
                (self.tenant_id, kind, target_id))
            row = cur.fetchone()
            return None if row is None else {"fingerprint": row[0], "payload": row[1]}

    @staticmethod
    def _describe(before: dict[str, Any], after: dict[str, Any]) -> str:
        """무엇이 달라졌는지 한 줄. ★실제 필드 차이에서만 만든다."""
        changed = []
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key), after.get(key)
            if old != new:
                changed.append(f"{key}: {old!r} → {new!r}")
        return "; ".join(changed) if changed else "값은 같은데 지문이 달라졌다"


__all__ = ["Change", "DEFAULT_BATCH", "TickResult", "TravelWatcher", "fingerprint"]
