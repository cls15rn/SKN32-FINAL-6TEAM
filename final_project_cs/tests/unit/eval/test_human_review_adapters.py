"""사람 검토 어댑터 — 묶음마다 시트가 정답을 가리는지 · AI 라벨이 선택지 안에 드는지 · 왕복이 되는지. 네트워크 · DB 없이."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from eval.human_review import agreement, core
from eval.human_review.adapters import REGISTRY, build
from eval.human_review.adapters import e2 as e2_adapter
from eval.human_review.adapters.paths import Sources


def sheet_text(bundle, tmp_path, subset=None):
    path = tmp_path / f"{bundle.name}.csv"
    core.export_sheet(bundle, path, subset)
    return path, path.read_text(encoding="utf-8-sig")


def rows_of(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def answer_with_ai(bundle, path, source):
    """시험 전용: AI 라벨을 그대로 시트에 적어 넣은 가짜 검토자(tmp 에서만). 왕복이 되는지 본다."""
    rows = rows_of(path)
    for row in rows:
        ai = bundle.by_id()[row["id"]].ai[source]
        for f in bundle.fields:
            value = ai.get(f.key)
            row[f.column] = core.SEP.join(value) if isinstance(value, list) else (value or "")
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def assert_ai_labels_are_valid_choices(bundle):
    for it in bundle.items:
        for source, label in it.ai.items():
            for f in bundle.fields:
                if f.key not in label:
                    continue
                allowed = bundle.allowed_for(it, f)
                values = label[f.key] if isinstance(label[f.key], list) else [label[f.key]]
                assert all(v in allowed for v in values), (bundle.name, it.id, source, f.key, label[f.key])


def test_registry_names_match_expected_bundles():
    assert set(REGISTRY) == {"activity", "g96", "rag", "e2_v2", "pointing_h", "pointing_mx_dispute", "pointing_mx_check"}


# ───────── G96 ─────────

def test_g96_vocabulary_matches_the_app_and_all_ai_labels_fit():
    from app.domains.travel_ops.components.core_hooks import feedback
    from eval.human_review.adapters import classification as c
    assert set(c.INTENTS) == set(feedback.INTENTS) and set(c.SENTIMENTS) == set(feedback.SENTIMENTS)
    assert set(c.SEVERITIES) == set(feedback.SEVERITIES) and set(c.ISSUE_CODES) == set(feedback.ISSUE_CODES)
    bundle = build("g96")
    assert len(bundle.items) == 96 and sum(1 for it in bundle.items if it.meta["split"] == "holdout") == 24
    assert {it.meta["label_by"] for it in bundle.items} == {"claude-draft"}          # 이 96개의 라벨은 전부 AI 초안이다
    assert_ai_labels_are_valid_choices(bundle)


def test_g96_sheet_is_blind_and_round_trips(tmp_path):
    bundle = build("g96")
    path, text = sheet_text(bundle, tmp_path)
    assert "claude" not in text.lower() and "label_by" not in text and "초안" not in text
    rows = rows_of(path)
    assert len(rows) == 96 and all(r["의도"] == r["대상"] == r["감정"] == r["긴급도"] == "" for r in rows)
    answer_with_ai(bundle, path, "claude_draft")                                     # 가짜 검토자가 AI 와 똑같이 답하면 일치도 1.0
    labels_path = core.apply_sheet(bundle, path, "시험용 가짜 검토자", tmp_path / "labels")
    result = agreement.compare(bundle, core.load_label_file(labels_path)["labels"], bundle.ai_labels("claude_draft"), "가짜", "AI")
    assert all(e["n"] == 96 and e["exact_match"] == 1.0 for e in result["fields"].values())


def test_second_rater_subsets_are_seeded_30_row_samples():
    for name in ("g96", "pointing_h"):
        bundle = build(name)
        sample = [it.id for it in bundle.select("second30")]
        assert len(sample) == 30 and set(sample) <= {it.id for it in bundle.items}
        assert sample == [it.id for it in build(name).select("second30")]                         # 다시 만들어도 같다


# ───────── 활동 판정 ─────────

def test_activity_sheet_hides_first_pass_labels_and_reasons(tmp_path):
    from eval.activity_judge import review
    bundle = build("activity")
    assert len(bundle.items) == 52 and {"closure", "operating_hours", "weather_sensitive", "disaster_effect"} == {it.context["종류"] for it in bundle.items}
    path, text = sheet_text(bundle, tmp_path)
    first = json.loads(review.FIRST_PASS.read_text(encoding="utf-8"))["labels"]
    reasons = [v["reason"] for v in first.values() if v.get("reason")]
    assert reasons and all(reason not in text for reason in reasons)                    # 1차 근거 문장이 시트에 없다
    assert "1차" not in text and "작성자" not in text and "확인 필요" not in text
    rows = rows_of(path)
    assert all(r["정답"] == "" and r["풀이 기준"] for r in rows)
    assert_ai_labels_are_valid_choices(bundle)


def test_activity_round_trip_and_reference_human_comparison(tmp_path):
    bundle = build("activity")
    path, _ = sheet_text(bundle, tmp_path)
    answer_with_ai(bundle, path, "claude_first_pass")
    out = core.apply_sheet(bundle, path, "시험용 가짜 검토자", tmp_path / "labels")
    labels = core.load_label_file(out)["labels"]
    reference = bundle.reference_humans()[0]
    assert reference["saw_ai_labels"] is True and len(reference["labels"]) == 52      # 기존 사람 라벨은 AI 1차를 보고 단 것이다
    vs_ref = agreement.compare(bundle, labels, reference["labels"], "가짜", "기존 사람")
    assert vs_ref["fields"]["expected"]["n"] == 52
    assert vs_ref["fields"]["expected"]["exact_match"] >= 0.9                           # 기존 사람이 거의 그대로 확정했다는 기록과 맞는지
    vs_ai = agreement.compare(bundle, labels, bundle.ai_labels("claude_first_pass"), "가짜", "AI")
    assert vs_ai["fields"]["expected"]["exact_match"] == 1.0 and vs_ai["fields"]["acceptable"]["exact_match"] == 1.0


# ───────── RAG 35행 ─────────

def test_rag_sheet_hides_claude_opinion_and_machine_verdict_sentences(tmp_path):
    bundle = build("rag")
    assert len(bundle.items) == 35
    path, text = sheet_text(bundle, tmp_path)
    opinion = json.loads((Sources().human_eval / "claude_opinion.json").read_text(encoding="utf-8"))
    why = [v["why"] for k, v in opinion.items() if k != "_설명"]
    assert len(why) == 35 and all(w not in text for w in why)
    for phrase in ("Claude", "예상", "위 규정이 없어요", "위 규정이 들어 있어요", "단위가 바뀐", "같아요.", "다르거나 빠졌어요"):
        assert phrase not in text, phrase
    assert "[숫자 목록]" in text and "[이 질문에 맞는 규정]" in text
    assert_ai_labels_are_valid_choices(bundle)


def test_rag_reference_human_and_weighted_kappa_excludes_unknown():
    bundle = build("rag")
    reference = bundle.reference_humans()[0]
    assert len(reference["labels"]) == 35 and reference["saw_ai_labels"] is True
    result = agreement.compare(bundle, reference["labels"], bundle.ai_labels("claude_opinion"), "사람 A", "Claude 의견")
    verdict = result["fields"]["verdict"]
    assert verdict["n"] == 34 and verdict["excluded"] == 1                              # 모름 1행은 뺀다(첫 결과 보고서: 31/34 일치)
    assert verdict["exact_match"] == pytest.approx(31 / 34, abs=1e-3)
    assert verdict["weighted_kappa"] is not None


# ───────── 일정 짚기(H 이견 64) — 저장소 안 자료 ─────────

def test_pointing_h_has_64_disputes_with_schedule_and_numeric_choices(tmp_path):
    bundle = build("pointing_h")
    assert len(bundle.items) == 64
    assert_ai_labels_are_valid_choices(bundle)
    path, text = sheet_text(bundle, tmp_path)
    rows = rows_of(path)
    assert all(r["일정 표"].startswith("오늘은 ") and r["판정(번호/없음/애매)"] == "" for r in rows)
    assert "sol_author" not in text and "luna_label" not in text
    one = bundle.items[0]
    allowed = bundle.allowed_for(one, bundle.fields[0])
    n_lines = len(re.findall(r"^\d+\. ", one.context["일정 표"], re.M))
    assert n_lines >= 40 and allowed == (*[str(i) for i in range(1, n_lines + 1)], "없음", "애매")


# ───────── 일정 짚기(MX) · E2 — 작은 가짜 자료(git 밖 원본 모양) ─────────

def head(n: int) -> str:
    lines = "\n".join(f"{i}. 1일차 3/15(월) {9 + i}:00 [activity] 활동 {i} @ 장소 {i}" for i in range(1, n + 1))
    return f"오늘은 2026-11-10 이다. 고객의 여행 일정(번호. 일차 날짜 시각 [종류] 제목 @ 장소):\n{lines}\n\n"


@pytest.fixture
def mx_root(tmp_path):
    root = tmp_path / "datasets" / "travel" / "mx_testset"
    root.mkdir(parents=True)
    tail = "\n\n이 질문이 가리키는 일정 항목 번호 하나를 item 에 넣어라."
    all_rows = [{"i": 1, "itin": 3, "prompt": head(5) + "고객 질문: 문장" + tail}, {"i": 2, "itin": 7, "prompt": head(6) + "고객 질문: 다른 문장" + tail}]
    (root / "mx_all.json").write_text(json.dumps(all_rows, ensure_ascii=False), encoding="utf-8")
    disputes = [{"k": 3, "cat": "MX4", "q": "비빔밥 색깔 참 곱네요", "gen_item": None, "luna_item": 2, "src": "code"},
                {"k": 3, "cat": "MX1", "q": "잡담 3번 일정 몇 시야", "gen_item": 3, "luna_item": 4, "src": "code"},
                {"k": 7, "cat": "MX2", "q": "섞인 문장", "req": "요청만", "gen_item": 6, "luna_blend": None, "luna_req": 6, "src": "llm"}]
    (root / "mx_disputes.json").write_text(json.dumps(disputes, ensure_ascii=False), encoding="utf-8")
    with (root / "mx_human_check_sample.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "유형", "생성 방식", "고객 입력(혼합)", "요청만(대조)", "일정 번호(일정 파일)", "기계 정답(번호/없음)", "사람 판정(번호/없음/애매)", "메모"])
        writer.writerow(["11", "MX1", "code", "잡담 요청", "요청", "3", "4", "", ""])
        writer.writerow(["12", "MX3", "llm", "일정 밖 질문", "일정 밖 질문", "7", "없음", "", ""])
    return Sources(repo_root=Path("unused"), datasets_root=tmp_path / "datasets")


def test_mx_dispute_sheet_shows_schedule_but_not_model_answers(mx_root, tmp_path):
    bundle = build("pointing_mx_dispute", mx_root)
    assert [it.id for it in bundle.items] == ["MXD-000", "MXD-001", "MXD-002"] and bundle.ai_sources() == ["sol_author", "luna_label"]
    assert bundle.items[0].ai == {"sol_author": {"item": "없음"}, "luna_label": {"item": "2"}}                  # null 은 「없음」 으로 옮긴다
    path, text = sheet_text(bundle, tmp_path)
    assert "활동 5 @ 장소 5" in text and "활동 6 @ 장소 6" in text and "sol_author" not in text
    rows = rows_of(path)
    assert rows[0]["고를 수 있는 값"] == "판정(번호/없음/애매): 1~5 / 없음 / 애매" and rows[2]["고를 수 있는 값"] == "판정(번호/없음/애매): 1~6 / 없음 / 애매"
    rows[0]["판정(번호/없음/애매)"], rows[1]["판정(번호/없음/애매)"], rows[2]["판정(번호/없음/애매)"] = "없음", "3", "6"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    labels = core.read_sheet(bundle, path)
    assert labels["MXD-001"]["item"] == "3"
    rows[2]["판정(번호/없음/애매)"] = "7"                                                      # 6항목 일정에 7번은 없다
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(core.SheetError, match="고를 수 없는 값"):
        core.read_sheet(bundle, path)


def test_mx_dispute_priority_subset_keeps_all_non_mx4_and_samples_mx4(tmp_path):
    from eval.human_review.adapters import pointing
    root = tmp_path / "datasets" / "travel" / "mx_testset"
    root.mkdir(parents=True)
    (root / "mx_all.json").write_text(json.dumps([{"i": 1, "itin": 0, "prompt": head(3) + "고객 질문: x\n\n끝"}]), encoding="utf-8")
    disputes = [{"k": 0, "cat": "MX4" if n < 100 else "MX1", "q": f"문장 {n}", "gen_item": None, "luna_item": 1, "src": "code"} for n in range(130)]
    (root / "mx_disputes.json").write_text(json.dumps(disputes), encoding="utf-8")
    bundle = pointing.build_mx_dispute(Sources(repo_root=Path("unused"), datasets_root=tmp_path / "datasets"))
    first = bundle.select("first200")
    assert len(first) == pointing.MX4_SAMPLE + 30 and sum(1 for it in first if it.meta["cat"] == "MX1") == 30
    assert [it.id for it in first] == [it.id for it in bundle.select("first200")]         # 시드 고정 — 다시 뽑아도 같다
    second = [it.id for it in bundle.select("second30")]
    assert len(second) == 30 and set(second) <= {it.id for it in first}                    # 두 번째 평가자 표본은 우선 시트 안에서 뽑는다


def test_mx_check_sheet_drops_the_machine_answer_column(mx_root, tmp_path):
    bundle = build("pointing_mx_check", mx_root)
    path, text = sheet_text(bundle, tmp_path)
    assert "기계 정답" not in text and "사람 판정" not in text and len(rows_of(path)) == 2
    assert bundle.items[0].ai == {"sol_luna_agreed": {"item": "4"}} and bundle.items[1].ai["sol_luna_agreed"] == {"item": "없음"}


def test_e2_plan_class_mapping_covers_every_reply_shape():
    ex = {"action": "update", "target_ids": ["i3"], "values": {}, "mode": "execute"}
    assert e2_adapter.plan_class({"requests": [ex], "reply_kind": "answered"}) == "바로 실행"
    assert e2_adapter.plan_class({"requests": [{"action": "read", "mode": "answer"}, ex], "reply_kind": "answered"}) == "바로 실행"
    assert e2_adapter.plan_class({"requests": [{"action": "delete", "mode": "conditional", "target_ids": ["i2"]}], "reply_kind": "answered"}) == "조건부 실행"
    assert e2_adapter.plan_class({"requests": [{"action": "update", "mode": "clarify"}], "reply_kind": "clarify"}) == "되묻기"
    assert e2_adapter.plan_class({"requests": [{"action": "update", "mode": "blocked"}], "reply_kind": "blocked"}) == "막힘"
    assert e2_adapter.plan_class({"requests": [{"action": "read", "mode": "answer"}], "reply_kind": "answered"}) == "조회·답변"
    assert e2_adapter.plan_class({"requests": [], "reply_kind": "answered"}) == "할 일 없음"
    assert e2_adapter.plan_targets({"requests": [ex, {"action": "read", "target_ids": ["i9"], "mode": "answer"}]}) == ["i3"]


def test_e2_sheet_never_contains_the_gold_plan(tmp_path):
    root = tmp_path / "datasets" / "travel" / "e2_itinerary_crud_20261012_v2" / "processed"
    root.mkdir(parents=True)
    items = {"i1": {"id": "i1", "title": "호텔", "place": "호텔", "kind": "lodging", "date": "2026-10-18", "start": "09:00", "end": "09:50", "locked": False, "reservation": False},
             "i2": {"id": "i2", "title": "시장", "place": "광장시장", "kind": "dining", "date": "2026-10-18", "start": "12:30", "end": "14:00", "locked": True, "reservation": False}}
    base = {"received_at": "2026-10-18T11:07:00+09:00", "timezone": "Asia/Seoul", "actor_id": "a", "tenant_id": "t", "itinerary_version": 7, "items": items,
            "history": [], "ui_selected_day": "2026-10-18", "ui_selected_item": "i2", "last_verified_target": None,
            "capabilities": {"profile": "p", "itinerary_crud": True, "external_booking": False, "payment": False}, "evidence": [],
            "permissions": {"read_own": True, "write_own": True, "read_other": False, "write_other": False, "book": False, "pay": False}}
    inputs = [{"case_id": f"E2-x-{s}", "family_id": "x", "split": "development", "slice": "s", "style": s, "provenance": {"kind": "synthetic"},
               "input": {**base, "message": "점심을 13시로"}} for s in ("original", "polite")]
    gold = {"requests": [{"action": "update", "target_ids": ["i2"], "values": {"start": "GOLD-ONLY-TOKEN"}, "mode": "execute"}],
            "final_items": items, "writes": [], "reply_kind": "answered", "obligations": ["GOLD-ONLY-OBLIGATION"], "forbidden_calls": [], "evaluation_status": "x"}
    (root / "inputs.jsonl").write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in inputs), encoding="utf-8")
    (root / "gold.jsonl").write_text("\n".join(json.dumps({"case_id": i["case_id"], "family_id": "x", "gold": gold}) for i in inputs), encoding="utf-8")
    bundle = build("e2_v2", Sources(repo_root=Path("unused"), datasets_root=tmp_path / "datasets"))
    assert [it.id for it in bundle.select("families")] == ["E2-x-original"] and [it.id for it in bundle.select("second30")] == ["E2-x-original"]
    path, text = sheet_text(bundle, tmp_path, "families")
    for secret in ("GOLD-ONLY-TOKEN", "GOLD-ONLY-OBLIGATION", "final_items", "reply_kind", "requests", "obligations", "forbidden_calls"):
        assert secret not in text
    assert "i2 | 10/18(일) 12:30-14:00 | 시장 @ 광장시장 | dining [잠김]" in text and "권한: 본인 일정 읽기 O" in text
    ai = bundle.items[0].ai[e2_adapter.AI_SOURCE]
    assert ai == {"plan_class": "바로 실행", "targets": ["i2"]}
    rows = rows_of(path)
    rows[0]["처리 갈래"], rows[0]["바꾸거나 지울 항목 번호(; 로 구분)"] = "바로 실행", "i2"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    labels = core.read_sheet(bundle, path, "families")
    assert agreement.compare(bundle, labels, bundle.ai_labels(e2_adapter.AI_SOURCE), "사람", "AI")["fields"]["plan_class"]["exact_match"] == 1.0
