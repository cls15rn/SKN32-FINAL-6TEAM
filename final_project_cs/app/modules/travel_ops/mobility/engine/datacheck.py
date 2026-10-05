# -*- coding: utf-8 -*-
"""이동 자료(시간표 등, git 밖 · 약 195MB) 배포 확인 — 있는가 · 그 판이 맞는가.

☆`[2026-09-29 문제목록 #24·#25]` 앞 판은 자료가 없으면 첫 판정 호출에서 「판정기 입력이 없다」로 멈췄고, 어떤 판을
  깔아야 하는지(판본 고정 · 파일 검증 · 갱신 방법)가 어디에도 없었다. 이 모듈이 두 가지를 한다.
    · 기동 확인 — 서버가 뜰 때 필수 파일이 다 있는지, 판 명세(manifest)가 있으면 크기·해시가 맞는지. 아니면 멈춘다(결정 15).
    · 판 명세 쓰기 — 자료가 있는 기기(이동 담당)에서 `--write` 로 `manifest_v1.json` 을 만든다. 이 파일을 자료와 함께 옮긴다.

사용(저장소 맨 위에서, `$env:PYTHONPATH = "final_project_cs"`)
  python -m app.modules.travel_ops.mobility.engine.datacheck --write     # 자료 기기 — 판 명세를 만든다
  python -m app.modules.travel_ops.mobility.engine.datacheck             # 어느 기기든 — 확인만(종료 코드 0/1)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "manifest_v1.json"
# runtime.build_verifier 가 없으면 멈추는 것(나머지는 없어도 돌고 근거없음으로 낸다)
OPTIONAL = ("meta", "station_exits", "bike_stations", "bus_profile")


def _paths():
    from .runtime import default_paths
    return {k: Path(v) for k, v in default_paths().items() if k not in ("rules", "holidays")}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


#: 저장소 안 자료 폴더(datasets/mobility/processed)에 이동 담당이 함께 올리는 판 명세 — 칸 이름이 다르다
#: (`files[]` 각 항목의 `path` · `bytes` · `sha256` 이 이 폴더 파일의 값이다. `src_*` 는 정본 원본의 값)
GIT_MANIFEST_NAME = "MANIFEST_git_v1.json"


def manifest_path() -> Path:
    from . import paths
    return paths.PROCESSED / "mobility" / MANIFEST_NAME


def _git_manifest_files(P: dict) -> tuple[Path | None, dict]:
    """이동 담당의 `MANIFEST_git_v1.json` 을 판정기 입력 이름(timetable · order …)의 명세로 옮긴다. 없으면 (None, {})."""
    from . import paths
    base = paths.PROCESSED / "mobility"
    mp = base / GIT_MANIFEST_NAME
    if not mp.exists():
        return None, {}
    by_path = {e["path"].replace("\\", "/"): e for e in json.loads(mp.read_text(encoding="utf-8")).get("files") or []}
    out = {}
    for k, p in P.items():
        try:
            rel = p.relative_to(base).as_posix()
        except ValueError:
            continue
        if rel in by_path:
            out[k] = {"bytes": by_path[rel].get("bytes"), "sha256": by_path[rel].get("sha256")}
    return mp, out


def write_manifest() -> Path:
    files = {}
    for k, p in _paths().items():
        if p.exists():
            files[k] = {"name": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p)}
    doc = {"v": 1, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": files}
    out = manifest_path()
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def check(*, verify_hash: bool = True) -> dict:
    """{"ok", "missing", "mismatched", "manifest", "data_dir_source"} — ok 는 필수 파일이 다 있고 명세와 맞을 때만."""
    from . import paths
    P = _paths()
    missing = [k for k, p in P.items() if k not in OPTIONAL and not p.exists()]
    mismatched = []
    mp = manifest_path()
    has_manifest = mp.exists()
    wanted_files = {}
    if has_manifest:
        wanted_files = json.loads(mp.read_text(encoding="utf-8")).get("files") or {}
    else:
        # ☆`[2026-09-29 자료 폴더 통일]` 우리 명세가 없으면 이동 담당이 자료와 함께 올린 명세(MANIFEST_git_v1.json)로 확인한다
        gp, wanted_files = _git_manifest_files(P)
        if gp is not None:
            mp, has_manifest = gp, True
    if has_manifest:
        for k, want in wanted_files.items():
            p = P.get(k)
            if p is None or not p.exists():
                if k not in OPTIONAL:
                    mismatched.append({"file": k, "why": "명세에 있는데 없다"})
                continue
            if p.stat().st_size != want.get("bytes"):
                mismatched.append({"file": k, "why": f"크기 {p.stat().st_size} ≠ 명세 {want.get('bytes')}"})
            elif verify_hash and _sha256(p) != want.get("sha256"):
                mismatched.append({"file": k, "why": "해시가 명세와 다르다"})
    return {"ok": not missing and not mismatched, "missing": missing, "mismatched": mismatched,
            "manifest": str(mp) if has_manifest else None, "data_dir": str(paths.DATA_DIR),
            "data_dir_source": paths.SOURCE}


def main(argv=None):
    from . import paths
    paths.load_cli_env()
    ap = argparse.ArgumentParser(description="이동 자료 배포 확인 · 판 명세 쓰기")
    ap.add_argument("--write", action="store_true", help="지금 자료로 판 명세(manifest_v1.json)를 쓴다")
    ap.add_argument("--no-hash", action="store_true", help="크기만 본다(해시 생략)")
    a = ap.parse_args(argv)
    if a.write:
        print(f"판 명세를 썼다: {write_manifest()}")
    r = check(verify_hash=not a.no_hash)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
