# -*- coding: utf-8 -*-
"""X2 — 종착 추정 정차(timepoint=0 · X1 이 만든 17,447)를 뺀 GTFS zip 을 만든다 (X1 주의 5 · MOTIS B 판 입력)."""
import argparse, csv, io, zipfile
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gtfs", default=r"C:\final_project\exp\x1_gtfs\out\gtfs_subway_x1.zip")
    ap.add_argument("--out", default=r"C:\final_project\exp\x2_compare\gtfs_noest.zip")
    a = ap.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    kept = dropped = 0
    with zipfile.ZipFile(a.gtfs) as zi, zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in zi.namelist():
            raw = zi.read(n)
            if n != "stop_times.txt":
                zo.writestr(n, raw); continue
            rd = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
            buf = io.StringIO(); w = csv.writer(buf, lineterminator="\n")
            h = next(rd); w.writerow(h); tp = h.index("timepoint")
            for r in rd:
                if r[tp] == "0": dropped += 1
                else: w.writerow(r); kept += 1
            zo.writestr(n, buf.getvalue().encode("utf-8"))
    print(f"stop_times {kept + dropped:,} → {kept:,} (종착 추정 {dropped:,} 뺌) → {a.out}")


if __name__ == "__main__":
    main()
