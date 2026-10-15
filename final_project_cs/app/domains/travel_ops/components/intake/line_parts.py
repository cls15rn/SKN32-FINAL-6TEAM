# -*- coding: utf-8 -*-
"""한 줄을 **뜻의 조각**으로 읽는다 — 이름이 있는 줄인가, 종류 + 지역만 적은 줄인가. `[2026-10-03 ui 세션 요청서 「장소 해석 개선」]`

☆왜: 「성수 예약 식당」 · 「서울역 인근 저녁 식당」 · 「이태원 소품숍」 · 「호텔 조식」은 **가게 이름이 없다**. 서버는 이런 줄도 가게 이름으로 찾았고(없는 가게를 찾아 글자를 줄여 가며),
종류를 활동으로 읽었고, 후보를 앞뒤 일정의 한가운데에서 찾아 동대문 쪽 활동이 권해졌다(실서버 실측). 단어 하나를 고칠 문제가 아니라 **「이름 없는 줄」이라는 개념이 빠진 것**이다.

★읽는 법: 줄을 띄어쓰기로 나누고, 조각마다 **표(`config/place_terms.yaml`)와 지명 사전(`areas.py`)의 말로 전부 설명되면**(붙여 쓴 「성수동쇼핑」도 나눈다) 그 조각은 일반 말이다.
    종류 말(식당 · 쇼핑 …) · 끼니 말(조식 · 저녁 …) · 지역 말(성수 · 이촌동 · 서울역) · 근처 말(인근 …) · 예약 말 · 군더더기(조사)
  한 조각이라도 어디에도 안 맞으면 그것이 **고유 이름**(`residue`)이다 → 이름이 있는 줄(지금처럼 이름으로 찾는다 — 「토속촌삼계탕」 · 「광장시장 빈대떡」).
★**이름 없는 줄** = 종류 말이나 끼니 말이 있고 + 남는 조각이 없다. 지역만 있는 줄(「경복궁 관람」)은 이름이 있는 줄이다 — 경복궁은 지역 말이기도 하지만 장소 이름이다.
★`[2026-10-04 사용자 지적 「그거 호텔인데 왜 식당이야?」]` **숙소 말 + 끼니 말(「호텔 조식」)은 식사 일정이지만 장소는 숙소다** — 일정 종류는 식사(`dining`)로 두되
  이름표는 「숙소」, 후보 · 자동 추천은 **식당이 아니라 숙소**에서만 고른다(`lodging_meal`). 「식당의 이름이 적혀 있지 않아요 — 후보에서 골라 주세요」로 호텔을 식당에 바꾸자던 것이 틀렸다.
★(앞 판) 끼니 말과 종류 말이 부딪히면(「호텔 조식」: 숙소 + 아침) **끼니 말이 이긴다** — 아침을 먹는 일정이다. 종류 말들이 서로 다른 종류면(식당 + 쇼핑) 읽지 않는다(이름 있는 줄로 둔다).
★알려진 한계: 이름이 통째로 일반 말로만 된 가게(「성수커피」 = 성수 + 커피)는 이름 없는 줄로 읽힌다. 고객은 후보 · 검색에서 그 가게를 직접 고른다(고른 값은 언제나 이긴다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .areas import Area, AreaIndex
from .places import normalize_full
from .terms import Category, Terms, load_terms

#: 관광공사 숙박 분류 번호(`config/place_terms.yaml` 의 「숙소」) — 숙소 말 + 끼니 말을 알아보는 데 쓴다
LODGING_CONTENT_TYPE = "32"
#: 한 글자 조각으로 받는 역할 — 나머지 역할은 두 글자 이상이어야 한다(한 글자가 이름 조각과 우연히 맞는 것을 막는다)
_SHORT_OK = ("near", "filler")


#: 숙소 줄에 붙는 **동작 말** — 이름의 일부가 아니다(`assemble._stays` 가 이 말로 체크인 · 체크아웃 줄을 한 숙소 항목으로 묶는다)
STAY_ACTION = re.compile(r"체크\s*(?:인|아웃)|check[\s-]*(?:in|out)|퇴실", re.IGNORECASE)


def lookup_title(title: str | None) -> str:
    """장소를 **찾을 때** 쓰는 말 — 줄 제목에서 체크인 · 체크아웃 동작 말을 뺀다. `[2026-10-15 숙박예약 점검 LB-01]`

    ☆「더 플라자 호텔 서울 체크인」 전체를 이름으로 찾아 어떤 곳과도 이름이 맞지 않았다(이름 있는 호텔도 `unresolved`), 「호텔 체크인」은 「체크인」이 이름 조각으로 남아
      이름 없는 줄(「호텔」 = 숙소를 고르는 줄)로 못 읽혔다. 제목 자체는 그대로 둔다 — 숙소 항목을 조립할 때 같은 말로 체크인 · 체크아웃 줄을 묶는다.
    동작 말밖에 없는 줄(「체크아웃」)은 찾을 이름이 없으므로 그대로 돌려준다(옛 동작 — 그 줄은 앞 체크인 줄에 합쳐진다)."""
    text = title or ""
    cleaned = " ".join(STAY_ACTION.sub(" ", text).replace("/", " ").split()).strip(" ·-~,")
    return cleaned if len(cleaned) >= 2 else text


@dataclass
class LineParts:
    title: str
    category: Category | None = None
    meal: str | None = None                 # 끼니 이름(아침 · 점심 · 저녁 · 식사)
    area: Area | None = None
    near: bool = False                      # 「인근」 「근처」 같은 말이 있었다
    booked: bool | None = None              # 줄 글자로 적은 예약(「예약한」 · 「미예약」) — 예약 칸(`booked`)과 따로다
    residue: list[str] = field(default_factory=list)   # 어디에도 안 맞은 조각 = 고유 이름
    conflict: bool = False                  # 종류 말들이 서로 다른 종류를 가리킨다
    #: 이 줄이 식사라고 이미 알려져 있다(원문의 끼니 말을 규칙이 떼어 일정 종류 칸을 채웠다) — 끼니 말과 같은 효과
    dining_hint: bool = False

    @property
    def kind(self) -> str | None:
        """줄이 가리키는 일정 종류 — 끼니 말이 이기고, 없으면 종류 말을 따른다. 읽을 것이 없으면 None."""
        if self.meal or self.dining_hint:
            return "dining"
        return self.category.kind if self.category else None

    @property
    def lodging_meal(self) -> bool:
        """숙소 말 + 끼니 말(「호텔 조식」) — 식사 일정이지만 장소는 **숙소 안**이다. 식당 후보를 권하지 않는다."""
        return bool(self.category and self.category.content_type == LODGING_CONTENT_TYPE and (self.meal or self.dining_hint))

    @property
    def content_type(self) -> str | None:
        """같은 분류로 거를 관광공사 번호 — 종류 말의 분류가 줄의 종류와 같을 때만. 숙소 + 끼니(`lodging_meal`)는 일정이 식사여도 **숙박 분류**다(식당 후보를 안 권한다)."""
        if self.lodging_meal:
            return self.category.content_type if self.category else None
        return self.category.content_type if self.category and self.category.kind == self.kind else None

    @property
    def nameless(self) -> bool:
        return bool(self.kind) and not self.residue and not self.conflict and bool(self.category or self.meal or self.dining_hint)

    @property
    def label(self) -> str:
        """화면에 보일 종류 이름 — 「식당」 · 「쇼핑」. 끼니만 있으면 「식당」을 종류 말에서 못 얻으므로 끼니 이름 대신 일반 말."""
        if self.lodging_meal:
            return self.category.label if self.category else "숙소"          # 「숙소」 — 식당이 아니다
        if self.category and self.category.kind == self.kind:
            return self.category.label
        return "식당" if self.kind == "dining" else "장소"

    def public(self) -> dict[str, Any]:
        """응답에 싣는 모양 — 확인 화면이 「성수 지역 식당」 같은 문장을 만드는 재료(지역은 이름 · 중심 · 반경)."""
        return {"label": self.label, "kind": self.kind, "content_type": self.content_type, "meal": self.meal,
                "near": self.near, "area": self.area.as_dict() if self.area else None, "lodging_meal": self.lodging_meal}


def _role(piece: str, terms: Terms, areas: AreaIndex | None) -> str | None:
    if piece in terms.categories:
        return "category"
    if piece in terms.meals:
        return "meal"
    if piece in terms.booking:
        return "booking"
    if piece in terms.proximity:
        return "near"
    if piece in terms.fillers:
        return "filler"
    if areas is not None and piece in areas:
        return "area"
    return None


def _segment(text: str, terms: Terms, areas: AreaIndex | None) -> list[tuple[str, str]] | None:
    """한 조각을 아는 말들로 **빈틈없이** 나눈다(가장 적은 덩어리로). 못 나누면 None. `[(말, 역할)]`."""
    best: list[list[tuple[str, str]] | None] = [None] * (len(text) + 1)
    best[0] = []
    for end in range(1, len(text) + 1):
        for start in range(end):
            before = best[start]
            if before is None:
                continue
            piece = text[start:end]
            role = _role(piece, terms, areas)
            if role is None or (len(piece) < 2 and role not in _SHORT_OK):
                continue
            candidate = before + [(piece, role)]
            if best[end] is None or len(candidate) < len(best[end]):
                best[end] = candidate
    return best[len(text)]


def parse(title: str | None, *, terms: Terms | None = None, areas: AreaIndex | None = None,
          kind_hint: str | None = None) -> LineParts:
    """한 줄의 제목 → 조각. `kind_hint` = 규칙이 이미 정한 일정 종류(끼니 말을 떼어 낸 줄은 `dining`)."""
    terms = terms or load_terms()
    parts = LineParts(title=title or "", dining_hint=kind_hint == "dining")
    for token in (title or "").split():
        pieces = _segment(normalize_full(token), terms, areas)
        if pieces is None or not any(role not in ("filler",) for _, role in pieces):
            parts.residue.append(token)
            continue
        for piece, role in pieces:
            if role == "category":
                category = terms.categories[piece]
                if parts.category is None:
                    parts.category = category
                elif parts.category.kind != category.kind:
                    parts.conflict = True
            elif role == "meal":
                parts.meal = parts.meal or terms.meals[piece]
            elif role == "area":
                parts.area = parts.area or areas.get(piece)          # type: ignore[union-attr]
            elif role == "near":
                parts.near = True
            elif role == "booking":
                value = terms.booking[piece]
                if value is not None:
                    parts.booked = value
    return parts


__all__ = ["LineParts", "STAY_ACTION", "lookup_title", "parse"]
