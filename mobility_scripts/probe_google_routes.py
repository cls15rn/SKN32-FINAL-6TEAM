"""Bounded Routes API capability check; never persist route content or secrets.

Run explicitly: python mobility_scripts/probe_google_routes.py --live
Outputs request metadata and capability/check flags only, not route snapshots.
Not a production adapter, transit accuracy benchmark, or model evaluation dataset.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
FIELDS = ','.join([
    'routes.duration', 'routes.distanceMeters',
    'routes.legs.steps.travelMode', 'routes.legs.steps.distanceMeters',
    'routes.legs.steps.staticDuration', 'routes.legs.steps.transitDetails',
    'routes.legs.steps.startLocation', 'routes.legs.steps.endLocation',
])


def read_key():
    key = os.environ.get('GOOGLE_MAPS_API_KEY')
    if key:
        return key
    for line in (ROOT / '.env').read_text(encoding='utf-8-sig').splitlines():
        name, sep, value = line.strip().partition('=')
        if sep and name.strip() == 'GOOGLE_MAPS_API_KEY':
            return value.strip().strip('"').strip("'")
    raise SystemExit('GOOGLE_MAPS_API_KEY is missing')


def point(lat, lng):
    return {'location': {'latLng': {'latitude': lat, 'longitude': lng}}}


def sec(value):
    return float(value.removesuffix('s'))


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def inspect(route, request):
    steps = [s for leg in route.get('legs', []) for s in leg.get('steps', [])]
    transit = [s for s in steps if s.get('travelMode') == 'TRANSIT']
    walking = [s for s in steps if s.get('travelMode') == 'WALK']
    flags = {
        'has_transit': bool(transit),
        'has_walk': bool(walking),
        'walk_field_omissions': [
            {'missing': [k for k in ('distanceMeters', 'staticDuration') if k not in s],
             'same_endpoints': s.get('startLocation') == s.get('endLocation')
                if 'startLocation' in s and 'endLocation' in s else None}
            for s in walking if 'distanceMeters' not in s or 'staticDuration' not in s],
        'has_transfer': len(transit) > 1,
        'walk_metrics_present': bool(walking) and all(
            'distanceMeters' in s and 'staticDuration' in s for s in walking),
        'route_metrics_present': 'duration' in route and 'distanceMeters' in route,
        'transit_times_complete': bool(transit) and all(
            all(k in s.get('transitDetails', {}).get('stopDetails', {})
                for k in ('departureTime', 'arrivalTime')) for s in transit),
        'contains_non_ascii_transit_text': any(
            not json.dumps(s.get('transitDetails', {}), ensure_ascii=False).isascii()
            for s in transit),
        'bus_returned': any(s.get('transitDetails', {}).get('transitLine', {})
            .get('vehicle', {}).get('type') in ('BUS', 'INTERCITY_BUS', 'TROLLEYBUS') for s in transit),
        'rail_returned': any(s.get('transitDetails', {}).get('transitLine', {})
            .get('vehicle', {}).get('type') in ('SUBWAY', 'METRO_RAIL', 'HEAVY_RAIL', 'COMMUTER_TRAIN', 'RAIL', 'HIGH_SPEED_TRAIN') for s in transit),
    }
    if steps and 'duration' in route and all('staticDuration' in s for s in steps):
        flags['total_exceeds_step_sum'] = sec(route['duration']) > sum(sec(s['staticDuration']) for s in steps) + 1
    if flags['transit_times_complete']:
        previous = None
        ordered = True
        for s in steps:
            if s.get('travelMode') == 'TRANSIT':
                stops = s['transitDetails']['stopDetails']
                dep, arr = instant(stops['departureTime']), instant(stops['arrivalTime'])
                ordered &= arr >= dep and (previous is None or dep >= previous)
                previous = arr
            elif previous is not None and 'staticDuration' in s:
                previous += timedelta(seconds=sec(s['staticDuration']))
        flags['connections_chronological'] = ordered
        if 'arrivalTime' in request and previous is not None:
            flags['arrives_by_requested_time'] = previous <= instant(request['arrivalTime'])
        if 'departureTime' in request:
            first = instant(transit[0]['transitDetails']['stopDetails']['departureTime'])
            flags['first_boarding_after_requested_time'] = first >= instant(request['departureTime'])
            flags['first_boarding_more_than_2h_later'] = first - instant(request['departureTime']) > timedelta(hours=2)
            if previous is not None and 'duration' in route:
                flags['requested_to_arrival_exceeds_route_duration'] = (
                    previous - instant(request['departureTime'])).total_seconds() > sec(route['duration']) + 1
    return flags


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live', action='store_true', help='Send up to 12 potentially billable API requests; no retries')
    ap.add_argument('--case', action='append', help='Run only this named case (repeatable)')
    ap.add_argument('--base-date', help='Explicit test date YYYY-MM-DD in KST; default tomorrow')
    args = ap.parse_args()
    if not args.live:
        ap.print_help()
        return
    key = read_key()
    coords = json.loads((ROOT.parent / 'data/travel/processed/mobility/station_coords.json').read_text(encoding='utf-8'))['stations']
    def station(name):
        value = coords[name]
        return point(value['lat'], value['lng'])
    seoul = point(37.5547, 126.9706)
    palace = point(37.5796, 126.9770)
    myeongdong = point(37.5636, 126.9869)
    hongdae = point(37.5570, 126.9240)
    jamsil = point(37.5133, 127.1001)
    tomorrow = (datetime.now(KST) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    if args.base_date:
        tomorrow = datetime.fromisoformat(args.base_date).replace(hour=14, tzinfo=KST)
    weekend = tomorrow + timedelta(days=(5 - tomorrow.weekday()) % 7)
    cases = [
        ('city_departure_alternatives', seoul, palace, 'TRANSIT', tomorrow, 'departureTime', {}),
        ('city_arrival', seoul, palace, 'TRANSIT', tomorrow, 'arrivalTime', {}),
        ('cross_city_rail_preference', hongdae, jamsil, 'TRANSIT', tomorrow, 'departureTime', {'allowedTravelModes': ['RAIL']}),
        ('airport_t1_public_station', station('공항철도|인천공항1터미널'), myeongdong, 'TRANSIT', tomorrow, 'arrivalTime', {}),
        ('airport_t2_rail_preference', station('공항철도|인천공항2터미널'), seoul, 'TRANSIT', tomorrow, 'departureTime', {'allowedTravelModes': ['RAIL']}),
        ('late_night_rail_preference', hongdae, jamsil, 'TRANSIT', tomorrow.replace(hour=2), 'departureTime', {'allowedTravelModes': ['RAIL']}),
        ('weekend_fewer_transfers', hongdae, palace, 'TRANSIT', weekend, 'departureTime', {'routingPreference': 'FEWER_TRANSFERS'}),
        ('future_less_walking', seoul, palace, 'TRANSIT', tomorrow + timedelta(days=30), 'departureTime', {'routingPreference': 'LESS_WALKING'}),
        ('airport_return_deadline', myeongdong, station('공항철도|인천공항1터미널'), 'TRANSIT', tomorrow.replace(hour=7), 'arrivalTime', {}),
        ('walk_city', seoul, point(37.5663,126.9779), 'WALK', None, None, {}),
        ('walk_short', palace, point(37.5720,126.9769), 'WALK', None, None, {}),
        ('drive_city', seoul, jamsil, 'DRIVE', None, None, {}),
    ]
    for name, origin, destination, mode, when, timefield, preferences in cases:
        if args.case and name not in args.case:
            continue
        body = {'origin': origin, 'destination': destination, 'travelMode': mode, 'languageCode': 'en'}
        if when:
            body[timefield] = when.isoformat()
            body['computeAlternativeRoutes'] = True
        if preferences:
            body['transitPreferences'] = preferences
        result = {'case': name, 'checked_at': datetime.now(KST).isoformat(), 'request': body}
        request = Request('https://routes.googleapis.com/directions/v2:computeRoutes',
            data=json.dumps(body).encode(), method='POST', headers={
                'Content-Type': 'application/json', 'X-Goog-Api-Key': key, 'X-Goog-FieldMask': FIELDS})
        try:
            with urlopen(request, timeout=25) as response:
                data = json.load(response)
            routes = data.get('routes', [])
            result.update(http_status=200, has_routes=bool(routes), has_alternatives=len(routes) > 1,
                          checks=[inspect(route, body) for route in routes])
            del data, routes
        except HTTPError as exc:
            result.update(http_status=exc.code, error='API_HTTP_ERROR')
        except (URLError, TimeoutError):
            result.update(error='NETWORK_ERROR')
            print(json.dumps(result), flush=True)
            raise SystemExit(2)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
