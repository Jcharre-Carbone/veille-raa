#!/usr/bin/env python3
"""
Veille RAA
==========

Surveille les recueils des actes administratifs (RAA) publiés sur les sites
des préfectures et envoie une alerte (WhatsApp, mail, notification) dès qu'un
nouveau document contient l'un des mots-clés choisis.

Utilisation ordinaire : double-cliquer sur l'application (ou lancer
« python veille_raa.py » sans rien ajouter). La page de réglage s'ouvre dans
le navigateur ; une fois la veille activée, l'ordinateur la lance tout seul.

Commandes pour les initiés :
    --controle              un contrôle, puis arrêt (utilisé par le planificateur)
    --boucle 10             un contrôle toutes les 10 minutes, fenêtre ouverte
    --essai                 contrôle sans envoyer d'alerte ni rien mémoriser
    --test-notification     envoie un message de test
    --analyser PDF          teste les mots-clés sur un PDF (fichier ou adresse)
    --lister-prefectures    affiche les préfectures connues
    --dossier CHEMIN        dossier des réglages et de la mémoire de la veille
"""

from __future__ import annotations

import argparse
import datetime as dt
import heapq
import html
import io
import json
import os
import re
import smtplib
import ssl
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

try:
    import requests
    import yaml
    from bs4 import BeautifulSoup
    from pypdf import PdfReader
except ImportError as erreur:  # pragma: no cover
    print(
        "Il manque un composant ({}).\n"
        "Lancez d'abord : pip install -r requirements.txt".format(erreur.name)
    )
    sys.exit(1)

VERSION = "2.1.0"
NOM_APPLI = "VeilleRAA"
FIGE = bool(getattr(sys, "frozen", False))          # application fabriquée (.exe / .app)
PROGRAMME = Path(__file__).resolve().parent           # dossier du code source
RESSOURCES = Path(getattr(sys, "_MEIPASS", PROGRAMME))  # fichiers fournis avec le programme


def dossier_par_defaut() -> Path:
    """Dossier personnel où sont rangés réglages, mémoire et journal."""
    env = os.environ.get("VEILLE_RAA_DOSSIER")
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / NOM_APPLI


DOSSIER = Path()
FICHIER_CONFIG = FICHIER_SECRETS = FICHIER_ETAT = FICHIER_JOURNAL = FICHIER_VERROU = Path()


def definir_dossier(chemin: Path) -> None:
    global DOSSIER, FICHIER_CONFIG, FICHIER_SECRETS, FICHIER_ETAT, FICHIER_JOURNAL, FICHIER_VERROU
    DOSSIER = Path(chemin)
    FICHIER_CONFIG = DOSSIER / "config.yml"
    FICHIER_SECRETS = DOSSIER / "secrets.yml"
    FICHIER_ETAT = DOSSIER / "etat" / "etat.json"
    FICHIER_JOURNAL = DOSSIER / "journal.txt"
    FICHIER_VERROU = DOSSIER / "etat" / "verrou"


definir_dossier(dossier_par_defaut())


def fichier_prefectures() -> Path:
    personnel = DOSSIER / "prefectures.yml"
    return personnel if personnel.exists() else RESSOURCES / "prefectures.yml"


AGENT = f"VeilleRAA/{VERSION} (veille des recueils des actes administratifs)"

MOIS = [
    "janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
    "aout", "septembre", "octobre", "novembre", "decembre",
]

# Pages qui font partie de la rubrique « Recueil des actes administratifs »
MOTIF_RAA = re.compile(r"recueil|actes?[-_ ]+administratifs?|(?<![a-z])raa(?![a-z])", re.I)
# Pages par lesquelles on peut passer pour trouver la rubrique RAA
MOTIF_NAVIGATION = re.compile(r"publications?", re.I)
MOTIF_PAGINATION = re.compile(r"(?:^|[?&])(?:page|p|start|offset)=\d+", re.I)
MOTIF_ANNEE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
# Numérotation normalisée des actes dans les RAA : 34-2026-10-09-00012
MOTIF_NUMERO_ACTE = re.compile(r"\b\d{2,3}[AB]?-20\d{2}-\d{2}-\d{2}-\d{3,6}\b[^\n]{0,220}")
EXTENSIONS_IGNOREES = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip", ".doc", ".docx",
    ".odt", ".xls", ".xlsx", ".ods", ".csv", ".mp3", ".mp4", ".css", ".js",
    ".xml", ".rss", ".ics",
)

REMPLACEMENTS = {
    "œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae",
    "’": "'", "‘": "'", "ʼ": "'", "´": "'", "`": "'",
    " ": " ", " ": " ", " ": " ",
    "‐": "-", "‑": "-", "–": "-",
}



# --------------------------------------------------------------------------
#  Outils généraux
# --------------------------------------------------------------------------

class ErreurConfig(Exception):
    """Réglage absent ou incorrect : message destiné à l'utilisateur."""


def afficher(texte: str) -> None:
    """Affiche à l'écran quand il y en a un (l'application fabriquée n'a pas de console)."""
    if sys.stdout is not None:
        try:
            print(texte, flush=True)
        except Exception:
            pass


def journal(message: str) -> None:
    ligne = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    afficher(ligne)
    try:
        FICHIER_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        if FICHIER_JOURNAL.exists() and FICHIER_JOURNAL.stat().st_size > 1_000_000:
            FICHIER_JOURNAL.replace(FICHIER_JOURNAL.with_name("journal.ancien.txt"))
        with FICHIER_JOURNAL.open("a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except OSError:
        pass


def lire_journal(lignes: int = 80) -> list[str]:
    try:
        contenu = FICHIER_JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return contenu[-lignes:]


def maintenant() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def charger_yaml(chemin: Path) -> dict:
    if not chemin.exists():
        return {}
    try:
        with chemin.open(encoding="utf-8") as f:
            contenu = yaml.safe_load(f)
    except yaml.YAMLError as erreur:
        raise ErreurConfig(
            f"Le fichier {chemin.name} contient une erreur de mise en forme.\n"
            f"Vérifiez les espaces en début de ligne et les tirets.\nDétail : {erreur}")
    return contenu if isinstance(contenu, dict) else {}


def ecrire_fichier(chemin: Path, texte: str, prive: bool = False) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(chemin.name + ".tmp")
    temporaire.write_text(texte, encoding="utf-8")
    if prive:
        try:
            os.chmod(temporaire, 0o600)
        except OSError:
            pass
    temporaire.replace(chemin)


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, apostrophes et espaces uniformisés."""
    return normaliser_avec_index(texte)[0]


def normaliser_avec_index(texte: str) -> tuple[str, list[int]]:
    """Comme normaliser(), mais garde la correspondance avec le texte d'origine
    afin de pouvoir citer l'extrait exact (avec ses accents)."""
    sortie: list[str] = []
    index: list[int] = []
    for i, caractere in enumerate(texte):
        equivalent = REMPLACEMENTS.get(caractere)
        if equivalent is None:
            decompose = unicodedata.normalize("NFKD", caractere)
            equivalent = "".join(c for c in decompose if not unicodedata.combining(c)).lower()
        for c in equivalent:
            sortie.append(c)
            index.append(i)
    return "".join(sortie), index


def espaces(texte: str) -> str:
    return re.sub(r"\s+", " ", texte or "").strip()




# --------------------------------------------------------------------------
#  Mots-clés
# --------------------------------------------------------------------------

@dataclass
class MotCle:
    texte: str
    motif: re.Pattern


def compiler_mot_cle(mot: str) -> MotCle:
    """Transforme un mot-clé saisi par l'utilisateur en expression de recherche.

    - majuscules et accents indifférents ;
    - une étoile * remplace la fin (ou le milieu) d'un mot ;
    - les espaces acceptent les retours à la ligne du PDF ;
    - « re: ... » permet aux initiés d'écrire une expression régulière
      (appliquée au texte en minuscules et sans accents).
    """
    brut = str(mot).strip()
    if brut.lower().startswith("re:"):
        return MotCle(brut, re.compile(brut[3:].strip()))
    norme = re.sub(r"\s+", " ", normaliser(brut))
    morceaux = []
    for c in norme:
        if c == "*":
            morceaux.append(r"\w*")
        elif c == " ":
            morceaux.append(r"\s+")
        elif c in "'-":
            morceaux.append(r"[\s'\-]?")
        else:
            morceaux.append(re.escape(c))
    fin = "" if norme.endswith("*") else r"(?!\w)"
    return MotCle(brut, re.compile(r"(?<!\w)" + "".join(morceaux) + fin))


def preparer_texte_pdf(texte: str) -> str:
    # Recolle les mots coupés en fin de ligne : « manifes-\ntation »
    texte = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", texte)
    return texte


@dataclass
class Extrait:
    mot_cle: str
    page: int | None
    texte: str
    acte: str | None = None


def chercher(texte: str, mots_cles: list[MotCle], page: int | None = None,
             max_par_mot: int = 3) -> list[Extrait]:
    if not texte:
        return []
    norme, index = normaliser_avec_index(texte)
    resultats: list[Extrait] = []
    acte = None
    numero = MOTIF_NUMERO_ACTE.search(texte)
    if numero:
        acte = espaces(numero.group(0))[:220]
    for mot in mots_cles:
        trouves = 0
        for m in mot.motif.finditer(norme):
            if not index:
                break
            debut = index[m.start()]
            fin = index[max(m.end() - 1, m.start())] + 1
            extrait = espaces(texte[max(0, debut - 160):debut] + "«" + texte[debut:fin]
                              + "»" + texte[fin:fin + 160])
            resultats.append(Extrait(mot.texte, page, f"…{extrait}…", acte))
            trouves += 1
            if trouves >= max_par_mot:
                break
    return resultats


# --------------------------------------------------------------------------
#  Accès aux sites
# --------------------------------------------------------------------------

class Navigateur:
    def __init__(self, delai: float, respecter_robots: bool):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": AGENT,
            "Accept-Language": "fr-FR,fr;q=0.9",
        })
        self.delai = max(0.0, float(delai))
        self.dernier_appel = 0.0
        self.respecter_robots = respecter_robots
        self.robots: dict[str, RobotFileParser | None] = {}

    def _patienter(self) -> None:
        attente = self.delai - (time.monotonic() - self.dernier_appel)
        if attente > 0:
            time.sleep(attente)
        self.dernier_appel = time.monotonic()

    def autorise(self, url: str) -> bool:
        if not self.respecter_robots:
            return True
        hote = urlparse(url)
        cle = f"{hote.scheme}://{hote.netloc}"
        if cle not in self.robots:
            regles = RobotFileParser()
            try:
                self._patienter()
                r = self.session.get(cle + "/robots.txt", timeout=(10, 30))
                if r.status_code in (401, 403):
                    regles.disallow_all = True
                elif r.ok:
                    regles.parse(r.text.splitlines())
                else:
                    regles.allow_all = True
            except requests.RequestException:
                regles.allow_all = True
            self.robots[cle] = regles
        return self.robots[cle].can_fetch(AGENT, url)

    def get(self, url: str, flux: bool = False) -> requests.Response | None:
        for tentative in range(3):
            self._patienter()
            try:
                r = self.session.get(url, timeout=(15, 90), stream=flux, allow_redirects=True)
            except requests.RequestException as erreur:
                journal(f"   accès impossible à {url} ({erreur.__class__.__name__}), nouvel essai")
                time.sleep(3 * (tentative + 1))
                continue
            if r.status_code >= 500 or r.status_code == 429:
                journal(f"   le site répond {r.status_code} pour {url}, nouvel essai")
                time.sleep(5 * (tentative + 1))
                continue
            return r
        return None

    def telecharger(self, url: str, taille_max: int) -> bytes | None:
        r = self.get(url, flux=True)
        if r is None or not r.ok:
            return None
        donnees = io.BytesIO()
        for bloc in r.iter_content(chunk_size=256 * 1024):
            donnees.write(bloc)
            if donnees.tell() > taille_max:
                journal(f"   document trop volumineux, ignoré : {url}")
                r.close()
                return None
        return donnees.getvalue()


# --------------------------------------------------------------------------
#  Exploration des pages RAA
# --------------------------------------------------------------------------

@dataclass
class Document:
    url: str
    titre: str
    page_source: str


@dataclass
class Lien:
    url: str
    titre: str
    page_source: str


@dataclass
class ResultatExploration:
    documents: dict[str, Document] = field(default_factory=dict)
    liens_supplementaires: dict[str, Lien] = field(default_factory=dict)
    pages_lues: int = 0
    pages_raa: int = 0
    refus_robots: list[str] = field(default_factory=list)


def nettoyer_titre(titre: str) -> str:
    """« Télécharger Recueil_spécial_n°295 PDF - 5,10 Mb - 19/10/2026 » -> « Recueil spécial n°295 »."""
    t = re.sub(r"^\s*t[ée]l[ée]charger\s*:?\s*", "", titre, flags=re.I)
    t = re.sub(r"\s*[-–(]?\s*\b(pdf|docx?|odt)\b\s*[-–]\s*[\d.,]+\s*[kmg]?o?b?\b.*$", "", t, flags=re.I)
    t = re.sub(r"\.pdf$", "", t, flags=re.I).replace("_", " ")
    return espaces(t) or titre


def est_pdf(url: str) -> bool:
    chemin = urlparse(url).path.lower()
    return chemin.endswith(".pdf") or "/contenu/telechargement/" in chemin


def nettoyer_url(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    url = urldefrag(urljoin(base, href))[0]
    if urlparse(url).scheme not in ("http", "https"):
        return None
    return url


def annee_minimale() -> int:
    aujourd_hui = dt.date.today()
    # En janvier et février, on regarde encore l'année précédente
    return aujourd_hui.year - 1 if aujourd_hui.month <= 2 else aujourd_hui.year


def trop_ancien(texte: str) -> bool:
    annees = [int(a) for a in MOTIF_ANNEE.findall(texte)]
    return bool(annees) and max(annees) < annee_minimale()


def priorite(url: str, titre: str) -> int:
    """Plus le nombre est petit, plus la page est lue tôt."""
    aujourd_hui = dt.date.today()
    texte = normaliser(url + " " + titre)
    score = 10
    if str(aujourd_hui.year) in texte:
        score -= 3
    mois = MOIS[aujourd_hui.month - 1]
    if mois in texte or f"-{aujourd_hui.month:02d}" in texte or f"/{aujourd_hui.month:02d}" in texte:
        score -= 3
    if MOTIF_RAA.search(texte):
        score -= 2
    return score


def explorer(prefecture: str, adresses: list[str], adresses_imposees: bool,
             pages_supplementaires: list[str], nav: Navigateur,
             pages_max: int) -> ResultatExploration:
    res = ResultatExploration()
    hotes = {urlparse(a).netloc for a in adresses + pages_supplementaires}
    prefixes: set[str] = set()
    imposees = set(adresses) if adresses_imposees else set()
    if adresses_imposees:
        prefixes = {urlparse(a).path.rstrip("/") for a in adresses if urlparse(a).path.strip("/")}

    def dans_rubrique(url: str) -> bool:
        if url in imposees:
            return True
        chemin = urlparse(url).path
        if MOTIF_RAA.search(chemin):
            return True
        return any(chemin.startswith(p) for p in prefixes)

    file: list[tuple[int, int, str, str, int]] = []
    compteur = 0
    vues: set[str] = set()
    # Sans adresse imposée, la première adresse est la plus probable ; les
    # suivantes (rubrique Publications, page d'accueil) ne servent qu'en secours.
    secours = set(adresses[1:]) if not adresses_imposees else set()
    for rang, a in enumerate(adresses):
        heapq.heappush(file, (0 if a not in secours else 100 + rang, compteur, a, "", 0))
        compteur += 1

    while file and res.pages_lues < pages_max:
        _, _, url, _, profondeur = heapq.heappop(file)
        if url in vues:
            continue
        if url in secours and res.pages_raa > 0:
            continue
        vues.add(url)
        if not nav.autorise(url):
            res.refus_robots.append(url)
            continue
        r = nav.get(url)
        res.pages_lues += 1
        if r is None or not r.ok or "html" not in r.headers.get("Content-Type", "html"):
            continue
        page_raa = dans_rubrique(r.url) or dans_rubrique(url)
        if page_raa:
            res.pages_raa += 1
            chemin = urlparse(r.url).path.rstrip("/")
            if MOTIF_RAA.search(chemin):
                prefixes.add(chemin)
        soupe = BeautifulSoup(r.text, "html.parser")
        for balise in soupe.find_all("a", href=True):
            cible = nettoyer_url(r.url, balise["href"])
            if not cible or urlparse(cible).netloc not in hotes:
                continue
            titre = espaces(balise.get_text(" ")) or espaces(balise.get("title", ""))
            if est_pdf(cible):
                if page_raa and cible not in res.documents:
                    res.documents[cible] = Document(cible, nettoyer_titre(titre or Path(urlparse(cible).path).name), r.url)
                continue
            if urlparse(cible).path.lower().endswith(EXTENSIONS_IGNOREES):
                continue
            if cible in vues or profondeur >= 5:
                continue
            texte = cible + " " + titre
            if trop_ancien(texte):
                continue
            suivre = False
            if page_raa and (dans_rubrique(cible) or MOTIF_RAA.search(texte)):
                suivre = True
            elif page_raa and urlparse(cible).path == urlparse(r.url).path and MOTIF_PAGINATION.search(urlparse(cible).query):
                suivre = True
            elif not page_raa and MOTIF_RAA.search(texte):
                suivre = True
            elif (not page_raa and profondeur == 0
                  and MOTIF_NAVIGATION.fullmatch(urlparse(cible).path.strip("/"))):
                suivre = True  # lien vers la rubrique « Publications » depuis l'accueil
            if suivre:
                heapq.heappush(file, (priorite(cible, titre) + profondeur, compteur, cible, titre, profondeur + 1))
                compteur += 1

    # Pages complémentaires (actualités, communiqués…) : lues à chaque fois,
    # sans aller plus loin. Les PDF sont analysés, les liens sont comparés
    # aux mots-clés.
    for url in pages_supplementaires:
        if not nav.autorise(url):
            res.refus_robots.append(url)
            continue
        r = nav.get(url)
        res.pages_lues += 1
        if r is None or not r.ok:
            continue
        soupe = BeautifulSoup(r.text, "html.parser")
        for balise in soupe.find_all("a", href=True):
            cible = nettoyer_url(r.url, balise["href"])
            if not cible:
                continue
            titre = espaces(balise.get_text(" ")) or espaces(balise.get("title", ""))
            if est_pdf(cible):
                res.documents.setdefault(cible, Document(cible, nettoyer_titre(titre or Path(urlparse(cible).path).name), r.url))
            elif titre and len(titre) > 12:
                res.liens_supplementaires.setdefault(cible, Lien(cible, titre, r.url))
    return res


# --------------------------------------------------------------------------
#  Lecture des PDF
# --------------------------------------------------------------------------

@dataclass
class Analyse:
    extraits: list[Extrait]
    pages: int
    lisible: bool
    erreur: str | None = None
    pages_scannees: list[int] = field(default_factory=list)
    sommaire: str = ""


def plages(pages: list[int]) -> str:
    """[2, 3, 4, 7] -> « 2 à 4 et 7 »."""
    morceaux, debut = [], None
    for i, p in enumerate(pages):
        if debut is None:
            debut = p
        if i + 1 == len(pages) or pages[i + 1] != p + 1:
            morceaux.append(str(debut) if debut == p else f"{debut} à {p}")
            debut = None
    return ", ".join(morceaux[:-1]) + (" et " if len(morceaux) > 1 else "") + morceaux[-1] if morceaux else ""


def remarque_scan(analyse: Analyse) -> str:
    n, total = len(analyse.pages_scannees), analyse.pages
    if n == total:
        texte = "Document entièrement scanné : son texte ne peut pas être lu automatiquement."
    else:
        texte = (f"{n} page(s) sur {total} sont des images scannées (page{'s' if n > 1 else ''} "
                 f"{plages(analyse.pages_scannees)}) : leur texte n'a pas pu être lu.")
    if analyse.sommaire:
        texte += f" Partie lisible : « {analyse.sommaire} »"
    return texte + " À vérifier à la main."


def analyser_pdf(donnees: bytes, mots_cles: list[MotCle]) -> Analyse:
    try:
        lecteur = PdfReader(io.BytesIO(donnees), strict=False)
        if lecteur.is_encrypted:
            try:
                lecteur.decrypt("")
            except Exception:
                pass
        nb_pages = len(lecteur.pages)
    except Exception as erreur:
        return Analyse([], 0, False, f"PDF illisible ({erreur.__class__.__name__})")
    extraits: list[Extrait] = []
    scannees: list[int] = []
    lu = ""
    for numero, page in enumerate(lecteur.pages, start=1):
        try:
            texte = page.extract_text() or ""
        except Exception:
            texte = ""
        if len(texte.strip()) < 40:  # page sans texte : image scannée (ou page blanche)
            scannees.append(numero)
        elif len(lu) < 400:
            lu += " " + texte
        extraits.extend(chercher(preparer_texte_pdf(texte), mots_cles, page=numero))
    # Document « illisible » si au moins la moitié de ses pages sont des images
    lisible = nb_pages == 0 or len(scannees) / nb_pages < 0.5
    sommaire = espaces(re.sub(r"(?i)recueil des actes\s+administratifs", "", lu))
    if len(sommaire) > 350:
        sommaire = sommaire[:350].rsplit(" ", 1)[0] + "…"
    return Analyse(extraits, nb_pages, lisible, pages_scannees=scannees, sommaire=sommaire)




# --------------------------------------------------------------------------
#  État (documents déjà vus, historique des alertes)
# --------------------------------------------------------------------------

def charger_etat() -> dict:
    if FICHIER_ETAT.exists():
        try:
            etat = json.loads(FICHIER_ETAT.read_text(encoding="utf-8"))
            if isinstance(etat, dict):
                etat.setdefault("prefectures", {})
                etat.setdefault("historique", [])
                return etat
        except (json.JSONDecodeError, OSError):
            journal("Le fichier d'état est abîmé : il est remis à zéro.")
    return {"version": 2, "prefectures": {}, "historique": []}


def enregistrer_etat(etat: dict) -> None:
    ecrire_fichier(FICHIER_ETAT, json.dumps(etat, ensure_ascii=False, indent=1, sort_keys=True))


def etat_prefecture(etat: dict, nom: str) -> dict:
    p = etat["prefectures"].setdefault(nom, {})
    p.setdefault("vus", {})
    p.setdefault("liens_vus", {})
    p.setdefault("tentatives", {})
    p.setdefault("initialise", False)
    p.setdefault("executions_sans_document", 0)
    return p


class Verrou:
    """Empêche deux contrôles simultanés (planificateur et bouton « Contrôler maintenant »)."""

    def __init__(self) -> None:
        self.pris = False

    def __enter__(self) -> "Verrou":
        FICHIER_VERROU.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(FICHIER_VERROU, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.pris = True
                return self
            except FileExistsError:
                try:
                    if time.time() - FICHIER_VERROU.stat().st_mtime > 1800:  # verrou oublié
                        FICHIER_VERROU.unlink()
                        continue
                except FileNotFoundError:
                    continue
                return self
        return self

    def __exit__(self, *exc) -> None:
        if self.pris:
            try:
                FICHIER_VERROU.unlink()
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------
#  Rédaction des messages
# --------------------------------------------------------------------------

@dataclass
class Alerte:
    prefecture: str
    nom_prefecture: str
    titre: str
    url: str
    page_source: str
    extraits: list[Extrait]
    remarque: str | None = None


@dataclass
class Message:
    sujet: str
    texte: str
    html: str | None = None
    court: str = ""
    whatsapp: str = ""
    lien: str | None = None
    libelle_lien: str = "Ouvrir"


def lien_page(url: str, page: int | None) -> str:
    """Lien qui ouvre le PDF directement à la bonne page (navigateurs, Acrobat)."""
    base = url.split("#", 1)[0]
    return f"{base}#page={page}" if page else base


def grouper_par_page(extraits: list[Extrait]) -> list[tuple[int | None, list[Extrait]]]:
    groupes: dict = {}
    for e in extraits:
        groupes.setdefault(e.page, []).append(e)
    return list(groupes.items())


def premiere_page(alerte: Alerte) -> int | None:
    return next((e.page for e in alerte.extraits if e.page), None)


def rediger_alertes(alertes: list[Alerte]) -> Message:
    quand = f"{dt.datetime.now():%d/%m/%Y à %H:%M}"
    prefs = sorted({a.nom_prefecture for a in alertes})
    mots = list(dict.fromkeys(e.mot_cle for a in alertes for e in a.extraits))
    if any(a.extraits for a in alertes):
        sujet = f"[Veille RAA] {', '.join(prefs)} : {len(alertes)} document(s) à regarder"
    else:
        sujet = f"[Veille RAA] {', '.join(prefs)} : {len(alertes)} recueil(s) scanné(s) à vérifier à la main"
    if mots:
        sujet += f" ({', '.join(mots[:5])}{'…' if len(mots) > 5 else ''})"

    lignes = [f"Veille RAA, contrôle du {quand}", ""]
    blocs_html = []
    for a in alertes:
        lignes.append(f"■ {a.nom_prefecture} : {a.titre}")
        lignes.append(f"  Document : {lien_page(a.url, premiere_page(a))}")
        lignes.append(f"  Trouvé sur : {a.page_source}")
        if a.remarque:
            lignes.append(f"  Remarque : {a.remarque}")
        items_html = []
        groupes = grouper_par_page(a.extraits)
        for page, extraits in groupes[:8]:
            loc = f"Page {page}" if page else "Titre du lien"
            mots_page = ", ".join(dict.fromkeys(e.mot_cle for e in extraits))
            lignes.append(f"  {loc} : {mots_page}")
            acte = next((e.acte for e in extraits if e.acte), None)
            if acte:
                lignes.append(f"    Acte : {acte}")
            choisis: list[Extrait] = []
            for e in extraits:
                if len(choisis) >= 2:
                    break
                cle = e.texte.split("«", 1)[-1][:60]
                if all(cle not in c.texte for c in choisis):
                    choisis.append(e)
            for e in choisis:
                lignes.append(f"    {e.texte}")
            if page:
                lignes.append(f"    Ouvrir à la page {page} : {lien_page(a.url, page)}")
            extraits_html = "".join(
                "<div style='margin-top:6px'>"
                + html.escape(e.texte).replace("«", "<mark>").replace("»", "</mark>") + "</div>"
                for e in choisis)
            acte_html = (f"<div style='font-family:Consolas,monospace;font-size:12.5px;margin-top:4px'>"
                         f"{html.escape(acte)}</div>") if acte else ""
            bouton_html = (
                f"<div style='margin-top:8px'><a href='{html.escape(lien_page(a.url, page))}' "
                f"style='display:inline-block;background:#1F4E8C;color:#ffffff;text-decoration:none;"
                f"font-weight:600;font-size:13px;padding:8px 14px;border-radius:6px'>"
                f"Ouvrir le PDF à la page {page}</a></div>") if page else ""
            items_html.append(
                "<div style='background:#F4F5F7;border-radius:6px;padding:12px 14px;margin:10px 0'>"
                f"<b>{loc}</b> : {html.escape(mots_page)}{acte_html}{extraits_html}{bouton_html}</div>")
        if len(groupes) > 8:
            reste = len(groupes) - 8
            lignes.append(f"  … et {reste} autre(s) page(s) concernée(s)")
            items_html.append(f"<div>… et {reste} autre(s) page(s) concernée(s)</div>")
        lignes.append("")
        remarque_html = (f"<div style='border:1px dashed #C2410C;background:#FFF7F0;border-radius:6px;"
                         f"padding:10px 14px;margin:10px 0'>{html.escape(a.remarque)}</div>") if a.remarque else ""
        blocs_html.append(
            "<div style='border:1px solid #D5DAE1;border-radius:8px;margin:18px 0;overflow:hidden'>"
            f"<div style='background:#16202B;color:#ffffff;padding:10px 16px;font-size:13px;"
            f"font-weight:600;letter-spacing:.04em;text-transform:uppercase'>{html.escape(a.nom_prefecture)}</div>"
            f"<div style='padding:14px 16px'><a href='{html.escape(lien_page(a.url, premiere_page(a)))}' "
            f"style='font-size:16px;font-weight:600;color:#1F4E8C'>{html.escape(a.titre)}</a>"
            f"<div style='font-size:12.5px;color:#4A5563;margin-top:4px'>Trouvé sur "
            f"<a href='{html.escape(a.page_source)}' style='color:#4A5563'>{html.escape(a.page_source)}</a></div>"
            f"{remarque_html}{''.join(items_html)}</div></div>")
    avertissement = ("Alerte automatique : vérifiez toujours l'acte lui-même. "
                     "Des oublis ou des faux positifs sont possibles.")
    lignes.append(avertissement)
    corps_html = (
        "<div style='font-family:Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;color:#16202B;"
        "max-width:760px'>"
        f"<div style='border-bottom:2px solid #16202B;padding-bottom:8px;font-size:20px;font-weight:600'>"
        f"Veille RAA <span style='font-size:13px;font-weight:400;color:#4A5563'>contrôle du {quand}</span></div>"
        + "".join(blocs_html)
        + f"<p style='color:#4A5563;font-size:12px'>{avertissement}</p></div>")

    court = "\n".join(
        f"• {a.nom_prefecture} : {a.titre[:110]}"
        + (f" ({', '.join(dict.fromkeys(e.mot_cle for e in a.extraits))})"[:140] if a.extraits else "")
        for a in alertes[:6])
    if len(alertes) > 6:
        court += f"\n… et {len(alertes) - 6} autre(s)."

    # WhatsApp (réservé à l'urgence) : seulement les documents où un mot-clé a été trouvé ;
    # les recueils scannés à vérifier à la main sont signalés par mail.
    urgentes = [a for a in alertes if a.extraits]
    autres = len(alertes) - len(urgentes)
    wa = [f"*Veille RAA* : {len(urgentes)} document(s) à regarder", ""]
    for a in urgentes[:5]:
        wa.append(f"*{a.nom_prefecture}*")
        wa.append(a.titre[:150])
        if a.remarque:
            wa.append(a.remarque[:150])
        for page, extraits in grouper_par_page(a.extraits)[:3]:
            mots_page = ", ".join(dict.fromkeys(e.mot_cle for e in extraits))
            wa.append(f"{'p. ' + str(page) if page else 'Titre'} : {mots_page}"[:160])
        wa.append(lien_page(a.url, premiere_page(a)))
        wa.append("")
    if len(urgentes) > 5:
        wa.append(f"… et {len(urgentes) - 5} autre(s) : détail par mail.")
    if autres:
        wa.append(f"(+ {autres} recueil(s) scanné(s) à vérifier : voir le mail)")
    texte_wa = "\n".join(wa).strip() if urgentes else ""
    if len(texte_wa) > 1500:
        texte_wa = texte_wa[:1450].rsplit("\n", 1)[0] + "\n… (suite par mail)"

    premiere = (urgentes or alertes)[0]
    page = premiere_page(premiere)
    return Message(sujet, "\n".join(lignes), corps_html, court, texte_wa,
                   lien_page(premiere.url, page),
                   f"Ouvrir p. {page}" if page else "Ouvrir")


# --------------------------------------------------------------------------
#  Envoi des alertes
# --------------------------------------------------------------------------

MESSAGERIES = {
    "ovh": ("ssl0.ovh.net", 465),
    "gmail": ("smtp.gmail.com", 587),
    "microsoft": ("smtp.office365.com", 587),
}
URL_CALLMEBOT = "https://api.callmebot.com/whatsapp.php"


def valeur(secrets: dict, cle_env: str, cle_fichier: str, defaut=None):
    v = os.environ.get(cle_env)
    if v not in (None, ""):
        return v
    v = secrets.get(cle_fichier)
    return defaut if v in (None, "") else v


def liste_destinataires(texte) -> list[str]:
    if isinstance(texte, list):
        return [str(t).strip() for t in texte if str(t).strip()]
    return [t.strip() for t in re.split(r"[,;\s]+", str(texte or "")) if t.strip()]


def normaliser_telephone(numero: str) -> str:
    s = re.sub(r"[^\d+]", "", str(numero or ""))
    if s.startswith("00"):
        s = "+" + s[2:]
    if re.fullmatch(r"0[1-9]\d{8}", s):  # numéro français saisi sans indicatif
        s = "+33" + s[1:]
    return s


def lire_secrets() -> dict:
    return charger_yaml(FICHIER_SECRETS)


class Notificateur:
    def __init__(self, secrets: dict, essai: bool = False):
        self.essai = essai
        messagerie = str(valeur(secrets, "MESSAGERIE", "messagerie", "") or "").lower()
        hote_defaut, port_defaut = MESSAGERIES.get(messagerie, (None, 465))
        self.smtp_hote = valeur(secrets, "SMTP_HOTE", "smtp_hote", hote_defaut)
        self.smtp_port = int(valeur(secrets, "SMTP_PORT", "smtp_port", port_defaut))
        self.smtp_utilisateur = valeur(secrets, "SMTP_UTILISATEUR", "smtp_utilisateur")
        self.smtp_mot_de_passe = valeur(secrets, "SMTP_MOT_DE_PASSE", "smtp_mot_de_passe")
        self.destinataires = liste_destinataires(valeur(secrets, "MAIL_DESTINATAIRES", "mail_destinataires", ""))
        self.expediteur = (valeur(secrets, "MAIL_EXPEDITEUR", "mail_expediteur")
                           or self.smtp_utilisateur or (self.destinataires[0] if self.destinataires else None))
        if not self.smtp_utilisateur and self.smtp_mot_de_passe and self.expediteur:
            self.smtp_utilisateur = self.expediteur
        self.ntfy_sujet = valeur(secrets, "NTFY_SUJET", "ntfy_sujet")
        self.ntfy_serveur = (valeur(secrets, "NTFY_SERVEUR", "ntfy_serveur", "https://ntfy.sh") or "").rstrip("/")
        self.wa_numero = normaliser_telephone(valeur(secrets, "WHATSAPP_NUMERO", "whatsapp_numero", ""))
        self.wa_cle = str(valeur(secrets, "WHATSAPP_CLE", "whatsapp_cle", "") or "").strip()
        self.wa_url = os.environ.get("WHATSAPP_URL") or URL_CALLMEBOT

    @property
    def mail_actif(self) -> bool:
        return bool(self.smtp_hote and self.destinataires and self.expediteur)

    @property
    def ntfy_actif(self) -> bool:
        return bool(self.ntfy_sujet)

    @property
    def whatsapp_actif(self) -> bool:
        return bool(self.wa_numero and self.wa_cle)

    def canaux(self) -> list[str]:
        sortie = []
        if self.whatsapp_actif:
            sortie.append(f"WhatsApp ({self.wa_numero})")
        if self.mail_actif:
            sortie.append(f"mail ({', '.join(self.destinataires)})")
        if self.ntfy_actif:
            sortie.append("notification ntfy")
        return sortie

    def resume_canaux(self) -> str:
        c = self.canaux()
        return " + ".join(c) if c else "aucun moyen d'alerte réglé (alertes affichées ici seulement)"

    def envoyer(self, message: Message, priorite_ntfy: int = 4) -> dict[str, str]:
        """Envoie sur tous les canaux réglés. Renvoie, par canal, « ok » ou le motif de l'échec."""
        resultats: dict[str, str] = {}
        if self.essai:
            journal("MODE ESSAI : aucun message envoyé. Voici ce qui aurait été envoyé :")
            afficher("\n" + message.sujet + "\n" + "=" * min(len(message.sujet), 100) + "\n" + message.texte
                     + ("\n\n--- WhatsApp ---\n" + message.whatsapp if message.whatsapp else "") + "\n")
            return {"essai": "ok"}
        if not (self.mail_actif or self.ntfy_actif or self.whatsapp_actif):
            afficher("\n" + message.sujet + "\n" + message.texte + "\n")
            return {"aucun": "aucun moyen d'alerte n'est réglé"}
        texte_wa = message.whatsapp or ("" if (self.mail_actif or self.ntfy_actif) else (message.court or message.texte))
        envois = [
            # Sans mail ni ntfy, WhatsApp transmet tout, y compris les recueils scannés à vérifier
            ("WhatsApp", self.whatsapp_actif and bool(texte_wa), lambda: self._whatsapp(texte_wa)),
            ("Mail", self.mail_actif, lambda: self._mail(message)),
            ("ntfy", self.ntfy_actif, lambda: self._ntfy(message, priorite_ntfy)),
        ]
        for nom, actif, action in envois:
            if not actif:
                continue
            try:
                action()
                resultats[nom] = "ok"
                journal(f"   {nom} : envoyé")
            except Exception as erreur:
                resultats[nom] = str(erreur) or erreur.__class__.__name__
                journal(f"   {nom} : ÉCHEC ({resultats[nom]})")
        return resultats

    def _mail(self, message: Message) -> None:
        m = EmailMessage()
        m["Subject"] = message.sujet
        m["From"] = self.expediteur
        m["To"] = ", ".join(self.destinataires)
        m.set_content(message.texte)
        if message.html:
            m.add_alternative(message.html, subtype="html")
        contexte = ssl.create_default_context()
        try:
            if self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_hote, self.smtp_port, context=contexte, timeout=60) as s:
                    s.ehlo()
                    if self.smtp_utilisateur and s.has_extn("auth"):
                        s.login(self.smtp_utilisateur, self.smtp_mot_de_passe or "")
                    s.send_message(m)
            else:
                with smtplib.SMTP(self.smtp_hote, self.smtp_port, timeout=60) as s:
                    s.ehlo()
                    if s.has_extn("starttls"):
                        s.starttls(context=contexte)
                        s.ehlo()
                    if self.smtp_utilisateur and s.has_extn("auth"):
                        s.login(self.smtp_utilisateur, self.smtp_mot_de_passe or "")
                    s.send_message(m)
        except smtplib.SMTPAuthenticationError:
            raise RuntimeError("identifiant ou mot de passe de la messagerie refusé")
        except (OSError, smtplib.SMTPException) as erreur:
            raise RuntimeError(f"serveur de messagerie injoignable ou refus ({erreur})")

    def _ntfy(self, message: Message, priorite_ntfy: int) -> None:
        corps = {
            "topic": self.ntfy_sujet,
            "title": message.sujet[:250],
            "message": (message.court or message.texte)[:3500],
            "priority": priorite_ntfy,
            "tags": ["rotating_light"] if priorite_ntfy >= 4 else ["information_source"],
        }
        if message.lien:
            corps["click"] = message.lien
            corps["actions"] = [{"action": "view", "label": message.libelle_lien, "url": message.lien}]
        r = requests.post(self.ntfy_serveur + "/", json=corps, timeout=30)
        r.raise_for_status()

    def _whatsapp(self, texte: str) -> None:
        r = requests.get(self.wa_url, params={"phone": self.wa_numero, "text": texte, "apikey": self.wa_cle},
                         timeout=60)
        corps = re.sub(r"<[^>]+>", " ", r.text or "")
        corps = re.sub(r"\s+", " ", corps).strip()
        if r.status_code != 200:
            raise RuntimeError(f"le service WhatsApp répond {r.status_code} : {corps[:160]}")
        erreur = re.search(r"\b(error|invalid|not\s+valid|wrong|incorrect|not\s+activated)\b", corps, re.I)
        succes = re.search(r"\b(queued|sent|success)\b", corps, re.I)
        if erreur and not succes:
            raise RuntimeError(f"refusé par le service WhatsApp : {corps[:200]}")


def message_service(texte: str) -> Message:
    return Message("[Veille RAA] Information sur la surveillance", texte,
                   court=texte[:3000], whatsapp=("*Veille RAA* : information\n\n" + texte)[:1500])


# --------------------------------------------------------------------------
#  Configuration
# --------------------------------------------------------------------------

MOTS_CLES_DEFAUT = [
    "lycée*", "manifestation*", "manifestant*", "manifester", "rassemblement*",
    "cortège*", "drone*", "aéronef*", "captation d'images",
]
AVANCE_DEFAUT = {
    "adresses_personnalisees": {},
    "pages_supplementaires": {},
    "alerter_si_pdf_illisible": True,
    "respecter_robots_txt": True,
    "delai_entre_requetes_secondes": 1.5,
    "pages_max_par_prefecture": 40,
    "pdf_max_par_execution": 25,
    "taille_max_pdf_mo": 80,
    "alerte_si_aucun_document_apres": 12,
}


def lire_config_brute() -> dict:
    brut = charger_yaml(FICHIER_CONFIG)
    avance = dict(AVANCE_DEFAUT)
    avance.update(brut.get("avance") or {})
    return {
        "prefectures": list(brut.get("prefectures") or []),
        "mots_cles": list(brut["mots_cles"]) if brut.get("mots_cles") is not None else list(MOTS_CLES_DEFAUT),
        "frequence_minutes": int(brut.get("frequence_minutes") or 10),
        "avance": avance,
    }


def ecrire_config_brute(brut: dict) -> None:
    entete = ("# Réglages de Veille RAA.\n"
              "# Ce fichier est écrit par la page de réglage : le plus simple est de passer par elle.\n\n")
    ecrire_fichier(FICHIER_CONFIG, entete + yaml.safe_dump(brut, allow_unicode=True, sort_keys=False))


def ecrire_secrets(secrets: dict) -> None:
    propres = {k: v for k, v in secrets.items() if v not in (None, "")}
    entete = ("# Moyens d'alerte de Veille RAA (fichier personnel, à ne jamais partager).\n\n")
    ecrire_fichier(FICHIER_SECRETS, entete + yaml.safe_dump(propres, allow_unicode=True, sort_keys=False),
                   prive=True)


def catalogue_prefectures() -> dict[str, dict]:
    return charger_yaml(fichier_prefectures()).get("prefectures", {}) or {}


@dataclass
class Config:
    prefectures: list[str]
    mots_cles: list[MotCle]
    frequence: int
    pages_max: int
    delai: float
    pdf_max_par_execution: int
    taille_max_pdf: int
    alerter_si_illisible: bool
    respecter_robots: bool
    adresses_personnalisees: dict[str, list[str]]
    pages_supplementaires: dict[str, list[str]]
    seuil_alerte_panne: int
    catalogue: dict[str, dict]


def cle_prefecture(texte: str) -> str:
    return re.sub(r"[\s']+", "-", normaliser(str(texte)).strip())


def charger_config(brut: dict | None = None) -> Config:
    brut = lire_config_brute() if brut is None else brut
    catalogue = catalogue_prefectures()
    avance = brut.get("avance") or {}

    def dictionnaire_de_listes(valeurs) -> dict[str, list[str]]:
        sortie = {}
        for cle, liste in (valeurs or {}).items():
            if isinstance(liste, str):
                liste = [liste]
            if liste:
                sortie[cle_prefecture(cle)] = [str(u).strip() for u in liste if str(u).strip()]
        return sortie

    personnalisees = dictionnaire_de_listes(avance.get("adresses_personnalisees"))
    prefectures = [cle_prefecture(p) for p in (brut.get("prefectures") or []) if str(p).strip()]
    if not prefectures:
        raise ErreurConfig("Aucune préfecture n'est choisie.")
    inconnues = [p for p in prefectures if p not in catalogue and p not in personnalisees]
    if inconnues:
        raise ErreurConfig(
            "Préfecture(s) inconnue(s) : " + ", ".join(inconnues)
            + ". Indiquez l'adresse de la page des recueils dans les options avancées.")
    mots = [m for m in (brut.get("mots_cles") or []) if str(m).strip()]
    if not mots:
        raise ErreurConfig("Aucun mot-clé n'est indiqué.")

    def nombre(cle, type_):
        try:
            return type_(avance.get(cle, AVANCE_DEFAUT[cle]))
        except (TypeError, ValueError):
            return type_(AVANCE_DEFAUT[cle])

    return Config(
        prefectures=list(dict.fromkeys(prefectures)),
        mots_cles=[compiler_mot_cle(m) for m in mots],
        frequence=max(5, int(brut.get("frequence_minutes") or 10)),
        pages_max=nombre("pages_max_par_prefecture", int),
        delai=nombre("delai_entre_requetes_secondes", float),
        pdf_max_par_execution=nombre("pdf_max_par_execution", int),
        taille_max_pdf=int(nombre("taille_max_pdf_mo", float) * 1024 * 1024),
        alerter_si_illisible=bool(avance.get("alerter_si_pdf_illisible", True)),
        respecter_robots=bool(avance.get("respecter_robots_txt", True)),
        adresses_personnalisees=personnalisees,
        pages_supplementaires=dictionnaire_de_listes(avance.get("pages_supplementaires")),
        seuil_alerte_panne=nombre("alerte_si_aucun_document_apres", int),
        catalogue=catalogue,
    )


def adresses_de(config: Config, nom: str) -> tuple[list[str], bool]:
    if nom in config.adresses_personnalisees:
        return config.adresses_personnalisees[nom], True
    fiche = config.catalogue.get(nom, {})
    site = fiche.get("site", f"https://www.{nom}.gouv.fr").rstrip("/")
    if fiche.get("raa"):
        return [fiche["raa"]] if isinstance(fiche["raa"], str) else list(fiche["raa"]), True
    return [
        f"{site}/Publications/Recueil-des-actes-administratifs",
        f"{site}/Publications",
        f"{site}/",
    ], False


def nom_affiche(config: Config, nom: str) -> str:
    return config.catalogue.get(nom, {}).get("nom", nom)


# --------------------------------------------------------------------------
#  Un contrôle complet
# --------------------------------------------------------------------------

SEUIL_REINITIALISATION = 150


def verifier(config: Config, notificateur: Notificateur, essai: bool = False) -> dict:
    """Contrôle toutes les préfectures. Renvoie un résumé (nombre d'alertes, envois)."""
    etat = charger_etat()
    nav = Navigateur(config.delai, config.respecter_robots)
    alertes: list[Alerte] = []
    messages_service: list[str] = []

    for nom in config.prefectures:
        libelle = nom_affiche(config, nom)
        ep = etat_prefecture(etat, nom)
        adresses, imposees = adresses_de(config, nom)
        supplementaires = config.pages_supplementaires.get(nom, [])
        journal(f"{libelle} : exploration ({adresses[0]})")
        try:
            res = explorer(nom, adresses, imposees, supplementaires, nav, config.pages_max)
        except Exception as erreur:  # une préfecture en panne ne bloque pas les autres
            journal(f"   erreur pendant l'exploration : {erreur!r}")
            ep["probleme"] = "erreur pendant l'exploration du site"
            continue
        journal(f"   {res.pages_lues} page(s) lue(s), {res.pages_raa} page(s) RAA, "
                f"{len(res.documents)} document(s) repéré(s)")
        ep["dernier_controle"] = maintenant()
        ep["nb_documents"] = len(res.documents)
        ep["probleme"] = None

        if res.refus_robots and not res.documents:
            ep["probleme"] = "accès refusé par le fichier robots.txt du site"
            if not ep.get("robots_signale"):
                ep["robots_signale"] = True
                messages_service.append(
                    f"{libelle} : le site interdit l'accès automatique (robots.txt). "
                    f"Voir les options avancées de la page de réglage.")
            journal("   accès refusé par robots.txt")
            continue

        if not res.documents:
            ep["executions_sans_document"] += 1
            ep["probleme"] = "aucun recueil trouvé sur le site"
            if ep["executions_sans_document"] == config.seuil_alerte_panne:
                messages_service.append(
                    f"{libelle} : aucun recueil trouvé lors des {config.seuil_alerte_panne} derniers contrôles. "
                    f"Le site a peut-être changé : ouvrez la page des recueils dans votre navigateur et "
                    f"copiez son adresse dans les options avancées de la page de réglage.")
            continue
        ep["executions_sans_document"] = 0

        nouveaux = [d for d in res.documents.values() if d.url not in ep["vus"]]
        nouveaux_liens = [l for l in res.liens_supplementaires.values() if l.url not in ep["liens_vus"]]

        # Premier passage : on enregistre l'existant sans alerter
        if not ep["initialise"] or len(nouveaux) > SEUIL_REINITIALISATION:
            premier = not ep["initialise"]
            for d in res.documents.values():
                ep["vus"][d.url] = maintenant()
            for l in res.liens_supplementaires.values():
                ep["liens_vus"][l.url] = maintenant()
            ep["initialise"] = True
            if premier:
                messages_service.append(
                    f"{libelle} : surveillance activée. {len(res.documents)} recueil(s) déjà en ligne "
                    f"enregistré(s) comme point de départ ; vous serez alerté des suivants.")
            else:
                messages_service.append(
                    f"{libelle} : {len(nouveaux)} documents nouveaux d'un coup (le site a sans doute été "
                    f"réorganisé). Ils ont été enregistrés sans analyse pour éviter un flot d'alertes.")
            journal(f"   point de départ enregistré ({len(res.documents)} documents)")
            continue

        # Liens des pages complémentaires : leur titre est comparé aux mots-clés
        for l in nouveaux_liens:
            ep["liens_vus"][l.url] = maintenant()
            extraits = chercher(l.titre, config.mots_cles)
            for e in extraits:
                e.page = None
            if extraits:
                alertes.append(Alerte(nom, libelle, l.titre, l.url, l.page_source, extraits))

        # Nouveaux PDF : téléchargement et lecture
        a_traiter = nouveaux[: config.pdf_max_par_execution]
        if len(nouveaux) > len(a_traiter):
            journal(f"   {len(nouveaux) - len(a_traiter)} document(s) reporté(s) au prochain contrôle")
        for d in a_traiter:
            journal(f"   nouveau document : {d.titre[:90]}")
            donnees = nav.telecharger(d.url, config.taille_max_pdf)
            if donnees is None:
                ep["tentatives"][d.url] = ep["tentatives"].get(d.url, 0) + 1
                if ep["tentatives"][d.url] >= 3:
                    ep["vus"][d.url] = maintenant()
                    ep["tentatives"].pop(d.url, None)
                    if config.alerter_si_illisible:
                        alertes.append(Alerte(nom, libelle, d.titre, d.url, d.page_source, [],
                                              "Document impossible à télécharger après 3 essais : à ouvrir à la main."))
                continue
            analyse = analyser_pdf(donnees, config.mots_cles)
            extraits_titre = chercher(d.titre, config.mots_cles)
            for e in extraits_titre:
                e.page = None
            ep["vus"][d.url] = maintenant()
            ep["tentatives"].pop(d.url, None)
            tous = extraits_titre + analyse.extraits
            if tous:
                journal(f"   → {len(tous)} passage(s) correspondant aux mots-clés")
                remarque = None
                if analyse.pages_scannees and analyse.pages_scannees != [analyse.pages]:
                    remarque = (f"Pages scannées non lues : {plages(analyse.pages_scannees)}. "
                                f"D'autres passages peuvent s'y trouver.")
                alertes.append(Alerte(nom, libelle, d.titre, d.url, d.page_source, tous, remarque))
            elif (not analyse.lisible or analyse.erreur) and config.alerter_si_illisible:
                journal(f"   → pages scannées non lues : {plages(analyse.pages_scannees)}, signalé")
                alertes.append(Alerte(nom, libelle, d.titre, d.url, d.page_source, [],
                                      analyse.erreur or remarque_scan(analyse)))

    envois: dict[str, str] = {}
    if alertes:
        journal(f"{len(alertes)} alerte(s) : envoi")
        envois = notificateur.envoyer(rediger_alertes(alertes), priorite_ntfy=5)
        for a in alertes:
            page = premiere_page(a)
            etat["historique"].insert(0, {
                "date": maintenant(), "prefecture": a.nom_prefecture, "titre": a.titre,
                "lien": lien_page(a.url, page), "page": page, "remarque": a.remarque,
                "mots": list(dict.fromkeys(e.mot_cle for e in a.extraits)),
            })
        del etat["historique"][100:]
    else:
        journal("Rien de nouveau correspondant aux mots-clés.")

    if messages_service:
        notificateur.envoyer(message_service("\n\n".join(messages_service)), priorite_ntfy=2)

    resume = {"date": maintenant(), "alertes": len(alertes), "envois": envois, "informations": messages_service}
    if essai:
        journal("MODE ESSAI : rien n'est mémorisé.")
    else:
        etat["derniere_execution"] = maintenant()
        etat["dernier_resultat"] = resume
        enregistrer_etat(etat)
    return resume


def controle_unique(essai: bool = False) -> int:
    """Ce que lance le planificateur : un contrôle protégé par le verrou."""
    with Verrou() as verrou:
        if not verrou.pris:
            journal("Un contrôle est déjà en cours : celui-ci est ignoré.")
            return 0
        try:
            config = charger_config()
        except ErreurConfig as erreur:
            journal(f"Réglages incomplets : {erreur}")
            return 2
        notificateur = Notificateur(lire_secrets(), essai)
        journal(f"Contrôle : {len(config.prefectures)} préfecture(s), {len(config.mots_cles)} mot(s)-clé(s), "
                f"alertes : {notificateur.resume_canaux()}")
        try:
            verifier(config, notificateur, essai)
        except Exception as erreur:
            journal(f"Erreur inattendue : {erreur!r}")
            return 1
    return 0


def message_test(config: Config | None) -> Message:
    if config:
        details = (f"Préfectures suivies : {', '.join(nom_affiche(config, p) for p in config.prefectures)}\n"
                   f"Mots-clés : {', '.join(m.texte for m in config.mots_cles)}")
    else:
        details = "Les préfectures et mots-clés restent à choisir."
    texte = "Si vous lisez ce message, les alertes de la veille RAA vous parviennent.\n" + details
    return Message("[Veille RAA] Message de test", texte, court=texte,
                   whatsapp="*Veille RAA* : message de test\n\n" + texte)


# --------------------------------------------------------------------------
#  Commandes
# --------------------------------------------------------------------------

def commande_analyser(config: Config, cible: str) -> None:
    if re.match(r"https?://", cible):
        donnees = Navigateur(0, False).telecharger(cible, config.taille_max_pdf)
        if donnees is None:
            raise ErreurConfig("Téléchargement impossible.")
    else:
        donnees = Path(cible).read_bytes()
    analyse = analyser_pdf(donnees, config.mots_cles)
    afficher(f"{analyse.pages} page(s), texte {'lisible' if analyse.lisible else 'NON lisible (scan ?)'}")
    if analyse.erreur:
        afficher(analyse.erreur)
    if not analyse.extraits:
        afficher("Aucun mot-clé trouvé.")
    for e in analyse.extraits:
        afficher(f"- [{e.mot_cle}] page {e.page} : {e.texte}")
        if e.acte:
            afficher(f"    acte : {e.acte}")


def main() -> None:
    # Lancé comme programme, ce fichier s'appelle « __main__ » : on le déclare aussi sous son
    # vrai nom pour que la page de réglage et le planificateur partagent les mêmes réglages.
    sys.modules.setdefault("veille_raa", sys.modules[__name__])
    parseur = argparse.ArgumentParser(description="Veille des recueils des actes administratifs des préfectures")
    parseur.add_argument("--controle", action="store_true", help="un contrôle, puis arrêt")
    parseur.add_argument("--boucle", type=float, metavar="MINUTES", help="recommencer toutes les N minutes")
    parseur.add_argument("--essai", action="store_true", help="contrôle sans alerte ni mémorisation")
    parseur.add_argument("--test-notification", action="store_true", help="envoyer un message de test")
    parseur.add_argument("--analyser", metavar="PDF", help="tester les mots-clés sur un PDF")
    parseur.add_argument("--lister-prefectures", action="store_true", help="afficher les préfectures connues")
    parseur.add_argument("--reglages", action="store_true", help="ouvrir la page de réglage (par défaut)")
    parseur.add_argument("--dossier", metavar="CHEMIN", help="dossier des réglages et de la mémoire")
    parseur.add_argument("--sans-navigateur", action="store_true", help=argparse.SUPPRESS)
    args = parseur.parse_args()

    if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if args.dossier:
        definir_dossier(Path(args.dossier).expanduser().resolve())

    try:
        if args.lister_prefectures:
            for cle, fiche in sorted(catalogue_prefectures().items()):
                afficher(f"{cle:28} {fiche.get('nom', '')}")
            return

        if args.controle or args.essai and not args.boucle:
            sys.exit(controle_unique(args.essai))

        if args.analyser:
            commande_analyser(charger_config(), args.analyser)
            return

        if args.test_notification:
            notificateur = Notificateur(lire_secrets())
            try:
                config = charger_config()
            except ErreurConfig:
                config = None
            journal(f"Envoi d'un message de test : {notificateur.resume_canaux()}")
            resultats = notificateur.envoyer(message_test(config), priorite_ntfy=3)
            sys.exit(0 if all(v == "ok" for v in resultats.values()) else 1)

        if args.boucle:
            intervalle = max(5.0, args.boucle) * 60
            journal(f"Mode continu : un contrôle toutes les {intervalle / 60:.0f} minutes.")
            while True:
                debut = time.monotonic()
                controle_unique(args.essai)
                attente = intervalle - (time.monotonic() - debut)
                if attente > 0:
                    time.sleep(attente)

        import interface
        interface.lancer(ouvrir_navigateur=not args.sans_navigateur)
    except ErreurConfig as erreur:
        afficher(str(erreur))
        sys.exit(2)


if __name__ == "__main__":
    main()
