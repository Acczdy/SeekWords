@echo off
if exist "%~dp0SeekWords.exe" (
    start "" "%~dp0SeekWords.exe" %*
) else (
    start "" wscript.exe "%~dp0seekwords.vbs" %*
)
