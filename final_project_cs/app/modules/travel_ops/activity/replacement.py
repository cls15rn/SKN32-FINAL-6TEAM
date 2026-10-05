# -*- coding: utf-8 -*-
"""불가 판정 뒤의 **대체 장소** — 후보 풀을 읽어 줄 세우고, 재검증을 통과한 것만 안내문에 싣는다.

★`team.py` 에서 옮겼다(동작 변경 없음). 순위 계산 자체는 `alternatives.py`(순수 함수)가 한다.
"""
from __future__ import annotations

from typing import Any

from app.core.contracts import TeamTask

from . import failure_codes as fc
from .alternatives import FALLBACK_DROPS, RADIUS_START_KM, preference_from_survey, rank_alternatives


class ReplacementMixin:
    # ── 대체 장소 ─────────────────────────────────────────────

    @staticmethod
    def _blocked_by_place(decisions: dict[str, Any]) -> bool:
        """불가 사유가 **그 장소**에 있나 — 정기휴무 요일이거나 위급재난. 이미 시작됨·정원 초과는 장소를 바꿔도 안 풀린다."""
        return bool((decisions.get("operating") or {}).get("weekday_match") is True
                    or (decisions.get("disaster") or {}).get("blocks"))

    @staticmethod
    def _survey(task: TeamTask) -> dict[str, Any] | None:
        """설문(`trips.constraints.survey`)이 Case 상태에 실려 오면 읽는다. 없으면 `None`."""
        state = task.context.current_state or {}
        survey = state.get("survey") or (state.get("constraints") or {}).get("survey")
        return survey if isinstance(survey, dict) else None

    @classmethod
    def _preference(cls, task: TeamTask) -> tuple[str | None, list[str]]:
        """선호도 — `current_state["activity_preference"]` 를 먼저, 없으면 설문 `priority` 에서. 둘 다 없으면 `None`.

        ★모르는 값이 들어 있으면 짐작하지 않고 경고를 남긴 채 `None`(폴백 없음)으로 처리한다.
        """
        state = task.context.current_state or {}
        raw = state.get("activity_preference")
        if raw is None:
            return preference_from_survey(cls._survey(task)), []
        if raw in FALLBACK_DROPS:
            return raw, []
        return None, [f"알 수 없는 선호도 값({raw!r})이라 선호도를 반영하지 않았다"]

    def _recommend_alternatives(self, task: TeamTask, place: Any, starts_at: Any, seen: set[str],
                                evidence: list, decisions: dict[str, Any],
                                ) -> tuple[dict[str, Any], list, str, list[str]]:
        """원래 장소 기준 대체 후보를 `read.place_candidates` 로 읽어 `rank_alternatives` 로 줄 세운다.

        ★**계산만 한다.** 후보를 못 읽으면 지어내지 않고 `status="unknown"` 과 이유 코드를 낸다.
        ★결과는 `decisions[].alternatives` 로 나간다 — `status`: ranked · unknown · withheld
          (wiki/teams/activity.md 「Team 연결」).
        ★위급재난이면 **도구를 부르지 않고** 안내도 하지 않는다(`withheld`). 재난문자가 전국 목록이라
          후보도 원래 장소와 같은 판정이다.
        """
        if (decisions.get("disaster") or {}).get("blocks"):
            # ★전국 목록이라 어느 후보로 옮겨도 같은 위급재난 판정을 받는다.
            self._record_failure(task, fc.ALTERNATIVES_WITHHELD)
            return ({"status": "withheld", "reason": "disaster_blocks"}, evidence,
                    "위급재난 문자는 지역 구분 없이 확인되어 대체 장소도 같은 판정을 "
                    "받으므로 대체 장소를 안내하지 않았습니다.", [])
        content_id = place.get("source_content_id") if isinstance(place, dict) else None
        if not content_id:
            self._record_failure(task, fc.ALTERNATIVES_NO_CONTENT_ID)
            return ({"status": "unknown", "reason": "no_content_id"}, evidence,
                    "원래 장소의 식별 정보가 없어 대체 장소를 찾지 못했습니다.",
                    ["대체 장소를 찾을 원래 장소 식별자(source_content_id)가 없다"])
        pool = self._read(task, "read.place_candidates", {"content_id": str(content_id)}, seen)
        if pool is None:
            self._record_failure(task, fc.ALTERNATIVES_POOL_UNAVAILABLE)
            return ({"status": "unknown", "reason": "pool_unavailable"}, evidence,
                    "대체 장소 후보를 조회하지 못했습니다.", ["대체 장소 후보 풀을 받지 못했다"])
        candidates = pool.get("candidates") or []
        # ★풀 전체(수천 건)를 근거에 싣지 않는다 — 어디서 몇 건을 언제 읽었는지만.
        evidence = self._evidence(task, source_id="read.place_candidates", claim="대체 후보 풀",
                                  value={"origin": {"contentid": (pool.get("origin") or {}).get("contentid"),
                                                    "title": (pool.get("origin") or {}).get("title")},
                                         "pool_size": len(candidates), "source": pool.get("source"),
                                         "confirmed_at": pool.get("confirmed_at")},
                                  base=evidence)
        preference, warnings = self._preference(task)
        ranked = rank_alternatives(pool["origin"], candidates, starts_at, preference=preference)
        if ranked["reason"] == "origin_no_coordinates":
            # ★근처를 잴 기준이 없다 — 「근처에 없음」으로 읽지 않는다.
            self._record_failure(task, fc.ALTERNATIVES_NO_COORDINATES)
            return ({"status": "unknown", "reason": "origin_no_coordinates"}, evidence,
                    "원래 장소의 좌표가 없어 근처 대체 장소를 찾지 못했습니다.",
                    [*warnings, "대체 장소를 찾을 원래 장소 좌표가 없다"])
        # ★재검증(v11 §5) — 도구를 더 부르지 않고 이미 읽은 값으로만. 휴무 요일·운영시간이 **확인된** 후보만
        #   `revalidated: True` 다. 모름인 후보는 `decisions` 에만 남기고 안내문에는 싣지 않는다.
        for alternative in [*ranked["alternatives"], *ranked["more_alternatives"]]:
            alternative["revalidated"] = alternative["availability"] != "unconfirmed"
        if ranked["dropped_fields"]:
            warnings.append(f"유사 조건 일부({', '.join(ranked['dropped_fields'])})를 풀어서 찾은 후보다")
        if ranked["alternatives"] and ranked["radius_km"] > RADIUS_START_KM:
            warnings.append(f"반경을 {ranked['radius_km']:g}km까지 넓혀서 찾은 후보다")
        if ranked["more_dropped_fields"] and ranked["more_alternatives"]:
            warnings.append(f"더보기 후보 일부는 유사 조건({', '.join(ranked['more_dropped_fields'])})을 "
                            "풀어서 찾았다")
        alt = {"status": "ranked", **ranked,
               "revalidation": {"checked": ["time", "weekday_closure", "business_hours", "disaster"],
                                "not_checked": ["capacity"]},
               "source": pool.get("source"), "confirmed_at": pool.get("confirmed_at")}
        passed = [a for a in ranked["alternatives"] if a["revalidated"]]
        more = sum(1 for a in ranked["more_alternatives"] if a["revalidated"])
        if not passed:
            self._record_failure(task, fc.ALTERNATIVES_NONE if not ranked["alternatives"]
                                 else fc.ALTERNATIVES_UNCONFIRMED)
            note = (f"근처(반경 {ranked['max_radius_km']:g}km)에 조건에 맞는 장소가 없습니다."
                    if not ranked["alternatives"]
                    else "대체 장소 후보는 있으나 운영 여부를 확인하지 못해 안내하지 않았습니다.")
            if more:
                # ★선호도 없음 — 화면 칸은 정확한 분류만 쓰므로 비지만, 비슷한 곳은 더보기에 있다.
                note += f" 비슷한 분류의 장소 {more}곳은 더보기에 있습니다."
            return alt, evidence, note, warnings

        # ★장소마다 추천 이유 한 줄(`reason_line` — 잰 값만 쓴다). 「더보기」 후보는 수만 말한다.
        lines = "\n".join(f"- {a['title']}: {a['reason']}" for a in passed)
        return (alt, evidence,
                f"대체 장소 후보:\n{lines}\n"
                + (f"더보기에 후보 {more}곳이 더 있습니다.\n" if more else "")
                + "후보는 휴무 요일·운영시간·재난문자만 다시 확인했고 정원은 확인하지 않았습니다.", warnings)
