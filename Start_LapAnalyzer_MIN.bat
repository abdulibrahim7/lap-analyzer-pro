@echo off
rem Legacy launcher name: start backend without automatically starting/pulling Ollama.
call "%~dp0Start_LapAnalyzer_Ollama.bat" --skip-ollama %*
exit /b %errorlevel%
