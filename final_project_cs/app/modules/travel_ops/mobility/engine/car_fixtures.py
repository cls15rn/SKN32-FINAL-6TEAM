# app/modules/travel_ops/mobility/engine/car_fixtures.py — 자동차 라우터의 시험·명령줄용 대역 (문제목록 #59 · 2026-10-04)
#
# car.py 에서 옮겼다. 서비스는 이 파일을 열지 않는다 — `car.make_router("fixture:<파일>" | "none")` 이 필요할 때만 불러온다.
import json
from pathlib import Path

from .car import RouterDown


class FixtureRouter:
    """시험용 — 합성 경로 파일에서 꺼낸다. 키 = 'lng,lat|lng,lat'(소수 4자리) 또는 케이스 id.
    실제 API 응답을 담는 자리가 아니다: 18번의 합성 시험(TOPIS 링크 체인)과 같은 종류의 픽스처만 둔다."""

    def __init__(self, path):
        self.doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self.routes = self.doc["routes"]
        self.calls = 0

    @staticmethod
    def key(s, e):
        return f"{s[0]:.4f},{s[1]:.4f}|{e[0]:.4f},{e[1]:.4f}"

    def route(self, s, e, profile="car", via=None):
        self.calls += 1
        r = self.routes.get(self.key(s, e))
        if r is None:
            raise RouterDown(f"픽스처에 경로가 없다: {self.key(s, e)}")
        if r.get("down"):
            raise RouterDown("픽스처가 라우터 다운을 흉내낸다")
        return r

    def info(self):
        return {"version": "fixture"}


class NoRouter:
    """라우터를 쓰지 않기로 한 실행(`make_router("none")`). 매번 RouterDown."""
    calls = 0

    def route(self, *a, **k):
        raise RouterDown("라우터 없이 실행 중(none)")

    def info(self):
        return None
