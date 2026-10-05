# -*- coding: utf-8 -*-
"""장소 찾기 — 고객이 쓴 이름 하나 → 장소 **하나**. `[2026-09-27]` 설계서 §4

    1  우리 장소 표(`places`) 정확 일치 — NFKC·공백·괄호·지점 접미어 정규화
    2  (별칭 — 표가 아직 없다. 쌓이면 여기)
    3  자모 오타 교정 — 우리 장소 이름과 자모 편집거리 ÷ 이름 길이 ≤ 0.2 `[추정]`
    4  관광공사 이름 정확 일치(서울 법정동 필터) → 없으면 카카오로 이름을 찾고 그 이름으로 관광공사 재확인
       → 관광공사에도 없으면 카카오 값을 **그 여행 항목에만**(출처 표시)
    5  (Places 유료 — 예산 장치는 있다. 이 단계는 아직 잇지 않았다)

★`[2026-10-01]` 요식 원장(`dining`) — 미리 적재해 둔 식당 데이터. 관광공사 자리(`tour`)는 지금 액티비티 CSV 라
  식당이 없다(액티비티 쪽 결정 8e9d8d0). 식당을 그 자리에 기대지 않고 요식이 따로 찾는다.
    식사(`kind_hint="dining"` 또는 앞에 끼니 말) — 1 → 3 → **요식 원장** → 관광공사 자리 → 카카오. 끼니 말(「점심」)은 뗀다
    그 밖의 항목                           — 1 → 3 → 관광공사 자리 → **요식 원장** → 카카오(관광공사 자리가 그대로 먼저)
    카카오가 찾은 이름도 원장에서 다시 확인한다 — 확인되면 원장 값(관광공사 ID 포함)을 싣는다.

★「원문이 가리킨 만큼만 좁힌다」(§4-2) — 「광장시장 빈대떡」은 전체로 먼저 찾고, 안 되면 **뒤 단어를 떼어**
  「광장시장」으로 찾는다. ★좁힌 이름은 우리 표·관광공사에서만 받는다(카카오는 원문 전체로만). 카카오 1위 가게(원문에 없는 이름)를 고르지 않는다 — 카카오 결과는 **원문 조각과 이름이
  같거나 원문 조각으로 시작할 때만** 받는다.
★서울 밖 — 관광공사는 서울 법정동으로 거르고, 카카오는 서울 사각 + 주소로 거른다. 그래도 없으면 `unresolved`.
★약관 — 관광공사·카카오 값은 **공용 표에 넣지 않는다**(콘텐츠랩 「로컬서버 저장방식 금지」 해석 대기 ·
  카카오 「디렉터리 입력」 금지). 결과는 이 접수의 값(`intake_claims`, 근거에 출처)으로만 남는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any

SEOUL_AREA_CODE = "1"
#: 이름 끝의 지점 표시 — 「토속촌삼계탕 본점」 = 「토속촌삼계탕」
_BRANCH = re.compile(r"\s*(본점|직영점|\S+점)$")
#: 자모 오타로 받는 상한(편집거리 ÷ 긴 이름의 자모 수) — 설계서 §4-1 `[추정]`
TYPO_RATIO = 0.2
#: 관광공사 종류 → 우리 종류
KIND_BY_CONTENT_TYPE = {"39": "dining"}
#: 「그 이름이 없다」는 뜻의 실패 — 나머지(속도 제한 · 시간 초과 · 연결 · 응답 이상)는 **조회가 막힌 것**이다.
#: ☆2026-09-27 실측: 9개 질의 중 11번째 관광공사 호출이 속도 제한에 걸렸는데 「장소를 정하지 못했다」로만 남았다 —
#:   없는 것과 못 물어본 것이 같은 말이 되면 고객은 틀린 이유로 고치고, 우리는 한도 부족을 모른다.
NOT_THERE = {"not_found", "no_exact_title", "ambiguous", "no_coordinates", "no_place_name"}
_HANGUL = re.compile(r"[가-힣]")
#: 식사 항목 앞의 끼니 말 — 뒤에서부터 좁히는 규칙으로는 떼지 못한다(「점심 토속촌삼계탕」 → 「점심」만 남는다)
MEAL_WORDS = frozenset({"아침", "조식", "점심", "중식", "저녁", "석식", "브런치", "식사"})
#: 떼어도 확인이 필요 없는 말 — **무엇을 하는지**만 말하고 다른 곳을 가리키지 않는다(「경복궁 관람」 = 경복궁).
#: ☆2026-09-27 실제 확인 화면: 이런 항목까지 전부 「확인 필요」가 붙어 정작 확인할 곳이 묻혔다.
#: ★「빈대떡」(음식 — 가게일 수 있다) · 「카약」(활동 자체 — 다른 업체일 수 있다)은 여기 넣지 않는다.
PLAIN_TAILS = frozenset({"관람", "산책", "구경", "투어", "방문", "둘러보기", "나들이", "견학", "탐방", "관광"})


@dataclass
class Resolved:
    status: str                         # resolved · unresolved
    method: str | None = None           # places · typo · tour_api · kakao
    name: str | None = None             # 고른 장소의 이름(출처가 준 그대로)
    query: str | None = None            # 실제로 찾은 원문 조각(좁힌 결과)
    kind: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    place_id: str | None = None         # 우리 장소 표의 id(1·3단계)
    content_id: str | None = None       # 관광공사 id
    needs_review: bool = False
    note: str | None = None
    tried: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)   # 막힌 조회(「소스:사유」) — 없음과 다르다
    #: 이름이 딱 맞지 않지만 **종류가 맞는** 카카오 후보(「한강 카약」 → 카약 업체들, 「북한산 둘레길」 → 구간들).
    #: 여기서는 고르지 않는다 — 처리 흐름이 앞뒤 일정에 가장 가까운 하나를 고른다(설계서 §4-2)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    #: 항목 종류 단서 — 원문 전체로 찾은 카카오 결과가 대부분 음식점이면 「dining」(「광장시장 빈대떡」 → 광장시장 + 식사)
    item_kind: str | None = None

    def evidence(self) -> dict[str, Any]:
        source = {"places": "places", "typo": "places", "tour_api": "tour_api", "kakao": "kakao",
                  "dining_ledger": "dining_ledger"}.get(self.method or "")
        return {"source": source, "method": self.method, "name": self.name, "query": self.query,
                "place_id": self.place_id, "content_id": self.content_id, "tried": self.tried,
                "blocked": self.blocked}


def normalize(name: str) -> str:
    text = unicodedata.normalize("NFKC", name or "").strip()
    text = re.sub(r"[\(\[（【].*?[\)\]）】]", "", text)
    text = _BRANCH.sub("", text)
    return re.sub(r"[\s·\-_/]+", "", text).lower()


def jamo(text: str) -> str:
    """한글 음절을 자모로 푼다(오타 비교용). 한글이 아니면 그대로."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [chr(0x1100 + code // 588), chr(0x1161 + (code % 588) // 28)]
            if code % 28:
                out.append(chr(0x11A7 + code % 28))
        else:
            out.append(ch)
    return "".join(out)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def narrowings(title: str) -> list[str]:
    """원문 조각 → 찾을 이름들(긴 것부터). 「광장시장 빈대떡」 → [「광장시장 빈대떡」, 「광장시장」]."""
    words = title.split()
    return [" ".join(words[:n]) for n in range(len(words), 0, -1) if len(" ".join(words[:n])) >= 2]


def resolve(title: str, *, our_places: list[dict[str, Any]], tour: Any = None, kakao: Any = None,
            kind_hint: str | None = None, aliases: dict[str, str] | None = None, dining: Any = None) -> Resolved:
    # ★앞에 끼니 말이 있으면 식사다 — 규칙 읽기는 「12:00 점심 토속촌삼계탕」에 식사 표시를 하지 않는다(모델이 읽은 줄만)
    words = title.split()
    meal = kind_hint == "dining" or (len(words) > 1 and words[0] in MEAL_WORDS)
    if meal:
        while len(words) > 1 and words[0] in MEAL_WORDS:
            words = words[1:]
        title = " ".join(words)
        kind_hint = "dining"
    # 2 — 별칭(고객이 고친 표현 · 우리가 넣은 기본값). ★값은 고객 글이다 — 바꾼 이름으로 **다시 찾는다**
    alias = (aliases or {}).get(normalize(title))
    if alias and normalize(alias) != normalize(title):
        found = resolve(alias, our_places=our_places, tour=tour, kakao=kakao, kind_hint=kind_hint, dining=dining)
        found.tried = [f"alias:{title}→{alias}"] + found.tried
        found.needs_review = True
        found.note = "; ".join(filter(None, [f"별칭으로 「{alias}」를 찾았다", found.note]))
        return found
    tried: list[str] = []
    blocked: list[str] = []
    candidates: list[dict[str, Any]] = []
    full_hits: list[dict[str, Any]] = []
    by_name = {normalize(p["name"]): p for p in our_places if p.get("name")}
    for query in narrowings(title):
        key = normalize(query)
        # 1 — 우리 장소 표 정확 일치
        if key in by_name:
            place = by_name[key]
            return Resolved("resolved", "places", place["name"], query, place.get("kind"), place.get("latitude"),
                            place.get("longitude"), place_id=str(place["place_id"]),
                            needs_review=not _plain(query, title), tried=tried + [f"places:{query}"],
                            note=None if query == title else f"원문 「{title}」에서 「{query}」로 좁혔다")
        tried.append(f"places:{query}")
        # 3 — 자모 오타(우리 장소 이름과)
        best = _typo(key, by_name)
        if best is not None:
            place, ratio = best
            return Resolved("resolved", "typo", place["name"], query, place.get("kind"), place.get("latitude"),
                            place.get("longitude"), place_id=str(place["place_id"]), needs_review=True,
                            tried=tried, note=f"오타로 보고 「{place['name']}」로 고쳤다(자모 거리 비율 {ratio:.2f})")
        # 4 — 관광공사(서울) 정확 일치 · 요식 원장. 식사면 원장이 먼저다(관광공사 자리는 지금 액티비티 CSV)
        for slot in (("dining", "tour") if meal else ("tour", "dining")):
            if slot == "dining":
                if dining is None:
                    continue
                found = _tour(dining, query, blocked, label="dining_ledger")
                tried.append(f"dining_ledger:{query}")
                if found is not None:
                    return _from_tour(found, query, title, tried, method="dining_ledger")
                continue
            found = _tour(tour, query, blocked)
            tried.append(f"tour_api:{query}")
            if found is not None:
                done = _from_tour(found, query, title, tried)
                done.item_kind = _food_hint(query, title, full_hits)
                return done
        # 4 — 카카오로 이름 찾기 → 관광공사 재확인 → 없으면 카카오 값(그 여행에만)
        # ★카카오는 **원문 전체**로만 찾는다. 좁힌 이름(뒤 단어를 뗀 것)은 우리 표·관광공사에서만 받는다 —
        #   「한강 카약」을 「한강」으로 좁혀 카카오가 강 자체를 골랐다(2026-09-27 실측). 뗀 말이 활동 자체일 수 있다.
        if kakao is not None and query == title:
            hits = kakao.search(query)
            tried.append(f"kakao:{query}")
            if hits is None:                        # ★못 불렀다(예산 · 시간 초과 · 연결) — 결과 0건과 다르다
                blocked.append("kakao:" + (max(kakao.misses, key=kakao.misses.get)
                                           if getattr(kakao, "misses", None) else "unavailable"))
                hits = []
            full_hits = hits
            if not _HANGUL.search(query):
                found = _romanized(query, title, hits, tour, tried, blocked)
                if found is not None:
                    return found
                continue
            match = _kakao_match(query, hits)
            if match is None:
                # ★이름이 특정하지 않는다 — 종류가 맞는 후보를 **남겨 두기만** 하고 좁혀 찾기를 계속한다.
                #   ☆2026-09-28 실측: 여기서 바로 돌려주니 「광장시장 빈대떡」이 「순희네빈대떡」(원문에 없는 가게)이 됐다 —
                #   설계서 §4-2 가 막은 바로 그것. 좁힌 이름(「광장시장」)이 먼저이고, 후보는 좁혀도 못 찾을 때만 쓴다
                candidates = candidates or _kind_hits(query, hits)[:5]
            if match is not None:
                # 카카오가 찾은 이름을 미리 적재한 데이터에서 다시 확인한다 — 식당(FD6 · CE7)이나 식사면 원장부터
                food = meal or match["category_group"] in ("FD6", "CE7")
                for slot in (("dining", "tour") if food else ("tour", "dining")):
                    if slot == "dining":
                        again = (_tour(dining, match["name"], blocked, label="dining_ledger")
                                 if dining is not None and match["name"] != query else None)
                        if again is not None:
                            tried.append(f"dining_ledger:{match['name']}")
                            return _from_tour(again, query, title, tried, via=match["name"], method="dining_ledger")
                        continue
                    again = _tour(tour, match["name"], blocked) if match["name"] != query else None
                    if again is not None:
                        tried.append(f"tour_api:{match['name']}")
                        return _from_tour(again, query, title, tried, via=match["name"])
                exact = normalize(match["name"]) == key
                return Resolved("resolved", "kakao", match["name"], query,
                                "dining" if match["category_group"] in ("FD6", "CE7") else (kind_hint or "activity"),
                                match["latitude"], match["longitude"], needs_review=not exact or query != title,
                                tried=tried, note=("카카오에서 찾은 곳 — 관광공사에 없어 이 여행에만 싣는다"
                                                   + ("" if exact else f" · 이름이 원문과 달라 확인이 필요하다(「{match['name']}」)")))
    if candidates:
        return Resolved("unresolved", tried=tried, needs_review=True, blocked=blocked, candidates=candidates,
                        note=f"이름이 특정하지 않아 종류가 맞는 {len(candidates)}곳 중 앞뒤 일정에 가까운 곳으로 고른다")
    note = "장소를 정하지 못했다 — 고객이 고친다"
    if blocked:
        note = f"조회가 막혀({', '.join(sorted(set(blocked)))}) 장소를 정하지 못했다 — 없는 곳이라는 뜻이 아니다. 고객이 고친다"
    return Resolved("unresolved", tried=tried, needs_review=True, note=note, blocked=blocked)


def _romanized(query: str, title: str, hits: list[dict[str, Any]], tour: Any, tried: list[str],
               blocked: list[str]) -> Resolved | None:
    """로마자 이름(「Gyeongbokgung」) — 원문과 글자가 같을 수 없어서 카카오 이름을 **관광공사 확인용으로만** 쓴다.

    ☆2026-09-27 실측: 설계서 실측 8 에서는 카카오 1위가 경복궁이었는데, 다시 재니 1~5위가 모두 식당 체인
      「경복궁 ○○점」이었다. 1위를 받으면 식당을 고른다. 그래서 지점 표시를 뗀 이름(「경복궁」)을 관광공사에
      물어 **관광공사가 확인한 것만** 받는다(확인 필요). 카카오 값 자체는 싣지 않는다.
    """
    names: list[str] = []
    for hit in hits:
        base = _BRANCH.sub("", hit["name"]).strip()
        if base and base not in names:
            names.append(base)
    for name in names[:2]:                      # 관광공사 호출을 아낀다 — 앞의 두 이름만
        again = _tour(tour, name, blocked)
        tried.append(f"tour_api:{name}")
        if again is not None:
            return _from_tour(again, query, title, tried, via=name)
    return None


def _typo(key: str, by_name: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], float] | None:
    if len(key) < 2:
        return None
    target = jamo(key)
    best = None
    for name_key, place in by_name.items():
        other = jamo(name_key)
        ratio = edit_distance(target, other) / max(len(target), len(other))
        if 0 < ratio <= TYPO_RATIO and (best is None or ratio < best[1]):
            best = (place, ratio)
    return best


def _tour(tour: Any, name: str, blocked: list[str], *, label: str = "tour_api") -> dict[str, Any] | None:
    """관광공사 자리 · 요식 원장이 같은 계약(`find` + `misses`)이다. `label` 은 막힌 조회에 붙는 이름."""
    if tour is None:
        return None
    before = dict(getattr(tour, "misses", {}) or {})
    found = tour.find(name, area_code=SEOUL_AREA_CODE)
    for reason, count in (getattr(tour, "misses", {}) or {}).items():
        if count > before.get(reason, 0) and reason not in NOT_THERE:
            blocked.append(f"{label}:{reason}")
    if found is None or not str(found.get("address") or "").startswith("서울"):
        return None
    return found


def _from_tour(found: dict[str, Any], query: str, title: str, tried: list[str], via: str | None = None,
               method: str = "tour_api") -> Resolved:
    note = []
    if query != title:
        note.append(f"원문 「{title}」에서 「{query}」로 좁혔다")
    if via:
        note.append(f"카카오로 「{via}」를 찾아 {'요식 원장' if method == 'dining_ledger' else '관광공사'}에서 확인했다")
    return Resolved("resolved", method, found.get("matched_title"), query,
                    KIND_BY_CONTENT_TYPE.get(str(found.get("content_type_id")), "activity"),
                    found.get("latitude"), found.get("longitude"), content_id=found.get("content_id"),
                    # ★카카오로 찾은 이름이 원문과 다르면(「토속촌」 → 「토속촌삼계탕」) 확인을 받는다
                    needs_review=not _plain(query, title) or (via is not None and normalize(via) != normalize(query)),
                    tried=tried, note=" · ".join(note) or None)


def _plain(query: str, title: str) -> bool:
    """좁히지 않았거나, 뗀 말이 전부 `PLAIN_TAILS` 인가 — 그러면 확인이 필요 없다."""
    dropped = title.split()[len(query.split()):] if title.startswith(query) else None
    return query == title or bool(dropped) and all(word in PLAIN_TAILS for word in dropped)


def _kakao_match(query: str, hits: list[dict[str, Any]]) -> dict[str, Any] | None:
    """★원문 조각과 **같은 이름**이 먼저, 없으면 원문 조각으로 **시작하는** 이름이 **하나뿐일 때**.
    원문에 없는 가게는 고르지 않는다. 시작하는 이름이 여럿이면(둘레길 구간들) 고르지 않고 후보로 넘긴다."""
    key = normalize(query)
    exact = [h for h in hits if normalize(h["name"]) == key]
    if exact:
        return exact[0]
    prefix = [h for h in hits if normalize(h["name"]).startswith(key)]
    return prefix[0] if len(prefix) == 1 else None


def _food_hint(query: str, title: str, hits: list[dict[str, Any]]) -> str | None:
    """좁혀서 찾았는데(뗀 말이 있는데) 원문 전체로 찾은 카카오 결과의 과반이 음식점·카페면 항목은 식사다."""
    if query == title or not hits:
        return None
    food = sum(1 for h in hits if h.get("category_group") in ("FD6", "CE7"))
    return "dining" if food * 2 > len(hits) else None


def _kind_hits(query: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """종류가 맞는 후보 — 이름이 원문으로 시작하거나(구간들), 원문의 **마지막 말**(활동 · 종류, 2자 이상)이 이름이나
    분류에 든 것. 「한강 카약」 → 「○○카약」 업체들, 「북한산 둘레길」 → 「북한산둘레길 n구간」들."""
    key = normalize(query)
    words = query.split()
    last = normalize(words[-1]) if len(words) > 1 else ""
    out = []
    for hit in hits:
        name, category = normalize(hit["name"]), normalize(hit.get("category") or "")
        if name.startswith(key) or (len(last) >= 2 and (last in name or last in category)):
            out.append(hit)
    return out


def distance_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """두 좌표 사이 거리(미터, 구면 근사)."""
    import math

    r = 6_371_000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def nearest(candidates: list[dict[str, Any]], neighbours: list[tuple[float, float]]) -> tuple[dict[str, Any], float | None]:
    """앞뒤 일정 좌표에 가장 가까운 후보(거리 합이 가장 작은 것). 이웃이 없으면 카카오 첫 후보(관련도 순)."""
    if not neighbours:
        return candidates[0], None
    scored = [(sum(distance_m(c["latitude"], c["longitude"], lat, lon) for lat, lon in neighbours), c)
              for c in candidates]
    best = min(scored, key=lambda s: s[0])
    return best[1], best[0] / len(neighbours)


__all__ = ["Resolved", "_kind_hits", "distance_m", "edit_distance", "jamo", "narrowings", "nearest", "normalize", "resolve"]
