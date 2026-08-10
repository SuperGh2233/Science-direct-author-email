@echo off
REM Lab4 launcher - venv python + UTF-8 (只跑 Turnstile 解码 lab, 不跑 lab1/2/3)
REM usage: lab4                          demo (官方测试 key, 只打 localhost)
REM        lab4 https://你的站/          打你的站
REM        lab4 --headed https://你的站/ 弹真窗口
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
"C:\Users\hao.guo\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%~dp0lab4_turnstile.py" %*
