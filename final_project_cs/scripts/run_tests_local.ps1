# develop CI 관문(.github/workflows/ci-develop.yml)을 로컬에서 같은 조건으로 돌린다.
#   준비: final_project_cs/.venv (Python 3.12 + requirements.txt), 로컬 PostgreSQL 5433.
#   값은 CI 와 같은 시험용 가짜다. .env 는 읽히더라도 여기 환경 변수가 이긴다.
#
#   사용: .\scripts\run_tests_local.ps1            # 린트 + 시험 + 보안
#         .\scripts\run_tests_local.ps1 -Setup     # 처음 한 번: 마이그레이션 · 프롬프트 등록까지
#         .\scripts\run_tests_local.ps1 tests/unit # pytest 대상만 바꿔서
#   ★위치 인자는 전부 pytest 대상이다 — 막지 않으면 첫 경로가 -Database 로 들어간다(2026-10-01)
[CmdletBinding(PositionalBinding = $false)]
param(
    [switch]$Setup,
    [string]$Database = "acop_ci_local",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Targets
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw ".venv 가 없다 — Python 3.12 로 'python -m venv .venv' 후 '.venv\Scripts\python -m pip install -r requirements.txt ruff==0.16.8'" }

# DB 가 안 떠 있으면 시험이 연결을 기다리며 멈춘다(시간 제한이 없다). 먼저 확인한다.
if (-not (Get-NetTCPConnection -State Listen -LocalPort 5433 -ErrorAction SilentlyContinue)) {
    throw "PostgreSQL(5433)이 안 떠 있다 — wiki/operations/local-setup.md 의 기동 절차를 따른다"
}

$env:TZ = "Asia/Seoul"; $env:PGTZ = "Asia/Seoul"
$env:PYTHONPATH = (Resolve-Path "..\final_project_sample").Path
$env:ACOP_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5433/$Database"
$env:ACOP_LLM_PROVIDER = "mock"
$env:ACOP_OPENAI_API_KEY = "sk-dummy-for-ci"
$env:ACOP_LLM_MODEL = "gpt-4o-mini"
$env:ACOP_EMBEDDING_MODEL = "text-embedding-3-small"
$env:ACOP_TENANT_ID = "demo"
$env:ACOP_SECRET_KEY = "dummy-secret-key-for-ci-0123456789abcdef"
$env:ACOP_COMPOSER_JWT_SECRET = "dummy-composer-jwt-secret-for-ci-0123456789"
$env:ACOP_COMPOSER_ISSUER_SECRET = "dummy-composer-issuer-secret-for-ci-0123456789"

if ($Setup) {
    & $py -m app.infrastructure.db.migrate; if ($LASTEXITCODE) { exit $LASTEXITCODE }
    & $py -m scripts.register_prompts; if ($LASTEXITCODE) { exit $LASTEXITCODE }
}

if ($Targets) {
    & $py -m pytest @Targets -q
    exit $LASTEXITCODE
}

$failed = 0
Push-Location ..; & (Join-Path $root ".venv\Scripts\ruff.exe") check .; $failed += [int]($LASTEXITCODE -ne 0); Pop-Location
& $py -m pytest tests/architecture tests/contract tests/unit -q; $failed += [int]($LASTEXITCODE -ne 0)
& $py -m pytest tests/security -q; $failed += [int]($LASTEXITCODE -ne 0)
if ($failed) { Write-Host "실패한 묶음: $failed" -ForegroundColor Red; exit 1 }
Write-Host "전부 통과" -ForegroundColor Green
