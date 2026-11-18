$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' 'build.py'
if ($LASTEXITCODE -ne 0) { throw 'Building VisionFlow failed' }
