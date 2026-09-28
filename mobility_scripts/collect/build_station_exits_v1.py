# mobility_scripts/collect/build_station_exits_v1.py — OSM 역 출구 노드 → 역별 출구 좌표표 (19번 방 · 2026-09-19)
#
# 입력  raw/mobility/osm/osm_subway_entrances_sudogwon_raw.json  (13번 방 · 3,088 노드 · ODbL)
#       processed/mobility/station_coords.json                    (793 역-노선 키)
# 출력  processed/mobility/station_exits_v1.json
#
# 귀속 규칙 (13번 인계 §6): 역 좌표 300 m 안 + description 의 역명이 그 역이면 name_match,
#   이름이 없거나 다르면 **가장 가까운 역**에 붙이고 attrib=nearest. 등급은 전부 **추정**(13번 §3-6).
# ★ 이 파일은 시각을 확정하는 데 쓰지 않는다 — 정류장↔역 환승 도보와 근접 상한 판정에만 쓴다.
# ★ 18번 전처리 방 목록에 있던 항목이지만 19번이 먼저 열려 출구표만 여기서 만든다(도로망 그래프는 18번 그대로).
import json, math, re, sys, collections
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from mobility_scripts.collect._paths import RAW_MOBILITY, PROCESSED   # noqa: E402

RADIUS_M = 300
KST = timezone(timedelta(hours=9))


def meters(lat1, lng1, lat2, lng2):
    dy = (lat2 - lat1) * 111_320
    dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def desc_station(d):
    """'성수역 1번 출구' / '건대입구역 1번출구' → '성수' / '건대입구'. 못 읽으면 None."""
    if not d:
        return None
    m = re.match(r"^\s*(.+?)역\s*[\(\d]", d)
    if not m:
        m = re.match(r"^\s*(.+?)역\b", d)
    if not m:
        return None
    return re.sub(r"\(.*?\)", "", m.group(1)).replace(" ", "")   # '대림(구로구청)' → '대림'


def main():
    src = RAW_MOBILITY / "osm" / "osm_subway_entrances_sudogwon_raw.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    nodes = raw["elements"]
    sc = json.loads((PROCESSED / "mobility" / "station_coords.json").read_text(encoding="utf-8"))
    stations = sc["stations"]

    # 역명 → 좌표 (환승역은 노선별 좌표가 같거나 수십 m 차이 — 노선별로 전부 둔다)
    by_name = collections.defaultdict(list)
    for k, v in stations.items():
        by_name[v["station_nm"]].append((k, v["lat"], v["lng"]))

    # 각 노드에 대해 300 m 안의 역 후보
    attrib = collections.defaultdict(list)        # station_nm → [exit]
    stats = collections.Counter()
    for n in nodes:
        lat, lng = n["a"], n["o"]
        cands = []
        for nm, keys in by_name.items():
            d = min(meters(lat, lng, la, lo) for _k, la, lo in keys)
            if d <= RADIUS_M:
                cands.append((d, nm))
        if not cands:
            stats["역_300m_밖"] += 1
            continue
        cands.sort()
        want = desc_station(n.get("d"))
        pick, how = None, None
        if want:
            hit = [c for c in cands if c[1].replace(" ", "") in (want, want + "역")]   # '서울' ↔ '서울역'
            if hit:
                pick, how = hit[0], "name_match"
        if pick is None:
            pick, how = cands[0], ("nearest_name_mismatch" if want else "nearest")
        stats[how] += 1
        d, nm = pick
        attrib[nm].append({"osm_id": n["i"], "lat": lat, "lng": lng, "ref": n.get("ref"),
                           "desc": n.get("d"), "desc_en": n.get("d:en"),
                           "wheelchair": n.get("wheelchair"), "attrib": how,
                           "dist_to_station_m": round(d)})
    for v in attrib.values():
        v.sort(key=lambda e: (str(e.get("ref") or "zz").zfill(3), e["dist_to_station_m"]))

    covered = sum(1 for nm in by_name if attrib.get(nm))
    out = {
        "source_id": f"osm_subway_entrance@{raw.get('fetched_at')}",
        "source": raw.get("source"), "license": raw.get("license"), "osm_base": raw.get("osm_base"),
        "built_at": datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "grade": "추정",
        "grade_근거": "13번 방 §3-6 — 793 대비 99.4% 커버 · ref 99% · 눈검수 5/6 이나 비공식 소스 + description 결손 13%",
        "radius_m": RADIUS_M,
        "attrib_rule": "역 좌표 300 m 안 + description 역명 일치 → name_match · 아니면 가장 가까운 역(nearest / nearest_name_mismatch)",
        "stats": dict(stats),
        "station_names": len(by_name), "stations_with_exit": covered,
        "exits": dict(sorted(attrib.items())),
    }
    dst = PROCESSED / "mobility" / "station_exits_v1.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"출구 노드 {len(nodes):,} → 귀속 {sum(stats[k] for k in stats if k != '역_300m_밖'):,} "
          f"({dict(stats)}) · 역명 {len(by_name)} 중 출구 있음 {covered} → {dst}")


if __name__ == "__main__":
    main()
