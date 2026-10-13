@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt impyla
set /p FROM=Start date (YYYY-MM-DD): 
set /p TO=End date   (YYYY-MM-DD): 
python analyze.py --from %FROM% --to %TO%
pause
