# -*- coding: utf-8 -*-
"""이동 계산기(engine/) 연결 — 서버 기동 때 켜고, 이동 에이전트가 구조화된 구간 입력을 판정할 때 쓴다.

☆`[2026-09-29 이동 계산기 문제목록 #34·#35·#31·#24]`
  #34 에이전트 본체가 계산기를 한 번도 부르지 않았다 → team.py 가 `current_state.mobility` 입력이 오면 여기로 판정한다.
  #35 구간 확인·막차 판정(check_route·exception)은 조회 도구가 비어 늘 「모름」이었다 → 계산기 판정으로 답한다.
  #31 첫 고객 요청이 자료 적재(약 33초)를 기다렸다 → 기동 때 적재한다.
  #24 자료가 없으면 첫 호출에서 멈췄다 → 기동 때 확인하고, 없거나 판 명세와 다르면 **서버를 띄우지 않는다**(결정 15).

켜고 끄기는 설정 `mobility_data_dir` 하나가 정한다. 비우면 꺼짐 — 계산기를 부르지 않고, 구조화 입력이 와도
「계산기가 꺼져 있다」는 오류로 올린다(지어낸 답을 내지 않는다). ★꺼짐에서 명령줄 관례(.env)로 새지 않는다(paths.disable).

★ 계약 타입(TeamResult)은 여기서 만들지 않는다 — 계산기 어댑터가 준 dict 를 team.py 가 계약으로 옮긴다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .engine import datacheck, paths
from .engine import guardrails as engine_guardrails
from .engine import runtime as engine_runtime

CS_ROOT = Path(__file__).resolve().parents[4]          # mobility → travel_ops → modules → app → final_project_cs

_STATE: dict[str, Any] = {"mode": "unconfigured", "kw": None, "datacheck": None}


class MobilityUnavailable(RuntimeError):
    """이동 자료가 없거나 판 명세와 다르다 — 켜라고 했는데 켤 수 없다. 기동을 멈춘다(결정 15)."""


def configure(*, data_dir: str | None, gh_url: str = "", seoul_key: str = "",
              guardrails_path: str | Path | None = None, preload: bool = True,
              verify_hash: bool = True) -> dict[str, Any]:
    """계산기를 켜거나 끈다. 켤 때는 자료를 확인하고(없거나 다르면 MobilityUnavailable) 적재까지 한다."""
    if not data_dir:
        paths.disable()
        _STATE.update(mode="disabled", kw=None, datacheck=None)
        return {"mode": "disabled"}
    paths.configure(data_dir)
    if guardrails_path:
        engine_guardrails.use(guardrails_path)
    dc = datacheck.check(verify_hash=verify_hash)
    if not dc["ok"]:
        _STATE.update(mode="broken", kw=None, datacheck=dc)
        raise MobilityUnavailable(f"이동 자료 확인 실패 — 서버를 띄우지 않는다(결정 15): 없음 {dc['missing']} · "
                                  f"다름 {dc['mismatched']} · 자료 폴더 {dc['data_dir']}")
    kw = {"quiet": True, "data_dir": data_dir, "gh_url": gh_url or "", "seoul_key": seoul_key or "",
          "guardrails_path": str(guardrails_path) if guardrails_path else None}
    _STATE.update(mode="enabled", kw=kw, datacheck=dc)
    if preload:
        engine_runtime.get_verifier(**kw)
    return {"mode": "enabled", "datacheck": dc}


def configure_from_settings(settings: Any, *, preload: bool = True) -> dict[str, Any]:
    """서버 설정(app.core.settings.Settings)으로 켠다. 설정 객체를 받기만 한다 — 여기서 설정을 읽지 않는다."""
    # 칸이 없는 설정(시험이 넣는 일부 칸짜리 대역)은 이동 칸이 빈 것과 같다 — 꺼짐
    gp = Path(getattr(settings, "guardrails_path", "config/guardrails.yaml"))
    if not gp.is_absolute():
        gp = CS_ROOT / gp
    return configure(data_dir=getattr(settings, "mobility_data_dir", ""),
                     gh_url=getattr(settings, "mobility_gh_url", ""),
                     seoul_key=getattr(settings, "seoul_openapi_key", ""), guardrails_path=gp, preload=preload)


def mode() -> str:
    return _STATE["mode"]


#: 설문 우선순위 「이동」 세부 코드(화면 PREFERENCES_CONTRACT) → 계산기 수단. 렌트카·택시는 계산기가 아직 못 다룬다(#47)
SURVEY_MODES = {"public": ("subway", "bus"), "walk": ("walk",)}


def modes_from_survey(constraints: dict[str, Any] | None) -> list[str] | None:
    """☆`[2026-09-29 문제목록 #46]` 설문의 이동 선호를 계산기 수단으로. 앞 판은 받아 두기만 했다.

    화면은 `preferred_mobility[]` 를 보내지 않고 `priority_details.mobility`(public·walk·car·taxi)로 보낸다 — 둘 다 본다.
    옮길 수 있는 것이 하나도 없으면(렌트카·택시만) None — 계산기 기본 수단(지하철·버스·도보). 도보는 늘 넣는다
    (역·정류장까지 걷기는 어느 수단에도 들어간다)."""
    survey = (constraints or {}).get("survey") or {}
    codes = list(((survey.get("priority_details") or {}).get("mobility") or []))
    ko = {"대중교통": "public", "도보": "walk"}
    codes += [ko.get(x, x) for x in (survey.get("preferred_mobility") or [])]
    modes = {m for c in codes for m in SURVEY_MODES.get(c, ())}
    return sorted(modes | {"walk"}) if modes else None


def engine_line(name: str) -> str:
    """uses 노선명 → 계산기(시간표) 노선명. '2호선' → '02호선' · '경의중앙선' → '경의선'(options.line_name 의 반대)."""
    from .engine.options import LINE_OFFICIAL
    back = {v: k for k, v in LINE_OFFICIAL.items()}
    if name in back:
        return back[name]
    if name.endswith("호선") and name[:-2].isdigit():
        return f"{int(name[:-2]):02d}호선"
    return name


def disruptions_from_events(events: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """☆`[2026-09-29 문제목록 #39]` 우리 경로 사건(대상 표기 → effect) → 계산기 사고 조건(kind). (옮긴 것, 못 옮긴 대상).

    뜻이 같은 것만 옮긴다 — 이름만 바꾸지 않는다.
      N호선:역 + skip_station  → station_skip{line, station}   (그 역에 안 선다 — 양쪽 같은 뜻)
      N호선:역 + skip_station + suspended(운행 중단 · 지하철 알림) → 구간 차단 edge_closed{line, between} 들
                               (section [A, B] 사이 간선 전부 · 역 하나면 그 역에 닿는 간선 전부 + 그 역 무정차 ·
                                section 이 없거나 노선 순서를 모르면 line_closed{line})
      N호선:*  + line_closed   → line_closed{line}
      ☆`[2026-10-01 · 84]` 버스는 셋으로 나눈다(앞 판은 전부 노선 전체 중단 route_closed 라 과잉 탈락):
      버스:노선 + skip_station + stops[ARS] → stop_skip{route, ars, window?}  (그 정류장에서 타고 내리는 경로만 불가 ·
                               지나가는 경로는 유지 · window = 사건 시간대가 하루 안이면 그 시각)
      버스:노선 + road_control (+detour) → route_detour{route}  (우회 — 소요 모름 · 판단불가)
      버스:노선 + skip_station(정류장 모름) · route_closed · line_closed → route_closed{route}  (어느 정류장인지 모르면 종전대로)
      도로:…   + road_control  → 못 옮김 — 우리 쪽은 「느려진다」, 계산기에는 대중교통이 지나는 도로 정보가 없다
    못 옮긴 대상은 부르는 쪽이 드러낸다(조용히 버리지 않는다).
    """
    out, unmapped = [], []
    for target, ev in (events or {}).items():
        ev = ev or {}
        head, _, rest = str(target).partition(":")
        effect = ev.get("effect")
        meta = {"note": ev.get("summary"), "source": ev.get("source_id") or "trip_watch",
                "grade": ev.get("grade", "추정"), "observed_at": ev.get("observed_at")}
        if head == "버스" and effect == "skip_station" and (ev.get("stop_windows") or ev.get("stops")):
            # ☆GPT 84 #3 — 정류장마다 제 시간대로(공지 여럿이 한 노선에 걸리면 시간대가 다르다). 옛 모양(stops + 한 시간대)도 받는다
            rows = ev.get("stop_windows") or [{"ars": a, "start": ev.get("window_start"), "end": ev.get("window_end")}
                                              for a in ev["stops"]]
            for row in rows:
                window = _window_hm(row.get("start"), row.get("end"))
                out.append({"kind": "stop_skip", "route": rest, "ars": str(row["ars"]),
                            **({"window": window} if window else {}), **meta})
        elif head == "버스" and effect == "road_control":
            window = _window_hm(ev.get("window_start"), ev.get("window_end"))
            out.append({"kind": "route_detour", "route": rest, **({"window": window} if window else {}), **meta})
        elif head == "버스" and effect in ("route_closed", "line_closed", "skip_station"):
            out.append({"kind": "route_closed", "route": rest, **meta})
        elif head not in ("버스", "도로") and effect == "skip_station" and ev.get("suspended"):
            for section in (ev.get("sections") or [ev.get("section")]):
                out += _section_closed(engine_line(head), section, meta)
            if ev.get("station_skipped") and rest:          # 같은 역에 무정차도 걸려 있다(GPT 84 #3)
                out.append({"kind": "station_skip", "line": engine_line(head), "station": rest, **meta})
        elif head not in ("버스", "도로") and effect == "skip_station" and rest:
            out.append({"kind": "station_skip", "line": engine_line(head), "station": rest, **meta})
        elif head not in ("버스", "도로") and effect == "line_closed":
            out.append({"kind": "line_closed", "line": engine_line(head), **meta})
        else:
            unmapped.append(str(target))
    # 같은 조건이 대상마다 겹쳐 나오면(운행 중단 구간을 그 노선 대상마다 알림) 하나로
    seen, uniq = set(), []
    for d in out:
        key = (d["kind"], d.get("line"), d.get("station"), d.get("route"), d.get("ars"),
               tuple(d.get("between") or ()), tuple(d.get("window") or ()))
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    return uniq, unmapped


def _window_hm(start: Any, end: Any) -> list[str] | None:
    """사건 시간대(ISO 둘) → 계산기 stop_skip window ["HH:MM", "HH:MM"]. 같은 날 안일 때만 — 날을 넘기면 None(늘 무정차로
    본다 · 보수적). 운행일 경계(04:00) 전 시각도 None 으로 둔다(계산기 시각 축은 운행일 표기)."""
    from datetime import datetime
    try:
        a, b = datetime.fromisoformat(str(start)), datetime.fromisoformat(str(end))
    except (TypeError, ValueError):
        return None
    if a.date() != b.date() or a.hour < 4:
        return None
    return [a.strftime("%H:%M"), b.strftime("%H:%M")]


def _section_closed(line: str, section: Any, meta: dict[str, Any]) -> list[dict[str, Any]]:
    """운행 중단 구간 [A, B] → A~B 사이 간선마다 edge_closed. 노선 순서를 모르거나 구간이 없으면 노선 전체 line_closed.

    ☆GPT 84 #7 — 역 하나만 적힌 운행 중단([A, A])을 무정차(station_skip)로 옮기지 않는다. 무정차는 「지나가기는 한다」인데
      운행 중단은 그 역을 **지나가지도 못한다**(차량 고장으로 선 열차 등 — 원천이 뜻을 밝히지 않는다 · 보수적으로). 그 역에
      닿는 간선을 모두 끊고(지나갈 수 없다) 그 역도 서지 않는 것으로 둔다."""
    whole = [{"kind": "line_closed", "line": line, **meta}]
    if not section or len(section) != 2 or _STATE["mode"] != "enabled":
        return whole
    lo = engine_runtime.get_verifier(**_STATE["kw"])._v.lo
    if section[0] == section[1]:
        near = sorted((getattr(lo, "_g", {}).get(line) or {}).get(section[0]) or [])
        if not near:
            return whole
        return ([{"kind": "edge_closed", "line": line, "between": [section[0], n], **meta} for n in near]
                + [{"kind": "station_skip", "line": line, "station": section[0], **meta}])
    path = lo.path(line, section[0], section[1])
    if not path or len(path) < 2:
        return whole
    return [{"kind": "edge_closed", "line": line, "between": [path[i], path[i + 1]], **meta}
            for i in range(len(path) - 1)]


def leg_planner(party_size: int | None, constraints: dict[str, Any] | None, *, disruptions=None):
    """일정 짜기(planner.add_moves)가 부를 **구간 계산기**. 꺼져 있으면 None — 부르는 쪽이 직선 어림값으로 간다.

    돌려주는 함수 leg(a_place, b_place, arrive_dt, not_before_dt) → (결과 dict, None) 또는 (None, 이유 dict).
      결과: starts_at · ends_at(도착 목표 − 여유 기준 출발 · 도착) · eta_min · route(options·uses 포함) · left_out
      route 에 밀도 검사(density.py)가 읽는 칸을 채운다 — average_eta_min(계획 수단 소요) · p95_eta_min(최악 소요) ·
      distance_m(계획 수단 도보 거리). ☆`[2026-09-29 문제목록 #43]` 앞 판 계산기는 이 칸을 내지 않았다.
    """
    if _STATE["mode"] != "enabled":
        return None
    from .engine.plan import Planner, iso_of, party_of
    rt = engine_runtime.get_verifier(**_STATE["kw"])
    c = dict(constraints or {})
    planner = Planner(rt, stage="planning", modes=modes_from_survey(c))
    if disruptions:
        planner.disruptions = tuple(dict(d) for d in disruptions)     # #38 — 사고를 모든 판정 호출에 싣는다
    party = party_of(party_size, c)
    first_visit = c.get("first_visit", True)
    buffer = rt._v.rv("buffer", "by_stage", "planning")
    counter = {"n": 0}

    def leg(a_place, b_place, arrive_dt, not_before_dt=None):
        counter["n"] += 1
        planner.trace = []
        got, why = planner.leg(a_place, b_place, arrive_dt, party, first_visit,
                               case_id=f"{a_place.get('key')}_to_{b_place.get('key')}_{counter['n']}",
                               not_before_dt=not_before_dt)
        if got is None:
            return None, why
        route, start, end, sdate, left = got
        planned = next(o for o in route["options"] if o["id"] == route["planned"])
        tr = next((o for o in (planner.trace[-1]["options"] if planner.trace else []) if o["id"] == planned["id"]), {})
        spread = max(0, int((tr.get("margin_min") or buffer) - buffer))
        route = dict(route, average_eta_min=planned["eta_min"], p95_eta_min=planned["eta_min"] + spread,
                     **({"distance_m": planned["walk_m"]} if planned.get("walk_m") is not None else {}))
        from datetime import datetime
        return {"route": route, "starts_at": datetime.fromisoformat(iso_of(sdate, start)),
                "ends_at": datetime.fromisoformat(iso_of(sdate, end)), "eta_min": int(planned["eta_min"]),
                "left_out": left}, None
    return leg


def team_result(task: Any) -> dict[str, Any] | None:
    """구조화 입력(current_state.mobility)을 계산기로 판정해 TeamResult 모양 dict 를. 꺼져 있으면 None."""
    if _STATE["mode"] != "enabled":
        return None
    from .engine.adapter import MobilityAdapter
    rt = engine_runtime.get_verifier(**_STATE["kw"])
    adapter = MobilityAdapter(rt.verify_case, basis={"timetable_built_at": rt.timetable_built_at,
                                                     "rules_version": rt.rules_version})
    out = adapter.run(task)
    if rt.timetable_stale:                      # #32 — 오래된 시간표로 낸 판정이면 드러낸다
        out["warnings"] = list(out.get("warnings") or []) + ["MOB_W_TIMETABLE_STALE"]
    return out
