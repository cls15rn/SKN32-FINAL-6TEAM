# -*- coding: utf-8 -*-
"""여행 일정 저장소 — Trip · 일정 버전 · 항목 (v11 §7-B · §6-C-6·7).

★**일정은 버전으로 쌓는다.** 바꿀 때 항목을 고치지 않고 새 버전을 통째로 쓴다.
  되돌림은 옛 버전을 다시 쓰는 것이다.

★★**낡은 쓰기를 거부한다**(§6-C-7). 새 버전은 `trips.latest_version = 기준 버전`
  조건으로만 올린다. 그 사이 다른 쪽이 먼저 올렸으면 `StaleItinerary` 다 —
  옛 기준으로 계산한 후보를 그대로 밀어 넣지 않는다.

★**저장과 통지를 같은 트랜잭션에 넣는다**(§6-C-6). 버전·포인터·바깥함 삽입을 함께
  커밋한다. 호출자가 트랜잭션을 쥔다 — `transition_case()` 와 같은 모양이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any
from uuid import UUID, uuid4


class StaleItinerary(RuntimeError):
    """기준 버전이 낡았다 — 다른 쪽이 먼저 일정을 바꿨다."""


@dataclass
class Item:
    item_id: UUID
    seq: int
    kind: str
    title: str
    place_id: UUID | None
    starts_at: datetime
    ends_at: datetime | None
    locked: bool = False
    booking_id: UUID | None = None
    replaces_item_id: UUID | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    place: dict[str, Any] | None = None      # 읽을 때 붙는 장소(판정용)

    def replaced_by(self, *, place: dict[str, Any] | None, title: str,
                    detail: dict[str, Any] | None = None, starts_at: datetime | None = None,
                    ends_at: datetime | None = None) -> "Item":
        """★같은 순서의 대체 항목. **원래 항목을 가리킨다**(§6-C-2 — 편법 방지).

        장소가 없으면(이동 항목) 원래 장소 칸을 그대로 둔다. 시각을 안 주면 그대로다.
        """
        return Item(item_id=uuid4(), seq=self.seq, kind=self.kind, title=title,
                    place_id=UUID(str(place["place_id"])) if place else self.place_id,
                    starts_at=starts_at or self.starts_at,
                    ends_at=ends_at if ends_at is not None else self.ends_at,
                    locked=False, booking_id=None, replaces_item_id=self.item_id,
                    detail=detail if detail is not None else dict(self.detail),
                    place=place if place else self.place)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _uuid(value: Any) -> UUID | None:
    return None if value in (None, "") else UUID(str(value))


def item_to_dict(item: Item) -> dict[str, Any]:
    """항목을 JSON 으로 — 읽기 도구의 결과와 제안 인자(`itinerary.apply`)가 이 모양을 쓴다."""
    return {"item_id": str(item.item_id), "seq": item.seq, "kind": item.kind, "title": item.title,
            "place_id": str(item.place_id) if item.place_id else None,
            "starts_at": _iso(item.starts_at), "ends_at": _iso(item.ends_at),
            "locked": item.locked, "booking_id": str(item.booking_id) if item.booking_id else None,
            "replaces_item_id": str(item.replaces_item_id) if item.replaces_item_id else None,
            "detail": item.detail, "place": item.place}


def item_from_dict(data: dict[str, Any]) -> Item:
    """`item_to_dict` 의 반대. ★시각은 오프셋이 붙은 ISO 문자열이어야 한다 — 없으면 거부한다."""
    starts = datetime.fromisoformat(str(data["starts_at"]))
    ends = datetime.fromisoformat(str(data["ends_at"])) if data.get("ends_at") else None
    for moment in (starts, ends):
        if moment is not None and moment.tzinfo is None:
            raise ValueError("itinerary item time must carry a UTC offset")
    return Item(item_id=UUID(str(data["item_id"])), seq=int(data["seq"]), kind=str(data["kind"]),
                title=str(data["title"]), place_id=_uuid(data.get("place_id")), starts_at=starts,
                ends_at=ends, locked=bool(data.get("locked", False)),
                booking_id=_uuid(data.get("booking_id")),
                replaces_item_id=_uuid(data.get("replaces_item_id")),
                detail=dict(data.get("detail") or {}), place=data.get("place"))


PLACE_COLUMNS = ("place_id", "name", "kind", "latitude", "longitude",
                 "weather_sensitive", "attributes")


def _place(row: tuple) -> dict[str, Any]:
    place = dict(zip(PLACE_COLUMNS, row))
    place["place_id"] = str(place["place_id"])
    attributes = place.get("attributes") or {}
    # ★판정이 바로 쓰는 속성은 위로 올린다 — 점검은 `district` 를 장소에서 읽는다.
    return {**attributes, **place, "attributes": attributes}


def visible_to(places: list[dict[str, Any]], trip_id: Any) -> list[dict[str, Any]]:
    """`places(every_trip=True)` 에서 한 여행이 볼 수 있는 것 — 공용 + 그 여행 전용."""
    return [p for p in places if p.get("trip_scope") in (None, str(trip_id))]


class TripStore:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    # ── 만들기 ──────────────────────────────────────────────────
    def create_trip(self, conn, *, customer_id: UUID, title: str, locale: str | None,
                    party_size: int | None, items: list[Item],
                    constraints: dict[str, Any] | None = None,
                    request_key: str | None = None,
                    request_sha256: str | None = None,
                    trip_id: UUID | None = None) -> tuple[UUID, int]:
        """★`trip_id` 를 미리 정해 넘길 수 있다 — 그 여행 전용 장소 행을 여행보다 먼저 넣을 때(029)."""
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trips (trip_id, tenant_id, customer_id, title, locale, party_size, constraints, "
                "request_key, request_sha256) VALUES (COALESCE(%s, gen_random_uuid()),%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING trip_id",
                (trip_id, self.tenant_id, customer_id, title, locale, party_size,
                 json.dumps(constraints or {}, ensure_ascii=False), request_key, request_sha256))
            trip_id = cur.fetchone()[0]
        version = self.append_version(conn, trip_id=trip_id, base_version=0, items=items,
                                      reason="created", causes=[])
        return trip_id, version

    def by_request_key(self, conn, request_key: str) -> tuple[UUID, str | None] | None:
        """같은 등록 요청으로 이미 만든 여행 — `(trip_id, 몸통 지문)`."""
        with conn.cursor() as cur:
            cur.execute("SELECT trip_id, request_sha256 FROM trips WHERE tenant_id=%s "
                        "AND request_key=%s", (self.tenant_id, request_key))
            row = cur.fetchone()
        return (row[0], row[1]) if row else None

    # ── 읽기 ────────────────────────────────────────────────────
    def latest(self, conn, trip_id: UUID) -> tuple[dict[str, Any], list[Item]]:
        with conn.cursor() as cur:
            cur.execute("SELECT trip_id, customer_id, title, locale, party_size, latest_version, "
                        "constraints FROM trips WHERE tenant_id=%s AND trip_id=%s",
                        (self.tenant_id, trip_id))
            row = cur.fetchone()
        if row is None:
            raise KeyError(f"trip {trip_id} 없음")
        trip = dict(zip(("trip_id", "customer_id", "title", "locale", "party_size",
                         "version", "constraints"), row))
        return trip, self.items(conn, trip_id, trip["version"])

    def items(self, conn, trip_id: UUID, version: int) -> list[Item]:
        select = ", ".join(f"p.{c}" for c in PLACE_COLUMNS)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.item_id, i.seq, i.kind, i.title, i.place_id, i.starts_at, i.ends_at, "
                "i.locked, i.booking_id, i.replaces_item_id, i.detail, " + select + " "
                "FROM itinerary_items i LEFT JOIN places p ON p.place_id = i.place_id "
                "AND p.tenant_id = i.tenant_id "
                "WHERE i.tenant_id=%s AND i.trip_id=%s AND i.version=%s ORDER BY i.seq",
                (self.tenant_id, trip_id, version))
            rows = cur.fetchall()
        out = []
        for row in rows:
            item = Item(*row[:11])
            item.place = _place(row[11:]) if row[11] is not None else None
            out.append(item)
        return out

    def active_trip_ids(self, conn) -> list[UUID]:
        """진행 중인 여행. 안내 되잡기 작업이 읽는다."""
        with conn.cursor() as cur:
            cur.execute("SELECT trip_id FROM trips WHERE tenant_id=%s AND status='active' ORDER BY trip_id",
                        (self.tenant_id,))
            return [row[0] for row in cur.fetchall()]

    def due(self, conn, *, start: datetime, end: datetime) -> list[tuple[UUID, Item]]:
        """최신 버전에서 `[start, end)` 에 시작하는 항목. 감시 루프가 읽는다."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.trip_id, t.latest_version FROM trips t "
                "WHERE t.tenant_id=%s AND t.status='active'", (self.tenant_id,))
            trips = cur.fetchall()
        found = []
        for trip_id, version in trips:
            for item in self.items(conn, trip_id, version):
                if start <= item.starts_at < end:
                    found.append((trip_id, item))
        return found

    def versions(self, conn, trip_id: UUID) -> list[dict[str, Any]]:
        """버전 이력 — 무엇이 왜 바뀌었나. 계획서 링크와 되돌림이 읽는다."""
        with conn.cursor() as cur:
            cur.execute("SELECT version, reason, cause_json, created_at FROM itinerary_versions "
                        "WHERE tenant_id=%s AND trip_id=%s ORDER BY version",
                        (self.tenant_id, trip_id))
            return [dict(zip(("version", "reason", "causes", "created_at"), row))
                    for row in cur.fetchall()]

    def version_for_request(self, conn, trip_id: UUID, request_id: str) -> int | None:
        """★이 요청으로 이미 올린 버전. 같은 신고를 두 번 받아 두 번 고치지 않게 한다.

        ☆같은 「70분 늦음」을 다시 적용하면 이미 옮긴 점심을 **또** 70분 민다 —
          재시도가 일정을 망가뜨린다. 원인 칸에 요청 id 를 남기고 여기서 찾는다.
        """
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM itinerary_versions WHERE tenant_id=%s AND trip_id=%s "
                        "AND cause_json @> %s::jsonb ORDER BY version LIMIT 1",
                        (self.tenant_id, trip_id, json.dumps([{"request_id": request_id}])))
            row = cur.fetchone()
        return row[0] if row else None

    def places(self, conn, trip_id: UUID | None = None, *, every_trip: bool = False) -> list[dict[str, Any]]:
        """장소 목록. ★`[2026-09-27]` 외부 서비스에서 온 장소는 **그 여행 전용 행**이다(`trip_scope`, 마이그레이션 029).

        - `trip_id` 없음 → 공용 행만(일정 생성기 후보 · 에이전트 도구의 장소 목록)
        - `trip_id` 있음 → 공용 + 그 여행 전용
        - `every_trip=True` → 전부(여러 여행을 한꺼번에 도는 감시). 여행마다 `visible_to` 로 거른다
        ★다른 여행의 전용 행을 대체 후보로 쓰면 외부 값을 다른 고객에게 재사용하는 것이다(약관 — 마이그레이션 029 머리).
        """
        where, params = "", [self.tenant_id]
        if not every_trip:
            where = " AND (trip_scope IS NULL OR trip_scope = %s)" if trip_id else " AND trip_scope IS NULL"
            params += [trip_id] if trip_id else []
        with conn.cursor() as cur:
            cur.execute("SELECT " + ", ".join(PLACE_COLUMNS) + ", trip_scope FROM places WHERE tenant_id=%s"
                        + where, params)
            return [{**_place(row[:-1]), "trip_scope": str(row[-1]) if row[-1] else None}
                    for row in cur.fetchall()]

    # ── 쓰기 ────────────────────────────────────────────────────
    def adopt_places(self, conn, trip_id: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """장소들을 **그 여행 전용 행**으로 넣고(이미 있으면 그대로) 그 행들을 돌려준다. `[2026-10-01]`

        ★대체 후보를 바깥(요식 원장 등)에서 들여올 때 쓴다 — 일정 항목은 코어 장소 id 만 가리킬 수 있다.
        ★공용 행은 만들지 않는다 — 공용 목록(일정 접수 · 일정 생성기 · 다른 여행)에 섞이지 않게(029).
        ★같은 여행 안에서 (이름, 종류)가 같으면 새로 만들지 않고 있는 행을 쓴다.
        `rows` = [{"name", "kind", "latitude", "longitude", "attributes"}].
        """
        if not rows:
            return []
        with conn.cursor() as cur:
            for row in rows:
                cur.execute("INSERT INTO places (tenant_id, name, kind, latitude, longitude, attributes, trip_scope) "
                            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT DO NOTHING",
                            (self.tenant_id, row["name"], row["kind"], row["latitude"], row["longitude"],
                             json.dumps(row.get("attributes") or {}, ensure_ascii=False), str(trip_id)))
            cur.execute("SELECT " + ", ".join(PLACE_COLUMNS) + ", trip_scope FROM places "
                        "WHERE tenant_id=%s AND trip_scope=%s AND name = ANY(%s)",
                        (self.tenant_id, str(trip_id), [row["name"] for row in rows]))
            return [{**_place(found[:-1]), "trip_scope": str(found[-1])} for found in cur.fetchall()]

    def append_version(self, conn, *, trip_id: UUID, base_version: int, items: list[Item],
                       reason: str, causes: list[dict[str, Any]],
                       case_id: UUID | None = None) -> int:
        """`base_version` 위에 새 버전을 쓴다. ★기준이 낡았으면 `StaleItinerary`."""
        new_version = base_version + 1
        with conn.cursor() as cur:
            cur.execute("UPDATE trips SET latest_version=%s WHERE tenant_id=%s AND trip_id=%s "
                        "AND latest_version=%s RETURNING trip_id",
                        (new_version, self.tenant_id, trip_id, base_version))
            if cur.fetchone() is None:
                raise StaleItinerary(f"trip {trip_id}: 기준 버전 {base_version} 이 낡았다")
            cur.execute("INSERT INTO itinerary_versions (trip_id, version, tenant_id, reason, "
                        "cause_json, case_id) VALUES (%s,%s,%s,%s,%s,%s)",
                        (trip_id, new_version, self.tenant_id, reason,
                         json.dumps(causes, ensure_ascii=False, default=str), case_id))
            for item in items:
                cur.execute(
                    "INSERT INTO itinerary_items (item_id, trip_id, version, tenant_id, seq, kind, "
                    "title, place_id, starts_at, ends_at, locked, booking_id, replaces_item_id, "
                    "detail) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (item.item_id, trip_id, new_version, self.tenant_id, item.seq, item.kind,
                     item.title, item.place_id, item.starts_at, item.ends_at, item.locked,
                     item.booking_id, item.replaces_item_id,
                     json.dumps(item.detail, ensure_ascii=False, default=str)))
        return new_version

    def enqueue_message(self, conn, *, trip_id: UUID, key: str,
                        payload: dict[str, Any]) -> bool:
        """일정 버전에 딸리지 않은 안내(하루 시작·출발·하루 정리 — v11 §6-B 의 ②·③).

        ★같은 `key` 는 두 번 들어가지 않는다(`outbox` UNIQUE). 버전 통지와 키가 겹치지
          않게 `{trip_id}:{key}` 로 둔다.
        ★`[2026-09-22]` 부르는 쪽이 안 넣었으면 **계획서 링크를 여기서 붙인다** — 시나리오 모드의
          안내가 링크 없이 나가고 있었다(되잡기 작업은 넣어 주고 있었다).
        """
        if "plan_url" not in payload:
            from .plan_link import plan_url

            payload = {**payload, "plan_url": plan_url(self.tenant_id, trip_id)}
        with conn.cursor() as cur:
            cur.execute("SELECT locale FROM trips WHERE tenant_id=%s AND trip_id=%s",
                        (self.tenant_id, trip_id))
            row = cur.fetchone()
            cur.execute(
                "INSERT INTO outbox (tenant_id, topic, dedupe_key, payload_json) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (tenant_id, topic, dedupe_key) DO NOTHING",
                (self.tenant_id, "trip.notice", f"{trip_id}:{key}",
                 json.dumps({"locale": row[0] if row else None, **payload},
                            ensure_ascii=False, default=str)))
            # ★새로 넣었으면 True. 이미 있던 키면 False — 되잡기 작업이 「보냄」과 「이미 보냄」을 가른다.
            return cur.rowcount == 1

    def enqueue_notice(self, conn, *, trip_id: UUID, version: int,
                       payload: dict[str, Any]) -> None:
        """★적용된 일정 버전당 통지 하나(§6-C-6). 같은 버전을 두 번 넣으면 막힌다.

        ★여행의 언어(`locale`)를 싣는다 — 보낼 때 그 언어로 옮긴다(결정 14).
        ★`[2026-09-20]` **계획서 링크도 싣는다.** 상태의 정본은 링크인데(v11 §6-A) 변경 통지에만
          링크가 없었다 — 일정 안내(②·③)에는 붙고 ①에는 안 붙는 상태였다.
        """
        if "plan_url" not in payload:
            from .plan_link import plan_url

            payload = {**payload, "plan_url": plan_url(self.tenant_id, trip_id)}
        if "locale" not in payload:
            with conn.cursor() as cur:
                cur.execute("SELECT locale FROM trips WHERE tenant_id=%s AND trip_id=%s",
                            (self.tenant_id, trip_id))
                row = cur.fetchone()
            payload = {**payload, "locale": row[0] if row else None}
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO outbox (tenant_id, topic, dedupe_key, payload_json) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (tenant_id, topic, dedupe_key) DO NOTHING",
                (self.tenant_id, "trip.notice", f"{trip_id}:v{version}",
                 json.dumps(payload, ensure_ascii=False, default=str)))


__all__ = ["Item", "StaleItinerary", "TripStore"]
