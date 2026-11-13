@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 雀魂AI助手：產生主程式

rem 從 GitHub 下載這個專案後，只要雙擊這個檔案：
rem   1. 找一個 Python 來打包；新電腦什麼都沒有的話，自動下載 uv 和獨立的 Python 3.10
rem      （放在 runtime\，跟主程式之後用的是同一份，不用另外裝 Anaconda）
rem   2. 用 PyInstaller 把 launcher\launcher.py 打包成「雀魂AI助手.exe」
rem   3. 自動開啟「雀魂AI助手.exe」，接著由它安裝辨識套件、下載權重、建立 Mortal 映像
rem 之後每次使用直接雙擊「雀魂AI助手.exe」就好。

set "RT=%~dp0runtime"
set "UV=%RT%\uv\uv.exe"
set "UV_PYTHON_INSTALL_DIR=%RT%\python"
set "UV_CACHE_DIR=%RT%\uv-cache"
set "UV_LINK_MODE=copy"

rem ---- 1. 找 Python -------------------------------------------------------
call :find_py
if not defined PY (
    echo 這台電腦還沒有可用的 Python，自動下載（約 30 MB，只需要一次）…
    if not exist "%UV%" (
        if not exist "%RT%\uv" mkdir "%RT%\uv"
        echo   下載安裝工具 uv…
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; $z=Join-Path $env:RT 'uv\uv.zip'; Invoke-WebRequest -UseBasicParsing 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $z; $d=Join-Path $env:RT 'uv'; Expand-Archive -Force $z $d; Get-ChildItem $d -Recurse -Include uv.exe,uvx.exe | Where-Object { $_.DirectoryName -ne $d } | Copy-Item -Destination $d -Force; Remove-Item $z"
        if not exist "%UV%" (
            echo [錯誤] uv 下載失敗，請確認網路連線後再執行一次。
            goto fail
        )
    )
    echo   下載 Python 3.10…
    "%UV%" python install 3.10
    if errorlevel 1 goto fail
    call :find_py
)
if not defined PY (
    echo [錯誤] 找不到可以用來打包的 Python。
    goto fail
)
for %%F in ("%PY%") do set "PYDIR=%%~dpF"
echo 使用 Python：%PY%

rem ★ conda 環境：_ctypes / _tkinter 需要的 DLL（ffi、tcl、tk…）在 Library\bin。
rem   沒有 activate 就打包的話 PyInstaller 找不到它們，exe 一開就出現
rem   「DLL load failed while importing _ctypes」（2026-11-12 實測）。
rem   所以照 conda activate 的做法把這幾個資料夾加進 PATH，再明確指定 DLL。
rem   （uv 下載的獨立 Python 沒有 Library\bin，不受影響）
set "EXTRA="
if exist "%PYDIR%Library\bin" (
    set "PATH=%PYDIR%;%PYDIR%Library\mingw-w64\bin;%PYDIR%Library\usr\bin;%PYDIR%Library\bin;%PYDIR%Scripts;%PATH%"
    for %%D in ("%PYDIR%Library\bin\ffi*.dll" "%PYDIR%Library\bin\libffi*.dll" "%PYDIR%Library\bin\tcl8*.dll" "%PYDIR%Library\bin\tk8*.dll" "%PYDIR%Library\bin\zlib*.dll" "%PYDIR%Library\bin\liblzma*.dll" "%PYDIR%Library\bin\libbz2*.dll" "%PYDIR%Library\bin\libexpat*.dll" "%PYDIR%Library\bin\libcrypto*.dll" "%PYDIR%Library\bin\libssl*.dll") do call :addbin "%%~fD"
)

rem ---- 2. 打包 --------------------------------------------------------------
echo 安裝打包工具 PyInstaller…
if exist "%UV%" (
    "%UV%" pip install --python "%PY%" --break-system-packages pyinstaller
) else (
    "%PY%" -m pip install --disable-pip-version-check --break-system-packages pyinstaller
    if errorlevel 1 "%PY%" -m pip install --disable-pip-version-check pyinstaller
)
if errorlevel 1 goto fail

"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name "雀魂AI助手" ^
    --distpath "%~dp0." --workpath "%~dp0build\pyinstaller" --specpath "%~dp0build" ^
    %EXTRA% "%~dp0launcher\launcher.py"
if errorlevel 1 goto fail

rem ---- 3. 開啟主程式 --------------------------------------------------------
echo.
echo [完成] 已產生「雀魂AI助手.exe」，現在幫你開啟。
echo        第一次會自動安裝辨識套件（約 3 GB）並建立 Mortal 映像，之後直接雙擊它就好。
start "" "%~dp0雀魂AI助手.exe"
timeout /t 5 >nul
exit /b 0

:find_py
set "PY="
for /d %%D in ("%RT%\python\cpython-3.10*") do if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
if defined PY exit /b 0
for %%P in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "C:\anaconda3" "C:\miniconda3") do (
    if not defined PY if exist "%%~P\envs\mahjongsoul_agent\python.exe" set "PY=%%~P\envs\mahjongsoul_agent\python.exe"
    if not defined PY if exist "%%~P\envs\mjai\python.exe" set "PY=%%~P\envs\mjai\python.exe"
)
exit /b 0

:addbin
if exist "%~1" (
    echo   一起打包：%~nx1
    set EXTRA=%EXTRA% --add-binary "%~1;."
)
exit /b 0

:fail
echo.
echo [錯誤] 沒有完成，請把上面的錯誤訊息截圖保存。
pause
exit /b 1
