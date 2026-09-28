# -*- coding: utf-8 -*-
"""TeamTask → 판정 → TeamResult. 이동 모듈 어댑터 **본체**.

경계 — 이 파일은 **계약 타입을 import 하지 않는다.**
  `task` 를 속성으로만 읽는다(duck typing). 여행 판 `contracts.py` 가 로컬 커머스 판과
  달라도 이 파일은 안 깨진다. 계약 객체를 만드는 일은 팀 저장소의 얇은 껍데기가 한다.
  결정: `2026-09-14_Mobility_아키텍처기반_입력결정_v1.md`

구조
  map_task_to_case(task) → (case, err)   ★ 아키텍처 가정이 **전부 여기 모인다**
  MobilityAdapter.run(task) → dict       분기 + 판정 호출 + 접기

  판정기는 주입받는다(`verify`). 시간표 195MB 를 이 파일이 들지 않는다 —
  껍데기가 프로세스 수명 동안 하나 만들어 넘긴다. 시험도 가짜 판정기로 돌릴 수 있다.

가정마다 출처를 주석에 적는다. 코드를 받는 날 `[아키텍처 v2]` 표시만 따라가면 대조가 끝난다.
"""
import json
from datetime import datetime, timezone, timedelta

from . import fold

KST = timezone(timedelta(hours=9))

# ── capability → 갈래. 앞자리(intent)는 코어 분류기 값에 맞춰 껍데기가 채운다.
#    [로컬 코드 registry.capability_for()] intent 와 같거나 'intent.' 로 시작하는 것을 고른다.
BRANCH = {"check_route": "verify", "exception": "verify", "status": "recheck"}

# ── ★ 우리 입력은 current_state 의 이 키 **한 덩어리**로 온다.
#    [결정 §3-2] current_state 는 여섯 팀이 공유하는 dict 다. date·legs 를 맨바닥에 쓰면
#    다른 팀 키와 충돌한다(Dining 의 dietary 가 그 자리였다).
SLOT = "mobility"

# 우리가 읽는 키 전부. 모르는 키는 조용히 버리지 않고 경고로 남긴다.
CASE_KEYS = ("date", "depart_at", "arrive_by", "legs", "disruptions",
             "party", "stage", "first_visit", "no_alternatives", "id")
REQUIRED = ("date", "legs")


def _to_plain(result):
    """판정기 산출(CaseResult 데이터클래스) → 순수 dict.

    ★ 왜 json 왕복이냐. 배치 경로가 정확히 이 변환을 한다 —
      verify_time.main() 의 `--json` 이 `json.dumps([r.__dict__ ...], default=lambda o: o.__dict__)`.
      접기는 **그 산출물**로 시험됐다(골든 픽스처·회귀 85건). 서버가 다른 표현을 주면
      **배치와 서버의 접기 결과가 갈린다** — 회귀가 통과해도 서버만 틀리는 종류다.
      그래서 빠른 길(dataclasses.asdict) 대신 같은 길을 쓴다.

    2026-09-14: 이 변환이 없어서 fold_case 가 'CaseResult' object has no attribute 'get' 로 죽었다.
      가짜 판정기가 dict 를 돌려주고 있어 check_adapter 가 못 잡았다 — 가짜도 객체로 바꿨다.
    """
    if isinstance(result, dict):
        return result
    try:
        plain = json.loads(json.dumps(result.__dict__, ensure_ascii=False,
                                      default=lambda o: o.__dict__))
    except (AttributeError, TypeError, ValueError) as e:
        raise TypeError(f"판정 산출을 dict 로 못 바꾼다: {type(result).__name__} — {e}") from e
    # ★ 변환만 되면 통과시키면 안 된다. 빈 객체는 __dict__ 가 {} 라 **조용히 {} 가 된다** —
    #   그러면 접기가 '구간 0개 · 근거없음' 이라는 그럴듯한 결과를 만든다. 판정을 안 했는데 답이 나온다.
    if "verdict" not in plain:
        raise TypeError(f"판정 산출에 verdict 가 없다: {type(result).__name__} "
                        f"(키: {sorted(plain)[:8]}) — 판정기가 아닌 것을 접으려 하고 있다")
    return plain


def _attr(o, name, default=None):
    """속성으로도 dict 키로도 받는다 — 계약 객체든 테스트용 dict 든 같게 다룬다."""
    if isinstance(o, dict):
        return o.get(name, default)
    return getattr(o, name, default)


def map_task_to_case(task):
    """TeamTask → 판정기 케이스 dict. 실패하면 (None, failure_code).

    ★ 고칠 자리는 여기 하나다. 아키텍처가 틀렸다면 이 함수만 바뀐다.
    """
    ctx = _attr(task, "context")
    if ctx is None:
        return None, "context_missing"

    # [로컬 코드 contracts.ContextPack] 구조화 입력은 전부 current_state 안이다(extra="forbid").
    state = _attr(ctx, "current_state") or {}
    raw = state.get(SLOT)

    if raw is None:
        # ★ 폴백을 두지 않는다 [결정 §3-3].
        #   input_text(자연어)에서 구간을 뽑는 것은 추측이다. 입력이 없으면 없다고 말한다 —
        #   조용히 그럴듯한 답을 내면 아무도 안 고친다.
        return None, "mobility_input_missing"
    if not isinstance(raw, dict):
        return None, "mobility_input_malformed"

    missing = [k for k in REQUIRED if not raw.get(k)]
    if missing:
        return None, f"mobility_input_incomplete:{'+'.join(missing)}"
    if not raw.get("depart_at") and not raw.get("arrive_by"):
        return None, "mobility_input_incomplete:depart_at|arrive_by"

    case = {k: raw[k] for k in CASE_KEYS if k in raw}
    case.setdefault("id", str(_attr(task, "case_id", "case"))[:8])
    # 모르는 키는 버리되 알린다. 코어가 새 키를 넣기 시작한 것을 놓치지 않는다.
    unknown = sorted(set(raw) - set(CASE_KEYS))
    return case, (f"unknown_keys:{','.join(unknown)}" if unknown else None)


class MobilityAdapter:
    """판정기를 주입받아 TeamTask 를 처리한다.

    verify(case) -> CaseResult dict   판정기. 같은 패키지 verify_time.py 의 Verifier.verify_case
    basis: **정적인 것만** — {"timetable_built_at", "rules_version"}.
           나머지 둘은 정적이 아니라서 어댑터가 케이스마다 만든다:
             service_date  ← 입력의 date (케이스마다 다르다)
             decided_at    ← 판정한 시각 (호출마다 다르다)
           이 둘을 생성자에 받으면 **모든 판정이 같은 날짜·같은 시각으로 기록된다.**
           판정 이력이 "언제 어느 날짜로 낸 판정인지"를 잃는다 — 재확인(F2)이 성립하지 않는다.
    """

    STATIC = ("timetable_built_at", "rules_version")

    def __init__(self, verify, *, basis, capabilities=(), now=None):
        missing = [k for k in self.STATIC if k not in basis]
        if missing:
            raise ValueError(f"basis 에 {missing} 이 없다 — 어느 판 시간표·규칙으로 냈는지 못 남긴다")
        self.verify = verify
        self.basis = {k: basis[k] for k in self.STATIC}
        self.capabilities = tuple(capabilities)
        self._now = now or (lambda: datetime.now(KST).isoformat(timespec="seconds"))

    def _basis_for(self, case):
        return dict(self.basis, service_date=case.get("date"), decided_at=self._now())

    # ── 실패는 한 자리에서 만든다. 모양이 갈라지면 코어가 다르게 다룬다.
    def _escalate(self, task, code, note=""):
        return {"contract_name": "a_cop.team_result", "contract_version": "1.0",
                "task_id": _attr(task, "task_id"), "team_id": "mobility",
                "outcome": "escalated", "next_action": "escalate",
                "confidence": 0.0, "answer": None,
                "evidence": [], "decisions": [], "action_proposals": [],
                "failure_code": code,
                # [로컬 코드 contracts.TeamResult] escalate 는 failure_code 또는 warnings 가 있어야 한다
                "warnings": [note] if note else [f"이동 판정을 낼 수 없다: {code}"]}

    def run(self, task):
        cap = _attr(task, "capability") or ""
        if self.capabilities and cap not in self.capabilities:
            # [로컬 코드 커머스 모듈 전례] manifest 밖 capability 는 ESCALATE 한다
            return self._escalate(task, "unsupported_capability", f"capability '{cap}' 는 이 모듈 것이 아니다")

        ctx = _attr(task, "context")
        if ctx is not None and _attr(ctx, "degraded"):
            # [로컬 코드 커머스 모듈 전례] degraded 면 조회 전에 끝낸다.
            #   ★ 다만 우리 판정은 정책 RAG 와 무관하다 — degraded 세 갈래가 전부 정책 검색 실패다.
            #     그래도 전례를 따른다. 관측되면 ◆ 로 올린다(04번 방).
            return self._escalate(task, "degraded_context", "문맥이 degraded 라 판정하지 않는다")

        branch = BRANCH.get(cap.rsplit(".", 1)[-1], "verify")
        if branch == "recheck":
            # F2 재확인은 mob_leg_verdict 이력을 읽어야 하는데 그 표가 아직 0건이다.
            # 돌려본 적 없는 경로를 '된다'고 내보내지 않는다.
            return self._escalate(task, "recheck_not_implemented", "재확인(F2)은 판정 이력이 쌓인 뒤에 연다")

        case, note = map_task_to_case(task)
        if case is None:
            return self._escalate(task, note)

        result = _to_plain(self.verify(case))
        out = fold.fold_case(result, task_id=str(_attr(task, "task_id")),
                             basis=self._basis_for(case), case_id=str(_attr(task, "case_id", "")))
        if note:
            out["warnings"] = list(out.get("warnings") or []) + [note]
        return out
