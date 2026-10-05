# app/modules/travel_ops/mobility/engine/verify_time.py — 시각 검증기 v2 (지하철 분기)
#
# "그 구간 이동이 그 시각에 성립하는가" 를 코드가 판정한다. 이 모듈의 본체다.
# 실행: 저장소 루트에서
#   $env:PYTHONPATH="final_project_cs"    (한 셸에 한 번)
#   python -m app.modules.travel_ops.mobility.engine.verify_time --cases final_project_cs/tests/unit/travel/mobility/synthetic_legs_v1.json --check-expect
#   python -m app.modules.travel_ops.mobility.engine.verify_time --cases final_project_cs/tests/unit/travel/mobility/synthetic_legs_v1.json --case LT-03 --verbose
#   python -m app.modules.travel_ops.mobility.engine.verify_time --cases ... --timetable <경로> --json out.json
#
# v1 과 달라진 것 (2026-09-10)
#   ★ 시각을 **분 단위 정수**로 다룬다. strptime 을 쓰지 않는다 — 시간표 dep_time 에
#     '24:50:00'·'27:59:00' 이 들어온다. modules/mobility/timeutil.py 가 유일한 자다.
#   ★ 막차 후보를 셋으로 거른다 — dest_nm 없음(근거없음) · 종착 열차(그 역이 행선지) ·
#     단축운행(목적지 전에 내려줌). C4/C5 가 소스 간 대조로 잡아낸 것들이고
#     **하나도 축소 시간표의 가짜 시각으로는 안 나온다.**
#   ★ 방향은 dir(U/D) 이 아니라 dest_nm 으로 정한다. 인천2호선·신림선은 같은 dir 에
#     양 끝 행선지가 섞여 있다.
#   ★ 2호선 신정지선 토요일은 등급을 추정으로 내린다(rules last_train.신정지선_토요일_예외).
#
# 규칙 v0.4 (2026-09-19 · 19번 방 · 지하철↔버스 혼합 환승)
#   ★ 지하철↔버스 환승 도보를 정류장 좌표 ↔ 그 역 가장 가까운 출구(OSM, 추정) 직선 × 우회계수로 잰다.
#     직선이 limits.walk_m 를 넘으면 **불가**, 상한 ±20 m 면 근거없음(unknown). _stop_station_walk.
#   ★ 버스 구간이 불가이면 노선교체(같은 두 정류장의 다른 노선)·수단교체(정류장 근처 역끼리 지하철)를 연다.
#   ★ 시간표 부분 적재에 대안 후보 노선·정류장 근처 역을 포함한다 — 종전에는 ⓑ 노선교체가 회귀에서 죽어 있었다.
#
# 규칙 v0.5 (2026-09-20 · 20번 방 · 다목적 후보 + 동급 판정)
#   ★ 케이스가 `legs` 대신 `multi: {from, to}` 를 주면 역 순서 777간선 위에서 **기준별 대표안**
#     (최단·최소환승·최소도보 — modules/mobility/candidates.py)과 역 앞 정류장 버스 직행 후보를 만들고,
#     후보마다 **같은 판정기**(verify_case)로 시각을 확정한다. 순위 없음. verify_multi.
#   ★ tie_band — 두 후보의 도착 차이가 대기 불확실성(버스 = 대기 추정치 · 지하철 = 0) 안이면 「동급」.
#     이유는 시간 밖 축(환승·도보·등급)으로만 말한다. rules tie_band.
#   ★ `legs` 케이스의 판정 경로는 손대지 않았다 — 회귀 91건은 그대로다.
#
# 규칙 v0.6 (2026-09-20 · 21번 방 · 자동차·택시 판정)
#   ★ 택시 대안이 「소요 판단 불가」에서 **소요·요금(추정)** 으로 바뀐다 — 차도 그래프 경로(☆99 부터 저장소 안 파일 ·
#     파이썬 도로 라우터 하나) 위에서 TOPIS 프로파일로 edge 마다 소요를 재계산하고(modules/mobility/car.py) 15번 산식으로 요금을 낸다.
#     라우터에 닿지 못하면 종전대로 근거없음 + MOB_W_CAR_ROUTER_DOWN. 지어내지 않는다.
#   ★ legs 에 mode=car / mode=taxi 구간이 생겼다(verify_leg_car). from/to 는 역명 또는 'lat,lng'.
#   ★ 경로는 저장하지 않는다 — LegResult.car / CaseResult.taxi.car 에는 거리·소요·커버·링크 요약만 남는다.
#   ★ ☆99(2026-10-04) 경로 서버 호출을 지웠다. ☆101(2026-10-05 · 합치기) 길찾기는 팀장 graph_router 하나 — 명령줄은
#     `--road-graph`(auto · none · <폴더> · 'fixture:<합성경로 파일>') 로 정한다(verify_time_cli.py). 경로 서버 주소·「라우터 없음
#     SKIP」 인자는 없다. 규칙 파일의 car.graphhopper.* 칸은 읽지 않는다(규칙 파일은 따로 모아 고친다).
#
# 규칙 v0.7 (2026-09-20 · 22번 방 · 자전거(따릉이) 판정 · rules bike.ddareungi)
#   ★ legs 에 mode=bike. from/to 는 역명(station_coords) 또는 {lat,lng,name}. verify_leg_bike.
#     출발·도착 반경 station_walk_m 안 운영 대여소 ≥1 → 후보(없으면 **불가**) · 출발 대여소 실시간 거치(bikeList 단건, 값만 쓰고 버림)
#     · 만 12세 이하 제외 · LCD 전용 출발 대여소 제외 · 요금·초과 경고(> 50분) · 소요 = 도보 + 대여 3 + bike 프로파일 + 반납 3.
#   ★ 시각을 확정하지 않는다 — 소요만. 대안 열거 수단교체 축과 multi 후보(「자전거」)에 들어간다. tie_band 는 여전히 지하철×버스.
#   ★ 길찾기·실시간 조회가 없으면 죽지 않고 **근거없음**으로 낸다 — 판정은 성립, 소요·가용은 미상.
#   ★ ☆99(2026-10-04) 승차 소요를 근거없음으로 내렸다가 ☆101(2026-10-05 · 본인 결정)에서 되살렸다 — 길찾기(로컬 도로 그래프
#     bike 프로파일 · 팀장 판)가 있으면 승차 소요·예정 도착을 낸다(추정). 없으면 「자전거 경로 계산 없음」(밖 판정 = 불가
#     no_data). 대여소 없음·연령·LCD·거치 부족의 「불가」는 그대로 확정으로 낸다. 도착을 못 내는 자전거는 대안 목록에 넣지 않는다(99 GPT 1).
#
# 규칙 v0.8 (2026-09-24 · 39번 방 · @ 판정 계약 — 최악값·최선값 이중 계산)
#   ★ 구간 열을 **두 번** 통과한다 — best(종전 계산 그대로 · 예정 시각) 와 worst(버스 대기 = 배차 전부 ·
#     혼잡 매우혼잡+조건이면 배차 1회 더 · 환승은 최악 도착에서 다음 편). **판정은 worst, 표시는 best + @.**
#     @(margin_min) = (최악 도착 − 예정 도착) + 정책 버퍼. slack_min = 도착 목표 − 예정 도착 − @ (음수 = 늦음).
#   ★ 밖으로 나가는 판은 CaseResult.out 하나 — verdict 는 **feasible / infeasible 둘**. 근거없음(unknown)은
#     불가 + code `no_data`, 동행 상한 탈락(rejected_by_limit)은 불가 + `over_limit`. 등급은 밖으로 안 나간다.
#     내부 verdict(넷)·grade 는 그대로 둔다 — 접기(fold)·자기점검·로그(40)가 읽는다.
#   ★ 불가에는 이유 코드(rules judgment.reason_codes)·대안·**마지막 성립 출발 시각**(last_feasible_depart_min —
#     같은 worst 판정기로 출발 시각을 뒤에서부터 역산)이 붙는다. 성립에도 같은 값을 계산해 둔다(안에서만 쓴다 · v1.1).
#   ★ eta_min(예정 소요 · 중앙값 · 버퍼 안 섞음)·buffer_min·p90_eta_min(스프레드 소스가 있을 때만)을 따로 낸다 —
#     팀 density.py 가 자기 버퍼를 더하므로 소요에 버퍼를 섞으면 이중 계산이다(◆밀도-2).
#   ★ 버스 승차 소요·자동차 소요의 스프레드(p90)는 아직 근거가 없다(41 대기) — worst = best 로 두고
#     MOB_W_WORST_NO_SPREAD 로 드러낸다. 값을 지어내지 않는다.
#
# 판정 값 넷 (내부)
#   feasible          성립
#   infeasible        불가 (+완화 조건)
#   rejected_by_limit 성립하지만 동행 상한 초과로 탈락
#   unknown           근거 없음
#   ※ 탈락과 불가를 구분하는 게 핵심이다. 탈락은 "되지만 이 일행에게 무리", 불가는 "안 된다".
import bisect, json, math, difflib, collections, gzip
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from pathlib import Path

# ★ 31번 방(2026-09-21) — 저장소 루트를 sys.path 에 끼우지 않는다.
#   이 파일은 이제 패키지 모듈이다. CLI 는 python -m 으로 부른다.
PKG = Path(__file__).resolve().parent
RULES_DIR = PKG / "rules"

# ☆101 — 아래에서 이 파일이 직접 쓰지 않는 이름(F401 표시)은 **다시 내보내는 것**이다: runtime.py(`vt.LineOrder` …)와
#   명령줄을 떼어 낸 verify_time_cli.py(#58)가 이 모듈에서 가져다 쓴다. 팀 린트(F)가 「안 쓰는 import」로 막지 않게 표시한다.
from .paths import REPO_ROOT                                            # noqa: E402, F401
from .line_order import LineOrder                                       # noqa: E402, F401
from .transfer_walk import TransferWalk, ceil1                          # noqa: E402, F401
from .bus import BusRoutes                                              # noqa: E402, F401
from .bus_profile import BusSegProfile, worst_not_before_best, board_caps, last_pass_early  # noqa: E402, F401
from .geo import StationCoords, meters                                  # noqa: E402, F401
from .exits import StationExits                                         # noqa: E402, F401
from .candidates import (CandidateGraph, MixedGenerator, interleave, mix_rule, ride_estimator,  # noqa: E402
                         MIX_MAX_PROPOSED, MIX_CUTS_PER_ROUTE_PROPOSED)
from .car import CarGraph, CarService, RouterDown, make_router          # noqa: E402, F401
from .congestion import Congestion                                       # noqa: E402, F401
from .bike import (BikeStations, BikeLive, BikeRouter,                  # noqa: E402, F401
                   party_excluded as bike_party_excluded, fare as bike_fare)
from .timeutil import (to_min, to_service_min, fmt_min,                 # noqa: E402, F401
                       fmt_wall, day_type_of, MIN_DAY, HolidayCalendar)

from .errors import CaseInputError                                       # noqa: E402  #30·#66 — SystemExit 대신

VERDICTS = ("feasible", "infeasible", "rejected_by_limit", "unknown")
#: 99(2026-10-04) — 자전거 승차 소요를 못 내는 이유 문구. 판정 이유·근거·계획의 뺀 후보(plan left_out)가 같은 말을 쓴다.
BIKE_NO_ROUTE = "자전거 경로 계산 없음"
OUT_VERDICTS = ("feasible", "infeasible")          # 밖으로 나가는 판정 둘 (v0.8)
# 내부 판정 → 밖 판정 · 이유 코드(내부 코드가 없을 때의 기본값). rules judgment.reason_codes 가 어휘의 정본이다.
OUT_OF = {"feasible": ("feasible", None), "infeasible": ("infeasible", None),
          "rejected_by_limit": ("infeasible", "over_limit"), "unknown": ("infeasible", "no_data")}
GRADE_ORDER = {"확정": 2, "추정": 1, "근거없음": 0}


def worst_grade(*grades):
    gs = [g for g in grades if g]
    return min(gs, key=lambda g: GRADE_ORDER[g.split(":")[0]]) if gs else "근거없음"


# ── 시간표 ────────────────────────────────────────────────────────────────
@dataclass
class Dep:
    min: int          # 출발 시각(분, 24 시 이상 가능)
    dir: str          # 참고용. 방향의 정본이 아니다
    dest: str         # 행선지 — 방향의 정본
    inferred: str = None   # 행선지가 원천 값이 아니라 채운 값이면 그 방법(28 · `dest_inferred` · chain_v1). 판정 등급을 추정으로 내린다


class Timetable:
    """processed/mobility/timetable_v1.jsonl 을 판정에 필요한 만큼만 올린다.

    46만 행을 통째로 dict 로 만들면 메모리가 아깝다. 케이스에 나오는 (노선, 역) 만
    골라 담는다. DB 전환(03번 방) 뒤에는 이 클래스가 조회로 바뀐다.
    """

    def __init__(self):
        self.by_key = collections.defaultdict(list)   # (line, station, day_type) → [Dep]
        self.stations = set()                         # (line, station) — 시간표에 존재하는가
        self.rows = 0
        self.skipped_no_dep = 0
        self.fetched_at = None

    @classmethod
    def load(cls, path, wanted=None):
        tt = cls()
        # ☆`[2026-09-29 문제목록 #63]` .gz 도 읽는다 — 시험용 축소 시간표(20MB)를 압축해 두었다(98% 줄어든다)
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                r = json.loads(raw)
                line, nm = r.get("line"), r.get("station_nm")
                if wanted is not None and (line, nm) not in wanted:
                    continue
                tt.stations.add((line, nm))
                if tt.fetched_at is None:
                    tt.fetched_at = r.get("fetched_at")
                m = to_min(r.get("dep_time"))
                if m is None:                     # 시·종착역은 출발이 없다
                    tt.skipped_no_dep += 1
                    continue
                tt.by_key[(line, nm, r.get("day_type"))].append(
                    Dep(m, r.get("dir"), r.get("dest_nm"), r.get("dest_inferred")))
                tt.rows += 1
        for v in tt.by_key.values():
            v.sort(key=lambda d: d.min)
        return tt

    def departures(self, line, station, day_type):
        return self.by_key.get((line, station, day_type), [])

    def has_station(self, line, station):
        return (line, station) in self.stations

    def similar(self, line, station, n=5):
        pool = [s for (l, s) in self.stations if l == line]
        return difflib.get_close_matches(station, pool, n=n, cutoff=0.5)


# ── 판정 결과 ─────────────────────────────────────────────────────────────
def dedup_warn(ws):
    """경고 중복 제거. ★ dict 는 해시가 안 되므로 dict.fromkeys 를 쓰면 터진다 — code 기준으로 거른다."""
    out, seen = [], set()
    for w in ws or []:
        k = w["code"] if isinstance(w, dict) else w
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    return out


@dataclass
class LegResult:
    idx: int
    label: str
    verdict: str
    reason: str
    grade: str = "확정"
    depart_min: int = None
    arrive_min: int = None
    wait_min: int = None
    ride_min: float = None
    ride_grade: str = "확정"
    relief: str = None                       # 완화 조건
    dropped: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    car: dict = None                         # 자동차·택시 구간 요약(v0.6 · 경로 없음) — 접기가 kind=car_leg 로 올린다
    walk_min: int = None                     # 구간 안 도보(자전거 — 대여소까지·대여소에서, v0.7). 지하철·버스는 None
    code: str = None                         # 이유 코드(v0.8 · rules judgment.reason_codes). 성립이면 None
    worst: bool = False                      # 최악값 통과에서 만든 구간인가(v0.8)
    ride_src: str = None                     # 버스 승차 소요의 출처(v0.9) — profile · profile_partial(대체 구간 섞임) · speed(종전 모델)


def leg_txt(l):
    """구간 한 줄 표기. from/to 가 dict(좌표)인 자전거 구간도 이름으로 찍는다."""
    nm = lambda x: x.get("name") or f"{x.get('lat')},{x.get('lng')}" if isinstance(x, dict) else x
    if l.get("mode") == "bus":
        return f"버스 {l['route']} {nm(l['from'])}→{nm(l['to'])}"
    if l.get("mode") == "bike":
        return f"자전거 {nm(l['from'])}→{nm(l['to'])}"
    return f"{l.get('line')} {nm(l['from'])}→{nm(l['to'])}"


def leg_mode(l):
    return l.get("mode", "subway")


@dataclass
class CaseResult:
    id: str
    verdict: str
    reason: str
    grade: str
    legs: list = field(default_factory=list)
    arrive_min: int = None
    slack_min: float = None
    relief: str = None
    warnings: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    day_type: str = None
    alternatives: list = field(default_factory=list)
    alt_tried: list = field(default_factory=list)
    taxi: dict = None
    # 다목적 후보(v0.5 · multi 케이스에서만 채워진다). 순위 없음 — 목록 순서는 규칙 candidates.기준 의 순서다.
    candidates: list = None
    ties: list = None                        # 동급 쌍 [{a, b, delta_min, band_min, axes}]
    dropped_candidates: list = None          # 허용_소요_배수·버스 직행 상한으로 뺀 후보
    bus_rejected: list = None                # 불가·근거없음으로 접은 버스 직행 후보 [{label, verdict, reason}]
    axis_best: dict = None                   # 판정 뒤 축별 최소 후보 {도착: [n], 환승: [n], 도보: [n]} — 순위 아님
    # ── v0.8 (39번 방) — 최악값·최선값 이중 계산. verdict 는 **worst 기준**, arrive_min 은 **best(예정)** 이다.
    code: str = None                         # 이유 코드(불가일 때). rules judgment.reason_codes
    verdict_best: str = None                 # best 통과만의 판정(내부 · worst 와 갈리면 「막차 근처」다)
    legs_worst: list = None                  # worst 통과의 구간 결과(내부)
    arrive_worst_min: int = None             # 최악 도착(버퍼 제외)
    buffer_min: int = None                   # 정책 버퍼(rules buffer.by_stage)
    margin_min: int = None                   # @ = (최악 도착 − 예정 도착) + 버퍼
    eta_min: int = None                      # 예정 소요 = 예정 도착 − 출발(버퍼 안 섞음 · 밀도용)
    eta_worst_min: int = None                # 최악 소요 = 최악 도착 − 출발(버퍼 제외)
    p90_eta_min: int = None                  # 스프레드 소스(41 버스 · 자동차)가 있을 때만. 지금은 None
    last_feasible_depart_min: int = None     # 마지막 성립 출발(worst 기준 역산). 내부값 — 밖으로 안 낸다(v1.1)
    out: dict = None                         # 밖으로 나가는 판 — _out() 이 만든다


# ── 92: 그 편이 도착역에 서는가 — 두 역 시간표의 편 잇기 ─────────────────────
# ☆`[2026-10-02 92번 방]` 시간표에는 열차 번호가 없다(TAGO 원천). 「출발역 a 를 t 분에 떠난 D 행 편이 도착역 b 에 서는가」는
#   b 의 같은 행선지 출발 행 가운데 그 편의 것이 있는지로만 알 수 있다. 계획 시간표라 같은 편성 종류끼리는 두 역 사이 시차가
#   분 단위로 같다(01호선 청량리→부평 인천행 99편 중 97편이 65분 · 용산→부평 동인천행 급행 76편 전부 34분 · 10/1 판).
#   ① 완행 시차 = 역 순서 표 소요(est · 인접 역 출발 시차의 합)와 ±1분 남짓 → 먼저 짝짓는다(급행이 완행 행을 가로채지 않게)
#   ⓞ ①에서 한 편도 안 이어졌는데 a 의 편 대부분이 b 를 est 만큼 **먼저** 떠난 행과 맞으면 반대로 가는 묶음이다(원천이 행선지를
#     잘못 적음) — 전부 짝 없음. 배차가 고르면 「다음 편의 행」과 일정한 시차가 생겨 ②가 가짜 묶음을 만든다(GPT 대조 1).
#   ② 남은 편은 같은 시차(±1분)로 이어지는 **서로 다른 짝이 둘 이상**인 묶음을 큰 것부터(급행끼리 · 대피로 늦는 완행끼리).
#     한 번 쓴 행은 다시 안 쓴다 · 한 편이 두 행과 맞는 것은 한 짝으로 센다(GPT 대조 2)
#   ③ 그래도 남은 편 — 이미 나온 시차(±1분)에 남은 행이 있으면 짝. 새 시차로 늦게 닿는 한 편(교행·대피 — 서해선 원종 18:12 →
#     백마 18:37 · 25분)은 ①②에서 이어진 편이 있고 남은 편과 남은 행이 창 안에서 **서로 하나뿐일 때만** 받고(남은 급행이 늦은
#     완행의 행을 가로채지 않게 — 후보가 둘이면 둘 다 짝 없음), **약한 짝**으로 표시한다 — 판정 등급을 추정으로(GPT 대조 3).
#   ④ 도착역 출발이 꼭 24:00 이 되는 편은 원천 표기 한계(000000 = 출발 없음)로 행이 없다 — 같은 시차 묶음이 있을 때 **한 편만**
#     선다고 보고 약한 짝으로 표시한다(GPT 대조 5).
#   짝이 없는 편 = b 의 시간표에 그 편의 행이 없다 — 무정차 통과이거나 원천에서 행이 빠진 것. 여기서는 못 가른다.
#   창: 급행 하한 = 소요×0.45−2분 · 늦는 완행 상한 = 소요×1.3+10분(경의선 도농 20:15 용문행은 대피로 운길산까지 20분 거리를
#   29분에 간다 — 좁게 잡으면 서는 편을 버린다). 창과 「둘 이상」은 코드 상수다 — 규칙 파일 값으로 옮기는 변경안은 92 닫힘 문서.
STOP_FAST_RATIO, STOP_SLOW_RATIO, STOP_SLOW_PAD_MIN, STOP_CLASS_MIN = 0.45, 1.3, 10, 2
STOP_REVERSED_SHARE = 0.8
MIDNIGHT_MIN = 24 * 60
STOP_CACHE_MAX = 20000


def match_stops(a_mins, b_mins, est, n_edges):
    """a_mins: 출발역의 그 묶음 출발 분 · b_mins: 도착역의 같은 묶음 출발 분(둘 다 정렬 · 중복 없음)
    → ({a 분: 짝지은 b 분}, {약한 짝인 a 분}). 약한 짝 = ③의 늦은 한 편 · ④의 자정 정각 — 시간표 행으로 직접 확인한 것이 아니다."""
    if not a_mins or not b_mins:
        return {}, set()
    if est is not None:
        lo, hi = max(1, int(est * STOP_FAST_RATIO) - 2), int(math.ceil(est * STOP_SLOW_RATIO)) + STOP_SLOW_PAD_MIN
    else:                                   # 소요를 모르는 구간(구조만 이어진 간선) — 간선 수로 넉넉한 창만 잡는다
        lo, hi = max(1, n_edges), n_edges * 8
    rem_a, rem_b, matched, weak, seen, used = list(a_mins), list(b_mins), {}, set(), set(), set()

    def pairs():
        out = collections.defaultdict(list)
        for a in rem_a:
            for b in rem_b[bisect.bisect_left(rem_b, a + lo):bisect.bisect_right(rem_b, a + hi)]:
                out[b - a].append((a, b))
        return out

    def pick(P, deltas):
        """deltas 순서대로 훑어 a·b 를 한 번씩만 쓰는 짝 목록."""
        ua, ub, got = set(), set(), []
        for k in deltas:
            for a, b in P.get(k, ()):
                if a not in ua and b not in ub:
                    ua.add(a)
                    ub.add(b)
                    got.append((a, b, k))
        return got

    def commit(got, as_weak=False):
        nonlocal rem_a, rem_b
        if not got:
            return
        for a, b, k in got:
            matched[a] = b
            used.add(k)
            if as_weak:
                weak.add(a)
        ua, ub = {g[0] for g in got}, {g[1] for g in got}
        rem_a = [a for a in rem_a if a not in ua]
        rem_b = [b for b in rem_b if b not in ub]

    if est is not None:                                   # ① 완행
        near = sorted(range(int(math.floor(est)) - 1, int(math.ceil(est)) + 2), key=lambda k: abs(k - est))
        got = pick(pairs(), near)
        if got:
            commit(got)
            seen.update(near)
        elif len(a_mins) >= STOP_CLASS_MIN:               # ⓞ 반대로 가는 묶음
            bs, r = set(b_mins), int(round(est))
            rev = sum(1 for a in a_mins if any((a - r + j) in bs for j in (-1, 0, 1)))
            if rev >= STOP_REVERSED_SHARE * len(a_mins):
                return {}, set()
    while rem_a and rem_b:                                # ② 같은 시차 묶음 — 서로 다른 짝이 둘 이상
        P = pairs()
        if not P:
            break
        ref = est if est is not None else (lo + hi) / 2
        best, best_got = None, []
        for k in sorted(P, key=lambda k: abs(k - ref)):
            got = pick(P, (k, k - 1, k + 1))
            if len(got) > len(best_got):
                best, best_got = k, got
        if len(best_got) < STOP_CLASS_MIN:
            break
        commit(best_got)
        seen.update((best - 1, best, best + 1))
    if rem_a and rem_b:                                   # ③ 남은 편
        P = pairs()
        ref = est if est is not None else 0
        commit(pick(P, sorted((k for k in P if k in seen), key=lambda k: abs(k - ref))))
        if rem_a and rem_b and used and est is not None:
            late = [(a, b, k) for k, v in pairs().items() if k >= est - 2 for a, b in v]
            ca, cb = collections.Counter(x[0] for x in late), collections.Counter(x[1] for x in late)
            commit([x for x in late if ca[x[0]] == 1 and cb[x[1]] == 1], as_weak=True)   # 서로 하나뿐인 짝만
    # ④ 자정 정각 — 원천은 00:00:00 출발을 「출발 없음」과 같은 값(000000)으로 줘서 그 행이 시간표에 없다(build_timetable hhmmss).
    #   같은 시차로 이어진 편이 이미 있을 때 한 편만(그 행은 하나뿐이다) · 08호선 휴일 모란행 막차의 수진 등.
    for a in rem_a:
        if any(a + k == MIDNIGHT_MIN for k in used):
            matched[a] = MIDNIGHT_MIN
            weak.add(a)
            break
    return matched, weak


# ── 검증기 ────────────────────────────────────────────────────────────────
class Verifier:
    def __init__(self, tt, lo, rules, holidays, tw=None, bus=None, sc=None, ex=None, car=None,
                 bk=None, bike_live=None, bike_router=None, cg_data=None, bus_prof=None):
        self.tt, self.lo, self.R, self.tw, self.bus = tt, lo, rules, tw, bus
        self.bus_prof = bus_prof        # 버스 구간 통행시간 프로파일(v0.9 · 41번 방) — None 이면 종전 모델(거리 ÷ 표정속도)
        self.sc = sc
        self.ex = ex            # 역 출구 좌표(OSM · 추정) — 지하철↔버스 환승에만 쓴다 (19번 방)
        # ★ 55 ④ — 동명이역: 좌표표를 이 판정기의 규칙 값으로 물리적 역으로 묶고, 출구표를 그 묶음으로 나눈다
        if sc is not None and hasattr(sc, "configure"):
            am = rules["station_names"]["동명이역_좌표차_m"]["value"]
            if getattr(sc, "ambig_m", None) != am:
                sc.configure(am)
        if ex is not None and hasattr(ex, "bind"):
            ex.bind(sc)
        self.car = car          # CarService(그래프 + 라우터 + 규칙) — 없으면 택시는 종전대로 근거없음 (21번 방)
        self._case_date = None  # verify_case 가 매 건 갈아 끼운다 — 자동차 소요는 날짜(요일형)가 필요하다
        self.bk = bk            # 따릉이 운영 대여소(22번 방) — 없으면 자전거는 근거없음
        self.bike_live = bike_live      # 실시간 거치 조회(BikeLive) — None 이면 가용 근거없음
        self.bike_router = bike_router  # 길찾기(bike/foot · 101 부터 로컬 graph_router 뿐) — None 이면 승차 소요 근거없음 · 걷기는 직선
        self.cg_data = cg_data          # 혼잡도(Congestion · v0.8 @ 부품) — None 이면 혼잡 가산 없음(근거없음)
        self.lfd_enabled = True         # 마지막 성립 출발 역산(v0.8). 자기점검처럼 수천 번 돌릴 땐 끈다(케이스당 최대 600회 재판정)
        self._lines_of = None
        self.holidays = holidays
        self.rules_src = rules.get("source_id", "mobility_rules")
        self.rules_at = rules.get("effective_date")
        self._passes_cache = {}
        self._origin_cache = {}
        self._dominant_cache = {}
        self._stop_cache = {}       # 92: (노선, 출발역, 도착역, 요일, 행선지) → 도착역 정차 대조 결과
        sj = rules["last_train"]["신정지선_토요일_예외"]["value"]
        self.sinjeong = (sj["line"], set(sj["stations"]))
        self.disr = []          # 이 케이스의 이슈 조건. verify_case 가 매 건 갈아 끼운다.
        self._cg = {}           # 후보 생성기(v0.5) — first_visit 별로 하나. 길찾기 가산이 달라진다
        self._leg_cache = {}    # (v0.8) 케이스 안 자동차·자전거 구간 결과 캐시 — verify_case 가 매 건 비운다
        self.lfd_capped = False # (v0.8) 마지막 성립 출발 역산이 상한(max_probe)에 걸렸다

    _CG_LOCK = __import__("threading").Lock()   # #33 — 요청별 얕은 복사본이 _cg 를 공유한다. 처음 만들 때만 잠근다

    def candidate_graph(self, first_visit=True):
        if first_visit not in self._cg:
            with self._CG_LOCK:
                if first_visit not in self._cg:
                    self._cg[first_visit] = CandidateGraph(self.lo, self.tw, self.R, first_visit)
        return self._cg[first_visit]

    # 규칙 값 꺼내기 — 값이 없으면 죽는다. 조용히 기본값을 쓰지 않는다.
    def rv(self, *path):
        n = self.R
        for k in path:
            n = n[k]
        # ☆`[2026-09-29 문제목록 #49]` 정책 수치는 팀 guardrails.yaml 이 정본이다 — 규칙 칸에는 value_from 만 있다.
        #   값이 비고 가리키는 곳이 있으면 거기서 읽는다(없으면 GuardrailMissing — 조용히 기본값을 쓰지 않는다).
        if n.get("value") is None and isinstance(n.get("value_from"), str):
            from .guardrails import lookup
            return lookup(n["value_from"])
        return n["value"]

    # ── 경고 어휘 (rules warnings 절) ─────────────────────────────────
    # 경고는 문자열이 아니라 {code, class, text, to_answer} 다.
    # 문구를 고쳐도 코드가 안 바뀌므로 회귀(expect_warn_codes)와 계약(Evidence.value.warnings)이
    # 문장에 매이지 않는다. 접기 설계 v1 §17.
    # ★ 정의에 없는 코드나 인자가 모자라면 여기서 죽는다 — 조용히 넘어가지 않는다.
    def warn_msg(self, code, **kw):
        spec = self.R["warnings"][code]
        if "text_ref" in spec:
            node = self.R
            for seg in spec["text_ref"].split("."):
                node = node[seg]
            text = node
        else:
            text = spec["text"].format(**kw)
        return {"code": code, "class": spec["class"],
                "text": text, "to_answer": spec["to_answer"]}

    # ── 이슈 조건 (코어 current_state.replan) ────────────────────────────
    # ★ 우리가 관측한 게 아니다. 등급을 올리지 않는다 — 준 쪽의 등급을 그대로 싣는다.
    # ★ 이슈로 인한 불가는 시간표로 인한 불가와 **완화 조건이 다르다**.
    #   시간표 불가는 "더 일찍/늦게 출발", 이슈 불가는 "우회 또는 복구 대기"다.
    # ★ 대안 열거는 self.disr 을 그대로 물려받는다 — 대안으로 낸 노선이 같은 이슈에 걸리면 안 된다.
    #   alternatives() 가 verify_leg 을 다시 부르므로 인스턴스에 두면 저절로 상속된다.
    DISR_KINDS = ("line_closed", "station_skip", "edge_closed", "route_closed", "stop_skip", "route_detour")
    # ☆`[2026-10-01 · 84 · 문제목록 #39]` 버스 사건을 노선 전체 중단(route_closed) 하나로만 받던 것을 나눈다.
    #   stop_skip    {ars, route?, window?} — 그 정류장(ARS 5자리)에 서지 않는다. 그 정류장에서 **타거나 내리는** 버스
    #                구간만 불가, 지나가기만 하는 구간은 그대로(지하철 station_skip 과 같은 뜻). route 가 없으면 그 정류장의
    #                모든 노선. window ["HH:MM","HH:MM"](운행일 시각)이 있으면 그 시간에 걸칠 때만 — 없으면 늘.
    #   route_detour {route} — 그 노선이 우회 운행 중. 지나는 길·소요를 모른다 → 그 노선 버스 구간은 **판단불가**
    #                (no_data · 근거없음) — 불가가 아니다(못 간다는 근거가 없다). 다른 후보가 있으면 그쪽이 계획이 된다.

    @staticmethod
    def ars_norm(x):
        """ARS 표기 → 5자리 문자열. 옛 표기 `01-126` → `01126`. 숫자 다섯이 아니면 None."""
        t = str(x or "").replace("-", "").strip()
        return t if len(t) == 5 and t.isdigit() else None

    def _stop_skip_at(self, route_nm, stop, t0, t1):
        """그 노선이 그 정류장 행(stop)에 [t0, t1] 사이 서지 않는 사건. 없으면 None. t1=None 은 끝 모름(그 뒤 전부)."""
        ars = self.ars_norm(stop.get("ars_id"))
        if ars is None:
            return None
        for d in self.disr:
            if d.get("kind") != "stop_skip" or self.ars_norm(d.get("ars")) != ars:
                continue
            if d.get("route") not in (None, "", route_nm):
                continue
            w = d.get("window")
            if w:
                ws, we = to_service_min(w[0]), to_service_min(w[1])
                if (t1 is not None and t1 < ws) or t0 > we:
                    continue
            return d
        return None

    def _disr(self, kind, **eq):
        for d in self.disr:
            if d.get("kind") != kind:
                continue
            if all(d.get(k) == v for k, v in eq.items()):
                return d
        return None

    def _disr_edges(self, line):
        """그 노선에서 끊긴 간선들. frozenset 쌍으로 돌려준다."""
        out = set()
        for d in self.disr:
            if d.get("kind") == "edge_closed" and d.get("line") == line:
                a, b = d["between"]
                out.add(frozenset((a, b)))
        return out

    def _ev_disr(self, d, claim):
        # source_type 은 'case_event' 다 — 03번 방 스키마의 CHECK 가
        # ('db','policy','case_event') 만 허용한다. 'external' 같은 새 값을 만들면
        # **판정 근거가 적재 단계에서 거부된다.** 그 CHECK 는 카카오 응답('tool_result')을
        # 막으려고 건 것이고, 좁게 두는 편이 맞다. 이슈는 케이스에 붙은 사건이다.
        return {"source_type": "case_event", "source_id": d.get("source", "core.current_state.replan"),
                "grade": d.get("grade", "추정"), "observed_at": d.get("observed_at"),
                "claim": claim}

    def _disr_label(self, d):
        return d.get("note") or {"line_closed": "노선 운행중단", "station_skip": "무정차 통과",
                                 "edge_closed": "구간 운행중단",
                                 "route_closed": "노선 운행중단", "stop_skip": "정류장 무정차 통과",
                                 "route_detour": "노선 우회 운행"}[d["kind"]]

    def _passes(self, line, origin, dest, target, dir=None, origin_terminal=False,
                full_circuit=False):
        # dir 은 순환선에서만 쓰인다(2호선). 다른 노선은 행선지가 방향을 정한다.
        k = (line, origin, dest, target, dir, origin_terminal, full_circuit)
        if k not in self._passes_cache:
            self._passes_cache[k] = self.lo.passes(
                line, origin, dest, target, dir=dir,
                origin_terminal=origin_terminal, full_circuit=full_circuit)
        return self._passes_cache[k]

    def dominant_dest(self, line, station, day_type, dir):
        """그 (역, 요일, dir) 출발행의 **지배적 행선지** — 그 방향 순환 편성의 종착지.

        ★ 02호선은 본선 열차의 행선지가 거의 전부 '성수' 다(강변 평일 479편 중 457편).
          '행선지를 처음 만나면 종착' 으로 읽으면 왕십리 내선 성수행이 3정거장짜리 열차가 되어
          왕십리→잠실이 외선 35정거장(73분)으로 나온다. 실제는 내선 12정거장이다.
          지배적 행선지의 열차는 행선지를 지나쳐 **한 바퀴 도는 편성**으로 본다.
          점유율이 낮은 행선지(막차 근처 삼성·홍대입구 각 1편)는 진짜 단축운행이다.
        """
        k = (line, station, day_type, dir)
        if k not in self._dominant_cache:
            deps = [d for d in self.tt.departures(line, station, day_type) if d.dir == dir]
            c = collections.Counter(d.dest for d in deps if d.dest)
            top = c.most_common(1)
            thr = self.rv("last_train", "순환선_한바퀴_판별")
            self._dominant_cache[k] = (top[0][0] if top and deps
                                       and top[0][1] / len(deps) >= thr else None)
        return self._dominant_cache[k]

    def is_origin_station(self, line, station, day_type):
        """이 역이 **시발역**인가 — dest 가 역 자신인 행이 그 역 출발의 다수인가.

        02호선 성수는 유효 출발 592편이 전부 dest='성수' 다(05:30~24:51). 입고 열차가 아니라
        시발 열차이고, 소스가 시발 열차의 행선지를 역 자신으로 준다. 05호선 여의도의
        24:47 여의도행은 198편 중 1편이라 그쪽이 입고다. 규칙 last_train.시발열차_판별.
        """
        k = (line, station, day_type)
        if k not in self._origin_cache:
            deps = self.tt.departures(line, station, day_type)
            n = len(deps)
            self_n = sum(1 for d in deps
                         if d.dest and self.lo.resolve_dest(line, d.dest) == station)
            thr = self.rv("last_train", "시발열차_판별")
            self._origin_cache[k] = bool(n and self_n / n >= thr)
        return self._origin_cache[k]

    # ── 92: 그 편이 도착역에 서는가 ──
    def _stop_status(self, line, origin, target, day_type, d, v):
        """그 편(d)이 target 에 **서는가** → "stop" · "weak" · "skip" · "unknown" · None(대조하지 않음).

        ☆`[2026-10-02 92번 방]` 앞 판은 역 순서상 「지난다」만 보고 그 편으로 내리는 것을 성립시켰다 — 01호선 개봉→구일 09:42
          용산행(경인 급행 · 구일 무정차)을 「성립·확정」으로 냈다. 정차역 목록을 따로 두지 않고 **도착역 시간표**로 가른다.
          · stop    — 도착역 시간표에 그 편의 행이 있다(짝짓기 ①②③의 확인된 시차)
          · weak    — 선다고 보지만 행으로 직접 확인한 것은 아니다(늦은 한 편 · 자정 정각) → 쓰되 등급을 추정으로 내린다
          · skip    — 그 행선지 묶음이 도착역에 하루 0행인데, 도착역에는 같은 방향 다른 행선지 행이 있다(묶음 전체에 행이 없다)
          · unknown — 묶음의 다른 편은 도착역에 행이 있는데 이 편의 행은 없다(섞인 묶음의 급행 · 원천 누락 · 잘못 적힌 행선지)
                      또는 도착역에 그 방향·그 요일 출발 행이 통째로 없다
          · None    — 순환선(행선지를 지나쳐 돈다) · 도착역이 그 편의 종착역(출발 행이 원래 없다) · 도착역 시간표를 안 올렸다
        skip·unknown 은 둘 다 「무정차인지 원천 누락인지 못 가른다」 — 그 편을 쓰지 않고, 「못 간다」를 말할 때 미확인 편으로 센다.
        지나가는 것(`passes`)은 그대로다 — 서지 않는 역을 지나 더 먼 역에서 내리는 것은 그 역의 행으로 다시 본다.
        ★ 캐시는 이 판정기가 든 시간표·역 순서가 **바뀌지 않는다**는 전제다(Timetable 은 적재 뒤 읽기만 한다). 도착역을 아직 안 올린
          경우(None)는 캐시하지 않는다 — 일부만 올린 실행(회귀 CLI)과 전체 상주(서버)가 같은 역을 올렸을 때 같은 값을 낸다."""
        dest = self.lo.resolve_dest(line, d.dest)
        if dest is None or dest == target or self.lo.is_loop(line) or not v.path or target not in v.path:
            return None
        if not self.tt.has_station(line, target):
            return None
        k = (line, origin, target, day_type, dest)
        tab = self._stop_cache.get(k)
        if tab is None:
            if len(self._stop_cache) >= STOP_CACHE_MAX:  # 상주 서버에서 (역 쌍 × 요일 × 행선지)가 끝없이 쌓이지 않게 — 통째로 비운다
                self._stop_cache.clear()
            tab = self._stop_cache[k] = self._stop_table(line, origin, target, day_type, dest, v.path)
        if isinstance(tab, str):
            return tab
        matched, weak = tab
        if d.min not in matched:
            return "unknown"
        return "weak" if d.min in weak else "stop"

    def _stop_table(self, line, origin, target, day_type, dest, path):
        tdeps = self.tt.departures(line, target, day_type)
        if not tdeps:
            return "unknown"                # 올린 역인데 그 요일 출발 행이 0 — 종착역이 아닌 자리라 확인할 수 없다(GPT 대조 6)
        res = self.lo.resolve_dest
        b_mins = sorted({x.min for x in tdeps if x.dest and res(line, x.dest) == dest})
        i = path.index(target)
        if not b_mins:
            nxt = path[i + 1] if i + 1 < len(path) else None
            for other in {res(line, x.dest) for x in tdeps if x.dest} - {None, target, dest}:
                p2 = self.lo.path(line, target, other)
                if p2 and len(p2) > 1 and p2[1] == nxt:
                    return "skip"           # 같은 방향 다른 행선지 편은 행이 있는데 이 묶음만 0행
            return "unknown"                # 그 방향 출발 행이 통째로 없다
        a_mins = sorted({x.min for x in self.tt.departures(line, origin, day_type)
                         if x.dest and res(line, x.dest) == dest})
        return match_stops(a_mins, b_mins, self.lo.travel_min_on_path(line, path, target), i)

    # ── 출발 후보 고르기 — 막차 4종이 전부 여기 있다 ──
    def candidates(self, line, origin, target, day_type):
        """그 역 출발행에서 **목적지까지 가는 열차**만 남긴다. 막차 4종이 전부 여기 있다."""
        deps = self.tt.departures(line, origin, day_type)
        is_origin = self.is_origin_station(line, origin, day_type)
        # ★ 한 바퀴 가정은 운행 시간대 안에서만 성립한다. 막차는 돌지 않는다.
        tdeps = self.tt.departures(line, target, day_type)
        last_at_target = tdeps[-1].min if tdeps else None
        margin = self.rv("last_train", "한바퀴_도착_여유_분")
        closed = self._disr_edges(line)      # 끊긴 간선 — 그 위를 지나는 편성은 쓸 수 없다
        out, drop, unk, weak = [], collections.Counter(), [], set()
        for d in deps:
            if not d.dest:                                    # ② dest_nm 없음
                drop["행선지없음"] += 1
                continue
            self_dest = self.lo.terminates_here(line, origin, d.dest)
            if self_dest and not is_origin:                    # ③ 입고(종착) 열차
                drop["종착열차"] += 1
                continue
            full = (self.lo.is_loop(line)
                    and d.dest == self.dominant_dest(line, origin, day_type, d.dir))
            v = self._passes(line, origin, d.dest, target, d.dir,
                             origin_terminal=(self_dest and is_origin), full_circuit=full)
            if v.value is True:
                # ★ 무정차(station_skip)는 여기서 안 거른다 — 서지 않을 뿐 지나간다.
                #   끊긴 간선(edge_closed)은 지나가지도 못하므로 그 편성을 뺀다.
                if closed and self._path_blocked(v.path, target, closed):
                    drop["이슈_구간차단"] += 1
                    continue
                if full and last_at_target is not None:
                    ride = self.lo.travel_min_on_path(line, v.path, target)
                    if ride is not None and d.min + math.ceil(ride) > last_at_target + margin:
                        drop["운행종료후_한바퀴"] += 1        # 막차가 한 바퀴 돈다는 판정을 막는다
                        continue
                # ☆`[2026-10-02 92번 방]` 지나가는 것과 서는 것은 다르다 — 도착역 시간표에 그 편의 행이 없으면 그 편으로 내리지 못한다.
                #   시발 열차(행선지가 역 자신)는 어느 묶음인지 몰라 대조하지 않는다(등급은 이미 추정).
                st = None if (self_dest and is_origin) else self._stop_status(line, origin, target, day_type, d, v)
                if st in ("skip", "unknown"):
                    drop["무정차_통과" if st == "skip" else "정차_미확인"] += 1
                    unk.append(d.min)
                    continue
                if st == "weak":
                    weak.add(d.min)
                out.append((d, v, full))
            elif v.value is False:
                drop["단축운행"] += 1
            else:
                drop["행선지_해석불가"] += 1
        drop.unknown_mins = unk         # 92: 도착역 행이 없어 뺀 편(무정차_통과 + 정차_미확인)의 출발 분 — verify_leg 가 「못 간다」를 말하기 전에 본다
        drop.weak_mins = weak           # 92: 약한 짝(늦은 한 편 · 자정 정각)으로 남긴 편 — 그 편을 쓰면 등급을 추정으로 내린다
        return out, drop, is_origin

    @staticmethod
    def _path_blocked(path, target, closed):
        """판정에 쓰는 경로가 끊긴 간선을 밟는가. **target 까지만** 본다(그 뒤는 안 탄다)."""
        if not path:
            return False
        for i in range(len(path) - 1):
            if frozenset((path[i], path[i + 1])) in closed:
                return True
            if path[i + 1] == target:
                return False
        return False

    def verify_leg(self, idx, leg, now_min, day_type, is_saturday, worst=False, party=None):
        line = leg["line"]
        a, b = leg["from"], leg["to"]
        label = f"{line} {a}→{b}"
        ev, warn = [], []

        # 0-) 이슈 조건을 시간표보다 **먼저** 본다. 시간표에 열차가 있어도 오늘 안 서면 못 탄다.
        d = self._disr("line_closed", line=line)
        if d:
            return LegResult(idx, label, "infeasible",
                             f"{line} 이 운행중단이다 ({self._disr_label(d)})",
                             grade=d.get("grade", "추정"), code="disruption",
                             relief="다른 노선 또는 수단으로 우회. 복구 시각은 우리가 모른다",
                             evidence=[self._ev_disr(d, f"{line} 운행중단")])
        for nm, wh in ((a, "출발"), (b, "도착")):
            d = self._disr("station_skip", line=line, station=nm)
            if d:
                return LegResult(
                    idx, label, "infeasible",
                    f"{line} 열차가 {nm} 에 서지 않는다 ({self._disr_label(d)}) — "
                    f"{wh} 역으로 쓸 수 없다. 지나가기는 한다",
                    grade=d.get("grade", "추정"), code="disruption",
                    relief=f"{nm} 대신 앞뒤 역에서 타거나 내려 걷는다. 또는 다른 노선·수단으로 우회",
                    evidence=[self._ev_disr(d, f"{line} {nm} 무정차")])

        # 0) 시간표에 그 역이 있는가
        for nm in (a, b):
            if not self.tt.has_station(line, nm):
                sim = self.tt.similar(line, nm)
                hint = f" (비슷한 역명: {', '.join(sim)})" if sim else ""
                return LegResult(idx, label, "unknown",
                                 f"{line} 시간표에 '{nm}' 이 없다{hint}", grade="근거없음", code="no_data",
                                 dropped={},
                                 warnings=[self.warn_msg("MOB_W_STATION_NOT_IN_TIMETABLE",
                                                         line=line, station=nm, hint=hint)])
        if not self.tt.departures(line, a, day_type):
            return LegResult(idx, label, "unknown",
                             f"{line} {a} 의 {day_type} 시간표가 없다", grade="근거없음", code="no_data")

        # 1) 방향 = 행선지(+순환선은 dir). 목적지를 지나는 열차만 남긴다
        cands, drop, is_origin = self.candidates(line, a, b, day_type)
        if not cands:
            # ☆`[2026-10-02 80번 방]` 「목적지까지 가는지 배제하지 못한 편」 = 행선지_해석불가(역 순서 표의 근거없음 간선이 목적지 앞에 낌)
            #   + 행선지없음(원천 빈칸). 이런 편이 한 편이라도 있으면 「열차가 없다(확정)」·「이슈로 전부 끊겼다(더 일찍·늦게도 같다)」고
            #   말하지 않는다 — 모른다고 한다. 앞 판은 가장 많이 버린 이유만 봐서, 상봉→회기처럼 춘천행(단축운행 63편)이 다수이고
            #   청량리행 12편이 해석불가인 자리를 no_service·확정으로 냈다(그 12편은 실제로 간다). GPT 대조 5·6.
            # ☆`[2026-10-02 92번 방]` 도착역 시간표에 행이 없어 뺀 편(정차_미확인 · 무정차_통과)도 「배제하지 못한 편」이다 —
            #   원천 누락이면 실제로 서는 편이다. 묶음 전체가 0행인 것도 누락과 못 가르므로 같이 센다(GPT 대조 4).
            stop_unk = drop.get("정차_미확인", 0) + drop.get("무정차_통과", 0)
            unresolved = drop.get("행선지_해석불가", 0) + drop.get("행선지없음", 0) + stop_unk
            if drop.get("이슈_구간차단") and not unresolved:
                # ★ 이슈로 길이 끊긴 것과 원래 열차가 없는 것을 섞어 말하면 안 된다.
                #   완화 조건이 다르다 — 이쪽은 더 일찍 출발해도 안 된다.
                # ★ 끊긴 간선을 **전부** 말한다. 하나만 말하면 순환선에서
                #   "성수–건대입구만 끊겼는데 왜 반대로 못 도나" 가 된다 —
                #   실제로는 반대쪽 간선도 끊겨서 양방향이 막힌 것이다.
                dds = [x for x in self.disr
                       if x.get("kind") == "edge_closed" and x.get("line") == line]
                seg = " · ".join(f"{x['between'][0]}–{x['between'][1]}" for x in dds)
                both = (" 양방향이 다 막혔다." if len(dds) > 1 and self.lo.is_loop(line) else "")
                return LegResult(
                    idx, label, "infeasible",
                    f"{line} {seg} 구간이 끊겨 {a}→{b} 를 잇는 편성이 없다.{both} "
                    f"시간표에는 {drop['이슈_구간차단']}편이 있다",
                    grade=worst_grade(*[x.get("grade", "추정") for x in dds]), dropped=dict(drop), code="disruption",
                    relief="다른 노선 또는 수단으로 우회. 더 일찍·늦게 출발해도 같다",
                    evidence=[self._ev_disr(x, f"{line} {x['between'][0]}–{x['between'][1]} 운행중단")
                              for x in dds])
            if unresolved:
                blocked = (f" (이슈로 끊긴 구간을 지나는 편 {drop['이슈_구간차단']}편은 못 쓴다)"
                           if drop.get("이슈_구간차단") else "")
                if stop_unk and not (drop.get("행선지_해석불가") or drop.get("행선지없음")):
                    return LegResult(idx, label, "unknown",
                                     f"{a} 에서 {b} 쪽으로 가는 편은 있는데 {b} 의 시간표에서 그 편의 정차 행을 확인하지 못했다 "
                                     f"(이 편만 행 없음 {drop.get('정차_미확인', 0)}편 · 그 행선지 편이 하루 0행 "
                                     f"{drop.get('무정차_통과', 0)}편){blocked}",
                                     grade="근거없음", dropped=dict(drop), code="no_data")
                return LegResult(idx, label, "unknown",
                                 f"{a} 출발 열차의 행선지를 확인할 수 없어 {b} 까지 간다고 말할 수 없다{blocked}",
                                 grade="근거없음", dropped=dict(drop), code="no_data")
            # ★ 반대 방향은 있는데 이쪽만 0편이면 운행이 없는 게 아니라 **수집이 빠진 것**이다.
            #   도림천→신도림 110편 / 신도림→도림천 0편 이 실제로 그랬다(지선 편성 누락).
            #   소스가 없는 구간을 정상으로 바꾸지 않는 것과 같은 이유로, 불가로도 바꾸지 않는다.
            rev, _, _ = self.candidates(line, b, a, day_type)
            if rev:
                return LegResult(
                    idx, label, "unknown",
                    f"{a}→{b} 편성이 {day_type} 시간표에 0편인데 반대 방향 {b}→{a} 는 "
                    f"{len(rev)}편 있다 — 운행이 없는 게 아니라 수집이 빠진 것으로 본다",
                    grade="근거없음", dropped=dict(drop), code="no_data",
                    warnings=[self.warn_msg("MOB_W_DIRECTION_NOT_COLLECTED",
                                            line=line, a=a, b=b)],
                    evidence=[self._ev_rule("service_window.왕복_비대칭_수집누락", "확정")])
            return LegResult(idx, label, "infeasible",
                             f"{a} 에서 {b} 방향으로 가는 열차가 {day_type} 시간표에 없다",
                             grade="확정", dropped=dict(drop), code="no_service",
                             relief="노선 교체 또는 반대 방향 확인이 필요하다")

        first, last = cands[0][0].min, cands[-1][0].min
        gap_max = self.rv("service_window", "gap_max_min")
        scan = self.rv("service_window", "candidate_scan_min")

        # 2) 첫차 이전 · 막차 이후
        after = [(d, v) for d, v, _f in cands if d.min >= now_min]
        # ☆`[2026-10-02 92번 방]` 「못 간다」(막차 이후 · 첫차 이전 · 배차 공백)는 그 사이에 **정차를 확인 못 해 뺀 편**이 없을 때만
        #   말한다 — 그 편이 실제로 서면 판정이 뒤집힌다. 있으면 모른다(no_data). 무정차_통과(묶음 전체 0행)도 같이 센다(GPT 대조 4).
        unk = [m for m in getattr(drop, "unknown_mins", ()) if m >= now_min]
        unk_until = (after[0][0].min if after else None)
        unk_hit = [m for m in unk if unk_until is None or m < unk_until]
        if unk_hit and (not after or now_min < first or after[0][0].min - now_min > gap_max):
            return LegResult(idx, label, "unknown",
                             f"{fmt_min(now_min)} 이후 {a} 를 떠나 {b} 쪽으로 가는 편 {len(unk_hit)}편"
                             f"({fmt_min(unk_hit[0])}~)이 {b} 에 서는지 시간표에서 확인하지 못했다 — "
                             + (f"확인된 다음 편은 {fmt_min(after[0][0].min)}" if after else "확인된 편은 더 없다"),
                             grade="근거없음", dropped=dict(drop), code="no_data")
        if not after:
            # ☆`[2026-09-29 문제목록 #4]` 새벽(24 시 이상) 요청의 「첫차를 기다리면 성립」은 **다음 운행일**의 첫차다.
            #   앞 판은 그날(전날 운행일) 요일형의 첫차를 썼다 — 평일 다음 날이 공휴일이면 휴일 시간표의 첫차여야 한다.
            nd_type, nd_first = self._next_day_first(line, a, b, day_type, first) if now_min >= MIN_DAY else (day_type, first)
            roll = self._rollover_relief(now_min, nd_first)
            if roll:
                wall, gap = roll
                return LegResult(
                    idx, label, "infeasible",
                    f"{fmt_wall(now_min)} 은 {line} {a} 의 {nd_type} 첫차({fmt_min(nd_first)}) 이전이다 "
                    f"(전날 막차 {fmt_min(last)} 는 이미 지났다)",
                    grade="확정", dropped=dict(drop), code="before_first",
                    relief=f"{gap}분 뒤 첫차 {fmt_min(nd_first)} 를 기다리면 성립",
                    evidence=[self._ev_tt(line, a, nd_type, f"그 방향 첫 출발 {fmt_min(nd_first)}")])
            return LegResult(
                idx, label, "infeasible",
                f"{fmt_min(now_min)} 이후 {b} 까지 가는 열차가 없다 "
                f"(그 방향 마지막 출발 {fmt_min(last)} {cands[-1][0].dest}행)",
                grade="확정", dropped=dict(drop), code="after_last",
                relief=f"{fmt_min(last)} 까지 출발하면 성립. 이후는 수단 교체(버스·택시)",
                evidence=[self._ev_tt(line, a, day_type, f"그 방향 마지막 출발 {fmt_min(last)}")])
        if now_min < first:
            return LegResult(
                idx, label, "infeasible",
                f"{fmt_min(now_min)} 은 {line} {a} 의 {day_type} 첫차({fmt_min(first)}) 이전이다",
                grade="확정", dropped=dict(drop), code="before_first",
                relief=f"출발을 {fmt_min(first)} 로 미루면 성립",
                evidence=[self._ev_tt(line, a, day_type, f"그 방향 첫 출발 {fmt_min(first)}")])
        wait0 = after[0][0].min - now_min
        if wait0 > gap_max:
            return LegResult(
                idx, label, "infeasible",
                f"{fmt_min(now_min)} 이후 다음 출발이 {fmt_min(after[0][0].min)} 이라 "
                f"{wait0}분을 기다려야 한다 (공백 상한 {gap_max}분)",
                grade="추정", dropped=dict(drop), code="service_gap",
                relief=f"{fmt_min(after[0][0].min)} 출발을 기다리면 성립")

        # 3) ★ 가장 이른 **도착**을 고른다. 가장 먼저 떠나는 열차가 가장 먼저 닿지 않는다 —
        #    02호선 강변 14:00 내선 성수행은 성수까지 40정거장, 14:02 외선 성수행은 3정거장이다.
        #    소요도 그 열차가 실제로 도는 경로 위에서 잰다(travel_min 은 최단경로라 순환선에서 틀린다).
        window = [(d, v) for d, v in after if d.min <= after[0][0].min + scan]
        best = None
        for d, v in window:
            ride = self.lo.travel_min_on_path(line, v.path, b)
            arr = d.min + math.ceil(ride) if ride is not None else None
            key = (arr is None, arr if arr is not None else d.min, d.min)
            if best is None or key < best[0]:
                best = (key, d, v, ride, arr)
        _, nxt, verd, ride, arrive = best
        wait = nxt.min - now_min
        ride_grade = "추정" if ride is not None else "근거없음"

        grade = worst_grade(verd.grade, "확정")
        # ── 혼잡 @ 부품(v0.8 · rules congestion.levels · 33번 데이터). 판정 입력이 아니라 최악값 부품이다 —
        #   매우혼잡(≥100%) + 조건(luggage·infant·elderly)이면 **worst 통과에서만** 다음 편으로 미룬다(배차 1회 더).
        #   극심(≥130%)은 경고만(대안 제시는 후보 열거가 한다). 값이 없는 노선(9호선 밖 코레일 등)은 근거없음 → 가산 없음.
        cg_hit = self._congestion_hit(line, a, nxt.dir, day_type, nxt.min, party)
        if cg_hit:
            cval, lvl, crow, act = cg_hit
            ev.append({"source_type": "db", "source_id": crow.get("source_id"), "grade": "추정",
                       "observed_at": crow.get("fetched_at"),
                       "claim": f"{line} {a} {crow.get('dir_raw') or nxt.dir} {crow.get('day_type')} {crow.get('slot')} 혼잡도 {cval}% ({lvl})"})
            if lvl == "극심":
                warn.append(self.warn_msg("MOB_W_CONGESTION_SEVERE", line=line, station=a, pct=cval))
            elif act == "warning":
                warn.append(self.warn_msg("MOB_W_CONGESTION_CROWDED", line=line, station=a, pct=cval))
            if act == "board_wait_extra_headway":
                # 다음 편 = **같은 경로(같은 방향·같은 편성 경로)** 로 가는 다음 열차 중 가장 이른 도착.
                #   출발 목록 전체에서 다음 출발을 집으면 순환선(02호선)에서 반대 방향 한 바퀴 편성이 잡혀
                #   서초→강남 08:33 이 @94(최악 10:02)로 튄다 — 2026-09-24 대조에서 확인.
                later = [(d, v) for d, v in after
                         if d.min > nxt.min and d.min <= nxt.min + scan and d.dir == nxt.dir and v.path == verd.path]
                nxt2 = None
                for d2, v2 in later:
                    ride2 = self.lo.travel_min_on_path(line, v2.path, b)
                    arr2 = d2.min + math.ceil(ride2) if ride2 is not None else None
                    if arr2 is not None and (nxt2 is None or arr2 < nxt2[3]):
                        nxt2 = (d2, v2, ride2, arr2)
                ev.append(self._ev_rule("congestion.levels.매우혼잡", "추정"))
                if nxt2 is None:
                    warn.append(self.warn_msg("MOB_W_CONGESTION_NO_NEXT", line=line, station=a, pct=cval))
                    # ☆`[2026-09-29 문제목록 #6]` 앞 판은 여기서 원래 편을 그대로 「성립(확정)」으로 두었다. 다음 편이 없으면
                    #   조건 있는 일행(짐·유아·어르신)이 이 편을 못 탈 수 있다 — 등급을 추정으로 내리고 고객 문장에 싣는다.
                    grade = worst_grade(grade, "추정")
                    warn.append(self.warn_msg("MOB_W_CONGESTION_NO_NEXT_BOARD", line=line, station=a, pct=cval))
                else:
                    extra = nxt2[3] - arrive if arrive is not None else nxt2[0].min - nxt.min
                    warn.append(self.warn_msg("MOB_W_CONGESTION_EXTRA_WAIT", line=line, station=a, pct=cval, extra=extra))
                    if worst:
                        nxt, verd, ride, arrive = nxt2
                        wait = nxt.min - now_min
                        ride_grade = "추정"
        if is_origin:
            warn.append(self.warn_msg("MOB_W_ORIGIN_TERMINAL_DEST", station=a))
            ev.append(self._ev_rule("last_train.시발열차_판별", "추정"))
        if is_saturday and line == self.sinjeong[0] and (
                a in self.sinjeong[1] or b in self.sinjeong[1]):
            grade = worst_grade(grade, "추정")
            warn.append(self.warn_msg("MOB_W_SINJEONG_SAT"))
            ev.append(self._ev_rule("last_train.신정지선_토요일_예외", "추정"))

        ev.append(self._ev_tt(line, a, day_type,
                              f"{fmt_min(nxt.min)} 출발 {nxt.dest}행 (그 방향 {len(cands)}편 중, "
                              f"{fmt_min(after[0][0].min)}~ 안에서 도착이 가장 이른 편)"
                              + (f" · 행선지는 원천 빈칸을 열차 잇기로 채운 값({nxt.inferred})" if nxt.inferred else ""),
                              grade="추정" if nxt.inferred else "확정"))
        if nxt.min in getattr(drop, "weak_mins", ()):
            # ☆ 92 — 이 편의 도착역 정차는 시간표 행으로 직접 확인한 것이 아니다(늦은 한 편 · 자정 정각). 등급을 추정으로.
            grade = worst_grade(grade, "추정")
            ev.append(self._ev_tt(line, b, day_type,
                                  f"{fmt_min(nxt.min)} {a} 출발 {nxt.dest}행의 {b} 정차는 같은 행선지 다른 편의 시차로 미룬 것"
                                  "(그 편의 행을 직접 확인하지 못함)", grade="추정"))
        if nxt.inferred:
            # ★ 28 — 채운 행선지는 추정이다. 「목적지를 지난다」가 그 값에 기대므로 구간 등급도 추정 이하로
            grade = worst_grade(grade, "추정")
            warn.append(self.warn_msg("MOB_W_DEST_INFERRED", line=line, station=a, dest=nxt.dest))
        ev.append({"source_type": "db", "source_id": "line_station_order_v1",
                   "grade": verd.grade, "observed_at": self.lo.built_at, "claim": verd.reason})
        if drop.get("종착열차") or drop.get("단축운행") or drop.get("행선지없음"):
            ev.append(self._ev_rule("last_train.행선지_확인", "확정"))

        return LegResult(idx, label, "feasible",
                         f"{fmt_min(nxt.min)} {nxt.dest}행 승차 (대기 {wait}분)" + (" [최악]" if worst else ""),
                         grade=grade, depart_min=nxt.min, arrive_min=arrive,
                         wait_min=wait, ride_min=ride, ride_grade=ride_grade,
                         dropped=dict(drop), warnings=warn, evidence=ev, worst=worst)

    def _congestion_hit(self, line, station, dir, day_type, minute, party):
        """혼잡도 조회 → (값, 등급명, 행, action) 또는 None. rules congestion.levels 의 조건(luggage·infant·elderly)까지 본다."""
        if not self.cg_data:
            return None
        L = self.R["congestion"]["levels"]
        is_hol = self._case_date is not None and self._case_date.isoformat() in self.holidays
        hit = self.cg_data.lookup(line, station, dir, day_type, self._case_date, minute, L, is_holiday=is_hol)
        if hit is None:
            return None
        v, lvl, row = hit
        spec = L.get(lvl) or {}
        act = spec.get("action")
        if act not in ("board_wait_extra_headway", "warning", "suggest_alternative"):
            act = None
        if act in ("board_wait_extra_headway", "warning"):
            cond = spec.get("조건") or []
            if cond and not any((party or {}).get(c) for c in cond):
                act = None
        if lvl not in ("매우혼잡", "극심") and act is None:
            return None
        return v, lvl, row, act


    def _bus_seg(self, r, leg):
        """버스 구간의 (타는 행, 내리는 행, 정거장 수). ☆GPT 84 #6 — 구간에 `from_seq`·`to_seq` 가 있으면 **그 행**을 쓴다.
        이름만으로 찾으면(segment) 같은 노선에 같은 이름이 여러 번 있을 때 일정 짜기가 고른 행과 다른 행(사고로 빼 둔
        정류장)을 다시 집을 수 있다. 순번이 이름과 안 맞으면 순번을 믿지 않고 이름으로 찾는다."""
        fs, ts = leg.get("from_seq"), leg.get("to_seq")
        if fs is not None and ts is not None and fs < ts:
            rows = {x["seq"]: x for x in self.bus.stops.get(r.route_id, [])}
            a, b = rows.get(fs), rows.get(ts)
            if a and b and a["station_nm"] == leg["from"] and b["station_nm"] == leg["to"]:
                return a, b, ts - fs
        return self.bus.segment(r.route_id, leg["from"], leg["to"])

    def _stop_skip_fail(self, idx, label, nm, stop, wh, d):
        st = stop["station_nm"]
        return LegResult(idx, label, "infeasible",
                         f"버스 {nm} 이 {st}({stop.get('ars_id')}) 에 서지 않는다 ({self._disr_label(d)}) — "
                         f"{wh} 정류장으로 쓸 수 없다. 지나가기는 한다",
                         grade=d.get("grade", "추정"), code="disruption",
                         relief=f"{st} 대신 앞뒤 정류장에서 타거나 내려 걷는다. 또는 다른 노선·수단",
                         evidence=[self._ev_disr(d, f"버스 {nm} {st}({stop.get('ars_id')}) 무정차")])

    # ── 버스 — 지하철과 판정 구조가 다르다 ──
    def verify_leg_bus(self, idx, leg, now_min, day_type, worst=False):
        """버스는 노선 단위 소스다. '운행 구간 안인가 + 배차만큼 기다리는가' 로 본다.

        지하철처럼 '그 시각에 출발하는 차'를 찾을 수 없다 — 정류장별 시각이 없기 때문이다.
        그래서 대기가 추정이고, 승차 소요는 아예 내지 않는다(rules.bus.ride_model).
        """
        nm, a_nm, b_nm = str(leg["route"]), leg["from"], leg["to"]
        label = f"버스 {nm} {a_nm}→{b_nm}"
        d = self._disr("route_closed", route=nm)
        if d:
            return LegResult(idx, label, "infeasible",
                             f"버스 {nm} 이 운행중단이다 ({self._disr_label(d)})",
                             grade=d.get("grade", "추정"), code="disruption",
                             relief="다른 노선 또는 수단으로 우회. 복구 시각은 우리가 모른다",
                             evidence=[self._ev_disr(d, f"버스 {nm} 운행중단")])
        d = self._disr("route_detour", route=nm)
        if d and d.get("window"):
            # 시간대가 있는 우회 — 구간 시작부터 3시간(이 구간이 끝날 만한 폭) 안에 걸칠 때만. 없으면 늘
            ws, we = to_service_min(d["window"][0]), to_service_min(d["window"][1])
            if we < now_min or ws > now_min + 180:
                d = None
        if d:
            # ☆84 — 우회는 「못 간다」가 아니라 「얼마나 걸릴지 모른다」. 소요를 지어내지 않는다(판단불가)
            return LegResult(idx, label, "unknown",
                             f"버스 {nm} 이 우회 운행 중이다 ({self._disr_label(d)}) — 지나는 길·소요를 모른다",
                             grade="근거없음", code="no_data",
                             relief="다른 노선 또는 수단. 우회가 풀린 뒤 다시 판정",
                             evidence=[self._ev_disr(d, f"버스 {nm} 우회 운행")])
        if self.bus is None:
            return LegResult(idx, label, "unknown", "버스 노선 데이터를 읽지 못했다", grade="근거없음", code="no_data")
        r = self.bus.route(nm)
        if r is None:
            return LegResult(idx, label, "unknown",
                             f"노선 '{nm}' 이 수집 범위에 없다 (수집된 {len(self.bus.by_id)}노선 밖)", grade="근거없음", code="no_data",
                             warnings=[self.warn_msg("MOB_W_BUS_ROUTE_NOT_COLLECTED", route=nm)])
        if r.route_type_nm in (self.rv("bus", "route_type_제외") or []):
            return LegResult(idx, label, "unknown",
                             f"{nm} 은 {r.route_type_nm} 버스라 일반 이동 수단으로 쓰지 않는다",
                             grade="근거없음", code="no_data",
                             warnings=[self.warn_msg("MOB_W_BUS_ROUTE_EXCLUDED",
                                                     route=nm, route_type=r.route_type_nm)],
                             evidence=[self._ev_rule("bus.route_type_제외", "확정")])

        seg = self._bus_seg(r, leg)
        if seg is None:
            back = self.bus.segment(r.route_id, b_nm, a_nm)
            if back:
                return LegResult(idx, label, "infeasible",
                                 f"{nm} 은 {a_nm}→{b_nm} 방향으로 가지 않는다 (반대 방향 노선이다)",
                                 grade="확정", code="no_service", relief="반대 방향 정류장 또는 다른 노선")
            miss = [x for x, got in ((a_nm, self.bus.find_stops(r.route_id, a_nm)),
                                     (b_nm, self.bus.find_stops(r.route_id, b_nm))) if not got]
            return LegResult(idx, label, "unknown",
                             f"{nm} 정류장 목록에 {', '.join(miss) or '해당 구간'} 이 없다",
                             grade="근거없음", code="no_data")
        a, b, span = seg
        # ☆84 — 탈 정류장 무정차. 승차 시각은 요청 시각부터 배차 한 번 기다리는 사이로 본다(최악 대기 · 보수적)
        d = self._stop_skip_at(nm, a, now_min, now_min + (r.term_min or 0))
        if d:
            return self._stop_skip_fail(idx, label, nm, a, "타는", d)
        warn, ev = [], []
        if day_type != "weekday":
            warn.append(self.warn_msg("MOB_W_BUS_NO_DAYTYPE", route=nm, day_type=day_type))

        # 0) 운행 요일 — 45번 방(2026-09-24). bus_route 의 service_days: 'daily'(기본) · None(미상).
        #    필드가 없는 옛 파일은 종전 동작(매일). 미상·모르는 값은 판정하지 않는다 — 틀린 「성립」보다 no_data.
        #    'weekday' 같은 요일 분기는 두지 않는다 — case["date"] 는 **운행일**이고(04:00 전 시각은 그 운행일의 연장),
        #    「월~금」과 「공휴일 제외 평일」의 구분 근거도 아직 없다. 쓰는 노선이 생기면 그때 의미부터 정한다.
        # ☆`[2026-09-29 문제목록 #15]` 칸이 빠진 노선을 무조건 「매일」로 보지 않는다 — 칸을 쓰는 판에서 빠졌으면 모름(None).
        if r.raw and "service_days" in r.raw:
            sd = r.raw["service_days"]
        elif getattr(self.bus, "has_service_days", False):
            sd = None
        else:
            sd = "daily"
            warn.append(self.warn_msg("MOB_W_BUS_SERVICE_DAYS_ASSUMED", route=nm))
        if sd != "daily":
            why = (r.raw.get("service_days_basis") or "근거 없음") if sd is None else f"알 수 없는 service_days 값 {sd!r}"
            return LegResult(idx, label, "unknown", f"{nm} 의 운행 요일을 모른다 — {why}",
                             grade="근거없음", code="no_data", warnings=warn,
                             evidence=[self._ev_bus(r, "운행 요일 미상")])

        # 1) 운행 구간 — 여기만 확정이다
        if r.first_min is None or r.last_min is None:
            return LegResult(idx, label, "unknown",
                             f"{nm} 의 첫차·막차가 없다" + (f" ({r.raw.get('window_note')})" if r.raw.get("window_note") else ""),
                             grade="근거없음", code="no_data")
        # ★ 45: 운행일 경계(04:00) 뒤에도 전날 운행분이 도는 노선(N61 막차 28:10). 04:00~04:10 요청은
        #   to_service_min 이 올리지 않아 그날 첫차(23:40) 이전으로 읽혀 「확정 불가」가 나갔다.
        #   전날 운행일로 옮겨 판정하면 운행일·요일형이 바뀌므로 여기서 하지 않는다 → 판정 안 함(no_data).
        #   운행일로 다시 물으려면 전날 날짜 + '28:05' 같은 운행일 표기로 준다.
        if now_min < r.first_min and now_min + MIN_DAY <= r.last_min:
            return LegResult(idx, label, "unknown",
                             f"{fmt_min(now_min)} 은 운행일 경계(04:00) 뒤지만 {nm} 의 전날 운행분이 아직 돈다 "
                             f"(막차 {fmt_min(r.last_min)}) — 이 운행일로는 판정하지 않는다",
                             grade="근거없음", code="no_data",
                             relief=f"전날 날짜로 {fmt_min(now_min + MIN_DAY)} 에 다시 판정",
                             warnings=warn, evidence=[self._ev_bus(r, "운행 구간")])
        if now_min < r.first_min:
            return LegResult(idx, label, "infeasible",
                             f"{fmt_min(now_min)} 은 {nm} 첫차({fmt_min(r.first_min)}) 이전이다",
                             grade="확정", code="before_first", relief=f"출발을 {fmt_min(r.first_min)} 로 미루면 성립",
                             warnings=warn, evidence=[self._ev_bus(r, "운행 구간")])
        # ☆`[2026-09-29 문제목록 #3]` 막차 시각은 **기점 출발**이다. 앞 판은 중간 정류장에서도 기점 막차로 거절했다 —
        #   23:00 기점 막차가 23:20 에 지나는 정류장에서 23:10 요청을 「막차 이후」로 막았다. 막차의 그 정류장 통과 추정
        #   (board_caps — 아래 승차 상한과 같은 값)이 있으면 그것과 비교한다. 추정을 못 내면 기점 막차 그대로(보수적) + 경고.
        last_pass = r.last_min
        if now_min > r.last_min:
            stops0 = self.bus.stops[r.route_id]
            if a["seq"] != stops0[0]["seq"]:
                md0 = self.rv("bus", "구간_프로파일", "min_days") if self.bus_prof is not None else None
                # ☆`[73 후속 · v0.9.2]` 비교 시각은 **빠른 쪽**(p10 이하 · last_pass_early). 팀장 판은 worst 에 p90(늦은 쪽)을
                #   썼다 — 「아직 탈 수 있나」는 버스가 일찍 지나갈 위험이 핵심이라 늦은 추정은 낙관이다. best·worst 같은 값.
                cap0 = last_pass_early(self.bus_prof, r.route_id, stops0, a["seq"], r.last_min,
                                       self._bus_day_types(day_type), md0)
                if cap0 is not None and cap0 > r.last_min:
                    last_pass = cap0
                    ev.append(self._ev_bus_prof(f"{nm} 막차 {fmt_min(r.last_min)} 기점 → {a['station_nm']} 통과 추정 "
                                                f"{fmt_min(cap0)}(빠른 쪽 · min(p10, p50) 누적) — 요청 {fmt_min(now_min)} 과 비교"))
                elif cap0 is None:
                    warn.append(self.warn_msg("MOB_W_BUS_LAST_PASS_UNCHECKED", route=nm, stop=a["station_nm"]))
        if now_min > last_pass:
            roll = self._rollover_relief(now_min, r.first_min)
            if roll:
                wall, gap = roll
                return LegResult(
                    idx, label, "infeasible",
                    f"{fmt_wall(now_min)} 은 {nm} 첫차({fmt_min(r.first_min)}) 이전이다 "
                    f"(전날 막차 {fmt_min(r.last_min)} 는 이미 지났다)",
                    grade="확정", code="before_first", relief=f"{gap}분 뒤 첫차 {fmt_min(r.first_min)} 를 기다리면 성립",
                    warnings=warn, evidence=[self._ev_bus(r, "운행 구간")])
            passed = (f" — 기점 출발 막차가 {a['station_nm']} 를 지나는 추정 시각(빠른 쪽) {fmt_min(last_pass)} 도 지났다"
                      if last_pass > r.last_min else "")
            # GPT 대조(78 Q2) — 중간 정류장에서의 막차 이후는 통과 추정(또는 기점 막차로 보수 대체)에 기댄 모델 판단이다.
            #   「막차가 이미 지났다」는 확정 사실처럼 내지 않는다 — 기점 승차만 확정.
            origin_board = a["seq"] == self.bus.stops[r.route_id][0]["seq"]
            return LegResult(idx, label, "infeasible",
                             f"{fmt_min(now_min)} 은 {nm} 막차({fmt_min(r.last_min)}) 이후다{passed}",
                             grade="확정" if origin_board else "추정", code="after_last", relief="수단 교체(지하철·택시)", warnings=warn,
                             evidence=[self._ev_bus(r, "운행 구간")])

        # 2) 대기 — 추정이다. 막차 근처는 배차 전부로 잡는다(놓치면 되돌릴 수 없다)
        if not r.term_min:
            return LegResult(idx, label, "unknown",
                             f"{nm} 의 배차가 0이다 — 배차 0분이 아니라 배차 개념 없음(예약제·출퇴근 전용)",
                             grade="근거없음", code="no_data", warnings=warn,
                             evidence=[self._ev_rule("bus.배차_0", "근거없음")])
        near_last = now_min >= last_pass - self.rv("bus", "막차근처_기준_분")   # #3 그 정류장 통과 추정 기준
        # ★ v0.8 — worst 통과는 언제나 배차 전부(rules bus.worst_case). best 는 종전대로 절반(막차 근처는 전부).
        full = near_last or worst
        wait = r.term_min if full else math.ceil(r.term_min / 2)
        model = ("배차 전부(최악)" if worst else "배차 전부(막차 근처)") if full else "배차의 절반"
        ev.append(self._ev_bus(r, f"운행 {fmt_min(r.first_min)}~{fmt_min(r.last_min)} · 배차 {r.term_min}분"))
        ev.append(self._ev_rule("bus.wait_model" if not full else "bus.worst_case", "추정"))
        ev.append(self._ev_rule("bus.ride_model", "추정"))
        # ★ 왕복 노선에서 길 건너 짝을 놓치고 한 바퀴 도는 답이 나오는지 본다
        det = self.rv("bus", "우회_경고")
        if span > len(self.bus.stops[r.route_id]) * det["비율"]:
            alt = self.bus.shorter_pair(r.route_id, a, b, det["근접_m"])
            if alt and alt[2] < span:
                warn.append(self.warn_msg("MOB_W_BUS_DETOUR", span=span,
                                          alt_from=alt[0]["station_nm"],
                                          alt_to=alt[1]["station_nm"], alt_span=alt[2]))
                ev.append(self._ev_rule("bus.우회_경고", "추정"))

        ride = arrive = None
        sgrade = "근거없음"
        stops = self.bus.stops[r.route_id]
        dayf = self._bus_day_types(day_type)
        min_days = self.rv("bus", "구간_프로파일", "min_days") if self.bus_prof is not None else None
        # 2-1) ★ 41(v0.9 · 적용 GPT 1·2) 승차 시각 상한 — 막차 시각은 **기점 출발**이다. 대기 모델(배차 절반·전부)은
        #   정책상의 추정이지 「그때까지 못 탄다」는 근거가 아니다. 그래서 막차 추정으로 **새 불가를 만들지 않는다**
        #   (불가는 위 「요청 시각 > 기점 막차」 규칙만). 대기 모델상 승차가 막차 통과 추정 뒤로 밀리면 막차 통과 추정
        #   시각에 탄다고 본다 — 통과 추정 = 막차 + 기점→a 구간 누적(best p50 · worst p90 · 요일형이 갈리면 늦은 쪽).
        dep = self._bus_board(r, a, stops, now_min + wait, "p90" if worst else "p50", dayf, min_days, warn, ev)
        if dep < now_min + wait:
            wait, model = dep - now_min, f"막차 통과 추정 {fmt_min(dep)} — 대기 모델 {fmt_min(now_min + wait)} 대신"

        # 3) 승차 소요 — ★ 41(v0.9): 구간 통행시간 프로파일을 **구간 진입 시각대로** 누적한다(best p50 · worst p90).
        #    프로파일을 못 쓰는 구간(셀 날 수 < min_days · 구간 없음 · 양끝 ID 불일치)만 종전 모델(구간 거리 ÷ 표정속도).
        #    프로파일 파일이 아예 없으면 종전 모델 그대로 — worst 스프레드는 근거없음(MOB_W_WORST_NO_SPREAD).
        spd = {}

        def old_min(dist_m):
            if dist_m is None:
                return None
            if "v" not in spd:
                spd["v"] = self.bus_speed(r, day_type)
            speed = spd["v"][0]
            return dist_m / 1000 / speed * 60 if speed else None

        wk, src = None, None
        wb = dep_b = None                    # 84 — 최악값 통과의 예정 도착 추정(내릴 정류장 무정차 구간 검사)에 쓴다
        if self.bus_prof is not None:
            q = "p90" if worst else "p50"
            wk = self.bus_prof.walk(r.route_id, stops, a["seq"], b["seq"], dep, dayf, q, min_days, old_min)
        if wk is not None and wk.minutes is not None:
            m = wk.minutes
            wb, adj = None, False
            if worst:
                # ★ 적용 GPT 5 — 시간 칸이 바뀌면 worst(늦게 타서 한산한 칸)가 best 보다 먼저 도착할 수 있다.
                #   같은 요청 시각의 best 도착보다 이르지 않게 맞춘다(FIFO — 늦게 탄 차가 먼저 닿지 않는다).
                dep_b = self._bus_board(r, a, stops, now_min + (r.term_min if near_last else math.ceil(r.term_min / 2)),
                                        "p50", dayf, min_days, [], [])
                wb = self.bus_prof.walk(r.route_id, stops, a["seq"], b["seq"], dep_b, dayf, "p50", min_days, old_min)
                if wb.minutes is not None:
                    m, adj = worst_not_before_best(dep_b, wb.minutes, dep, m)
                    if adj:
                        ev.append(self._ev_rule("bus.구간_프로파일.시나리오_도착_역전_보정", "추정"))
            ride = round(m, 1)
            arrive = dep + math.ceil(ride)
            sgrade = "추정"
            src = "profile" if wk.complete else "profile_partial"
            if worst and wb is not None and wb.minutes is not None and adj:
                src = "profile_adjusted"        # 보정분이 섞였다 — p90_eta 에 쓰지 않는다(적용 2차 GPT 10)
            ev.append(self._ev_bus(r, f"{a['station_nm']}→{b['station_nm']} ({span}정거장)"))
            ev.append(self._ev_bus_prof(f"{nm} {a['station_nm']}→{b['station_nm']} 구간 {wk.used + wk.half}개 {q} 누적"
                                        f"(진입 시각대 · {fmt_min(dep)} 승차)" + (f" · 대체 {wk.fb}개" if wk.fb else "")))
            ev.append(self._ev_rule("bus.구간_프로파일", "추정"))
            if worst:
                ev.append(self._ev_rule("judgment.worst.bus_ride_spread", "추정" if wk.used + wk.half else "근거없음"))
            if wk.fb:
                warn.append(self.warn_msg("MOB_W_BUS_PROFILE_FALLBACK", route=nm, n=wk.fb, total=wk.total))
                warn += spd.get("v", (None, None, None, []))[3]
                if worst:
                    warn.append(self.warn_msg("MOB_W_WORST_NO_SPREAD", what=f"버스 {nm} 승차 중 대체 구간 {wk.fb}개"))
            if wk.edge and not any(w["code"] == "MOB_W_BUS_PROFILE_DAYTYPE_EDGE" for w in warn):
                warn.append(self.warn_msg("MOB_W_BUS_PROFILE_DAYTYPE_EDGE", route=nm))
        elif wk is not None:
            # 프로파일도 대체 모델도 못 쓴 구간이 있다 — 승차 소요 근거없음. 그 자리를 말한다(적용 GPT 6).
            fs = next((x for x in stops if x["seq"] == wk.fail_seq), None)
            nx = next((x for x in stops if x["seq"] > (wk.fail_seq or 0)), None)
            warn.append(self.warn_msg("MOB_W_BUS_DIST_MISSING", route=nm,
                                      from_stop=(fs or a)["station_nm"], to_stop=(nx or b)["station_nm"]))
            warn += spd.get("v", (None, None, None, []))[3]
            ev.append(self._ev_rule("bus.구간_프로파일 — 대체 실패", "근거없음"))
        else:
            if worst:
                # 프로파일 파일이 없다 — worst 승차 = best 승차. 지어내지 않는다.
                warn.append(self.warn_msg("MOB_W_WORST_NO_SPREAD", what=f"버스 {nm} 승차 소요"))
                ev.append(self._ev_rule("judgment.worst.bus_ride_spread", "근거없음"))
            dist = self.bus.distance_m(r.route_id, a["seq"], b["seq"])
            speed, basis, sgrade, swarn = self.bus_speed(r, day_type)
            warn += swarn
            if dist is None:
                warn.append(self.warn_msg("MOB_W_BUS_DIST_MISSING", route=nm,
                                          from_stop=a["station_nm"], to_stop=b["station_nm"]))
            elif speed:
                ride = ceil1(dist / 1000 / speed * 60)          # #2 올림
                arrive = dep + math.ceil(ride)
                src = "speed"
                ev.append(self._ev_bus(r, f"{a['station_nm']}→{b['station_nm']} {dist:,}m ({span}정거장)"))
                ev.append(self._ev_rule(f"bus.표정속도 — {basis} {speed} km/h", sgrade))
            else:
                ev.append(self._ev_rule("bus.표정속도 — 값 없음", "근거없음"))
        # 공항버스 요금 경고는 승차 소요 모델과 무관하다 — 종전엔 bus_speed(공항 대용) 안에서만 붙었다
        if r.route_type_nm == "공항" and not any(w["code"] == "MOB_W_AIRPORT_FARE" for w in warn):
            warn.append(self.warn_msg("MOB_W_AIRPORT_FARE"))
        # ☆GPT 84 #5 — 타는 정류장을 실제 승차 추정 시각까지로 한 번 더 본다(막차 통과 추정·시각표로 승차가 요청+배차
        #   밖으로 나가는 경우). 위(구간 찾은 직후)의 검사는 근거없음으로 빠지기 전에 무정차를 먼저 말하려는 것이다.
        d = self._stop_skip_at(nm, a, now_min, max(dep, now_min + (r.term_min or 0)))
        if d:
            return self._stop_skip_fail(idx, label, nm, a, "타는", d)
        # ☆84 — 내릴 정류장 무정차. 도착 시각을 모르면(승차 소요 근거없음) 승차 뒤 전부를 본다(보수적).
        # ☆GPT 84 #5 — 도착은 한 시점이 아니라 **예정~최악 사이**다. 최악값 통과에서는 [예정 도착 추정, 최악 도착] 전체를
        #   본다 — 예정 10:00 · 최악 10:20 사이에 10:05~10:15 무정차가 끼면 양 끝만 보면 통과한다. 예정 도착 추정 =
        #   best 승차 프로파일(wb)이 있으면 그것, 없으면 최악 도착에서 대기 차(배차 전부 − 절반)만 뺀 값.
        lo = arrive if arrive is not None else dep
        if worst and arrive is not None:
            best = None
            if wb is not None and dep_b is not None and wb.minutes is not None:
                best = dep_b + math.ceil(wb.minutes)
            if best is None:
                best = arrive - max(0, (r.term_min or 0) - math.ceil((r.term_min or 0) / 2))
            lo = min(lo, best)
        d = self._stop_skip_at(nm, b, lo, arrive)
        if d:
            return self._stop_skip_fail(idx, label, nm, b, "내리는", d)
        return LegResult(idx, label, "feasible",
                         f"{fmt_min(dep)} 승차 예상 (대기 {wait}분 — {model})"
                         + ("" if ride is not None else " · 승차 소요 근거없음")
                         + f" · {a['station_nm']}(seq {a['seq']}) → {b['station_nm']}(seq {b['seq']})",
                         grade="추정" if ride is not None else "근거없음",
                         depart_min=dep, arrive_min=arrive,
                         wait_min=wait, ride_min=ride, ride_grade=sgrade,
                         warnings=warn, evidence=ev, worst=worst,
                         ride_src=src if ride is not None else None)


    # ── 자전거(따릉이) — 시간표가 없다. 소요만 낸다 (규칙 v0.7 · 22번 방 · rules bike.ddareungi) ──
    def bike_enabled(self):
        b = (self.R.get("bike") or {}).get("ddareungi")
        return bool(b and b.get("enabled"))

    def _bike_point(self, x):
        """역명(station_coords) 또는 {lat,lng,name} → (lat, lng, 표시 이름). 못 찾으면 None."""
        if isinstance(x, dict):
            if x.get("lat") is None or x.get("lng") is None:
                return None
            return x["lat"], x["lng"], x.get("name") or f"{x['lat']:.4f},{x['lng']:.4f}"
        if self.sc:
            p = self.sc.by_name.get(x)                 # 55 ④ — 동명이역은 by_name 에 없다(조용히 한쪽을 집지 않는다)
            if p and p.get("lat") is not None:
                return p["lat"], p["lng"], f"{x}역"
        return None

    def _ambig_warn(self, station, what):
        """역명만 받은 자리에서 동명이역이면 경고 하나(55 ④ · MOB_W_STATION_AMBIGUOUS). 아니면 None."""
        if not isinstance(station, str) or not self.sc or not getattr(self.sc, "is_ambiguous", None) \
                or not self.sc.is_ambiguous(station):
            return None
        return self.warn_msg("MOB_W_STATION_AMBIGUOUS", station=station,
                             groups=" / ".join(self.sc.ambiguous_lines(station)), what=what)

    def _bike_walk(self, lat1, lng1, lat2, lng2):
        """대여소까지 도보 — 직선 × 우회계수(추정). (m, 분, 근거 dict, 직선 m). 보행망 거리는 없다(99 — 경로 서버 삭제)."""
        B = self.R["bike"]["ddareungi"]
        speed = self.R["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        straight = meters(lat1, lng1, lat2, lng2)
        r = (self.bike_router.route(B["ride"]["walk_profile"], lat1, lng1, lat2, lng2)
             if self.bike_router and self.bike_router.available() else None)
        if r:
            dist, basis = r["distance_m"], f"보행망 {r['basis']}"
            note = ""
            if r.get("optimistic"):                    # 길 밖 접근 구간이 길어 낙관적일 수 있다 — 직선×계수와 큰 쪽(2026-10-04)
                factor = B["station_walk_detour"]["value"]
                dist = max(dist, straight * factor)
                note = f" · ★길까지 {r.get('quality', {}).get('access_max_m', 0):.0f}m 떨어져 낙관 가능 — 직선×{factor:g} 과 큰 쪽"
            ev = {"source_type": "db", "source_id": r["source_id"], "grade": "추정", "observed_at": None,
                  "claim": f"도보 {dist:,.0f}m (직선 {straight:,.0f}m · {basis}{note})"}
        else:
            factor = B["station_walk_detour"]["value"]
            dist, basis = straight * factor, f"직선×{factor:g}"
            ev = self._ev_rule(f"bike.ddareungi.station_walk_detour — {basis}", "추정")
            ev["claim"] = f"도보 {dist:,.0f}m (직선 {straight:,.0f}m × {factor:g} — 보행망 없음)"
        return dist, math.ceil(dist / speed / 60), ev, straight

    def verify_leg_bike(self, idx, leg, now_min, day_type, party=None, live_fixture=None):
        """따릉이 구간. 17번 §4 표 그대로 —

        후보     출발·도착 각각 반경 station_walk_m 안 운영 대여소 ≥1. 없으면 **불가**(근거없음이 아니다 — 대여소 목록은 확정이다).
        가용     출발 대여소 bikeList 단건 조회 → parkingBikeTotCnt ≥ 1. 도착은 검사 안 함(거치대 초과 반납 가능).
                 응답은 값만 쓰고 버린다 — 이력엔 checked_at + 개수. 조회 못 하면 후보 유지 · 가용 근거없음 · 경고.
        제외     만 12세 이하 동반 → 불가(확정). 출발 대여소 LCD 전용 → 다음 대여소로(추정).
        요금     1h 1,000 … 초과 200원/5분. 승차 > overtime_warn_min → MOB_W_BIKE_OVERTIME.
        소요     ☆99 — **승차 소요는 내지 않는다**(자전거 경로 계산 없음 · 근거없음). 대여소 도보(직선×계수)·대여 3·반납 3 만 안다.
        시각     도착을 내지 않는다(예정 시각 없음 → 밖 판정은 불가 no_data · 이유에 「자전거 경로 계산 없음」).
                 살릴 때는 보행 경로 거리 ÷ 자전거 평균 속도(단위 환산)로(본인 10/4 · 보행 그래프 뒤) — 전용 경로 계산을 만들지 않는다.
        """
        party = party or {}
        B = self.R["bike"]["ddareungi"]
        a_raw, b_raw = leg["from"], leg["to"]
        a_nm = a_raw.get("name") if isinstance(a_raw, dict) else a_raw
        b_nm = b_raw.get("name") if isinstance(b_raw, dict) else b_raw
        label = f"자전거 {a_nm}→{b_nm}"
        if not self.bike_enabled():
            return LegResult(idx, label, "unknown", "규칙 bike.ddareungi 가 꺼져 있다", grade="근거없음", code="no_data")
        if self.bk is None:
            return LegResult(idx, label, "unknown", "따릉이 대여소 목록(bike_stations_v1.jsonl)을 읽지 못했다",
                             grade="근거없음", code="no_data")
        ev, warn = [], []

        # 0) 연령 제외 — 운영사 규칙(확정)
        why = bike_party_excluded(party, B)
        if why:
            return LegResult(idx, label, "infeasible", why, grade="확정", code="mode_unavailable",
                             relief="자전거 대신 다른 수단. 만 13세 이상은 회원가입 후 이용",
                             evidence=[self._ev_rule("bike.ddareungi.exclude.age_max_excluded", "확정")])

        # 1) 양끝 좌표
        pa, pb = self._bike_point(a_raw), self._bike_point(b_raw)
        if pa is None or pb is None:
            miss = a_nm if pa is None else b_nm
            amb = self._ambig_warn(miss, "자전거 끝점 좌표")
            if amb:
                return LegResult(idx, label, "unknown", f"'{miss}' 은(는) 동명이역 — 역명만으로는 대여소를 찾지 않는다",
                                 grade="근거없음", code="no_data", relief="장소 좌표나 노선을 같이 준다", warnings=[amb])
            return LegResult(idx, label, "unknown", f"'{miss}' 의 좌표가 없다 — 대여소를 찾을 수 없다", grade="근거없음", code="no_data")
        radius = B["station_walk_m"]["value"]
        st_src = {"source_type": "db", "source_id": self.bk.source_id, "grade": "확정",
                  "observed_at": self.bk.checked_at}
        A = self.bk.stations_near(pa[0], pa[1], radius)
        Bs = self.bk.stations_near(pb[0], pb[1], radius)
        for lst, where in ((A, pa[2]), (Bs, pb[2])):
            if not lst:
                return LegResult(idx, label, "infeasible",
                                 f"{where} 반경 {radius}m 안에 운영 중인 따릉이 대여소가 없다", grade="확정", code="mode_unavailable",
                                 relief="반경 밖 대여소까지 걷거나 다른 수단",
                                 warnings=[self.warn_msg("MOB_W_BIKE_NO_STATION", where=where, radius_m=radius)],
                                 evidence=[dict(st_src, claim=f"{where} 반경 {radius}m 운영 대여소 0곳 "
                                                              f"(목록 {len(self.bk.rows):,}곳 · {self.bk.checked_at})"),
                                           self._ev_rule("bike.ddareungi.station_walk_m", "추정")])
        ev.append(self._ev_rule("bike.ddareungi.station_walk_m", "추정"))

        # 2) 출발 대여소 — 가까운 순으로 candidate_stations 곳. LCD 전용은 건너뛰고, 실시간 거치 ≥1 이어야 한다
        live = (BikeLive.from_fixture(live_fixture) if live_fixture else None) or self.bike_live
        pick, avail, avail_grade = None, None, "근거없음"
        lcd_skipped, empty = [], []
        mode_grade = "확정"
        checked = 0                                   # 실시간 조회(호출 예산)에 드는 대여소만 센다 — LCD 건너뜀은 호출이 아니다
        for d, st in A:
            qr = BikeStations.qr_ok(st)
            if qr is False and B["exclude"]["lcd_origin"]["value"]:
                lcd_skipped.append(st)
                warn.append(self.warn_msg("MOB_W_BIKE_LCD_SKIPPED", station=st["name"]))
                continue
            if checked >= B["candidate_stations"]["value"]:
                break
            checked += 1
            L = live.get(st["stationId"]) if live else None
            # ☆`[2026-09-29 문제목록 #9]` 일행 인원만큼 있어야 한다 — 앞 판은 1대만 보고 4인 일행을 태웠다.
            need = max(1, int((party or {}).get("size") or 1))
            if L is not None and L["available"] < need:
                empty.append((st, L))
                ev.append({"source_type": "db", "source_id": L["source_id"], "grade": "확정",
                           "observed_at": L["checked_at"],
                           "claim": f"{st['name']}({st['stationId']}) 거치 {L['available']}대 — 일행 {need}명이 "
                                    f"빌릴 수 없어 건너뜀"})
                continue
            pick, avail = (d, st), L
            if qr is None:
                warn.append(self.warn_msg("MOB_W_BIKE_MODE_UNKNOWN", station=st["name"]))
                mode_grade = "추정"
            elif st.get("mode") != "QR":
                mode_grade = "추정"                    # 'LCD,QR' 혼합 — xlsx 속성이라 추정
            break
        if lcd_skipped:
            ev.append(self._ev_rule("bike.ddareungi.exclude.lcd_origin", "추정"))
        if pick is None:
            if empty:
                st, L = empty[0]
                return LegResult(idx, label, "infeasible",
                                 f"{pa[2]} 근처 대여소 {len(empty)}곳이 조회 시각({L['checked_at']}) 거치 대수가 "
                                 f"일행 {max(1, int((party or {}).get('size') or 1))}명보다 적다",
                                 grade="확정", code="mode_unavailable", relief="몇 분 뒤 다시 조회하거나 다른 수단", warnings=warn, evidence=ev,
                                 dropped={"거치0": len(empty), "LCD전용": len(lcd_skipped)})
            return LegResult(idx, label, "infeasible",
                             f"{pa[2]} 반경 {radius}m 안 대여소 {len(lcd_skipped)}곳이 전부 LCD 전용이다 — "
                             f"외국인 비회원(QR)은 빌릴 수 없다", grade="추정", code="mode_unavailable",
                             relief="회원가입(만 13세 이상) 또는 다른 수단", warnings=warn, evidence=ev,
                             dropped={"LCD전용": len(lcd_skipped)})
        da, sa = pick
        db, sb = Bs[0]
        ev.append(dict(st_src, claim=f"출발 대여소 {sa['name']}({sa['stationId']} · {sa.get('mode') or '운영방식 미상'} · "
                                     f"거치대 {sa.get('rack')}) 직선 {da:,.0f}m · 도착 대여소 {sb['name']}({sb['stationId']}) "
                                     f"직선 {db:,.0f}m"))
        if avail is not None:
            avail_grade = "확정"
            ev.append({"source_type": "db", "source_id": avail["source_id"], "grade": "확정",
                       "observed_at": avail["checked_at"],
                       "claim": f"{sa['name']} 거치 {avail['available']}대 (조회 시각의 값 · 저장 안 함)"})
        else:
            why = "실시간 조회 없음" if live is None else "응답 없음"
            warn.append(self.warn_msg("MOB_W_BIKE_LIVE_UNKNOWN", station=sa["name"], why=why))
            ev.append(self._ev_rule(f"bike.ddareungi.live_check — {why}", "근거없음"))
        ev.append(self._ev_rule("bike.ddareungi.live_check", "확정"))

        # 3) 소요 = 도보 + 대여 + 승차 + 반납 + 도보
        wdist_in, wmin_in, wev_in, _ = self._bike_walk(pa[0], pa[1], sa["lat"], sa["lon"])
        wdist_out, wmin_out, wev_out, _ = self._bike_walk(sb["lat"], sb["lon"], pb[0], pb[1])
        ev += [wev_in, wev_out]
        rent = B["rent_return_min"]["value"]
        ev.append(self._ev_rule("bike.ddareungi.rent_return_min", "근거없음"))
        # ☆101(2026-10-05 · 합치기) 승차 소요 — 팀장 판대로 길찾기(BikeRouter · 로컬 도로 그래프 bike 프로파일)가 있으면 낸다(추정).
        #   길찾기가 없으면(시험·명령줄 기본 · 도로 그래프 없음) 99 때처럼 근거없음 — 숫자를 지어내지 않고 다른 수단으로 바꾸지도 않는다.
        ride = ride_grade = None
        r = (self.bike_router.route(B["ride"]["profile"], sa["lat"], sa["lon"], sb["lat"], sb["lon"])
             if self.bike_router and self.bike_router.available() else None)
        if r:
            ride = ceil1(r["time_s"] / 60)                  # #2 올림
            ride_grade = "추정"
            ev.append({"source_type": "db", "source_id": r["source_id"], "grade": "추정", "observed_at": None,
                       "claim": f"{sa['name']} → {sb['name']} bike 프로파일 {r['distance_m']/1000:.2f}km · "
                                f"{ride:g}분 ({r['basis']})"})
            ev.append(self._ev_rule("bike.ddareungi.ride", "추정"))
        else:
            ride_grade = "근거없음"
            ev.append(self._ev_rule(f"bike.ddareungi.ride — {BIKE_NO_ROUTE}(길찾기·픽스처 없음) → 승차 소요 없음", "근거없음"))

        # 4) 요금 · 초과 경고 · 외국인 안내
        fare_txt = ""
        if ride is not None:
            f = bike_fare(B["fare"], ride)
            fare_txt = f" · 이용권 {f['pass']} {f['pass_won']:,}원" + (
                f" + 초과 {f['overtime_min']}분 {f['overtime_won']:,}원" if f["overtime_won"] else "")
            ev.append(self._ev_rule("bike.ddareungi.fare.passes", "확정"))
            if ride > B["fare"]["overtime_warn_min"]["value"]:
                warn.append(self.warn_msg("MOB_W_BIKE_OVERTIME", ride_min=f"{ride:g}",
                                          warn_min=B["fare"]["overtime_warn_min"]["value"]))
                ev.append(self._ev_rule("bike.ddareungi.fare.overtime_warn_min", "추정"))
        # ☆`[73 후속 · v0.9.2 · 본인 9/29]` 따릉이 외국인 이용 안내(MOB_W_BIKE_FOREIGNER_GUIDE)를 붙이지 않는다 — 자전거는
        #   요청했을 때만 싣고 자전거 따로 안내는 하지 않는다. 앞 판은 설문이 없으면 foreign 기본 True 로 내국인에게도 붙였다.

        total = wmin_in + rent + (math.ceil(ride) if ride is not None else 0) + rent + wmin_out
        arrive = now_min + total if ride is not None else None
        grade = worst_grade("확정", mode_grade, avail_grade, ride_grade)
        reason = (f"대여소 {sa['name']}(도보 {wmin_in}분) → {sb['name']}(도보 {wmin_out}분)"
                  + (f" · 승차 {ride:g}분" if ride is not None else f" · {BIKE_NO_ROUTE}(승차 소요 근거없음)")
                  + f" · 대여·반납 {rent * 2}분"
                  + (f" · 거치 {avail['available']}대" if avail else " · 거치 미상")
                  + fare_txt)
        res = LegResult(idx, label, "feasible", reason, grade=grade, depart_min=now_min, arrive_min=arrive,
                        wait_min=0, ride_min=ride, ride_grade=ride_grade, warnings=warn, evidence=ev,
                        dropped={k: v for k, v in (("LCD전용", len(lcd_skipped)), ("거치0", len(empty))) if v})
        res.walk_min = wmin_in + wmin_out
        return res

    def _next_day_first(self, line, a, b, day_type, first):
        """다음 운행일의 (요일형, 그 방향 첫 출발). 요일형이 같으면 오늘 값 그대로(#4).
        다음 날 시간표에 목적지까지 가는 열차가 없으면 (요일형, None) — 「첫차를 기다리면」을 만들지 않는다."""
        if self._case_date is None:
            return day_type, first
        nd = day_type_of(self._case_date + _timedelta(days=1), self.holidays)
        if nd == day_type:
            return day_type, first
        c2, _drop, _o = self.candidates(line, a, b, nd)
        return nd, (c2[0][0].min if c2 else None)

    def _rollover_relief(self, now_min, first_min):
        """운행일 연장 시각(24 시 이상)이 실은 **그날 아침 첫차 이전**인 경우.

        03:50 요청은 to_service_min 이 27:50 으로 올린다 — 전날 운행분의 연장이라 맞다.
        그런데 그 값만 보면 '막차(22:20) 이후'가 되어 '수단 교체' 라는 답이 나간다.
        실제로는 20분 뒤 04:10 첫차를 타면 된다. 벽시계로 되돌려 첫차와 비교한다.

        ★ 단, 첫차까지 남은 시간이 공백 상한을 넘으면 이 틀을 쓰지 않는다.
          24:50 요청(막차 24:46 을 4분 놓친 것)에 "286분 뒤 첫차를 기다리면 성립" 은
          맞는 말이지만 쓸모없는 답이고, **막차 경과 신호를 지워서** 대안 열거(F3)의 입구를 막는다.
          그 경우는 '막차 이후 → 수단 교체' 가 맞다.
        """
        if now_min < MIN_DAY or first_min is None:
            return None
        wall = now_min - MIN_DAY
        gap = first_min - wall
        if 0 < gap <= self.rv("service_window", "gap_max_min"):
            return wall, gap
        return None


    def bus_speed(self, r, day_type):
        """노선의 표정속도(km/h). 값_우선순위: 노선별 실측 > 노선유형별 통계 > 근거없음.

        ★ 순환 통계값(21.3)은 남산 01A·01B 에 쓰지 않는다 — 실측 15.3/17.8 과 -28%/-16% 어긋난다.
          통계값을 쓰면 충무로→남산서울타워(4.66km)가 13.1분으로 나오는데 실측으로는 18.3분이다.
          규칙 bus.표정속도.통계_금지_노선. 노선별 값이 들어오면 자동으로 풀린다.
        """
        S = self.R["bus"]["표정속도"]
        per = (S.get("노선별", {}).get("value") or {}).get(r.route_nm)
        pw = []
        if isinstance(per, dict):
            # ☆`[2026-09-29 문제목록 #15]` 휴일 값이 없으면 평일 값으로 **대신한다는 것을 드러낸다**(조용히 바꾸지 않는다)
            if per.get(day_type) is None and day_type != "weekday" and per.get("weekday"):
                pw.append(self.warn_msg("MOB_W_BUS_SPEED_WEEKDAY_FOR_HOLIDAY", route=r.route_nm, day_type=day_type))
            per = per.get(day_type) or per.get("weekday")
        if per:
            return per, "노선별 실측" + (" (평일 값으로 대신)" if pw else ""), "추정", pw
        if r.route_nm in (S.get("통계_금지_노선", {}).get("value") or []):
            return None, None, "근거없음", [
                self.warn_msg("MOB_W_BUS_SPEED_BLOCKED_ROUTE", route=r.route_nm)]
        warn, t = [], r.route_type_nm
        if t == "심야":
            t = S["대용_심야"]["value"]
            warn.append(self.warn_msg("MOB_W_BUS_SPEED_PROXY_NIGHT", route=r.route_nm, proxy=t))
        elif t == "공항":
            t = S["대용_공항"]["value"]
            warn.append(self.warn_msg("MOB_W_BUS_SPEED_PROXY_AIRPORT", route=r.route_nm, proxy=t))
            warn.append(self.warn_msg("MOB_W_AIRPORT_FARE"))
        key = "holiday" if day_type == "holiday" else "weekday"
        v = (S["value"].get(key) or {}).get(t)
        if not v:
            return None, None, "근거없음", [
                self.warn_msg("MOB_W_BUS_SPEED_MISSING", route_type=r.route_type_nm)]
        warn.append(self.warn_msg("MOB_W_SPEED_NO_PEAK"))
        return v, f"{t} 유형 통계({key})", "추정", warn

    def _ev_bus(self, r, claim):
        return {"source_type": "db", "source_id": self.bus.source_id, "grade": "확정",
                "observed_at": self.bus.fetched_at, "claim": f"{r.route_nm}({r.route_type_nm}): {claim}"}

    def _ev_bus_prof(self, claim):
        p = self.bus_prof
        return {"source_type": "db", "source_id": p.source_id, "grade": "추정",
                "observed_at": self._iso8((p.dates or [None, None])[-1]), "claim": claim}

    @staticmethod
    def _iso8(v):
        """20260913 → '2026-09-13' — 프로파일 메타의 날짜는 정수다. 근거의 observed_at 은 ISO 문자열이다."""
        v = str(v) if v is not None else None
        return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if v and len(v) == 8 and v.isdigit() else v

    def _bus_day_types(self, day_type):
        """구간 프로파일에서 볼 요일형 — 41(v0.9). 운행일 **자정(24:00) 이후** 연장 구간(1440 ≤ 분 < 2880)은 달력상
        다음 날이라 원천 날짜 기준(달력일/운행일 · 41 GPT 9 보류)에 따라 칸이 달라진다. 두 날 요일형이 다르면 둘 다 본다
        (고르는 법은 부른 쪽 — 소요·통과 추정 모두 늦은 쪽). 04:00 경계는 요청 시각을 운행일로 올리는 규칙(timeutil)이고
        여기서는 쓰지 않는다 — 28:10 처럼 04시를 넘긴 연장 구간도 달력상 다음 날이다(적용 GPT 8).
        48:00(2880) 이상은 판정기가 만들지 않는다(가장 늦은 막차 N61 28:10 + 승차) — 들어오면 같은 규칙으로 본다."""
        nxt = None
        if self._case_date is not None:
            nxt = day_type_of(self._case_date + _timedelta(days=1), self.holidays)

        def f(t):
            if t >= MIN_DAY and nxt is not None and nxt != day_type:
                return (day_type, nxt)
            return (day_type,)
        return f

    def _bus_board(self, r, a, stops, dep_model, q, dayf, min_days, warn, ev):
        """승차 시각 — 대기 모델 시각과 막차 통과 추정 중 이른 쪽(41 v0.9 · 적용 GPT 1·2 · 2차 3·7). 새 불가는 만들지 않는다.
        통과 추정은 bus_profile.board_caps — best(q=p50)는 p50 누적, worst(q=p90)는 max(p90, p50) 누적(상한 역전 방지).
        기점 승차는 막차 시각 그대로(시간표 확정 · 프로파일 없어도). 중간 정류장에서 사슬이 온전하지 않거나 프로파일이 없으면
        상한을 두지 않고 MOB_W_BUS_LAST_PASS_UNCHECKED — 그 승차 시각도 막차를 확인한 값이 아니다."""
        if dep_model <= r.last_min:
            return dep_model
        cb, cw = board_caps(self.bus_prof, r.route_id, stops, a["seq"], r.last_min, dayf, min_days)
        if cb is None:
            warn.append(self.warn_msg("MOB_W_BUS_LAST_PASS_UNCHECKED", route=r.route_nm, stop=a["station_nm"]))
            return dep_model
        origin = a["seq"] == stops[0]["seq"]
        if not origin and self.bus_prof is not None:
            f = dayf(r.last_min)
            if len(f) > 1 and not any(w["code"] == "MOB_W_BUS_PROFILE_DAYTYPE_EDGE" for w in warn):
                warn.append(self.warn_msg("MOB_W_BUS_PROFILE_DAYTYPE_EDGE", route=r.route_nm))
        cap = cw if q == "p90" else cb
        if dep_model <= cap:
            return dep_model
        warn.append(self.warn_msg("MOB_W_BUS_BOARD_CAPPED", route=r.route_nm, stop=a["station_nm"],
                                  model=fmt_min(dep_model), cap=fmt_min(cap), last=fmt_min(r.last_min)))
        if origin:
            ev.append(self._ev_bus(r, f"막차 {fmt_min(r.last_min)} 기점 출발 — {a['station_nm']}(기점) 승차 상한"))
        else:
            ev.append(self._ev_bus_prof(f"{r.route_nm} 막차 {fmt_min(r.last_min)} 기점 → {a['station_nm']} 통과 추정 "
                                        f"{fmt_min(cap)} ({'max(p90, p50)' if q == 'p90' else 'p50'} 누적) — "
                                        f"대기 모델 승차 {fmt_min(dep_model)} 대신"))
        ev.append(self._ev_rule("bus.구간_프로파일.막차_통과", "확정" if origin else "추정"))
        return cap


    # ── 대안 열거(F3) ──────────────────────────────────────────────────
    def _phys_lines(self, rec):
        """좌표 레코드의 물리적 역 노선군(55 ④). 동명이역이 아니면 제한 없음(모든 노선)."""
        if self.sc and getattr(self.sc, "is_ambiguous", None) and self.sc.is_ambiguous(rec["station_nm"]):
            return set(self.sc.group_lines(rec))
        return self.lines_with(rec["station_nm"])

    def _other_station(self, station, line_a, line_b):
        """(역명, 노선 a) 와 (역명, 노선 b) 가 이름만 같은 다른 역인가(55 GPT #1). 좌표표가 있으면 물리적 역 묶음으로,
        없으면 규칙 station_names.환승_제외_역명 으로 본다."""
        if line_a == line_b:
            return False
        if self.sc and getattr(self.sc, "is_ambiguous", None) and self.sc.is_ambiguous(station):
            ra, rb = self.sc.by_key.get(f"{line_a}|{station}"), self.sc.by_key.get(f"{line_b}|{station}")
            if ra and rb:
                return self.sc.group_lines(ra) != self.sc.group_lines(rb)
        return station in self.R["station_names"]["환승_제외_역명"]["value"]

    def _lines_at(self, station, line):
        """(노선, 역)과 같은 물리적 역에 있는 노선들(55 ④). 동명이역이 아니면 lines_with 그대로."""
        if self.sc and getattr(self.sc, "is_ambiguous", None) and self.sc.is_ambiguous(station):
            rec = self.sc.by_key.get(f"{line}|{station}")
            return self.lines_with(station) & (set(self.sc.group_lines(rec)) if rec else {line})
        return self.lines_with(station)

    def lines_with(self, station):
        """그 역이 있는 노선들. 노선 교체 후보를 만들 때 쓴다."""
        if self._lines_of is None:
            self._lines_of = collections.defaultdict(set)
            for ln, L in self.lo.doc["lines"].items():
                for st in L["stations"]:
                    self._lines_of[st["station_nm"]].add(ln)
        return self._lines_of.get(station, set())

    def alternatives(self, case, idx, leg, now_min, day_type, is_sat, failed, worst=False):
        """불가·탈락 구간의 대안을 **규칙 순서로 열거하고 같은 검증기에 재통과**시킨다.

        LLM 을 쓰지 않는다 — 후보 공간이 노선·수단·시각·포기 넷으로 닫혀 있고,
        규칙으로 만든 후보는 시간표로 즉시 검증된다(rules alternatives.LLM_없음).

        ★ 순위를 매기지 않는다. 총소요 등급이 추정인데 두 안의 차이가 배차 불확실성보다 작으면
          **추정값으로 매긴 순위는 그 자체가 추정**이다(rules alternatives.순위_미부여).
        """
        maxn = self.rv("alternatives", "최대_제시")
        radius = self.rv("alternatives", "정류장_반경_m")
        party = case.get("party", {}) or {}
        wlim = self._walk_limit(party)
        line, a, b = leg.get("line"), leg["from"], leg["to"]
        is_bus = leg.get("mode") == "bus"
        is_bike_leg = leg.get("mode") == "bike"       # 자전거 구간의 대안은 택시만(모르는 것) — 자기_자신_제외
        out, tried = [], []
        excluded = self.rv("bus", "route_type_제외") or []

        speed = self.R["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        walk_min = lambda m: math.ceil(m / speed / 60) if m else 0

        def take(axis, label, newleg, at=None, walk_in=0, walk_out=0, note=None):
            """★ 접근·이탈 도보를 시각에 **반드시 넣는다**.

            라벨에 '도보 157m' 라고 적어 놓고 계산에서 빼면 도착이 낙관적으로 나온다 —
            2026-09-10 에 실제로 그렇게 냈다(N73 대안이 6분 이르게 나왔다).
            판정 경로와 소요 경로가 달랐던 것과 같은 실수다.
            """
            t = (at if at is not None else now_min) + walk_min(walk_in)
            # v0.8 — 최악값 통과에서 막힌 구간의 대안은 **같은 최악 가정**으로 판정한다(worst=True). 아니면 대안이 낙관이다.
            r = (self.verify_leg_bus(idx, newleg, t, day_type, worst=worst)
                 if newleg.get("mode") == "bus"
                 else self.verify_leg_bike(idx, newleg, t, day_type, party, case.get("bike_live"))
                 if newleg.get("mode") == "bike"
                 else self.verify_leg(idx, newleg, t, day_type, is_sat, worst=worst, party=party))
            if newleg.get("mode") == "bike" and r.verdict == "feasible" and r.arrive_min is None:
                # 99(GPT #1) — 도착을 못 내는 자전거(자전거 경로 계산 없음)는 **성립 대안이 아니다**. 본 경로가 이 모양을
                #   판단불가(no_data)로 올리는 것과 같게, 대안에서도 근거없음으로 적고 대안 목록(최대_제시 자리)에 넣지 않는다.
                tried.append((axis, f"{label} — {BIKE_NO_ROUTE}(승차 소요 근거없음)", "unknown"))
                return r
            tried.append((axis, label, r.verdict))
            if r.verdict == "feasible":
                arr = r.arrive_min + walk_min(walk_out) if r.arrive_min is not None else None
                out.append({"axis": axis, "label": label, "leg": newleg, "mode": leg_mode(newleg),
                            "depart_min": r.depart_min, "arrive_min": arr,
                            "walk_in_min": walk_min(walk_in), "walk_out_min": walk_min(walk_out),
                            "grade": r.grade, "reason": r.reason, "note": note,
                            "warnings": r.warnings, "at": t})
            return r

        # ★ 버스 구간의 대안 (규칙 v0.4 · 19번 방). v0.3.1 까지는 세 축이 전부 `line`(지하철) 조건이라
        #   버스 구간이 불가이면 택시 하나만 남았다(MIX-03). 요청한 두 정류장 행을 좌표로 쓴다.
        #   ⓐ 출발시각_이동은 버스에 없다 — 정류장별 시각표가 없어 '다음 차'를 특정할 수 없고,
        #      첫차 이전·막차 이후는 완화 조건이 이미 시각을 말한다.
        bx = by = None
        if is_bus:
            bx, by = self._bus_stop_row(leg, "from"), self._bus_stop_row(leg, "to")
            if bx is None or by is None or bx.get("lat") is None or by.get("lat") is None:
                tried.append(("노선교체", f"버스 {leg.get('route')} 정류장 좌표 없음 — 후보를 만들 수 없다", "unknown"))
                bx = by = None

        for axis in self.rv("alternatives", "후보축_순서"):
            if len(out) >= maxn:
                break

            # ⓑ' 버스 노선 교체 — 같은 두 정류장(동일 반경 안)을 잇는 다른 노선
            if axis == "노선교체" and is_bus and bx is not None:
                same_r = self.rv("alternatives", "정류장_동일_반경_m")
                for r, x, y, span, da, db in self.bus.routes_between(
                        bx["lat"], bx["lng"], by["lat"], by["lng"], same_r):
                    if len(out) >= maxn:
                        break
                    if r.route_nm == str(leg["route"]):          # 자기_자신_제외
                        continue
                    if r.route_type_nm in excluded:
                        continue
                    take(axis,
                         f"버스 {r.route_nm}({r.route_type_nm}) 로 교체 — "
                         f"{x['station_nm']} → {y['station_nm']} {span}정거장"
                         + (f" (정류장까지 {da}m · 하차 후 {db}m)" if da or db else ""),
                         {"mode": "bus", "route": r.route_nm,
                          "from": x["station_nm"], "to": y["station_nm"]},
                         walk_in=da, walk_out=db)

            # ⓒ' 버스 → 지하철 수단 교체 — 정류장 근처 역끼리 한 노선으로 이어지는가
            elif axis == "수단교체" and is_bus and bx is not None and self.sc:
                A = self.sc.stations_near(bx["lat"], bx["lng"], radius)
                B = self.sc.stations_near(by["lat"], by["lng"], radius)
                pairs = []
                for da, sa in A:
                    for db, sb in B:
                        if sa["station_nm"] == sb["station_nm"]:
                            continue
                        # 접근·이탈 도보는 역 좌표가 아니라 **가장 가까운 출구**까지로 잰다(있을 때)
                        # 55 ④ — 동명이역(신촌)은 레코드의 노선으로 물리적 역을 골라 그 출구만 본다
                        ea = self.ex.nearest(sa["station_nm"], bx["lat"], bx["lng"], sa.get("line")) if self.ex else None
                        eb = self.ex.nearest(sb["station_nm"], by["lat"], by["lng"], sb.get("line")) if self.ex else None
                        da2 = round(ea[0]) if ea else round(da)
                        db2 = round(eb[0]) if eb else round(db)
                        # 55 ④ — 노선은 그 물리적 역의 노선군 안에서만(신촌 2호선 근처 정류장에 경의선을 붙이지 않는다)
                        la = self.lines_with(sa["station_nm"]) & self._phys_lines(sa)
                        lb = self.lines_with(sb["station_nm"]) & self._phys_lines(sb)
                        for ln in sorted(la & lb):
                            pairs.append((da2 + db2, ln, sa["station_nm"], sb["station_nm"], da2, db2))
                for _tot, ln, sa, sb, da, db in sorted(pairs):
                    if len(out) >= maxn:
                        break
                    if max(da, db) > wlim:
                        tried.append((axis, f"{ln} {sa}→{sb} (도보 {max(da, db)}m > 상한 {wlim}m)",
                                      "rejected_by_limit"))
                        continue
                    take(axis,
                         f"{ln} {sa}→{sb} 로 교체 — {sa}역까지 도보 {da}m({walk_min(da)}분) · "
                         f"하차 후 {db}m({walk_min(db)}분)",
                         {"line": ln, "from": sa, "to": sb}, walk_in=da, walk_out=db)

            # ⓐ 출발 시각 이동 — 같은 노선을 다시 쓸 수 있는 유일한 축
            elif axis == "출발시각_이동" and line:
                cands, _, _ = self.candidates(line, a, b, day_type)
                nxt = next((d for d, _v, _f in cands if d.min > now_min), None)
                if nxt:
                    take(axis, f"{fmt_min(nxt.min)} 출발로 미룸", leg, at=nxt.min)

            # ⓑ 노선 교체 — 같은 두 역을 잇는 다른 노선
            elif axis == "노선교체" and line:
                # 55 ④ — 동명이역(양평·신촌)은 같은 물리적 역의 노선만(경의선 양평→공덕을 5호선 양평→공덕으로 바꾸지 않는다)
                for ln in sorted(self._lines_at(a, line) & self._lines_at(b, line)):
                    if ln == line or len(out) >= maxn:      # 자기_자신_제외
                        continue
                    take(axis, f"{ln} 로 교체", {"line": ln, "from": a, "to": b})

            # ⓒ 수단 교체 — 역 앞 정류장에서 한 노선으로 이어지는 버스
            elif axis == "수단교체" and self.bus and self.sc and line:
                pa, pb = self.sc.get(line, a), self.sc.get(line, b)
                if pa and pb:
                    for r, x, y, span, da, db in self.bus.routes_between(
                            pa["lat"], pa["lng"], pb["lat"], pb["lng"], radius):
                        if len(out) >= maxn:
                            break
                        if r.route_type_nm in excluded:
                            continue
                        # 도보 상한을 넘는 후보는 내지 않는다 — 성립해도 이 일행이 못 걷는다
                        if max(da, db) > wlim:
                            tried.append((axis, f"버스 {r.route_nm} (도보 {max(da, db)}m > 상한 {wlim}m)",
                                          "rejected_by_limit"))
                            continue
                        take(axis,
                             f"버스 {r.route_nm}({r.route_type_nm}) — "
                             f"{x['station_nm']}까지 도보 {da}m({walk_min(da)}분) · {span}정거장 · "
                             f"하차 후 {db}m({walk_min(db)}분)",
                             {"mode": "bus", "route": r.route_nm,
                              "from": x["station_nm"], "to": y["station_nm"]},
                             walk_in=da, walk_out=db)

            # ⓓ 자전거(따릉이) — 수단교체 축의 마지막 후보(규칙 v0.7 · 22번 방 · bike.ddareungi).
            #   지하철 구간은 역 좌표, 버스 구간은 요청한 두 정류장 좌표에서 반경 안 대여소를 찾는다.
            #   대여소까지 도보는 verify_leg_bike 안에서 재므로 walk_in/out 은 0 이다.
            #   버스·지하철 후보가 최대_제시 를 이미 채웠으면 열거하지 않는다 — 축 순서가 곧 규칙이다.
            if axis == "수단교체" and self.bike_enabled() and len(out) < maxn and not is_bike_leg:
                if is_bus and bx is not None:
                    fr = {"lat": bx["lat"], "lng": bx["lng"], "name": f"{bx['station_nm']} 정류장"}
                    to = {"lat": by["lat"], "lng": by["lng"], "name": f"{by['station_nm']} 정류장"}
                    src = f"{bx['station_nm']}→{by['station_nm']}"
                elif line and self.sc:
                    fr, to, src = a, b, f"{a}→{b}"
                else:
                    fr = to = None
                if fr is not None:
                    take(axis, f"따릉이 {src} — 반경 {self.R['bike']['ddareungi']['station_walk_m']['value']}m 대여소",
                         {"mode": "bike", "from": fr, "to": to})


        # 택시 — 불가가 난 구간의 양 끝(역 좌표 · 버스는 정류장 좌표)에서 그 시각 출발 (v0.6)
        if is_bus:
            ts = (bx["lng"], bx["lat"]) if bx is not None else None
            te = (by["lng"], by["lat"]) if by is not None else None
        else:
            ts, te = self._pt(line, a), self._pt(line, b)
        return out[:maxn], tried, self._taxi(ts, te, now_min)

    def _ev_tt(self, line, station, day_type, claim, grade="확정"):
        return {"source_type": "db", "source_id": f"timetable_v1@{self.tt.fetched_at}",
                "grade": grade, "observed_at": self.tt.fetched_at,
                "claim": f"{line} {station} {day_type}: {claim}"}

    def _ev_rule(self, name, grade):
        return {"source_type": "policy", "source_id": self.rules_src, "grade": grade,
                "observed_at": self.rules_at, "claim": name}

    # ── 지하철↔버스 환승 (규칙 v0.4 · 19번 방) ──────────────────────────
    def _walk_limit(self, party):
        return self.rv("limits", "walk_m", "infant_or_luggage" if (
            party.get("infant") or party.get("luggage")) else "default")

    # ── 자동차·택시 (규칙 v0.6 · 21번 방) ─────────────────────────────────
    def _pt(self, line, where):
        """구간 끝점 → (lng, lat). 역명(역 좌표) · 'lat,lng' 문자열 · {lat, lng} dict. 못 찾으면 None."""
        if isinstance(where, dict):
            return (where["lng"], where["lat"]) if where.get("lat") is not None else None
        if isinstance(where, str) and "," in where:
            try:
                la, lo = (float(x) for x in where.split(",", 1))
                return (lo, la)
            except ValueError:
                return None
        if not self.sc or not where:
            return None
        v = self.sc.get(line, where) if line else self.sc.by_name.get(where)
        return (v["lng"], v["lat"]) if v and v.get("lat") is not None else None

    def _depart_dt(self, now_min):
        d = self._case_date or _date.today()
        return _datetime(d.year, d.month, d.day) + _timedelta(minutes=int(now_min))

    def _car_evidence(self, c):
        ev = [{"source_type": "db", "source_id": c["source_id"][0], "grade": "추정",
               "observed_at": "2026-05-31",
               "claim": f"자동차 {c['distance_m']/1000:.1f} km · {c['day_type']} {c['hour_start']}시 출발 소요 "
                        f"{c['topis_time_s']/60:.1f}분 · 커버 topis {c['coverage_pct']['topis']}% / "
                        f"class {c['coverage_pct']['class']}% / default {c['coverage_pct']['default']}% "
                        f"(링크 {c['n_links']}개)"},
              {"source_type": "db", "source_id": c["source_id"][1], "grade": "확정",
               "observed_at": "2026-09-18", "claim": f"도로망 경로 거리 {c['distance_m']:,.0f} m (좌표열은 저장하지 않는다)"},
              self._ev_rule("car.등급", "확정"), self._ev_rule("car.coverage", "추정")]
        if c["mode"] == "taxi":
            ev.append(self._ev_rule("taxi.fare.산식", "확정"))
            ev.append({"source_type": "policy", "source_id": "seoul_taxi_fare@2023-02-01(확인 2026-09-19)",
                       "grade": "추정", "observed_at": "2026-09-19",
                       "claim": f"택시({c['fare_kind']}) 요금 하한 {c['fare_won']:,}원 = 미터 {c['meter_won']:,}"
                                + (f" + 통행료 {c['toll_won']:,}" if c["toll_won"] else "")
                                + f" · 심야율 {c['night_rate']:g} · 저속 {c['slow_s']}초"})
            ev.append(self._ev_rule("car.택시_대기", "근거없음"))
        return ev

    def _car_warnings(self, c):
        ws = [self.warn_msg(code, **kw) for code, kw in c["warn"]]
        if c["mode"] == "taxi":
            ws.append(self.warn_msg("MOB_W_TAXI_FARE_LOWER_BOUND", fare_won=f"{c['fare_won']:,}",
                                    toll=(f" (통행료 {c['toll_won']:,}원 포함)" if c["toll_won"] else "")))
        return ws

    def _car_run(self, s, e, now_min, taxi):
        """CarService 호출 한 번. 반환 (요약 dict | None, 실패 사유). 예외를 밖으로 내지 않는다."""
        if self.car is None:
            return None, "자동차 판정 서비스가 없다(그래프 미적재 또는 라우터 없음)"
        if s is None or e is None:
            return None, "출발·도착 좌표를 못 찾았다"
        try:
            c = self.car.leg(s, e, self._depart_dt(now_min), taxi=taxi)
        except RouterDown as ex:
            return None, str(ex)
        return c, None

    def _taxi(self, s=None, e=None, now_min=None):
        """택시 대안 — 후보에서 빼지 않는다. 라우터가 있으면 소요·요금(추정), 없으면 **모르는** 것이다(근거없음)."""
        base = {"axis": "수단교체", "label": "택시", "leg": {"mode": "taxi"}}
        if s is None or e is None or now_min is None:
            return dict(base, verdict="unknown", grade="근거없음",
                        reason="택시 구간의 출발·도착 지점을 정하지 못했다 — 소요 판단 불가",
                        warnings=[])
        c, why = self._car_run(s, e, now_min, taxi=True)
        if c is None:
            return dict(base, verdict="unknown", grade="근거없음",
                        reason=f"소요 판단 불가 — {why}",
                        warnings=[self.warn_msg("MOB_W_CAR_ROUTER_DOWN", reason=why[:80])])
        ride = ceil1(c["topis_time_s"] / 60)                # #2 올림
        arr = int(now_min) + math.ceil(ride)
        return dict(base, verdict="feasible", grade=c["grade"],
                    reason=f"{c['distance_m']/1000:.1f} km · 소요 {ride:g}분 · 요금 하한 {c['fare_won']:,}원({c['fare_kind']})",
                    depart_min=int(now_min), arrive_min=arr, ride_min=ride,
                    distance_m=c["distance_m"], fare_won=c["fare_won"], fare_kind=c["fare_kind"],
                    warnings=self._car_warnings(c), evidence=self._car_evidence(c),
                    car={k: v for k, v in c.items() if k != "warn"})

    def verify_leg_car(self, idx, leg, now_min, day_type):
        """자동차·택시 구간. 성립 여부는 「경로가 있다」이고 소요는 프로파일(추정). 라우터 없으면 근거없음."""
        taxi = leg.get("mode") == "taxi"
        a_nm, b_nm = leg["from"], leg["to"]
        label = f"{'택시' if taxi else '자동차'} {a_nm}→{b_nm}"
        s, e = self._pt(leg.get("line"), a_nm), self._pt(leg.get("line"), b_nm)
        c, why = self._car_run(s, e, now_min, taxi)
        if c is None:
            if s is None or e is None:
                amb = self._ambig_warn(a_nm if s is None else b_nm, "도로 끝점 좌표")
                return LegResult(idx, label, "unknown", f"{why} ({a_nm if s is None else b_nm})", grade="근거없음", code="no_data",
                                 warnings=[amb] if amb else [])
            return LegResult(idx, label, "unknown", f"도로 소요를 낼 수 없다 — {why}", grade="근거없음", code="no_data",
                             warnings=[self.warn_msg("MOB_W_CAR_ROUTER_DOWN", reason=why[:80])])
        ride = ceil1(c["topis_time_s"] / 60)                # #2 올림
        arr = int(now_min) + math.ceil(ride)
        reason = (f"{fmt_min(now_min)} 출발 · {c['distance_m']/1000:.1f} km · 소요 {ride:g}분"
                  + (f" · 요금 하한 {c['fare_won']:,}원({c['fare_kind']}) · 대기 0분(근거없음)" if taxi else ""))
        return LegResult(idx, label, "feasible", reason, grade=c["grade"],
                         depart_min=int(now_min), arrive_min=arr, wait_min=0 if taxi else None,
                         ride_min=ride, ride_grade=c["grade"],
                         warnings=self._car_warnings(c), evidence=self._car_evidence(c),
                         car={k: v for k, v in c.items() if k != "warn"})

    def _bus_stop_row(self, leg, which):
        """버스 구간의 승차('from')/하차('to') 정류장 행. 노선·정류장을 못 찾으면 None."""
        if self.bus is None:
            return None
        r = self.bus.route(str(leg["route"]))
        if r is None:
            return None
        seg = self._bus_seg(r, leg)
        if seg is None:
            return None
        return seg[0] if which == "from" else seg[1]

    def _stop_station_walk(self, prev_leg, leg, party):
        """정류장 ↔ 역 환승 도보와 근접 상한. rules.transfer.stop_station_walk.

        거리 = 정류장 좌표 ↔ **그 역에서 가장 가까운 출구**(OSM, 추정) 직선. 출구가 없는 역은 역 좌표.
        도보 분 = 직선 × 우회계수 ÷ 1.04 m/s.  상한 판정은 **직선거리**로 limits.walk_m 과 비교한다(13번 §4).
        ★ 상한 ±경계값(20 m) 안이면 출구 좌표 오차가 판정을 뒤집을 수 있어 **근거없음(unknown)** 으로 낸다.
        ★ 좌표가 없으면(수집 밖 노선·정류장) 도보 0분·근거없음 — 종전 fallback 과 같되 경고 코드로 드러낸다.
        """
        S = self.R["transfer"]["stop_station_walk"]
        factor = S["detour_factor"]["value"]
        margin = S["boundary_m"]["value"]
        speed = self.R["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        wlim = self._walk_limit(party)
        if leg.get("mode") == "bus":                 # 지하철 → 버스
            stop, st_line, st_nm = self._bus_stop_row(leg, "from"), prev_leg.get("line"), prev_leg["to"]
        else:                                        # 버스 → 지하철
            stop, st_line, st_nm = self._bus_stop_row(prev_leg, "to"), leg.get("line"), leg["from"]
        stop_nm = stop["station_nm"] if stop else (leg["from"] if leg.get("mode") == "bus" else prev_leg["to"])
        label = f"환승 정류장 {stop_nm} ↔ {st_line} {st_nm}"
        src = {"source_type": "db", "source_id": (self.ex.source_id if self.ex else "osm_subway_entrance"),
               "grade": "추정", "observed_at": (self.ex.built_at if self.ex else None)}

        # 좌표
        pt = self.sc.get(st_line, st_nm) if self.sc else None
        if stop is None or stop.get("lat") is None or pt is None:
            why = ("정류장 좌표가 없다" if stop is None or stop.get("lat") is None
                   else f"{st_line} {st_nm} 역 좌표가 없다")
            # ☆`[2026-09-29 문제목록 #1]` 앞 판은 여기서 도보 0분·성립을 냈다 — 모르는 값을 0 으로 지어낸 것이다.
            #   좌표가 없으면 거리를 낼 대체 출처도 없다 → 판정하지 않는다(no_data). 부르는 쪽이 대체·치명을 정한다.
            return {"verdict": "unknown", "label": label, "walk_min": None, "grade": "근거없음",
                    "dist_m": None, "walk_m": None, "factor": factor, "reason": f"{label} — {why}",
                    "relief": "정류장·역 좌표를 확인한다",
                    "warnings": [self.warn_msg("MOB_W_TRANSFER_COORD_MISSING", reason=why)],
                    "evidence": [{"source_type": "policy", "source_id": self.rules_src, "grade": "근거없음",
                                  "observed_at": self.rules_at,
                                  "claim": f"transfer.stop_station_walk — {why} → 판정하지 않음"}]}
        near = self.ex.nearest(st_nm, stop["lat"], stop["lng"], st_line) if self.ex else None
        if near:
            dist, ex = near
            where = f"{st_nm}역 {ex.get('ref') or '?'}번 출구"
            src["claim"] = (f"정류장 {stop_nm} ↔ {where} 직선 {dist:,.0f}m "
                            f"(출구 귀속 {ex.get('attrib')})")
        else:
            dist = meters(stop["lat"], stop["lng"], pt["lat"], pt["lng"])
            where = f"{st_nm}역 (출구 없음 → 역 좌표)"
            src = {"source_type": "db", "source_id": "station_coords", "grade": "추정",
                   "observed_at": self.sc.built_at,
                   "claim": f"정류장 {stop_nm} ↔ {st_nm} 역 좌표 직선 {dist:,.0f}m (OSM 출구 없음)"}
        walk_m = dist * factor
        walk_min = ceil1(walk_m / speed / 60)               # #2 올림
        rule_ev = self._ev_rule("transfer.stop_station_walk", "추정")

        # 근접 상한 — 직선거리로 본다
        if abs(dist - wlim) <= margin:
            why = (f"정류장 {stop_nm} ↔ {where} 직선 {dist:,.0f}m 가 도보 상한 {wlim:,}m 의 "
                   f"±{margin}m 안이다 — 출구 좌표 오차가 판정을 뒤집을 수 있어 판정하지 않는다")
            return {"verdict": "unknown", "label": label, "walk_min": walk_min, "grade": "근거없음",
                    "dist_m": dist, "walk_m": walk_m, "factor": factor, "reason": why,
                    "relief": "정류장 또는 역을 실제 위치로 다시 확인한다",
                    "warnings": [self.warn_msg("MOB_W_TRANSFER_NEAR_WALK_LIMIT", stop=stop_nm,
                                               station=st_nm, dist_m=round(dist), limit_m=wlim)],
                    "evidence": [dict(src, grade="근거없음"), rule_ev]}
        if dist > wlim:
            nearby = self.sc.stations_near(stop["lat"], stop["lng"],
                                           self.rv("alternatives", "정류장_반경_m")) if self.sc else []
            hint = " · ".join(f"{v['station_nm']}({v['line']}, {d:,.0f}m)" for d, v in nearby[:3])
            why = (f"정류장 {stop_nm} ↔ {where} 직선 {dist:,.0f}m — 도보 상한 {wlim:,}m 를 넘어 "
                   f"환승할 수 없다")
            return {"verdict": "infeasible", "label": label, "walk_min": walk_min, "grade": "추정",
                    "dist_m": dist, "walk_m": walk_m, "factor": factor, "reason": why,
                    "relief": (f"정류장 근처 역은 {hint} — 그 역에서 타는 구간으로 다시 잡는다"
                               if hint else f"정류장 {stop_nm} 반경 안에 역이 없다 — 수단을 바꾼다"),
                    "warnings": [], "evidence": [src, rule_ev]}
        return {"verdict": "feasible", "label": label, "walk_min": walk_min, "grade": "추정",
                "dist_m": dist, "walk_m": walk_m, "factor": factor, "reason": None, "relief": None,
                "warnings": [], "evidence": [src, rule_ev]}

    def _bus_bus_walk(self, prev_leg, leg, party):
        """버스 ↔ 버스 환승 도보와 근접 상한. rules.transfer.bus_bus_walk(v0.9.2 · 73 후속).

        정류장은 **이름이 아니라 노선 안 행**(bus.segment 가 방향·순번으로 고른 행 · station_id)으로 맞춘다 —
        같은 이름의 맞은편 정류장을 0 m 로 합치지 않는다.
          · 같은 station_id  → 같은 정류장 · 도보 0분 · 확정
          · 다른 station_id  → 두 정류장 좌표 직선 × 우회계수 ÷ 1.04 m/s · 추정 (지하철↔버스와 같은 식 · 같은 값)
          · 근접 상한         → 직선이 limits.walk_m 초과면 불가 · 상한 ±boundary_m 안이면 근거없음
          · 행·좌표 없음      → 근거없음(no_data) · MOB_W_TRANSFER_COORD_MISSING
        ★ 옛 규칙 254(0분·근거없음)도, 팀장 #1 대체값(지하철 거리표 상위 10%)도 쓰지 않는다 — 지하철 분포는 버스 근거가 아니다.
        """
        S = self.R["transfer"]["stop_station_walk"]          # 값은 지하철↔버스와 같다(bus_bus_walk.값_출처)
        factor = S["detour_factor"]["value"]
        margin = S["boundary_m"]["value"]
        speed = self.R["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        wlim = self._walk_limit(party)
        a = self._bus_stop_row(prev_leg, "to")               # 앞 버스 하차 정류장(방향·순번으로 고른 행)
        b = self._bus_stop_row(leg, "from")                  # 뒤 버스 승차 정류장
        a_nm = a["station_nm"] if a else prev_leg.get("to")
        b_nm = b["station_nm"] if b else leg.get("from")
        ra, rb = prev_leg.get("route"), leg.get("route")
        label = f"환승 정류장 {a_nm}(버스{ra}) ↔ {b_nm}(버스{rb})"
        rule_ev = self._ev_rule("transfer.bus_bus_walk", "추정")
        if a is not None and b is not None and a.get("station_id") and a.get("station_id") == b.get("station_id"):
            # GPT 대조(78 Q1) — 같은 ID 는 좌표와 무관한 근거다(좌표 검사보다 먼저). 뜻은 「정류장 간 이동 0 m」 —
            #   하차·승차 준비는 0 으로 보지 않는다(길찾기 가산은 부르는 쪽이 따로 더한다).
            src = {"source_type": "db", "source_id": getattr(self.bus, "source_id", None) or "bus_stops_v1",
                   "grade": "확정", "observed_at": a.get("fetched_at") or b.get("fetched_at"),
                   "claim": f"같은 정류장 {a_nm}(ID {a['station_id']}) — 정류장 간 이동 0m"}
            return {"verdict": "feasible", "label": label, "walk_min": 0, "grade": "확정",
                    "dist_m": 0.0, "walk_m": 0.0, "factor": factor, "reason": None, "relief": None,
                    "warnings": [], "evidence": [src, dict(rule_ev, grade="확정")]}

        def _ok(r):
            try:
                return r is not None and all(math.isfinite(float(r.get(k))) for k in ("lat", "lng"))
            except (TypeError, ValueError):
                return False
        if not (_ok(a) and _ok(b)):
            why = ("정류장 행을 못 찾았다(노선·정류장·방향)" if a is None or b is None else "정류장 좌표가 없다")
            return {"verdict": "unknown", "label": label, "walk_min": None, "grade": "근거없음",
                    "dist_m": None, "walk_m": None, "factor": factor, "reason": f"{label} — {why}",
                    "relief": "노선·정류장을 확인한다",
                    "warnings": [self.warn_msg("MOB_W_TRANSFER_COORD_MISSING", reason=why)],
                    "evidence": [dict(rule_ev, grade="근거없음",
                                      claim=f"transfer.bus_bus_walk — {why} → 판정하지 않음")]}
        src = {"source_type": "db", "source_id": getattr(self.bus, "source_id", None) or "bus_stops_v1",
               "grade": "추정", "observed_at": a.get("fetched_at") or b.get("fetched_at")}
        dist = meters(a["lat"], a["lng"], b["lat"], b["lng"])
        walk_m = dist * factor
        walk_min = ceil1(walk_m / speed / 60)
        src["claim"] = (f"정류장 {a_nm}(ID {a.get('station_id')} · {a.get('direction') or '?'} 방향) ↔ "
                        f"{b_nm}(ID {b.get('station_id')} · {b.get('direction') or '?'} 방향) 직선 {dist:,.0f}m")
        if abs(dist - wlim) <= margin:
            why = (f"{label} 직선 {dist:,.0f}m 가 도보 상한 {wlim:,}m 의 ±{margin}m 안이다 — "
                   f"정류장 좌표 오차가 판정을 뒤집을 수 있어 판정하지 않는다")
            return {"verdict": "unknown", "label": label, "walk_min": walk_min, "grade": "근거없음",
                    "dist_m": dist, "walk_m": walk_m, "factor": factor, "reason": why,
                    "relief": "정류장을 실제 위치로 다시 확인한다",
                    "warnings": [self.warn_msg("MOB_W_TRANSFER_NEAR_WALK_LIMIT", stop=a_nm,
                                               station=b_nm, dist_m=round(dist), limit_m=wlim)],
                    "evidence": [dict(src, grade="근거없음"), rule_ev]}
        if dist > wlim:
            why = f"{label} 직선 {dist:,.0f}m — 도보 상한 {wlim:,}m 를 넘어 환승할 수 없다"
            return {"verdict": "infeasible", "label": label, "walk_min": walk_min, "grade": "추정",
                    "dist_m": dist, "walk_m": walk_m, "factor": factor, "reason": why,
                    "relief": f"{a_nm} 에서 가까운 정류장을 지나는 다른 노선으로 다시 잡는다",
                    "warnings": [], "evidence": [src, rule_ev]}
        return {"verdict": "feasible", "label": label, "walk_min": walk_min, "grade": "추정",
                "dist_m": dist, "walk_m": walk_m, "factor": factor, "reason": None, "relief": None,
                "warnings": [], "evidence": [src, rule_ev]}

    # ── 다목적 후보 (규칙 v0.5 · 20번 방) ────────────────────────────────
    MULTI_STRIP = ("multi", "expect", "expect_candidates_min", "expect_criteria", "expect_candidate_legs",
                   "expect_candidate_arrive", "expect_tie", "expect_tie_axes", "expect_feasible_max",
                   "expect_no_line_feasible", "expect_mixed_min", "expect_mixed_max", "expect_dropped_why", "note")
    #: (87) 역 기준 혼합 후보를 판정에 넣는 개수(추정 소요 순) — 회귀·CLI 용 opt-in(multi.mixed) 이라 성능 상한만
    MIX_VERIFY_MAX = 3

    def verify_multi(self, case):
        """`multi: {from, to}` 케이스 — 후보를 만들고 **후보마다 verify_case 를 그대로** 돌린다.

        생성기(candidates.py)는 판정하지 않는다. 이슈·시간표·환승 상한은 전부 판정기가 본다.
        ★ 순위를 매기지 않는다 — candidates 의 순서는 규칙 candidates.기준 의 순서지 우열이 아니다.
        ★ tie_band — 도착이 있는 두 후보의 차이가 (불확실성 A + 불확실성 B) 안이면 동급. 이유는 시간 밖 축.
        """
        origin, dest = case["multi"]["from"], case["multi"]["to"]
        # ★ 55 ④ — 동명이역(양평·신촌)은 역명만으로 물리적 역을 모른다. 노선(from_lines · to_lines)을 같이 받으면 그 역으로 고른다.
        o_lines, d_lines = case["multi"].get("from_lines"), case["multi"].get("to_lines")
        first_visit = case.get("first_visit", True)
        party = case.get("party", {}) or {}
        C = self.R["candidates"]
        d = _date.fromisoformat(case["date"])
        day_type = day_type_of(d, self.holidays)
        now = to_service_min(case.get("depart_at"), ceil_seconds=True)   # #12 초는 올린다
        if now is None:
            raise CaseInputError(f"[{case.get('id')}] depart_at 이 없다.")
        warns, ev = [], [self._ev_rule("candidates.기준", "확정")]
        amb = [(nm, ls) for nm, ls in ((origin, o_lines), (dest, d_lines))
               if self.sc and getattr(self.sc, "is_ambiguous", None) and self.sc.is_ambiguous(nm)
               and self.sc.resolve(nm, ls) is None]
        if amb:
            # 조용히 한쪽을 집지 않는다 — 값을 비우고 경고(55 ④). 노선을 주면 풀린다.
            warns += [self._ambig_warn(nm, "출발·도착역") for nm, _ in amb]
            ev.append(self._ev_rule("station_names.동명이역_좌표차_m", "추정"))
            res = self._finish(case, day_type, [], "unknown",
                               f"{' · '.join(nm for nm, _ in amb)} 은(는) 이름이 같은 다른 역이 있다 — 노선 없이 역명만으로는 후보를 만들지 않는다",
                               "근거없음", None, None, "노선(from_lines · to_lines)이나 장소 좌표를 같이 준다", warns, ev)
            res.code, res.candidates = "no_data", []
            res.out = self._out(res, case, now, to_service_min(case.get("arrive_by")))
            return res
        cg = self.candidate_graph(first_visit)
        tlim = self.rv("limits", "transfers", "default")          # 환승 상한 — 판정기와 같은 값(동행별)
        for k in ("infant", "elderly", "fatigue_high"):
            if party.get(k):
                tlim = min(tlim, self.rv("limits", "transfers", k))
        gen = cg.candidates(origin, dest, C["기준"]["value"], max_transfers=tlim,
                            origin_lines=o_lines, dest_lines=d_lines)
        arrive_by = to_service_min(case.get("arrive_by"))
        # ☆`[2026-09-29 문제목록 #7]` 앞 판은 지하철 후보가 없으면 여기서 「근거없음」으로 끝냈다 — 버스 직행·자전거
        #   후보를 만들기 전이라 버스로만 가는 구간이 데이터 없음이 됐다. 지하철 후보가 없어도 아래로 내려가고,
        #   **어떤 수단의 후보도 없을 때만** 끝낸다.
        keep, dropped = [], []
        ev.append(self._ev_rule("candidates.허용_소요_배수", "추정"))
        for c in gen:
            keep.append({"criteria": list(c.criteria), "legs": c.legs, "est_min": c.est_min,
                         "transfers": c.transfers, "walk_min": c.walk_min, "gen_grade": c.grade,
                         "fallback_edges": c.fallback_edges, "walk_in": 0, "walk_out": 0})

        # 버스 직행 후보 — 대안 열거 ⓒ 와 같은 후보 공간
        if C["버스_직행_후보"]["value"] and self.bus and self.sc:
            radius = self.rv("alternatives", "정류장_반경_m")
            excluded = self.rv("bus", "route_type_제외") or []
            wlim = self._walk_limit(party)
            pa, pb = self.sc.resolve(origin, o_lines), self.sc.resolve(dest, d_lines)   # 55 ④ — 동명이역은 노선으로
            if pa and pb and pa.get("lat") is not None and pb.get("lat") is not None:
                for r, x, y, span, da, db in self.bus.routes_between(
                        pa["lat"], pa["lng"], pb["lat"], pb["lng"], radius):
                    if r.route_type_nm in excluded or max(da, db) > wlim:
                        continue
                    keep.append({"criteria": ["버스직행"],
                                 "legs": [{"mode": "bus", "route": r.route_nm,
                                           "from": x["station_nm"], "to": y["station_nm"]}],
                                 "est_min": None, "transfers": 0, "walk_min": None, "gen_grade": "추정",
                                 "fallback_edges": [], "walk_in": da, "walk_out": db})
            ev.append(self._ev_rule("candidates.버스_직행_후보", "확정"))

        # 자전거 후보(규칙 v0.7 · 22번 방) — 역 좌표 기준. 판정(대여소·가용·소요)은 verify_leg_bike 가 한다.
        if self.bike_enabled() and self.R["bike"]["ddareungi"]["multi_후보"]["value"] and self.bk and self.sc:
            # 55 ④ — 동명이역 끝점은 역명 대신 고른 물리적 역의 좌표로 넘긴다(자전거는 역명만 받으면 값을 안 낸다)
            bend = lambda nm, ls: (nm if not self.sc.is_ambiguous(nm) else
                                   (lambda r: {"lat": r["lat"], "lng": r["lng"], "name": f"{nm}역({r['line']})"}
                                    if r and r.get("lat") is not None else nm)(self.sc.resolve(nm, ls)))
            keep.append({"criteria": ["자전거"], "legs": [{"mode": "bike", "from": bend(origin, o_lines), "to": bend(dest, d_lines)}],
                         "est_min": None, "transfers": 0, "walk_min": None, "gen_grade": "추정",
                         "fallback_edges": [], "walk_in": 0, "walk_out": 0})
            ev.append(self._ev_rule("bike.ddareungi.multi_후보", "추정"))

        # ☆`[2026-10-01 87]` 지하철+버스 혼합(환승 1회 · A 버스→지하철 · B 지하철→버스) — **multi.mixed 가 true 일 때만**(회귀·CLI ·
        #   역 기준). 장소 기준 혼합은 plan.Planner._mixed 가 따로 만든다(plan 은 이 함수의 버스 섞인 후보를 안 쓴다 — 23 결정 4).
        #   기본(없음)은 앞 판과 같은 후보 집합이다. 판정은 아래 같은 판정기 · 거르기(앞설 축 · 상한)는 판정 뒤.
        mix_notes = []
        if case["multi"].get("mixed") and self.bus and self.sc:
            pa, pb = self.sc.resolve(origin, o_lines), self.sc.resolve(dest, d_lines)
            if pa and pb and pa.get("lat") is not None and pb.get("lat") is not None:
                radius = self.rv("alternatives", "정류장_반경_m")
                wlim = self._walk_limit(party)
                avoid = {x.get("line") for x in case.get("disruptions") or [] if x.get("kind") == "line_closed"}
                skip = {(x.get("line"), x.get("station")) for x in case.get("disruptions") or []
                        if x.get("kind") == "station_skip"}

                ride = ride_estimator(self)
                gen = MixedGenerator(cg, self.bus, self.sc, self.ex, radius_m=radius, near_m=radius,
                                     cuts=mix_rule(self.R, "혼합_끊는_지점_최대", MIX_CUTS_PER_ROUTE_PROPOSED), tlim=tlim,
                                     excluded=self.rv("bus", "route_type_제외") or [], ride_min=ride,
                                     wayfinding=self.R["transfer"]["wayfinding_addition_min"]["value"] if first_visit else 0,
                                     walk_speed=self.R["measured_baseline"]["kakao_walk_speed_mps"]["value"],
                                     detour=self.R["transfer"]["stop_station_walk"]["detour_factor"]["value"],
                                     avoid_lines=avoid, skip_at=skip)
                # 끊는 역은 출발·도착 역에서 정류장 반경 밖만(역 기준 — 그 안이면 같은 역 앞에서 버스만 한 번 더 타는 꼴)
                mc = interleave(gen.bus_to_subway(pa["lat"], pa["lng"], [(dest, d_lines, 0)], wlim, radius),
                                gen.subway_to_bus([(origin, o_lines, 0)], pb["lat"], pb["lng"], wlim, radius))
                n_ver = n_over = 0
                for c in mc:
                    if n_ver >= self.MIX_VERIFY_MAX:
                        n_over += 1
                        continue
                    if not gen.materialize(c):
                        continue
                    n_ver += 1
                    keep.append({"criteria": [f"혼합{c.shape}"], "legs": c.legs, "est_min": c.est_min,
                                 "transfers": c.transfers, "walk_min": c.sub_walk_min, "gen_grade": c.grade,
                                 "fallback_edges": c.fallback_edges,
                                 "walk_in": c.walk_in_m or 0, "walk_out": c.walk_out_m or 0, "mixed": True,
                                 "link_m": c.link_m})
                if n_over:
                    mix_notes.append({"criteria": ["혼합"], "legs": [], "est_min": None, "transfers": None,
                                      "walk_min": None, "arrive_min": None,
                                      "why": f"그 밖 혼합 후보 {n_over}개 — 혼합 판정 {self.MIX_VERIFY_MAX}개(추정 소요 순)를 넘어 판정하지 않음"})
                air = sorted(set(gen.skipped_airport))
                if air:
                    mix_notes.append({"criteria": ["혼합B"], "legs": [], "est_min": None, "transfers": None,
                                      "walk_min": None, "arrive_min": None, "code": "no_data",
                                      "why": f"공항행 공항버스 {len(air)}노선({', '.join(air[:5])}{' …' if len(air) > 5 else ''}) — "
                                             "공항행 시각 근거가 판정기에 없어(37) 혼합 후보로 만들지 않음(no_data)"})

        if not keep:
            res = self._finish(case, day_type, [], "unknown",
                               f"{origin}→{dest} 를 잇는 후보(지하철·버스 직행·자전거)를 만들지 못했다",
                               "근거없음", None, None, None, warns, ev)
            res.code, res.candidates = "no_data", []
            res.out = self._out(res, case, now, arrive_by)
            return res

        # 후보마다 같은 판정기 — 대안 열거는 끈다(후보끼리가 이미 대안이다)
        speed = self.R["measured_baseline"]["kakao_walk_speed_mps"]["value"]
        wm = lambda m: math.ceil(m / speed / 60) if m else 0
        base = {k: v for k, v in case.items() if k not in self.MULTI_STRIP}
        base["no_alternatives"] = True
        out = []
        for n, c in enumerate(keep, 1):
            # ★ 후보의 도착 목표는 이탈 도보만큼 앞당겨 준다 — 판정기는 마지막 역 도착까지만 보기 때문이다(v0.8 대조 6).
            sub = dict(base, id=f"{case.get('id')}/{n}", legs=c["legs"],
                       depart_at=now + wm(c["walk_in"]))
            if arrive_by is not None:
                sub["arrive_by"] = arrive_by - wm(c["walk_out"])
            r = self.verify_case(sub)
            arr = r.arrive_min + wm(c["walk_out"]) if r.arrive_min is not None else None
            bus_wait = sum((l.wait_min or 0) for l in r.legs
                           if l.verdict == "feasible" and l.label.startswith("버스 "))
            # ★ v0.8 — 불확실성 폭은 @ 부품이다: 최악 도착 − 예정 도착(버퍼 제외). 버스면 배차 전부 − 절반, 지하철은 0.
            #   worst 를 못 냈으면(도착 없음) 종전 값(버스 대기 추정치)으로 둔다.
            spread = (r.arrive_worst_min - r.arrive_min) if (r.arrive_worst_min is not None and r.arrive_min is not None) else None
            unc = spread if spread is not None else (
                bus_wait if any(l.get("mode") == "bus" for l in c["legs"])
                else self.rv("tie_band", "지하철_불확실성_분"))
            walk_total = ((c["walk_min"] or 0) + wm(c["walk_in"]) + wm(c["walk_out"])
                          + sum((l.walk_min or 0) for l in r.legs)        # 자전거 구간 안 도보(v0.7)
                          + (math.ceil(c["link_m"] * self.R["transfer"]["stop_station_walk"]["detour_factor"]["value"]
                                       / speed / 60) if c.get("mixed") else 0))   # 87 — 정류장↔역 환승 도보(판정기와 같은 식)
            out.append({"n": n, "criteria": c["criteria"], "legs": c["legs"],
                        "label": " → ".join(leg_txt(l) for l in c["legs"]),
                        "verdict": r.verdict, "reason": r.reason, "relief": r.relief, "grade": r.grade,
                        "depart_min": now + wm(c["walk_in"]), "arrive_min": arr,
                        "walk_in_min": wm(c["walk_in"]), "walk_out_min": wm(c["walk_out"]),
                        "transfers": c["transfers"], "walk_min": round(walk_total, 1),
                        "est_min": c["est_min"], "gen_grade": c["gen_grade"],
                        "fallback_edges": c["fallback_edges"],
                        "uncertainty_min": unc, "warnings": r.warnings, "legs_result": r.legs, "mixed": c.get("mixed", False),
                        "evidence": r.evidence, "tie_with": [],
                        # v0.8 — 후보마다의 밖 판(out)과 부품. arrive_min 은 예정, out 은 worst 기준 판정이다.
                        "code": r.code, "arrive_worst_min": r.arrive_worst_min, "margin_min": r.margin_min,
                        "buffer_min": r.buffer_min, "slack_min": r.slack_min, "eta_min": r.eta_min,
                        "last_feasible_depart_min": r.last_feasible_depart_min,
                        # 후보의 밖 판 — 출발지 기준으로 되돌린다(접근 도보 앞 · 이탈 도보 뒤)
                        "out": self._shift_out(r.out, now, wm(c["walk_in"]), wm(c["walk_out"]))})

        # 버스 직행 상한 — 성립한 버스 후보를 도보 짧은 순으로 N개만 남긴다(자르는 기준이지 순위가 아니다).
        #   불가·근거없음 버스 후보는 bus_rejected 로 접는다. rules candidates.버스_직행_최대.
        bmax = C["버스_직행_최대"]["value"]
        bus_rejected, kept = [], []
        live_bus = sorted((c for c in out if "버스직행" in c["criteria"] and c["verdict"] == "feasible"),
                          key=lambda c: (c["walk_in_min"] + c["walk_out_min"], c["n"]))
        keep_n = {c["n"] for c in live_bus[:bmax]}
        for c in out:
            if "버스직행" not in c["criteria"] or c["n"] in keep_n:
                kept.append(c)
            elif c["verdict"] == "feasible":
                dropped.append({"criteria": c["criteria"], "legs": c["legs"], "est_min": None,
                                "transfers": 0, "walk_min": c["walk_min"], "arrive_min": c["arrive_min"],
                                "why": f"버스 직행 상한 {bmax}개(도보 순)"})
            else:
                bus_rejected.append({"label": c["label"], "verdict": c["verdict"], "reason": c["reason"]})
        if len(kept) != len(out):
            ev.append(self._ev_rule("candidates.버스_직행_최대", "추정"))
        out = kept
        # 87 — 혼합은 **지하철만·버스만(성립) 후보보다 소요·환승·도보 중 하나라도 앞설 때만** 남긴다. 뒤지거나 성립이 아니면
        #   dropped_candidates(이유) · 남은 혼합이 상한(candidates.혼합_최대 · 변경안)을 넘으면 추정 소요 순으로 자르고 dropped.
        if any(c.get("mixed") for c in out) or mix_notes:
            base_l = [c for c in out if not c.get("mixed") and c["verdict"] == "feasible" and c["arrive_min"] is not None
                      and not any(l.get("mode") == "bike" for l in c["legs"])]
            # 소요는 **요청 출발(now)부터** 도착까지 — 후보의 depart_min 은 접근 도보 뒤라 그걸로 빼면 도보 긴 후보가 유리해진다
            bst = ((min(c["arrive_min"] - now for c in base_l), min(c["transfers"] for c in base_l),
                    min(c["walk_min"] for c in base_l)) if base_l else None)
            mmax = mix_rule(self.R, "혼합_최대", MIX_MAX_PROPOSED)
            ratio = C["허용_소요_배수"]["value"]
            kept2, nmix = [], 0
            for c in out:
                if not c.get("mixed"):
                    kept2.append(c)
                    continue
                d0 = {"criteria": c["criteria"], "legs": c["legs"], "est_min": c["est_min"], "transfers": c["transfers"],
                      "walk_min": c["walk_min"], "arrive_min": c["arrive_min"]}
                if c["verdict"] != "feasible" or c["arrive_min"] is None:
                    dropped.append(dict(d0, why=f"혼합 — 성립 아님({c['verdict']}: {(c['reason'] or '')[:80]})"))
                    continue
                eta = c["arrive_min"] - now
                if bst is not None and eta > bst[0] * ratio:
                    dropped.append(dict(d0, why=f"혼합 — 소요 {eta}분이 가장 짧은 지하철만·버스만 후보 {bst[0]}분 × 허용_소요_배수 "
                                                f"{ratio:g} 를 넘는다(환승·도보가 적어도 대표안이 아니다)"))
                    continue
                if bst is not None and not (eta < bst[0] or c["transfers"] < bst[1] or c["walk_min"] < bst[2]):
                    dropped.append(dict(d0, why=f"혼합 — 지하철만·버스만 후보보다 앞서는 축 없음(소요 {eta} vs {bst[0]} · "
                                                f"환승 {c['transfers']} vs {bst[1]} · 도보 {c['walk_min']} vs {bst[2]})"))
                    continue
                if nmix >= mmax:
                    dropped.append(dict(d0, why=f"혼합 상한 {mmax}개(추정 소요 순)"))
                    continue
                nmix += 1
                kept2.append(c)
            dropped.extend(mix_notes)
            out = kept2
        for i, c in enumerate(out, 1):          # 번호를 다시 매긴다 — 목록에 남은 순서로
            c["n"] = i

        # tie_band
        ties = []
        axes_rule = self.rv("tie_band", "이유_축")
        live = [c for c in out if c["verdict"] == "feasible" and c["arrive_min"] is not None]
        is_bus = lambda c: any(l.get("mode") == "bus" for l in c["legs"])
        is_bike = lambda c: any(l.get("mode") == "bike" for l in c["legs"])
        ev.append(self._ev_rule("tie_band.비교_대상", "확정"))
        for i in range(len(live)):
            for j in range(i + 1, len(live)):
                A, B = live[i], live[j]
                if is_bus(A) == is_bus(B) or is_bike(A) or is_bike(B):
                    continue                         # 지하철×버스 만 비교한다(rules tie_band.비교_대상) — 자전거는 비교 밖(v0.7)
                delta = abs(A["arrive_min"] - B["arrive_min"])
                band = A["uncertainty_min"] + B["uncertainty_min"]
                if delta <= band:
                    axes = {}
                    for ax in axes_rule:
                        if ax == "환승":
                            axes[ax] = (A["transfers"], B["transfers"])
                        elif ax == "도보":
                            axes[ax] = (A["walk_min"], B["walk_min"])
                        elif ax == "등급":
                            axes[ax] = (A["grade"], B["grade"])
                    txt = " · ".join(f"{k} {v[0]} vs {v[1]}" for k, v in axes.items())
                    same = all(v[0] == v[1] for v in axes.values())
                    ties.append({"a": A["n"], "b": B["n"], "delta_min": delta, "band_min": band,
                                 "axes": axes, "same_on_axes": same})
                    A["tie_with"].append(B["n"]); B["tie_with"].append(A["n"])
                    warns.append(self.warn_msg("MOB_W_TIE_BAND", a=A["label"], b=B["label"],
                                               delta=delta, band=band,
                                               axes=txt + (" — 축에서도 차이 없음" if same else "")))
        if ties:
            ev.append(self._ev_rule("tie_band.판정", "확정"))
            ev.append(self._ev_rule("tie_band.버스_불확실성", "추정"))

        # 확정 축 — 판정기가 낸 값으로 축마다 「가장 작은」 후보를 표시한다(동률 전부). 순위가 아니라 축별 사실이다.
        #   생성기의 기준 표식(최단 등)은 추정치로 고른 것이라 판정 뒤 도착 순서와 다를 수 있다(공릉→회현에서 실제로 그랬다).
        axis_best = {}
        if live:
            for ax, fn in (("도착", lambda c: c["arrive_min"]), ("환승", lambda c: c["transfers"]),
                           ("도보", lambda c: c["walk_min"])):
                m = min(fn(c) for c in live)
                axis_best[ax] = [c["n"] for c in live if fn(c) == m]
        nf = sum(1 for c in out if c["verdict"] == "feasible")
        nf_out = sum(1 for c in out if (c["out"] or {}).get("verdict") == "feasible")
        if nf:
            # 케이스 등급 = 성립 후보 중 **가장 좋은** 등급. 케이스 판정 「성립하는 후보가 있다」는 그 후보 하나로
            # 뒷받침되므로 최악값을 쓰면 과소평가다. 후보마다의 등급은 candidates[].grade 에 그대로 있다.
            verdict = "feasible"
            grade = max((c["grade"] for c in out if c["verdict"] == "feasible"),
                        key=lambda g: GRADE_ORDER[g.split(":")[0]])
        elif all(c["verdict"] == "unknown" for c in out):
            verdict, grade = "unknown", "근거없음"
        else:
            verdict, grade = "infeasible", worst_grade(*[c["grade"] for c in out])
        reason = (f"{origin}→{dest} 후보 {len(out)}개 중 성립 {nf}개"
                  + (f" · 동급 {len(ties)}쌍" if ties else "") + " (순위 없음)")
        res = self._finish(case, day_type, [], verdict, reason, grade, None, None,
                           None if nf else "후보 전부 불가 — 출발 시각을 옮기거나 수단을 바꾼다", warns, ev)
        res.candidates, res.ties, res.dropped_candidates, res.bus_rejected = out, ties, dropped, bus_rejected
        res.axis_best = axis_best
        if not nf:
            ra = self.sc.resolve(origin, o_lines) if self.sc else None     # 55 ④ — 동명이역은 고른 물리적 역 좌표로
            rb = self.sc.resolve(dest, d_lines) if self.sc else None
            res.taxi = self._taxi(self._pt(None, ra if ra else origin), self._pt(None, rb if rb else dest), now)
        # v0.8 — 케이스의 밖 판: 성립 후보가 하나라도 있으면 성립. 없으면 후보 코드 중 가장 흔한 것(전부 근거없음이면 no_data).
        res.verdict_best = verdict
        res.buffer_min = self.rv("buffer", "by_stage", case.get("stage", "planning"))
        # 밖 판은 **후보의 밖 판**으로 센다 — 내부 성립이어도 예정 시각을 못 낸 후보(no_data)는 밖으로 성립이 아니다.
        if not nf_out:
            codes = collections.Counter((c["out"] or {}).get("code") or OUT_OF[c["verdict"]][1] or "no_service" for c in out)
            res.code = codes.most_common(1)[0][0] if codes else "no_data"
        res.out = self._out(res, case, now, arrive_by)
        res.out["verdict"] = "feasible" if nf_out else "infeasible"
        if nf_out:
            res.out.pop("code", None); res.out.pop("reason", None)
        else:
            res.out["code"], res.out["reason"] = res.code, res.reason
        res.out["candidates"] = [{"n": c["n"], "label": c["label"], **(c["out"] or {})} for c in out]
        return res

    @staticmethod
    def _shift_out(o, depart_min, walk_in, walk_out):
        """후보(역→역) 밖 판을 출발지→목적지 기준으로 — 출발은 origin 시각, 도착·최악 도착은 이탈 도보만큼 뒤,
        소요(eta · eta_worst · p90_eta)는 접근+이탈 도보만큼 길게, 늦어도 출발은 접근 도보만큼 앞."""
        if not o:
            return o
        o = dict(o)
        o["depart_min"] = depart_min
        for k in ("arrive_min", "arrive_worst_min"):
            if o.get(k) is not None:
                o[k] += walk_out
        # ★ 55 ① — p90_eta_min 도 eta 와 같은 축(출발지→목적지)이다. 안 밀면 p90 이 p50 보다 작게 나온다
        #   (서울역→이태원 421: eta 29 · p90 23 · 48 작업메모 §4). 뜻(41 D5 · 버스 승차 p90 가산)은 그대로, 축만 맞춘다.
        for k in ("eta_min", "eta_worst_min", "p90_eta_min"):
            if o.get(k) is not None:
                o[k] += walk_in + walk_out
        if o.get("last_feasible_depart_min") is not None:
            o["last_feasible_depart_min"] -= walk_in
        if o.get("arrive_by_min") is not None:
            o["arrive_by_min"] += walk_out
        return o

    # ── 케이스 한 건 ──
    def _party_limit(self, party):
        lim = self.rv("limits", "transfers", "default")
        which = "default"
        for k in ("infant", "elderly", "fatigue_high"):
            if party.get(k):
                v = self.rv("limits", "transfers", k)
                if v < lim:
                    lim, which = v, k
        return lim, which

    def _chain(self, case, now, day_type, is_sat, party, worst=False):
        """구간 열을 한 번 통과한다 — best(종전 계산) 또는 worst(v0.8).

        반환 dict: ok · verdict · code · reason · relief · legs · warns · ev · arrive · transfers ·
                   fail_idx · fail_leg · fail_now · fail_kind(leg | transfer_walk | transfer_skip | car) · no_arrive
        판정 조립(대안 열거 · 버퍼 · 상한 · 밖 판)은 verify_case 가 한다. 여기서는 구간만 본다.
        """
        first_visit = case.get("first_visit", True)
        arrive_by = to_service_min(case.get("arrive_by"))
        legs, warns, ev = [], [], []
        transfers = 0
        prev_line = None

        def fail(verdict, reason, code, relief, kind, i, leg, at):
            return {"ok": False, "verdict": verdict, "code": code, "reason": reason, "relief": relief,
                    "legs": legs, "warns": warns, "ev": ev, "arrive": None, "transfers": transfers,
                    "fail_idx": i, "fail_leg": leg, "fail_now": at, "fail_kind": kind, "no_arrive": False}

        for i, leg in enumerate(case["legs"]):
            mode = leg.get("mode", "subway")
            if mode not in ("subway", "bus", "car", "taxi", "bike"):
                raise CaseInputError(f"[{case.get('id')}] 모르는 수단이다: mode={mode}")
            if mode in ("car", "taxi"):
                # 자동차·택시 구간(v0.6). 앞 구간이 있으면 수단 교체 = 환승 1회로 센다. 승차 지점까지의 도보·대기는
                # 자료가 없어 0분(car.택시_대기 근거없음) — 구간 사유에 적고 요금 경고가 하한이라 말한다.
                if prev_line is not None:
                    transfers += 1
                r = self._cached_leg(("car", i, json.dumps(leg, sort_keys=True, ensure_ascii=False), now),
                                     lambda: self.verify_leg_car(i, leg, now, day_type))
                r.worst = worst
                legs.append(r)
                warns += r.warnings
                ev += r.evidence
                if r.verdict != "feasible":
                    return fail(r.verdict, r.reason, r.code, r.relief, "car", i, leg, now)
                if worst:
                    # 자동차 소요의 스프레드는 근거가 없다(TOPIS 프로파일은 시간대 평균) — worst = best. 지어내지 않는다.
                    warns.append(self.warn_msg("MOB_W_WORST_NO_SPREAD", what=f"{r.label} 소요"))
                    ev.append(self._ev_rule("judgment.worst.car_spread", "근거없음"))
                now = r.arrive_min
                prev_line = mode
                continue
            if prev_line is not None:
                transfers += 1
                # ☆`[2026-09-29 문제목록 #11]` 지하철끼리 이을 때 앞 구간 도착역 = 뒤 구간 출발역이어야 한다. 앞 판은 검사하지
                #   않아 A→B 다음 C→D 를 「C 환승 · 도보 0분」으로 성립시켰다. 후보 생성기는 이어진 구간만 만든다 — 직접
                #   부른 입력의 결함이므로 판정하지 않고 입력 오류로 돌려준다.
                prev0 = case["legs"][i - 1]
                if leg_mode(prev0) == "subway" and mode == "subway" and prev0.get("to") != leg.get("from"):
                    raise CaseInputError(f"[{case.get('id')}] 구간이 이어지지 않는다 — "
                                         f"{leg_txt(prev0)} 다음 {leg_txt(leg)}")
                st = leg.get("from")
                st = st.get("name") if isinstance(st, dict) else st      # 자전거 구간은 좌표 dict 일 수 있다
                prev_mode = leg_mode(case["legs"][i - 1])
                with_bike = (mode == "bike" or prev_mode == "bike")
                # ★ 환승역이 무정차면 갈아탈 수 없다. 출발·도착역 검사(verify_leg)로는 안 잡힌다 —
                #   여기서는 그 역이 앞 구간의 '도착'이자 뒤 구간의 '출발'이라 둘 다 통과해 버린다.
                dsk = (self._disr("station_skip", line=prev_line, station=st)
                       or self._disr("station_skip", line=leg.get("line"), station=st))
                if dsk:
                    legs.append(LegResult(i, f"환승 {st}", "infeasible",
                                          f"{st} 에 서지 않는 열차가 있어 환승할 수 없다 "
                                          f"({self._disr_label(dsk)})",
                                          grade=dsk.get("grade", "추정"), code="disruption", worst=worst))
                    ev.append(self._ev_disr(dsk, f"{st} 무정차"))
                    return fail("infeasible", f"{st} 환승이 무정차로 막힌다 ({self._disr_label(dsk)})", "disruption",
                                f"{st} 말고 다른 역에서 갈아타거나 수단을 바꾼다", "transfer_skip", i, leg, now)
                # 길찾기 가산은 **초행일 때만**이다(rules.transfer.wayfinding_addition_min.적용조건).
                # 1순위 고객이 첫 방문 인바운드라 기본값은 true 로 둔다.
                wf = self.rv("transfer", "wayfinding_addition_min") if first_visit else 0
                # ★ 환승 도보는 역별 실제 거리로 잰다(rules.transfer.walk_distance).
                #   13개 역 일괄 +2분은 superseded — 거리표에 없는 환승의 fallback 으로만 남는다.
                cur_line = leg.get("line") or f"버스{leg.get('route')}"
                prev_leg = case["legs"][i - 1]
                mixed = (mode == "bus") != (prev_leg.get("mode", "subway") == "bus")
                label, w, tg, twarn = f"환승 {st}", None, "추정", []
                if mode == "bus" and not with_bike and self.bus is not None and self._bus_stop_row(leg, "from") is None:
                    # ☆`[73 후속 · 탐침 전이표]` 뒤 버스 구간의 정류장 행을 못 찾으면(그 방향으로 안 감 · 수집 밖 노선) 환승 좌표도
                    #   없다. 팀장 #1 뒤로 여기서 「좌표 없음 → 근거없음」으로 먼저 멈춰, 알 수 있는 버스 구간 불가(no_service ·
                    #   그 방향 운행 없음)가 근거없음으로 바뀌었다(탐침 443건 · 역방향 탐침). 버스 구간을 먼저 판정해 그 이유를 낸다.
                    #   버스 구간이 성립하면(행이 없는데 성립할 일은 없다) 아래 환승 분기가 종전대로 좌표 없음을 낸다.
                    r0 = self.verify_leg_bus(i, leg, now, day_type, worst=worst)
                    # GPT 대조(78 Q3) — 시각과 무관한 이유(방향 없음 no_service · 수집 밖·식별 불가 no_data)만 여기서 낸다.
                    #   첫차·막차·도착 목표는 환승 도보를 더한 뒤에 봐야 하므로, 다른 코드가 나오면 아래 환승 분기로 간다.
                    if r0.verdict != "feasible" and r0.code in ("no_service", "no_data"):
                        r0.worst = worst
                        legs.append(r0)
                        warns += r0.warnings
                        ev += r0.evidence
                        return fail(r0.verdict, r0.reason, r0.code or OUT_OF[r0.verdict][1], r0.relief, "leg", i, leg, now)
                if with_bike:
                    # ★ 자전거와의 환승(v0.7 · 22번 방). 대여소까지·대여소에서의 도보는 **자전거 구간 안에서** 잰다
                    #   (verify_leg_bike → LegResult.walk_min) — 여기서 또 더하면 이중 계산이다. 길찾기 가산만 붙인다.
                    label, walk, tev = f"환승 {st} ↔ 자전거", 0, []
                    dist_txt = "(대여소까지 도보는 자전거 구간 안에서 잰다)"
                elif mode == "bus" and prev_leg.get("mode") == "bus":
                    # ★ 버스↔버스 환승(v0.9.2 · 73 후속) — 정류장 행(ID·방향·순번) 좌표로 잰다(_bus_bus_walk).
                    #   팀장 #1 뒤로 여기가 지하철 거리표 상위 10% 대체값을 탔다 — 지하철 분포는 버스 근거가 아니다.
                    bb = self._bus_bus_walk(prev_leg, leg, party)
                    label, tg, twarn, tev = bb["label"], bb["grade"], bb["warnings"], bb["evidence"]
                    if bb["verdict"] != "feasible":
                        warns += twarn
                        ev += tev
                        code = "transfer_walk" if bb["verdict"] == "infeasible" else "no_data"
                        legs.append(LegResult(i, label, bb["verdict"], bb["reason"],
                                              grade=tg, warnings=twarn, evidence=list(tev), code=code, worst=worst))
                        return fail(bb["verdict"], bb["reason"], code, bb["relief"], "transfer_walk", i, leg, now)
                    walk = bb["walk_min"]
                    dist_txt = (f"({bb['dist_m']:,.0f}m 직선×{bb['factor']:g} = {bb['walk_m']:,.0f}m)"
                                if bb["dist_m"] else "(같은 정류장)")
                elif mixed:
                    # ★ 지하철↔버스 환승 (규칙 v0.4 · 19번 방 · rules.transfer.stop_station_walk).
                    #   v0.3.1 까지는 tw.lookup 이 지하철↔지하철만 타서 **도보 0분 + 길찾기 1분**으로
                    #   붙였고 정류장↔역 근접을 아예 보지 않았다 — MIX-04 가 성수 정류장 → 여의도역 환승을
                    #   1분으로 성립시켰다. 정류장 좌표 ↔ 그 역의 가장 가까운 출구(OSM, 추정) 직선거리로 잰다.
                    ss = self._stop_station_walk(prev_leg, leg, party)
                    label, tg, twarn, tev = ss["label"], ss["grade"], ss["warnings"], ss["evidence"]
                    if ss["verdict"] != "feasible":
                        warns += twarn
                        ev += tev
                        code = "transfer_walk" if ss["verdict"] == "infeasible" else "no_data"
                        legs.append(LegResult(i, label, ss["verdict"], ss["reason"],
                                              grade=tg, warnings=twarn, evidence=list(tev), code=code, worst=worst))
                        return fail(ss["verdict"], ss["reason"], code, ss["relief"], "transfer_walk", i, leg, now)
                    walk = ss["walk_min"]
                    dist_txt = (f"({ss['dist_m']:,.0f}m 직선×{ss['factor']:g} = {ss['walk_m']:,.0f}m)"
                                if ss["dist_m"] is not None else "")
                elif leg.get("line") and prev_leg.get("line") and self._other_station(st, prev_line, cur_line):
                    # ★ 55 GPT #1 — 이름만 같은 다른 역(경의선 양평 ↔ 5호선 양평 53.6 km)은 환승이 아니다. 후보 생성기를
                    #   거치지 않는 legs 입력도 여기서 막는다(종전: 도보 0분 + 길찾기 1분으로 성립).
                    why = f"{st} 의 {prev_line} 역과 {cur_line} 역은 이름만 같은 다른 역이다 — 갈아탈 수 없다"
                    tev = [self._ev_rule("station_names.환승_제외_역명", "확정")]
                    ev += tev
                    legs.append(LegResult(i, f"환승 {st}", "infeasible", why, grade="확정", evidence=list(tev),
                                          code="transfer_walk", worst=worst))
                    return fail("infeasible", why, "transfer_walk",
                                f"{st} 에서는 갈아타지 않는다 — 실제 환승역을 거치는 경로로 다시 잡는다",
                                "transfer_walk", i, leg, now)
                else:
                    # 지하철↔지하철(버스↔버스는 위 분기 · v0.9.2). 거리표 → 없으면 같은 거리표 상위 10%(팀장 #1).
                    w = (self.tw.lookup(st, prev_line, cur_line)
                         if self.tw and leg.get("line") and not prev_line.startswith("버스") else None)
                    if w is not None and w.min is not None:
                        walk = w.min
                        tev = {"source_type": "db", "source_id": "transfer_walk_v1",
                               "grade": w.grade, "observed_at": w.checked_at, "claim": w.reason}
                        if w.basis == "station_max":
                            twarn.append(self.warn_msg("MOB_W_TRANSFER_WALK_STATION_MAX",
                                                       reason=w.reason))
                    else:
                        # ☆`[2026-09-29 문제목록 #1]` 앞 판은 여기서 0분(대형역 +2분)으로 채우고 성립시켰다.
                        #   대체 출처 = 같은 거리표의 측정 분포 상위 10%(tw.network_fallback). 거리표가 없으면 판정 불가.
                        why = w.reason if w is not None else "환승 거리표를 읽지 못했다"
                        nf = self.tw.network_fallback() if self.tw else None
                        if nf is None:
                            why = f"{label} — {why} · 대체할 거리표도 없다"
                            legs.append(LegResult(i, label, "unknown", why, grade="근거없음", code="no_data",
                                                  worst=worst))
                            return fail("unknown", why, "no_data", "환승 거리표를 확인한다", "transfer_walk", i, leg, now)
                        w = nf
                        walk = nf.min
                        tg = "추정"
                        twarn.append(self.warn_msg("MOB_W_TRANSFER_WALK_NETWORK_P90", reason=f"{why} — {nf.reason}"))
                        tev = {"source_type": "db", "source_id": "transfer_walk_v1", "grade": "추정",
                               "observed_at": nf.checked_at, "claim": nf.reason}
                    dist_txt = f"({w.distance_m:g}m)" if w and w.distance_m else ""
                    tev = [tev]
                add = math.ceil(walk + wf + int(leg.get("walk_min", 0)))
                now += add
                warns += twarn
                ev += tev
                legs.append(LegResult(
                    i, label, "feasible",
                    f"도보 {walk:g}분{dist_txt}"
                    + (f" + 길찾기 {wf}분" if wf else " (초행 아님)")
                    + f" = +{add}분", grade=tg, warnings=twarn, worst=worst))
            r = (self.verify_leg_bus(i, leg, now, day_type, worst=worst) if mode == "bus"
                 else self._cached_leg(("bike", i, json.dumps(leg, sort_keys=True, ensure_ascii=False), now),
                                       lambda: self.verify_leg_bike(i, leg, now, day_type, party, case.get("bike_live")))
                 if mode == "bike"
                 else self.verify_leg(i, leg, now, day_type, is_sat, worst=worst, party=party))
            r.worst = worst
            legs.append(r)
            warns += r.warnings
            ev += r.evidence
            if r.verdict != "feasible":
                return fail(r.verdict, r.reason, r.code or OUT_OF[r.verdict][1], r.relief, "leg", i, leg, now)
            if r.arrive_min is None:                        # 소요 판단 불가
                # 설계서: "카카오 실패 → 성립 여부는 내고 소요는 판단 불가".
                # 마지막 구간이고 도착 제약이 없으면 **성립**으로 내되 도착 시각을 만들지 않는다.
                # 뒤에 구간이 더 있거나 도착 시각을 대조해야 하면 그때는 근거없음이다.
                tail = (i == len(case["legs"]) - 1) and arrive_by is None
                if mode == "bike":
                    warns.append(self.warn_msg("MOB_W_WORST_NO_SPREAD", what=f"{r.label} 소요")) if worst else None
                reason = ((f"{r.label} 은 성립한다(대여소·{fmt_min(r.depart_min)} 출발) — {BIKE_NO_ROUTE}: 승차 소요를 낼 수 없어 도착 시각은 내지 않는다"
                           if mode == "bike" else
                           f"{r.label} 은 그 시각 편성이 있다({fmt_min(r.depart_min)} 출발) — "
                           f"승차 소요를 낼 수 없어 도착 시각은 내지 않는다")
                          if tail else
                          f"{r.label} 의 승차 소요를 낼 수 없어{'(' + BIKE_NO_ROUTE + ')' if mode == 'bike' else ''} 이후 구간의 시각을 이어 갈 수 없다 "
                          f"(그 구간 편성은 있다: {fmt_min(r.depart_min)} 출발)")
                # ☆`[2026-09-29 문제목록 #65]` 앞 판은 마지막 구간이고 도착 기한이 없으면 도착 시각 없이 「성립」을 냈다.
                #   소요를 모르는 것은 데이터 결함이다(결정 15) — 끝 구간이어도 판정 불가로 올린다.
                #   (일정 계산 plan() 은 늘 도착 기한을 주므로 원래도 이 길을 타지 않았다 — 직접 부른 경우만 바뀐다)
                return fail("unknown", reason, "no_data", None, "leg", i, leg, now)
            if worst and mode == "bike":
                warns.append(self.warn_msg("MOB_W_WORST_NO_SPREAD", what=f"{r.label} 소요"))
            now = r.arrive_min
            prev_line = "자전거" if mode == "bike" else (leg.get("line") or f"버스{leg.get('route')}")
        return {"ok": True, "verdict": "feasible", "code": None, "reason": None, "relief": None,
                "legs": legs, "warns": warns, "ev": ev, "arrive": now, "transfers": transfers,
                "fail_idx": None, "fail_leg": None, "fail_now": None, "fail_kind": None, "no_arrive": False}

    def _cached_leg(self, key, fn):
        """자동차·자전거 구간은 라우터·실시간 조회(HTTP)를 부른다 — 한 케이스 안(마지막 성립 출발 역산이 같은 구간을
        수백 번 되묻는다)에서는 (구간, 시각)마다 한 번만 묻는다. 캐시는 verify_case 가 매 건 비운다(저장 없음)."""
        if key not in self._leg_cache:
            self._leg_cache[key] = fn()
        r = self._leg_cache[key]
        import copy
        return copy.copy(r)

    def _last_feasible_depart(self, case, now, arrive_by, buffer_min, day_type, is_sat, party):
        """마지막 성립 출발 시각 — **같은 worst 판정기**로 출발 시각을 뒤에서부터 역산한다(rules judgment.last_feasible_depart).

        후보 출발 시각: 첫 구간이 지하철이면 그 방향 편성 목록(candidates)의 출발 시각(막차부터 내려온다),
        버스·자동차·자전거면 상한(도착 목표 · 버스 막차)에서 1분씩 첫차까지. 상한이 없으면(도착 목표도 막차도 없음) None.
        ★ 요청 시각(now)으로 자르지 않는다 — 불가일 때 「언제 출발했어야 했나」가 이 값이고(규칙 14 · 앞단이 당긴다),
          성립일 때는 저절로 now 이상이 나온다.
        성립 = worst 통과가 끝까지 가고 · 도착 목표가 있으면 최악 도착 + 버퍼 ≤ 목표 · 환승 상한 안.
        """
        legs = case["legs"]
        if not legs or not self.lfd_enabled:
            return None
        first = legs[0]
        mode = leg_mode(first)
        if mode in ("car", "taxi", "bike"):
            # 시간표가 없는 수단은 「마지막 편」이 없다 — 역산할 후보 목록이 없고, 분 단위로 훑으면 라우터를 수백 번 부른다. None.
            return None
        hi = arrive_by
        unverified_top = None                   # 73 후속 — 막차 통과를 확인 못 한 버스 상한(여기에 걸린 역산값은 비운다)
        lim, _ = self._party_limit(party)
        if mode == "subway":
            cands, _, _ = self.candidates(first["line"], first["from"], first["to"], day_type)
            times = sorted({d.min for d, _v, _f in cands if hi is None or d.min <= hi}, reverse=True)
        else:
            lo = 0
            if mode == "bus" and self.bus is not None:
                r = self.bus.route(str(first["route"]))
                top = r.last_min if (r is not None and r.last_min is not None) else None
                # ☆`[73 후속 · v0.9.2 · GPT Q3]` 상한은 판정과 **같은 모델** — 승차 정류장의 막차 통과 추정(빠른 쪽).
                #   기점이면 기점 막차. 중간 정류장에서 추정을 못 내면(UNCHECKED) 판정처럼 기점 막차를 보수적 상한으로 두되,
                #   역산 결과가 **그 상한에 걸린 값**이면 비운다(unverified_top) — 기점 막차를 그 정류장 시각처럼 내보이지 않는다
                #   (팀장 판은 기점 27:25 를 내고 27:26 요청을 성립시켰다 · NIGHT-10). 다른 구간(지하철 막차 등)에 걸린 값은 낸다.
                if top is not None:
                    a = self._bus_stop_row(first, "from")
                    stops0 = self.bus.stops.get(r.route_id) if a is not None else None
                    if a is not None and stops0 and a["seq"] != stops0[0]["seq"]:
                        md0 = self.rv("bus", "구간_프로파일", "min_days") if self.bus_prof is not None else None
                        early = last_pass_early(self.bus_prof, r.route_id, stops0, a["seq"], r.last_min,
                                                self._bus_day_types(day_type), md0)
                        if early is None:
                            unverified_top = top
                        else:
                            top = early
                    elif a is None or not stops0:
                        unverified_top = top
                hi = top if hi is None else (min(hi, top) if top is not None else hi)
                if r is not None and r.first_min is not None:
                    lo = r.first_min
            if hi is None:
                return None
            times = range(int(hi), int(lo) - 1, -1)
        cap = self.rv("judgment", "last_feasible_depart", "max_probe")
        for n, t in enumerate(times):
            if n >= cap:
                self.lfd_capped = True          # 「없다」가 아니라 「못 찾았다」 — verify_case 가 경고로 드러낸다
                break
            w = self._chain(case, t, day_type, is_sat, party, worst=True)
            if not w["ok"] or w["arrive"] is None or w["transfers"] > lim:
                continue
            if arrive_by is not None and w["arrive"] + buffer_min > arrive_by:
                continue
            if unverified_top is not None and t >= unverified_top:
                return None                     # 확인 못 한 막차 상한에 걸린 값 — 「늦어도」를 비운다(73 후속 · GPT Q3)
            return t
        return None

    def _out(self, res, case, depart_min, arrive_by):
        """밖으로 나가는 판(v0.8). 판정 둘 · 이유 코드 · 예정/최악 도착 · @ · 여유 · 마지막 성립 출발 · 소요 셋.
        등급·내부 판정·근거는 여기 없다. 모르는 값은 키를 뺀다(출력 스펙 v1 §2)."""
        v, dflt = OUT_OF[res.verdict]
        code = res.code or dflt
        if v == "feasible" and res.arrive_min is None and res.candidates is None:
            # 성립인데 예정 시각을 못 낸다(소요 근거없음) — 밖으로는 「예정 + @」를 낼 수 없으니 불가(no_data)다.
            #   multi 케이스는 예정 시각이 후보마다 있으므로(out.candidates) 이 규칙을 안 탄다.
            v, code = "infeasible", "no_data"
        o = {"verdict": v, "depart_min": depart_min}
        if code:
            o["code"] = code
            o["reason"] = res.reason
        if res.relief:
            o["relief"] = res.relief
        for k in ("arrive_min", "arrive_worst_min", "margin_min", "slack_min", "eta_min", "eta_worst_min",
                  "buffer_min", "p90_eta_min", "last_feasible_depart_min"):
            val = getattr(res, k)
            if val is not None:
                o[k] = val
        if arrive_by is not None:
            o["arrive_by_min"] = arrive_by
        return o

    def verify_case(self, case):
        if case.get("multi"):
            return self.verify_multi(case)
        d = _date.fromisoformat(case["date"])
        self._case_date = d
        day_type = day_type_of(d, self.holidays)
        is_sat = d.weekday() == 5
        stage = case.get("stage", "planning")
        buffer_min = self.rv("buffer", "by_stage", stage)
        party = case.get("party", {}) or {}

        # ★ 이슈 조건. 코어의 current_state.replan 자리이고 지금은 케이스 JSON 이 대신 준다.
        #   모르는 kind 를 조용히 무시하지 않는다 — 무시하면 "이슈를 넣었는데 판정이 그대로"가 된다.
        self.disr = case.get("disruptions") or []
        for x in self.disr:
            if x.get("kind") not in self.DISR_KINDS:
                raise CaseInputError(f"[{case.get('id')}] 모르는 이슈 kind 다: {x.get('kind')!r} "
                                 f"(쓸 수 있는 것: {', '.join(self.DISR_KINDS)})")
            if x["kind"] == "edge_closed" and len(x.get("between") or []) != 2:
                raise CaseInputError(f"[{case.get('id')}] edge_closed 는 between 에 두 역이 필요하다")
            if x["kind"] == "stop_skip":
                if self.ars_norm(x.get("ars")) is None:
                    raise CaseInputError(f"[{case.get('id')}] stop_skip 은 ars 에 정류장 번호 5자리가 필요하다: {x.get('ars')!r}")
                w = x.get("window")
                if w is not None and (len(w) != 2 or to_service_min(w[0]) is None or to_service_min(w[1]) is None
                                      or to_service_min(w[0]) > to_service_min(w[1])):
                    raise CaseInputError(f"[{case.get('id')}] stop_skip window 는 [\"HH:MM\", \"HH:MM\"](앞 ≤ 뒤)이다: {w!r}")
            if x["kind"] in ("route_closed", "route_detour") and not x.get("route"):
                raise CaseInputError(f"[{case.get('id')}] {x['kind']} 는 route(노선 번호)가 필요하다")

        now = to_service_min(case.get("depart_at"), ceil_seconds=True)   # #12 초는 올린다
        if now is None:
            raise CaseInputError(f"[{case.get('id')}] depart_at 이 없다. 도착 역산은 아직 미구현이다.")
        arrive_by = to_service_min(case.get("arrive_by"))
        no_alt = case.get("no_alternatives")
        self._leg_cache = {}
        self.lfd_capped = False

        def alt_for(res, ch, worst=False):
            """불가 구간의 대안 열거 — 종전 규칙 그대로(정류장↔역 환승 불가는 택시만). worst 에서 막혔으면 대안도 worst 로."""
            if ch["verdict"] != "infeasible" or no_alt:
                return
            if ch["fail_kind"] == "transfer_walk":
                res.alternatives, res.alt_tried, res.taxi = [], [], self._taxi()
            elif ch["fail_kind"] == "leg":
                res.alternatives, res.alt_tried, res.taxi = self.alternatives(
                    case, ch["fail_idx"], ch["fail_leg"], ch["fail_now"], day_type, is_sat, ch["legs"][-1], worst=worst)

        def merged(best, worst):
            """best 통과 위에 worst 통과의 경고·근거를 겹치지 않게 얹는다."""
            seen = {x["code"] for x in best["warns"]}
            return dict(best, warns=best["warns"] + [w for w in worst["warns"] if w["code"] not in seen],
                        ev=best["ev"] + [e for e in worst["ev"] if e not in best["ev"]])

        def lfd_of():
            v = self._last_feasible_depart(case, now, arrive_by, buffer_min, day_type, is_sat, party)
            return v

        def finish(ch, verdict, reason, code, arrive, slack, relief, extra_ev=(), legs_worst=None):
            res = self._finish(case, day_type, ch["legs"], verdict, reason,
                               worst_grade(*[x.grade for x in ch["legs"]]) if ch["legs"] else "근거없음",
                               arrive, slack, relief, ch["warns"], ch["ev"] + list(extra_ev))
            res.code = code
            res.buffer_min = buffer_min
            res.legs_worst = legs_worst
            return res

        # ① best 통과 — 예정 시각. 여기서 막히면 종전과 같은 자리에서 같은 이유로 불가·근거없음이다.
        best = self._chain(case, now, day_type, is_sat, party, worst=False)
        if not best["ok"]:
            res = finish(best, best["verdict"], best["reason"], best["code"], None, None, best["relief"])
            res.verdict_best = best["verdict"]
            alt_for(res, best)
            res.last_feasible_depart_min = lfd_of()
            return self._seal(res, case, now, arrive_by)
        if best["no_arrive"]:
            res = finish(best, "feasible", best["reason"], None, None, None, None)
            res.verdict_best = "feasible"
            return self._seal(res, case, now, arrive_by)

        # ② 동행 상한 — 성립하더라도 이 일행에게 무리인가(환승 수는 best·worst 가 같다)
        lim, which = self._party_limit(party)
        if best["transfers"] > lim:
            res = finish(best, "rejected_by_limit",
                         f"환승 {best['transfers']}회로 상한({which} {lim}회)을 넘는다 — 성립하지만 이 일행에게는 무리다",
                         "over_limit", best["arrive"], None, "환승이 적은 노선으로 교체",
                         extra_ev=[self._ev_rule(f"limits.transfers.{which}", "추정")])
            res.verdict_best = "feasible"
            res.eta_min = best["arrive"] - now
            return self._seal(res, case, now, arrive_by)

        # ③ worst 통과 — 판정은 여기서 한다. best 가 성립해도 worst 가 막차·첫차를 놓치면 불가다(규칙 14 — 막차는 값이다).
        worst = self._chain(case, now, day_type, is_sat, party, worst=True)
        ev_j = [self._ev_rule("judgment.worst", "확정"), self._ev_rule(f"buffer.by_stage.{stage}", "추정")]
        lfd = lfd_of()
        if worst["ok"] and worst["arrive"] is None:
            # best 는 도착을 냈는데 worst 통과가 도착을 못 냈다 — 최악 경로의 소요를 모르는 것이다(스프레드 없음 취급).
            #   최악 도착 = 예정 도착으로 두고 경고로 드러낸다. 성립 판정은 아래 공통 경로로 간다.
            worst = dict(worst, arrive=best["arrive"],
                         warns=worst["warns"] + [self.warn_msg("MOB_W_WORST_NO_SPREAD", what="최악 통과의 소요")])
        if not worst["ok"]:
            # best 성립 · worst 불가(또는 근거없음). 예정 시각은 best 것을 그대로 남긴다 — 「됐을 수도 있다」가 아니라
            #   「최악값으로는 안 된다」이므로 판정은 불가다. 대안은 worst 가 막힌 구간·시각으로 **같은 최악 가정**으로 연다.
            ch = dict(merged(best, worst), verdict=worst["verdict"], code=worst["code"], reason=worst["reason"],
                      relief=worst["relief"], fail_idx=worst["fail_idx"], fail_leg=worst["fail_leg"],
                      fail_now=worst["fail_now"], fail_kind=worst["fail_kind"])
            reason = f"최악값으로는 {worst['reason']}" if worst["reason"] else "최악값으로는 성립하지 않는다"
            relief = (f"늦어도 {fmt_min(lfd)} 출발이면 최악값으로도 성립 · 안 되면 택시" if lfd is not None
                      else (worst["relief"] or "출발을 당기거나 택시"))
            res = finish(ch, worst["verdict"], reason, worst["code"], best["arrive"], None,
                         relief, extra_ev=ev_j, legs_worst=worst["legs"])
            res.verdict_best = "feasible"
            res.warnings.append(self.warn_msg("MOB_W_BEST_OK_WORST_FAIL",
                                              best=fmt_min(best["arrive"]), why=(worst["reason"] or "")[:60]))
            res.eta_min = best["arrive"] - now
            res.last_feasible_depart_min = lfd
            alt_for(res, ch, worst=True)
            return self._seal(res, case, now, arrive_by)

        arrive_best, arrive_worst = best["arrive"], worst["arrive"]
        if arrive_worst < arrive_best:
            # ★ 41(적용 2차 GPT 5) — 구간 안 보정은 worst 경로의 도착 시각 기준이라, 앞 구간에서 best 가 더 일찍 닿아 혼잡한 칸을
            #   만나면 worst 가 먼저 도착할 수 있다. 케이스 끝에서 한 번 더 맞춘다(동일 요청의 시나리오 도착 역전 보정).
            worst = dict(worst, warns=worst["warns"] + [self.warn_msg("MOB_W_WORST_BEFORE_BEST",
                                                                       best=fmt_min(arrive_best), worst=fmt_min(arrive_worst))])
            arrive_worst = arrive_best
        margin = (arrive_worst - arrive_best) + buffer_min
        slack = None if arrive_by is None else arrive_by - arrive_best - margin
        ch = merged(best, worst)
        txt_at = f"예정 {fmt_min(arrive_best)} +@{margin}분(최악 {fmt_min(arrive_worst)} + 버퍼 {buffer_min}분)"
        if slack is not None and slack < 0:
            res = finish(ch, "infeasible",
                         f"{txt_at} 이 필요 시각 {fmt_min(arrive_by)} 를 {-slack}분 넘긴다",
                         "arrive_late", arrive_best, slack,
                         # ★ 역산으로 확인한 시각만 「성립」으로 말한다. lfd 가 없으면 하루 어느 출발도 안 맞는 것이다
                         #   (첫차보다 이른 목표 등) — 「N분 당기면 성립」은 검증 안 된 안내라 내지 않는다(GPT 대조 2026-09-24 #1).
                         (f"늦어도 {fmt_min(lfd)} 출발이면 성립 · 안 되면 택시" if lfd is not None else
                          ("역산이 상한에 걸려 성립 출발 시각을 못 찾았다 · 택시" if self.lfd_capped else
                           "출발을 당겨도 맞는 편이 없다(첫차 이후로는 못 맞춘다) · 택시")),
                         extra_ev=ev_j, legs_worst=worst["legs"])
            res.verdict_best = "feasible"
        else:
            res = finish(ch, "feasible", txt_at + (f" · 여유 {slack}분" if slack is not None else ""),
                         None, arrive_best, slack, None, extra_ev=ev_j, legs_worst=worst["legs"])
            res.verdict_best = "feasible"
        res.arrive_worst_min = arrive_worst
        res.margin_min = margin
        res.eta_min = arrive_best - now
        res.eta_worst_min = arrive_worst - now
        res.p90_eta_min = self._bus_p90_eta(case, best["legs"], worst["legs"], res.eta_min)
        res.last_feasible_depart_min = lfd
        if res.verdict == "infeasible" and not no_alt:
            # 늦는 구간은 「출발 시각 이동」 대안이 뜻이 없다(더 늦어진다) — 택시만 열어 둔다.
            res.alternatives, res.alt_tried, res.taxi = [], [], self._taxi(
                self._pt(case["legs"][0].get("line"), case["legs"][0]["from"]),
                self._pt(case["legs"][-1].get("line"), case["legs"][-1]["to"]), now)
        return self._seal(res, case, now, arrive_by)

    def _bus_p90_eta(self, case, best_legs, worst_legs, eta_min):
        """p90_eta_min(v0.9 · 41) — **버스 한 구간짜리 케이스**에서 그 구간이 온전한 프로파일로 셈해졌을 때만
        = eta_min + (worst 승차 − best 승차). 뜻은 「버스 승차 소요 p90 가산 추정」이다 — 날짜별 시간대 평균의 분위라
        개별 운행 p90 도 전체 이동 p90 도 아니다(적용 GPT 9). 대기 차이(배차 절반↔전부)는 넣지 않는다."""
        modes = [leg_mode(l) for l in case.get("legs") or []]
        if modes != ["bus"]:
            # 적용 GPT 9 — 환승이 끼면 버스 지연이 다음 편 놓침으로 번지는데 이 산식은 승차 차이만 더한다.
            #   전체 이동의 p90 이라 부를 수 없으므로 버스 한 구간짜리 케이스에만 낸다.
            return None
        bb = [l for l in best_legs if l.ride_src is not None]
        ww = [l for l in worst_legs if l.ride_src is not None]
        if not bb or len(bb) != len(ww) or any(l.ride_src != "profile" or l.ride_min is None for l in bb + ww):
            return None
        return eta_min + max(0, math.ceil(sum(w.ride_min for w in ww) - sum(b.ride_min for b in bb)))

    def _seal(self, res, case, depart_min, arrive_by):
        """밖 판(out)을 붙이고 역산 상한 경고를 단다 — verify_case 의 모든 출구가 여기를 지난다."""
        if self.lfd_capped:
            res.warnings.append(self.warn_msg("MOB_W_LFD_CAP", n=self.rv("judgment", "last_feasible_depart", "max_probe")))
        res.out = self._out(res, case, depart_min, arrive_by)
        return res

    def _finish(self, case, day_type, legs, verdict, reason, grade,
                arrive, slack, relief, warns, ev):
        return CaseResult(case.get("id", "?"), verdict, reason, grade, legs,
                          arrive, slack, relief, warns, ev, day_type)


# ── 명령줄 · 회귀 대조 · 화면 출력은 verify_time_cli.py 로 옮겼다(문제목록 #58 · 2026-10-04) ──────────
# 옛 이름으로 부르던 곳(시험 · 스크립트)이 그대로 돌도록 첫 접근 때 새 파일에서 가져온다 — 서비스 경로는 이 파일을 안 연다.
_CLI_NAMES = ("show", "load_cases", "build_verifier_for_cases", "check_expect", "main", "MARK", "OUT_MARK")


def __getattr__(name):
    if name in _CLI_NAMES:
        from . import verify_time_cli
        return getattr(verify_time_cli, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    from .verify_time_cli import main
    main()
