# modules/mobility/candidates.py — k-후보 생성기 (규칙 v0.5 · 20번 방 · 2026-09-20)
#
# 역 순서 777간선(line_station_order_v1) 위에서 **기준별 대표안**을 만든다.
#   최단     : Σ간선 소요 + Σ환승 비용(도보 + 길찾기 + 대기 추정)  이 가장 작은 길
#   최소환승 : 환승 횟수 → 그 다음 시간
#   최소도보 : 환승 도보 분 → 그 다음 시간
# 최단을 먼저 구하고, 최소환승·최소도보는 **최단 × 허용_소요_배수 안에서** 파레토 라벨 탐색으로 찾는다.
# (도보만 줄이려고 열 배를 도는 길은 대표안이 아니다 — rules candidates.허용_소요_배수)
#
# ★ 이 모듈은 **판정하지 않는다.** 후보의 소요·환승·도보는 생성기의 추정치일 뿐이고,
#   시각은 verify_time.py 가 시간표로 확정한다(각 후보를 같은 판정기에 넣는다).
# ★ 순위를 매기지 않는다 — 후보 목록의 순서는 규칙 candidates.기준 의 순서지 우열이 아니다
#   (rules alternatives.순위_미부여). 어느 후보가 낫다고 말하는 필드는 없다.
# ★ 이슈(운행중단 등)를 모른다 — 대안 열거와 같은 원칙으로 판정기가 거른다.
# ★ 환승은 **같은 역명이 두 노선에 있으면** 가능한 것으로 본다 — 단 규칙 station_names.환승_제외_역명(양평 · 신촌)은
#   같은 이름의 **다른 역**이라 잇지 않는다(55 ② · 2026-09-28). 종전 주석(「같은 역명 좌표 차 전부 400 m 안」)은 역명으로
#   좌표를 붙인 판에서 잰 것이라 동명이역을 못 가렸다 — 57 재생성 뒤 양평 53,610 m · 신촌 702 m(54 발견 · 원덕→영등포구청).
#   그 역명이 출발·도착이면 노선(origin_lines · dest_lines)을 같이 받아야 후보를 만든다 — 안 받으면 None(한쪽을 조용히 안 집는다).
import heapq, collections
from dataclasses import dataclass, field

CRITERIA = ("최단", "최소환승", "최소도보")


@dataclass
class Candidate:
    legs: list                      # [{"line","from","to"}]  — 판정기 입력 그대로
    criteria: list                  # 이 후보가 대표하는 기준들(중복 제거 결과)
    est_min: float                  # 생성기 추정 총소요(분) — 판정기가 확정하기 전 값
    transfers: int
    walk_min: float                 # 환승 도보 분 합(길찾기 제외)
    grade: str                      # 추정 / 근거없음(소요 없는 간선을 대체값으로 메운 경우)
    fallback_edges: list = field(default_factory=list)   # 대체값을 쓴 간선
    path: list = field(default_factory=list)              # [(line, station)] — 설명용

    def key(self):
        return tuple((l["line"], l["from"], l["to"]) for l in self.legs)


class CandidateGraph:
    """(노선, 역) 노드 그래프. 간선 = 같은 노선 인접역(소요) · 같은 역명의 노선 갈아타기(환승 비용)."""

    def __init__(self, lo, tw, rules, first_visit=True):
        self.lo, self.tw, self.R = lo, tw, rules
        C = rules["candidates"]
        self.edge_fallback = C["간선_소요_대체_분"]["value"]
        self.transfer_wait = C["환승_대기_추정_분"]["value"]
        self.walk_unknown = C["환승_도보_미상_분"]["value"]
        self.wayfinding = rules["transfer"]["wayfinding_addition_min"]["value"] if first_visit else 0
        self.large_add = rules["transfer"]["large_station_addition_min"]["value"]
        self.large = set(rules["transfer"]["large_station_addition_min"]["stations"])
        self.speed = rules["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        self.no_transfer = set(rules["station_names"]["환승_제외_역명"]["value"])   # 55 ② — 같은 이름의 다른 역
        # 노선 간선
        self.adj = collections.defaultdict(list)     # (line, st) → [((line, st2), ride_min, fallback)]
        self.lines_of = collections.defaultdict(set)
        for ln, L in lo.doc["lines"].items():
            for s in L["stations"]:
                self.lines_of[s["station_nm"]].add(ln)
            for e in L["edges"]:
                t = e.get("travel_min")
                fb = t is None
                w = self.edge_fallback if fb else t
                self.adj[(ln, e["a"])].append(((ln, e["b"]), w, fb))
                self.adj[(ln, e["b"])].append(((ln, e["a"]), w, fb))

    def transfer_walk_min(self, station, from_line, to_line):
        """환승 도보 분(길찾기 제외). 거리표 → 역 최대값 → 대형역 고정값 → **미상값**.

        ★ 판정기의 사다리와 마지막 단만 다르다. 판정기는 거리표에 없으면 0분(근거없음)으로 두지만,
          생성기가 0으로 치면 최소도보 기준이 거리표 밖 역으로 몰린다(rules candidates.환승_도보_미상_분).
        """
        if self.tw is not None:
            w = self.tw.lookup(station, from_line, to_line)
            if w is not None and w.min is not None:
                return w.min
        return self.large_add if station in self.large else self.walk_unknown

    def transfer_cost(self, station, from_line, to_line):
        walk = self.transfer_walk_min(station, from_line, to_line)
        return walk, walk + self.wayfinding + self.transfer_wait

    # ── 라벨 설정 탐색 (기준별 목적 쌍 · 시간 상한) ──
    @staticmethod
    def _obj(criterion, time, transfers, walk):
        """기준별 (1차 목적, 시간). 1차 목적이 같으면 시간이 짧은 쪽."""
        if criterion == "최단":
            return (time, time)
        if criterion == "최소환승":
            return (transfers, time)
        if criterion == "최소도보":
            return (round(walk, 3), time)
        raise ValueError(criterion)

    def search(self, origin, dest, criterion, max_transfers=None, time_bound=None, origin_lines=None, dest_lines=None):
        """origin → dest 대표안 하나. 상태 = ((line, station), 환승 횟수).

        **파레토 라벨 설정** — 상태마다 (1차 목적, 시간) 비지배 라벨을 여럿 둔다. 최소환승·최소도보는
        시간 상한(time_bound = 최단 추정 × candidates.허용_소요_배수) 안에서만 찾는다.
        ★ 사전식 다익스트라(1차 목적 우선)로 하면 최소도보가 「도보 0 인 환승역」(도봉산·까치산)으로
          몰려 최단의 10배짜리 길이 나온다(2026-09-20 무작위 3,000쌍 중 1,304쌍). 시간 상한을 걸면
          상태당 라벨 하나로는 최적성이 깨진다 — 도보는 작지만 시간이 큰 라벨이 뒤에서 상한에 막힌다.
          그래서 라벨을 여럿 둔다. 그래프가 작아(노드 ~900 · 환승 ≤3) 비용은 무시할 만하다.
        ★ 환승 상한(limits.transfers)도 여기서 지킨다 — 넘는 후보는 판정기가 탈락시키므로 대표안이 못 된다.
        """
        if origin not in self.lines_of or dest not in self.lines_of:
            return None
        # 55 ② — 환승 제외 역명(동명이역)이 끝점이면 노선이 있어야 한다. 노선은 그 역명에 실제로 있는 것만 쓴다.
        o_lines = self.lines_of[origin] & set(origin_lines) if origin_lines else self.lines_of[origin]
        d_lines = self.lines_of[dest] & set(dest_lines) if dest_lines else self.lines_of[dest]
        # 같은 역이면 후보가 없다 — 단 동명이역(경의선 양평 → 5호선 양평)은 노선군이 겹치지 않으면 다른 역이다(55 GPT #2)
        if origin == dest and (origin not in self.no_transfer or o_lines & d_lines):
            return None
        if ((origin in self.no_transfer and not origin_lines) or (dest in self.no_transfer and not dest_lines)
                or not o_lines or not d_lines):
            return None
        cap = max_transfers if max_transfers is not None else 99
        bound = time_bound if time_bound is not None else float("inf")
        labels = collections.defaultdict(list)     # state → [(obj, time, tr, walk, fbs, prev_state, prev_label_idx)]
        pq, tick = [], 0

        def push(state, t, tr, wk, fbs, prev):
            nonlocal tick
            if t > bound:
                return
            obj = self._obj(criterion, t, tr, wk)
            L = labels[state]
            for o, *_ in L:                      # 지배당하면 버린다
                if o[0] <= obj[0] and o[1] <= obj[1]:
                    return
            L[:] = [x for x in L if not (obj[0] <= x[0][0] and obj[1] <= x[0][1])]   # 내가 지배하는 것 제거
            L.append((obj, t, tr, wk, fbs, prev))
            tick += 1
            heapq.heappush(pq, (obj, tick, state, len(L) - 1, obj))

        for ln in sorted(o_lines):
            push(((ln, origin), 0), 0.0, 0, 0.0, [], None)
        goal = None
        while pq:
            obj, _, state, idx, _o = heapq.heappop(pq)
            L = labels[state]
            if idx >= len(L) or L[idx][0] != obj:          # 지배로 지워진 라벨
                cur = next((x for x in L if x[0] == obj), None)
                if cur is None:
                    continue
            else:
                cur = L[idx]
            (line, st), tr = state
            if st == dest and line in d_lines:
                goal = (state, cur)
                break
            _, t, _tr, wk, fbs, _prev = cur
            for v, w, fb in self.adj[(line, st)]:          # 같은 노선 다음 역
                push((v, tr), t + w, tr, wk, fbs + ([f"{line} {st}–{v[1]}"] if fb else []), (state, obj))
            # 환승 — 같은 역명의 다른 노선. 출발역·제외 역명에서는 안 갈아탄다(출발역에서 갈아타면 그 노선에서 출발한 것과 같다 ·
            #   동명이역 출발은 no_transfer 라 어차피 막힌다 — 55 GPT #2 점검)
            if st != origin and tr < cap and st not in self.no_transfer:
                for ln in self.lines_of[st]:
                    if ln == line:
                        continue
                    walk, cost = self.transfer_cost(st, line, ln)
                    push(((ln, st), tr + 1), t + cost, tr + 1, wk + walk, fbs, (state, obj))
        if goal is None:
            return None
        # 경로 복원 — (state, obj) 를 따라 올라간다
        path, cur = [], goal
        while cur is not None:
            state, lab = cur
            path.append(state[0])
            prev = lab[5]
            if prev is None:
                break
            pstate, pobj = prev
            plab = next(x for x in labels[pstate] if x[0] == pobj)
            cur = (pstate, plab)
        path.reverse()
        _, t, tr, wk, fbs, _ = goal[1]
        return Candidate(self._legs(path), [criterion], round(t, 1), tr, round(wk, 1),
                         "근거없음" if fbs else "추정", fbs, path)

    @staticmethod
    def _legs(path):
        legs, cur_line, start = [], path[0][0], path[0][1]
        last = path[0][1]
        for ln, st in path[1:]:
            if ln != cur_line:                      # 환승 노드(같은 역명)
                legs.append({"line": cur_line, "from": start, "to": last})
                cur_line, start = ln, st
            last = st
        legs.append({"line": cur_line, "from": start, "to": last})
        return [l for l in legs if l["from"] != l["to"]]

    def candidates(self, origin, dest, criteria=CRITERIA, max_transfers=None, ratio=None,
                   origin_lines=None, dest_lines=None):
        """기준별 대표안. 최단을 먼저 구해 시간 상한(× ratio)을 정하고, 나머지 기준은 그 안에서 찾는다.
        같은 구간열이면 하나로 합치고 기준을 모은다. **순위 없음.**"""
        ratio = ratio if ratio is not None else self.R["candidates"]["허용_소요_배수"]["value"]
        out, seen = [], {}
        kw = {"origin_lines": origin_lines, "dest_lines": dest_lines}
        shortest = self.search(origin, dest, "최단", max_transfers, **kw)
        if shortest is None:
            return out
        bound = shortest.est_min * ratio
        for c in criteria:
            cand = shortest if c == "최단" else self.search(origin, dest, c, max_transfers, bound, **kw)
            if cand is None:
                continue
            k = cand.key()
            if k in seen:
                if c not in seen[k].criteria:
                    seen[k].criteria.append(c)
                continue
            cand.criteria = [c]
            seen[k] = cand
            out.append(cand)
        return out
