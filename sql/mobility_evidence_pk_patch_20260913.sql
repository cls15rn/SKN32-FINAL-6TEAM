-- mob_verdict_evidence PK 수정 — 2026-09-13
-- 왜: evidence_id 'mob:L3:timetable:1' 은 Case 가 달라도 같은 문자열이다.
--     PK 가 evidence_id 하나면 **두 번째 Case 의 적재가 PK 충돌로 터진다.**
--     evidence_id 는 한 결과(TeamResult) 안에서만 유일하면 된다 — 계약 검증기가 보는 범위가 그것이다.
--     길게 만들면 answer·evidence 예산에 불리하므로 복합키로 간다.
-- 안전: 이 표는 현재 0건이다(모듈 산출은 02번 방이 채운다). 재생성해도 잃을 것이 없다.
-- 되돌리기: 팀 공용 RDB 로 이관할 때 그쪽 PK 정책이 다르면 evidence_id 에 case_id 를 넣는 쪽으로 바꾼다.

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS mob_verdict_evidence;

CREATE TABLE mob_verdict_evidence (
    verdict_id          TEXT    NOT NULL REFERENCES mob_leg_verdict(verdict_id) ON DELETE CASCADE,
    evidence_id         TEXT    NOT NULL,          -- 'mob:{leg_id}:{kind}:{n}' — 결과 안에서만 유일
    source_type         TEXT    NOT NULL CHECK (source_type IN ('db','policy','case_event')),
    source_id           TEXT    NOT NULL,          -- 'tago_subway@2026-09-09' · 'mobility_rules@v0.3'
    claim               TEXT    NOT NULL,
    value_json          TEXT,                      -- ★ mobility_scripts/mobility_checks/contract/mob_evidence_value_v1.schema.json 로 검증 후 적재
    confidence          REAL    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    observed_at         TEXT    NOT NULL,          -- 시간표는 수집일, 규칙은 개정일
    PRIMARY KEY (verdict_id, evidence_id)
);
--  ★ source_type CHECK 3종은 그대로다. 넓히려면 02·04번 방과 합의(◆6).
--  ★ 저장하는 kind 는 timetable·rule·walk·bus·issue·prev_verdict 여섯이다.
--    verdict·run·alt 는 저장하지 않는다 — mob_leg_verdict 행에서 접을 때 생성한다(접기 설계 v1 §15).
CREATE INDEX IF NOT EXISTS ix_evidence_verdict ON mob_verdict_evidence (verdict_id);
