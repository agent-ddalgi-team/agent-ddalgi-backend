# llm 모드로 백엔드를 켠다. .env는 mock으로 두고, 이 실행에서만 AGENT_MODE를 덮어쓴다.
# 실제 유료 AI 호출이 발생한다. 기본 모델·한도는 .env를 사용하고 명시한 실행 옵션만 덮어쓴다.
# 사용: .\scripts\run_llm.ps1            (기본 DB)
#       .\scripts\run_llm.ps1 -Demo      (시연 데이터 허용)
#       .\scripts\run_llm.ps1 -Demo -ContentReview (내용 검증 허용)
#       .\scripts\run_llm.ps1 -Demo -ContentReview -TextProposals (문구 수정안도 허용)
# runtime 기본값: 입력 20만자/검증 40만자/출력 64000토큰/180초 (.env 변경 없음)
# trial/interactive는 해당 모드 범위의 옵션과 재시도 0을 명시한다. -CheckOnly는 API/서버 없이 설정만 확인한다.
param([switch]$Demo, [switch]$ContentReview, [switch]$TextProposals, [switch]$CheckOnly,
      [ValidateRange(1, 300)][int]$RequestTimeoutSeconds = 180,
      [ValidateRange(1, 200000)][int]$MaxInputChars = 200000,
      [ValidateRange(1, 400000)][int]$MaxReviewInputChars = 400000,
      [ValidateRange(1, 64000)][int]$MaxOutputTokens = 64000,
      [ValidateRange(0, 2)][int]$MaxRetries = 2)
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:AGENT_MODE = 'llm'
if ($Demo) { $env:DEMO_MODE = 'true' }
if ($ContentReview) { $env:OPENAI_ENABLE_CONTENT_REVIEW = 'true' }
if ($TextProposals) { $env:OPENAI_ENABLE_TEXT_PROPOSALS = 'true' }
$env:OPENAI_TIMEOUT_SECONDS = [string]$RequestTimeoutSeconds
$env:OPENAI_MAX_INPUT_CHARS = [string]$MaxInputChars
$env:OPENAI_MAX_OUTPUT_TOKENS = [string]$MaxOutputTokens
$env:OPENAI_REVIEW_MAX_INPUT_CHARS = [string]$MaxReviewInputChars
$env:OPENAI_MAX_RETRIES = [string]$MaxRetries
# trial/interactive는 요청 옵션과 별도의 가드를 함께 검사한다.
# runtime에서는 이 값들을 사용하지 않으며 예산/실행 모드를 바꾸지 않는다.
$env:OPENAI_TRIAL_TIMEOUT_LIMIT_SECONDS = [string]$RequestTimeoutSeconds
$env:OPENAI_TRIAL_INPUT_CHAR_LIMIT = [string]$MaxInputChars
$env:OPENAI_TRIAL_OUTPUT_TOKEN_LIMIT = [string]$MaxOutputTokens
$startup = @'
import json, logging, os, sys
logging.basicConfig(level=logging.WARNING)
logging.getLogger('app.agent_llm').setLevel(logging.INFO)
from app.config import load_settings
from app.agent_bridge import get_bridge
s = load_settings()
b = get_bridge(s)  # 설정 불일치는 포트를 열거나 API를 호출하기 전에 중단한다.
report = b.request_json.ledger.snapshot()
print(json.dumps({'mode': s.agent_mode, 'execution_mode': os.environ.get('OPENAI_EXECUTION_MODE', 'runtime'),
    'bridge': type(b).__name__, 'demo': s.demo_mode, 'usage': type(b.request_json.ledger).__name__,
    'input': b.max_input_chars, 'review': b.max_review_input_chars,
    'output': b.request_json.options.max_output_tokens, 'timeout': b.request_json.options.timeout_seconds,
    'retries': b.request_json.options.max_retries,
    'limits': {k: report.get(k) for k in ('timeout_limit_seconds', 'input_char_limit', 'output_token_limit', 'budget_usd')},
    'check_only': sys.argv[1] == '--check-only'}, ensure_ascii=False), flush=True)
if sys.argv[1] != '--check-only':
    import uvicorn
    uvicorn.run('main:app', host='127.0.0.1', port=8000)
'@
$startupMode = if ($CheckOnly) { '--check-only' } else { '--serve' }
.\.venv\Scripts\python.exe -X utf8 -B -u -c $startup $startupMode
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
