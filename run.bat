@echo off
echo Starting YouTube Automation Factory...

echo Starting Backend...
start "Backend (FastAPI)" cmd /k "cd backend && call venv\Scripts\activate.bat && uvicorn main:app --reload --port 8000"

echo Starting Frontend...
start "Frontend (Next.js)" cmd /k "cd frontend && npm run dev"

echo ----------------------------------------------------
echo Both services have been started in new windows!
echo To turn them off, simply close those two new windows.
echo ----------------------------------------------------
pause
