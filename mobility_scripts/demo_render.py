#!/usr/bin/env python3
# mobility_scripts/demo_render.py — 시연용 화면 생성기
#
# verify_time.py 의 --json 출력을 읽어 HTML 한 장으로 그린다.
# 판정 코드는 한 줄도 건드리지 않는다 — 회귀 85건이 그대로 유효하다.
#
# 쓰는 법 (경로 인자는 verify_time.py 와 같다):
#   python mobility_scripts/demo_render.py --cases final_project_cs/tests/unit/travel/mobility/issue_legs_v1.json --out demo.html
#   python mobility_scripts/demo_render.py --cases final_project_cs/tests/unit/travel/mobility/issue_legs_v1.json --only ISSUE-01,ISSUE-02,ISSUE-03 --out demo.html
#
# 경로를 안 주면 verify_time.py 가 .env 의 DATA_DIR 을 쓴다.
# ★ Windows: 자식 출력을 파이프로 받으므로 PYTHONIOENCODING=utf-8 을 넣어 준다 — 없으면 cp949 로 죽는다.
# 화면에서 ← → 로 장면을 넘긴다. A 를 누르면 전체가 한 번에 보인다.

import argparse, html, json, subprocess, sys, tempfile, os
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

VERDICT = {
    "feasible":          ("성립",   "ok"),
    "infeasible":        ("불가",   "no"),
    "rejected_by_limit": ("탈락",   "lim"),
    "unknown":           ("근거없음", "unk"),
}
GRADE_CLS = {"확정": "g-fix", "추정": "g-est", "근거없음": "g-non"}
KIND_KO = {
    "station_skip": "무정차 — 그 역에 안 선다",
    "edge_closed":  "간선 차단 — 두 역 사이가 끊겼다",
    "line_closed":  "노선 전체 중단",
    "route_closed": "버스 노선 중단",
}
DAY_KO = {"weekday": "평일", "sat": "토요일", "sun": "일요일", "holiday": "휴일"}


def _warn_html(x):
    import html
    if isinstance(x, dict):
        dim = "" if x.get("to_answer") else " dim"
        return (f'<div class="warn{dim}">! {html.escape(str(x.get("text", "")))}'
                f' <span class="wcode">{html.escape(str(x.get("code", "")))}</span></div>')
    return f'<div class="warn">! {html.escape(str(x))}</div>'


def e(x):
    return html.escape("" if x is None else str(x))


def em(x):
    """케이스 note 의 **굵게** 만 살려 이스케이프한다."""
    import re as _re
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", e(x))


def dis_target(d):
    """이슈 종류마다 대상 필드가 다르다 — station / between / line / route."""
    if d.get("station"):
        return f'{d.get("line","")} {d["station"]}'.strip()
    if d.get("between"):
        return f'{d.get("line","")} ' + "–".join(str(x) for x in d["between"])
    if d.get("route"):
        return f'버스 {d["route"]}'
    return str(d.get("line") or "")


def hhmm(m):
    if m is None:
        return None
    return f"{(m // 60) % 24:02d}:{m % 60:02d}" + ("(익일)" if m >= 24 * 60 else "")


def run_verify(args, extra):
    out = Path(tempfile.mkdtemp()) / "verdicts.json"
    cmd = [sys.executable, "-m", "app.modules.travel_ops.mobility.engine.verify_time",
           "--cases", args.cases, "--json", str(out)] + extra
    # ★ 자식 출력을 파이프로 받으면 파이썬이 콘솔 인코딩이 아니라 로캘 인코딩을 쓴다.
    #    한국어 Windows 에서는 cp949 라서 verify_time.py 의 "—" 에서 UnicodeEncodeError 로 죽는다.
    #    (PowerShell 에서 직접 돌릴 때는 콘솔이 UTF-8 이라 안 터진다 — 파이프일 때만 난다.)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
           PYTHONPATH=str(REPO / "final_project_cs"))
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if not out.exists():
        sys.stderr.write((r.stdout or "") + (r.stderr or ""))
        raise SystemExit(
            f"verify_time.py 가 판정 JSON 을 못 냈다 (종료코드 {r.returncode}) — 위 출력을 보라")
    return json.loads(out.read_text(encoding="utf-8")), r.stdout


def badge_verdict(v, grade):
    ko, cls = VERDICT.get(v, (v, "unk"))
    g = f'<span class="grade {GRADE_CLS.get(grade,"g-non")}">{e(grade)}</span>' if grade else ""
    return f'<span class="verdict v-{cls}">{e(ko)}</span>{g}'


def ev_rows(evs):
    if not evs:
        return '<p class="muted">근거 항목 없음</p>'
    out = ['<table class="ev"><thead><tr><th>등급</th><th>내용</th><th>소스</th><th>확인 시각</th></tr></thead><tbody>']
    for v in evs:
        out.append(
            f'<tr><td><span class="grade {GRADE_CLS.get(v.get("grade"),"g-non")}">{e(v.get("grade"))}</span></td>'
            f'<td>{e(v.get("claim"))}</td>'
            f'<td><code>{e(v.get("source_id"))}</code><span class="st">{e(v.get("source_type"))}</span></td>'
            f'<td class="mono">{e(v.get("observed_at"))}</td></tr>')
    out.append("</tbody></table>")
    return "".join(out)


def scene(i, n, case, res):
    cid = case.get("id", res.get("id", "?"))
    note = case.get("note", "")
    day = DAY_KO.get(res.get("day_type"), res.get("day_type", ""))

    # ── 들어오는 것
    legs_in = "".join(
        f'<li><b>{e(l.get("line"))}</b> {e(l.get("from"))} → {e(l.get("to"))}</li>'
        for l in case.get("legs", []))
    dis = case.get("disruptions") or []
    dis_html = ""
    if dis:
        rows = "".join(
            f'<li><span class="kind">{e(KIND_KO.get(d.get("kind"), d.get("kind")))}</span> '
            f'<b>{e(dis_target(d))}</b> '
            f'— {e(d.get("note"))} '
            f'<span class="grade {GRADE_CLS.get(d.get("grade"),"g-non")}">{e(d.get("grade"))}</span> '
            f'<code>{e(d.get("source"))}</code> <span class="mono st">{e(d.get("observed_at"))}</span></li>'
            for d in dis)
        dis_html = f'<div class="mock"><div class="mocklab">사고 소식 (mock 주입 — 감시→Case 생성은 미구현)</div><ul>{rows}</ul></div>'

    # ── 나가는 것
    arr = hhmm(res.get("arrive_min"))
    head = badge_verdict(res.get("verdict"), res.get("grade"))
    arr_html = f'<div class="arr">도착 {e(arr)}</div>' if arr else '<div class="arr none">도착 미상</div>'
    reason = f'<p class="reason">{e(res.get("reason"))}</p>' if res.get("reason") else ""
    relief = f'<p class="relief"><b>완화 조건</b> — {e(res.get("relief"))}</p>' if res.get("relief") else ""

    # 구간별
    lr = []
    for l in res.get("legs", []):
        w = "".join(_warn_html(x) for x in (l.get("warnings") or []))
        dropped = l.get("dropped") or {}
        dr = ('<div class="drop">거른 행 — ' +
              ", ".join(f"{e(k)} {e(v)}" for k, v in dropped.items()) + "</div>") if dropped else ""
        t = []
        if l.get("wait_min") is not None:
            t.append(f'대기 {l["wait_min"]}분')
        if l.get("ride_min") is not None:
            t.append(f'승차 {l["ride_min"]}분<span class="grade {GRADE_CLS.get(l.get("ride_grade"),"g-non")}">{e(l.get("ride_grade"))}</span>')
        if l.get("arrive_min") is not None:
            t.append(f'도착 {hhmm(l["arrive_min"])}')
        lr.append(
            f'<div class="leg"><div class="legh">{badge_verdict(l.get("verdict"), l.get("grade"))}'
            f'<span class="leglab">{e(l.get("label"))}</span></div>'
            + (f'<div class="legt">{" · ".join(t)}</div>' if t else "") +
            f'<p class="legr">{e(l.get("reason"))}</p>{w}{dr}'
            f'<details><summary>근거 {len(l.get("evidence") or [])}건</summary>{ev_rows(l.get("evidence"))}</details></div>')

    # 대안
    alt = res.get("alternatives") or []
    tried = res.get("alt_tried") or []
    taxi = res.get("taxi")
    alt_html = ""
    if alt or tried or taxi:
        items = "".join(
            f'<li><span class="axis">{e(a.get("axis"))}</span> {e(a.get("label"))}'
            f'<span class="grade {GRADE_CLS.get(a.get("grade"),"g-non")}">{e(a.get("grade"))}</span></li>'
            for a in alt) or '<li class="muted">통과한 대안 없음</li>'
        tr = "".join(
            f'<li>{e(t[1] if len(t) > 1 else t)} → <b class="t-{VERDICT.get(t[2],("","unk"))[1]}">'
            f'{e(VERDICT.get(t[2], (t[2],))[0])}</b></li>' for t in tried if isinstance(t, (list, tuple)) and len(t) >= 3)
        tx = ""
        if taxi:
            tx = (f'<div class="taxi"><b>{e(taxi.get("label"))}</b> — {e(taxi.get("reason"))} '
                  f'<span class="grade {GRADE_CLS.get(taxi.get("grade"),"g-non")}">{e(taxi.get("grade"))}</span>'
                  f'<div class="muted">검토해서 뺀 게 아니라 <b>모르는</b> 것이라 열거한다</div></div>')
        alt_html = (f'<div class="block"><h3>대안 <span class="muted">— 순위를 매기지 않는다 '
                    f'(추정값으로 매긴 순위는 그 자체가 추정이다)</span></h3><ul class="alts">{items}</ul>'
                    + (f'<details><summary>열거하고 떨어뜨린 후보 {len(tried)}건</summary><ul class="tried">{tr}</ul></details>' if tr else "")
                    + tx + "</div>")

    warns = "".join(_warn_html(x) for x in (res.get("warnings") or []))

    return f'''<section class="scene" id="s{i}" data-i="{i}">
<div class="sh"><span class="num">장면 {i} / {n}</span><span class="cid">{e(cid)}</span></div>
<h2>{em(note) or e(cid)}</h2>
<div class="io">
  <div class="in"><div class="lab">들어오는 것</div>
    <div class="when"><b>{e(case.get("date"))}</b> <span class="day">{e(day)}</span> · {e(case.get("depart_at"))} 출발</div>
    <ul class="legsin">{legs_in}</ul>{dis_html}</div>
  <div class="arrow">▶</div>
  <div class="out"><div class="lab">나가는 것</div>
    <div class="vhead">{head}{arr_html}</div>{reason}{relief}{warns}</div>
</div>
<div class="block"><h3>구간별 판정</h3>{"".join(lr)}</div>
{alt_html}
<div class="block"><h3>이 판정의 근거 <span class="muted">— 값마다 등급과 확인 시각이 붙는다</span></h3>{ev_rows(res.get("evidence"))}</div>
</section>'''


CSS = """
:root{--fg:#16181d;--mut:#6b7280;--line:#e3e6ea;--bg:#fbfbfc;--card:#fff;
--ok:#0b7a43;--no:#b3261e;--lim:#9a5b00;--unk:#5f6368}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 "Pretendard","Malgun Gothic",system-ui,sans-serif;padding:0 16px}
.wrap{max-width:1080px;margin:0 auto;padding-block:28px}
header{border-bottom:3px solid var(--fg);padding-bottom:14px;margin-bottom:8px}
h1{font-size:26px;margin:0 0 6px}
.sub{color:var(--mut);font-size:14px}
.nav{position:sticky;top:0;background:var(--bg);padding:12px 0;border-bottom:1px solid var(--line);
z-index:5;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.nav button{font:inherit;font-size:14px;padding:6px 12px;border:1px solid var(--line);
background:var(--card);border-radius:8px;cursor:pointer}
.nav button.on{background:var(--fg);color:#fff;border-color:var(--fg)}
.hint{color:var(--mut);font-size:13px;margin-left:auto}
.scene{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:22px;margin:18px 0}
.sh{display:flex;gap:10px;align-items:center;font-size:13px;color:var(--mut)}
.num{font-weight:700;color:var(--fg)}
.cid{font-family:ui-monospace,Menlo,monospace;background:#f1f3f5;padding:1px 7px;border-radius:5px}
h2{font-size:19px;margin:8px 0 18px;line-height:1.45}
h3{font-size:15px;margin:0 0 10px}
.io{display:grid;grid-template-columns:1fr auto 1fr;gap:14px;align-items:stretch}
.in,.out{border:1px solid var(--line);border-radius:11px;padding:14px;min-width:0}
.in{background:#f7f9fb}.out{background:#fcfcfd}
.arrow{align-self:center;color:var(--mut);font-size:20px}
.lab{font-size:12px;letter-spacing:.06em;color:var(--mut);font-weight:700;margin-bottom:8px}
.when{font-size:15px;margin-bottom:6px}
.day{background:#eef1f4;border-radius:5px;padding:1px 7px;font-size:13px}
.legsin{margin:0;padding-left:18px}.legsin li{margin:2px 0}
.mock{margin-top:10px;border-top:1px dashed var(--line);padding-top:9px}
.mocklab{font-size:12px;color:var(--lim);font-weight:700;margin-bottom:5px}
.mock ul{margin:0;padding-left:18px;font-size:14px}
.kind{background:#fff3e0;color:var(--lim);border-radius:5px;padding:1px 6px;font-size:12px;font-weight:700}
.vhead{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}
.verdict{font-size:22px;font-weight:800;letter-spacing:-.01em}
.v-ok{color:var(--ok)}.v-no{color:var(--no)}.v-lim{color:var(--lim)}
.v-unk{color:var(--unk);border-bottom:2px dashed var(--unk)}
.t-ok{color:var(--ok)}.t-no{color:var(--no)}.t-lim{color:var(--lim)}.t-unk{color:var(--unk)}
.arr{font-size:15px;color:var(--mut)}.arr.none{color:var(--unk);border-bottom:1px dashed var(--unk)}
.grade{font-size:11.5px;font-weight:700;border-radius:5px;padding:1px 6px;margin-left:6px;white-space:nowrap}
.g-fix{background:#e6f4ec;color:var(--ok)}
.g-est{background:#fdf1dd;color:var(--lim)}
.g-non{background:#f0f1f2;color:var(--unk);border:1px dashed #c4c8cc}
.reason{margin:6px 0;font-size:14.5px}
.relief{margin:8px 0 0;font-size:14px;background:#f4f6f8;border-left:3px solid var(--mut);padding:7px 10px;border-radius:0 7px 7px 0}
.warn{font-size:13.5px;color:var(--lim);background:#fff8ec;border-radius:6px;padding:5px 9px;margin-top:6px}
.warn.dim{opacity:.65}.wcode{font-size:11px;color:#9a8a6a;margin-left:6px}
.block{margin-top:20px;border-top:1px solid var(--line);padding-top:16px}
.leg{border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:9px}
.legh{display:flex;gap:9px;align-items:baseline;flex-wrap:wrap}
.legh .verdict{font-size:16px}
.leglab{font-weight:700}
.legt{font-size:13.5px;color:var(--mut);margin-top:3px}
.legr{margin:6px 0 0;font-size:14px}
.drop{font-size:12.5px;color:var(--mut);margin-top:5px}
details{margin-top:8px}summary{cursor:pointer;font-size:13px;color:var(--mut)}
table.ev{width:100%;border-collapse:collapse;margin-top:8px;font-size:13.5px}
table.ev th{text-align:left;color:var(--mut);font-weight:600;font-size:12px;
border-bottom:1px solid var(--line);padding:5px 8px 5px 0}
table.ev td{border-bottom:1px solid #f0f2f4;padding:7px 8px 7px 0;vertical-align:top}
table.ev code{font-size:12px;background:#f1f3f5;padding:1px 5px;border-radius:4px}
.st{display:block;font-size:11px;color:var(--mut);margin-top:2px}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px;white-space:nowrap}
.alts{margin:0;padding-left:18px}.alts li{margin:3px 0}
.axis{background:#eef1f4;border-radius:5px;padding:1px 6px;font-size:12px;margin-right:6px}
.tried{margin:6px 0;padding-left:18px;font-size:13.5px;color:var(--mut)}
.taxi{margin-top:10px;border:1px dashed #c4c8cc;border-radius:9px;padding:10px;background:#fafbfc}
.muted{color:var(--mut);font-weight:400;font-size:13px}
footer{color:var(--mut);font-size:12.5px;margin:26px 0 8px;border-top:1px solid var(--line);padding-top:12px}
@media(max-width:760px){.io{grid-template-columns:1fr}.arrow{transform:rotate(90deg);justify-self:center}}
"""

JS = """
const scenes=[...document.querySelectorAll('.scene')];let cur=0,all=false;
function draw(){document.getElementById('pos').textContent=all?'전체':(cur+1)+' / '+scenes.length;
scenes.forEach((s,i)=>s.style.display=(all||i===cur)?'':'none');
document.getElementById('allb').classList.toggle('on',all);
if(!all)window.scrollTo({top:0,behavior:'instant'});}
function go(d){if(all)return;cur=(cur+d+scenes.length)%scenes.length;draw();}
document.getElementById('prev').onclick=()=>go(-1);
document.getElementById('next').onclick=()=>go(1);
document.getElementById('allb').onclick=()=>{all=!all;draw();};
addEventListener('keydown',ev=>{if(ev.key==='ArrowLeft')go(-1);
else if(ev.key==='ArrowRight'||ev.key===' ')go(1);
else if(ev.key.toLowerCase()==='a'){all=!all;draw();}});
draw();
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", default="demo.html")
    ap.add_argument("--only", help="쉼표로 구분한 케이스 id 만 그린다")
    ap.add_argument("--title", default="이동·동선 에이전트 — 들어오는 것과 나가는 것")
    # verify_time.py 로 그대로 넘기는 인자
    for f in ("--timetable", "--order", "--transfer-walk", "--bus-route",
              "--bus-stops", "--station-coords", "--rules", "--holidays"):
        ap.add_argument(f)
    a = ap.parse_args()

    extra = []
    for f in ("timetable", "order", "transfer_walk", "bus_route",
              "bus_stops", "station_coords", "rules", "holidays"):
        v = getattr(a, f)
        if v:
            extra += ["--" + f.replace("_", "-"), v]

    results, _ = run_verify(a, extra)
    src = json.loads(Path(a.cases).read_text(encoding="utf-8"))
    cases = src["cases"] if isinstance(src, dict) and "cases" in src else src
    by_id = {c.get("id"): c for c in cases}

    keep = [x.strip() for x in a.only.split(",")] if a.only else None
    pairs = [(by_id.get(r.get("id"), {}), r) for r in results
             if keep is None or r.get("id") in keep]
    if not pairs:
        raise SystemExit("그릴 케이스가 없다 — --only 의 id 를 확인하라")

    n = len(pairs)
    body = "\n".join(scene(i + 1, n, c, r) for i, (c, r) in enumerate(pairs))
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(a.title)}</title><style>{CSS}</style></head><body><div class="wrap">
<header><h1>{e(a.title)}</h1>
<div class="sub">판정 경로에 LLM 이 없다 — 시간표를 읽는 코드가 판정하고, 값마다 근거 등급과 확인 시각이 붙는다.
모르는 것은 <b>모른다고</b> 나간다.</div></header>
<div class="nav"><button id="prev">← 이전</button><button id="next">다음 →</button>
<button id="allb">전체 보기 (A)</button><span id="pos" class="hint"></span></div>
{body}
<footer>생성 {e(now)} · 케이스 <code>{e(Path(a.cases).name)}</code> · 판정 <code>final_project_cs/app/modules/travel_ops/mobility/engine/verify_time.py</code><br>
사고 소식은 <b>mock 주입</b>이다 — 감시→Case 생성이 미구현이라 코어가 넣어 주지 않는다. 판정은 mock 이 아니다.<br>
경로 API 응답은 원값도 가공값도 저장하지 않는다. 이 화면도 저장하지 않는다.</footer>
</div><script>{JS}</script></body></html>"""
    Path(a.out).write_text(doc, encoding="utf-8", newline="\n")
    print(f"장면 {n}개 → {a.out}")


if __name__ == "__main__":
    main()
