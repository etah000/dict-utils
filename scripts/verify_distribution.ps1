$ErrorActionPreference = "Stop"

function Invoke-Checked([string] $Label, [scriptblock] $Action) {
    Write-Host "[$Label]"
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Label failed ($LASTEXITCODE)" }
}

$root = Split-Path -Parent $PSScriptRoot
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "python was not found" }
$env:PYTHONPATH = Join-Path $root "src"

foreach ($required in @("bin\ffmpeg.exe", "bin\ffprobe.exe", "LICENSES")) {
    $requiredPath = Join-Path $root $required
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Missing distribution prerequisite: $requiredPath"
    }
}

Invoke-Checked "application self-check" { & $python.Source -m mdict_audio_app.main --self-check --data-dir (Join-Path $env:TEMP "mdict-audio-verify") }
Invoke-Checked "Python Qt and SQLite" { & $python.Source -c "import sqlite3; from PySide6 import QtCore; print(QtCore.__version__, sqlite3.sqlite_version)" }
Invoke-Checked "FFmpeg" { & (Get-Command ffmpeg -ErrorAction Stop).Source -version }
Invoke-Checked "FFprobe" { & (Get-Command ffprobe -ErrorAction Stop).Source -version }
Invoke-Checked "synthetic smoke test" { & $python.Source -c "from pathlib import Path; from mdict_audio_app.services.audio_plan import parse_word_list; assert parse_word_list('alpha`nbeta') == ('alpha', 'beta'); print('smoke test passed')" }
Write-Host "Distribution verification passed"
