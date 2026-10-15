# -*- coding: utf-8 -*-
"""사람 검토 시트 공통 틀 — 묶음(bundle)이 무엇을 묻는지만 정하면 시트 · 검사 · 들이기는 여기서 같다.

★**블라인드가 규칙이다.** 시트에는 `Item.context`(입력 문장 · 후보 답 · 풀이 기준)와 선택지만 실린다.
  AI 라벨(`Item.ai`)은 시트 쓰기 코드가 읽지도 않는다 — 일치도를 낼 때만 `Bundle.ai_labels()` 로 따로 꺼낸다.
★**검사가 틀리면 아무것도 안 들인다.** 허용 값 밖 · 빈 칸 · 묶음에 없는 id · 중복 id · (선택) 빠진 행이 하나라도 있으면
  `SheetError` 로 멈추고 파일을 만들지 않는다(`activity_judge/review.py` 와 같은 방식).
★사람 라벨 파일은 **이 도구가 만든 것만** 사람 라벨이다. AI 가 시트를 채운 파일을 들이면 안 된다 — 들이는 사람이 `--reviewer` 에
  실제 검토자 이름을 적는다(이름이 비면 거절).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

SEP = ";"                        # 집합 칸의 구분 기호
NOTE_COL = "메모"
ID_COL = "id"
BUNDLE_COL = "묶음"
GUIDE_COL = "고를 수 있는 값"
CRITERIA_COL = "풀이 기준"


class SheetError(ValueError):
    """시트 · 라벨 파일에 문제가 있다 — 메시지에 줄별 문제를 담는다."""


@dataclass(frozen=True)
class Field:
    """사람이 채우는 칸 하나.

    kind: `choice`(하나 고름) · `set`(여럿, `;` 로 구분 — 비워도 됨) · `text`(자유 기재, 비워도 됨)
    allowed: 모든 행에 같은 선택지면 여기, 행마다 다르면 `Item.allowed[key]` 가 이긴다.
    ordered: 선택지에 순서가 있으면(맞다 > 중간 > 틀리다) 가중 카파도 낸다. `exclude` 는 일치도에서 뺄 값(예: 모름).
    """
    key: str
    column: str
    kind: str = "choice"
    allowed: tuple[str, ...] = ()
    required: bool = True
    ordered: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in ("choice", "set", "text"):
            raise ValueError(f"알 수 없는 칸 종류: {self.kind}")


@dataclass
class Item:
    id: str
    context: dict[str, str]                          # 시트에 실리는 열 → 글
    criteria: str = ""                               # 한 행당 풀이 기준(무엇을 묻는가). 라벨이 아니다
    allowed: dict[str, tuple[str, ...]] = field(default_factory=dict)   # 칸 key → 이 행의 선택지
    ai: dict[str, dict[str, Any]] = field(default_factory=dict)         # AI 출처 이름 → {칸 key: 값}. ★시트에 안 실린다
    meta: dict[str, Any] = field(default_factory=dict)                  # 부분집합 고르기용 꼬리표(시트에 안 실린다)


@dataclass
class Bundle:
    name: str
    title: str
    fields: list[Field]
    items: list[Item]
    context_columns: list[str]
    notes: str = ""                                   # 시트 설명(README 용). 시트에는 안 실린다
    subsets: dict[str, Callable[[], list[str]]] = field(default_factory=dict)   # 이름 → id 목록(고정 시드)
    reference_humans: Callable[[], list[dict]] | None = None                   # 이미 있는 사람 라벨(라벨 파일 모양)
    normalize: Callable[[dict], dict] | None = None                           # 들이기 직전 한 행 다듬기(예: 정답과 같은 값을 「함께 맞다」에서 뺌)

    def by_id(self) -> dict[str, Item]:
        return {it.id: it for it in self.items}

    def allowed_for(self, item: Item, f: Field) -> tuple[str, ...]:
        return item.allowed.get(f.key, f.allowed)

    def select(self, subset: str | None) -> list[Item]:
        if not subset:
            return list(self.items)
        if subset not in self.subsets:
            raise SheetError(f"{self.name}: 부분집합 「{subset}」 이 없다 — 있는 것: {', '.join(self.subsets) or '(없음)'}")
        ids = self.subsets[subset]()
        by = self.by_id()
        return [by[i] for i in ids]

    def ai_labels(self, source: str) -> dict[str, dict[str, Any]]:
        return {it.id: it.ai[source] for it in self.items if source in it.ai}

    def ai_sources(self) -> list[str]:
        seen: list[str] = []
        for it in self.items:
            for s in it.ai:
                if s not in seen:
                    seen.append(s)
        return seen


SEED = 20261015          # 부분집합 뽑기 시드(묶음 공통 — 시트 README 에도 적는다)


def sample_ids(ids: list[str], n: int, seed: int = SEED) -> list[str]:
    """`ids` 에서 n 개를 시드 고정 무작위로 뽑아 **원래 순서대로** 돌려준다(두 번째 평가자 카파용 표본)."""
    import random
    pick = set(random.Random(seed).sample(sorted(ids), min(n, len(ids))))
    return [i for i in ids if i in pick]


# ───────────────────────── 시트 쓰기 ─────────────────────────

def header(bundle: Bundle) -> list[str]:
    return [ID_COL, BUNDLE_COL, *bundle.context_columns, CRITERIA_COL, GUIDE_COL,
            *[f.column for f in bundle.fields], NOTE_COL]


def _compress(allowed: tuple[str, ...]) -> str:
    """1 / 2 / 3 … 42 처럼 이어진 숫자 선택지는 「1~42」 로 줄여 보인다(검사는 원래 목록으로 한다)."""
    nums = [int(v) for v in allowed if v.isdigit()]
    rest = [v for v in allowed if not v.isdigit()]
    if len(nums) >= 4 and nums == list(range(nums[0], nums[0] + len(nums))):
        return " / ".join([f"{nums[0]}~{nums[-1]}", *rest])
    return " / ".join(allowed)


def _guide(bundle: Bundle, item: Item) -> str:
    lines = []
    for f in bundle.fields:
        allowed = bundle.allowed_for(item, f)
        if f.kind == "text":
            lines.append(f"{f.column}: 자유롭게 적는다" + ("" if f.required else "(비워도 됨)"))
        elif f.kind == "set":
            lines.append(f"{f.column}: {_compress(allowed)} 중 여럿은 {SEP} 로 구분(없으면 비움)")
        else:
            lines.append(f"{f.column}: {_compress(allowed)}")
    return "\n".join(lines)


def export_sheet(bundle: Bundle, out: Path, subset: str | None = None) -> int:
    """블라인드 시트를 쓴다. 이미 있는 파일은 덮어쓰지 않는다. 쓴 행 수를 돌려준다."""
    out = Path(out)
    if out.exists():
        raise SheetError(f"{out} 이(가) 이미 있다 — 덮어쓰지 않는다(다른 이름을 쓰거나 옛 파일을 _backup 으로 옮긴다)")
    items = bundle.select(subset)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:     # BOM — 엑셀이 한글을 바로 읽는다
        writer = csv.writer(fh)
        writer.writerow(header(bundle))
        for it in items:
            missing = [c for c in bundle.context_columns if c not in it.context]
            if missing:
                raise SheetError(f"{it.id}: 시트 열 {missing} 의 내용이 없다")
            writer.writerow([it.id, bundle.name, *[it.context[c] for c in bundle.context_columns], it.criteria,
                             _guide(bundle, it), *["" for _ in bundle.fields], ""])
    return len(items)


# ───────────────────────── 시트 읽기 · 검사 ─────────────────────────

def _split_set(text: str) -> list[str]:
    return [v.strip() for v in re.split(r"[;；]", text) if v.strip()]


def read_sheet(bundle: Bundle, path: Path, subset: str | None = None, require_complete: bool = True) -> dict[str, dict[str, Any]]:
    """채운 시트를 읽어 검사한다. 하나라도 틀리면 `SheetError` — 아무것도 들이지 않는다.

    `require_complete`: 시트가 (부분집합의) 모든 행을 담고 있어야 한다. 행을 지워서 어려운 것만 건너뛰는 일을 막는다.
    """
    by = bundle.by_id()
    errors: list[str] = []
    labels: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        lost = [c for c in [ID_COL, *[f.column for f in bundle.fields]] if c not in cols]
        if lost:
            raise SheetError(f"시트에 필요한 열이 없다: {lost}")
        for line, row in enumerate(reader, start=2):
            rid = (row.get(ID_COL) or "").strip()
            if not rid:
                errors.append(f"{line}줄: id 가 비었다")
                continue
            if rid not in by:
                errors.append(f"{rid}: 묶음 「{bundle.name}」 에 없는 id")
                continue
            if rid in seen:
                errors.append(f"{rid}: id 가 두 번 나온다")
                continue
            seen.add(rid)
            sheet_bundle = (row.get(BUNDLE_COL) or "").strip()
            if sheet_bundle and sheet_bundle != bundle.name:
                errors.append(f"{rid}: 다른 묶음 「{sheet_bundle}」 의 시트다")
                continue
            item = by[rid]
            entry: dict[str, Any] = {}
            bad = False
            for f in bundle.fields:
                raw = (row.get(f.column) or "").strip()
                allowed = bundle.allowed_for(item, f)
                if f.kind == "choice":
                    if not raw:
                        errors.append(f"{rid}: 「{f.column}」 이(가) 비었다")
                        bad = True
                    elif raw not in allowed:
                        errors.append(f"{rid}: 「{f.column}」 {raw!r} 는 고를 수 없는 값이다({' / '.join(allowed)})")
                        bad = True
                    else:
                        entry[f.key] = raw
                elif f.kind == "set":
                    vals = _split_set(raw)
                    wrong = [v for v in vals if v not in allowed]
                    if not vals and f.required:
                        errors.append(f"{rid}: 「{f.column}」 이(가) 비었다")
                        bad = True
                    elif wrong:
                        errors.append(f"{rid}: 「{f.column}」 {wrong} 는 고를 수 없는 값이다({' / '.join(allowed)})")
                        bad = True
                    else:
                        entry[f.key] = list(dict.fromkeys(vals))
                else:
                    if not raw and f.required:
                        errors.append(f"{rid}: 「{f.column}」 이(가) 비었다")
                        bad = True
                    else:
                        entry[f.key] = raw
            if bad:
                continue
            if bundle.normalize:
                entry = bundle.normalize(entry)
            entry["note"] = (row.get(NOTE_COL) or "").strip()
            labels[rid] = entry
    expected = {it.id for it in bundle.select(subset)}
    extra = sorted(set(labels) - expected)
    if extra:
        errors += [f"{i}: 이 부분집합({subset or '전체'})에 없는 id" for i in extra]
    if require_complete:
        missing = sorted(expected - seen)
        errors += [f"{i}: 시트에 행이 없다" for i in missing]
    if errors:
        shown = errors[:40]
        more = f"\n  … 외 {len(errors) - 40}건" if len(errors) > 40 else ""
        raise SheetError("검토 시트에 문제가 있어 들이지 않았다(" + str(len(errors)) + "건):\n  " + "\n  ".join(shown) + more)
    return labels


# ───────────────────────── 사람 라벨 파일 ─────────────────────────

def label_file_name(bundle: Bundle, reviewer: str, subset: str | None, today: date | None = None) -> str:
    today = today or date.today()
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "", reviewer) or "reviewer"
    return f"{bundle.name}{'-' + subset if subset else ''}_human_{safe}_{today.isoformat()}.json"


def apply_sheet(bundle: Bundle, sheet: Path, reviewer: str, out_dir: Path, subset: str | None = None,
                saw_ai_labels: bool = False, today: date | None = None, require_complete: bool = True) -> Path:
    """검사 → 통과하면 사람 라벨 JSON 을 쓴다(덮어쓰지 않는다). 쓴 경로를 돌려준다."""
    if not reviewer.strip():
        raise SheetError("--reviewer 가 비었다 — 실제로 검토한 사람의 이름(또는 약칭)을 적는다")
    labels = read_sheet(bundle, sheet, subset, require_complete=require_complete)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / label_file_name(bundle, reviewer, subset, today)
    if out.exists():
        raise SheetError(f"{out} 이(가) 이미 있다 — 덮어쓰지 않는다")
    payload = {
        "bundle": bundle.name, "subset": subset, "reviewer": reviewer.strip(),
        "reviewed_on": (today or date.today()).isoformat(), "sheet": Path(sheet).name,
        "saw_ai_labels": bool(saw_ai_labels), "n": len(labels),
        "fields": {f.key: {"column": f.column, "kind": f.kind} for f in bundle.fields},
        "labels": labels,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def load_label_file(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("bundle", "reviewer", "labels"):
        if key not in data:
            raise SheetError(f"{path}: 라벨 파일에 「{key}」 가 없다")
    return data


def iter_fields(bundle: Bundle) -> Iterable[Field]:
    return [f for f in bundle.fields if f.kind != "text"]
