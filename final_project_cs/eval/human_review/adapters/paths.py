# -*- coding: utf-8 -*-
"""원본 자료 위치 — git 밖 자료(`datasets/`)는 명령줄 · 환경변수로 가리킨다."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]      # final_project_cs/eval/human_review/adapters/paths.py → 저장소 루트


@dataclass
class Sources:
    repo_root: Path = REPO_ROOT
    datasets_root: Path = field(default_factory=lambda: Path(os.environ.get("TRIPILOT_DATASETS_ROOT", REPO_ROOT / "datasets")))

    @property
    def cs(self) -> Path:
        return self.repo_root / "final_project_cs"

    @property
    def plan_qa(self) -> Path:
        return self.repo_root / "program" / "scripts" / "plan_qa_bench"

    @property
    def human_eval(self) -> Path:
        return self.repo_root / "program" / "scripts" / "rag_gate" / "human_eval"

    def need(self, path: Path, what: str) -> Path:
        if not path.exists():
            raise FileNotFoundError(f"{what} 을(를) 찾지 못했다: {path} — 원본이 git 밖(datasets/)이면 --datasets-root 로 가리킨다")
        return path
