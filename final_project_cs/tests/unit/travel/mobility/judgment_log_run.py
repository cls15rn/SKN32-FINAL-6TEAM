#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""판정 로그를 켜고 회귀 러너·자기점검을 **그대로** 돌린다 (40번 방 · ◆3 = 회귀 러너에서).

판정기·러너 코드는 안 고친다. `Verifier.verify_case` 를 밖에서 감싸 시간을 재고 1줄씩 적은 뒤,
대상 스크립트의 main() 을 원래 인자 그대로 부른다. 종료 코드도 대상 그대로 돌려준다.

  # 회귀 한 묶음 (저장소 루트, PYTHONPATH=final_project_cs)
  python final_project_cs/tests/unit/travel/mobility/judgment_log_run.py --bundle judgment -- verify_time \\
      --cases final_project_cs/tests/unit/travel/mobility/judgment_legs_v1.json --check-expect --road-graph none

  # 자기점검 (◆1 = 자기점검까지 · 탐침 9,776줄)
  python final_project_cs/tests/unit/travel/mobility/judgment_log_run.py --source selfcheck --bundle selfcheck -- selfcheck \\
      --seeds final_project_cs/tests/unit/travel/mobility/*_legs_v1.json

로그 자리: 기본 `<PROCESSED>/mobility/logs/` — 한 기기 전용(DEVICE.txt). PROCESSED 는 진입점에서 먼저 정한다
(`.env` DATA_DIR → 없으면 저장소 datasets/mobility/processed). 자료 폴더를 못 정하면 로그를 쓰지 않고 멈춘다(73 후속).
★ `--log-dir` 을 주면 그 자리에 쓴다 — 자료 폴더 출처(unset/disabled) 검사를 **의도적으로** 건너뛴다(명시한 자리는 사람이 고른 것).
  판정 자체는 대상 스크립트가 자료 폴더를 다시 찾으므로, 자료가 없으면 대상이 「입력 없음」으로 멈춘다.
"""
import argparse
import sys
from pathlib import Path

REPO = next(p for p in Path(__file__).resolve().parents if (p / "final_project_cs" / "app").is_dir())   # 81: 시험 폴더로 옮김
for _p in (REPO / "final_project_cs", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.modules.travel_ops.mobility.engine import verify_time as vt          # noqa: E402
from app.modules.travel_ops.mobility.devtools.judgment_log import JudgmentLogger, install, DeviceMismatch   # noqa: E402

TARGETS = ("verify_time", "selfcheck")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        raise SystemExit("사용: judgment_log_run.py [옵션] -- {verify_time|selfcheck} <대상 인자…>")
    i = argv.index("--")
    mine, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="판정 로그를 켜고 회귀·자기점검을 돌린다")
    ap.add_argument("--source", default="regression", choices=("regression", "selfcheck", "field", "adhoc"))
    ap.add_argument("--bundle", help="묶음 이름(기본: --cases 파일 이름에서)")
    ap.add_argument("--synthetic", action="store_true", help="합성 자료(mini 시간표 · ODsay mock) — 채점에서 따로 센다")
    ap.add_argument("--log-dir", help="기본 DATA_DIR/travel/processed/mobility/logs")
    ap.add_argument("--run-id", help="여러 묶음을 한 실행으로 묶을 때(ps1 이 넘긴다)")
    ap.add_argument("--no-dump", action="store_true")
    ap.add_argument("--log-allow-other-device", action="store_true")
    a = ap.parse_args(mine)
    if not rest or rest[0] not in TARGETS:
        raise SystemExit(f"대상은 {'/'.join(TARGETS)} 중 하나")
    target, targs = rest[0], rest[1:]

    bundle = a.bundle
    if not bundle:
        for j, t in enumerate(targs):
            if t == "--cases" and j + 1 < len(targs):
                bundle = Path(targs[j + 1]).stem.replace("_legs_v1", "")
            elif t.startswith("--cases="):
                bundle = Path(t.split("=", 1)[1]).stem.replace("_legs_v1", "")
    # ☆`[73 후속 · 3-5]` 로그 폴더를 정하기 **전에** 자료 폴더를 정한다(`.env` DATA_DIR · 없으면 저장소 datasets).
    #   앞 판은 #48(엔진 import 때 .env 를 안 읽음) 뒤로 자리표시 `/data` 를 보고 `C:\data\…\logs` 에 썼다(73 실행).
    #   자료 폴더를 못 정했거나(unset) 계산기가 꺼졌으면(disabled) **로그를 쓰지 않는다** — --log-dir 을 주면 그 자리에 쓴다.
    from app.modules.travel_ops.mobility.engine import paths as _paths
    if _paths.SOURCE == "unset":
        _paths.load_cli_env()
    if not a.log_dir and _paths.SOURCE in ("unset", "disabled"):
        print(f"판정 로그를 안 켰다(판정도 안 돌렸다) — 자료 폴더를 정하지 못했다(출처 {_paths.SOURCE}). "
              f"`.env` 의 DATA_DIR 을 확인하거나 --log-dir 을 준다", file=sys.stderr)
        return 2
    lg = JudgmentLogger(a.log_dir, source=a.source, bundle=bundle, synthetic=a.synthetic, run_id=a.run_id,
                        dump=not a.no_dump, allow_other_device=a.log_allow_other_device)
    if target == "verify_time":
        entry, prog = vt.main, "verify_time"
    else:
        import selfcheck_mobility as sc
        entry, prog = sc.main, "selfcheck_mobility.py"

    code = 0
    started = False
    try:
        with install(vt.Verifier, lg):               # 기기 표식에 막히면 패치 전에 여기서 DeviceMismatch
            started = True
            old = sys.argv
            sys.argv = [prog] + targs
            try:
                entry()
            except SystemExit as e:
                if isinstance(e.code, str):          # 대상이 문장으로 끝냈다(예: 「케이스 X 가 없다」) — 삼키지 않는다
                    print(e.code, file=sys.stderr)
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
            finally:
                sys.argv = old
    except DeviceMismatch as e:
        print(f"판정 로그를 안 켰다(판정도 안 돌렸다) — {e}", file=sys.stderr)
        return 2
    except OSError as e:
        # ☆77(78 GPT 기록) — 로그 자리를 못 열면(명시한 --log-dir 이 파일 · 권한 없음 · 드라이브 없음) 패치 전에
        #   install() 의 open() 에서 멈춘다. 앞 판은 이 예외를 안 잡아 추적 출력과 종료코드 1(판정 실패처럼 보임)이었다.
        #   기기 표식과 같은 「안 켰다 · 안 돌렸다」 = 2. 판정 도중의 한 줄 쓰기 실패는 종전대로 세고(로거 실패 N) 판정은 잇는다.
        if started:
            raise                                    # 연 뒤의 OSError 는 대상(판정·자기점검) 쪽 — 삼키지 않는다
        print(f"판정 로그를 안 켰다(판정도 안 돌렸다) — 로그 자리 {lg.dir} 를 열 수 없다: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
