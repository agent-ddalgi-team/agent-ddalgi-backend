# llm 모드로 백엔드를 켠다. .env는 mock으로 두고, 이 실행에서만 AGENT_MODE를 덮어쓴다.
# 실제 유료 AI 호출이 발생한다. 기본 모델·한도는 .env를 사용하고 명시한 실행 옵션만 덮어쓴다.
# 사용: .\scripts\run_llm.ps1            (기본 DB)
#       .\scripts\run_llm.ps1 -Demo      (시연 데이터 허용)
#       .\scripts\run_llm.ps1 -Demo -ContentReview (기존 전체 한도 안에서 내용 검증도 허용)
#       .\scripts\run_llm.ps1 -Demo -ContentReview -TextProposals (문구 수정안도 허용)
# 실제 자료 시연: -RequestTimeoutSeconds 120 -MaxInputChars 40000 -MaxReviewInputChars 80000 -MaxOutputTokens 24000 (.env 변경 없음)
param([switch]$Demo, [switch]$ContentReview, [switch]$TextProposals,
      [ValidateRange(1, 120)][int]$RequestTimeoutSeconds = 60,
      [ValidateRange(1, 40000)][int]$MaxInputChars = 10000,
      [ValidateRange(1, 120000)][int]$MaxReviewInputChars = 10000,
      [ValidateRange(1, 32000)][int]$MaxOutputTokens = 8000)
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:AGENT_MODE = 'llm'
if ($Demo) { $env:DEMO_MODE = 'true' }
if ($ContentReview) { $env:OPENAI_ENABLE_CONTENT_REVIEW = 'true' }
if ($TextProposals) { $env:OPENAI_ENABLE_TEXT_PROPOSALS = 'true' }
if ($PSBoundParameters.ContainsKey('RequestTimeoutSeconds')) {
    $env:OPENAI_TIMEOUT_SECONDS = [string]$RequestTimeoutSeconds
    $env:OPENAI_TRIAL_TIMEOUT_LIMIT_SECONDS = [string]$RequestTimeoutSeconds
}
if ($PSBoundParameters.ContainsKey('MaxInputChars')) {
    $env:OPENAI_MAX_INPUT_CHARS = [string]$MaxInputChars
    $env:OPENAI_TRIAL_INPUT_CHAR_LIMIT = [string]$MaxInputChars
}
if ($PSBoundParameters.ContainsKey('MaxOutputTokens')) {
    $env:OPENAI_MAX_OUTPUT_TOKENS = [string]$MaxOutputTokens
    $env:OPENAI_TRIAL_OUTPUT_TOKEN_LIMIT = [string]$MaxOutputTokens
}
if ($PSBoundParameters.ContainsKey('MaxReviewInputChars')) {
    $env:OPENAI_REVIEW_MAX_INPUT_CHARS = [string]$MaxReviewInputChars
}
.\.venv\Scripts\python.exe -X utf8 -B -u -c "import logging; logging.basicConfig(level=logging.WARNING); logging.getLogger('app.agent_llm').setLevel(logging.INFO); from app.config import load_settings; from app.agent_bridge import get_bridge; import uvicorn; s=load_settings(); b=get_bridge(s); print('mode='+s.agent_mode+' bridge='+type(b).__name__+' demo='+str(s.demo_mode)+' db='+str(s.db_path), flush=True); uvicorn.run('main:app', host='127.0.0.1', port=8000)"
