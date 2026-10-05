# datasets/mobility/scripts/build_road_graph_v2.py — 서울(+인접·공항) 차도·자전거·**도보** 그래프 파일 (2026-10-04)
#
# v1(`build_road_graph_v1.py`) 위에 **걸음 길**을 더한 판이다. v1 은 차도와 자전거 길만 싣고 보행자 전용 길(계단·샛길·보행자도로)은
# 자전거 허용일 때만 들어 있어, 파이썬 길찾기의 걸음 거리가 GraphHopper 기록과 어긋났다(중앙 오차 9% · 최대 2배 이상 틀린 쌍 있음).
# v2 는 같은 원자료(OSM pbf)에서 **걸음 길**을 따로 판정해 같은 간선 표에 `foot`·`st`(계단)·`fmain` 칸을 더한다.
#
# 입력: raw/osm/south-korea-latest.osm.pbf (Geofabrik · ODbL)   출력: processed/mobility/road_graph_v2/
#   nodes.jsonl.gz   {"id","lat","lon","r"}
#   edges.jsonl.gz   v1 칸 + "foot"(걸음 통행 0/1) · "st"(계단 0/1) · "fmain"(걸음 최대 약연결 성분 0/1)
# 걸음 규칙(OSM 공표 태그만 · 추정 아님이 아니라 「태그 해석」이다):
#   · 간선도로 이하 차도(primary~service)는 걷는다. 자동차전용(motorway·trunk)은 foot=yes/designated 일 때만.
#   · footway·path·pedestrian·steps·corridor·track 은 걷는다. cycleway(자전거 전용)는 foot=yes/designated/permissive 일 때만.
#   · foot=no·private 이면 안 걷는다. access=no·private 는 foot 이 허용 태그일 때만 걷는다. area=yes(광장 면)는 싣지 않는다.
#   · parking_aisle·drive-through·emergency_access 서비스 길은 v1 과 같이 싣지 않는다.
#   · 걸음은 일방통행을 보지 않는다(oneway:foot 은 한국에 거의 없어 생략).
# 실행: <osmium·shapely 가 있는 파이썬> datasets/mobility/scripts/build_road_graph_v2.py --src <pbf> --out <폴더>
import argparse, gzip, hashlib, json, math, sys, time, datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_road_graph_v1 as V1                              # 같은 범위·같은 도구 함수(해시·폴리라인·범위 다각형)를 그대로 쓴다

VERSION = "road_graph_v2"
FOOT_ROAD_HW = V1.CAR_HW - {"motorway", "motorway_link", "trunk", "trunk_link"}
FOOT_CAR_ONLY_HW = {"motorway", "motorway_link", "trunk", "trunk_link"}
FOOT_PATH_HW = {"footway", "path", "pedestrian", "steps", "corridor", "track"}
FOOT_YES = V1.YES | {"yes", "designated", "permissive"}


def classify_foot(tags):
    """(걸음 통행 가능, 계단)."""
    hw = tags.get("highway")
    if not hw or tags.get("area") == "yes":
        return False, False
    foot, acc = tags.get("foot", ""), tags.get("access", "")
    if foot in V1.NO:
        return False, False
    if acc in V1.NO and foot not in FOOT_YES:
        return False, False
    if hw == "service" and tags.get("service") in V1.SERVICE_DROP:
        return False, False
    if hw in FOOT_ROAD_HW:
        return True, False
    if hw in FOOT_CAR_ONLY_HW:
        return foot in FOOT_YES, False
    if hw in FOOT_PATH_HW:
        return True, hw == "steps"
    if hw == "cycleway":
        return foot in FOOT_YES, False
    return False, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import osmium
    import numpy as np
    import shapely
    from shapely.geometry import mapping

    t0 = time.time()
    src = Path(a.src)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log = {"version": VERSION, "src": src.name, "src_bytes": src.stat().st_size,
           "built_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds")}
    h = hashlib.md5()
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    log["src_md5"] = h.hexdigest()
    hdr = osmium.io.Reader(str(src), osmium.osm.osm_entity_bits.NOTHING).header()
    log["osmosis_replication_timestamp"] = hdr.get("osmosis_replication_timestamp", "")

    region, polys, corridor = V1.build_region(str(src), log)
    load = region.buffer(V1.MARGIN_M / 111320.0 / math.cos(math.radians(37.5))) if V1.MARGIN_M else region
    shapely.prepare(load)
    minx, miny, maxx, maxy = load.bounds
    log["region"]["margin_m"] = V1.MARGIN_M
    print(f"[범위] {log['region']} ({time.time()-t0:.0f}s)", flush=True)

    # ── 1패스: 차도·자전거·걸음 way 수집 ──
    ways = []
    keep_tags = ("highway", "oneway", "oneway:bicycle", "maxspeed", "junction", "service", "name", "ref")
    n_seen = 0
    for w in osmium.FileProcessor(str(src)).with_locations().with_filter(osmium.filter.KeyFilter("highway")):
        if not isinstance(w, osmium.osm.Way):
            continue
        n_seen += 1
        tags = dict(w.tags)
        car, bike = V1.classify(tags)
        foot, steps = classify_foot(tags)
        if not (car or bike or foot):
            continue
        try:
            nids = [n.ref for n in w.nodes]
            ll = [(n.lat, n.lon) for n in w.nodes]
        except osmium.InvalidLocationError:
            continue
        if len(nids) < 2:
            continue
        if not any(miny <= la <= maxy and minx <= lo <= maxx for la, lo in ll):
            continue
        lats = np.fromiter((p[0] for p in ll), float)
        lons = np.fromiter((p[1] for p in ll), float)
        if not shapely.contains_xy(load, lons, lats).any():
            continue
        ways.append((w.id, nids, ll, {k: tags[k] for k in keep_tags if k in tags}, car, bike, foot, steps))
    print(f"[way] 보유 highway {n_seen:,} → 범위 안 차도·자전거·걸음 {len(ways):,} ({time.time()-t0:.0f}s)", flush=True)

    cnt = {}
    for _, nids, *_rest in ways:
        for i, n in enumerate(nids):
            cnt[n] = cnt.get(n, 0) + (2 if i in (0, len(nids) - 1) else 1)
    gnode = {n for n, c in cnt.items() if c >= 2}
    coord, edges = {}, []
    for wid, nids, ll, tg, car, bike, foot, steps in ways:
        hw = tg["highway"]
        ow = V1.oneway_of(tg, hw) if car else 0
        bow = V1.oneway_of(tg, hw) if bike else 0
        if tg.get("oneway:bicycle") == "no":
            bow = 0
        elif hw in V1.BIKE_ONLY_HW and "oneway" not in tg:
            bow = 0
        ms = V1.parse_maxspeed(tg.get("maxspeed"))
        start = 0
        for i in range(1, len(nids)):
            if nids[i] in gnode or i == len(nids) - 1:
                seg = ll[start:i + 1]
                L = sum(V1.hav(*seg[k], *seg[k + 1]) for k in range(len(seg) - 1))
                u, v = nids[start], nids[i]
                coord[u] = ll[start]
                coord[v] = ll[i]
                edges.append({"u": u, "v": v, "way": wid, "sa": start, "sb": i, "len": round(L, 1), "ow": ow,
                              "hw": hw, "ms": ms, "car": int(car), "bike": int(bike), "bow": bow,
                              "foot": int(foot), "st": int(steps), "g": V1.enc_polyline(seg)})
                start = i
    print(f"[그래프] 노드 {len(coord):,} · 간선 {len(edges):,} ({time.time()-t0:.0f}s)", flush=True)

    # ── 표시: main(차량 강연결) · bmain(자전거 약연결) · fmain(걸음 약연결) ──
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    ids = list(coord)
    ix = {n: i for i, n in enumerate(ids)}

    def comp(sel, owkey, directed):
        us, vs = [], []
        for e in edges:
            if not e[sel]:
                continue
            u, v = ix[e["u"]], ix[e["v"]]
            o = e[owkey] if owkey else 0
            if not directed or o in (0, 1):
                us.append(u)
                vs.append(v)
            if not directed or o in (0, -1):
                us.append(v)
                vs.append(u)
        M = coo_matrix((np.ones(len(us)), (us, vs)), shape=(len(ids), len(ids))).tocsr()
        _, lab = connected_components(M, directed=directed, connection="strong")
        used = np.zeros(len(ids), bool)
        used[us] = True
        return lab, np.bincount(lab[used]).argmax()

    lab_c, big_c = comp("car", "ow", True)
    lab_b, big_b = comp("bike", "bow", False)
    lab_f, big_f = comp("foot", None, False)
    for e in edges:
        iu, iv = ix[e["u"]], ix[e["v"]]
        e["main"] = int(bool(e["car"]) and lab_c[iu] == big_c and lab_c[iv] == big_c)
        e["bmain"] = int(bool(e["bike"]) and lab_b[iu] == big_b)
        e["fmain"] = int(bool(e["foot"]) and lab_f[iu] == big_f)
    lats = np.array([coord[n][0] for n in ids])
    lons = np.array([coord[n][1] for n in ids])
    inreg = shapely.contains_xy(region, lons, lats)

    def write_gz(path, rows):
        with open(path, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0) as gz:
            for r in rows:
                gz.write((json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))

    write_gz(out / "nodes.jsonl.gz", ({"id": n, "lat": round(coord[n][0], 7), "lon": round(coord[n][1], 7), "r": int(inreg[ix[n]])}
                                      for n in sorted(coord)))
    edges.sort(key=lambda e: (e["way"], e["sa"]))
    write_gz(out / "edges.jsonl.gz", edges)
    reg = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": k, "relation": V1.REGION_ADMIN[k][1]},
         "geometry": mapping(shapely.simplify(p, 0.0002))} for k, p in polys.items()] + [
        {"type": "Feature", "properties": {"name": "인천국제공항고속도로 회랑", "buffer_m": V1.CORRIDOR_M},
         "geometry": mapping(shapely.simplify(corridor, 0.0002))}]}
    (out / "region_v1.geojson").write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")

    log["counts"] = {"ways": len(ways), "nodes": len(coord), "edges": len(edges),
                     "edges_car": sum(e["car"] for e in edges), "edges_bike": sum(e["bike"] for e in edges),
                     "edges_foot": sum(e["foot"] for e in edges),
                     "edges_foot_only": sum(1 for e in edges if e["foot"] and not e["car"] and not e["bike"]),
                     "edges_steps": sum(e["st"] for e in edges),
                     "edges_car_main": sum(e["main"] for e in edges), "edges_bike_main": sum(e["bmain"] for e in edges),
                     "edges_foot_main": sum(e["fmain"] for e in edges), "nodes_in_region": int(inreg.sum())}
    log["options"] = {"service_drop": sorted(V1.SERVICE_DROP), "turn_restrictions": "생략", "oneway_foot": "생략"}
    log["elapsed_s"] = round(time.time() - t0, 1)
    man = {"version": VERSION, "source": "Geofabrik south-korea-latest.osm.pbf",
           "source_url": "https://download.geofabrik.de/asia/south-korea-latest.osm.pbf",
           "license": "ODbL 1.0 (© OpenStreetMap contributors)", "grade": "확정(OSM 공표 태그) — 걸음 길은 태그 해석",
           "src_md5": log["src_md5"], "osm_data_at": log["osmosis_replication_timestamp"],
           "built_at": log["built_at"], "files": {}}
    for fn in ("nodes.jsonl.gz", "edges.jsonl.gz", "region_v1.geojson"):
        b = (out / fn).read_bytes()
        rows = gzip.decompress(b).count(b"\n") if fn.endswith(".gz") else None
        man["files"][fn] = {"bytes": len(b), "md5": hashlib.md5(b).hexdigest(), "rows": rows}
    (out / "MANIFEST.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "build_report.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(man["files"], ensure_ascii=False))
    print(json.dumps(log["counts"], ensure_ascii=False), f"{log['elapsed_s']}s")


if __name__ == "__main__":
    main()
