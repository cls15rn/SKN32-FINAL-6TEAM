# -*- coding: utf-8 -*-
"""사람 검토 도구 — AI 가 만든 정답(라벨)을 사람이 **정답을 가린 채** 다시 다는 시트 · 들이기 · 일치도.

    python -m eval.human_review list
    python -m eval.human_review export <묶음> --out 시트.csv [--subset 이름]
    python -m eval.human_review apply  <묶음> 시트.csv --reviewer 이름 --out-dir 폴더 [--subset 이름]
    python -m eval.human_review agree  <묶음> --a 라벨A.json (--b 라벨B.json | --ai 출처이름)

묶음마다 얇은 어댑터(`adapters/`)가 「입력 · 후보 답 · 선택지」만 정한다. 사람 라벨이 없는 곳을 있다고 쓰지 않는다 —
이 도구가 만드는 JSON 은 사람이 시트를 채워 들인 것만 사람 라벨이다.
"""
