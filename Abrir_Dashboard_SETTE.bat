@echo off
setlocal

set "PROJECT_DIR=C:\\Users\\DELTA06\\Documents\\api_sette"
set "APP=%PROJECT_DIR%\\principal.py"

if not exist "%APP%" (
    echo ERRO: Projeto nao encontrado em:
    echo %PROJECT_DIR%
    echo.
    pause
    exit /b 1
)

cd /d "%PROJECT_DIR%"

echo Reiniciando o integrador SETTE...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process ^| Where-Object { $_.CommandLine -like '*api_sette*' -and $_.CommandLine -like '*principal.py*' } ^| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

timeout /t 1 /nobreak >nul
del /q "%TEMP%\\sette_integrador_instancia.lock" >nul 2>&1

where pyw.exe >nul 2>&1
if %errorlevel% equ 0 (
    start "SETTE Dashboard" pyw.exe -3 "%APP%" --dashboard
    exit /b 0
)

where pythonw.exe >nul 2>&1
if %errorlevel% equ 0 (
    start "SETTE Dashboard" pythonw.exe "%APP%" --dashboard
    exit /b 0
)

echo ERRO: Python nao encontrado no PATH do Windows.
echo Instale o Python marcando a opcao "Add Python to PATH".
echo Depois instale as dependencias com:
echo py -m pip install -r "%PROJECT_DIR%\\requisitos.txt"
echo.
pause
exit /b 1
