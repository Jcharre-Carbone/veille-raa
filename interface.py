"""
Page de réglage de Veille RAA.

Un petit serveur web tourne sur l'ordinateur lui-même (127.0.0.1, invisible
depuis le réseau) et ouvre la page dans le navigateur. Il s'arrête tout seul
quelques minutes après la fermeture de l'onglet.
"""

from __future__ import annotations

import json
import secrets as aleatoire
import socket
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import planificateur
import veille_raa as moteur

PORT_PREFERE = 8737
FREQUENCES = (5, 10, 15, 30, 60)

JETON = aleatoire.token_urlsafe(24)
_controle = threading.Event()        # un contrôle lancé depuis la page est en cours
_dernier_signe = [time.monotonic()]  # dernier signe de vie de l'onglet
_onglet_vu = [False]


# --------------------------------------------------------------------------
#  Données échangées avec la page
# --------------------------------------------------------------------------

def _catalogue() -> list[dict]:
    return sorted(({"cle": cle, "nom": fiche.get("nom", cle)} for cle, fiche in moteur.catalogue_prefectures().items()),
                  key=lambda p: moteur.normaliser(p["nom"]))


def _secrets_pour_page() -> dict:
    s = moteur.lire_secrets()
    page = {k: v for k, v in s.items() if k != "smtp_mot_de_passe"}
    page["mot_de_passe_enregistre"] = bool(s.get("smtp_mot_de_passe"))
    return page


def _resume_etat() -> dict:
    etat = moteur.charger_etat()
    prefectures = {
        cle: {k: ep.get(k) for k in ("dernier_controle", "nb_documents", "probleme", "initialise")}
        for cle, ep in etat.get("prefectures", {}).items()
    }
    return {
        "derniere_execution": etat.get("derniere_execution"),
        "dernier_resultat": etat.get("dernier_resultat"),
        "historique": etat.get("historique", [])[:15],
        "prefectures": prefectures,
        "controle_en_cours": _controle.is_set() or moteur.FICHIER_VERROU.exists(),
        "planification": planificateur.est_active(),
        "journal": moteur.lire_journal(60),
    }


def _donnees() -> dict:
    return {
        "version": moteur.VERSION,
        "config": moteur.lire_config_brute(),
        "secrets": _secrets_pour_page(),
        "catalogue": _catalogue(),
        "etat": _resume_etat(),
        "dossier": str(moteur.DOSSIER),
    }


def _enregistrer(corps: dict) -> dict:
    recu = corps.get("config") or {}
    actuel = moteur.lire_config_brute()
    catalogue = moteur.catalogue_prefectures()

    avance = dict(actuel.get("avance") or {})
    avance_recu = recu.get("avance") or {}
    for cle in ("respecter_robots_txt", "alerter_si_pdf_illisible"):
        if cle in avance_recu:
            avance[cle] = bool(avance_recu[cle])
    for cle in ("adresses_personnalisees", "pages_supplementaires"):
        if cle in avance_recu:
            propre = {}
            for pref, adresses in (avance_recu[cle] or {}).items():
                liste = [a.strip() for a in (adresses or []) if isinstance(a, str) and a.strip()]
                if any(not a.startswith(("http://", "https://")) for a in liste):
                    raise ValueError("Les adresses de pages doivent commencer par https://")
                if liste:
                    propre[moteur.cle_prefecture(pref)] = liste
            avance[cle] = propre

    prefectures = [moteur.cle_prefecture(p) for p in (recu.get("prefectures") or []) if str(p).strip()]
    inconnues = [p for p in prefectures if p not in catalogue and p not in avance.get("adresses_personnalisees", {})]
    if inconnues:
        raise ValueError("Préfecture inconnue : " + ", ".join(inconnues))
    mots = list(dict.fromkeys(str(m).strip() for m in (recu.get("mots_cles") or []) if str(m).strip()))
    frequence = int(recu.get("frequence_minutes") or actuel.get("frequence_minutes") or 10)
    if frequence not in FREQUENCES:
        frequence = 10

    nouvelle = {"prefectures": list(dict.fromkeys(prefectures)), "mots_cles": mots,
                "frequence_minutes": frequence, "avance": avance}
    for mot in mots:  # vérifie que chaque mot-clé se compile
        try:
            moteur.compiler_mot_cle(mot)
        except Exception:
            raise ValueError(f"Mot-clé illisible : {mot}")
    moteur.ecrire_config_brute(nouvelle)

    s_recu = corps.get("secrets") or {}
    anciens = moteur.lire_secrets()
    champs = ("whatsapp_numero", "whatsapp_cle", "mail_destinataires", "messagerie", "smtp_utilisateur",
              "smtp_hote", "smtp_port", "ntfy_sujet")
    nouveaux = {k: str(s_recu.get(k) or "").strip() for k in champs}
    if nouveaux["whatsapp_numero"]:
        nouveaux["whatsapp_numero"] = moteur.normaliser_telephone(nouveaux["whatsapp_numero"])
    if nouveaux["messagerie"] != "autre":
        nouveaux["smtp_hote"] = ""
        nouveaux["smtp_port"] = ""
    mot_de_passe = s_recu.get("smtp_mot_de_passe") or ""
    if mot_de_passe:
        nouveaux["smtp_mot_de_passe"] = mot_de_passe
    elif not s_recu.get("effacer_mot_de_passe"):
        nouveaux["smtp_mot_de_passe"] = anciens.get("smtp_mot_de_passe", "")
    if not nouveaux["mail_destinataires"]:  # pas de mail : on n'en garde rien
        for k in ("messagerie", "smtp_utilisateur", "smtp_hote", "smtp_port", "smtp_mot_de_passe"):
            nouveaux[k] = ""
    for k, v in anciens.items():  # réglages ajoutés à la main (ntfy_serveur, etc.)
        if k not in nouveaux and k not in champs and k != "smtp_mot_de_passe":
            nouveaux[k] = v
    moteur.ecrire_secrets(nouveaux)

    # Si la veille est déjà active, la fréquence choisie est appliquée tout de suite
    if planificateur.est_active() and frequence != actuel.get("frequence_minutes"):
        planificateur.activer(frequence)
    return {"ok": True, "message": "Réglages enregistrés."}


def _lancer_controle() -> bool:
    if _controle.is_set():
        return False
    _controle.set()

    def tache():
        try:
            moteur.controle_unique()
        finally:
            _controle.clear()

    threading.Thread(target=tache, daemon=True).start()
    return True


def _activer() -> dict:
    config = moteur.charger_config()  # ErreurConfig si incomplet
    if not moteur.Notificateur(moteur.lire_secrets()).canaux():
        raise moteur.ErreurConfig("Indiquez au moins un moyen d'alerte (WhatsApp ou mail) à l'étape 3.")
    message = planificateur.activer(config.frequence)
    _lancer_controle()
    return {"ok": True, "message": message + " Premier contrôle en cours."}


def _tester() -> dict:
    notificateur = moteur.Notificateur(moteur.lire_secrets())
    if not notificateur.canaux():
        raise moteur.ErreurConfig("Aucun moyen d'alerte n'est enregistré : remplissez l'étape 3 puis enregistrez.")
    try:
        config = moteur.charger_config()
    except moteur.ErreurConfig:
        config = None
    return {"ok": True, "resultats": notificateur.envoyer(moteur.message_test(config), priorite_ntfy=3)}


# --------------------------------------------------------------------------
#  Serveur
# --------------------------------------------------------------------------

class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "VeilleRAA"

    def log_message(self, *args) -> None:  # pas de bruit dans la console
        pass

    def _hote_valide(self) -> bool:
        # Protection contre les sites qui tenteraient de parler à ce serveur local
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _repondre(self, code: int, corps, type_contenu: str = "application/json; charset=utf-8") -> None:
        donnees = corps if isinstance(corps, bytes) else json.dumps(corps, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", type_contenu)
        self.send_header("Content-Length", str(len(donnees)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if type_contenu.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(donnees)

    def do_GET(self) -> None:
        if not self._hote_valide():
            return self._repondre(403, {"erreur": "Accès refusé."})
        url = urlparse(self.path)
        if url.path == "/favicon.ico":
            return self._repondre(200, ICONE, "image/svg+xml")
        if url.path == "/":
            if parse_qs(url.query).get("t", [""])[0] != JETON:
                return self._repondre(403, "<p>Lien expiré : relancez Veille RAA.</p>".encode(), "text/html; charset=utf-8")
            return self._repondre(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if self.headers.get("X-Jeton") != JETON:
            return self._repondre(403, {"erreur": "Accès refusé."})
        if url.path == "/api/donnees":
            return self._repondre(200, _donnees())
        if url.path == "/api/etat":
            return self._repondre(200, _resume_etat())
        if url.path == "/api/ping":
            return self._repondre(200, {"appli": "veille-raa"})
        self._repondre(404, {"erreur": "Introuvable."})

    def do_POST(self) -> None:
        if not self._hote_valide() or self.headers.get("X-Jeton") != JETON:
            return self._repondre(403, {"erreur": "Accès refusé."})
        try:
            longueur = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            corps = json.loads(self.rfile.read(longueur) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._repondre(400, {"erreur": "Requête illisible."})
        chemin = urlparse(self.path).path
        try:
            if chemin == "/api/ping":
                _dernier_signe[0] = time.monotonic()
                _onglet_vu[0] = True
                return self._repondre(200, {"ok": True})
            if chemin == "/api/enregistrer":
                return self._repondre(200, _enregistrer(corps))
            if chemin == "/api/activer":
                return self._repondre(200, _activer())
            if chemin == "/api/desactiver":
                planificateur.desactiver()
                return self._repondre(200, {"ok": True, "message": "Veille arrêtée. Vos réglages sont conservés."})
            if chemin == "/api/tester":
                return self._repondre(200, _tester())
            if chemin == "/api/controler":
                moteur.charger_config()
                lance = _lancer_controle()
                return self._repondre(200, {"ok": True, "message": "Contrôle lancé." if lance else "Un contrôle est déjà en cours."})
            if chemin == "/api/quitter":
                self._repondre(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
        except (ValueError, moteur.ErreurConfig, planificateur.ErreurPlanification) as erreur:
            return self._repondre(400, {"erreur": str(erreur)})
        except Exception as erreur:
            moteur.journal(f"Erreur de la page de réglage : {erreur!r}")
            return self._repondre(500, {"erreur": f"Erreur inattendue : {erreur}"})
        self._repondre(404, {"erreur": "Introuvable."})


def _sans_proxy():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _instance_existante() -> str | None:
    """Si la page de réglage tourne déjà, renvoie son adresse."""
    fichier = moteur.DOSSIER / "serveur.json"
    try:
        info = json.loads(fichier.read_text(encoding="utf-8"))
        requete = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/ping",
                                         headers={"X-Jeton": info["jeton"]})
        with _sans_proxy().open(requete, timeout=2) as r:
            if json.loads(r.read()).get("appli") == "veille-raa":
                return f"http://127.0.0.1:{info['port']}/?t={info['jeton']}"
    except Exception:
        return None
    return None


def _port_libre() -> int:
    for port in (PORT_PREFERE, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise OSError("aucun port disponible")


def _surveiller(serveur: ThreadingHTTPServer) -> None:
    """Arrête le serveur quand l'onglet est fermé depuis 2 minutes (10 minutes s'il n'a jamais été ouvert)."""
    while True:
        time.sleep(15)
        silence = time.monotonic() - _dernier_signe[0]
        limite = 120 if _onglet_vu[0] else 600
        if silence > limite and not _controle.is_set():
            serveur.shutdown()
            return


def lancer(ouvrir_navigateur: bool = True) -> None:
    existante = _instance_existante()
    if existante:
        if ouvrir_navigateur:
            webbrowser.open(existante)
        moteur.afficher(f"La page de réglage est déjà ouverte : {existante}")
        return
    port = _port_libre()
    serveur = ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)
    adresse = f"http://127.0.0.1:{port}/?t={JETON}"
    info = moteur.DOSSIER / "serveur.json"
    moteur.ecrire_fichier(info, json.dumps({"port": port, "jeton": JETON}), prive=True)
    threading.Thread(target=_surveiller, args=(serveur,), daemon=True).start()
    moteur.afficher("Veille RAA : la page de réglage s'ouvre dans votre navigateur.\n"
                    f"Si rien ne s'ouvre, copiez cette adresse dans le navigateur :\n{adresse}\n"
                    "Cette fenêtre peut rester ouverte ; la page se ferme seule après usage.")
    if ouvrir_navigateur:
        threading.Timer(0.6, webbrowser.open, args=(adresse,)).start()
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
        try:
            Path(info).unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
#  La page
# --------------------------------------------------------------------------

ICONE = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" '
         b'fill="#16202B"/><path d="M9 11h14M9 16h14M9 21h9" stroke="#fff" stroke-width="2.6" '
         b'stroke-linecap="round"/><circle cx="24" cy="22" r="4" fill="#C2410C"/></svg>')

PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veille RAA : réglages</title>
<link rel="icon" href="/favicon.ico" type="image/svg+xml">
<style>
:root{
  --encre:#16202B; --doux:#4A5563; --trait:#D5DAE1; --trait-fort:#B9C1CC; --fond:#F4F5F7; --carte:#FFFFFF;
  --bleu:#1F4E8C; --bleu-fonce:#123462; --vert:#1F7A4D; --orange:#C2410C; --orange-fonce:#9A3412;
  --orange-pale:#FFF7F0; --bleu-pale:#EEF2F7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--fond);color:var(--encre);font:15px/1.5 "Segoe UI",system-ui,-apple-system,"Helvetica Neue",sans-serif}
a{color:var(--bleu)} a:hover{color:var(--bleu-fonce)}
.page{max-width:1120px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:flex-end;border-bottom:2px solid var(--encre);padding-bottom:14px;margin-bottom:24px}
h1{margin:0;font-family:Georgia,"Times New Roman",serif;font-size:32px;font-weight:600}
.sous-titre{color:var(--doux);margin-top:2px}
.grille{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start}
.gauche{flex:999 1 560px;min-width:0;display:flex;flex-direction:column;gap:18px}
.droite{flex:1 1 320px;min-width:0;display:flex;flex-direction:column;gap:18px}
.carte{background:var(--carte);border:1px solid var(--trait);border-radius:10px;padding:20px}
.etape{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.numero{width:28px;height:28px;border-radius:999px;background:var(--encre);color:#fff;font-weight:600;font-size:14px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
h2{margin:0;font-size:19px}
h3{margin:0 0 8px;font-size:16px}
label{display:block;font-size:13.5px;color:var(--doux);margin:12px 0 4px}
input[type=text],input[type=tel],input[type=email],input[type=password],input[type=url],select,textarea{
  width:100%;font:inherit;padding:10px 12px;border:1px solid var(--trait-fort);border-radius:6px;background:#fff;color:var(--encre)}
textarea{min-height:64px;resize:vertical}
input:focus-visible,select:focus-visible,textarea:focus-visible,button:focus-visible,a:focus-visible{outline:2px solid var(--bleu);outline-offset:2px}
.ligne{display:flex;gap:8px;align-items:stretch}
.ligne>input{flex:1}
button{font:inherit;cursor:pointer;border-radius:6px;min-height:42px;padding:0 16px}
.btn-principal{background:var(--vert);color:#fff;border:0;font-weight:600;font-size:16px;min-height:50px;width:100%}
.btn-bleu{background:var(--bleu);color:#fff;border:0;font-weight:600}
.btn-secondaire{background:#fff;color:var(--encre);border:1px solid var(--trait-fort)}
.btn-discret{background:transparent;border:0;color:var(--doux);text-decoration:underline;min-height:36px;padding:0 4px}
button:disabled{opacity:.55;cursor:wait}
.puces{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.puce{display:inline-flex;align-items:center;gap:2px;background:var(--bleu-pale);border:1px solid #C9D3E0;border-radius:999px;padding:2px 4px 2px 12px;font-size:14px}
.puce.choisie{background:var(--encre);color:#fff;border-color:var(--encre);font-weight:600}
.puce button{min-height:30px;width:30px;padding:0;border:0;background:transparent;color:inherit;border-radius:999px;display:flex;align-items:center;justify-content:center}
.resultats{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.resultats button{background:#fff;border:1px solid var(--trait-fort);border-radius:999px;min-height:38px;padding:0 14px;font-size:14px}
.resultats button:hover{border-color:var(--encre)}
.aide{font-size:13px;color:var(--doux);margin-top:6px}
.vide{font-size:14px;color:var(--doux);font-style:italic}
.canal{border:1px solid var(--trait);border-radius:8px;padding:14px 16px;margin-top:12px}
.canal-titre{display:flex;justify-content:space-between;align-items:center;gap:10px}
details{margin-top:10px}
summary{cursor:pointer;color:var(--bleu);font-size:14px;font-weight:600}
.etapes-wa{margin:10px 0 0;padding-left:20px;font-size:14px}
.etapes-wa li{margin:6px 0}
code{font-family:Consolas,"SF Mono",monospace;background:var(--fond);padding:1px 6px;border-radius:4px;font-size:13.5px}
.avert{background:var(--orange-pale);border:1px solid #F3C9A8;border-radius:8px;padding:10px 12px;font-size:13.5px;margin-top:10px}
.etat-veille{display:flex;align-items:center;gap:10px;font-size:18px;font-weight:600}
.pastille{width:14px;height:14px;border-radius:999px;background:var(--trait-fort);flex-shrink:0}
.pastille.active{background:var(--vert)} .pastille.attention{background:var(--orange)}
.liste-prefs{margin:12px 0 0;padding:0;list-style:none;font-size:14px}
.liste-prefs li{padding:6px 0;border-top:1px solid var(--trait)}
.probleme{color:var(--orange-fonce)}
.actions{display:flex;flex-direction:column;gap:8px;margin-top:14px}
.retour{font-size:14px;margin-top:10px;min-height:1em}
.retour.erreur{color:var(--orange-fonce);font-weight:600}
.retour.ok{color:var(--vert);font-weight:600}
.alerte{border-top:1px solid var(--trait);padding:10px 0;font-size:14px}
.alerte .quand{color:var(--doux);font-size:12.5px}
pre{white-space:pre-wrap;word-break:break-word;font-size:12px;background:var(--fond);padding:10px;border-radius:6px;max-height:280px;overflow:auto}
.modifie{font-size:13px;color:var(--orange-fonce);text-align:center;margin-top:6px}
.cache{display:none}
.ligne-pref{border-top:1px solid var(--trait);padding-top:10px;margin-top:10px}
.case{display:flex;gap:10px;align-items:flex-start;margin-top:10px;font-size:14px;color:var(--encre)}
.case input{width:20px;height:20px;margin-top:1px;flex-shrink:0}
</style>
</head>
<body>
<div class="page">
<header>
  <div>
    <h1>Veille RAA</h1>
    <div class="sous-titre">Alertes sur les recueils des actes administratifs des préfectures</div>
  </div>
  <div class="sous-titre" id="version"></div>
</header>

<div class="grille">
<main class="gauche">

<section class="carte" aria-labelledby="t1">
  <div class="etape"><div class="numero">1</div><h2 id="t1">Quelles préfectures ?</h2></div>
  <label for="recherche">Rechercher un département (nom ou numéro)</label>
  <input id="recherche" type="text" autocomplete="off" placeholder="Hérault, 34, Gard...">
  <div class="resultats" id="resultats" aria-live="polite"></div>
  <div class="puces" id="prefs-choisies"></div>
</section>

<section class="carte" aria-labelledby="t2">
  <div class="etape"><div class="numero">2</div><h2 id="t2">Quels mots-clés ?</h2></div>
  <div class="aide">Majuscules et accents sont indifférents. L'étoile * remplace la fin d'un mot : <code>lycée*</code> trouve aussi lycées, lycéen, lycéennes.</div>
  <div class="puces" id="mots"></div>
  <label for="nouveau-mot">Ajouter un mot ou une expression</label>
  <div class="ligne">
    <input id="nouveau-mot" type="text" placeholder="ex. attroupement*">
    <button type="button" class="btn-bleu" id="ajouter-mot">Ajouter</button>
  </div>
  <div class="aide" id="suggestions"></div>
</section>

<section class="carte" aria-labelledby="t3">
  <div class="etape"><div class="numero">3</div><h2 id="t3">Où vous prévenir ?</h2></div>
  <div class="aide">Remplissez au moins un des deux moyens. Les deux ensemble, c'est encore mieux.</div>

  <div class="canal">
    <h3>Par WhatsApp</h3>
    <label for="wa-numero">Votre numéro de portable</label>
    <input id="wa-numero" type="tel" autocomplete="tel" placeholder="06 12 34 56 78">
    <label for="wa-cle">Clé d'activation WhatsApp</label>
    <input id="wa-cle" type="text" autocomplete="off" placeholder="ex. 1234567">
    <details id="wa-aide">
      <summary>Comment obtenir la clé (2 minutes, une seule fois)</summary>
      <ol class="etapes-wa">
        <li>Sur votre téléphone, ajoutez ce numéro à vos contacts : <code>+34 644 91 07 79</code> (nommez-le par exemple « Veille RAA »).</li>
        <li>Dans WhatsApp, envoyez-lui exactement ce message : <code>I allow callmebot to send me messages</code> <button type="button" class="btn-discret" id="copier-wa">copier</button></li>
        <li>Vous recevez en réponse « Your APIKEY is ... » : recopiez ce nombre ci-dessus.</li>
      </ol>
      <div class="avert">Ce service gratuit (CallMeBot) n'est pas édité par WhatsApp ; il est réservé à un usage personnel et n'envoie qu'à votre propre numéro. Les alertes ne contiennent que des informations publiques. S'il venait à cesser, les alertes par mail continueraient.</div>
    </details>
  </div>

  <div class="canal">
    <h3>Par mail</h3>
    <label for="mail-dest">Adresse qui reçoit les alertes</label>
    <input id="mail-dest" type="email" autocomplete="email" placeholder="prenom.nom@cabinet.fr">
    <div id="bloc-envoi">
      <label for="messagerie">Votre messagerie (pour envoyer les alertes depuis votre propre boîte)</label>
      <select id="messagerie">
        <option value="ovh">OVH (MX Plan, E-mail Pro)</option>
        <option value="gmail">Gmail</option>
        <option value="microsoft">Microsoft 365 / Outlook.com</option>
        <option value="autre">Autre</option>
      </select>
      <div class="aide" id="aide-messagerie"></div>
      <div id="bloc-autre" class="cache">
        <label for="smtp-hote">Serveur d'envoi (SMTP)</label>
        <input id="smtp-hote" type="text" placeholder="smtp.exemple.fr">
        <label for="smtp-port">Port</label>
        <input id="smtp-port" type="text" inputmode="numeric" placeholder="465 ou 587">
      </div>
      <label for="smtp-compte">Adresse de la boîte qui envoie (laisser vide si c'est la même)</label>
      <input id="smtp-compte" type="email" autocomplete="off">
      <label for="smtp-mdp">Mot de passe de cette boîte</label>
      <input id="smtp-mdp" type="password" autocomplete="new-password">
      <div class="aide">Le mot de passe reste sur cet ordinateur, dans votre dossier personnel. Une boîte dédiée (par exemple veille@cabinet.fr) est préférable.</div>
    </div>
  </div>
</section>

<section class="carte" aria-labelledby="t4">
  <details id="avance">
    <summary id="t4">Options avancées (facultatif)</summary>
    <label for="frequence">Fréquence des contrôles</label>
    <select id="frequence">
      <option value="5">Toutes les 5 minutes</option>
      <option value="10">Toutes les 10 minutes</option>
      <option value="15">Toutes les 15 minutes</option>
      <option value="30">Toutes les 30 minutes</option>
      <option value="60">Toutes les heures</option>
    </select>
    <label class="case"><input type="checkbox" id="illisible"> <span>Me prévenir aussi quand un nouveau recueil ne peut pas être lu (PDF scanné)</span></label>
    <label class="case"><input type="checkbox" id="robots"> <span>Respecter les consignes « robots.txt » des sites (recommandé)</span></label>
    <label for="ntfy">Notification ntfy (facultatif : nom du sujet)</label>
    <input id="ntfy" type="text" autocomplete="off" placeholder="veille-raa-dupont-x7k2p9q4">
    <div id="avance-prefs"></div>
  </details>
</section>

</main>

<aside class="droite">
<section class="carte" aria-labelledby="t-etat">
  <h2 id="t-etat" class="cache">État de la veille</h2>
  <div class="etat-veille"><span class="pastille" id="pastille"></span><span id="etat-texte">Chargement...</span></div>
  <div class="aide" id="etat-detail"></div>
  <ul class="liste-prefs" id="etat-prefs"></ul>
  <div class="actions">
    <button type="button" class="btn-principal" id="activer">Enregistrer et activer la veille</button>
    <div class="modifie cache" id="modifie">Modifications non enregistrées</div>
    <button type="button" class="btn-secondaire" id="tester">Envoyer un message de test</button>
    <button type="button" class="btn-secondaire" id="controler">Contrôler maintenant</button>
    <button type="button" class="btn-discret" id="arreter">Arrêter la veille</button>
  </div>
  <div class="retour" id="retour" role="status" aria-live="polite"></div>
</section>

<section class="carte" aria-labelledby="t-alertes">
  <h2 id="t-alertes" style="font-size:17px">Dernières alertes</h2>
  <div id="alertes"><div class="vide">Aucune alerte pour l'instant.</div></div>
</section>

<section class="carte">
  <details>
    <summary>Journal technique</summary>
    <pre id="journal"></pre>
    <div class="aide" id="dossier"></div>
  </details>
</section>
</aside>
</div>
</div>

<script>
"use strict";
const JETON = new URLSearchParams(location.search).get("t");
const SUGGESTIONS = ["interdiction de manifester", "périmètre de protection", "couvre-feu", "vidéoprotection", "attroupement*", "rassemblement festif"];
const AIDES_MESSAGERIE = {
  ovh: "Serveur ssl0.ovh.net, port 465 : rien d'autre à régler.",
  gmail: "Gmail exige un « mot de passe d'application » : compte Google, Sécurité, Validation en deux étapes, Mots de passe des applications.",
  microsoft: "L'envoi par logiciel doit être autorisé par l'administrateur de votre messagerie Microsoft 365.",
  autre: "Indiquez le serveur d'envoi donné par votre hébergeur de messagerie."
};
let etat = {catalogue: [], config: null, secrets: null, etat: null};
let choix = {prefectures: [], mots: [], avance: {adresses_personnalisees: {}, pages_supplementaires: {}}};
let modifie = false;

const $ = (id) => document.getElementById(id);
function el(balise, attributs, ...enfants) {
  const n = document.createElement(balise);
  for (const [k, v] of Object.entries(attributs || {})) {
    if (k === "onclick") n.addEventListener("click", v);
    else if (k === "texte") n.textContent = v;
    else n.setAttribute(k, v);
  }
  for (const e of enfants) n.append(e);
  return n;
}
function croix() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", "14"); s.setAttribute("height", "14"); s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true"); s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2.4"); s.setAttribute("stroke-linecap", "round");
  s.innerHTML = '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>';
  return s;
}
function sansAccents(t) { return (t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
function nomPref(cle) { const p = etat.catalogue.find(x => x.cle === cle); return p ? p.nom : cle; }
function marquerModifie() { modifie = true; $("modifie").classList.remove("cache"); }

async function api(chemin, corps) {
  const r = await fetch(chemin, {
    method: corps === undefined ? "GET" : "POST",
    headers: {"X-Jeton": JETON, "Content-Type": "application/json"},
    body: corps === undefined ? undefined : JSON.stringify(corps)
  });
  let d = {};
  try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(d.erreur || "La page de réglage ne répond plus : relancez Veille RAA.");
  return d;
}
function retour(texte, type) {
  const r = $("retour"); r.textContent = texte; r.className = "retour " + (type || "");
}

/* Étape 1 : préfectures */
function dessinerRecherche() {
  const q = sansAccents($("recherche").value.trim());
  const zone = $("resultats"); zone.replaceChildren();
  if (!q) return;
  const trouves = etat.catalogue.filter(p => !choix.prefectures.includes(p.cle) &&
    (sansAccents(p.nom).includes(q) || p.cle.includes(q.replace(/\s+/g, "-")))).slice(0, 8);
  if (!trouves.length) { zone.append(el("div", {class: "vide", texte: "Aucun département ne correspond."})); return; }
  for (const p of trouves) {
    zone.append(el("button", {type: "button", texte: "+ " + p.nom, onclick: () => {
      choix.prefectures.push(p.cle); $("recherche").value = ""; marquerModifie(); dessinerPrefs(); dessinerRecherche(); $("recherche").focus();
    }}));
  }
}
function dessinerPrefs() {
  const zone = $("prefs-choisies"); zone.replaceChildren();
  if (!choix.prefectures.length) zone.append(el("div", {class: "vide", texte: "Aucune préfecture choisie."}));
  for (const cle of choix.prefectures) {
    zone.append(el("span", {class: "puce choisie"}, nomPref(cle),
      el("button", {type: "button", "aria-label": "Retirer " + nomPref(cle), onclick: () => {
        choix.prefectures = choix.prefectures.filter(x => x !== cle); marquerModifie(); dessinerPrefs();
      }}, croix())));
  }
  dessinerAvancePrefs();
}

/* Étape 2 : mots-clés */
function ajouterMot(mot) {
  mot = (mot || "").trim();
  if (mot && !choix.mots.includes(mot)) { choix.mots.push(mot); marquerModifie(); }
  dessinerMots();
}
function dessinerMots() {
  const zone = $("mots"); zone.replaceChildren();
  if (!choix.mots.length) zone.append(el("div", {class: "vide", texte: "Aucun mot-clé."}));
  for (const m of choix.mots) {
    zone.append(el("span", {class: "puce"}, m,
      el("button", {type: "button", "aria-label": "Retirer " + m, onclick: () => {
        choix.mots = choix.mots.filter(x => x !== m); marquerModifie(); dessinerMots();
      }}, croix())));
  }
  const sug = $("suggestions"); sug.replaceChildren();
  const restantes = SUGGESTIONS.filter(s => !choix.mots.includes(s));
  if (restantes.length) {
    sug.append("Suggestions : ");
    restantes.forEach((s, i) => {
      sug.append(el("button", {type: "button", class: "btn-discret", texte: s, onclick: () => ajouterMot(s)}));
      if (i < restantes.length - 1) sug.append(" · ");
    });
  }
}

/* Options avancées par préfecture */
function dessinerAvancePrefs() {
  const zone = $("avance-prefs"); zone.replaceChildren();
  for (const cle of choix.prefectures) {
    const perso = (choix.avance.adresses_personnalisees[cle] || []).join("\n");
    const supp = (choix.avance.pages_supplementaires[cle] || []).join("\n");
    const idA = "perso-" + cle, idS = "supp-" + cle;
    const a = el("textarea", {id: idA, placeholder: "https://www.exemple.gouv.fr/Publications/Recueil-des-actes-administratifs"});
    a.value = perso;
    a.addEventListener("input", () => { choix.avance.adresses_personnalisees[cle] = lignes(a.value); marquerModifie(); });
    const s = el("textarea", {id: idS, placeholder: "https://www.exemple.gouv.fr/Actualites"});
    s.value = supp;
    s.addEventListener("input", () => { choix.avance.pages_supplementaires[cle] = lignes(s.value); marquerModifie(); });
    zone.append(el("div", {class: "ligne-pref"},
      el("h3", {texte: nomPref(cle)}),
      el("label", {for: idA, texte: "Adresse de la page des recueils, si elle n'est pas trouvée toute seule"}), a,
      el("label", {for: idS, texte: "Pages à surveiller en plus (actualités, communiqués), une par ligne"}), s));
  }
}
function lignes(t) { return t.split(/\n+/).map(x => x.trim()).filter(Boolean); }

/* Remplissage du formulaire */
function remplir(d) {
  etat = d;
  const c = d.config, s = d.secrets;
  choix.prefectures = [...c.prefectures];
  choix.mots = [...c.mots_cles];
  choix.avance.adresses_personnalisees = JSON.parse(JSON.stringify(c.avance.adresses_personnalisees || {}));
  choix.avance.pages_supplementaires = JSON.parse(JSON.stringify(c.avance.pages_supplementaires || {}));
  $("wa-numero").value = s.whatsapp_numero || "";
  $("wa-cle").value = s.whatsapp_cle || "";
  $("mail-dest").value = s.mail_destinataires || "";
  $("messagerie").value = s.messagerie || "ovh";
  $("smtp-hote").value = s.smtp_hote || "";
  $("smtp-port").value = s.smtp_port || "";
  $("smtp-compte").value = (s.smtp_utilisateur && s.smtp_utilisateur !== s.mail_destinataires) ? s.smtp_utilisateur : "";
  $("smtp-mdp").value = "";
  $("smtp-mdp").placeholder = s.mot_de_passe_enregistre ? "Enregistré (laisser vide pour le garder)" : "";
  $("ntfy").value = s.ntfy_sujet || "";
  $("frequence").value = String(c.frequence_minutes || 10);
  $("illisible").checked = c.avance.alerter_si_pdf_illisible !== false;
  $("robots").checked = c.avance.respecter_robots_txt !== false;
  $("version").textContent = "Version " + d.version;
  $("dossier").textContent = "Réglages et journal rangés dans : " + d.dossier;
  majMessagerie();
  dessinerPrefs(); dessinerMots(); dessinerEtat(d.etat);
  modifie = false; $("modifie").classList.add("cache");
}
function majMessagerie() {
  const m = $("messagerie").value;
  $("aide-messagerie").textContent = AIDES_MESSAGERIE[m] || "";
  $("bloc-autre").classList.toggle("cache", m !== "autre");
}
function collecter() {
  const dest = $("mail-dest").value.trim();
  return {
    config: {
      prefectures: choix.prefectures, mots_cles: choix.mots,
      frequence_minutes: Number($("frequence").value),
      avance: {
        alerter_si_pdf_illisible: $("illisible").checked, respecter_robots_txt: $("robots").checked,
        adresses_personnalisees: choix.avance.adresses_personnalisees, pages_supplementaires: choix.avance.pages_supplementaires
      }
    },
    secrets: {
      whatsapp_numero: $("wa-numero").value, whatsapp_cle: $("wa-cle").value,
      mail_destinataires: dest, messagerie: $("messagerie").value,
      smtp_utilisateur: $("smtp-compte").value.trim() || dest,
      smtp_hote: $("smtp-hote").value, smtp_port: $("smtp-port").value,
      smtp_mot_de_passe: $("smtp-mdp").value, ntfy_sujet: $("ntfy").value
    }
  };
}
function verifierSaisie(d) {
  if (!d.config.prefectures.length) return "Choisissez au moins une préfecture (étape 1).";
  if (!d.config.mots_cles.length) return "Indiquez au moins un mot-clé (étape 2).";
  const wa = d.secrets.whatsapp_numero.trim() || d.secrets.whatsapp_cle.trim();
  if (wa && !(d.secrets.whatsapp_numero.trim() && d.secrets.whatsapp_cle.trim()))
    return "Pour WhatsApp, il faut le numéro et la clé d'activation (étape 3).";
  const mail = d.secrets.mail_destinataires;
  if (mail && !d.secrets.smtp_mot_de_passe && !etat.secrets.mot_de_passe_enregistre)
    return "Pour le mail, indiquez le mot de passe de la boîte qui envoie (étape 3).";
  if (!wa && !mail && !d.secrets.ntfy_sujet.trim()) return "Indiquez au moins un moyen d'alerte : WhatsApp ou mail (étape 3).";
  return null;
}

/* État de la veille */
function depuis(iso) {
  if (!iso) return "jamais";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "à l'instant";
  if (min < 60) return "il y a " + min + " min";
  const h = Math.round(min / 60);
  if (h < 24) return "il y a " + h + " h";
  return "le " + new Date(iso).toLocaleDateString("fr-FR");
}
function dessinerEtat(e) {
  etat.etat = e;
  const actif = e.planification;
  const enCours = e.controle_en_cours;
  const problemes = choix.prefectures.filter(c => e.prefectures[c] && e.prefectures[c].probleme);
  $("pastille").className = "pastille " + (actif ? (problemes.length ? "attention" : "active") : "");
  $("etat-texte").textContent = enCours ? "Contrôle en cours..." : (actif ? "Veille active" : "Veille arrêtée");
  const freq = $("frequence").selectedOptions[0] ? $("frequence").selectedOptions[0].textContent.toLowerCase() : "";
  $("etat-detail").textContent = actif
    ? "Dernier contrôle : " + depuis(e.derniere_execution) + ". Prochains contrôles " + freq + ", tant que l'ordinateur est allumé."
    : "Une fois les trois étapes remplies, cliquez sur « Enregistrer et activer la veille ».";
  const liste = $("etat-prefs"); liste.replaceChildren();
  for (const cle of choix.prefectures) {
    const p = e.prefectures[cle];
    let texte = "pas encore contrôlée";
    if (p && p.probleme) texte = p.probleme;
    else if (p && p.dernier_controle) texte = (p.nb_documents || 0) + " recueil(s) en ligne, contrôlée " + depuis(p.dernier_controle);
    liste.append(el("li", {}, el("b", {texte: nomPref(cle)}), el("br"),
      el("span", {class: p && p.probleme ? "probleme" : "", texte: texte})));
  }
  $("arreter").classList.toggle("cache", !actif);
  $("activer").textContent = actif ? "Enregistrer les modifications" : "Enregistrer et activer la veille";
  const zone = $("alertes"); zone.replaceChildren();
  if (!e.historique.length) zone.append(el("div", {class: "vide", texte: "Aucune alerte pour l'instant."}));
  for (const a of e.historique) {
    const lien = el("a", {href: a.lien, target: "_blank", rel: "noopener noreferrer", texte: a.titre});
    const details = a.mots && a.mots.length ? a.mots.join(", ") : (a.remarque || "");
    zone.append(el("div", {class: "alerte"},
      el("div", {class: "quand", texte: depuis(a.date) + " · " + a.prefecture + (a.page ? " · page " + a.page : "")}),
      lien, el("div", {class: "aide", texte: details})));
  }
  $("journal").textContent = (e.journal || []).join("\n");
}
async function rafraichirEtat() {
  try { dessinerEtat(await api("/api/etat")); } catch (e) {}
}

/* Boutons */
async function avecBouton(bouton, action) {
  document.querySelectorAll("button").forEach(b => b.disabled = true);
  try { await action(); } catch (e) { retour(e.message, "erreur"); }
  finally { document.querySelectorAll("button").forEach(b => b.disabled = false); }
}
async function enregistrer() {
  const d = collecter();
  const pb = verifierSaisie(d);
  if (pb) throw new Error(pb);
  await api("/api/enregistrer", d);
  remplir(await api("/api/donnees"));
}
$("activer").addEventListener("click", () => avecBouton($("activer"), async () => {
  retour("Enregistrement...");
  const dejaActif = etat.etat && etat.etat.planification;
  await enregistrer();
  if (dejaActif) { retour("Modifications enregistrées. La veille continue.", "ok"); return; }
  const r = await api("/api/activer", {});
  retour(r.message, "ok");
  suivreControle();
}));
$("tester").addEventListener("click", () => avecBouton($("tester"), async () => {
  if (modifie) { retour("Enregistrement..."); await enregistrer(); }
  retour("Envoi du message de test...");
  const r = await api("/api/tester", {});
  const morceaux = Object.entries(r.resultats).map(([canal, res]) => canal + " : " + (res === "ok" ? "envoyé" : "échec (" + res + ")"));
  const tousOk = Object.values(r.resultats).every(v => v === "ok");
  retour(morceaux.join(" ; ") + (tousOk ? ". Vérifiez la réception." : ""), tousOk ? "ok" : "erreur");
}));
$("controler").addEventListener("click", () => avecBouton($("controler"), async () => {
  if (modifie) { retour("Enregistrement..."); await enregistrer(); }
  const r = await api("/api/controler", {});
  retour(r.message, "ok");
  suivreControle();
}));
$("arreter").addEventListener("click", () => avecBouton($("arreter"), async () => {
  const r = await api("/api/desactiver", {});
  retour(r.message, "ok");
  await rafraichirEtat();
}));
let suivi = null;
function suivreControle() {
  clearInterval(suivi);
  let tours = 0;
  suivi = setInterval(async () => {
    await rafraichirEtat();
    tours++;
    if ((etat.etat && !etat.etat.controle_en_cours && tours > 1) || tours > 300) {
      clearInterval(suivi); suivi = null;
      const res = etat.etat.dernier_resultat;
      if (res) retour("Contrôle terminé : " + (res.alertes ? res.alertes + " alerte(s) envoyée(s)." : "rien de nouveau.") +
        (res.informations && res.informations.length ? " " + res.informations.join(" ") : ""), "ok");
    }
  }, 2000);
}

/* Saisie */
$("recherche").addEventListener("input", dessinerRecherche);
$("recherche").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { const b = $("resultats").querySelector("button"); if (b) { e.preventDefault(); b.click(); } }
});
$("ajouter-mot").addEventListener("click", () => { ajouterMot($("nouveau-mot").value); $("nouveau-mot").value = ""; $("nouveau-mot").focus(); });
$("nouveau-mot").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("ajouter-mot").click(); } });
$("messagerie").addEventListener("change", majMessagerie);
document.querySelectorAll("main input, main select").forEach(n => {
  if (n.id !== "recherche" && n.id !== "nouveau-mot") n.addEventListener("input", marquerModifie);
});
$("copier-wa").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText("I allow callmebot to send me messages"); $("copier-wa").textContent = "copié"; } catch (e) {}
});
window.addEventListener("beforeunload", (e) => { if (modifie) { e.preventDefault(); e.returnValue = ""; } });

/* Signe de vie : le serveur local s'arrête quand l'onglet est fermé */
function signe() { api("/api/ping", {}).catch(() => {}); }
setInterval(signe, 20000); signe();
setInterval(() => { if (!suivi) rafraichirEtat(); }, 30000);

api("/api/donnees").then(remplir).catch(e => retour(e.message, "erreur"));
</script>
</body>
</html>
"""
