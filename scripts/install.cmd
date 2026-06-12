@echo off
REM ============================================================================
REM QIQI-Claw Installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the PowerShell installer for users running CMD.
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM   set QIQICLAW_INSTALL_SOURCE=gitee && install.cmd
REM
REM Or if you're already in PowerShell, use the direct command instead:
REM   iex (irm https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.ps1)
REM   $env:QIQICLAW_INSTALL_SOURCE="gitee"; iex (irm https://gitee.com/szd20020329/qiqiclaw/raw/main/scripts/install.ps1)
REM ============================================================================

set "INSTALL_SOURCE=%QIQICLAW_INSTALL_SOURCE%"
if "%INSTALL_SOURCE%"=="" set "INSTALL_SOURCE=%HERMES_INSTALL_SOURCE%"
if "%INSTALL_SOURCE%"=="" set "INSTALL_SOURCE=github"

set "INSTALL_PS1_URL=https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.ps1"
if /I "%INSTALL_SOURCE%"=="gitee" set "INSTALL_PS1_URL=https://gitee.com/szd20020329/qiqiclaw/raw/main/scripts/install.ps1"
if /I "%INSTALL_SOURCE%"=="cn" set "INSTALL_SOURCE=gitee"
if /I "%INSTALL_SOURCE%"=="china" set "INSTALL_SOURCE=gitee"
if /I "%INSTALL_SOURCE%"=="domestic" set "INSTALL_SOURCE=gitee"
if /I "%INSTALL_SOURCE%"=="gitee" set "INSTALL_PS1_URL=https://gitee.com/szd20020329/qiqiclaw/raw/main/scripts/install.ps1"

echo.
echo  QIQI-Claw Installer
echo  Source: %INSTALL_SOURCE%
echo  Launching PowerShell installer...
echo.

powershell -ExecutionPolicy ByPass -NoProfile -Command "$env:QIQICLAW_INSTALL_SOURCE='%INSTALL_SOURCE%'; iex (irm '%INSTALL_PS1_URL%')"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
    echo    powershell -ExecutionPolicy ByPass -c "$env:QIQICLAW_INSTALL_SOURCE='%INSTALL_SOURCE%'; iex (irm '%INSTALL_PS1_URL%')"
    echo.
    pause
    exit /b 1
)
