# mobility_scripts/collect/tago_redo_holiday.py — 2·7호선 휴일(03) 조합만 체크포인트에서 제거
import json
from _paths import RAW_MOBILITY
p = RAW_MOBILITY / "tago_timetable_done.json"
done = json.loads(p.read_text(encoding="utf-8"))
redo = [k for k in done if k.startswith(("MTRS12", "MTRS17")) and "|03|" in k]
p.write_text(json.dumps(sorted(set(done) - set(redo))), encoding="utf-8")
print(len(redo), "조합 제거 → tago_subway_collect.py 다시 실행")