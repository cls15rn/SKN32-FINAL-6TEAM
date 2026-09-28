# -*- coding: utf-8 -*-
"""판정기를 서버로 올릴 때 시간표를 어떻게 들 것인가 — 재 보고 정한다.

  python mobility_scripts/probe_timetable_load.py

배경: Timetable.load(path, wanted) 의 wanted 는 **케이스 파일에서 뽑은 (노선,역) 집합**이다.
      배치에는 맞지만 서버는 요청마다 어느 역이 올지 미리 모른다.
      Timetable 클래스 주석에 이미 적혀 있다 — "DB 전환 뒤에는 이 클래스가 조회로 바뀐다".

재는 것 셋
  A. 요청마다 재로드   — 한 요청(구간 3개)만큼만 wanted 를 주고 load 시간
  B. 전부 상주         — wanted=None 으로 46만 행 전부. 시간과 **메모리**
  C. (참고) 파일 한 번 훑는 순수 I/O 시간 — A 의 하한

판단 기준을 미리 적어 둔다. 재고 나서 기준을 만들면 결과에 맞추게 된다.
  · A 가 **1초 이하**면 시연·MVP 에 임시로 쓸 수 있다
  · B 가 **1GB 이하**면 상주가 가장 단순하다
  · 둘 다 아니면 **PG 질의(C안)가 유일한 길**이고 저장소 전환이 어댑터의 선행 조건이 된다
"""
import sys
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from collect._paths import PROCESSED                    # noqa: E402
sys.path.insert(0, str(REPO / "scripts"))
import importlib.util                                    # noqa: E402
spec = importlib.util.spec_from_file_location(
    "vt", REPO / "final_project_cs" / "app" / "modules" / "travel_ops"
         / "mobility" / "engine" / "verify_time.py")
vt = importlib.util.module_from_spec(spec)
sys.modules["vt"] = vt
spec.loader.exec_module(vt)

TT = PROCESSED / "mobility" / "timetable_v1.jsonl"
if not TT.exists():
    raise SystemExit(f"없다: {TT}")
size_mb = TT.stat().st_size / 1024 / 1024
print(f"시간표 {TT.name} · {size_mb:,.1f} MB\n")

# ── C. 순수 I/O 하한
t0 = time.time()
n = sum(1 for _ in TT.open(encoding="utf-8"))
io_s = time.time() - t0
print(f"[C] 파일 한 번 훑기(파싱 없음)  {n:,}행 · {io_s:.2f}초   ← A 의 하한")

# ── A. 한 요청만큼
WANTED = {("05호선", "여의도"), ("05호선", "왕십리"),
          ("02호선", "왕십리"), ("02호선", "잠실"),
          ("08호선", "잠실"), ("08호선", "몽촌토성")}
t0 = time.time()
tt_a = vt.Timetable.load(str(TT), WANTED)
a_s = time.time() - t0
print(f"[A] 요청 하나만큼 로드(역 6개)  {tt_a.rows:,}행 · {a_s:.2f}초")

# ── B. 전부
tracemalloc.start()
t0 = time.time()
tt_b = vt.Timetable.load(str(TT), None)
b_s = time.time() - t0
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(f"[B] 전부 상주                   {tt_b.rows:,}행 · {b_s:.2f}초 · "
      f"상주 {cur/1024/1024:,.0f} MB · 최대 {peak/1024/1024:,.0f} MB")
print(f"    (역 {len(tt_b.stations):,} · 출발없음 {tt_b.skipped_no_dep:,}행)")

# ── 판단
print("\n" + "─" * 60)
ok_a = a_s <= 1.0
ok_b = cur / 1024 / 1024 <= 1024
print(f"A(요청마다 재로드) 1초 이하?  {'예' if ok_a else '아니오'}  — {a_s:.2f}초")
print(f"B(전부 상주) 1GB 이하?        {'예' if ok_b else '아니오'}  — {cur/1024/1024:,.0f} MB")
if ok_b:
    print("→ B 가 가장 단순하다. 프로세스당 한 번 올리고 상주시킨다.")
elif ok_a:
    print("→ A 를 시연·MVP 임시 경로로 쓴다. 본안은 여전히 PG 질의다.")
else:
    print("→ 둘 다 안 된다. **PG 질의(C안)가 유일한 길**이고,")
    print("   저장소 전환(팀 PG 접속)이 어댑터 완성의 선행 조건이 된다.")
print("\n※ 어느 쪽이든 본안은 PG 질의다 — Timetable 주석과 ix_timetable_lookup 인덱스가 그 설계다.")
