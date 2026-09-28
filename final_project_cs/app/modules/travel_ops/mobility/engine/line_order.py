# final_project_cs/app/modules/travel_ops/mobility/engine/line_order.py — line_station_order_v1.json 조회 계층
# 02번 방(막차 판정)이 쓰는 진입점. 판정은 코드가 하고, 값마다 근거 등급을 함께 돌려준다.
#
# 쓰는 법
#   from app.modules.travel_ops.mobility.engine.line_order import LineOrder
#   lo = LineOrder.load()
#   lo.passes("02호선", "강남", dest="성수", target="잠실")
#     → Verdict(value=True, grade="확정", path=[...], reason="...")
#
# ★ 방향은 dir(U/D) 이 아니라 행선지로 정한다. 열차의 dest_nm 이 곧 방향이다.
#   인천2호선·신림선은 같은 dir 에 양 끝 행선지가 섞여 있어 dir 로는 못 가른다.
import json, collections
from dataclasses import dataclass, field
from pathlib import Path

GRADE_ORDER = {"확정": 2, "추정": 1, "근거없음": 0}


def _base(grade):
    return grade.split(":")[0]


def _worst(grades):
    return min(grades, key=lambda g: GRADE_ORDER[_base(g)], default="근거없음")


@dataclass
class Verdict:
    value: object                 # True / False / None(판정 불가)
    grade: str                    # 확정 / 추정 / 근거없음
    reason: str
    path: list = field(default_factory=list)
    weak_edges: list = field(default_factory=list)   # 확정이 아닌 간선들
    checked_at: str = ""          # 데이터 확인 시각(built_at)


class LineOrder:
    def __init__(self, doc):
        self.doc = doc
        self.built_at = doc["built_at"]
        self._g = {}
        self._grade = {}
        self._main = {}
        for ln, v in doc["lines"].items():
            g = {s["station_nm"]: set() for s in v["stations"]}
            gr = {}
            for e in v["edges"]:
                g[e["a"]].add(e["b"]); g[e["b"]].add(e["a"])
                gr[frozenset((e["a"], e["b"]))] = e["grade"]
            self._g[ln], self._grade[ln] = g, gr

    @classmethod
    def load(cls, path=None):
        if path is None:
            from .paths import PROCESSED
            path = PROCESSED / "mobility" / "line_station_order_v1.json"
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    # ── 기본 조회 ──
    def stations(self, line):
        return [s["station_nm"] for s in self.doc["lines"][line]["stations"]]

    def resolve_dest(self, line, dest_nm):
        """시간표 dest_nm 을 역명으로. 못 맞추면 None."""
        if dest_nm is None:
            return None
        if dest_nm in self._g.get(line, {}):
            return dest_nm
        return self.doc.get("dest_alias", {}).get(line, {}).get(dest_nm)

    def dir_label_reliable(self, line):
        return self.doc["lines"][line]["dir_label"]["reliable"]

    def path(self, line, a, b):
        """a → b 최단 경로(역명 리스트). 없으면 None.
        순환선은 경로가 둘이라 짧은 쪽이 나온다 — 실제 운행 방향은 행선지로 정해지므로
        passes() 를 쓸 것."""
        g = self._g.get(line)
        if not g or a not in g or b not in g:
            return None
        prev, q = {a: None}, collections.deque([a])
        while q:
            x = q.popleft()
            if x == b:
                break
            for y in sorted(g[x]):
                if y not in prev:
                    prev[y] = x; q.append(y)
        if b not in prev:
            return None
        out, x = [], b
        while x is not None:
            out.append(x); x = prev[x]
        return out[::-1]

    def _edges_of(self, line, path):
        return [self._grade[line][frozenset((path[i], path[i + 1]))] for i in range(len(path) - 1)]

    # ── 순환선 ──
    def is_loop(self, line):
        return bool(self.doc["lines"].get(line, {}).get("is_loop"))

    def main_order(self, line):
        """본선 역을 FR_CODE 순서로. 순환선의 방향은 이 순서로만 정의된다."""
        if line not in self._main:
            ss = [s for s in self.doc["lines"][line]["stations"] if not s.get("is_spur")]
            ss.sort(key=lambda s: s["fr_order"])
            self._main[line] = [s["station_nm"] for s in ss]
        return self._main[line]

    def _loop_walk(self, line, origin, dest, step, full=False):
        """순환선에서 origin 에서 한 방향으로 걸어 dest 까지 지나는 역 목록.

        full=True 면 dest 를 처음 만나도 멈추지 않고 **한 바퀴**를 다 돈다.
        순환 열차는 행선지를 지나쳐 계속 도는 경우가 있기 때문이다 — 아래 _loop_passes 참고.
        """
        names = self.main_order(line)
        n = len(names)
        i = names.index(origin)
        out = [origin]
        for k in range(1, n + 1):
            x = names[(i + step * k) % n]
            out.append(x)
            if x == dest and not full:
                return out
        return out if full else None

    def _loop_passes(self, line, origin, dest, target, dir=None, full_circuit=False):
        """★순환선은 행선지만으로 방향이 안 정해진다.

        왕십리에서 신도림행을 타도 내선·외선 둘 다 신도림에 닿는다. 지나는 역이 완전히
        다른데 path() 는 짧은 쪽만 준다 — 2호선에서 이게 절반쯤 조용히 틀린다
        (왕십리→잠실이 '열차 없음'으로 나온다). 그래서 순환선만 dir 을 함께 본다.
        dir 은 방향의 정본이 아니지만 **순환선에서는 행선지가 방향을 못 정하므로**
        여기서만 보조로 쓰고, 그때 등급은 노선의 dir 라벨 등급까지 낮춘다.
        """
        names = self.main_order(line)
        if origin not in names or dest not in names or target not in names:
            return None                                   # 지선이 끼면 일반 경로로
        dirs = self.doc["lines"][line].get("direction", {})
        cand = []                                         # (step, 등급, 이유)
        for label, meta in dirs.items():
            step = 1 if meta.get("fr_order") == "asc" else -1
            cand.append((label, step, meta.get("grade", "추정")))
        if dir and self.dir_label_reliable(line):
            cand = [c for c in cand if c[0] == dir] or cand
        best = None
        for label, step, dgrade in cand:
            seq = self._loop_walk(line, origin, dest, step, full=full_circuit)
            if seq is None:
                continue
            hit = target in seq[1:]
            if best is None or (hit and not best[0]):
                best = (hit, seq, dgrade, label)
        if best is None:
            return Verdict(None, "근거없음", f"{origin}→{dest} 순환 경로를 못 만들었다",
                           checked_at=self.built_at)
        hit, seq, dgrade, label = best
        grades = []
        for i in range(len(seq) - 1):
            g = self._grade[line].get(frozenset((seq[i], seq[i + 1])))
            if g:
                grades.append(g)
        grade = _base(_worst(grades + [dgrade]))
        weak = [f"{seq[i]}–{seq[i+1]}({g})" for i, g in enumerate(grades) if _base(g) != "확정"]
        circ = " 한 바퀴 도는 편성" if full_circuit else ""
        reason = (f"{dest}행({label}){circ} 열차는 {origin} 에서 {seq.index(target)}정거장 뒤 "
                  f"{target} 을 지난다" if hit else
                  f"{dest}행({label}) 열차는 {target} 을 지나지 않는다({origin}→{dest})")
        if hit and grade == "근거없음":
            return Verdict(None, "근거없음", reason, path=seq, weak_edges=weak,
                           checked_at=self.built_at)
        return Verdict(hit, grade, reason, path=seq, weak_edges=weak, checked_at=self.built_at)

    # ── 판정 ──
    def passes(self, line, origin, dest, target, dir=None, origin_terminal=False,
               full_circuit=False):
        """origin 에서 dest 행 열차를 탔을 때 target 을 지나는가.

        C5(consistency_check.py)에서 막차 단축운행이 광범위하다는 것이 나왔다.
        2호선 막차 113 조합이 성수행, 5호선 46 조합이 애오개행이다. '그 시각에 열차가
        있는가'만 보면 성립인데 목적지 전에 내려준다. 그래서 이 판정이 필요하다.

        dir 은 순환선에서만 쓴다(_loop_passes 참고). 다른 노선은 행선지로 방향이 정해진다."""
        d = self.resolve_dest(line, dest)
        if d is None:
            return Verdict(None, "근거없음", f"행선지 '{dest}' 를 {line} 역명으로 맞추지 못했다",
                           checked_at=self.built_at)
        g = self._g.get(line, {})
        for nm, what in ((origin, "출발역"), (target, "목적지")):
            if nm not in g:
                return Verdict(None, "근거없음", f"{what} '{nm}' 이 {line} 역 목록에 없다",
                               checked_at=self.built_at)
        if self.is_loop(line):
            v = self._loop_passes(line, origin, d, target, dir, full_circuit)
            if v is not None:
                return v
        if d == origin and origin_terminal:
            # 시발 열차 — 소스가 행선지를 역 자신으로 준다. 어디까지 가는지는 모른다.
            # 노선 구조상 target 이 이어져 있으면 지난다고 보되 등급을 추정으로 내린다.
            p = self.path(line, origin, target)
            if p is None:
                return Verdict(None, "근거없음",
                               f"{origin} 시발 열차이고 {target} 로 이어지는 경로가 없다",
                               checked_at=self.built_at)
            grades = self._edges_of(line, p)
            weak = [f"{p[i]}–{p[i+1]}({g})" for i, g in enumerate(grades) if _base(g) != "확정"]
            grade = _base(_worst(grades + ["추정"]))
            return Verdict(True, grade,
                           f"{origin} 시발 열차(행선지가 역 자신으로 기록됨) — 노선 구조상 "
                           f"{len(p)-1}개 역 뒤 {target} 을 지난다고 본다",
                           path=p, weak_edges=weak, checked_at=self.built_at)
        p = self.path(line, origin, d)
        if p is None:
            return Verdict(None, "근거없음", f"{origin}→{d} 경로를 못 찾았다", checked_at=self.built_at)
        hit = target in p and target != origin
        # ★ 28 — 등급은 출발~목적지 구간만 본다. 목적지 너머 간선(예: 04호선 오이도행의 안산–신길온천 · 시간표 0행)은
        #   「열차가 목적지를 지나는가」와 무관하다 — 경로(행선지까지 이어짐)는 위 path() 가 이미 확인했다.
        seg = p[:p.index(target) + 1] if hit else p
        grades = self._edges_of(line, seg)
        weak = [f"{seg[i]}–{seg[i+1]}({gr})" for i, gr in enumerate(grades) if _base(gr) != "확정"]
        # 경로에 근거없음 간선이 끼어 있으면 '지난다'고 단정하지 않는다.
        grade = _worst(grades) if grades else "근거없음"
        if hit and _base(grade) == "근거없음":
            return Verdict(None, "근거없음",
                           f"{origin}→{d} 경로에 확인 안 된 구간이 있다: {', '.join(weak)}",
                           path=p, weak_edges=weak, checked_at=self.built_at)
        reason = (f"{d}행 열차는 {origin} 에서 {p.index(target)}정거장 뒤 {target} 을 지난다"
                  if hit else f"{d}행 열차는 {target} 앞에서 운행을 마친다({origin}→{d})")
        return Verdict(hit, _base(grade), reason, path=p, weak_edges=weak, checked_at=self.built_at)

    def terminates_here(self, line, station, dest_nm):
        """이 행이 '그 역에서 운행을 마치는' 행인가. True 면 막차 후보에서 뺀다.

        C4 ③ — dest_nm 이 그 역 자신인 행이 3,651개(유효 출발행의 0.8%)이고
        26개 (노선, 역, 방향, 요일) 조합의 막차가 이런 행이다(05호선 여의도 24:47 여의도행 등).
        차량기지 입고나 종착 기록이지 손님이 탈 수 있는 출발이 아니다.
        시간표만 보면 24:47 은 완벽하게 정상적인 값이라 소스 간 대조가 아니면 안 나온다.

        표기 변형(`하남검단산역`)이 있으므로 resolve_dest 로 맞춘 뒤 비교한다.
        맞추지 못하면 False 를 돌려준다 — '종착이 아니다'가 아니라 '이 검사로는 못 거른다'는
        뜻이고, 행선지를 못 맞춘 행은 passes() 가 근거없음으로 따로 걸러 낸다.
        """
        d = self.resolve_dest(line, dest_nm)
        return d is not None and d == station

    def travel_min_on_path(self, line, path, target):
        """**그 열차가 실제로 도는 경로** 위에서 origin→target 소요(분).

        ★ travel_min() 은 최단경로를 쓴다. 순환선에서 이게 조용히 틀린다 —
          강변에서 내선(U) 성수행을 타면 성수까지 40개 역인데 travel_min('강변','성수')
          은 외선 3정거장인 6.2분을 준다. 도착 시각이 통째로 틀린다.
          그래서 판정에 쓴 경로를 그대로 받아 그 위에서만 더한다.
        확정 간선이 하나라도 빠지면 None(소요 판단 불가).
        """
        if not path:
            return None
        idx = {}
        for e in self.doc["lines"][line]["edges"]:
            if e.get("travel_min") is not None:
                idx[frozenset((e["a"], e["b"]))] = e["travel_min"]
        tot = 0.0
        for i in range(len(path) - 1):
            v = idx.get(frozenset((path[i], path[i + 1])))
            if v is None:
                return None
            tot += v
            if path[i + 1] == target:
                return tot
        return None

    def travel_min(self, line, a, b):
        """a→b 관측 소요시간(분). 확정 간선만 더한다. 하나라도 없으면 None."""
        p = self.path(line, a, b)
        if not p:
            return None
        idx = {}
        for e in self.doc["lines"][line]["edges"]:
            if "travel_min" in e:
                idx[frozenset((e["a"], e["b"]))] = e["travel_min"]
        tot = 0
        for i in range(len(p) - 1):
            v = idx.get(frozenset((p[i], p[i + 1])))
            if v is None:
                return None
            tot += v
        return tot
