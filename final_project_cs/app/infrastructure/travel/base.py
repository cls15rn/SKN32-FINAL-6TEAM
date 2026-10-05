# -*- coding: utf-8 -*-
"""외부 데이터 어댑터의 공통 규칙.

여기 있는 것은 **판단이 아니라 규율**이다. 어떤 공급자를 붙이든 아래 넷은 같다.

  ① 못 가져오면 `None` — 지어내지 않고 폴백하지 않는다
  ② 성공하면 `confirmed_at`·`source` 를 **반드시** 함께 준다
  ③ HTTP 상태만 보고 성공을 판정하지 않는다
  ④ 왜 못 가져왔는지를 **센다** — 조용한 스킵을 만들지 않는다

★③ 은 지어낸 걱정이 아니다. 2026-09-09 실측:

    ODsay   `searchPubTransPathT` 키 없이 호출 → **HTTP 200**
            body: {"error":[{"code":"500","message":"[ApiKeyAuthFailed] ..."}]}

  `r.raise_for_status()` 만 쓰면 **인증 실패가 성공으로 지나간다.** 그러면
  `read.transit` 이 빈 dict 를 돌려주고, Team 은 그걸 「확인했다」로 읽는다.
  같은 호출에서 data.go.kr 계열은 401 로 정직하게 답했다 — 공급자마다 다르다.
  그래서 **본문까지 봐야 한다**는 것을 공통 규칙으로 못 박는다.

★②·④ 는 v10 §4-D 다. 「확인 시각·출처를 같이 저장한다 / 모르면 '미확인'」.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
import threading
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

#: 바깥 호출 상한(초). ★Team 전체 타임아웃(`reliability.team_timeout_seconds`)
#:  보다 **작아야** 한다 — 안 그러면 Team 이 먼저 죽어 어느 소스가 늦었는지 모른다.
DEFAULT_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class SourceMiss:
    """가져오지 못한 한 건. ★예외가 아니라 **기록**이다.

    예외로 던지면 Team 마다 잡는 방식이 갈리고, 안 잡으면 Case 하나가 통째로
    죽는다. 「모름」은 정상적인 결과 갈래이지 사고가 아니다.
    """

    source: str
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.source}: {self.reason}" + (f" ({self.detail})" if self.detail else "")


class TravelSource:
    """HTTP 로 바깥을 읽는 어댑터의 바닥.

    ★상태를 들고 있는 이유는 **미스를 세기 위해서**다. 세지 않으면 분모가
      줄어 성공률이 실제보다 좋아 보인다(`CLAUDE.md` §3 「조용한 스킵 금지」).
    """

    #: 하위 클래스가 자기 이름을 준다. 근거(`Evidence.source_id`)에 그대로 실린다.
    name: str = "unknown"

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 transport: Callable[..., httpx.Response] | None = None,
                 limiter: Any | None = None, cache: Any | None = None,
                 proxy: str | None = None) -> None:
        self._timeout = timeout
        # ★경유 프록시(고정 IP 서버). 조립이 `apply_outbound_proxy()` 로 넣는다 — IP 에 묶인
        #   소스만. 없으면 바로 나간다.
        self._proxy = proxy or None
        # ★응답 캐시(`cache.py`). 없으면 매번 나간다 — 단위 시험이 그 상태다.
        self._cache = cache
        # ★마지막으로 받아 온 값의 시각 — `stamp()` 가 확인 시각으로 쓴다. 요청 스레드마다
        #   따로 둔다(여러 요청이 같은 어댑터를 동시에 쓸 수 있다).
        self._fetch_state = threading.local()
        # ★테스트가 네트워크 없이 돌 수 있게 주입 지점을 연다. 기본값은 실제 호출이다.
        self._get = transport or self._http_get
        # ★속도 제한기. 없으면 제한 없이 나간다 - 단위 시험이 그 상태다.
        #   조립(`build_travel_sources`)은 반드시 넣는다.
        self._limiter = limiter
        self.misses: Counter[str] = Counter()

    # ── 하위 클래스가 쓰는 부품 ──────────────────────────────────
    def _http_get(self, url: str, params: dict[str, Any]) -> httpx.Response:
        proxy = getattr(self, "_proxy", None)
        if proxy:
            return httpx.get(url, params=params, timeout=self._timeout, proxy=proxy)
        return httpx.get(url, params=params, timeout=self._timeout)

    def _allow(self) -> bool:
        """속도 제한을 통과하면 True. 거부되면 세고 False.

        ★거부를 예외로 위에 던지지 않는다 - 「모름」은 정상 갈래이고,
          Case 하나가 통째로 죽을 일이 아니다.
        """
        if self._limiter is None:
            return True
        from .ratelimit import RateLimited

        try:
            self._limiter.acquire(self.name)
            return True
        except RateLimited as exc:
            self._miss("rate_limited", f"{exc.wait_seconds:.1f}s remaining")
            return False

    def _miss(self, reason: str, detail: str = "") -> None:
        """★`None` 을 돌려주기 **전에** 반드시 부른다. 이유 없는 모름은 못 고친다."""
        self.misses[reason] += 1
        logger.warning("travel source miss: %s", SourceMiss(self.name, reason, detail))

    def _fetch_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """읽고, **본문까지 검사하고**, 실패면 `None`.

        ★타임아웃을 성공으로 추정하지 않는다(`CLAUDE.md` §0.2). 재시도도 하지
          않는다 — 여기서 자동 재시도를 걸면 공급자 장애 때 우리가 부하를 보탠다.
        ★캐시를 **제한기보다 먼저** 본다 — 재사용은 바깥으로 안 나가니 한도를 안 쓴다.
        """
        cached = self._from_cache(url, params)
        if cached is not None:
            return cached
        if not self._allow():
            return None
        try:
            response = self._get(url, params)
        except httpx.TimeoutException as exc:
            self._miss("timeout", str(exc))
            return None
        except httpx.HTTPError as exc:
            self._miss("transport_error", f"{type(exc).__name__}: {exc}")
            return None

        if response.status_code != 200:
            self._miss(f"http_{response.status_code}", response.text[:200])
            return None

        try:
            payload = response.json()
        except ValueError:
            # ★JSON 이 아니면 대개 HTML 오류 페이지다. data.go.kr 이 그렇게 답한다.
            self._miss("not_json", response.text[:200])
            return None

        if not isinstance(payload, dict):
            self._miss("unexpected_shape", type(payload).__name__)
            return None

        problem = self._body_error(payload)
        if problem is not None:
            # ★HTTP 200 인데 본문이 오류인 경우. ODsay 가 실제로 이렇게 답한다.
            self._miss("body_error", problem)
            return None
        self._remember(url, params, payload)
        return payload

    # ── 응답 캐시 ────────────────────────────────────────────────
    def _cache_key(self, url: str, params: dict[str, Any]) -> tuple:
        return (self.name, url, tuple(sorted((str(k), str(v)) for k, v in params.items())))

    def _from_cache(self, url: str, params: dict[str, Any]) -> Any | None:
        self._fetch_state.fetched_at = None
        self._fetch_state.from_cache = False
        if self._cache is None:
            return None
        hit = self._cache.get(self._cache_key(url, params))
        if hit is None:
            return None
        value, fetched_at = hit
        self._fetch_state.fetched_at, self._fetch_state.from_cache = fetched_at, True
        return value

    def _remember(self, url: str, params: dict[str, Any], value: Any) -> None:
        """★성공한 응답만 여기 온다. 실패는 담지 않는다."""
        now = datetime.now(UTC)
        self._fetch_state.fetched_at = now
        if self._cache is not None:
            self._cache.put(self._cache_key(url, params), value, fetched_at=now,
                            ttl_seconds=getattr(self, "cache_ttl_seconds", None))

    def _fetch_xml(self, url: str, params: dict[str, Any]) -> str | None:
        """XML 을 문자열로 읽는다. 실패면 `None`.

        ★국가유산청은 JSON 을 주지 않는다 — `application/xml` 뿐이다.
          그래서 `_fetch_json` 을 못 쓴다. 규율(①~④)은 그대로 지킨다.
        """
        cached = self._from_cache(url, params)
        if cached is not None:
            return cached
        if not self._allow():
            return None
        try:
            response = self._get(url, params)
        except httpx.TimeoutException as exc:
            self._miss("timeout", str(exc))
            return None
        except httpx.HTTPError as exc:
            self._miss("transport_error", f"{type(exc).__name__}: {exc}")
            return None

        if response.status_code != 200:
            self._miss(f"http_{response.status_code}", response.text[:200])
            return None
        text = response.text
        if "<" not in text:
            # ★XML 이 아니면 대개 오류 페이지다. 빈 응답도 여기서 걸린다.
            self._miss("not_xml", text[:200])
            return None
        self._remember(url, params, text)
        return text

    @staticmethod
    def _body_error(payload: dict[str, Any]) -> str | None:
        """본문이 오류를 말하고 있으면 그 문구를, 아니면 `None`.

        ★하위 클래스가 공급자별 모양으로 덮어쓴다. 여기 있는 것은 실측으로
          확인한 두 모양이다(2026-09-09).
        """
        error = payload.get("error")
        if isinstance(error, list) and error:
            first = error[0]
            if isinstance(first, dict):
                return str(first.get("message") or first)
            return str(first)
        if isinstance(error, dict):
            return str(error.get("message") or error)
        # ★★평평한 오류 봉투. `response` 감싸개 **없이** 최상위에 코드가 온다
        #   (2026-09-10 실측: detailCommon2 에 안 받는 인자를 주면
        #    {"responseTime":..., "resultCode":"10",
        #     "resultMsg":"INVALID_REQUEST_PARAMETER_ERROR(contentTypeId)"}).
        #   이걸 안 잡으면 진짜 이유가 「모양이 이상하다」로 가려져,
        #   인자 하나 틀린 것을 찾는 데 한참 걸린다.
        code = str(payload.get("resultCode", ""))
        if code and code not in ("00", "0000") and "response" not in payload:
            return f'{code} {payload.get("resultMsg", "")}'.strip()
        header = payload.get("OpenAPI_ServiceResponse")
        if isinstance(header, dict):
            message = header.get("cmmMsgHeader", {})
            if isinstance(message, dict) and message.get("errMsg"):
                return str(message["errMsg"])
        return None

    def stamp(self, payload: dict[str, Any], *, source: str) -> dict[str, Any]:
        """조회 결과에 **확인 시각과 출처**를 박는다.

        ★`confirmed_at` 은 「우리가 확인한 시각」이지 「현장이 그러한 시각」이
          아니다. Team 이 이 둘을 섞어 말하면 예정 정보를 관찰처럼 전한다
          (v10 §4-D). Team 쪽 문구도 그렇게 적어 뒀다.
        ★캐시에서 꺼낸 값이면 **처음 받아 온 시각**을 쓰고 `from_cache` 를 붙인다 —
          재사용한 값에 「지금 확인」을 찍으면 확인 시각을 속인다(2026-09-14).
          시각은 한 번 쓰면 비운다 — 조회 없이 부른 `stamp()` 가 옛 시각을 물려받지 않게.
        """
        state = getattr(self, "_fetch_state", None)
        fetched_at = getattr(state, "fetched_at", None) if state is not None else None
        from_cache = bool(getattr(state, "from_cache", False)) if state is not None else False
        if state is not None:
            state.fetched_at, state.from_cache = None, False
        stamped = {**payload, "confirmed_at": (fetched_at or datetime.now(UTC)).isoformat(),
                   "source": source}
        if from_cache:
            stamped["from_cache"] = True
        return stamped


@dataclass
class TravelSources:
    """Team 이 쓰는 소스 묶음. ★없는 소스는 `None` 이고, 그건 「모름」이다.

    조립 지점(`app/composition.py`)이 만들어 `ReadToolbox` 에 넣는다.
    테스트는 넣지 않는다 — 그러면 도구가 전부 `None` 을 돌려주고 네트워크를
    타지 않는다.
    """

    weather: Any | None = None
    place: Any | None = None
    #: 한국천문연구원 특일 정보 — 공휴일. 공공데이터포털 공통 키를 쓴다.
    holiday: Any | None = None
    #: 국가유산청 — ★키가 필요 없어서 **항상 붙는다**(2026-09-10 실호출 확인).
    #:  좌표를 모르는 장소의 좌표를 채우는 데만 쓴다. 운영시간은 주지 않는다.
    heritage: Any | None = None
    #: 기상청 기상특보 — 지금 그 지역에 발효 중인 특보. 공공데이터포털 공통 키.
    warning: Any | None = None
    #: 외교부 여행경보 — 해외 확장용(v11 MVP 는 서울뿐이라 지금 부르는 Team 없음).
    advisory: Any | None = None
    #: 행정안전부 긴급재난문자. 키(`ACOP_DISASTER_MSG_API_KEY`)가 있으면 API 판,
    #:  없으면 **샘플 CSV 판**(2023-09 일부 기간만). `read.disruptions`(`active()`)가
    #:  쓴다. ★★`[미확보 2026-09-28]` `read.disaster`(구 도구, `read_tools.py`)는
    #:  `.near()`를 부르는데 `DisasterMsgApi`/`DisasterMsgCsv`엔 그 메서드가
    #:  없다 — 이 소스가 조립된 채로 `read.disaster`가 불리면 AttributeError다.
    #:  role-activity 쪽 재난·대체장소 기능을 다시 합칠 때 같이 정리해야 한다.
    disaster: Any | None = None
    #: 국토교통부 ITS 돌발상황 — 교통 사고·공사·통제(시내 도로 포함, 실측).
    traffic: Any | None = None
    #: 에어코리아 대기오염정보 — 구 측정소의 1시간 값(실측 2026-09-14).
    air: Any | None = None
    #: 기상청 지진정보 — 최근 지진(규모·진앙). 공공데이터포털 공통 키(실측 2026-09-14).
    earthquake: Any | None = None
    #: 경로에 걸린 운행·통제 사건(무정차·도로 통제) — 도로(UTIC·ITS) · 지하철 알림 · 버스 TOPIS 예고 공지를 합친 것
    #:  (`subway_notice.CompositeRouteEvents`). 재생 모드는 같은 `affecting()` 모양의 재생 입력을 끼운다.
    route_events: Any | None = None
    transit: Any | None = None
    #: 서울 열린데이터광장 실시간 지하철 도착정보(「실시간 지하철 인증키」) — **지금 값**. `arrivals(역, line=…)`. 판정에 넣지 않고 같이 놓고 보는 용도(2026-10-05)
    subway_arrival: Any | None = None
    route: Any | None = None
    #: 키가 없어 못 붙인 소스 이름들. ★조용히 비워 두지 않는다.
    unavailable: dict[str, str] = field(default_factory=dict)
    #: 소스 전부가 같은 것을 공유한다 - 따로 두면 한 키를 두 소스가 나눠
    #: 쓸 때 합계가 한도를 넘는다.
    limiter: Any | None = None
    #: 응답 캐시 — 제한기처럼 소스 전부가 하나를 공유한다(`cache.py`).
    cache: Any | None = None


def _public_data_key(settings: Any, field: str) -> str:
    """공공데이터포털 키 하나를 고른다 — 서비스별 키 > 공통 키.

    ★`Settings.public_data_key()` 가 있으면 그것을 쓰고, 없으면(테스트가 넣는
      가짜 설정 객체) 같은 규칙을 여기서 적용한다. 규칙을 두 벌 쓰지 않으려고
      한 곳에 모았다.
    """
    override = getattr(settings, field, "") or ""
    resolver = getattr(settings, "public_data_key", None)
    if callable(resolver):
        return resolver(override)
    return override or getattr(settings, "data_go_kr_key", "") or ""


def _proxy_would_apply(settings: Any, name: str) -> bool:
    """`apply_outbound_proxy()` 가 이 소스에 경유를 **실제로 걸까**. 키를 고를 때 쓴다.

    ★조립 끝에서 거는 것과 같은 조건이어야 한다 — 어긋나면 key 2 를 들고 바로 나가거나
      key 1 을 들고 서버로 나가 거절당한다.
    """
    import importlib.util

    url = (getattr(settings, "outbound_proxy_url", "") or "").strip()
    wanted = {part.strip() for part in (getattr(settings, "outbound_proxy_sources", "") or "")
              .split(",") if part.strip()}
    if not url or not ("*" in wanted or name in wanted):
        return False
    return not (url.startswith("socks") and importlib.util.find_spec("socksio") is None)


def apply_outbound_proxy(sources: TravelSources, *, url: str, names: str) -> str | None:
    """이름이 맞는 소스에만 경유 프록시를 건다. 못 걸면 **이유**를, 걸었으면 `None`.

    ★사슬(`FallbackWeather`·`FallbackAir`)은 안의 소스까지 본다 — 사슬 이름이 아니라
      실제로 바깥에 나가는 어댑터 이름으로 고른다.
    ★SOCKS 인데 `socksio` 가 없으면 **걸지 않는다.** 걸면 요청 순간 ImportError 로
      점검이 통째로 죽는다. 대신 이유를 돌려주고 조립이 `unavailable` 에 적는다.
    """
    import importlib.util

    url = (url or "").strip()
    wanted = {name.strip() for name in (names or "").split(",") if name.strip()}
    if not url or not wanted:
        return None
    if url.startswith("socks") and importlib.util.find_spec("socksio") is None:
        return (f"경유 프록시 {url} 는 SOCKS 인데 socksio 가 없다 — 경유하지 않는다. "
                f"`pip install socksio`(httpx[socks]) 뒤 다시 띄운다. 대상: {sorted(wanted)}")
    for value in vars(sources).values():
        for source in getattr(value, "sources", [value]):
            if isinstance(source, TravelSource) and ("*" in wanted or source.name in wanted):
                source._proxy = url
    return None


def build_travel_sources(settings: Any) -> TravelSources:
    """설정을 보고 붙일 수 있는 것만 붙인다.

    ★키가 없으면 **그 소스만** 빠진다. 앱이 죽지도 않고, 가짜로 채우지도
      않는다. 무엇이 왜 빠졌는지는 `unavailable` 이 들고 있고
      `/ui/admin` 이 그대로 보여 준다.
    """
    from .heritage import HeritageSource
    from .open_meteo import OpenMeteoWeather

    from app.core.settings import get_guardrails

    from .cache import ResponseCache
    from .ratelimit import RateLimiter, interval_for

    guardrails = get_guardrails()
    # ★몰림 허용·재사용 시간은 가드레일 한 곳에 둔다(`travel.rate_burst`·`travel.source_cache_seconds`).
    burst = max(1, int(guardrails.get("travel.rate_burst") or 1))
    limits = (settings.source_rate_limits()
              if hasattr(settings, "source_rate_limits") else {})
    by_source = guardrails.get("travel.rate_burst_by_source") or {}
    bursts = {name: max(1, int(by_source.get(name) or burst)) for name in limits}
    limiter = RateLimiter(
        intervals={name: interval_for(per_day, burst=bursts[name]) for name, per_day in limits.items()},
        bursts=bursts,
        max_wait_seconds=float(getattr(settings, "rate_max_wait_seconds", 5.0)))
    cache = ResponseCache(ttl_seconds=float(guardrails.get("travel.source_cache_seconds") or 0))

    sources = TravelSources(limiter=limiter, cache=cache)
    # ★키를 안 보고 붙인다 — 이 소스는 인증 파라미터 자체가 없다.
    sources.heritage = HeritageSource(limiter=limiter, cache=cache)
    # ── 기상: 1차 + 대체 (v11 §0-4 결정 15) ─────────────────────────
    # ★`weather_provider` 는 **어느 쪽이 먼저인가**만 정한다. 붙일 수 있는 것은
    #   전부 붙이고 나머지를 대체로 둔다 — 1차가 못 주면 대체가 값을 낸다.
    #   ☆2026-09-14 전에는 둘 중 하나만 붙였고, `kma` 를 고르면 `kma.py` 가 없어
    #     `ModuleNotFoundError` 로 **기동이 안 됐다.**
    provider = getattr(settings, "weather_provider", "open_meteo")
    weather_by_name: dict[str, Any] = {
        # ★키가 필요 없다(2026-09-09 실호출 200 확인). 그래서 조건 없이 붙는다.
        "open_meteo": OpenMeteoWeather(limiter=limiter, cache=cache),
    }
    # ★서비스별 키가 비면 공공데이터포털 공통 키로 떨어진다 — 계정 하나면
    #   키도 하나이기 때문이다. 둘 다 비면 빈 문자열이고 그건 「없음」이다.
    kma_key = _public_data_key(settings, "kma_api_key")
    if kma_key:
        from .kma import KmaWeather
        weather_by_name["kma"] = KmaWeather(service_key=kma_key, limiter=limiter, cache=cache)
    else:
        sources.unavailable["weather_kma"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_KMA_API_KEY)가 비어 있다. "
            "공공데이터포털 「기상청_단기예보 조회서비스」 활용신청 후 "
            ".env.apikeys 에 채운다(승인 최소 1일). 대체 소스 없이 Open-Meteo 만 쓴다.")

    if provider not in ("open_meteo", "kma"):
        sources.unavailable["weather"] = f"모르는 weather_provider: {provider}"
    else:
        order = [provider] + [name for name in weather_by_name if name != provider]
        chain = [weather_by_name[name] for name in order if name in weather_by_name]
        if len(chain) == 1:
            sources.weather = chain[0]
        else:
            from .weather_chain import FallbackWeather
            sources.weather = FallbackWeather(chain)

    warning_key = _public_data_key(settings, "kma_warning_api_key")
    if warning_key:
        from .kma_warning import KmaWarningSource
        sources.warning = KmaWarningSource(service_key=warning_key, limiter=limiter, cache=cache)
    else:
        sources.unavailable["warning"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_KMA_WARNING_API_KEY)가 비어 있다. "
            "공공데이터포털 「기상청_기상특보 조회서비스」 활용신청 후 .env.apikeys 에 채운다.")

    quake_key = _public_data_key(settings, "kma_earthquake_api_key")
    if quake_key:
        from .kma_earthquake import KmaEarthquakeSource
        sources.earthquake = KmaEarthquakeSource(service_key=quake_key, limiter=limiter, cache=cache)
    else:
        sources.unavailable["earthquake"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_KMA_EARTHQUAKE_API_KEY)가 비어 있다. "
            "공공데이터포털 「기상청_지진정보 조회서비스」(15000420) 활용신청 후 .env.apikeys 에 채운다.")

    mofa_key = _public_data_key(settings, "mofa_api_key")
    if mofa_key:
        from .mofa import MofaTravelAlarm
        sources.advisory = MofaTravelAlarm(service_key=mofa_key, limiter=limiter, cache=cache)
    else:
        sources.unavailable["advisory"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_MOFA_API_KEY)가 비어 있다. "
            "공공데이터포털 「외교부_국가·지역별 여행경보」 활용신청 후 .env.apikeys 에 채운다.")

    # ★재난문자 — **키가 있으면 API 판, 없으면 샘플 CSV 판**(2026-09-14 키 발급).
    #   ☆전에는 `disaster_msg_source` 로 골랐는데 그 이름이 `Settings` 에 없어서(extra=forbid)
    #     설정으로 바꿀 길이 없었다 — 키를 넣어도 영원히 샘플 판이었을 자리다.
    #   샘플 판은 샘플 기간(2023-09-16~19) 밖을 「미연결」로 답한다 — 「없음」이라 하지 않는다.
    disaster_key = (getattr(settings, "disaster_msg_api_key", "") or "").strip()
    if disaster_key:
        from .disaster_msg import DisasterMsgApi

        # ★하루 한도가 낮아(100 `[미확인]`) 이 소스만 캐시를 길게 둔다.
        ttl = float(guardrails.get("travel.disaster_msg_cache_seconds") or 0) or None
        sources.disaster = DisasterMsgApi(service_key=disaster_key, limiter=limiter,
                                          cache=cache, cache_ttl_seconds=ttl)
    else:
        from pathlib import Path

        from .disaster_msg import DEFAULT_SAMPLE_PATH, DisasterMsgCsv
        sample = Path(getattr(settings, "disaster_msg_sample_path", "") or DEFAULT_SAMPLE_PATH)
        if sample.exists():
            sources.disaster = DisasterMsgCsv(sample)
        else:
            sources.unavailable["disaster"] = f"재난문자 샘플 CSV 가 없다: {sample}"

    # ── 교통 돌발: ITS + UTIC 를 **합친다**(`traffic_chain.py`) ─────────────
    #   둘이 보는 것이 다르다 — 시내 사고·행사·집회는 UTIC 가 본체다.
    traffic_sources: list[Any] = []
    route_event_sources: list[Any] = []
    its: Any = None
    # ★ITS 는 공공데이터포털 공통 키가 아니라 **ITS 가 발급한 키**를 쓴다.
    its_key = getattr(settings, "its_api_key", "") or ""
    if its_key:
        from .its_traffic import ItsTrafficEvents
        its = ItsTrafficEvents(service_key=its_key, limiter=limiter, cache=cache)
        traffic_sources.append(its)
    else:
        sources.unavailable["traffic_its"] = (
            "ACOP_ITS_API_KEY 가 비어 있다. ITS 국가교통정보센터(its.go.kr/opendata) 에서 "
            "발급한 키를 .env.apikeys 에 채운다. ★공공데이터포털 키와 다른 키다.")
    # ★UTIC 키는 IP 에 묶여 있다 — **실제로 나가는 길**에 맞는 키를 고른다.
    #   서버 경유가 걸리면 key 2, 아니면 key 1. 다른 쪽 키로 대신하지 않는다(어차피 거절).
    proxied = _proxy_would_apply(settings, "utic")
    utic_key = settings.utic_key(proxied=proxied) if hasattr(settings, "utic_key") else ""
    if utic_key:
        from .utic import UticIncidents, UticRouteEvents
        utic = UticIncidents(service_key=utic_key, limiter=limiter, cache=cache)
        traffic_sources.append(utic)
        # ★같은 레코드로 감시 루프의 **경로 사건**(도로 통제)도 답한다 — 요청은 캐시로 나눠 쓴다.
        road_events: Any = UticRouteEvents(utic)
        if its is not None:
            # ★`[2026-10-05]` 도로 사건은 UTIC(1차) + ITS(2차)를 **함께** 묻는다 — UTIC 키는 고정 IP 에 묶여 서버 이전·경유 장애 때 통째로 거절되고,
            #   그 전엔 경로 사건이 곧바로 치명이 됐다(결정 15 의 대체가 이 자리에 없었다). 둘 다 못 읽을 때만 치명.
            from .its_traffic import ItsRouteEvents
            from .subway_notice import AlternateRouteEvents
            road_events = AlternateRouteEvents(road_events, ItsRouteEvents(its))
        route_event_sources.append(road_events)
    elif its is not None:
        from .its_traffic import ItsRouteEvents
        route_event_sources.append(ItsRouteEvents(its))        # ★UTIC 키가 없어도 ITS 가 도로 사건에 답한다
    if not utic_key:
        sources.unavailable["traffic_utic"] = (
            f"ACOP_UTIC_API_KEY_{2 if proxied else 1} 가 비어 있다 — "
            f"{'서버 경유' if proxied else '바로 부르는'} 길의 IP 에 등록된 키가 필요하다.")
    # ★`[2026-10-04]` 지하철 무정차 통과(서울교통공사 지하철알림정보 · 1~8호선) — 공통 키. 감시 루프의 경로 사건에 도로(UTIC)와 함께 답한다.
    subway_key = _public_data_key(settings, "subway_notice_api_key")
    if subway_key:
        from .subway_notice import SubwayNotices, SubwayRouteEvents
        route_event_sources.append(SubwayRouteEvents(SubwayNotices(service_key=subway_key, limiter=limiter, cache=cache)))
    else:
        sources.unavailable["subway_notice"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_SUBWAY_NOTICE_API_KEY)가 비어 있다. 공공데이터포털 「서울교통공사_지하철알림정보」 "
            "활용신청 후 .env.apikeys 에 채운다.")
    # ★`[2026-10-05]` 서울 실시간 지하철 도착정보 — 「실시간 지하철 인증키」(일반 키와 별개 · 하루 1,000건). 못 붙이면 이름으로 남긴다
    metro_key = (getattr(settings, "seoul_metro_api_key", "") or "").strip()
    if metro_key:
        from .seoul_subway import SeoulSubwayArrival
        sources.subway_arrival = SeoulSubwayArrival(service_key=metro_key, limiter=limiter, cache=cache)
    else:
        sources.unavailable["subway_arrival"] = (
            "ACOP_SEOUL_METRO_API_KEY 가 비어 있다. 서울 열린데이터광장 「실시간 지하철 인증키」를 .env.apikeys 에 채운다. "
            "★일반 인증키(ACOP_SEOUL_OPENAPI_KEY)와 **다른 키**다.")
    # ★`[2026-10-01 · 이동 문제목록 #39 · 2026-10-05 합치기]` 버스 정류장 무정차·우회 — 서울시 TOPIS 예고 공지(누리집 공지판 · 키 없음 ·
    #   운행일마다 한 번 읽는다 `topis_notice.py`). 대상 `버스:<노선>` 에 답하고 그 밖은 `unsupported()` — 위 합치기(CompositeRouteEvents)가
    #   소스마다 자기 대상만 묻는다. 정류장 번호 → 지나는 노선 표는 이동 계산기 자료 파일에서 읽는다(자료 폴더가 비면 버스 대상은 전부
    #   「확인 못 한 대상」). ☆지하철은 위 `subway_notice.py`(팀장 판) 하나다 — 이동 담당이 따로 만들었던 `seoulmetro_alert.py` 는 내렸다.
    from .route_events_chain import MobilityTables
    from .topis_notice import TopisNotices
    tables = MobilityTables(getattr(settings, "mobility_data_dir", "") or "")
    route_event_sources.append(TopisNotices(stops=tables.stop_table, limiter=limiter, cache=cache))
    if len(route_event_sources) == 1:
        sources.route_events = route_event_sources[0]
    elif route_event_sources:
        from .subway_notice import CompositeRouteEvents
        sources.route_events = CompositeRouteEvents(route_event_sources)
    if not traffic_sources:
        sources.unavailable["traffic"] = "교통 돌발 — ITS·UTIC 키가 모두 없다"
    elif len(traffic_sources) == 1:
        sources.traffic = traffic_sources[0]
    else:
        from .traffic_chain import CombinedTraffic
        sources.traffic = CombinedTraffic(traffic_sources)

    # ── 대기질: 1차(에어코리아 측정값) + 대체(Open-Meteo 모델 추정) — 결정 15 ─────
    # ★☆2026-09-14 실측 — 에어코리아가 연달아 504 SERVICETIMEOUT 을 냈다. 대체가 없으면
    #   그때마다 치명이다. 대체는 키가 없어서 **항상** 붙는다.
    from .air_quality import AirKoreaRealtime, FallbackAir, OpenMeteoAir

    air_chain: list[Any] = []
    air_key = _public_data_key(settings, "airkorea_api_key")
    if air_key:
        air_chain.append(AirKoreaRealtime(service_key=air_key, limiter=limiter, cache=cache))
    else:
        sources.unavailable["air_airkorea"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_AIRKOREA_API_KEY)가 비어 있다. "
            "공공데이터포털 「에어코리아 대기오염정보」 활용신청 후 채운다. "
            "그동안은 Open-Meteo 모델 추정값만 쓴다.")
    air_chain.append(OpenMeteoAir(limiter=limiter, cache=cache))
    sources.air = air_chain[0] if len(air_chain) == 1 else FallbackAir(air_chain)

    holiday_key = _public_data_key(settings, "holiday_api_key")
    if holiday_key:
        from .holiday import HolidaySource
        sources.holiday = HolidaySource(service_key=holiday_key, limiter=limiter, cache=cache)
    else:
        sources.unavailable["holiday"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_HOLIDAY_API_KEY)가 비어 있다. "
            "공공데이터포털 「한국천문연구원_특일 정보」 활용신청 후 "
            ".env.apikeys 에 채운다.")

    if not _public_data_key(settings, "tour_api_key"):
        sources.unavailable["place"] = (
            "ACOP_DATA_GO_KR_KEY(또는 ACOP_TOUR_API_KEY)가 비어 있다. "
            "공공데이터포털 「한국관광공사_국문 관광정보 서비스」 활용신청 후 "
            ".env.apikeys 에 채운다.")
    else:
        from .tour_api import TourApiPlace
        sources.place = TourApiPlace(
            service_key=_public_data_key(settings, "tour_api_key"), limiter=limiter, cache=cache)

    # ★`[정리 2026-09-28]` `disaster_api_key`(구, `ACOP_DISASTER_API_KEY`) 조립
    #   블록을 지웠다 — `DisasterMsgSource` 클래스가 이번 병합으로 없어졌고
    #   (위 `disaster_msg_api_key` 블록이 `sources.disaster`를 이미 채운다),
    #   그대로 두면 키가 있을 때 `ImportError`가 난다.

    if not getattr(settings, "odsay_api_key", ""):
        sources.unavailable["transit"] = (
            "ACOP_ODSAY_API_KEY 가 비어 있다. lab.odsay.com 에서 발급해 "
            ".env.apikeys 에 채운다. ★공공데이터포털 키와 **다른 키**다.")
    else:
        # ★ODsay 어댑터(`odsay.py`)는 아직 없다 — 한 번도 커밋된 적이 없다. ☆2026-09-18 전에는 여기서
        #   `from .odsay import OdsayTransit` 를 불러, 키를 채우는 순간 조립 전체가 ModuleNotFoundError 로
        #   죽었다(`kma.py` 때와 같은 모양). 어댑터가 생기기 전까지 키가 있어도 **없다고 이름으로 남긴다** —
        #   조용한 대체가 아니다(RULE.md §3.2). 경로(`read.route`)·운행(`read.transit`)은 「모름」이다.
        #   경위: wiki/records/reports/debugs/2026-09-18_1520_ODsay_키를_넣으면_감시소스_조립이_죽는다.md
        sources.unavailable["transit"] = (
            "ODsay 어댑터 미구현 — ACOP_ODSAY_API_KEY 는 채워져 있지만 odsay.py 가 없다. "
            "대중교통 경로·운행은 조회하지 않는다.")

    # ── 고정 IP 서버 경유 (IP 에 묶인 소스만) ─────────────────────────
    reason = apply_outbound_proxy(sources, url=getattr(settings, "outbound_proxy_url", ""),
                                  names=getattr(settings, "outbound_proxy_sources", ""))
    if reason:
        sources.unavailable["outbound_proxy"] = reason
    return sources
