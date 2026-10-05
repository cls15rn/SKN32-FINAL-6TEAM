# -*- coding: utf-8 -*-
"""API 키 파일 — **새지 않는가**와 **템플릿이 선언과 맞는가**.

★두 가지를 본다.

  ① `.env.apikeys` 가 git 에 잡히지 않는다.
     `.gitignore` 의 `*.key` 는 `.key` 로 **끝나는** 이름만, `.env` 는 그 이름
     하나만 매치한다. 둘 다 이 파일을 **안 잡는다** — 명시해야 한다.
     이걸 사람이 기억하게 두면 언젠가 키가 올라간다.

  ② 템플릿에 적힌 이름이 전부 `Settings` 에 **선언돼 있다.**
     ★`Settings` 는 `extra="forbid"` 다. 선언 안 된 이름을 키 파일에 적으면
     `ValidationError` 로 **앱이 기동조차 못 한다**(2026-09-10 실측). 템플릿을
     복사해 쓰는 사람이 그 지뢰를 밟게 두지 않는다.

  그리고 반대 방향 — 선언은 했는데 템플릿에 없는 키가 있으면, 발급받을 것이
  있다는 사실을 아무도 모른다.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest

from app.core.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = REPO_ROOT / ".env.apikeys"
TEMPLATE = REPO_ROOT / ".env.apikeys.example"

#: 템플릿에서 `ACOP_XXX=` 꼴을 뽑는다. 주석(`#`)으로 시작하는 줄은 뺀다.
_ASSIGNMENT = re.compile(r"^(ACOP_[A-Z0-9_]+)=", re.MULTILINE)


def _template_names() -> set[str]:
    return set(_ASSIGNMENT.findall(TEMPLATE.read_text(encoding="utf-8")))


def _declared_names() -> set[str]:
    return {f"ACOP_{name.upper()}" for name in Settings.model_fields}


def test_the_template_is_committed_and_not_empty():
    assert TEMPLATE.exists(), f"{TEMPLATE.name} 이 없다 — 키를 어디에 넣는지 아무도 모른다."
    assert _template_names(), "템플릿에 항목이 하나도 없다 — 아래 검사가 전부 빈 통과가 된다."


def test_the_real_key_file_is_ignored_by_git():
    """★실제 파일이 없어도 **규칙**은 검사한다 — 파일은 사람마다 있고 없고가 갈린다."""
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--no-index", str(KEY_FILE)],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, (
        f".env.apikeys 가 .gitignore 에 안 걸린다. 키가 커밋될 수 있다.\n"
        f"  ★`*.key` 는 `.key` 로 끝나는 이름만, `.env` 는 그 이름 하나만 잡는다.\n"
        f"  stdout={result.stdout!r} stderr={result.stderr!r}")


def test_the_template_itself_is_not_ignored():
    """★템플릿까지 무시되면 형식을 아무도 못 본다."""
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", str(TEMPLATE)],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode != 0, ".env.apikeys.example 이 무시된다 — 커밋돼야 한다."


@pytest.mark.parametrize("name", sorted(_template_names()))
def test_every_template_name_is_declared_in_settings(name: str):
    """★★선언 안 된 이름이 키 파일에 있으면 **앱이 기동조차 못 한다.**"""
    assert name in _declared_names(), (
        f"템플릿의 {name} 이 Settings 에 선언돼 있지 않다.\n"
        f"  ★이 템플릿을 복사해 쓰면 `extra='forbid'` 때문에 ValidationError 로\n"
        f"    앱이 안 뜬다. `app/core/settings.py` 에 필드를 먼저 만든다.")


def test_every_declared_api_key_appears_in_the_template():
    """선언만 하고 템플릿에 안 적으면, 발급받을 게 있다는 걸 아무도 모른다.

    ★`ACOP_OPENAI_API_KEY`·`ACOP_SECRET_KEY` 는 뺀다 — 이 둘은 바깥 데이터
      소스가 아니라 `.env` 쪽 설정이다.
    """
    elsewhere = {"ACOP_OPENAI_API_KEY", "ACOP_SECRET_KEY",
                 "ACOP_COMPOSER_JWT_SECRET", "ACOP_COMPOSER_ISSUER_SECRET"}
    declared = {name for name in _declared_names()
                if _KEY_NAME.search(name) and name not in elsewhere}
    missing = sorted(declared - _template_names())
    assert not missing, (
        f"Settings 에 선언됐는데 템플릿에 없는 키: {missing}\n"
        f"  → `.env.apikeys.example` 에 발급처 링크와 함께 자리를 만든다.")


#: 키 이름 — `_KEY` 로 끝나거나 `_KEY_1`·`_KEY_2` 처럼 번호가 붙는다.
#:  ★2026-09-14 UTIC 키가 둘(등록 IP 마다 하나)이 되며 번호가 붙었다. `_KEY` 로 끝나는
#:    이름만 보면 번호 붙은 키를 「기본값 없는 설정」으로 오판해 붉어진다.
_KEY_NAME = re.compile(r"_KEY(_\d+)?$")


def _is_secret(name: str) -> bool:
    """이름으로 비밀을 가른다.

    ★2026-09-14 — 처음엔 `_KEY` 로 끝나는 이름만 비밀로 봤다. 그런데 **디스코드
      웹훅 URL 은 이름이 `_URL` 이어도 자격 증명**이다 — 그 주소를 가진 사람은 누구나
      우리 채널에 글을 올린다. 그래서 `_WEBHOOK_URL` 도 비밀로 본다. 비밀은 템플릿에
      **비어 있어야** 하고, 비어 있어도 문자열이라 기동은 막히지 않는다.
    ★2026-10-04 — OAuth 형 키 쌍(`_CLIENT_ID` · `_CLIENT_SECRET`)도 자격 증명이다. 네이버 검색 키
      (`ACOP_NAVER_SEARCH_CLIENT_ID/SECRET`)가 `_KEY` 로 끝나지 않아 「기본값 없는 설정」으로 오판됐다.
    """
    return (bool(_KEY_NAME.search(name))
            or name.endswith(("_WEBHOOK_URL", "_SECRET", "_CLIENT_ID")))


def test_no_real_key_value_leaked_into_the_template():
    """★**비밀은** 템플릿에서 비어 있어야 한다. 채운 채 커밋하는 사고를 막는다.

    ★2026-09-10 정정. 처음엔 「값이 채워진 줄이 하나도 없어야 한다」로 썼는데,
      호출 속도 기본값(`ACOP_RATE_*_PER_DAY`)이 들어오자 붉어졌다. 그 값들은
      **비밀이 아니라 설정**이고 기본값이 있어야 쓸모가 있다.
      검사를 「비밀만」으로 좁힌다 — 넓은 검사가 옳은 변경을 막고 있었다.
    """
    secrets = [line for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
               if (matched := _ASSIGNMENT.match(line))
               and _is_secret(matched.group(1))
               and line.split("=", 1)[1].strip()]
    assert not secrets, f"템플릿에 키 값이 채워진 줄이 있다: {secrets}"


def test_settings_that_are_not_secrets_carry_a_default():
    """★반대 방향 — 숫자 설정이 비어 있으면 **앱이 아예 안 뜬다.**

    2026-09-10 실제로 났다. `sync_apikeys` 가 빠진 항목을 `NAME=` 빈 값으로
    덧붙였는데, `ACOP_RATE_..._PER_DAY` 가 빈 문자열이라 int 파싱에 실패해
    `ValidationError` 로 기동이 막혔다. 템플릿이 기본값을 들고 있어야 한다.
    """
    blank = []
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        matched = _ASSIGNMENT.match(line)
        if matched and not _is_secret(matched.group(1)) \
                and not line.split("=", 1)[1].strip():
            blank.append(matched.group(1))
    assert not blank, (
        f"비밀이 아닌데 템플릿 기본값이 없다: {blank} "
        f"— 이 상태로 복사하면 int/float 파싱 실패로 앱이 기동하지 못한다.")
