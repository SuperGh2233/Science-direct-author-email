@echo off
REM Lab5 launcher - venv python + UTF-8 (取内容: 抽通讯作者邮箱, 层2)
REM usage: lab5 https://你的站/article/123        单篇
REM        lab5 --list urls.txt                 批量 (看数量不定+不漏)
REM        lab5 --headed https://...           渲染兜底弹真窗
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
"C:\Users\hao.guo\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%~dp0lab5_extract.py" %*
