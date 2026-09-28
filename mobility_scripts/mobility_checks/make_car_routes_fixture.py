# -*- coding: utf-8 -*-
"""자동차·택시 회귀용 **합성 경로 픽스처** 생성 (21번 방 · 2026-09-20).

    python mobility_scripts/mobility_checks/make_car_routes_fixture.py [--graph-dir <processed/mobility/graph>]
    → final_project_cs/tests/unit/travel/mobility/car_routes_fixture_v1.json

18번 방의 합성 시험 경로(test_route_teheran_up.json — TOPIS 링크 체인으로 만든 테헤란로 상행 4.0 km, GraphHopper
응답과 같은 모양)를 씨앗으로, 등급 규칙이 갈리는 변형 넷을 만든다. **실제 라우터 응답을 담는 자리가 아니다**(경로 비저장 원칙).
verify_time.py --gh-url fixture:final_project_cs/tests/unit/travel/mobility/car_routes_fixture_v1.json 이 이 파일을 라우터 대신 읽는다.

  T-ALL     원본 그대로 — 전부 TOPIS 프로파일(topis 100%) → 추정 · 경고 없음
  T-CLASS   way id 를 매칭표에 없는 값으로 바꾸고 road_class=primary → 전부 도로급 계수(class 100%) → 추정 + MOB_W_CAR_SPEED_CLASS
  T-ALLEY   road_class=residential → 전부 골목 고정속도(default 100%) → **근거없음** + MOB_W_CAR_SPEED_DEFAULT
  T-MIX     앞 절반 원본 · 뒤 절반 골목 → default 40% 대 → 추정 + MOB_W_CAR_SPEED_DEFAULT (50% 경계 아래)
  T-DOWN    라우터 다운 흉내 → 근거없음 + MOB_W_CAR_ROUTER_DOWN
  T-ALT     02호선 강변→잠실 역 좌표 키 — 택시 **대안** 경로(ALT-02 모양, CAR-11). 형상은 씨앗 그대로(합성)
키는 'lng,lat|lng,lat'(소수 4자리) — 케이스의 from/to 를 'lat,lng' 문자열로 주면 그 좌표가 키가 된다.
"""
import argparse, copy, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "final_project_cs"))
from app.modules.travel_ops.mobility.engine.car import FixtureRouter, hav  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--graph-dir")
ap.add_argument("--out", default=str(REPO / "final_project_cs" / "tests" / "unit" / "travel" / "mobility" / "car_routes_fixture_v1.json"))
a = ap.parse_args()
if a.graph_dir is None:
    from mobility_scripts.collect._paths import PROCESSED
    a.graph_dir = PROCESSED / "mobility" / "graph"
seed = json.loads((Path(a.graph_dir) / "test_route_teheran_up.json").read_text(encoding="utf-8"))
P = seed["paths"][0]
pts = P["points"]["coordinates"]
det = P["details"]["osm_way_id"]
s, e = pts[0], pts[-1]


def variant(shift_lng, way_map=None, road_class=None, split=None):
    """경로 형상은 씨앗 그대로(요청 좌표만 키에서 다르다 — 실제 라우터도 요청 좌표를 도로에 스냅한다).
    way_map: 원 way→새 way. road_class: 전체 급. split: 그 비율 뒤 edge 를 골목으로."""
    r = copy.deepcopy(seed)
    p = r["paths"][0]
    n = len(det)
    cut = n if split is None else int(n * split)
    new_det, rc = [], []
    for k, (x, y, w) in enumerate(det):
        if k >= cut:
            new_det.append([x, y, 2])                     # 매칭표에도 형상표에도 없는 way
            rc.append([x, y + 1, "residential"])
        elif way_map is not None:
            new_det.append([x, y, way_map])
            rc.append([x, y + 1, road_class])
        else:
            new_det.append([x, y, w])
    p["details"]["osm_way_id"] = new_det
    if rc:
        p["details"]["road_class"] = rc
    p["distance"] = round(sum(hav(pts[i], pts[i + 1]) for x, y, _ in det for i in range(x, y)), 1)
    p["time"] = None
    return r


routes = {
    FixtureRouter.key(s, e): variant(0.0),                                   # T-ALL
    FixtureRouter.key([s[0] + 0.01, s[1]], [e[0] + 0.01, e[1]]): variant(0.01, way_map=1, road_class="primary"),      # T-CLASS
    FixtureRouter.key([s[0] + 0.02, s[1]], [e[0] + 0.02, e[1]]): variant(0.02, way_map=2, road_class="residential"),  # T-ALLEY
    FixtureRouter.key([s[0] + 0.03, s[1]], [e[0] + 0.03, e[1]]): variant(0.03, split=0.55),                           # T-MIX
    FixtureRouter.key([s[0] + 0.04, s[1]], [e[0] + 0.04, e[1]]): {"down": True},                                      # T-DOWN
    FixtureRouter.key([127.094684, 37.535161], [127.100129, 37.513305]): variant(0.0),                              # T-ALT 강변→잠실
}
doc = {"note": "합성 경로 픽스처 — 18번 test_route_teheran_up.json(TOPIS 링크 체인) 변형. 실제 라우터 응답이 아니다. "
               "make_car_routes_fixture.py 가 만든다.",
       "built_from": "processed/mobility/graph/test_route_teheran_up.json",
       "endpoints": {"T-ALL": [s, e], "T-CLASS": [[s[0] + 0.01, s[1]], [e[0] + 0.01, e[1]]],
                     "T-ALLEY": [[s[0] + 0.02, s[1]], [e[0] + 0.02, e[1]]],
                     "T-MIX": [[s[0] + 0.03, s[1]], [e[0] + 0.03, e[1]]],
                     "T-DOWN": [[s[0] + 0.04, s[1]], [e[0] + 0.04, e[1]]],
                     "T-ALT": [[127.094684, 37.535161], [127.100129, 37.513305]]},
       "routes": routes}
Path(a.out).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
print(f"→ {a.out} · 경로 {len(routes)}개 · 씨앗 {len(det)} edge · 끝점 {s} → {e}")
for k, v in doc["endpoints"].items():
    print(f"  {k}: from '{v[0][1]:.4f},{v[0][0]:.4f}' to '{v[1][1]:.4f},{v[1][0]:.4f}'")
