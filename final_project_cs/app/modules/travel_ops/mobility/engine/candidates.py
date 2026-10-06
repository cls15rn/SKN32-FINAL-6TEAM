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
# ★ 환승은 **같은 역명이 두 노선에 있으면** 가능한 것으로 본다(+ 105: 이름 맞춤표 transfer_name_map_v1 의 같은 역 묶음) — 단 규칙 station_names.환승_제외_역명(양평 · 신촌)은
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

    def _partners(self, line, station):
        """(노선, 역)과 이름이 다른 같은 환승역의 [(노선, 역 이름)] — 역 순서 표에 실제로 있는 것만(105)."""
        f = getattr(self.tw, "partners", None)
        return [m for m in (f(line, station) if f else ()) if m[0] in self.lines_of.get(m[1], ())]

    def transfer_walk_min(self, station, from_line, to_line):
        """환승 도보 분(길찾기 제외). 거리표 → 역 최대값 → 대형역 고정값 → **미상값**.

        ★ 판정기의 사다리와 마지막 단만 다르다. 판정기는 거리표에 없으면 0분(근거없음)으로 두지만,
          생성기가 0으로 치면 최소도보 기준이 거리표 밖 역으로 몰린다(rules candidates.환승_도보_미상_분).
        """
        if self.tw is not None:
            w = self.tw.lookup(station, from_line, to_line)
            if w is not None and w.min is not None:
                return w.min
            # ☆`[2026-09-29 문제목록 #8]` 거리표에 없는 환승은 판정기와 **같은 대체 출처**(측정 분포 상위 10%)를 쓴다.
            #   앞 판은 근거없음 고정값(3분 · 대형역 2분)을 써서 생성기와 판정기가 다른 값을 봤다.
            nf = self.tw.network_fallback()
            if nf is not None:
                return nf.min
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

    def search(self, origin, dest, criterion, max_transfers=None, time_bound=None, origin_lines=None, dest_lines=None,
               avoid_lines=None, no_fallback=False):
        """origin → dest 대표안 하나. 상태 = ((line, station), 환승 횟수).

        **파레토 라벨 설정** — 상태마다 (1차 목적, 시간) 비지배 라벨을 여럿 둔다. 최소환승·최소도보는
        시간 상한(time_bound = 최단 추정 × candidates.허용_소요_배수) 안에서만 찾는다.
        ★ 사전식 다익스트라(1차 목적 우선)로 하면 최소도보가 「도보 0 인 환승역」(도봉산·까치산)으로
          몰려 최단의 10배짜리 길이 나온다(2026-09-20 무작위 3,000쌍 중 1,304쌍). 시간 상한을 걸면
          상태당 라벨 하나로는 최적성이 깨진다 — 도보는 작지만 시간이 큰 라벨이 뒤에서 상한에 막힌다.
          그래서 라벨을 여럿 둔다. 그래프가 작아(노드 ~900 · 환승 ≤3) 비용은 무시할 만하다.
        ★ 환승 상한(limits.transfers)도 여기서 지킨다 — 넘는 후보는 판정기가 탈락시키므로 대표안이 못 된다.
        ☆`[2026-10-01 87]` avoid_lines — 이 노선은 타지 않는다(혼합 후보 생성기가 사고로 **운행 중단**인 노선을 피할 때만 ·
          기본 None = 앞 판과 같다). 판정은 여전히 판정기가 한다 — 피하지 않아도 판정기가 떨어뜨리지만, 혼합 후보는 끊는
          지점마다 지하철 잔여 소요 추정으로 고르므로 막힌 노선으로 잰 추정이 고르는 순서를 흐리지 않게 한다.
        """
        if origin not in self.lines_of or dest not in self.lines_of:
            return None
        avoid = frozenset(avoid_lines or ())
        # 55 ② — 환승 제외 역명(동명이역)이 끝점이면 노선이 있어야 한다. 노선은 그 역명에 실제로 있는 것만 쓴다.
        o_lines = (self.lines_of[origin] & set(origin_lines) if origin_lines else self.lines_of[origin]) - avoid
        d_lines = (self.lines_of[dest] & set(dest_lines) if dest_lines else self.lines_of[dest]) - avoid
        # 같은 역이면 후보가 없다 — 단 동명이역(경의선 양평 → 5호선 양평)은 노선군이 겹치지 않으면 다른 역이다(55 GPT #2)
        if origin == dest and (origin not in self.no_transfer or o_lines & d_lines):
            return None
        # ☆105 — 끝점이 **이름이 다른 같은 환승역**(총신대입구 = 이수 · 서울역 = GTX-A 「서울」)이면 그 역의 다른 이름 노드에서도
        #   출발·도착한다(노선을 안 준 경우만 — 노선을 줬으면 그 노선의 역만). 앞 판 없이 환승 이음만 넣으면 「사당→이수」가
        #   총신대입구에 닿은 뒤 이수로 「갈아타는」 길이 0 인 구간을 만든다. 같은 역끼리는 후보가 없다.
        o_alt = [] if origin_lines else sorted({m for ln in self.lines_of[origin] for m in self._partners(ln, origin)
                                                if m[0] not in avoid and m[1] not in self.no_transfer})
        d_alt = set() if dest_lines else {m for ln in self.lines_of[dest] for m in self._partners(ln, dest)
                                          if m[0] not in avoid and m[1] not in self.no_transfer}
        if any(m[1] == dest for m in o_alt):
            return None
        o_names = {origin} | {m[1] for m in o_alt}
        if (origin in self.no_transfer and not origin_lines) or (dest in self.no_transfer and not dest_lines):
            return None
        # (GPT 105 #4) 끝점이 비었는지는 **원래 이름 + 다른 이름 노드를 합쳐서** 본다 — 원래 이름의 노선이 전부 avoid 여도
        #   같은 역의 다른 이름 노드에서 출발·도착할 수 있다.
        if not (o_lines or o_alt) or not (d_lines or d_alt):
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
        for m in o_alt:
            push((m, 0), 0.0, 0, 0.0, [], None)
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
            if (st == dest and line in d_lines) or (line, st) in d_alt:
                goal = (state, cur)
                break
            _, t, _tr, wk, fbs, _prev = cur
            for v, w, fb in self.adj[(line, st)]:          # 같은 노선 다음 역
                if fb and no_fallback:                      # 105 — 소요 없는 간선(관측 없는 구조만의 이음)을 타지 않는 탐색
                    continue
                push((v, tr), t + w, tr, wk, fbs + ([f"{line} {st}–{v[1]}"] if fb else []), (state, obj))
            # 환승 — 같은 역명의 다른 노선. 출발역·제외 역명에서는 안 갈아탄다(출발역에서 갈아타면 그 노선에서 출발한 것과 같다 ·
            #   동명이역 출발은 no_transfer 라 어차피 막힌다 — 55 GPT #2 점검)
            if st not in o_names and tr < cap and st not in self.no_transfer:
                for ln in self.lines_of[st]:
                    if ln == line or ln in avoid:
                        continue
                    walk, cost = self.transfer_cost(st, line, ln)
                    push(((ln, st), tr + 1), t + cost, tr + 1, wk + walk, fbs, (state, obj))
                # ☆105 — **이름이 다른 같은 환승역**(04호선 총신대입구 ↔ 07호선 이수 · 서울역 ↔ GTX-A 「서울」). 묶음은 자료
                #   transfer_name_map_v1.json(거리표 조회 계층이 읽는다)에서만 온다 — 앞 판은 역명이 같아야만 이어 이 환승이 없었다.
                for ln, st2 in self._partners(line, st):
                    if ln in avoid or st2 in self.no_transfer:
                        continue
                    walk, cost = self.transfer_cost(st2, line, ln)
                    push(((ln, st2), tr + 1), t + cost, tr + 1, wk + walk, fbs, (state, obj))
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

    def search_solid(self, origin, dest, criterion, max_transfers=None, time_bound=None, **kw):
        """대표안 **하나만** 쓰는 쪽(혼합 후보의 지하철 구간 · 버스 환승 뒤 지하철)이 부른다 — 소요 없는 간선을 타지 않는 길을 먼저,
        그런 길이 없을 때만 종전 탐색(105 · candidates() 가 둘 다 내는 것과 같은 까닭)."""
        return (self.search(origin, dest, criterion, max_transfers, time_bound, no_fallback=True, **kw)
                or self.search(origin, dest, criterion, max_transfers, time_bound, **kw))

    @staticmethod
    def _legs(path):
        legs, cur_line, start = [], path[0][0], path[0][1]
        last = path[0][1]
        for ln, st in path[1:]:
            if ln != cur_line:                      # 환승 노드(같은 역명 · 105: 이름이 다른 같은 역이면 뒤 구간은 그 노선의 역 이름으로 출발)
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
        # ☆105 — 최단이 **소요 없는 간선**(역 순서 표에 구조만 있고 열차가 관측되지 않은 이음 — GTX-A 서울–수서 등)을 탔으면, 그 간선을
        #   타지 않는 최단을 같이 낸다. 소요 없는 간선은 대체 분(몇 분)으로 쳐서 20 km 를 몇 분에 가는 길이 최단이 되고, 판정기가
        #   「그 방향 열차 없음」으로 떨어뜨리면 진짜 최단이 후보에 없다. 다른 기준의 시간 상한도 진짜 최단 기준으로 잡는다.
        #   (서울역 ↔ GTX-A 「서울」을 이으면서 서울역→잠실·삼성 등에서 드러났다. 앞 판에도 연신내·수서에서 타는 구간에는 있었다.)
        solid = self.search(origin, dest, "최단", max_transfers, no_fallback=True, **kw) if shortest.fallback_edges else None
        bound = (solid or shortest).est_min * ratio
        if solid is not None:
            solid.criteria = ["최단"]
            seen[solid.key()] = solid
            out.append(solid)
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


# ── 지하철+버스 혼합 후보(환승 1회) · 87번 방 2026-10-01 ─────────────────────────────
#
# 두 모양만 만든다(12 전달 §2 · 87):
#   A. 버스 → 지하철 — 출발점 근처 정류장(정류장_반경_m)에서 타는 노선이 **역 앞**(정류장↔그 역 가장 가까운 출구 직선
#      ≤ 정류장_반경_m)을 지나면 거기서 내려 그 역에서 목적지 역까지 지하철(이 파일의 최단 탐색).
#   B. 지하철 → 버스 — 출발 역에서 지하철로 **역 앞 정류장이 있는 역**까지 간 뒤, 그 정류장에서 목적지 근처 정류장까지
#      한 노선 버스.
# 안 하는 것: 환승 2회 이상 혼합(버스·지하철을 두 번 이상 바꾸기) · 버스→버스 · 통합 그래프 탐색기(12 §3).
# 지하철 구간 안의 지하철↔지하철 환승은 있을 수 있다 — 총 환승(버스↔지하철 1 + 지하철 안) ≤ 환승 상한(limits.transfers).
#
# ★ 이 생성기도 **판정하지 않는다**(위 다목적 후보와 같다). 끊는 지점 고르기·순서만 정하고, 시각·성립·환승 도보·근접 상한은
#   판정기(verify_case · 19 환승 도보 · 39 best/worst)가 그대로 본다. est_min 은 고르는 순서용 추정이다.
# ★ 끊는 지점은 **노선당 최대 N**(규칙 candidates.혼합_끊는_지점_최대 · 변경안) — 출발에서 가까운 순이 아니라 **지하철 쪽
#   소요 추정이 짧은 순**(A = 끊는 역 → 목적지 역 · B = 출발 역 → 끊는 역). 같은 노선이 역 앞을 열 번 지나도 셋만 본다.
# ★ 공항버스: A 에서만, **공항 정류장에서 타는** 노선 단위로(공항 → 시내). 공항으로 가는 공항버스는 만들지 않는다 — 도착점이
#   공항인 B(공항행)는 skipped_airport 에 노선만 적어 부르는 쪽이 no_data 로 밝힌다(37 정본 공항행 시각 목록이 판정기에 아직 없음 ·
#   12 §2 · 37). 시내 정류장에서 타고 내리는 공항버스 구간(A 의 공항 아닌 승차 · B 의 시내 하차)은 조용히 뺀다.
# ★ 가상 정류장(이름에 「(가상)」 — 노선 형상용 점 · 영종대교(가상) 등)은 끊는 지점이 아니다.
#: 혼합 후보 상한 — 규칙 candidates.혼합_최대 **변경안** 값(27 규칙 32 · 규칙 파일은 모아서 한 번에). 규칙에 들어가면 규칙 값.
MIX_MAX_PROPOSED = 3
#: 노선당 끊는 지점 상한 — 규칙 candidates.혼합_끊는_지점_최대 **변경안** 값.
MIX_CUTS_PER_ROUTE_PROPOSED = 3
MIX_SHAPES = ("A", "B")
AIRPORT_TYPE = "공항"
_VIRTUAL = "(가상)"


def mix_rule(rules, key, proposed):
    """규칙 candidates.<key> — 칸이 없으면 변경안 값(proposed). 1 이상 정수가 아니면 ValueError(85 _station_k 와 같은 규칙)."""
    n = ((rules or {}).get("candidates") or {}).get(key)
    k = n["value"] if n and n.get("value") is not None else proposed
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"candidates.{key} 는 1 이상 정수 — 받은 값 {k!r}")
    return k


def is_airport_stop(row):
    return "공항" in str(row.get("station_nm") or "")


@dataclass
class MixedCandidate:
    shape: str                      # "A"(버스→지하철) / "B"(지하철→버스)
    legs: list                      # 판정기 입력 그대로 — 버스 {"mode":"bus","route","from","to"} · 지하철 {"line","from","to"}
    est_min: float                  # 고르는 순서용 추정(접근 도보 + 대기 + 승차 + 환승 + 지하철 + 이탈 도보) — 판정 아님
    transfers: int                  # 버스↔지하철 1 + 지하철 안 환승
    walk_in_m: float                # 출발점 → 첫 승차 지점 직선(A = 정류장 · B = 부르는 쪽이 준 출발 역 도보)
    walk_out_m: float               # 마지막 하차 지점 → 도착점 직선(A = 부르는 쪽이 준 도착 역 도보 · B = 정류장)
    link_m: float                   # 정류장 ↔ 끊는 역 가장 가까운 출구(없으면 역 좌표) 직선
    route_nm: str
    cut_station: str                # 끊는 역(A = 내려서 타는 역 · B = 내려서 버스로 가는 역)
    end_station: str                # 지하철 쪽 다른 끝(A = 도착 역 · B = 출발 역)
    sub_walk_min: float             # 지하철 안 환승 도보 분(생성기 값)
    grade: str                      # 추정 / 근거없음(지하철 쪽이 대체 간선을 밟음)
    fallback_edges: list = field(default_factory=list)

    def key(self):
        return tuple((l.get("route") or l.get("line"), l["from"], l["to"]) for l in self.legs)


class _PointIndex:
    """좌표 목록의 근처 찾기 — numpy 가 있으면 한 번에, 없으면 파이썬 반복(같은 식 · geo.meters 평면 근사)."""

    def __init__(self, items, lat_of, lng_of):
        self.items = [x for x in items if lat_of(x) is not None and lng_of(x) is not None]
        try:
            import numpy as np
            self.np = np
            self.lat = np.array([float(lat_of(x)) for x in self.items])
            self.lng = np.array([float(lng_of(x)) for x in self.items])
        except Exception:          # pragma: no cover — numpy 는 팀 목록에 있다
            self.np = None
            self._ll = [(float(lat_of(x)), float(lng_of(x))) for x in self.items]

    def near(self, lat, lng, within_m):
        """(거리 m, item) 가까운 순."""
        if not self.items:
            return []
        if self.np is not None:
            np = self.np
            dy = (self.lat - lat) * 111_320
            dx = (self.lng - lng) * 111_320 * np.cos(np.radians((self.lat + lat) / 2))
            d = np.hypot(dx, dy)
            idx = np.nonzero(d <= within_m)[0]
            out = [(float(d[i]), self.items[i]) for i in idx]
        else:
            import math
            out = []
            for (a, b), x in zip(self._ll, self.items):
                dy = (a - lat) * 111_320
                dx = (b - lng) * 111_320 * math.cos(math.radians((a + lat) / 2))
                dd = math.hypot(dx, dy)
                if dd <= within_m:
                    out.append((dd, x))
        out.sort(key=lambda t: t[0])
        return out


import weakref as _weakref
_IDX = _weakref.WeakKeyDictionary()       # 정류장·역 표 객체 → 색인(표가 같으면 요청마다 다시 안 만든다)


def _stop_index(bus):
    ix = _IDX.get(bus)
    if ix is None:
        rows = [s for rs in bus.stops.values() for s in rs]
        ix = _IDX[bus] = _PointIndex(rows, lambda s: s.get("lat"), lambda s: s.get("lng"))
    return ix


def _station_index(sc):
    """역 좌표표의 **노선 레코드 전부**(GPT 87 #6 — 물리적 역의 대표 하나만 두면 대표 좌표에서 먼 출구를 놓친다). 부르는 쪽이
    물리적 역(phys_key)으로 묶는다."""
    ix = _IDX.get(sc)
    if ix is None:
        recs = [v for v in sc.by_key.values() if v.get("lat") is not None]
        ix = _IDX[sc] = _PointIndex(recs, lambda v: v.get("lat"), lambda v: v.get("lng"))
    return ix


def exit_reach_m(sc, ex):
    """출구 ↔ 그 역 레코드 좌표의 가장 먼 거리(m) — 역 좌표로 「정류장 근처 역」을 고를 때 넉넉히 볼 여유(GPT 87 #6 · 근거 없는
    300 m 고정값 대신 출구표에서 잰다). 출구마다 같은 역명 레코드 중 가장 가까운 것까지의 거리, 그 최대. 출구표가 없으면 0."""
    if ex is None or sc is None:
        return 0.0
    import math
    by_nm = collections.defaultdict(list)
    for v in sc.by_key.values():
        if v.get("lat") is not None:
            by_nm[v["station_nm"]].append(v)
    reach = 0.0
    for nm, exits in (getattr(ex, "exits", None) or {}).items():
        recs = by_nm.get(nm)
        if not recs:
            continue
        for e in exits:
            d = min(math.hypot((r["lat"] - e["lat"]) * 111_320,
                               (r["lng"] - e["lng"]) * 111_320 * math.cos(math.radians((r["lat"] + e["lat"]) / 2)))
                    for r in recs)
            reach = max(reach, d)
    return reach


_NEAR_ST = _weakref.WeakKeyDictionary()   # 역 좌표표 객체 → {(near_m, 정류장 키): [(출구 직선 m, 역 레코드)]}


class MixedGenerator:
    """혼합 후보 생성기(A·B). 부르는 쪽(판정기 verify_multi · plan.Planner)이 끝점과 역 목록을 주고, 후보를 판정기에 넣는다.

    인자
      cg            CandidateGraph(지하철 최단 탐색)        bus · sc · ex   버스 노선·정류장 / 역 좌표 / 역 출구
      radius_m      출발·도착점 근처 정류장 반경(alternatives.정류장_반경_m · 버스 직행과 같은 값)
      near_m        정류장이 「역 앞」인 거리(정류장 ↔ 그 역 가장 가까운 출구 직선 · 같은 규칙 값 — 「같은 지점으로 볼 수 있는
                    거리」. 도보 상한 limits.walk_m 은 판정기가 정류장↔역 환승에서 따로 본다)
      cuts          노선당 끊는 지점 상한 N          tlim   총 환승 상한(limits.transfers · 동행별)
      excluded      버스 유형 제외(bus.route_type_제외)
      ride_min      (route, 타는 행, 내리는 행) → 승차 분 추정 또는 None(속도 근거 없음 → 그 노선은 안 쓴다)
      wayfinding    환승 길찾기 가산 분 · walk_speed · detour  도보 분 식(직선 × 우회 ÷ 속도)
      avoid_lines   사고로 운행 중단인 노선(지하철 탐색에서 피함)   skip_at  {(노선, 역)} 사고로 서지 않는 역(끊는 역 노선에서 뺌)

    순서: ① 끝점 근처 정류장의 노선마다 역 앞 정류장(끊는 지점)을 모은다 ② 지하철 쪽 소요는 **한 번의 최단 거리 표**(목적지
    역에서 거꾸로 · 출발 역에서 앞으로 — 같은 그래프 비용)로 끊는 역마다 잰다 ③ 노선마다 지하철 쪽이 짧은 N 개 ④ 전체를 추정
    소요 순으로. 지하철 구간열(legs)은 **부르는 쪽이 판정에 넣을 후보만** materialize() 로 채운다(그때 환승 상한도 본다).
    """

    def __init__(self, cg, bus, sc, ex, *, radius_m, near_m, cuts, tlim, excluded=(), ride_min=None,
                 wayfinding=0, walk_speed=1.04, detour=1.4, avoid_lines=(), skip_at=()):
        self.cg, self.bus, self.sc, self.ex = cg, bus, sc, ex
        self.radius_m, self.near_m, self.cuts, self.tlim = radius_m, near_m, cuts, tlim
        self.excluded = set(excluded or ())
        self.ride_min = ride_min
        self.wayfinding, self.walk_speed, self.detour = wayfinding, walk_speed, detour
        self.avoid = frozenset(avoid_lines or ())
        self.skip_at = frozenset(skip_at or ())
        self.skipped_airport = []          # 공항행 공항버스(만들지 않음 · 부르는 쪽이 no_data 로 밝힌다)
        self.no_cut = []                   # 역 앞을 한 번도 안 지나 끊지 못한 노선(근접 밖)

    # ── 조각 ──
    def _walk_min(self, m):
        return (m or 0) * self.detour / self.walk_speed / 60

    def _lines(self, rec):
        """역 레코드 → 지하철 탐색에 쓸 노선(동명이역은 그 물리적 역 노선만) · 그 역에서 서지 않는 노선 · 운행 중단 노선은 뺀다."""
        nm = rec["station_nm"]
        ls = set(self.sc.group_lines(rec)) - {None}
        if not ls:
            ls = {rec.get("line")}
        return {l for l in ls if (l, nm) not in self.skip_at and l not in self.avoid and l in self.cg.lines_of.get(nm, ())}

    def _dist(self, seeds):
        """지하철 최단 거리 표 — seeds = [(역명, 노선 집합, 시작 비용 분)]. {(노선, 역): 분}. 비용은 탐색(search)과 같다
        (간선 소요 · 환승 = 도보 + 길찾기 + 대기 추정). 간선·환승 비용이 방향과 무관해(환승 도보 거리표만 방향별 · 차이 작음)
        「목적지에서 거꾸로」 잰 값을 「끊는 역 → 목적지」 추정으로 쓴다 — 고르는 순서용이고 실제 구간열은 materialize 가 다시 찾는다."""
        cg, dist, pq, tick = self.cg, {}, [], 0
        for nm, lines, c0 in seeds:
            for ln in lines:
                heapq.heappush(pq, (c0, tick, (ln, nm)))
                tick += 1
        while pq:
            d, _, node = heapq.heappop(pq)
            if node in dist:
                continue
            dist[node] = d
            line, st = node
            for v, w, _fb in cg.adj[node]:
                if v not in dist:
                    tick += 1
                    heapq.heappush(pq, (d + w, tick, v))
            if st not in cg.no_transfer:
                for ln in cg.lines_of[st]:
                    if ln == line or ln in self.avoid or (ln, st) in dist:
                        continue
                    tick += 1
                    heapq.heappush(pq, (d + cg.transfer_cost(st, line, ln)[1], tick, (ln, st)))
        return dist

    @staticmethod
    def _best(dist, nm, lines):
        vals = [dist[(l, nm)] for l in lines if (l, nm) in dist]
        return min(vals) if vals else None

    def _link(self, row, rec):
        """정류장 행 ↔ 역 — 그 역에서 정류장에 가장 가까운 출구(없으면 역 좌표) 직선. 판정기 _stop_station_walk 와 같은 기준."""
        nm = rec["station_nm"]
        if self.ex is not None:
            e = self.ex.nearest(nm, row["lat"], row["lng"], rec.get("line"))
            if e is not None:
                return e[0]
        import math
        dy = (rec["lat"] - row["lat"]) * 111_320
        dx = (rec["lng"] - row["lng"]) * 111_320 * math.cos(math.radians((rec["lat"] + row["lat"]) / 2))
        return math.hypot(dx, dy)

    def _stations_by(self, row):
        """정류장 행 → 역 앞으로 볼 역들 [(출구 직선 m, 역 레코드)] — 역 좌표로 넉넉히(근접 + 출구표에서 잰 출구 최대 거리) 고른 뒤
        출구 거리로 near_m 안만 · 물리적 역마다 하나.
        정류장(ID · 없으면 좌표)마다 한 번만 잰다(표 객체에 붙은 캐시 — 같은 표면 요청이 달라도 값이 같다)."""
        # ☆(GPT 87 #3) 칸 = 역 좌표표(약한 키) 아래 (출구표 · 반경 · 정류장 ID · 정류장 좌표) — 출구표가 다르거나 같은 ID 의 좌표가
        #   바뀐 표면 다른 칸. 출구표 객체는 칸에 같이 붙잡아 둔다(id 재사용 방지).
        per_sc = _NEAR_ST.setdefault(self.sc, {})
        slot = per_sc.get(id(self.ex))
        if slot is None or slot[0] is not self.ex:
            slot = per_sc[id(self.ex)] = (self.ex, {}, exit_reach_m(self.sc, self.ex))
        cache, reach = slot[1], slot[2]
        k = (self.near_m, row.get("station_id"), row["lat"], row["lng"])
        got = cache.get(k)
        if got is None:
            best = {}
            for _d, rec in _station_index(self.sc).near(row["lat"], row["lng"], self.near_m + reach + 1):
                m = self._link(row, rec)
                pk = self.sc.phys_key(rec)
                if m <= self.near_m and (pk not in best or m < best[pk][0]):
                    best[pk] = (m, rec)               # 물리적 역마다 하나(그 역의 출구 중 정류장에 가장 가까운 쪽)
            got = cache[k] = sorted(best.values(), key=lambda t: (t[0], t[1]["station_nm"]))
        return got

    def _boards(self, lat, lng, walk_lim):
        """점 근처 정류장 행 [(m, row, route)] — 반경·도보 상한·유형 제외."""
        out = []
        for d, s in _stop_index(self.bus).near(lat, lng, self.radius_m):
            if d > walk_lim:
                continue
            r = self.bus.by_id.get(s["route_id"])
            if r is None or r.route_type_nm in self.excluded:
                continue
            out.append((d, s, r))
        return out

    def _ride(self, r, x, y):
        if self.ride_min is None:
            return None
        return self.ride_min(r, x, y)

    @staticmethod
    def _walkable(rec, lat, lng, excl_m):
        if not excl_m:
            return False
        import math
        dy = (rec["lat"] - lat) * 111_320
        dx = (rec["lng"] - lng) * 111_320 * math.cos(math.radians((rec["lat"] + lat) / 2))
        return math.hypot(dx, dy) <= excl_m

    def _ends(self, ends):
        out = []
        for nm, lines, w in ends:
            rec = self.sc.resolve(nm, lines) if self.sc else None
            if rec is None:
                continue
            ls = self._lines(rec)
            if ls:
                out.append((nm, ls, w))
        return out

    # ── A: 버스 → 지하철 ──
    def bus_to_subway(self, lat, lng, targets, walk_lim, excl_m=None, excl_st=None):
        """출발점(lat, lng) → 버스 → 끊는 역 → 지하철 → targets 중 하나. 구간열의 지하철 쪽은 materialize() 가 채운다.
        targets = [(역명, 노선 목록 또는 None, 도착 역 → 도착점 직선 m)] — 부르는 쪽이 도보 상한 안 역(막힌 역 뺌)을 준다.
        excl_m: 출발점에서 이 거리(역 좌표 직선) 안의 역은 끊지 않는다 — 걸어서 갈 수 있는 역까지 버스를 타는 후보는
        그 역에서 타는 지하철 후보와 같다(역 기준 verify_multi 는 정류장 반경 · None = 같은 역만 뺌).
        excl_st: 끊지 않을 물리적 역(phys_key) — 장소 기준 plan 은 **지하철 후보가 실제로 본 역**(장소마다 가까운 역 최대 k)만 준다
        (GPT 87 #6 — 도보 상한 안이라는 것만으로 지하철 후보가 그 역을 봤다고 할 수 없다)."""
        tg = self._ends(targets)
        if not tg:
            return []
        dist = self._dist([(nm, ls, self._walk_min(w)) for nm, ls, w in tg])
        tnames = {(nm, frozenset(ls)) for nm, ls, _w in tg}
        per_route = collections.defaultdict(dict)     # route_id → {끊는 역 물리 키: 후보}
        for d, x, r in self._boards(lat, lng, walk_lim):
            if r.route_type_nm == AIRPORT_TYPE and not is_airport_stop(x):
                continue                                  # 공항이 아닌 곳에서 타는 공항버스 = 공항행 — A(→지하철)에는 안 쓴다
            any_cut = False
            for y in self.bus.stops.get(r.route_id, []):
                if y["seq"] <= x["seq"] or _VIRTUAL in str(y.get("station_nm")) or y.get("lat") is None:
                    continue
                if r.route_type_nm == AIRPORT_TYPE and is_airport_stop(y):
                    continue                              # 공항 → 공항(돌아가는 끝) 은 끊는 지점이 아니다
                for link, rec in self._stations_by(y):
                    any_cut = True
                    if self._walkable(rec, lat, lng, excl_m) or (excl_st and self.sc.phys_key(rec) in excl_st):
                        continue
                    s_lines = self._lines(rec)
                    if not s_lines or any(rec["station_nm"] == nm and (s_lines & ls) for nm, ls in tnames):
                        continue                          # 서는 노선이 없거나 끊는 역이 도착 역(그건 버스 직행의 몫)
                    sub = self._best(dist, rec["station_nm"], s_lines)
                    if sub is None:
                        continue
                    ride = self._ride(r, x, y)
                    if ride is None:
                        continue
                    est = (self._walk_min(d) + (r.term_min or 0) / 2 + ride + self._walk_min(link) + self.wayfinding
                           + self.cg.transfer_wait + sub)
                    c = MixedCandidate("A", [{"mode": "bus", "route": r.route_nm, "from": x["station_nm"],
                                              "to": y["station_nm"]}], round(est, 1), 1, d, None, link, r.route_nm,
                                       rec["station_nm"], None, 0.0, "추정")
                    c._rank, c._sub_lines, c._tg = sub, sorted(s_lines), tg
                    c.route_id, c.board = r.route_id, x
                    pk = self.sc.phys_key(rec)
                    cur = per_route[r.route_id].get(pk)
                    if cur is None or c.est_min < cur.est_min:
                        per_route[r.route_id][pk] = c     # 같은 노선·같은 역은 추정 소요가 짧은 정류장 하나로
            if not any_cut and r.route_nm not in self.no_cut:
                self.no_cut.append(r.route_nm)
        return self._finish(per_route)

    # ── B: 지하철 → 버스 ──
    def subway_to_bus(self, sources, lat, lng, walk_lim, excl_m=None, excl_st=None):
        """sources 중 하나 → 지하철 → 끊는 역 → 버스 → 도착점(lat, lng). 구간열의 지하철 쪽은 materialize() 가 채운다.
        sources = [(역명, 노선 목록 또는 None, 출발점 → 출발 역 직선 m)] · excl_m · excl_st 는 A 와 같다(도착점 기준)."""
        sr = self._ends(sources)
        if not sr:
            return []
        dist = self._dist([(nm, ls, self._walk_min(w)) for nm, ls, w in sr])
        snames = {(nm, frozenset(ls)) for nm, ls, _w in sr}
        per_route = collections.defaultdict(dict)
        seen_air = set()
        for d, y, r in self._boards(lat, lng, walk_lim):
            if r.route_type_nm == AIRPORT_TYPE:
                if (is_airport_stop(y) and r.route_nm not in seen_air        # 도착점이 공항 = 공항행 — 37 정본 전 no_data 로 밝힌다
                        and any(s["seq"] < y["seq"] for s in self.bus.stops.get(r.route_id, []))):
                    seen_air.add(r.route_nm)
                    self.skipped_airport.append(r.route_nm)
                continue                                  # 시내에서 내리는 공항버스 구간은 B 에 안 쓴다(공항버스는 A 의 공항 → 시내만)
            any_cut = False
            for x in self.bus.stops.get(r.route_id, []):
                if x["seq"] >= y["seq"] or _VIRTUAL in str(x.get("station_nm")) or x.get("lat") is None:
                    continue
                for link, rec in self._stations_by(x):
                    any_cut = True
                    if self._walkable(rec, lat, lng, excl_m) or (excl_st and self.sc.phys_key(rec) in excl_st):
                        continue
                    s_lines = self._lines(rec)
                    if not s_lines or any(rec["station_nm"] == nm and (s_lines & ls) for nm, ls in snames):
                        continue                          # 끊는 역이 출발 역이면 지하철이 없다
                    sub = self._best(dist, rec["station_nm"], s_lines)
                    if sub is None:
                        continue
                    ride = self._ride(r, x, y)
                    if ride is None:
                        continue
                    est = (sub + self._walk_min(link) + self.wayfinding + (r.term_min or 0) / 2 + ride + self._walk_min(d))
                    c = MixedCandidate("B", [{"mode": "bus", "route": r.route_nm, "from": x["station_nm"],
                                              "to": y["station_nm"]}], round(est, 1), 1, None, d, link, r.route_nm,
                                       rec["station_nm"], None, 0.0, "추정")
                    c._rank, c._sub_lines, c._tg = sub, sorted(s_lines), sr
                    c.route_id, c.board = r.route_id, x
                    c.alight = y                          # 102 — 장소 쪽 하차 정류장 행(걷기를 이 행까지 잰다 · 이름으로 다시 찾지 않는다)
                    pk = self.sc.phys_key(rec)
                    cur = per_route[r.route_id].get(pk)
                    if cur is None or c.est_min < cur.est_min:
                        per_route[r.route_id][pk] = c
            if not any_cut and r.route_nm not in self.no_cut:
                self.no_cut.append(r.route_nm)
        return self._finish(per_route)

    def materialize(self, c):
        """후보 c 의 지하철 구간열을 채운다(최단 탐색 · 총 환승 상한 − 1). 성공하면 True — legs·transfers·est·도보·등급을 실제
        구간열 값으로 고친다. A 는 도착 역 후보 중 (지하철 + 이탈 도보)가 가장 짧은 쪽, B 는 출발 역 후보 중 (접근 도보 + 지하철)."""
        if getattr(c, "_done", False):
            return True
        cap = self.tlim - 1
        if cap < 0:
            return False
        best = None
        for nm, ls, w in c._tg:
            if c.shape == "A":
                sub = self.cg.search_solid(c.cut_station, nm, "최단", cap, origin_lines=c._sub_lines, dest_lines=sorted(ls),
                                     avoid_lines=self.avoid)
            else:
                sub = self.cg.search_solid(nm, c.cut_station, "최단", cap, origin_lines=sorted(ls), dest_lines=c._sub_lines,
                                     avoid_lines=self.avoid)
            if sub is None or not sub.legs:
                continue
            v = sub.est_min + self._walk_min(w)
            if best is None or v < best[0]:
                best = (v, sub, nm, w)
        if best is None:
            return False
        v, sub, nm, w = best
        bus = c.legs[0]
        c.legs = ([bus] + [dict(x) for x in sub.legs]) if c.shape == "A" else ([dict(x) for x in sub.legs] + [bus])
        c.est_min = round(c.est_min - c._rank + v, 1)
        c.transfers = 1 + sub.transfers
        c.end_station = nm
        if c.shape == "A":
            c.walk_out_m = w
        else:
            c.walk_in_m = w
        c.sub_walk_min, c.grade, c.fallback_edges = sub.walk_min, sub.grade, list(sub.fallback_edges)
        c._done = True
        return True

    def _finish(self, per_route):
        """노선마다 지하철 쪽 추정이 짧은 순 N개 → 전체를 추정 소요 순(같으면 노선·역 순)."""
        out = []
        for rid in sorted(per_route):
            out.extend(sorted(per_route[rid].values(), key=lambda c: (c._rank, c.cut_station))[:self.cuts])
        out.sort(key=lambda c: (c.est_min, c.route_nm, c.cut_station))
        return out


_RIDE = _weakref.WeakKeyDictionary()      # 버스 표 객체 → {route_id: (누적 분 {seq: 분}, 빈 구간 수 {seq: n})}


def ride_estimator(v, hour=12):
    """혼합 후보의 **버스 승차 분 추정**(고르는 순서용 · 판정 아님) — v = 판정기(bus · bus_prof · bus_speed · R).
    구간마다 버스 구간 프로파일(41 · 평일 hour 시 p50 · min_days 이상)이 있으면 그 값, 없으면 판정기 종전 모델(거리 ÷ 표정속도 ·
    평일)로 누적한다. 공항버스처럼 표정속도 대용(간선 통계)이 실제보다 크게 느린 노선도 프로파일이 있으면 실제에 가깝다
    (인천공항→서울역 6001: 대용 속도 272분 · 판정기 판정 약 90분). 노선마다 한 번만 만든다. 값을 못 내는 구간이 끼면 None."""
    bus, prof = v.bus, getattr(v, "bus_prof", None)
    md = None
    if prof is not None:
        try:
            md = v.rv("bus", "구간_프로파일", "min_days")
        except Exception:      # noqa: BLE001 — 규칙 칸이 없으면 프로파일을 안 쓴다(종전 모델만)
            prof = None
    # ☆(GPT 87 #3) 캐시 칸 = 버스 표 객체(약한 키) 아래 **값을 정하는 입력 전부** — 프로파일 표 · 규칙(표정속도) · 시각 · min_days.
    #   객체 id 는 그 객체를 칸 안에 같이 붙잡아 둬서(살아 있는 동안 id 가 다른 객체에 다시 쓰이지 않게) 키로 쓴다.
    #   요청마다 판정기를 얕은 복사해도 이 셋은 같은 객체라 칸이 공유되고, 설정이 다른 판정기는 다른 칸을 쓴다.
    per_bus = _RIDE.setdefault(bus, {})
    ck = (id(prof), id(v.R), hour, md)
    slot = per_bus.get(ck)
    if slot is None or slot[0] is not prof or slot[1] is not v.R:
        slot = per_bus[ck] = (prof, v.R, {})
    cache = slot[2]

    def build(r):
        sp = v.bus_speed(r, "weekday")[0]
        stops = bus.stops.get(r.route_id, [])
        cum, nil, tot, miss = {}, {}, 0.0, 0
        for k, st in enumerate(stops):
            if k:
                prev = stops[k - 1]
                m = None
                if prof is not None:
                    i, _bad = prof.row(r.route_id, prev, st)
                    if i is not None:
                        c = prof.cell(i, "weekday", hour, "p50", md)
                        m = None if c is None else c / 60.0
                if m is None:
                    dd = st.get("sect_dist_m")
                    m = None if (dd is None or not sp) else dd / 1000 / sp * 60
                if m is None:
                    miss += 1
                else:
                    tot += m
            cum[st["seq"]], nil[st["seq"]] = tot, miss
        return cum, nil

    def ride(r, x, y):
        c = cache.get(r.route_id)
        if c is None:
            c = cache[r.route_id] = build(r)
        cum, nil = c
        a, b = x["seq"], y["seq"]
        if a not in cum or b not in cum or nil[b] != nil[a]:
            return None
        return cum[b] - cum[a]
    return ride


def interleave(a_list, b_list):
    """두 모양 후보를 번갈아(각자 추정 소요 순) — 판정 개수 상한 안에서 한 모양만 보지 않게(87). A 먼저."""
    out = []
    for i in range(max(len(a_list), len(b_list))):
        if i < len(a_list):
            out.append(a_list[i])
        if i < len(b_list):
            out.append(b_list[i])
    return out


# ══ 환승 2회까지(94 · 2026-10-02 · 본인 「버스만이어도 환승은 있을 수밖에 없다」) ══════════════════════════════
# 87 의 끊는 지점 방식을 이어 **모양을 더한다**(본인 10/2 설계 답 — 통합 탐색기가 아니다):
#   BB  버스→버스            BBB 버스→버스→버스           (「버스만」 칸 · plan.Planner._bus_transfer)
#   BSB 버스→지하철→버스      SBS 지하철→버스→지하철       (「지하철+버스」 칸 · plan.Planner._mixed2)
# ★ 판정하지 않는다 — 여기 값(추정 소요)은 **판정에 넣을 순서**만 정한다. 성립·시각은 판정기(verify_case)가 낸다.
# ★ 노선끼리 「만나는 자리」 = 앞 노선 하차 정류장과 뒤 노선 승차 정류장이 같은 정류장이거나 만남 반경 안. 반경은 기존 규칙 값
#   둘을 차례로 쓴다(새 숫자 없음 · 본인 10/2): alternatives.정류장_동일_반경_m(100 · 같은 정류장·길 건너 짝) → 그 반경에서
#   후보가 하나도 없을 때만 alternatives.정류장_반경_m(500 · 「역 앞」과 같은 값). 판정기의 환승 도보 상한(limits.walk_m)은
#   판정기가 따로 본다(transfer.bus_bus_walk).
# ★ 만남 표를 파일로 두지 않는다(본인 10/2) — 정류장 행 격자(반경 크기 칸)를 버스 표 객체마다 한 번 만들어 두고(약한 키 캐시),
#   구간마다 「출발 쪽에서 한 번 타서 닿는 행」과 「도착 쪽으로 한 번 타고 오는 행」을 그 격자에서 맞춘다. 정류장 파일이 바뀌면
#   표 객체가 새로 생겨 격자도 새로 만든다(어긋날 파일이 없다).
# ★ 공항버스는 이 모양들에 쓰지 않는다(공항버스는 87 의 A 공항→시내만 — 공항행 시각 근거가 판정기에 없다 · 37).
# ★ 막는 값은 규칙 **변경안**(27 규칙 32 · 규칙 파일은 모아서 한 번에 · 규칙에 들어가면 규칙 값이 이긴다 · mix_rule):
#: 노선쌍당 끊는 지점 — 같은 두 노선이 여러 정류장에서 만나면 추정 소요가 가장 짧은 자리 하나(같은 길을 같이 달리는 구간의
#:   앞뒤 정류장은 같은 후보다). grade 추정.
XFER_CUTS_PER_PAIR_PROPOSED = 1
#: 2회 환승에서 양 끝 노선쌍당 가운데 노선(또는 가운데 구간) 수 — 추정 소요 짧은 순. grade 추정.
XFER_MID_PER_PAIR_PROPOSED = 2
#: 단계마다 판정에 넣는 후보 수(추정 소요 짧은 순 · 첫 노선·끝 노선이 겹치지 않게 먼저 고른다) — 버스_직행_최대(3)·혼합 판정
#:   상한(3)과 같은 크기. grade 추정.
XFER_MAX_PROPOSED = 3
XFER_KEYS = {"cuts": ("환승_끊는_지점_최대", XFER_CUTS_PER_PAIR_PROPOSED),
             "mid": ("환승_가운데_노선_최대", XFER_MID_PER_PAIR_PROPOSED),
             "max": ("환승_후보_최대", XFER_MAX_PROPOSED)}


def xfer_rule(rules, name):
    key, proposed = XFER_KEYS[name]
    return mix_rule(rules, key, proposed)


@dataclass
class ChainCandidate:
    """환승 후보(BB · BBB · BSB · SBS). plan.Planner._mixed_one 이 MixedCandidate 와 같은 칸으로 읽는다."""
    shape: str
    legs: list                      # 판정기 입력 — 버스 구간은 from_seq·to_seq 를 싣는다(같은 이름 정류장이 한 노선에 두 번 있다)
    est_min: float                  # 고르는 순서용 추정(판정 아님)
    transfers: int
    walk_in_m: float
    walk_out_m: float
    link_m: float                   # 환승 도보 직선 m 합(정류장↔정류장 · 정류장↔역 출구)
    route_nm: str                   # 표시용(노선 이름 이어 붙임)
    rides: list = field(default_factory=list)       # [(route_id, 타는 행)] — 운행 시간 추정(순서용)에 쓴다
    radius_m: float = 0.0           # 이 후보를 만든 만남 반경
    sub_walk_min: float = 0.0
    grade: str = "추정"
    fallback_edges: list = field(default_factory=list)
    cut_station: str = ""
    end_station: str = ""

    def key(self):
        return tuple((l.get("route") or l.get("line"), l["from"], l["to"], l.get("from_seq"), l.get("to_seq"))
                     for l in self.legs)

    def cut_key(self):
        """끊는 자리를 뺀 키 — 같은 노선열(끊는 정류장만 다른 후보)을 한 묶음으로 볼 때."""
        return tuple(l.get("route") or l.get("line") for l in self.legs)


_GRID = _weakref.WeakKeyDictionary()      # 버스 표 객체 → {칸 크기 m: (칸 → [정류장 행])}
#: 격자 칸 번호를 매길 때 쓰는 **고정** 경도 축척(서울 위도 37.5°) — 행마다 제 위도의 cos 를 쓰면 위도가 조금만 달라도 칸 번호가
#:   어긋난다(경도 127° × 축척 차). 칸은 「근처 후보를 모으는 그물」일 뿐이고 거리는 행마다 제 위도로 다시 잰다.
_GRID_COS = 0.7933533


def _row_grid(bus, cell_m):
    per = _GRID.setdefault(bus, {})
    g = per.get(cell_m)
    if g is None:
        import math
        g = collections.defaultdict(list)
        for rows in bus.stops.values():
            for s in rows:
                if s.get("lat") is None or s.get("lng") is None or _VIRTUAL in str(s.get("station_nm")):
                    continue
                g[(math.floor(s["lat"] * 111_320 / cell_m), math.floor(s["lng"] * 111_320 * _GRID_COS / cell_m))].append(s)
        per[cell_m] = g = dict(g)
    return g


def _rows_near(bus, row, within_m):
    """정류장 행 근처(직선 within_m 안)의 다른 행 [(m, 행)]. 같은 정류장(station_id)은 0 m(판정기 bus_bus_walk 와 같은 뜻)."""
    import math
    cell = max(float(within_m), 50.0)
    g = _row_grid(bus, cell)
    la, lo = row["lat"], row["lng"]
    cy = math.floor(la * 111_320 / cell)
    cx = math.floor(lo * 111_320 * _GRID_COS / cell)
    out = []
    for dy_ in (-1, 0, 1):
        for dx_ in (-2, -1, 0, 1, 2):          # 고정 축척과 실제 축척의 차(서울 안 0.5% 미만)만큼 한 칸 더 본다
            for s in g.get((cy + dy_, cx + dx_), ()):
                if s.get("station_id") and s.get("station_id") == row.get("station_id"):
                    out.append((0.0, s))
                    continue
                m = math.hypot((s["lat"] - la) * 111_320,
                               (s["lng"] - lo) * 111_320 * math.cos(math.radians((s["lat"] + la) / 2)))
                if m <= within_m:
                    out.append((m, s))
    return out


class ChainGenerator:
    """환승 2회까지의 후보 생성기 — MixedGenerator(mg)의 조각(근처 정류장 · 승차 추정 · 역 앞 정류장 · 지하철 그래프)을 쓴다.
    cuts · mid · cap = 규칙 변경안(노선쌍당 끊는 지점 · 가운데 노선 · 단계마다 내는 후보 수)."""

    def __init__(self, mg, *, cuts=XFER_CUTS_PER_PAIR_PROPOSED, mid=XFER_MID_PER_PAIR_PROPOSED, cap=XFER_MAX_PROPOSED):
        self.mg, self.bus = mg, mg.bus
        self.cuts, self.mid, self.cap = cuts, mid, cap
        self.n_generated = 0               # 마지막 호출이 상한으로 자르기 전 후보 수(부르는 쪽이 dropped 를 적는다)
        #: (GPT 94 #1) 마지막 호출이 **추정으로 접은** 후보 수 — 노선쌍당 끊는 지점(cuts)·가운데 노선(mid) 밖. 같은 후보가 아니라
        #:   추정 소요로 고른 것이라 「판정하지 않은 후보가 남았다」에 든다(부르는 쪽이 xfer_skipped 로 적는다).
        self.n_pruned = 0

    # ── 조각 ──
    def _usable(self, r):
        return r is not None and r.route_type_nm not in self.mg.excluded and r.route_type_nm != AIRPORT_TYPE

    def _wait(self, r):
        return (r.term_min or 0) / 2

    def _fwd(self, lat, lng, walk_lim):
        """출발점에서 **한 번 타서** 닿는 행 {(route_id, 내리는 seq): (추정 분, 접근 직선 m, 타는 행, 내리는 행, 노선)}."""
        mg, out = self.mg, {}
        for d, x, r in mg._boards(lat, lng, walk_lim):
            if not self._usable(r):
                continue
            base = mg._walk_min(d) + self._wait(r)
            for y in self.bus.stops.get(r.route_id, []):
                if y["seq"] <= x["seq"] or y.get("lat") is None or _VIRTUAL in str(y.get("station_nm")):
                    continue
                ride = mg._ride(r, x, y)
                if ride is None:
                    continue
                k = (r.route_id, y["seq"])
                cur = out.get(k)
                if cur is None or base + ride < cur[0]:
                    out[k] = (base + ride, d, x, y, r)
        return out

    def _bwd(self, lat, lng, walk_lim):
        """도착점으로 **한 번 타고** 오는 행 {(route_id, 타는 seq): (추정 분(대기 + 승차 + 이탈 도보), 이탈 직선 m, 타는 행, 내리는 행, 노선)}."""
        mg, out = self.mg, {}
        for d, y, r in mg._boards(lat, lng, walk_lim):
            if not self._usable(r):
                continue
            tail = mg._walk_min(d) + self._wait(r)
            for x in self.bus.stops.get(r.route_id, []):
                if x["seq"] >= y["seq"] or x.get("lat") is None or _VIRTUAL in str(x.get("station_nm")):
                    continue
                ride = mg._ride(r, x, y)
                if ride is None:
                    continue
                k = (r.route_id, x["seq"])
                cur = out.get(k)
                if cur is None or tail + ride < cur[0]:
                    out[k] = (tail + ride, d, x, y, r)
        return out

    @staticmethod
    def _keep2(table, k, item):
        """(GPT 94 #4) 가운데 노선의 같은 자리(k)에 닿는 길을 **끝 노선이 다른 것 둘까지** 남긴다(item = (추정, 만남 m, 앞·뒤 재료, 행) ·
        재료[4] = 끝 노선). 하나만 남기면, 나중에 「첫 노선 = 끝 노선」으로 그 하나가 빠질 때 다른 노선으로 닿는 길까지 같이 사라진다."""
        cur = table.setdefault(k, [])
        nm = item[2][4].route_nm
        for i, old in enumerate(cur):
            if old[2][4].route_nm == nm:
                if item[0] < old[0]:
                    cur[i] = item
                break
        else:
            cur.append(item)
        cur.sort(key=lambda t: t[0])
        del cur[2:]

    @staticmethod
    def _bus_leg(r, x, y):
        return {"mode": "bus", "route": r.route_nm, "from": x["station_nm"], "to": y["station_nm"],
                "from_seq": x["seq"], "to_seq": y["seq"]}

    @staticmethod
    def _m(a_lat, a_lng, b_lat, b_lng):
        import math
        return math.hypot((a_lat - b_lat) * 111_320,
                          (a_lng - b_lng) * 111_320 * math.cos(math.radians((a_lat + b_lat) / 2)))

    def _pick(self, cands, first, last):
        """추정 소요 순으로 cap 개 — 첫 노선·끝 노선이 겹치지 않는 것을 먼저 고르고(같은 길을 같이 달리는 노선 짝이 목록을 덮지
        않게 · 「같은 방향 중복」), 자리가 남으면 나머지를 추정 순으로 채운다."""
        cands.sort(key=lambda c: (c.est_min, c.route_nm))
        self.n_generated = len(cands)
        out, f_seen, l_seen = [], set(), set()
        for c in cands:
            if len(out) >= self.cap:
                break
            if first(c) in f_seen or last(c) in l_seen:
                continue
            out.append(c)
            f_seen.add(first(c))
            l_seen.add(last(c))
        for c in cands:
            if len(out) >= self.cap:
                break
            if c not in out:
                out.append(c)
        out.sort(key=lambda c: (c.est_min, c.route_nm))
        return out

    # ── BB · BBB ──
    def bus_chain(self, n_transfers, a_lat, a_lng, b_lat, b_lng, walk_lim, meet_m):
        """버스만 n_transfers(1 · 2)회 환승 후보. meet_m = 만남 반경(직선 m)."""
        mg = self.mg
        F = self._fwd(a_lat, a_lng, walk_lim)
        B = self._bwd(b_lat, b_lng, walk_lim)
        self.n_generated = self.n_pruned = 0
        if not F or not B:
            return []
        wf = mg.wayfinding
        # 걸어서 갈 수 있는 자리에서 갈아타는 후보는 만들지 않는다 — 출발점 근처(정류장 반경 안)에서 뒤 노선을 바로 타거나
        #   (뒤 노선 직행), 도착점 근처에서 앞 노선을 내리면(앞 노선 직행) 환승이 필요 없다. 그 정류장은 직행 후보(_bus_direct)가
        #   쓰는 것과 같은 반경 안이라 「더 일찍 떠나 걸어가서 그 노선을 타는」 직행이 같은 도착 목표를 맞춘다 — 직행이 성립하면
        #   이 단계는 아예 안 온다. ★(GPT 94 #2) 예외는 남는다: 직행은 노선마다 정거장 수가 가장 적은 짝 하나만 보므로 다른 정류장
        #   짝이어야 성립하는 경우 · 걸어가서는 그 노선 첫차 전인데 앞 버스로는 닿는 경우. 확정 탈락이 아니라 정책상 생략이다.
        near_a = lambda s: self._m(s["lat"], s["lng"], a_lat, a_lng) <= mg.radius_m
        near_b = lambda s: self._m(s["lat"], s["lng"], b_lat, b_lng) <= mg.radius_m
        if n_transfers == 1:
            best = collections.defaultdict(list)                 # (r1, r2) → [(추정, 후보 재료)]
            for (rid1, _s), (e1, d1, x1, y1, r1) in F.items():
                if near_b(y1):
                    continue
                for m, x2 in _rows_near(self.bus, y1, meet_m):
                    rid2 = x2["route_id"]
                    got = B.get((rid2, x2["seq"]))
                    if got is None or rid2 == rid1 or near_a(x2):
                        continue
                    e2, d2, _x2, y2, r2 = got
                    if r2.route_nm == r1.route_nm:
                        continue                                  # 같은 노선 되돌아가기
                    est = e1 + mg._walk_min(m) + wf + e2
                    best[(rid1, rid2)].append((est, m, d1, d2, x1, y1, r1, x2, y2, r2))
            cands = []
            for lst in best.values():
                lst.sort(key=lambda t: (t[0], t[4]["seq"], t[7]["seq"]))
                self.n_pruned += max(0, len(lst) - self.cuts)
                for est, m, d1, d2, x1, y1, r1, x2, y2, r2 in lst[:self.cuts]:
                    cands.append(ChainCandidate(
                        "BB", [self._bus_leg(r1, x1, y1), self._bus_leg(r2, x2, y2)], round(est, 1), 1, d1, d2, m,
                        f"{r1.route_nm}→{r2.route_nm}", rides=[(r1.route_id, x1), (r2.route_id, x2)], radius_m=meet_m))
            return self._pick(cands, lambda c: c.legs[0]["route"], lambda c: c.legs[-1]["route"])
        # 2회 — 가운데 노선 rm: 앞 노선 하차 자리 근처에서 타고(p) 뒤 노선 승차 자리 근처에서 내린다(q)
        P, Q = {}, {}                                            # (rm, seq) → (추정, 만남 m, 앞·뒤 재료)
        for (rid1, _s), f in F.items():
            y1 = f[3]
            if near_b(y1):
                continue
            for m, p in _rows_near(self.bus, y1, meet_m):
                rm = self.bus.by_id.get(p["route_id"])
                if p["route_id"] == rid1 or not self._usable(rm) or rm.route_nm == f[4].route_nm or near_a(p):
                    continue
                arr = f[0] + mg._walk_min(m) + wf + self._wait(rm)
                self._keep2(P, (p["route_id"], p["seq"]), (arr, m, f, p))
        for (rid2, _s), b in B.items():
            x2 = b[2]
            if near_a(x2):
                continue
            for m, q in _rows_near(self.bus, x2, meet_m):
                rm = self.bus.by_id.get(q["route_id"])
                if q["route_id"] == rid2 or not self._usable(rm) or rm.route_nm == b[4].route_nm or near_b(q):
                    continue
                frm = mg._walk_min(m) + wf + b[0]
                self._keep2(Q, (q["route_id"], q["seq"]), (frm, m, b, q))
        by_p, by_q = collections.defaultdict(list), collections.defaultdict(list)
        for (rid, _s), vs in P.items():
            by_p[rid].extend(vs)
        for (rid, _s), vs in Q.items():
            by_q[rid].extend(vs)
        best = collections.defaultdict(dict)                     # (r1, r2) → {rm: (추정, 재료)}
        for rid, ps in by_p.items():
            qs = by_q.get(rid)
            if not qs:
                continue
            rm = self.bus.by_id[rid]
            for arr, m1, f, p in ps:
                for frm, m2, b, q in qs:
                    if p["seq"] >= q["seq"] or f[4].route_id == b[4].route_id or f[4].route_nm == b[4].route_nm:
                        continue
                    ride = mg._ride(rm, p, q)
                    if ride is None:
                        continue
                    est = arr + ride + frm
                    slot = best[(f[4].route_id, b[4].route_id)]
                    if rid not in slot or est < slot[rid][0]:
                        slot[rid] = (est, m1, m2, f, p, q, b, rm)
        cands = []
        for slot in best.values():
            self.n_pruned += max(0, len(slot) - self.mid)
            for est, m1, m2, f, p, q, b, rm in sorted(slot.values(), key=lambda t: (t[0], t[7].route_nm))[:self.mid]:
                _e1, d1, x1, y1, r1 = f
                _e2, d2, x2, y2, r2 = b
                cands.append(ChainCandidate(
                    "BBB", [self._bus_leg(r1, x1, y1), self._bus_leg(rm, p, q), self._bus_leg(r2, x2, y2)],
                    round(est, 1), 2, d1, d2, m1 + m2, f"{r1.route_nm}→{rm.route_nm}→{r2.route_nm}",
                    rides=[(r1.route_id, x1), (rm.route_id, p), (r2.route_id, x2)], radius_m=meet_m))
        return self._pick(cands, lambda c: c.legs[0]["route"], lambda c: c.legs[-1]["route"])

    # ── 지하철 한 노선 안 거리(환승 없이) ──
    def _line_dist(self, seeds):
        """seeds = [(역명, 노선 집합, 시작 비용 분, 표식)] → ({(노선, 역): 분}, {(노선, 역): 표식}) · self._alt = 노드마다 표식이
        다른 것 둘까지(가장 짧은 표식이 부르는 쪽 조건으로 빠질 때 쓸 다음 것). **갈아타지 않고** 그 노선으로만
        닿는 역 — 2회 환승 모양은 버스↔지하철 환승이 이미 둘이라 지하철 안 환승 자리가 (환승 상한 − 2)뿐이다(기본 상한 2 → 0).
        실제 구간열과 환승 상한은 materialize 가 다시 본다."""
        cg, dist, src, pq, tick = self.mg.cg, {}, {}, [], 0
        alt = {}                           # (GPT 94 #3) 노드 → [(분, 표식)] 표식이 다른 것 둘까지(가장 짧은 것 + 다른 출발의 가장 짧은 것)
        for nm, lines, c0, tag in seeds:
            for ln in lines:
                heapq.heappush(pq, (c0, tick, (ln, nm), tag))
                tick += 1
        while pq:
            d, _, node, tag = heapq.heappop(pq)
            got = alt.setdefault(node, [])
            if len(got) >= 2 or any(t == tag for _d, t in got):
                continue
            got.append((d, tag))
            if node not in dist:
                dist[node], src[node] = d, tag
            for v, w, _fb in cg.adj[node]:
                if len(alt.get(v, ())) < 2:
                    tick += 1
                    heapq.heappush(pq, (d + w, tick, v, tag))
        self._alt = alt
        return dist, src

    def _sub(self, a_nm, a_lines, b_nm, b_lines):
        cap = self.mg.tlim - 2
        if cap < 0:
            return None
        s = self.mg.cg.search_solid(a_nm, b_nm, "최단", cap, origin_lines=sorted(a_lines), dest_lines=sorted(b_lines),
                              avoid_lines=self.mg.avoid)
        return s if s is not None and s.legs else None

    # ── BSB: 버스 → 지하철 → 버스 ──
    def bus_subway_bus(self, a_lat, a_lng, b_lat, b_lng, walk_lim, excl_a=None, excl_b=None):
        """출발점 → 버스 → 역 앞에서 내려 → 지하철(갈아타지 않고) → 역 앞 정류장 → 버스 → 도착점.
        excl_a · excl_b = 끊지 않을 물리적 역(출발·도착 장소에서 지하철 후보가 이미 본 역 — 그 역은 걸어가면 된다 · 87 과 같은 규칙)."""
        mg = self.mg
        self.n_generated = self.n_pruned = 0
        if mg.sc is None or mg.tlim < 2:
            return []
        F = self._fwd(a_lat, a_lng, walk_lim)
        B = self._bwd(b_lat, b_lng, walk_lim)
        heads, tails = {}, {}                                    # 물리적 역 → (추정, 역 레코드, 노선, 버스 재료, 정류장↔역 m)
        for f in F.values():
            for link, rec in mg._stations_by(f[3]):
                pk = mg.sc.phys_key(rec)
                ls = mg._lines(rec)
                if not ls or (excl_a and pk in excl_a) or (excl_b and pk in excl_b):
                    continue
                arr = f[0] + mg._walk_min(link) + mg.wayfinding + mg.cg.transfer_wait
                if pk not in heads or arr < heads[pk][0]:
                    heads[pk] = (arr, rec, ls, f, link)
        for b in B.values():
            for link, rec in mg._stations_by(b[2]):
                pk = mg.sc.phys_key(rec)
                ls = mg._lines(rec)
                if not ls or (excl_a and pk in excl_a) or (excl_b and pk in excl_b):
                    continue
                frm = mg._walk_min(link) + mg.wayfinding + b[0]
                if pk not in tails or frm < tails[pk][0]:
                    tails[pk] = (frm, rec, ls, b, link)
        if not heads or not tails:
            return []
        self._line_dist([(rec["station_nm"], ls, arr, pk) for pk, (arr, rec, ls, _f, _l) in heads.items()])
        alt = self._alt
        self.n_pruned = 0
        best = collections.defaultdict(list)
        for pk2, (frm, rec2, ls2, b, link2) in tails.items():
            for ln in ls2:
                # (GPT 94 #3) 가장 짧게 닿는 출발 역이 이 역 자신이거나(지하철 구간 없음) 같은 버스 노선이면 그다음 출발 역을 본다
                for d_, pk1 in alt.get((ln, rec2["station_nm"]), ()):
                    if pk1 == pk2:
                        continue
                    _arr, rec1, ls1, f, link1 = heads[pk1]
                    if f[4].route_nm == b[4].route_nm:
                        continue
                    best[(f[4].route_id, b[4].route_id)].append((d_ + frm, ln, rec1, ls1, f, link1, rec2, ls2, b, link2))
                    break
        cands = []
        for lst in best.values():
            lst.sort(key=lambda t: (t[0], t[2]["station_nm"], t[6]["station_nm"]))
            self.n_pruned += max(0, len(lst) - self.mid)
            for est, ln, rec1, ls1, f, link1, rec2, ls2, b, link2 in lst[:self.mid]:
                _e1, d1, x1, y1, r1 = f
                _e2, d2, x2, y2, r2 = b
                c = ChainCandidate("BSB", [self._bus_leg(r1, x1, y1), self._bus_leg(r2, x2, y2)], round(est, 1), 2, d1, d2,
                                   link1 + link2, f"{r1.route_nm}→{ln}→{r2.route_nm}",
                                   rides=[(r1.route_id, x1), (r2.route_id, x2)],
                                   cut_station=rec1["station_nm"], end_station=rec2["station_nm"])
                c._sub = (rec1["station_nm"], ls1, rec2["station_nm"], ls2)
                cands.append(c)
        return self._pick(cands, lambda c: c.legs[0]["route"], lambda c: c.legs[-1]["route"])

    # ── SBS: 지하철 → 버스 → 지하철 ──
    def subway_bus_subway(self, sources, targets, excl_a=None, excl_b=None):
        """sources 중 한 역 → 지하철(갈아타지 않고) → 역 앞 정류장 → 버스 → 역 앞에서 내려 → 지하철(갈아타지 않고) → targets 중 한 역.
        sources · targets = [(역명, 노선 목록 또는 None, 장소↔역 직선 m)] — 부르는 쪽이 도보 상한 안 역을 준다.
        excl_a · excl_b = 버스로 잇지 않을 물리적 역(출발·도착 장소의 역 — 그 역끼리는 지하철 후보의 몫)."""
        mg = self.mg
        self.n_generated = self.n_pruned = 0
        if mg.sc is None or mg.tlim < 2:
            return []
        sr, tg = mg._ends(sources), mg._ends(targets)
        if not sr or not tg:
            return []
        # (GPT 94 #5) 출발 쪽·도착 쪽을 **따로** 든다 — 표식은 목록 순번(같은 역명이 양쪽에 있거나 동명이역이어도 접근·이탈 거리와
        #   노선이 섞이지 않는다). 역은 역명이 아니라 물리적 역 키로 묶는다.
        dF, sF = self._line_dist([(nm, ls, mg._walk_min(w), i) for i, (nm, ls, w) in enumerate(sr)])
        dB, sB = self._line_dist([(nm, ls, mg._walk_min(w), i) for i, (nm, ls, w) in enumerate(tg)])

        def pk_of(nm, ls):
            rec = mg.sc.resolve(nm, sorted(ls))
            return mg.sc.phys_key(rec) if rec is not None else nm
        ends = {pk_of(nm, ls) for nm, ls, _w in sr} | {pk_of(nm, ls) for nm, ls, _w in tg}
        reach = None
        self.n_pruned = 0

        def stops_by(dist, src_of):
            """한 노선으로 닿는 역마다 역 앞 정류장 행 {(route_id, seq): (분, 역 레코드, 노선, 끝 역, 정류장↔역 m, 행)}."""
            nonlocal reach
            if reach is None:
                reach = exit_reach_m(mg.sc, mg.ex)
            per_st = {}
            for (ln, nm), d in dist.items():
                if (ln, nm) in mg.skip_at:
                    continue
                rec = mg.sc.resolve(nm, [ln])
                if rec is None or rec.get("lat") is None:
                    continue
                pk = mg.sc.phys_key(rec)
                if pk in ends or (excl_a and pk in excl_a) or (excl_b and pk in excl_b):
                    continue
                cur = per_st.get(pk)
                if cur is None or d < cur[0]:
                    per_st[pk] = (d, ln, src_of[(ln, nm)], rec)
            out = {}
            for _pk, (d, ln, end_nm, rec) in per_st.items():
                for _d0, row in _stop_index(self.bus).near(rec["lat"], rec["lng"], mg.near_m + reach + 1):
                    r = self.bus.by_id.get(row["route_id"])
                    if not self._usable(r) or _VIRTUAL in str(row.get("station_nm")):
                        continue
                    link = mg._link(row, rec)
                    if link > mg.near_m:
                        continue
                    v = d + mg._walk_min(link) + mg.wayfinding
                    k = (row["route_id"], row["seq"])
                    if k not in out or v < out[k][0]:
                        out[k] = (v, rec, ln, end_nm, link, row)
            return out
        P, Q = stops_by(dF, sF), stops_by(dB, sB)
        by_p, by_q = collections.defaultdict(list), collections.defaultdict(list)
        for (rid, _s), v in P.items():
            by_p[rid].append(v)
        for (rid, _s), v in Q.items():
            by_q[rid].append(v)
        best = {}
        for rid, ps in by_p.items():
            qs = by_q.get(rid)
            if not qs:
                continue
            r = self.bus.by_id[rid]
            for a, rec1, ln1, src1, link1, p in ps:
                for b, rec2, ln2, dst2, link2, q in qs:
                    if p["seq"] >= q["seq"] or mg.sc.phys_key(rec1) == mg.sc.phys_key(rec2):
                        continue
                    ride = mg._ride(r, p, q)
                    if ride is None:
                        continue
                    est = a + self._wait(r) + ride + b + mg.cg.transfer_wait
                    k = (rid, mg.sc.phys_key(rec1), mg.sc.phys_key(rec2))
                    if k not in best or est < best[k][0]:
                        best[k] = (est, r, p, q, rec1, ln1, src1, link1, rec2, ln2, dst2, link2)
        per_route = collections.defaultdict(list)
        for v in best.values():
            per_route[v[1].route_id].append(v)
        cands = []
        for lst in per_route.values():
            lst.sort(key=lambda t: (t[0], t[4]["station_nm"], t[8]["station_nm"]))
            self.n_pruned += max(0, len(lst) - self.mid)
            for est, r, p, q, rec1, ln1, i1, link1, rec2, ln2, i2, link2 in lst[:self.mid]:
                (src1, _l1, w_in), (dst2, _l2, w_out) = sr[i1], tg[i2]
                c = ChainCandidate("SBS", [self._bus_leg(r, p, q)], round(est, 1), 2, w_in, w_out, link1 + link2,
                                   f"{ln1}→{r.route_nm}→{ln2}", rides=[(r.route_id, p)],
                                   cut_station=rec1["station_nm"], end_station=rec2["station_nm"])
                c._sub = (src1, [ln1], rec1["station_nm"], [ln1], rec2["station_nm"], [ln2], dst2, [ln2])
                cands.append(c)
        return self._pick(cands, lambda c: (c._sub[0], c.legs[0]["route"]), lambda c: (c._sub[6], c.legs[0]["route"]))

    def materialize(self, c):
        """BSB · SBS 의 지하철 구간열을 채운다(지하철 안 환승 상한 = 총 상한 − 2). 성공하면 True. BB · BBB 는 늘 True."""
        if getattr(c, "_done", False) or c.shape in ("BB", "BBB"):
            return True
        if c.shape == "BSB":
            a, la, b, lb = c._sub
            s = self._sub(a, la, b, lb)
            if s is None:
                return False
            c.legs = [c.legs[0]] + [dict(x) for x in s.legs] + [c.legs[-1]]
            c.transfers = 2 + s.transfers
            c.sub_walk_min, c.grade, c.fallback_edges = s.walk_min, s.grade, list(s.fallback_edges)
        else:
            a, la, b, lb, c2, lc, d, ld = c._sub
            s1, s2 = self._sub(a, la, b, lb), self._sub(c2, lc, d, ld)
            if s1 is None or s2 is None or s1.transfers + s2.transfers > self.mg.tlim - 2:
                return False
            c.legs = [dict(x) for x in s1.legs] + [c.legs[0]] + [dict(x) for x in s2.legs]
            c.transfers = 2 + s1.transfers + s2.transfers
            c.sub_walk_min = (s1.walk_min or 0) + (s2.walk_min or 0)
            c.grade = "근거없음" if "근거없음" in (s1.grade, s2.grade) else "추정"
            c.fallback_edges = list(s1.fallback_edges) + list(s2.fallback_edges)
        c._done = True
        return True
