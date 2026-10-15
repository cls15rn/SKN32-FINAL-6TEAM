"""저장된 선택 구간을 지도에 연결한다. 외부 경로/API 없이 방향·식별값을 검사한다."""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domains.travel_ops.instances.mobility import route_shape as RS
from app.domains.travel_ops.instances.mobility.engine.bus import BusRoutes

A, B = (126.97, 37.56), (127.04, 37.57)
BUS_LEG = {'mode': 'bus', 'route': '405', 'route_id': 'r405',
           'from': '승차', 'to': '하차', 'from_sequence': 4, 'to_sequence': 6,
           'from_stop_id': 's4', 'to_stop_id': 's6'}
RAIL_LEG = {'line': '02호선', 'from': '시청', 'to': '아현',
            'station_path': ['시청', '반대방향경유역', '아현']}


class Router:
    def __init__(self):
        self.calls = []

    def route(self, start, end, profile):
        self.calls.append((profile, start, end))
        return {'paths': [{'points': {'coordinates': [list(start), list(end)]}}]}


class Stations:
    def get(self, line, name):
        return {'시청': {'lng': 126.98, 'lat': 37.56},
                '반대방향경유역': {'lng': 127.01, 'lat': 37.59},
                '아현': {'lng': 127.03, 'lat': 37.57},
                '최단경유역': {'lng': 127.00, 'lat': 37.56}}.get(name)


class ShortestLoop:
    def path(self, *args):
        return ['시청', '최단경유역', '아현']

    def is_loop(self, line):
        return True


def bus_data():
    route = {'route_id': 'r405', 'route_nm': '405', 'first_time': '0500',
             'last_time': '2300', 'term_min': 10}
    rows = [{'route_id': 'r405', 'seq': n, 'station_id': f's{n}',
             'station_nm': name, 'lng': lng, 'lat': lat, 'direction': '도착방면',
             'source_id': 'seoul_bus_route@test', 'fetched_at': '2026-09-10'}
            for n, name, lng, lat in [(1, '승차', 126.95, 37.55),
                                      (2, '다른방향', 126.96, 37.54),
                                      (3, '하차', 126.97, 37.53),
                                      (4, '승차', 126.98, 37.56),
                                      (5, '실제경유', 127.00, 37.59),
                                      (6, '하차', 127.03, 37.57)]]
    return BusRoutes([route], rows)


def route(planned='bus_405', uses=None):
    return {'planned': planned, 'options': [{'id': planned, 'uses': uses or ['버스:405']}]}


def context(legs, planned='bus_405'):
    return {'schema_version': 1, 'options': {planned: {'option_id': planned, 'legs': legs}}}


def item(seq, kind, point=None, detail=None):
    return SimpleNamespace(seq=seq, kind=kind, item_id=uuid4(), title=kind,
                           place={'longitude': point[0], 'latitude': point[1]} if point else None,
                           detail=detail or {})


def test_registered_bus_reads_selected_sequences_and_draws_intermediate_stop(monkeypatch):
    router = Router()
    monkeypatch.setattr(RS, '_engine', lambda: (router, Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: None)
    monkeypatch.setattr(RS, '_bus_routes', lambda: bus_data(), raising=False)
    move = item(2, 'mobility', detail={'route_def': route(),
                                      'departure_context': context([BUS_LEG])})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['source'] == 'bus_stops'
    assert shape['rides'][0]['stations'] == ['승차', '실제경유', '하차']
    assert shape['rides'][0]['from_sequence'] == 4
    assert [127.00, 37.59] in shape['line']['coordinates']
    assert all(call[0] == 'foot' for call in router.calls)
    assert shape['grade'] == '추정' and '실제 도로 형상' in shape['note']
    coords, ((h0, h1), (t0, t1)) = shape['line']['coordinates'], shape['walks']
    assert h0 == 0 and coords[h1] == [126.98, 37.56], '걸음: 출발 장소 → 탄 정류장'
    assert coords[t0] == [127.03, 37.57] and t1 == len(coords) - 1, '걸음: 내린 정류장 → 도착 장소'


def test_registered_subway_uses_planned_long_loop_instead_of_shortest(monkeypatch):
    monkeypatch.setattr(RS, '_engine', lambda: (Router(), Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: ShortestLoop())
    monkeypatch.setattr(RS, '_bus_routes', lambda: None, raising=False)
    rd = route('subway_1', ['2호선:시청', '2호선:아현'])
    move = item(2, 'mobility', detail={'route_def': rd,
                        'departure_context': context([RAIL_LEG], 'subway_1')})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['rides'][0]['stations'] == RAIL_LEG['station_path']
    assert [127.01, 37.59] in shape['line']['coordinates']
    assert '짧은 쪽' not in shape['note']


@pytest.mark.parametrize('bad', [
    {'from_stop_id': 's1'}, {'to_stop_id': 's3'}, {'route_id': 'other'},
    {'from_sequence': True}, {'to_sequence': 4}, {'route': '406'},
])
def test_bus_mismatched_identity_does_not_substitute_same_named_stops(monkeypatch, bad):
    monkeypatch.setattr(RS, '_engine', lambda: (Router(), Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: None)
    monkeypatch.setattr(RS, '_bus_routes', lambda: bus_data(), raising=False)
    move = item(2, 'mobility', detail={'route_def': route(),
                'departure_context': context([{**BUS_LEG, **bad}])})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['source'] == 'straight_line' and shape['grade'] == '근거없음'
    assert '정류장' in shape['note'] and '불일치' in shape['note']


@pytest.mark.parametrize('mutate', ['missing', 'duplicate', 'coordinate'])
def test_bus_incomplete_segment_does_not_claim_complete_route(monkeypatch, mutate):
    bus = bus_data()
    rows = bus.stops['r405']
    if mutate == 'missing':
        rows[:] = [r for r in rows if r['seq'] != 5]
    elif mutate == 'duplicate':
        rows.append(deepcopy(rows[4]))
    else:
        rows[4]['lat'] = float('nan')
    monkeypatch.setattr(RS, '_engine', lambda: (Router(), Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: None)
    monkeypatch.setattr(RS, '_bus_routes', lambda: bus, raising=False)
    move = item(2, 'mobility', detail={'route_def': route(),
                'departure_context': context([BUS_LEG])})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['source'] == 'straight_line'
    assert shape['grade'] == '근거없음'


def test_mixed_keeps_bus_then_subway_order_and_walks_only_connections(monkeypatch):
    router = Router()
    monkeypatch.setattr(RS, '_engine', lambda: (router, Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: ShortestLoop())
    monkeypatch.setattr(RS, '_bus_routes', lambda: bus_data(), raising=False)
    rd = route('bus_subway_1', ['버스:405', '2호선:시청', '2호선:아현'])
    move = item(2, 'mobility', detail={'route_def': rd,
                'departure_context': context([BUS_LEG, RAIL_LEG], 'bus_subway_1')})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['source'] == 'transit_stops'
    assert [r['mode'] for r in shape['rides']] == ['bus', 'subway']
    assert all(call[0] == 'foot' for call in router.calls)
    assert len(router.calls) == 3


@pytest.mark.parametrize('paths,arrive,expected', [
    ([(600, ['시청', '반대방향경유역', '아현'])], 604, RAIL_LEG['station_path']),
    ([(600, ['시청', '反対', '아현']), (600, ['시청', '최단경유역', '아현'])], 604, None),
    ([(600, ['시청', '반대방향경유역', '아현'])], 609, None),
    ([(590, ['시청', '최단경유역', '아현']), (600, ['시청', '반대방향경유역', '아현'])],
     604, RAIL_LEG['station_path']),
])
def test_capture_preserves_only_unique_path_matching_selected_boarding_and_arrival(paths, arrive, expected):
    from app.domains.travel_ops.instances.mobility.engine.departure_context import capture
    from app.domains.travel_ops.instances.mobility.engine.verify_time import leg_txt, Verifier as RealVerifier

    class Verifier:
        # 고른 편성 찾기(options.chosen_departures)가 쓰는 승차·예정도착 계산은 진짜 판정기 구현을 그대로 붙인다(대역에 새 계산을 만들지 않는다)
        ride_of = RealVerifier.ride_of
        _scheduled_arrival_sec = staticmethod(RealVerifier._scheduled_arrival_sec)
        _scheduled_arrival_min = RealVerifier._scheduled_arrival_min

        def __init__(self):
            self.lo = self

        def candidates(self, line, start, end, day_type):
            assert day_type == 'weekday'
            return [(SimpleNamespace(min=minute), SimpleNamespace(path=path), False)
                    for minute, path in paths], {}, False

        def travel_min_on_path(self, line, path, target):
            return 2 * path.index(target)

    leg = {k: v for k, v in RAIL_LEG.items() if k != 'station_path'}
    option = {'id': 'subway_1', 'eta_min': 10, '_start': 595, '_legs': [leg],
              '_day_type': 'weekday', '_check': {'legs': [leg]},
              '_lr': [SimpleNamespace(label=leg_txt(leg), verdict='feasible', depart_min=600, arrive_min=arrive)]}
    runtime = SimpleNamespace(_v=Verifier(), timetable_built_at='v1', rules_version='r1')
    saved = capture([option], runtime=runtime, origin={}, destination={}, stage='planning', party={},
                    first_visit=True, service_date=date(2026, 10, 12), start_min=595)['options']['subway_1']
    assert saved['station_paths'] == [expected]
    assert saved['day_type'] == 'weekday'
    assert saved['legs'] == saved['check']['legs'] == [leg]
    assert 'station_path' not in leg


def test_loop_without_selected_path_does_not_choose_shorter_direction(monkeypatch):
    monkeypatch.setattr(RS, '_engine', lambda: (Router(), Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: ShortestLoop())
    leg = {k: v for k, v in RAIL_LEG.items() if k != 'station_path'}
    move = item(2, 'mobility', detail={'route_def': route('subway_1', ['2호선:시청', '2호선:아현']),
                'departure_context': context([leg], 'subway_1')})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['rides'][0]['stations'] == ['시청', '아현']
    assert not shape['rides'][0]['direction_preserved']
    assert '순환 방향 근거가 없어' in shape['note']


def test_parallel_station_path_from_saved_context_reaches_registered_shape(monkeypatch):
    monkeypatch.setattr(RS, '_engine', lambda: (Router(), Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: ShortestLoop())
    leg = {k: v for k, v in RAIL_LEG.items() if k != 'station_path'}
    ctx = context([leg], 'subway_1')
    ctx['options']['subway_1']['station_paths'] = [RAIL_LEG['station_path']]
    move = item(2, 'mobility', detail={'route_def': route('subway_1', ['2호선:시청', '2호선:아현']),
                                     'departure_context': ctx})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['rides'][0]['stations'] == RAIL_LEG['station_path']
    assert shape['rides'][0]['direction_preserved']


@pytest.mark.parametrize('bad_context', [
    {'schema_version': True, 'options': {'bus_405': {'option_id': 'bus_405', 'legs': [BUS_LEG]}}},
    {'schema_version': 1, 'options': {'bus_405': {'option_id': 'bus_other', 'legs': [BUS_LEG]}}},
    {'schema_version': 1, 'options': {'bus_405': {'option_id': 'bus_405', 'legs': [RAIL_LEG]}}},
    {'schema_version': 1, 'options': {'bus_405': {'option_id': 'bus_405', 'legs': []}}},
])
def test_damaged_or_different_option_context_is_not_rendered_as_selected_bus(monkeypatch, bad_context):
    monkeypatch.setattr(RS, '_engine', lambda: (Router(), Stations()))
    monkeypatch.setattr(RS, '_line_order', lambda: ShortestLoop())
    monkeypatch.setattr(RS, '_bus_routes', lambda: bus_data())
    move = item(2, 'mobility', detail={'route_def': route(), 'departure_context': bad_context})
    shape, = RS.shapes_for_items([item(1, 'activity', A), move, item(3, 'dining', B)])
    assert shape['source'] == 'straight_line' and '불일치' in shape['note']
