@echo off
rem The benchmark tool's launcher on Windows: `hb <command> [args]`, the same commands as lib/hb on a Mac.
rem It runs hb.py with its own Python and libraries (listed at the top of hb.py), which uv fetches into
rem the home folder the first time: no administrator rights, no installer windows.
setlocal
rem A shortcut or a background server starts with a short PATH; the installers for uv and for
rem Claude Code's command line both put their programs in %USERPROFILE%\.local\bin.
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
rem Python reads and writes UTF-8 (a pound sign or an accent in Claude's replies), not the Windows code page.
set "PYTHONUTF8=1"
set "UV_PYTHON_PREFERENCE=only-managed"
where uv >nul 2>nul
if errorlevel 1 (
  echo The benchmark tool needs uv, a small program that fetches its Python. Install it in PowerShell with: 1>&2
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" 1>&2
  exit /b 1
)
uv run --quiet --script "%~dp0hb.py" %*
