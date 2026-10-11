# Dev launcher for Windows. Uses the repo's .venv (see docs/BUILD_ON_WINDOWS.md).
#   .\run_windows.ps1                run the app on the real display/GPU
#   .\run_windows.ps1 test [...]     run the pytest suite offscreen (extra args go to pytest)
#   .\run_windows.ps1 test-gl [...]  run the tests that need a real OpenGL context
# Native Qt ("windows" platform) gives a desktop WGL context, which the
# GLSL 1.20 shaders need, so unlike run_linux.sh nothing is overridden
# for the app or the GL tests.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$mode = if ($args.Count -gt 0) { [string]$args[0] } else { "" }
$rest = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }

if ($mode -eq "test") {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $py -m pytest @rest
} elseif ($mode -eq "test-gl") {
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    & $py -m pytest --gl @rest
} else {
    & $py main_window.py @args
}
exit $LASTEXITCODE
