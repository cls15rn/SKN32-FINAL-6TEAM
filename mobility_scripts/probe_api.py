# mobility_scripts/probe_api.py — 새 오픈API 응답 구조를 한 번에 확인한다 (재사용 도구)
# 실행 예:
#   python mobility_scripts/probe_api.py --url https://apis.data.go.kr/B551177/BusInformation/getBusInfo
#   python mobility_scripts/probe_api.py --url http://ws.bus.go.kr/api/rest/busRouteInfo/getBusRouteList -p strSrch=721 --key DATA_GO_KR_KEY
#   python mobility_scripts/probe_api.py --url ... -p cityCode=23 --save tago_bus_sample
# 왜: 활용신청한 API 마다 필드명·중첩 구조·페이징 방식이 달라서, 수집 스크립트를 쓰기 전에
#     "무엇이 오는가"를 먼저 봐야 한다. 오늘만 네 번(지하철·인천공항버스·TAGO버스·카카오) 필요했다.
# 하는 일: serviceKey 주입 → JSON/XML 자동 판별 → 키·타입 트리 출력 → 첫 항목 전체 → totalCount → raw 저장(옵션)
import os, json, argparse, re
from pathlib import Path
from dotenv import load_dotenv
import requests

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
RAW = Path(os.environ.get("DATA_DIR") or (REPO.parent / "data")) / "travel" / "raw" / "probe"

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True)
ap.add_argument("-p", "--param", action="append", default=[], help="key=value (여러 번)")
ap.add_argument("--key", default="DATA_GO_KR_KEY", help=".env 의 인증키 변수명")
ap.add_argument("--key-name", default="serviceKey", help="인증키 파라미터 이름 (ws.bus.go.kr 는 ServiceKey)")
ap.add_argument("--no-key", action="store_true")
ap.add_argument("--rows", default="10")
ap.add_argument("--save", help="raw 응답을 raw/probe/<이름>.(json|xml) 으로 저장")
args = ap.parse_args()

params = {}
for kv in args.param:
    k, _, v = kv.partition("=")
    params[k] = v
if not args.no_key:
    params.setdefault(args.key_name, os.environ[args.key])
params.setdefault("_type", "json")
params.setdefault("numOfRows", args.rows)
params.setdefault("pageNo", "1")

def mask(s):
    """인증키를 가린다. URL 이 실리는 곳이면 어디든 통과시킨다."""
    return re.sub(r"(?i)(serviceKey|ServiceKey|apiKey|authKey)=[^&\s'\"]+", r"\1=***", str(s))


try:
    r = requests.get(args.url, params=params, timeout=30)
except Exception as e:
    # ★ requests 예외 메시지에는 인증키가 붙은 URL 이 그대로 실린다(프록시 차단·타임아웃·DNS 실패).
    #   traceback 을 그대로 띄우면 키가 로그·대화기록에 남으므로 여기서 끊는다.
    raise SystemExit(f"요청 실패 — {type(e).__name__}: {mask(e)}") from None

shown = mask(r.request.url)
print(f"GET {shown}\nHTTP {r.status_code} · {len(r.content):,} bytes · {r.headers.get('content-type','')}\n")

def xml_to_obj(el):
    """ElementTree → dict/list/str. 같은 태그가 반복되면 리스트."""
    kids = list(el)
    if not kids:
        return (el.text or "").strip()
    out = {}
    for k in kids:
        v = xml_to_obj(k)
        if k.tag in out:
            if not isinstance(out[k.tag], list):
                out[k.tag] = [out[k.tag]]
            out[k.tag].append(v)
        else:
            out[k.tag] = v
    return out


body = None
try:
    body = r.json()
except ValueError:
    if "<" in r.text[:200]:
        print("※ XML 응답 — 파싱해서 구조를 보여준다(_type=json 이 안 먹는 API 다).\n")
        import xml.etree.ElementTree as ET
        try:
            body = {ET.fromstring(r.text).tag: xml_to_obj(ET.fromstring(r.text))}
        except ET.ParseError as e:
            print("XML 파싱 실패:", e)
            print(r.text[:1200])
        for tag in ("resultCode", "resultMsg", "errMsg", "returnAuthMsg", "returnReasonCode"):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", r.text, re.S)
            if m:
                print(f"   {tag}: {m.group(1).strip()}")
        print()
    else:
        print("JSON 도 XML 도 아님 — 앞부분 원문:")
        print(r.text[:1200])


def shape(o, depth=0, path="", max_depth=5):
    pad = "  " * depth
    if isinstance(o, dict):
        for k, v in o.items():
            t = type(v).__name__
            extra = f" = {v}" if isinstance(v, (str, int, float)) and len(str(v)) < 40 else ""
            print(f"{pad}{k}: {t}{extra}")
            if depth < max_depth and isinstance(v, (dict, list)):
                shape(v, depth + 1, f"{path}.{k}", max_depth)
    elif isinstance(o, list):
        print(f"{pad}[{len(o)}개] of {type(o[0]).__name__ if o else '?'}")
        if o and depth < max_depth:
            shape(o[0], depth + 1, path + "[0]", max_depth)


if body is not None:
    print("── 응답 구조 ──")
    shape(body)
    # 흔한 위치에서 항목·총건수 찾기
    def find(o, names):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in names:
                    return v
                got = find(v, names)
                if got is not None:
                    return got
        elif isinstance(o, list):
            for v in o:
                got = find(v, names)
                if got is not None:
                    return got
        return None

    total = find(body, {"totalCount", "totalcount", "listTotalCount"})
    items = find(body, {"item", "items", "row", "routes", "msgBody"})
    if isinstance(items, dict):
        items = items.get("item", items)
    print(f"\ntotalCount: {total}")
    if isinstance(items, list) and items:
        print(f"항목 {len(items)}개 · 필드: {sorted(items[0]) if isinstance(items[0], dict) else type(items[0]).__name__}")
        print("첫 항목:", json.dumps(items[0], ensure_ascii=False, indent=1)[:900])
    elif isinstance(items, dict):
        print("항목 1개 · 필드:", sorted(items))
        print(json.dumps(items, ensure_ascii=False, indent=1)[:900])
    else:
        print("항목을 못 찾았다 — 위 구조를 보고 경로를 확인한다.")

if args.save:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / f"{args.save}.{'xml' if r.text.lstrip().startswith('<') else 'json'}"
    out.write_text(r.text, encoding="utf-8")
    print(f"\nraw 저장 → {out}")
