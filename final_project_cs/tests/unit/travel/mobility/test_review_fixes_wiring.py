# -*- coding: utf-8 -*-
"""이동 계산기 연결(2026-09-29 문제목록 #24·#31·#34·#35) — 켜고 끄기 · 기동 확인 · 에이전트가 계산기로 답하기.

작은 가공 자료(test_review_fixes_runtime._write_mini_data)로 실제 판정기를 올린다.
"""
from __future__ import annotations

import asyncio

import pytest

from app.modules.travel_ops.mobility import wiring
from app.modules.travel_ops.mobility.engine import paths
from app.modules.travel_ops.mobility.engine import runtime as RT
from app.modules.travel_ops.mobility.team import MobilityTeam

from ..helpers import FakeTools, pack, task
from .test_review_fixes_runtime import _write_mini_data

ALLOWED = list(MobilityTeam.manifest.allowed_tools)


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    before = (paths.SOURCE, paths.DATA_DIR)
    monkeypatch.setattr(RT, "_SINGLETON", None)
    saved = dict(wiring._STATE)
    yield
    paths._layout(before[1], before[0])
    wiring._STATE.clear()
    wiring._STATE.update(saved)
    RT._SINGLETON = None


def _mobility_task(state, capability="mobility.check_route"):
    return task("mobility", capability, pack("mobility", state=state), ALLOWED, input_text="강남 가는 막차")


LEGS = {"date": "2026-10-07", "depart_at": "10:00", "legs": [{"line": "01호선", "from": "A", "to": "C"}]}


def test_disabled_when_setting_is_empty_and_never_falls_back_to_dotenv():
    assert wiring.configure(data_dir="")["mode"] == "disabled"
    with pytest.raises(RuntimeError, match="꺼져 있다"):
        RT.build_verifier(quiet=True)                   # 꺼짐 뒤에는 명령줄 관례(.env)로도 켜지지 않는다


def test_24_missing_data_stops_startup(tmp_path):
    with pytest.raises(wiring.MobilityUnavailable, match="서버를 띄우지 않는다"):
        wiring.configure(data_dir=str(tmp_path), preload=False)


def test_31_enabled_preloads_once(tmp_path):
    _write_mini_data(tmp_path)
    got = wiring.configure(data_dir=str(tmp_path), gh_url="", seoul_key="")
    assert got["mode"] == "enabled" and RT._SINGLETON is not None, "기동 때 적재한다 — 첫 요청이 기다리지 않는다"


def test_99_gh_url_setting_is_accepted_but_not_used(tmp_path):
    """99(2026-10-04) — 팀 설정 칸(ACOP_MOBILITY_GH_URL)에 값이 와도 계산기로 넘기지 않는다(경로 서버를 부르지 않는다).
    인자는 받는다 — 팀 설정·팀 시험이 이 이름으로 부른다."""
    _write_mini_data(tmp_path)
    wiring.configure(data_dir=str(tmp_path), gh_url="http://localhost:1", seoul_key="")
    assert "gh_url" not in wiring._STATE["kw"], wiring._STATE["kw"]
    from types import SimpleNamespace
    wiring.configure_from_settings(SimpleNamespace(mobility_data_dir=str(tmp_path), mobility_gh_url="http://localhost:1",
                                                   seoul_openapi_key="", guardrails_path="config/guardrails.yaml"))
    assert "gh_url" not in wiring._STATE["kw"] and wiring.mode() == "enabled"


def test_34_35_team_answers_structured_route_with_engine(tmp_path):
    _write_mini_data(tmp_path)
    wiring.configure(data_dir=str(tmp_path), gh_url="", seoul_key="")
    tools = FakeTools({})
    result = asyncio.run(MobilityTeam(tools).execute(_mobility_task({"mobility": LEGS})))
    assert result.outcome == "completed" and "가능" in (result.answer or ""), result
    assert tools.calls == [], "조회 도구(read.route — 비어 있음)를 거치지 않는다"
    assert result.evidence and all(e.observed_at is not None for e in result.evidence)


def test_34_team_without_engine_raises_error_not_made_up_answer():
    wiring.configure(data_dir="")
    result = asyncio.run(MobilityTeam(FakeTools({})).execute(_mobility_task({"mobility": LEGS})))
    assert result.outcome == "escalated" and result.failure_code == "mobility_engine_disabled"


def test_35_without_structured_input_keeps_old_path():
    wiring.configure(data_dir="")
    tools = FakeTools({"read.route": None})
    result = asyncio.run(MobilityTeam(tools).execute(_mobility_task({})))
    assert [c[0] for c in tools.calls] == ["read.route"], "구조화 입력이 없으면 자연어에서 구간을 짐작하지 않는다(종전 길)"
    assert result.outcome != "completed"
