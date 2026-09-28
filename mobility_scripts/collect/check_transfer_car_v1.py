# mobility_scripts/collect/check_transfer_car_v1.py — transfer_car_v1.json 점검 + 참고 조회 함수 (46번 방 · GPT 대조 반영 · 2026-09-25)
#
# 23 이 표시 필드를 붙일 때 이 lookup() 과 같은 규칙을 쓴다:
#   · 입력은 **판정기가 고른 실제 역열**(from/to 끝점이 아니다) — 순환선·분기역에서 끝점만으로 경로를 다시 고르면 틀린다
#   · 키 = 환승역 · 타고 온 노선 · 그 열차가 환승역 다음에 설 역 · 환승 노선 · 환승 열차가 환승역 다음에 설 역
#   · 정확 일치만. 폴백 없음. 없으면 None(필드를 안 낸다)
# 실행: python mobility_scripts/collect/check_transfer_car_v1.py  → 실패가 있으면 rc=1
import json, sys, collections
from _paths import PROCESSED

OUT_DIR = PROCESSED / "mobility"
doc = json.loads((OUT_DIR / "transfer_car_v1.json").read_text(encoding="utf-8"))
lo = json.loads((OUT_DIR / "line_station_order_v1.json").read_text(encoding="utf-8"))
E = doc["entries"]
adj = {}
for ln, L in lo["lines"].items():
    g = collections.defaultdict(set)
    for e in L["edges"]:
        g[e["a"]].add(e["b"]); g[e["b"]].add(e["a"])
    adj[ln] = g

by_prev = {}
for k, e in E.items():
    if e["prev_nm"] is not None:
        by_prev[(e["station_nm"], e["line"], e["prev_nm"], e["to_line"], e["to_next_nm"])] = e
LOOP6 = {"응암", "역촌", "불광", "독바위", "연신내", "구산"}


def lookup(line_a, path_a, line_b, path_b):
    """path_a = 타고 온 leg 의 실제 역열(마지막이 환승역), path_b = 환승 leg 의 실제 역열(첫째가 환승역).
    반환: positions 리스트 또는 None."""
    if len(path_a) < 2 or len(path_b) < 2:
        return None
    S, p = path_a[-1], path_a[-2]
    to_next = path_b[1]
    e = by_prev.get((S, line_a, p, line_b, to_next))
    if e is None:
        # 이수/총신대입구처럼 환승 노선 쪽 역 이름이 다른 경우 — to_station_nm 로 한 번 더(키는 타고 온 쪽 이름)
        return None
    if e["to_station_nm"] != path_b[0]:
        return None
    return e["positions"]


fails = []
def check(name, ok, detail=""):
    print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)

# T1 폴백 키(*) 없음
check("T1 to_next_nm 없는 항목 0", all(e["to_next_nm"] is not None for e in E.values()),
      [k for k, e in E.items() if e["to_next_nm"] is None][:5])
# T2 종착 키는 노선 끝 역에서만
bad = [k for k, e in E.items() if e["arrive_terminating"] and len(adj[e["line"]][e["station_nm"]]) != 1]
check("T2 중간역 종착 키 0", not bad, bad[:5])
# T3 6호선 응암 순환 구간 없음(양쪽)
bad = [k for k, e in E.items() if (e["line"] == "06호선" and e["station_nm"] in LOOP6)
       or (e["to_line"] == "06호선" and e["to_station_nm"] in LOOP6)]
check("T3 응암 순환 구간 항목 0", not bad, bad[:5])
# T4 인접성
bad = []
for k, e in E.items():
    if e["next_nm"] and e["next_nm"] not in adj[e["line"]][e["station_nm"]]: bad.append(k)
    if e["prev_nm"] and e["prev_nm"] not in adj[e["line"]][e["station_nm"]]: bad.append(k)
    if e["prev_nm"] and e["prev_nm"] == e["next_nm"]: bad.append(k)
    if e["to_next_nm"] not in adj[e["to_line"]][e["to_station_nm"]]: bad.append(k)
check("T4 prev/next/to_next 인접성", not bad, bad[:5])
# T5 한 키에 값 하나(「All」 포함) — 같은 표기 여러 값은 뺐다
bad = [k for k, e in E.items() if len(e["positions"]) != 1]
check("T5 항목당 위치 1개", not bad, bad[:5])
# T6 이음매·최단 규칙으로만 방향을 정한 통합본 단독 값 없음
bad = [k for k, e in E.items() if e["sources"] == ["molit_15151816"]
       and {e["src"]["dir_how"], e["src"]["to_dir_how"]} & {"loop_seam_rule", "loop_shortest"}]
check("T6 순환 규칙 단독 항목 0", not bad, bad[:5])
# T7 GPT 사례 — 종합운동장 9호선 중간역 종착 키가 없고, 방향 없는 조회는 값이 없다
check("T7a 종합운동장 9호선 (종착) 키 없음", not any(k.startswith("종합운동장|09호선|(종착)") for k in E))
check("T7b 불광 6호선 관련 키 없음", not any(k.startswith("불광|") and ("06호선" in k) for k in E))
# T8 참고 조회 — 방향이 반대인 역열을 넣으면 다른 키(또는 None)가 나와야 한다
sample = [e for e in E.values() if e["prev_nm"] and e["next_nm"]][:200]
bad = []
for e in sample:
    fwd = lookup(e["line"], [e["prev_nm"], e["station_nm"]], e["to_line"], [e["to_station_nm"], e["to_next_nm"]])
    if fwd != e["positions"]:
        bad.append(("fwd", e["station_nm"], e["line"]))
    rev = lookup(e["line"], [e["next_nm"], e["station_nm"]], e["to_line"], [e["to_station_nm"], e["to_next_nm"]])
    other = by_prev.get((e["station_nm"], e["line"], e["next_nm"], e["to_line"], e["to_next_nm"]))
    if rev is not None and (other is None or rev != other["positions"]):
        bad.append(("rev", e["station_nm"], e["line"]))
    fake = lookup(e["line"], [e["prev_nm"], e["station_nm"]], e["to_line"], [e["to_station_nm"], "없는역"])
    if fake is not None:
        bad.append(("fake", e["station_nm"], e["line"]))
check(f"T8 참고 조회 정방향·역방향·가짜 방향({len(sample)}건)", not bad, bad[:5])
# T9 분기역(prev None) 항목은 prev 로 조회되지 않는다
bad = [k for k, e in E.items() if e["prev_nm"] is None and not e["arrive_terminating"]
       and any(lookup(e["line"], [n, e["station_nm"]], e["to_line"], [e["to_station_nm"], e["to_next_nm"]]) is e["positions"]
               for n in adj[e["line"]][e["station_nm"]])]
check("T9 분기역 항목 prev 조회 차단", not bad, bad[:5])
# T10 규칙으로 고른 항목 표시
n_rule = sum(1 for e in E.values() if e.get("chosen_by_rule"))
check("T10 병합 규칙 선택 항목에 chosen_by_rule", n_rule == len([d for d in doc.get("merge_disagreements", []) if d.get("chosen")]),
      f"{n_rule}")

print(f"\n항목 {len(E)} · 실패 {len(fails)}")
sys.exit(1 if fails else 0)
