@echo off
cd /d %~dp0
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
python seed.py
start http://127.0.0.1:8000
uvicorn main:app --host 127.0.0.1 --port 8000
