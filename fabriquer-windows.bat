@echo off
chcp 65001 >nul
title Fabrication de VeilleRAA-Windows.exe
cd /d "%~dp0"
rem Fabrique l'application autonome VeilleRAA-Windows.exe (aucune installation
rem nécessaire chez la personne qui la reçoit). À lancer sur un PC où Python est installé.

where py >nul 2>nul
if %errorlevel%==0 (set PYTHON=py -3) else (set PYTHON=python)
%PYTHON% --version >nul 2>nul
if not %errorlevel%==0 (
  echo Python est necessaire pour FABRIQUER l'application ^(pas pour l'utiliser^).
  echo Installez-le depuis https://www.python.org/downloads/ puis relancez ce fichier.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Preparation de l'environnement...
  %PYTHON% -m venv .venv
)
echo Installation des composants (1 a 2 minutes)...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt pyinstaller
if not %errorlevel%==0 (
  echo Echec de l'installation des composants.
  pause
  exit /b 1
)

echo Fabrication de l'application (1 a 3 minutes)...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name VeilleRAA --add-data "prefectures.yml;." --distpath dist --workpath build veille_raa.py
if not exist "dist\VeilleRAA.exe" (
  echo La fabrication a echoue : envoyez le texte ci-dessus.
  pause
  exit /b 1
)
move /y "dist\VeilleRAA.exe" "VeilleRAA-Windows.exe" >nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q VeilleRAA.spec 2>nul

echo.
echo Termine : le fichier VeilleRAA-Windows.exe se trouve dans ce dossier.
echo C'est le seul fichier a transmettre aux confreres.
explorer /select,"%~dp0VeilleRAA-Windows.exe"
pause
