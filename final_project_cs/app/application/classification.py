"""인라인 분류의 **실행** — ★코어 1 소유 (v8 §3-A · §7-A · §16).

무엇이 여기 있고 무엇이 여기 없나:

    여기(코어 1)      언제 부르는가 · 실패를 어떻게 처리하는가 · 어느 상태로 보내는가
    app/modules/     라벨 어휘 · 프롬프트 · provider 호출 (모델 담당)

★v8 §3-A 가 그대로 그렇게 적는다: "분류기의 라벨 어휘와 프롬프트 품질은 모델
  담당이 만들지만, 언제 부르고 실패를 어떻게 처리하며 어느 상태로 보내는지는
  코어 1이 정한다."

★**왜 어휘까지 여기로 올리지 않는가.** `order_payment_failed` 같은 업무 어휘는
  basement 에 있을 수 없다(`tests/architecture/test_basement_is_domain_free.py`).
  복사본이 다른 도메인으로 갈아 끼울 때 그 어휘까지 물려받으면 안 되기 때문이다.
  그래서 어휘는 `app/modules/` 에 남기고, **실행 절차만** 코어 1 이 갖는다.
  여기에는 업무 어휘가 한 낱말도 없다 — 그게 이 파일이 이 자리에 있을 수 있는
  이유다.

★**왜 접수 라우트에서 뺐는가**(v8 §3-A [2026-09-01 교정] 결함 1). 전에는 이
  절차가 `app/presentation/api/cases.py` 안에만 있어서, 부를 수 있는 곳이 접수
  라우트 하나뿐이었다. Controller 도 재시도 작업도 같은 절차를 쓸 수 없었다.
  이제 코어 1 계층에 있으므로 누구나 부른다.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import UUID

from app.core.transition import transition_case
from app.domain.events import EventType
from app.presentation.security import masked

logger = logging.getLogger(__name__)

#: 분류기가 반드시 채워야 하는 라벨. ★기본값으로 메우지 않는다 — 하나라도 없으면
#:  실패로 친다(`CLAUDE.md` §1, 조용한 분류 성공 위장 금지).
REQUIRED_LABELS = ("intent", "issue_code", "sentiment")

#: 분류 실패를 기록할 때 쓰는 코드. 상태기계가 이걸 보고 `escalated` 로 보낸다.
FAILURE_CODE = "classification_failed"


class Classifier(Protocol):
    def __call__(self, text: str) -> dict[str, Any] | None: ...


def classify_case(conn: Any, *, tenant_id: str, case_id: UUID, text: str,
                  classifier: Classifier | None, actor_id: str,
                  expected_version: int = 1, state_patch: dict[str, Any] | None = None) -> EventType:
    """분류를 수행하고 그 결과를 **한 문으로** 기록한다.

    ★반드시 Case 생성 트랜잭션 **밖에서** 부른다. 안에서 부르면 외부 provider 를
      기다리는 동안 커넥션과 advisory lock 을 잡고 있게 되고, 타임아웃이면 Case
      생성까지 롤백된다 — 고객 문의가 통째로 사라진다(v8 §3-A 결함 2,
      §7-A "Case 생성 transaction 밖에서").

    ★실패를 삼키지 않는다. 어떤 이유로든 라벨을 못 얻으면
      `CLASSIFICATION_FAILED` 를 남기고 상태기계가 `escalated` 로 보낸다.
      분류를 건너뛰고 조용히 넘어가는 경로는 없다.

    돌려주는 것은 **실제로 기록한 event** 다 — 부르는 쪽이 무엇이 일어났는지
    알아야 하고, 그걸 다시 DB 에 물어보게 하지 않는다.
    """
    try:
        if classifier is None:
            raise ValueError("classifier is not configured")
        result = classifier(masked(text))
        # ★**키가 있는지가 아니라 값이 있는지를 본다.** 전에는
        #   `label in result` 로 키 존재만 봐서 `""`·`"  "`·`None` 이 전부
        #   통과했다(2026-09-01 발견,
        #   wiki/records/reports/debugs/2026-09-01_분류_빈라벨_통과.md).
        #   빈 라벨을 통과시키면 Case 가 `classified` 로 넘어가 **분류가 된 것처럼
        #   보이는데 실제로는 아무 라벨이 없다** — `CLAUDE.md` §1 이 막으려는
        #   "조용한 분류 성공 위장" 그 자체다. 라우팅도 빈 intent 로 가서
        #   엉뚱한 곳에서 실패한다.
        if not result or not all(str(result.get(label) or "").strip() for label in REQUIRED_LABELS):
            raise ValueError("classifier returned no usable labels")
        event: EventType = EventType.CLASSIFIED
        payload: dict[str, Any] = dict(result)
    except Exception as exc:
        # ★원인을 남긴다. 여기서 삼키면 키 오류(401)도 「분류하지 못했다」 한 줄로만 보여 원인을 못 찾는다.
        #   문의 원문은 남기지 않는다 — 원인 종류와 메시지만.
        logger.warning("분류 실패: tenant=%s case=%s 원인=%s: %s",
                       tenant_id, case_id, type(exc).__name__, exc)
        event, payload = EventType.CLASSIFICATION_FAILED, {"failure_code": FAILURE_CODE}
    if state_patch:
        # ★`[2026-09-17]` 분류와 **같은 이벤트**로 기록한다 — Case 상태는 한 문으로만 바뀐다.
        payload["state_patch"] = dict(state_patch)

    with conn.transaction():
        transition_case(conn, tenant_id=tenant_id, case_id=case_id,
                        expected_version=expected_version, event_type=event,
                        payload=payload, actor_type="api", actor_id=actor_id)
    return event


__all__ = ["classify_case", "Classifier", "REQUIRED_LABELS", "FAILURE_CODE"]
