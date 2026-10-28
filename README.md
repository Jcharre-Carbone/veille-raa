# Veille RAA

**Être prévenu, sur WhatsApp ou par mail, dans les minutes qui suivent la publication d'un arrêté préfectoral qui vous intéresse.**

Veille RAA surveille les recueils des actes administratifs (RAA) des préfectures que vous choisissez. Dès qu'un nouveau recueil contient l'un de vos mots-clés (« drone », « manifestation », « lycée »...), vous recevez un message avec la préfecture, la page concernée, le numéro de l'acte, l'extrait où figure le mot et un lien qui **ouvre le PDF directement à la bonne page**.

L'outil a été pensé pour les avocats qui contestent en urgence des arrêtés préfectoraux (référé-liberté notamment). Il est gratuit, tourne sur votre propre ordinateur et son code est public.

---

## Installation (5 minutes, aucune connaissance informatique requise)

### 1. Télécharger l'application

Sur la page **[Releases](../../releases/latest)** de ce projet, téléchargez :

- **Windows** : `VeilleRAA-Windows.exe`
- **Mac** : `VeilleRAA-Mac.zip` (double-cliquez dessus pour obtenir `VeilleRAA`, puis faites-le glisser dans le dossier **Applications**)

- **Linux** : `VeilleRAA-Linux`. Rendez-le exécutable puis lancez-le, par exemple dans un terminal ouvert dans le dossier de téléchargement : `chmod +x VeilleRAA-Linux && ./VeilleRAA-Linux`. Une entrée « Veille RAA » est ensuite ajoutée au menu des applications. Le contrôle automatique passe par `cron` ou, à défaut, par un minuteur systemd de l'utilisateur. L'application est compatible avec les distributions récentes (Ubuntu 22.04 ou plus récent, Debian 12, Fedora récente).

### 2. L'ouvrir une première fois

Double-cliquez sur l'application. L'ordinateur va probablement se méfier, car elle ne vient pas d'un grand éditeur :

- **Windows** affiche « Windows a protégé votre ordinateur » : cliquez sur **Informations complémentaires**, puis **Exécuter quand même**.
- **Mac** indique qu'il ne peut pas vérifier le développeur : ouvrez **Réglages Système**, puis **Confidentialité et sécurité**, descendez jusqu'au message concernant VeilleRAA et cliquez sur **Ouvrir quand même**.

Il n'est pas nécessaire de refaire cette manipulation ensuite.

### 3. Répondre aux trois questions

La page de réglage s'ouvre dans votre navigateur :

1. **Quelles préfectures ?** Tapez le nom ou le numéro du département et cliquez dessus.
2. **Quels mots-clés ?** Une liste de départ est proposée : ajoutez ou retirez ce que vous voulez.
3. **Où vous prévenir ?** WhatsApp, mail, ou les deux (voir ci-dessous).

Cliquez sur **Envoyer un message de test** pour vérifier que vous le recevez, puis sur **Enregistrer et activer la veille**.

**C'est terminé.** Vous pouvez fermer la page. Tant que l'ordinateur est allumé, il contrôle les recueils toutes les 10 minutes, sans fenêtre et sans rien vous demander. Au premier contrôle, les recueils déjà en ligne sont enregistrés comme point de départ : vous ne serez alerté que des publications suivantes.

Pour revenir aux réglages plus tard : rouvrez l'application (sur Windows, un raccourci « Veille RAA » est ajouté au menu Démarrer et au Bureau).

---

## Recevoir les alertes

### Sur WhatsApp

L'envoi passe par **CallMeBot**, un service gratuit qui permet d'envoyer des messages à **son propre numéro**. Il faut l'autoriser une fois :

1. Ajoutez à vos contacts le numéro **+34 644 91 07 79**.
2. Envoyez-lui sur WhatsApp le message : `I allow callmebot to send me messages`
3. Vous recevez en réponse une clé (« Your APIKEY is ... ») : recopiez-la dans la page de réglage avec votre numéro de portable.

À savoir : CallMeBot n'est pas édité par WhatsApp. Selon ses conditions, l'offre gratuite est réservée à un usage personnel ([page du service](https://www.callmebot.com/blog/free-api-whatsapp-messages/)). Les alertes ne contiennent que des informations publiques (titre du recueil, mots trouvés, lien). Si le service venait à cesser, les alertes par mail continueraient : c'est pourquoi il est conseillé de régler aussi le mail.

### Par mail

Indiquez l'adresse qui reçoit les alertes, choisissez votre messagerie et donnez le mot de passe de la boîte qui envoie (les alertes partent de votre propre boîte, vers vous-même).

| Messagerie | Remarque |
|---|---|
| OVH (MX Plan, E-mail Pro) | Rien de plus à régler |
| Gmail | Il faut un « mot de passe d'application » (compte Google, Sécurité, Validation en deux étapes, Mots de passe des applications) |
| Microsoft 365 / Outlook | L'envoi par logiciel doit être autorisé par l'administrateur de la messagerie |
| Autre | Indiquez le serveur d'envoi (SMTP) et le port donnés par votre hébergeur |

Le mot de passe est conservé sur votre ordinateur, dans votre dossier personnel, et nulle part ailleurs. Le plus prudent est de créer une boîte dédiée (par exemple `veille@votre-cabinet.fr`).

---

## Ce que l'outil fait et ne fait pas

**Il fait**

- un contrôle toutes les 10 minutes (réglable de 5 minutes à 1 heure) de la rubrique « Recueil des actes administratifs » de chaque préfecture choisie ;
- la lecture de chaque nouveau PDF et la recherche de vos mots-clés, accents et majuscules compris ;
- un signalement des recueils qu'il ne parvient pas à lire (PDF scannés), pour que vous les ouvriez vous-même ;
- sur demande, la surveillance d'autres pages de la préfecture (actualités, communiqués), dans les options avancées.

**Il ne fait pas**

- **Il ne fonctionne que si l'ordinateur est allumé** (et non en veille). Un ordinateur de bureau qui reste allumé au cabinet est l'idéal. Si l'ordinateur était éteint, un contrôle a lieu dès son redémarrage.
- Il ne remplace pas votre vigilance : un site qui change d'organisation, un PDF mal construit ou un mot-clé mal choisi peuvent faire manquer un acte. Une alerte reste un signal : vérifiez toujours l'acte lui-même.
- Il ne voit pas ce qui n'est pas publié au RAA. Pour les caméras aéroportées, l'article L. 242-5 du code de la sécurité intérieure permet, en cas d'urgence, une autorisation délivrée « par tout moyen » qui fait immédiatement l'objet d'une publicité par tout moyen, avant d'être formalisée ([Légifrance](https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000054724568)). Ajoutez la page des actualités de la préfecture dans les options avancées et suivez ses réseaux sociaux.

Pour mémoire, le juge des référés du Conseil d'État a rappelé que ces autorisations doivent être publiées, sauf urgence impérieuse, dans un délai permettant un accès utile au juge (CE, réf., 24 mai 2023, n° 473547, [présentation sur le site du Conseil d'État](https://conseil-etat.fr/site/actualites/emploi-de-drones-a-des-fins-de-maintien-de-l-ordre-le-conseil-d-etat-rejette-la-demande-de-suspension-du-decret-du-gouvernement)).

---

## Questions fréquentes

**Une préfecture affiche « aucun recueil trouvé sur le site ».**
La page des recueils n'a pas été trouvée toute seule. Ouvrez le site de la préfecture dans votre navigateur, allez dans la rubrique des recueils des actes administratifs, copiez l'adresse affichée en haut, et collez-la dans les **options avancées** de la page de réglage, sous le nom de la préfecture.

**« accès refusé par le fichier robots.txt du site ».**
Les sites peuvent indiquer, dans un fichier `robots.txt`, qu'ils ne souhaitent pas être consultés par des programmes. Par défaut, l'outil respecte ces consignes. La case correspondante, dans les options avancées, permet de passer outre : c'est un choix qui vous appartient. Dans tous les cas, l'outil reste discret (une page toutes les 1,5 seconde, au plus 40 pages par contrôle).

**Je ne reçois rien.**
Rouvrez l'application et cliquez sur **Envoyer un message de test** : le résultat s'affiche pour chaque moyen d'alerte (« envoyé » ou la raison de l'échec). Le **Journal technique**, en bas à droite de la page, détaille chaque contrôle. Pensez aux courriers indésirables.

**Arrêter la veille.**
Rouvrez l'application et cliquez sur **Arrêter la veille**. Vos réglages sont conservés.

**L'antivirus bloque l'application.**
Les applications fabriquées avec l'outil utilisé ici (PyInstaller) sont parfois signalées à tort. Vous pouvez autoriser le fichier dans votre antivirus, ou utiliser la version « code source » ci-dessous.

**Où sont rangés mes réglages ?**
Windows : `%APPDATA%\VeilleRAA` ; Mac : `~/Library/Application Support/VeilleRAA`. Le chemin exact est rappelé dans le Journal technique.

---

## Pour les informaticiens

**Lancer depuis le code source** (Python 3.10 ou plus) : `lancer-windows.bat` ou `lancer-mac-linux.sh` installent les composants dans un environnement local et ouvrent la page de réglage. Sous Linux, le contrôle automatique passe par la crontab de l'utilisateur.

**Commandes utiles**

```sh
python veille_raa.py                       # page de réglage
python veille_raa.py --controle            # un contrôle (ce que lance le planificateur)
python veille_raa.py --essai               # un contrôle sans envoi ni mémorisation
python veille_raa.py --test-notification   # message de test
python veille_raa.py --analyser recueil.pdf   # tester les mots-clés sur un PDF
python veille_raa.py --dossier /chemin     # autre dossier de réglages
```

Les variables d'environnement `WHATSAPP_NUMERO`, `WHATSAPP_CLE`, `MAIL_DESTINATAIRES`, `SMTP_HOTE`, `SMTP_PORT`, `SMTP_UTILISATEUR`, `SMTP_MOT_DE_PASSE`, `NTFY_SUJET` et `VEILLE_RAA_DOSSIER` remplacent les réglages enregistrés (utile sur un serveur).

**Fonctionnement** : `veille_raa.py` contient le moteur (exploration des sites, lecture des PDF, alertes), `interface.py` la page de réglage (serveur web local sur 127.0.0.1, protégé par un jeton aléatoire, arrêté automatiquement après fermeture de l'onglet) et `planificateur.py` l'inscription au Planificateur de tâches Windows, à launchd sur Mac ou à cron sous Linux. `prefectures.yml` liste les sites des préfectures.

**Fabriquer l'application Windows sur son propre PC** (Python requis pour la fabrication seulement) : double-cliquez sur `fabriquer-windows.bat`. Le fichier `VeilleRAA-Windows.exe` apparaît dans le dossier : c'est le seul fichier à transmettre.

**Publier une nouvelle version** : onglet **Releases**, **Draft a new release**, étiquette `vX.Y.Z`, **Publish release**. Le workflow `.github/workflows/publier.yml` fabrique alors `VeilleRAA-Windows.exe` et `VeilleRAA-Mac.zip` et les joint à la version.

---

## Crédits et licence

- Cet outil s'inspire du travail de La Quadrature du Net, dont le moteur [Attrap](https://www.laquadrature.net/2024/12/19/contre-lopacite-de-la-surveillance-locale-attrap-tes-arretes-prefectoraux/) recense les arrêtés préfectoraux de toute la France.
- Code distribué sous licence MIT (fichier `LICENSE`).
- L'outil est fourni sans garantie. Il ne constitue ni un conseil juridique ni un service de veille exhaustif.
