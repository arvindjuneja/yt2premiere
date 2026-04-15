@echo off
REM Launch YT to Premiere
cd /d "%~dp0"
call venv\Scripts\activate.bat
python app.py
