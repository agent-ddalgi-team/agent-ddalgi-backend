# llm 모드로 백엔드를 켠다. .env는 mock으로 두고, 이 실행에서만 AGENT_MODE를 덮어쓴다.
# 실제 유료 AI 호출이 발생한다. 기본 모델·한도는 .env를 사용하고 명시한 실행 옵션만 덮어쓴다.
# 사용: .\scripts\run_llm.ps1            (기본 DB)
#       .\scripts\run_llm.ps1 -Demo      (시연 데이터 허용)
#       .\scripts\run_llm.ps1 -Demo -ContentReview (내용 검증 허용)
#       .\scripts\run_llm.ps1 -Demo -ContentReview -TextProposals (문구 수정안도 허용)
# 기본값: 입력 20만자/검증 40만자/출력 64000토큰/300초, 횟수·누적 비용 차단 없음 (.env 변경 없음)
param([switch]$Demo, [switch]$ContentReview, [switch]$TextProposals,
      [ValidateRange(1, 300)][int]$RequestTimeoutSeconds = 300,
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
.\.venv\Scripts\python.exe -X utf8 -B -u -c "import logging; logging.basicConfig(level=logging.WARNING); logging.getLogger('app.agent_llm').setLevel(logging.INFO); from app.config import load_settings; from app.agent_bridge import get_bridge; import uvicorn; s=load_settings(); b=get_bridge(s); print('mode='+s.agent_mode+' bridge='+type(b).__name__+' demo='+str(s.demo_mode)+' usage='+type(b.request_json.ledger).__name__+' input='+str(b.max_input_chars)+' review='+str(b.max_review_input_chars)+' output='+str(b.request_json.options.max_output_tokens)+' timeout='+str(b.request_json.options.timeout_seconds)+' retries='+str(b.request_json.options.max_retries), flush=True); uvicorn.run('main:app', host='127.0.0.1', port=8000)"
