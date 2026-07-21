@echo off
title Prank Remover
echo Removing prank files...
echo.

taskkill /f /im Prank.exe >nul 2>&1
taskkill /f /im Prank_Test.exe >nul 2>&1
taskkill /f /im Setup.exe >nul 2>&1

reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v AudioService /f >nul 2>&1
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WindowsCppRedist /f >nul 2>&1

attrib -h -s "%ProgramData%\WindowsCppRedist" /s /d >nul 2>&1
rd /s /q "%ProgramData%\WindowsCppRedist" >nul 2>&1

echo Done! Everything has been removed.
pause
