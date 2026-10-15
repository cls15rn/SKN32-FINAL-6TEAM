"""사람 검토 도구 공통 틀 — 블라인드 시트 · 검사 후 들이기 · 왕복. 네트워크 · DB 없이(합성 묶음)."""
from __future__ import annotations

import csv
import json
from datetime import date

import pytest

from eval.human_review import core
from eval.human_review.core import Bundle, Field, Item, SheetError

AI_TOKENS = ("AI-SECRET-LABEL-ALPHA", "AI-SECRET-REASON-BETA")


def make_bundle() -> Bundle:
    colors = ("red", "green", "blue")
    items = [
        Item(id=f"r{n}", context={"입력": f"문장 {n}", "후보": f"후보 {n}"}, criteria="무슨 색인가",
             allowed={"pick": colors + (("extra",) if n == 3 else ())},
             ai={"draft": {"pick": "red", "extras": ["green"]}, "other_ai": {"pick": AI_TOKENS[0]}},
             meta={"group": "a" if n < 3 else "b"})
        for n in range(1, 5)
    ]
    items[0].ai["draft"]["reason"] = AI_TOKENS[1]
    return Bundle(
        name="toy", title="장난감 묶음",
        fields=[Field("pick", "정답", "choice"), Field("extras", "함께 맞다(; 로 구분)", "set", ("red", "green", "blue"), required=False)],
        items=items, context_columns=["입력", "후보"],
        subsets={"group_a": lambda: ["r1", "r2"]},
    )


def read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def fill(path, answers: dict[str, tuple[str, str]], note: str = ""):
    rows = read_rows(path)
    for row in rows:
        if row["id"] in answers:
            row["정답"], row["함께 맞다(; 로 구분)"] = answers[row["id"]]
            row["메모"] = note
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def sheet(tmp_path):
    path = tmp_path / "toy.csv"
    core.export_sheet(make_bundle(), path)
    return path


def test_sheet_has_no_ai_labels_and_blank_answer_cells(sheet):
    """★시트에는 입력 · 후보 · 풀이 기준 · 선택지만 있다 — AI 라벨(출처 둘)의 값 · 이유 문자열은 한 글자도 없고 답칸은 비어 있다."""
    text = sheet.read_text(encoding="utf-8-sig")
    assert all(token not in text for token in AI_TOKENS)
    rows = read_rows(sheet)
    assert [r["id"] for r in rows] == ["r1", "r2", "r3", "r4"]
    assert all(r["정답"] == "" and r["함께 맞다(; 로 구분)"] == "" and r["메모"] == "" and r["묶음"] == "toy" for r in rows)
    assert rows[0]["풀이 기준"] == "무슨 색인가" and rows[0]["입력"] == "문장 1"
    assert "red / green / blue" in rows[0]["고를 수 있는 값"] and "extra" in rows[2]["고를 수 있는 값"]    # 행마다 선택지가 다를 수 있다
    assert sheet.read_bytes().startswith(b"\xef\xbb\xbf")                                               # 엑셀용 BOM


def test_export_never_overwrites(sheet):
    with pytest.raises(SheetError, match="덮어쓰지 않는다"):
        core.export_sheet(make_bundle(), sheet)


def test_round_trip_sheet_to_human_label_file(sheet, tmp_path):
    fill(sheet, {"r1": ("green", ""), "r2": ("red", "green; blue;green"), "r3": ("extra", ""), "r4": ("blue", "")}, note="메모 하나")
    out = core.apply_sheet(make_bundle(), sheet, "검토자 홍", tmp_path / "labels", today=date(2026, 10, 16))
    data = core.load_label_file(out)
    assert out.name == "toy_human_검토자홍_2026-10-16.json"
    assert data["reviewer"] == "검토자 홍" and data["reviewed_on"] == "2026-10-16" and data["n"] == 4
    assert data["saw_ai_labels"] is False and data["bundle"] == "toy" and data["subset"] is None
    assert data["labels"]["r1"] == {"pick": "green", "extras": [], "note": "메모 하나"}
    assert data["labels"]["r2"]["extras"] == ["green", "blue"]                       # 중복은 한 번만, 순서 유지
    assert data["labels"]["r3"]["pick"] == "extra"
    with pytest.raises(SheetError, match="이미 있다"):
        core.apply_sheet(make_bundle(), sheet, "검토자 홍", tmp_path / "labels", today=date(2026, 10, 16))     # 덮어쓰지 않는다


@pytest.mark.parametrize("answers, message", [
    ({"r1": ("", "")}, r"「정답」 이\(가\) 비었다"),
    ({"r1": ("purple", "")}, "고를 수 없는 값"),
    ({"r1": ("extra", "")}, "고를 수 있는 값이 아니다|고를 수 없는 값"),          # r1 에서는 extra 가 선택지 밖(r3 만 허용)
    ({"r1": ("red", "purple")}, "고를 수 없는 값"),
])
def test_one_bad_row_writes_nothing(sheet, tmp_path, answers, message):
    full = {"r1": ("red", ""), "r2": ("red", ""), "r3": ("red", ""), "r4": ("red", "")}
    full.update(answers)
    fill(sheet, full)
    out_dir = tmp_path / "labels"
    with pytest.raises(SheetError, match=message):
        core.apply_sheet(make_bundle(), sheet, "누구", out_dir)
    assert not out_dir.exists() or list(out_dir.iterdir()) == []


def test_unknown_duplicate_and_missing_ids_are_errors(sheet, tmp_path):
    fill(sheet, {"r1": ("red", ""), "r2": ("red", ""), "r3": ("red", ""), "r4": ("red", "")})
    rows = read_rows(sheet)
    rows.append({**rows[0], "id": "zzz"})            # 묶음에 없는 id
    rows.append(dict(rows[1]))                        # 중복 id
    rows = [r for r in rows if r["id"] != "r4"]       # 빠진 행
    with sheet.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(SheetError) as err:
        core.apply_sheet(make_bundle(), sheet, "누구", tmp_path / "labels")
    message = str(err.value)
    assert "zzz: 묶음 「toy」 에 없는 id" in message and "r2: id 가 두 번 나온다" in message and "r4: 시트에 행이 없다" in message
    assert not (tmp_path / "labels").exists()


def test_empty_reviewer_and_other_bundle_sheet_rejected(sheet, tmp_path):
    fill(sheet, {f"r{n}": ("red", "") for n in range(1, 5)})
    with pytest.raises(SheetError, match="--reviewer"):
        core.apply_sheet(make_bundle(), sheet, "  ", tmp_path / "labels")
    rows = read_rows(sheet)
    rows[0]["묶음"] = "another"
    with sheet.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(SheetError, match="다른 묶음 「another」"):
        core.apply_sheet(make_bundle(), sheet, "누구", tmp_path / "labels")


def test_subset_sheet_round_trip_and_rows_outside_subset_rejected(tmp_path):
    bundle = make_bundle()
    path = tmp_path / "toy_a.csv"
    assert core.export_sheet(bundle, path, "group_a") == 2
    fill(path, {"r1": ("red", ""), "r2": ("blue", "")})
    out = core.apply_sheet(bundle, path, "누구", tmp_path / "labels", subset="group_a")
    assert core.load_label_file(out)["subset"] == "group_a" and out.name.startswith("toy-group_a_human_")
    full = tmp_path / "toy_full.csv"
    core.export_sheet(bundle, full)
    fill(full, {f"r{n}": ("red", "") for n in range(1, 5)})
    with pytest.raises(SheetError, match="r3: 이 부분집합\\(group_a\\)에 없는 id"):
        core.apply_sheet(bundle, full, "누구", tmp_path / "labels2", subset="group_a")
    with pytest.raises(SheetError, match="부분집합 「nope」"):
        core.export_sheet(bundle, tmp_path / "x.csv", "nope")


def test_text_field_and_normalize_hook(tmp_path):
    bundle = make_bundle()
    bundle.fields.append(Field("why", "이유", "text", required=True))
    bundle.normalize = lambda e: {**e, "extras": [v for v in e["extras"] if v != e["pick"]]}
    path = tmp_path / "t.csv"
    core.export_sheet(bundle, path)
    rows = read_rows(path)
    for r in rows:
        r["정답"], r["함께 맞다(; 로 구분)"], r["이유"] = "red", "red;green", "그냥"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    labels = core.read_sheet(bundle, path)
    assert labels["r1"] == {"pick": "red", "extras": ["green"], "why": "그냥", "note": ""}
    rows[0]["이유"] = ""
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(SheetError, match="「이유」 이\\(가\\) 비었다"):
        core.read_sheet(bundle, path)


def test_saw_ai_labels_is_recorded_not_assumed(sheet, tmp_path):
    fill(sheet, {f"r{n}": ("red", "") for n in range(1, 5)})
    out = core.apply_sheet(make_bundle(), sheet, "누구", tmp_path / "labels", saw_ai_labels=True)
    assert json.loads(out.read_text(encoding="utf-8"))["saw_ai_labels"] is True


def test_compressed_numeric_choices_in_guide(tmp_path):
    nums = tuple(str(n) for n in range(1, 43))
    bundle = Bundle(name="num", title="t", fields=[Field("item", "판정", "choice")], context_columns=["문장"],
                    items=[Item(id="a", context={"문장": "x"}, allowed={"item": (*nums, "없음", "애매")})])
    path = tmp_path / "n.csv"
    core.export_sheet(bundle, path)
    assert read_rows(path)[0]["고를 수 있는 값"] == "판정: 1~42 / 없음 / 애매"
    rows = read_rows(path)
    rows[0]["판정"] = "42"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    assert core.read_sheet(bundle, path)["a"]["item"] == "42"
    rows[0]["판정"] = "43"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(SheetError, match="고를 수 없는 값"):
        core.read_sheet(bundle, path)


def test_sample_ids_is_seeded_ordered_and_capped():
    ids = [f"id{n:03d}" for n in range(100)]
    first = core.sample_ids(ids, 30)
    assert first == core.sample_ids(ids, 30) and len(first) == 30 and first == sorted(first)       # 같은 시드면 같은 표본 · 원래 순서
    assert set(first) <= set(ids) and first != core.sample_ids(ids, 30, seed=1)                    # 시드가 바뀌면 달라진다
    assert core.sample_ids(ids[:5], 30) == ids[:5]                                                  # 모자라면 있는 만큼
