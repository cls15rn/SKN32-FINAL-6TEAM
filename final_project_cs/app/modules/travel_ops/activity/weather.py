# -*- coding: utf-8 -*-
"""날씨 — 장소명으로 실내외를 추정하고, 예보를 **사실로만** 전하는 문구를 만든다.

★`team.py` 에서 옮겼다(동작 변경 없음). 여기서 「불가」를 만들지 않는다 — 우천 취소 기준은 운영 규정이 정한다.
"""
from __future__ import annotations

from typing import Any


class WeatherMixin:
    # ── 성립 점검 부품 ────────────────────────────────────────

    @staticmethod
    def _weather_sensitive_from_title(title: str | None) -> bool | None:
        """장소명 단서로 실내외 여부를 근사 추정한다. 단서 없거나 상충하면 `None`."""
        if not title:
            return None
        _OUTDOOR = ("공원", "옥상", "광장", "거리", "운동장", "야외", "노천", "숲")
        _INDOOR = ("실내", "지하", "전시실", "전시관", "전시홀", "컨벤션", "홀", "B1", "B2", "B3")
        outdoor = any(kw in title for kw in _OUTDOOR)
        indoor = any(kw in title for kw in _INDOOR)
        if outdoor and indoor:
            return None
        if outdoor:
            return True
        if indoor:
            return False
        return None

    # ── 기상 문구 ──────────────────────────────────────────────
    @staticmethod
    def _weather_note(forecast: dict[str, Any]) -> tuple[str, list[str]]:
        """예보를 **사실로만** 전한다.

        ★★**여기서 「불가」를 만들지 않는다.** 강수확률이 얼마부터 취소·순연
          인지는 **운영 규정**이 정할 일이고, 그 규정은 `read.policy` 가 대야
          한다. 가드레일의 수치는 판정 기준이 아니라 **주의 문구 기준**이며,
          측정이 아니라 우리가 고른 값이다. 그걸로 확정 답을 만들면
          근거 없는 확정이 된다(CLAUDE.md §0.1).

        ★예보를 관찰처럼 말하지 않는다 — 「조회 시각」과 「예보 대상 시각」을
          둘 다 밝힌다(v10 §4-D).
        """
        from app.core.settings import get_guardrails

        guardrails = get_guardrails()
        pop = forecast.get("precipitation_probability")
        wind = forecast.get("wind_speed_kmh")
        hour = forecast.get("matched_hour")

        parts = []
        if pop is not None:
            parts.append(f"강수확률 {pop}%")
        if wind is not None:
            parts.append(f"풍속 {wind}km/h")
        if not parts:
            # ★값이 하나도 없으면 「예보를 봤다」고 말하지 않는다.
            return ("기상 예보 값을 읽지 못해 날씨는 판정에 넣지 않았습니다.",
                    ["기상 예보에 필요한 값이 비어 있었다"])

        note = (f"예보상 {hour} 기준 {', '.join(parts)}입니다"
                f"(예보이며 현장 확인이 아닙니다).")

        advisories: list[str] = []
        pop_limit = guardrails.get("travel.weather.advisory_precipitation_probability")
        wind_limit = guardrails.get("travel.weather.advisory_wind_speed_kmh")
        if pop is not None and pop >= pop_limit:
            advisories.append(
                f"강수확률 {pop}% — 주의 기준({pop_limit}%) 이상이다. "
                f"취소·순연 기준은 운영 규정에서 확인해야 한다")
        if wind is not None and wind >= wind_limit:
            advisories.append(
                f"풍속 {wind}km/h — 주의 기준({wind_limit}km/h) 이상이다. "
                f"취소·순연 기준은 운영 규정에서 확인해야 한다")
        if advisories:
            note += " 다만 취소·순연 기준은 규정에서 확인되지 않아 판정하지 않았습니다."
        return note, advisories
