@echo off
rem Daily run: save today's portfolio (B3 serves only the current one), fetch closes and quotes, write the report.
rem Schedule once, from a terminal in this folder:
rem   schtasks /create /tn "ibov update" /sc weekly /d MON,TUE,WED,THU,FRI /st 22:30 /tr "\"%CD%\run_daily.cmd\""
cd /d "%~dp0"
if not exist data mkdir data
echo ==== %date% %time% >> data\daily.log
python -m ibov update >> data\daily.log 2>&1
python -m ibov report >> data\daily.log 2>&1
