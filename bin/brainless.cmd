@echo off
rem brainless command line, Windows shim. Finds the vault and its Python, then hands
rem over to tools\cli.py, which holds every command (brainless help for the list).
rem Vault resolution: %%BRAINLESS_VAULT%%, else %%USERPROFILE%%\.config\brainless\vault, else %%USERPROFILE%%\brainless.
setlocal
set "VAULT=%BRAINLESS_VAULT%"
if not defined VAULT if exist "%USERPROFILE%\.config\brainless\vault" set /p VAULT=<"%USERPROFILE%\.config\brainless\vault"
if not defined VAULT set "VAULT=%USERPROFILE%\brainless"
if not exist "%VAULT%\tools\cli.py" (
  echo brainless: no vault at %VAULT% ^(set BRAINLESS_VAULT or run install.ps1^) 1>&2
  exit /b 1
)
set "BRAINLESS_VAULT=%VAULT%"
set "PYTHONUTF8=1"
set "PY=%VAULT%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=py"
"%PY%" "%VAULT%\tools\cli.py" %*
exit /b %ERRORLEVEL%
