@echo off
chcp 65001 > nul
cd /d "%~dp0"
setlocal EnableDelayedExpansion

REM ==========================================================
REM  Update check (fast-forward only, never rewrites your work)
REM ==========================================================
if not exist ".git" goto RUN
where git >nul 2>&1
if errorlevel 1 goto RUN

echo [update] checking for updates...
git fetch --quiet origin
if errorlevel 1 (
    echo [update] fetch failed ^(offline?^) - starting with current version
    goto RUN
)

set "LOCAL="
set "REMOTE="
for /f "delims=" %%i in ('git rev-parse HEAD 2^>nul') do set "LOCAL=%%i"
for /f "delims=" %%i in ('git rev-parse @{u} 2^>nul') do set "REMOTE=%%i"

if not defined REMOTE (
    echo [update] no upstream branch - skipped
    goto RUN
)
if "!LOCAL!"=="!REMOTE!" (
    echo [update] already up to date
    goto RUN
)

echo [update] new version found - fast-forwarding...
git merge --ff-only @{u}
if errorlevel 1 (
    echo.
    echo [update] fast-forward FAILED. Starting with the current version.
    echo          Your local edits or diverged commits are blocking it.
    echo          Check with:  git status
    echo.
    pause
) else (
    echo [update] updated:
    git --no-pager log --oneline -1
)

:RUN
rem clear any errorlevel left over from the update check
ver >nul
python chest_auto.py
if errorlevel 1 (
    echo.
    echo ---- error ----
    echo If modules are missing, run: pip install -r requirements.txt
    pause
)
endlocal
