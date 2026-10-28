@echo off
chcp 65001 >nul
title Veille RAA
cd /d "%~dp0"
rem Lancement depuis le code source (l'application VeilleRAA-Windows.exe n'a pas besoin de ce fichier).

where py >nul 2>nul
if %errorlevel%==0 (set PYTHON=py -3) else (set PYTHON=python)
%PYTHON% --version >nul 2>nul
if not %errorlevel%==0 (
  echo.
  echo Ce fichier sert a lancer Veille RAA depuis le code source et demande Python.
  echo Pour un ordinateur sans Python, utilisez plutot l'application VeilleRAA-Windows.exe :
  echo un double-clic suffit, rien a installer.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Premiere utilisation : installation des composants, patientez...
  %PYTHON% -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)

start "" ".venv\Scripts\pythonw.exe" veille_raa.py
