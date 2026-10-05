# -*- coding: utf-8 -*-
"""성립 판정 — 이 시각에 이 활동이 성립하는가(운영시간·재난문자·기상·대체 장소).

★`team.py` 에서 옮겼다(동작 변경 없음). 계산으로만 판정하고 LLM 을 부르지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.contracts import NextAction, TeamResult, TeamTask

from . import failure_codes as fc
from .csv_places import CsvPlaceLookup as _CsvPlaceLookup
from .csv_places import weather_sensitive_from_lclssystm2 as _ws_from_lclssystm2

_csv_lookup = _CsvPlaceLookup()


@dataclass
class _Check:
    """한 번의 성립 판정이 단계(운영시간 · 재난 · 기상 · 대체 장소)를 지나며 함께 쌓는 값."""

    task: TeamTask
    booking: dict
    place: dict
    seen: set[str]
    evidence: list
    answer_parts: list[str]
    decisions: dict[str, Any] = field(default_factory=lambda: {"feasible": True, "place_confirmed": True})
    warnings: list[str] = field(default_factory=list)

    @property
    def lat(self) -> Any:
        return self.place.get("latitude") if isinstance(self.place, dict) else None

    @property
    def lng(self) -> Any:
        return self.place.get("longitude") if isinstance(self.place, dict) else None


class FeasibilityMixin:
    #: 날씨가 판정에 들어가는 활동만 기상을 본다. ★실내 활동에 기상 감시를 걸면
    #:  감시 소스가 둘로 갈려 Team 경계가 흐려진다(v10 §5 「객체 종류별로 나눈다」).
    #:  이 판단은 `read.place` 가 돌려주는 속성으로 하고, 모르면 **보지 않는다**.
    _WEATHER_SENSITIVE_KEY = "weather_sensitive"

    def _check_feasible(self, task: TeamTask, booking: dict, policy: Any,  # noqa: ARG002
                        remaining: float | None, evidence: list, seen: set[str]) -> TeamResult:
        early = self._feasible_early_exit(task, booking, remaining, evidence)
        if early is not None:
            return early

        place = self._read(task, "read.place", {"place_id": booking.get("place_id")}, seen)
        evidence = self._evidence(task, source_id="read.place",
                                  claim="장소·운영 정보", value=place, base=evidence)

        # ★결함 1 수정(2026-09-21): 장소를 모르면 성립을 단정하지 않는다.
        if place is None:
            return self._result(
                task, outcome="completed", confidence=0.5, evidence=evidence,
                next_action=NextAction.RESPOND,
                answer="장소·운영 정보가 확인되지 않아 판정하지 않았습니다.",
                decisions=[{"feasible": False, "status": "insufficient_info", "place_confirmed": False,
                            "failure_code": self._record_failure(task, fc.PLACE_UNKNOWN)}],
                warnings=["장소·운영 정보를 확인하지 못했다"])

        ck = _Check(task=task, booking=booking, place=place, seen=seen, evidence=evidence,
                    answer_parts=[f"확인한 범위에서는 성립합니다(시작까지 {remaining:.1f}시간)."])
        self._feasible_operating(ck)
        self._feasible_disaster(ck)
        self._feasible_weather(ck)
        self._feasible_alternatives(ck)

        return self._result(
            task, outcome="completed", confidence=0.8, evidence=ck.evidence,
            next_action=NextAction.RESPOND, answer=" ".join(ck.answer_parts),
            decisions=[ck.decisions], warnings=ck.warnings)

    def _feasible_early_exit(self, task: TeamTask, booking: dict, remaining: float | None,
                             evidence: list) -> TeamResult | None:
        """장소를 읽기 전에 끝나는 갈래 — 이미 시작됨 · 정원 초과 · 시각 모름. 해당 없으면 `None`."""
        # ★결함 2 수정(2026-09-21): 이미 시작된 예약은 성립 판정 없이 즉시 반환.
        #   `remaining is None` = 시각을 모른다(아래 「정보 부족」에서 다룬다).
        if remaining is not None and remaining < 0:
            return self._result(
                task, outcome="completed", confidence=1.0, evidence=evidence,
                next_action=NextAction.RESPOND,
                answer=f"이미 시작됐거나 종료된 활동입니다 — {-remaining:.1f}시간 전에 시작됐습니다.",
                decisions=[{"feasible": False, "status": "problem", "reason": "already_started",
                            "failure_code": self._record_failure(task, fc.ALREADY_STARTED),
                            "hours_elapsed": round(-remaining, 1)}])

        party = booking.get("party_size")
        capacity = booking.get("capacity")
        if party is not None and capacity is not None and party > capacity:
            return self._result(
                task, outcome="completed", confidence=1.0, evidence=evidence,
                next_action=NextAction.RESPOND,
                answer=f"인원이 정원을 넘습니다 — 신청 {party}명, 정원 {capacity}명.",
                decisions=[{"feasible": False, "status": "problem", "reason": "party_over_capacity",
                            "failure_code": self._record_failure(task, fc.PARTY_OVER_CAPACITY)}])

        # ★시각을 모르면 휴무 요일·운영시간·재난·기상 어느 것도 잴 수 없다 — 「모름」을 「성립」으로 읽지 않는다.
        #   정원 초과처럼 시각 없이 확인된 불가(위)는 이미 `problem` 으로 나갔다. 대체 장소는 찾지 않는다.
        if remaining is None:
            return self._result(
                task, outcome="completed", confidence=0.5, evidence=evidence,
                next_action=NextAction.RESPOND,
                answer="예약 시각을 확인하지 못해 성립 여부를 판정하지 않았습니다.",
                decisions=[{"feasible": False, "status": "insufficient_info", "reason": "time_unknown",
                            "failure_code": self._record_failure(task, fc.TIME_UNKNOWN)}],
                warnings=["예약 시각을 확인하지 못했다"])
        return None

    # ── ① 운영시간 ─────────────────────────────────────────
    def _feasible_operating(self, ck: "_Check") -> None:
        operating = ck.place.get("operating") if isinstance(ck.place, dict) else None
        if operating is None:
            return
        usetime = operating.get("usetime_text")
        restdate = operating.get("restdate_text")
        wm = self._weekday_closure_match(restdate, ck.booking.get("starts_at"))
        ck.decisions["operating"] = {
            "usetime_text": usetime,
            "restdate_text": restdate,
            "weekday_match": wm,
            "source": operating.get("source"),
            "confirmed_at": operating.get("confirmed_at"),
        }
        ck.evidence = self._evidence(ck.task, source_id="read.place.operating",
                                     claim="운영시간 원문", value=operating, base=ck.evidence)
        if not usetime and not restdate:
            ck.answer_parts.append("운영시간 정보를 받지 못해 판정에 넣지 않았습니다.")
        elif wm is True:
            ck.decisions["feasible"] = False
            ck.answer_parts.append(
                f"다만 정기휴무 요일({restdate})에 해당합니다."
                " 공휴일과 겹치는 경우 등 예외가 있을 수 있습니다.")
            ck.warnings.append("운영시간 정기휴무 요일 일치 — 예외 조건은 반영하지 않았다")
        else:
            parts = []
            if usetime:
                parts.append(f"운영시간 {usetime}")
            if restdate:
                parts.append(f"휴무 {restdate}")
            if parts:
                ck.answer_parts.append(
                    f"TourAPI 기준 {' / '.join(parts)}. 원문 그대로이며 자동 해석하지 않았습니다.")
            ck.warnings.append("TourAPI 운영시간 원문은 자동 판정에 쓰지 않았다 — 원문으로 전달")

    # ── ② 재난문자 ─────────────────────────────────────────
    def _feasible_disaster(self, ck: "_Check") -> None:
        disaster = self._read(ck.task, "read.disaster",
                              {"latitude": ck.lat, "longitude": ck.lng,
                               "starts_at": ck.booking.get("starts_at")}, ck.seen)
        if disaster is None:
            return
        ck.evidence = self._evidence(ck.task, source_id="read.disaster",
                                     claim="재난문자", value=disaster, base=ck.evidence)
        messages = disaster.get("for_region") or []
        blocks = any(m.get("step") == "위급재난" for m in messages)
        ck.decisions["disaster"] = {
            "messages": messages,
            "blocks": blocks,
            "confirmed_at": disaster.get("confirmed_at"),
            "source": disaster.get("source"),
        }
        if blocks:
            kinds = ", ".join(
                m.get("kind", "") for m in messages
                if m.get("step") == "위급재난")
            ck.decisions["feasible"] = False
            ck.answer_parts.append(
                f"위급재난({kinds})이 발령 중이라 이 일정은 성립하지 않습니다. "
                f"지역·주제가 이 활동과 관련 없을 수 있습니다.")
            ck.warnings.append("재난문자 위급재난 등급 확인 — 지역·주제 관련성은 확인하지 않았다")
        elif messages:
            grade = messages[0].get("step", "")
            ck.answer_parts.append(
                f"재난문자 {len(messages)}건 확인됨({grade}). 판정에 영향을 주는 등급은 아닙니다.")
        else:
            ck.answer_parts.append("확인 범위 내 재난문자는 없습니다.")

    # ── ③ 기상 ─────────────────────────────────────────────
    def _guess_weather_sensitive(self, ck: "_Check") -> tuple[bool | None, str | None]:
        """실내외를 정한다 — DB 값 → 장소명 추정 → 분류 유형 추정 순. 반환: (값, 어디서 추정했나 `title`/`category`/`None`)."""
        place = ck.place
        weather_sensitive = place.get(self._WEATHER_SENSITIVE_KEY)
        if weather_sensitive is not None:
            return weather_sensitive, None
        title = place.get("name") if isinstance(place, dict) else None
        weather_sensitive = self._weather_sensitive_from_title(title)
        if weather_sensitive is not None:
            ck.evidence = self._evidence(
                ck.task, source_id="activity.weather_sensitive_from_title",
                claim="장소명 기반 실내외 추정",
                value={"title": title, "result": weather_sensitive},
                base=ck.evidence)
            return weather_sensitive, "title"
        content_id = place.get("source_content_id") if isinstance(place, dict) else None
        csv_row = _csv_lookup.find_by_content_id(str(content_id)) if content_id else None
        if csv_row is None:
            return None, None
        lclssystm2 = csv_row.get("lclsSystm2")
        ws = _ws_from_lclssystm2(lclssystm2)
        if ws is None:
            return None, None
        ck.evidence = self._evidence(
            ck.task, source_id="activity.weather_sensitive_from_lclssystm2",
            claim="분류 유형 기반 실내외 추정",
            value={"lclsSystm2": lclssystm2, "result": ws},
            base=ck.evidence)
        return ws, "category"

    def _feasible_weather(self, ck: "_Check") -> None:
        weather_sensitive, guessed_from = self._guess_weather_sensitive(ck)
        if weather_sensitive is not True:
            return
        forecast = self._read(ck.task, "read.weather",
                              {"latitude": ck.lat, "longitude": ck.lng,
                               "starts_at": ck.booking.get("starts_at")}, ck.seen)
        if forecast is None:
            return
        ck.evidence = self._evidence(ck.task, source_id="read.weather",
                                     claim="기상 예보", value=forecast, base=ck.evidence)
        note, advisories = self._weather_note(forecast)
        ck.answer_parts.append(note)
        ck.warnings.extend(advisories)
        w_dec: dict[str, Any] = {
            "matched_hour": forecast.get("matched_hour"),
            "precipitation_probability": forecast.get("precipitation_probability"),
            "wind_speed_kmh": forecast.get("wind_speed_kmh"),
            "source": forecast.get("source"),
            "fell_back_from": forecast.get("fell_back_from", []),
            "confirmed_at": forecast.get("confirmed_at"),
        }
        if guessed_from == "title":
            w_dec["weather_sensitive_guessed_from_title"] = True
            ck.warnings.append(
                "장소명으로 추정한 실내외 여부로 기상을 조회했다 — 확정 정보가 아닐 수 있다")
        elif guessed_from == "category":
            w_dec["weather_sensitive_guessed_from_category"] = True
            ck.warnings.append(
                "분류 유형(lclsSystm2)으로 추정한 실내외 여부로 기상을 조회했다 — 확정 정보가 아닐 수 있다")
        ck.decisions["weather"] = w_dec

    # ── ④ 대체 장소 — 이 장소가 **장소 때문에** 안 될 때만(휴무 요일 · 위급재난) ──
    def _feasible_alternatives(self, ck: "_Check") -> None:
        decisions = ck.decisions
        decisions["status"] = "ok" if decisions["feasible"] else "problem"
        # ★장소 때문에 불가일 때만 코드를 단다. 둘이 겹치면 위급재난이 앞선다(대체 장소 `withheld` 와 같은 우선순위).
        if (decisions.get("disaster") or {}).get("blocks"):
            decisions["failure_code"] = self._record_failure(ck.task, fc.DISASTER_BLOCKS)
        elif (decisions.get("operating") or {}).get("weekday_match") is True:
            decisions["failure_code"] = self._record_failure(ck.task, fc.CLOSED_WEEKDAY)
        if decisions["feasible"] is False and self._blocked_by_place(decisions):
            alt, ck.evidence, alt_text, alt_warnings = self._recommend_alternatives(
                ck.task, ck.place, ck.booking.get("starts_at"), ck.seen, ck.evidence, decisions)
            decisions["alternatives"] = alt
            if alt_text:
                ck.answer_parts.append(alt_text)
            ck.warnings.extend(alt_warnings)

    @staticmethod
    def _weekday_closure_match(restdate_text: str | None, starts_at: Any) -> bool | None:
        """"매주 X 휴무" 패턴이 starts_at 요일과 일치하면 True, 패턴 없으면 False, 텍스트 없으면 None."""
        if restdate_text is None:
            return None
        _KO_DAYS = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3,
                    "금요일": 4, "토요일": 5, "일요일": 6}
        day_names = re.findall(r"매주\s+(\S+?)\s*(?:휴무|휴관)", restdate_text)
        if not day_names:
            return False
        if not isinstance(starts_at, datetime):
            return None
        weekday = starts_at.weekday()
        return any(_KO_DAYS.get(d) == weekday for d in day_names)
