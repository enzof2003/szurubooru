# Local OpenAI-compatible server: Gemma 4 26B A4B (official QAT, UD-Q4_K_XL) + vision projector.
# Reuses the GGUF files Koharu already downloaded (read-only). Listens on 127.0.0.1:8080/v1.
# Start the translate service FIRST so --fit sizes the GPU offload around the detector/OCR VRAM.
# If .env has LOCAL_LLM_API_KEY, the server requires it (set LLM_API_KEY to the same value).
$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$snap  = "$env:LOCALAPPDATA\koharu\store\hugging-face\models\unsloth--gemma-4-26B-A4B-it-qat-GGUF\snapshots\7b92b5b28818151e8669af2e45e88d6086f490dd"
$model = if ($env:GEMMA_GGUF) { $env:GEMMA_GGUF } else { "$snap\gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf" }
$mmproj = if ($env:GEMMA_MMPROJ) { $env:GEMMA_MMPROJ } else { "$snap\mmproj-F16.gguf" }

$extra = @()
$keyLine = Get-Content "$root\.env" -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*LOCAL_LLM_API_KEY\s*=\s*(\S+)' } | Select-Object -First 1
if ($keyLine -and $keyLine -match '=\s*(\S+)') { $extra += @("--api-key", $Matches[1]) }

& "$root\tools\llama.cpp\llama-server.exe" `
    -m $model --mmproj $mmproj `
    --alias gemma-4-26b-a4b-it `
    --host 127.0.0.1 --port 8080 `
    -c 16384 -np 1 `
    -b 2048 -ub 2048 `
    -fa on --fit on --fit-target 2048 `
    --jinja --reasoning-budget 0 `
    @extra @args
