"""
Mise en place du contrôle automatique sur l'ordinateur.

- Windows : une tâche du Planificateur de tâches (« Veille RAA »), sans droits administrateur.
- Mac : un agent de lancement (~/Library/LaunchAgents/fr.veilleraa.controle.plist).
- Linux : une ligne dans la crontab de l'utilisateur.

Dans les trois cas, l'ordinateur lance « VeilleRAA --controle » à intervalle régulier,
tant qu'il est allumé et que la session est ouverte.
"""

from __future__ import annotations

import datetime as dt
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import veille_raa as moteur

NOM_TACHE = "Veille RAA"
ETIQUETTE_MAC = "fr.veilleraa.controle"
MARQUE_CRON = "# veille-raa"
SANS_FENETRE = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


class ErreurPlanification(Exception):
    pass


def _executer(commande: list[str], entree: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(commande, input=entree, capture_output=True, text=True,
                          creationflags=SANS_FENETRE, timeout=60)


# --------------------------------------------------------------------------
#  Quel programme lancer ?
# --------------------------------------------------------------------------

def dossier_programme() -> Path:
    """Emplacement stable de l'application (elle peut avoir été lancée depuis Téléchargements)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / moteur.NOM_APPLI
    if sys.platform == "darwin":
        return Path.home() / "Applications"
    return Path.home() / ".local" / "share" / "veille-raa"


def installer_programme() -> list[str]:
    """Range l'application à un endroit stable et renvoie la commande à planifier."""
    if not moteur.FIGE:
        python = Path(sys.executable)
        if sys.platform == "win32":
            sans_console = python.with_name("pythonw.exe")
            if sans_console.exists():
                python = sans_console
        return [str(python), str(moteur.PROGRAMME / "veille_raa.py"), "--controle"]

    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        if "/AppTranslocation/" in str(executable):
            raise ErreurPlanification(
                "Faites d'abord glisser Veille RAA dans le dossier Applications, "
                "puis rouvrez-le depuis ce dossier.")
        return [str(executable), "--controle"]

    destination = dossier_programme() / ("VeilleRAA.exe" if sys.platform == "win32" else "VeilleRAA")
    deja_en_place = destination.exists() and destination.resolve() == executable
    if not deja_en_place:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(executable, destination)
        except PermissionError:
            if not destination.exists():  # copie impossible : on garde l'emplacement actuel
                destination = executable
    if sys.platform == "win32":
        _raccourcis(destination)
    return [str(destination), "--controle"]


def _raccourcis(cible: Path) -> None:
    """Raccourcis « Veille RAA » dans le menu Démarrer et sur le Bureau (au mieux)."""
    script = (
        "$w = New-Object -ComObject WScript.Shell;"
        "foreach ($d in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {"
        "  $s = $w.CreateShortcut((Join-Path $d 'Veille RAA.lnk'));"
        f"  $s.TargetPath = '{str(cible).replace(chr(39), chr(39) * 2)}';"
        "  $s.Description = 'Réglages de la veille des recueils des actes administratifs';"
        "  $s.Save() }")
    try:
        _executer(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])
    except Exception:
        pass


# --------------------------------------------------------------------------
#  Windows
# --------------------------------------------------------------------------

def xml_tache_windows(commande: list[str], minutes: int) -> str:
    executable, arguments = commande[0], " ".join(f'"{a}"' if " " in a else a for a in commande[1:])
    debut = dt.datetime.now().replace(second=0, microsecond=0).isoformat()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Veille RAA : contrôle des recueils des actes administratifs des préfectures</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT{int(minutes)}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{debut}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(executable)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(str(moteur.DOSSIER))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _windows_installer(commande: list[str], minutes: int) -> None:
    moteur.DOSSIER.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(xml_tache_windows(commande, minutes))
        chemin = f.name
    try:
        r = _executer(["schtasks", "/Create", "/TN", NOM_TACHE, "/XML", chemin, "/F"])
    finally:
        os.unlink(chemin)
    if r.returncode != 0:
        raise ErreurPlanification(
            "Windows a refusé de créer la tâche planifiée : " + (r.stderr or r.stdout).strip())


def _windows_active() -> bool:
    return _executer(["schtasks", "/Query", "/TN", NOM_TACHE]).returncode == 0


def _windows_retirer() -> None:
    _executer(["schtasks", "/Delete", "/TN", NOM_TACHE, "/F"])


# --------------------------------------------------------------------------
#  Mac
# --------------------------------------------------------------------------

def _plist_mac() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{ETIQUETTE_MAC}.plist"


def contenu_plist(commande: list[str], minutes: int) -> bytes:
    return plistlib.dumps({
        "Label": ETIQUETTE_MAC,
        "ProgramArguments": commande,
        "StartInterval": int(minutes) * 60,
        "RunAtLoad": True,
        "WorkingDirectory": str(moteur.DOSSIER),
        "StandardOutPath": str(moteur.DOSSIER / "planificateur.txt"),
        "StandardErrorPath": str(moteur.DOSSIER / "planificateur.txt"),
    })


def _mac_installer(commande: list[str], minutes: int) -> None:
    moteur.DOSSIER.mkdir(parents=True, exist_ok=True)
    plist = _plist_mac()
    plist.parent.mkdir(parents=True, exist_ok=True)
    _mac_retirer()
    plist.write_bytes(contenu_plist(commande, minutes))
    domaine = f"gui/{os.getuid()}"
    r = _executer(["launchctl", "bootstrap", domaine, str(plist)])
    if r.returncode != 0:
        r = _executer(["launchctl", "load", "-w", str(plist)])
        if r.returncode != 0:
            raise ErreurPlanification("macOS a refusé l'agent de lancement : " + (r.stderr or r.stdout).strip())


def _mac_active() -> bool:
    return _plist_mac().exists()


def _mac_retirer() -> None:
    plist = _plist_mac()
    _executer(["launchctl", "bootout", f"gui/{os.getuid()}/{ETIQUETTE_MAC}"])
    if plist.exists():
        _executer(["launchctl", "unload", "-w", str(plist)])
        plist.unlink()


# --------------------------------------------------------------------------
#  Linux
# --------------------------------------------------------------------------

def _crontab_actuelle() -> list[str]:
    if not shutil.which("crontab"):
        raise ErreurPlanification("La commande crontab est absente de cet ordinateur.")
    r = _executer(["crontab", "-l"])
    return r.stdout.splitlines() if r.returncode == 0 else []


def ligne_cron(commande: list[str], minutes: int) -> str:
    morceaux = " ".join("'" + a.replace("'", "'\\''") + "'" for a in commande)
    return f"*/{int(minutes)} * * * * {morceaux} >/dev/null 2>&1 {MARQUE_CRON}"


def _linux_installer(commande: list[str], minutes: int) -> None:
    lignes = [l for l in _crontab_actuelle() if MARQUE_CRON not in l]
    lignes.append(ligne_cron(commande, minutes))
    r = _executer(["crontab", "-"], "\n".join(lignes) + "\n")
    if r.returncode != 0:
        raise ErreurPlanification("Impossible de modifier la crontab : " + r.stderr.strip())


def _linux_active() -> bool:
    try:
        return any(MARQUE_CRON in l for l in _crontab_actuelle())
    except ErreurPlanification:
        return False


def _linux_retirer() -> None:
    try:
        lignes = [l for l in _crontab_actuelle() if MARQUE_CRON not in l]
    except ErreurPlanification:
        return
    _executer(["crontab", "-"], "\n".join(lignes) + ("\n" if lignes else ""))


# --------------------------------------------------------------------------
#  Interface commune
# --------------------------------------------------------------------------

def _systeme():
    if sys.platform == "win32":
        return _windows_installer, _windows_active, _windows_retirer
    if sys.platform == "darwin":
        return _mac_installer, _mac_active, _mac_retirer
    return _linux_installer, _linux_active, _linux_retirer


def activer(minutes: int) -> str:
    installer, _, _ = _systeme()
    commande = installer_programme()
    if moteur.DOSSIER != moteur.dossier_par_defaut():
        commande += ["--dossier", str(moteur.DOSSIER)]
    installer(commande, max(5, int(minutes)))
    moteur.journal(f"Contrôle automatique activé toutes les {minutes} minutes : {' '.join(commande)}")
    return f"Contrôle automatique toutes les {minutes} minutes, tant que l'ordinateur est allumé."


def est_active() -> bool:
    try:
        return _systeme()[1]()
    except Exception:
        return False


def desactiver() -> None:
    _systeme()[2]()
    moteur.journal("Contrôle automatique désactivé.")
