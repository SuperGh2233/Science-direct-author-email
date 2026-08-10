@echo off
REM anti-bot lab launcher - venv python + UTF-8
REM usage: lab                    run all 3 with defaults
REM        lab https://yoursite/   run all 3, lab2+lab3 hit your site
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
"C:\Users\hao.guo\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%~dp0lab.py" %*
