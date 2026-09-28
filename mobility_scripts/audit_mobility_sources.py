"""Read-only timetable audit plus optional small OSM connectivity sample.

OSM source: https://www.openstreetmap.org/copyright (ODbL).
This is a graph availability check, not a pedestrian navigation service.
No speeds, fabricated timetable links, or straight-line detour factors are used.
"""
import argparse
import collections
import heapq
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent / 'data/travel'


def audit_local():
    sources = collections.defaultdict(collections.Counter)
    groups = collections.defaultdict(set)
    for line in (DATA/'processed/mobility/timetable_v1.jsonl').open(encoding='utf-8'):
        row = json.loads(line)
        counter = sources[row['source']]
        counter['rows'] += 1
        for field in ('train_no', 'arr_time', 'dep_time'):
            counter[field+'_present'] += bool(row.get(field))
        if row.get('train_no'):
            key = tuple(row[k] for k in ('source', 'line', 'day_type', 'dir', 'train_no'))
            groups[key].add(row['station_key'])
    return {
        'sources': sources,
        'train_groups': len(groups),
        'groups_with_multiple_stations': sum(len(s)>1 for s in groups.values()),
        'seoul_bus_route_count': json.loads((DATA/'raw/mobility/tago_bus_routes_11.json').read_text(encoding='utf-8'))['count'],
        'warning': 'Matching train numbers alone do not validate continuity or service calendars.',
    }


def distance(a, b):
    lat1, lat2 = map(math.radians, (a['lat'], b['lat']))
    dlat = lat2-lat1
    dlon = math.radians(b['lon']-a['lon'])
    x = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371000*2*math.asin(min(1, math.sqrt(x)))


def audit_osm():
    # Approx. 1km square around Gwanghwamun. One read-only request, at most 10MB.
    query = '[out:json][timeout:25];way[highway](37.568,126.970,37.580,126.982);out geom;'
    req = Request('https://overpass-api.de/api/interpreter',
                  data=urlencode({'data': query}).encode(),
                  headers={'User-Agent': 'Mobility-feasibility-check/1.0'})
    with urlopen(req, timeout=40) as response:
        raw = response.read(10_000_001)
    if len(raw)>10_000_000:
        raise ValueError('OSM response exceeds bounded sample size')
    data = json.loads(raw)
    if data.get('remark'):
        raise ValueError('Overpass returned an incomplete result')
    graph = collections.defaultdict(list)
    points = {}
    selected = 0
    for way in data['elements']:
        tags = way.get('tags', {})
        # Intentionally conservative subset; omitting other roads is not evidence
        # that walking is impossible. Conditional access/barriers need OTP/full QA.
        if tags.get('highway') not in ('footway', 'pedestrian', 'path', 'steps', 'living_street'):
            continue
        if tags.get('access') in ('private', 'no') or tags.get('foot') in ('private', 'no') or tags.get('area') == 'yes':
            continue
        if 'access:conditional' in tags or 'foot:conditional' in tags:
            continue
        nodes, geom = way.get('nodes', []), way.get('geometry', [])
        if len(nodes) != len(geom):
            continue
        selected += 1
        points.update(zip(nodes, geom))
        for i in range(len(nodes)-1):
            a,b = nodes[i:i+2]
            weight = distance(geom[i],geom[i+1])
            direction = tags.get('oneway:foot')
            if direction != '-1':
                graph[a].append((b, weight))
            if direction not in ('yes','1','true'):
                graph[b].append((a, weight))
    sample = []
    # These are approximate public-space points; snapping does not validate access.
    for label, a, b in [
        ('gwanghwamun_square', {'lat':37.5720,'lon':126.9769}, {'lat':37.5750,'lon':126.9769}),
        ('square_to_palace_front', {'lat':37.5720,'lon':126.9769}, {'lat':37.5760,'lon':126.9768}),
    ]:
        if not graph:
            sample.append({'case':label,'connected':False})
            continue
        start=min(graph,key=lambda n:distance(a,points[n]))
        end=min(graph,key=lambda n:distance(b,points[n]))
        queue=[(0,start)]; seen=set(); path_distance=None
        while queue:
            cost,node=heapq.heappop(queue)
            if node in seen:
                continue
            seen.add(node)
            if node==end:
                path_distance=cost; break
            for nxt,length in graph[node]:
                if nxt not in seen:
                    heapq.heappush(queue,(cost+length,nxt))
        sample.append({'case':label,'connected':path_distance is not None,
                       'mapped_path_m':round(path_distance,1) if path_distance is not None else None,
                       'start_snap_m':round(distance(a,points[start]),1),
                       'end_snap_m':round(distance(b,points[end]),1)})
    return {'attribution':'© OpenStreetMap contributors, ODbL 1.0',
            'license':'https://www.openstreetmap.org/copyright',
            'query':query,'osm_timestamp':data.get('osm3s',{}).get('timestamp_osm_base'),
            'ways_returned':len(data['elements']),'selected_ways':selected,
            'graph_nodes':len(graph),'samples':sample,
            'limits':'Small-area connectivity only. No indoor, barrier, crossing signal, accessibility or field validation. Snap distances are not walkable connectors. No ETA calculated.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--osm',action='store_true')
    args=ap.parse_args()
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'local':audit_local()}
    if args.osm:
        result['osm']=audit_osm()
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
