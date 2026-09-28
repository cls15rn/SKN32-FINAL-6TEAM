"""TMAP capability probe; dry-run by default; never writes response content.

--live sends 6 transit requests and 2 pedestrian requests at most, no retries.
Requires service subscriptions as well as app keys. Keys are never printed.
This is not a production provider, a cache, or a mobility verdict engine.
"""
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
KST=timezone(timedelta(hours=9))


def keys():
    values={}
    for line in (ROOT/'.env').read_text(encoding='utf-8-sig').splitlines():
        name,sep,value=line.strip().partition('=')
        if sep and not name.startswith('#'):
            values[name.strip()]=value.strip().strip('"').strip("'")
    for name in ('TMAP_APP_KEY','TMAP_TRANSIT_APP_KEY'):
        if os.environ.get(name):
            values[name]=os.environ[name]
    return values


def summaries(data, pedestrian):
    if pedestrian:
        features=data.get('features',[])
        props=[f.get('properties',{}) for f in features]
        return {'has_route':bool(features),
                'has_geometry':any(f.get('geometry',{}).get('type')=='LineString' for f in features),
                'has_total_metrics':any('totalTime' in p and 'totalDistance' in p for p in props)}
    paths=data.get('metaData',{}).get('plan',{}).get('itineraries',[])
    return {'has_routes':bool(paths),'has_alternatives':len(paths)>1,
            'checks':[{'has_totals':all(k in p for k in ('totalTime','totalWalkTime','totalWalkDistance','transferCount')),
                       'has_walk_geometry':any(s.get('linestring') for l in p.get('legs',[]) for s in l.get('steps',[])),
                       'service_flag_present':any('service' in l for l in p.get('legs',[])),
                       'leg_time_field_names':sorted({k for l in p.get('legs',[]) for k in l if any(t in k.lower() for t in ('time','dttm','arrival','departure'))}),
                       'endpoint_time_field_names':sorted({k for l in p.get('legs',[]) for side in ('start','end') for k in l.get(side,{}) if any(t in k.lower() for t in ('time','dttm','arrival','departure'))}),
                       'transport_service_flags':[{'mode':l.get('mode'),'service':l.get('service'),'lane_services':[v.get('service') for v in l.get('lane',[])]} for l in p.get('legs',[]) if l.get('mode')!='WALK'],
                       'has_non_operating_leg':any(l.get('service')==0 for l in p.get('legs',[])),
                       'has_bus':any(l.get('mode')=='BUS' for l in p.get('legs',[])),
                       'has_subway':any(l.get('mode')=='SUBWAY' for l in p.get('legs',[]))} for p in paths]}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--live',action='store_true')
    ap.add_argument('--case',action='append',help='Run only the named case; repeat to select multiple cases')
    args=ap.parse_args()
    tomorrow=(datetime.now(KST)+timedelta(days=1)).replace(hour=14,minute=0,second=0,microsecond=0)
    weekend=tomorrow+timedelta(days=(5-tomorrow.weekday())%7)
    def body(a,b,when):
        return {'startX':str(a[1]),'startY':str(a[0]),'endX':str(b[1]),'endY':str(b[0]),
                'count':3,'lang':1,'format':'json','searchDttm':when.strftime('%Y%m%d%H%M')}
    seoul=(37.5547,126.9706);palace=(37.5796,126.977);hongdae=(37.557,126.924);jamsil=(37.5133,127.1001)
    cases=[('city_day',body(seoul,palace,tomorrow),False),
           ('city_night',body(seoul,palace,tomorrow.replace(hour=2)),False),
           ('rail_day',body(hongdae,jamsil,tomorrow),False),
           ('rail_night',body(hongdae,jamsil,tomorrow.replace(hour=2)),False),
           ('weekend',body(hongdae,palace,weekend),False),
           ('airport',body((37.44760672,126.4523433),seoul,tomorrow),False)]
    for name,a,b in [('walk_city',seoul,(37.5663,126.9779)),('walk_short',palace,(37.572,126.9769))]:
        cases.append((name,{'startX':a[1],'startY':a[0],'endX':b[1],'endY':b[0],
                            'startName':'Origin','endName':'Destination','reqCoordType':'WGS84GEO','resCoordType':'WGS84GEO'},True))
    values=keys() if args.live else {}
    for name,payload,walk in cases:
        if args.case and name not in args.case:
            continue
        key=values.get('TMAP_APP_KEY') if walk else values.get('TMAP_TRANSIT_APP_KEY') or values.get('TMAP_APP_KEY')
        row={'case':name,'request':payload,'checked_at':datetime.now(KST).isoformat()}
        if not args.live:
            row['status']='DRY_RUN_NO_REQUEST'
        elif not key:
            row['status']='MISSING_APP_KEY'
        else:
            endpoint='https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1' if walk else 'https://apis.openapi.sk.com/transit/routes'
            req=Request(endpoint,data=json.dumps(payload).encode(),method='POST',headers={
                'Content-Type':'application/json','Accept':'application/json','appKey':key})
            try:
                with urlopen(req,timeout=25) as res:
                    data=json.load(res)
                row.update(status='HTTP_200',capabilities=summaries(data,walk))
                del data
            except HTTPError as exc:
                row.update(status='HTTP_ERROR',http_status=exc.code)
            except (URLError,TimeoutError):
                row['status']='NETWORK_ERROR'
                print(json.dumps(row));raise SystemExit(2)
        print(json.dumps(row),flush=True)


if __name__=='__main__':
    main()
