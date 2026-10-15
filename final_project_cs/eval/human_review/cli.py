# -*- coding: utf-8 -*-
"""명령줄 — `python -m eval.human_review <명령>` (final_project_cs 에서 실행).

    list                                              묶음 · 부분집합 · 행 수
    export <묶음> --out 시트.csv [--subset 이름]       블라인드 시트(AI 라벨 없음)
    apply  <묶음> 시트.csv --reviewer 이름 --out-dir 폴더 [--subset 이름] [--saw-ai-labels]
    agree  <묶음> --a 라벨A.json (--b 라벨B.json | --ref | --ai 출처이름) [--json 결과.json]

    --datasets-root  git 밖 자료 폴더(기본: 환경변수 TRIPILOT_DATASETS_ROOT 또는 <저장소>/datasets)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agreement
from .adapters import REGISTRY, build
from .adapters.paths import Sources
from .core import SheetError, apply_sheet, export_sheet, load_label_file


def _sources(args) -> Sources:
    src = Sources()
    if getattr(args, "datasets_root", None):
        src.datasets_root = Path(args.datasets_root)
    return src


def cmd_list(args) -> None:
    src = _sources(args)
    for name in REGISTRY:
        try:
            b = build(name, src)
        except Exception as exc:                       # 자료가 없는 묶음도 목록에는 보인다
            print(f"{name}: 만들지 못했다 — {type(exc).__name__}: {exc}")
            continue
        subs = ", ".join(f"{s}={len(b.select(s))}" for s in b.subsets)
        print(f"{name}: {len(b.items)}행 · AI 출처 {b.ai_sources() or '없음'}" + (f" · 부분집합 {subs}" if subs else "") + f" — {b.title}")


def cmd_export(args) -> None:
    bundle = build(args.bundle, _sources(args))
    n = export_sheet(bundle, Path(args.out), args.subset)
    print(f"{args.out} — {n}행 (묶음 {bundle.name}{' · ' + args.subset if args.subset else ''}). AI 라벨은 싣지 않았다.")


def cmd_apply(args) -> None:
    bundle = build(args.bundle, _sources(args))
    out = apply_sheet(bundle, Path(args.sheet), args.reviewer, Path(args.out_dir), args.subset, saw_ai_labels=args.saw_ai_labels)
    print(f"{out} — 사람 라벨로 들였다(검토자 {args.reviewer}).")


def cmd_agree(args) -> None:
    bundle = build(args.bundle, _sources(args))
    a = load_label_file(Path(args.a))
    a_labels = a["labels"]
    if args.b:
        b = load_label_file(Path(args.b))
        result = agreement.compare(bundle, a_labels, b["labels"], f"사람 {a['reviewer']}", f"사람 {b['reviewer']}")
    elif args.ref:
        if not bundle.reference_humans:
            raise SystemExit(f"{bundle.name}: 이미 있는 사람 라벨이 없다")
        refs = bundle.reference_humans()
        result = agreement.compare(bundle, a_labels, refs[0]["labels"], f"사람 {a['reviewer']}", f"기존 사람 {refs[0]['reviewer']}(AI 라벨을 보고 단 것)")
    else:
        result = agreement.compare(bundle, a_labels, bundle.ai_labels(args.ai), f"사람 {a['reviewer']}", f"AI 초안 {args.ai}")
    print(agreement.summarize(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--datasets-root")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    ex = sub.add_parser("export")
    ex.add_argument("bundle", choices=list(REGISTRY))
    ex.add_argument("--out", required=True)
    ex.add_argument("--subset")
    ap_apply = sub.add_parser("apply")
    ap_apply.add_argument("bundle", choices=list(REGISTRY))
    ap_apply.add_argument("sheet")
    ap_apply.add_argument("--reviewer", required=True)
    ap_apply.add_argument("--out-dir", required=True)
    ap_apply.add_argument("--subset")
    ap_apply.add_argument("--saw-ai-labels", action="store_true", help="검토자가 AI 라벨을 본 상태로 달았다면 켠다(기록만 남긴다)")
    ag = sub.add_parser("agree")
    ag.add_argument("bundle", choices=list(REGISTRY))
    ag.add_argument("--a", required=True)
    grp = ag.add_mutually_exclusive_group(required=True)
    grp.add_argument("--b")
    grp.add_argument("--ref", action="store_true", help="이미 있는 사람 라벨(AI 라벨을 보고 단 것)과 견준다")
    grp.add_argument("--ai")
    ag.add_argument("--json")
    args = ap.parse_args(argv)
    try:
        {"list": cmd_list, "export": cmd_export, "apply": cmd_apply, "agree": cmd_agree}[args.cmd](args)
    except SheetError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    sys.exit(main())
