#!/bin/sh
# Lancement depuis le code source sur Mac ou Linux : ouvre la page de réglage.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 n'est pas installé. Installez-le depuis https://www.python.org/downloads/"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Première utilisation : installation des composants, patientez..."
  python3 -m venv .venv || exit 1
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -r requirements.txt || exit 1
fi

exec .venv/bin/python veille_raa.py
