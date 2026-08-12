import os
import time
import ssl
import urllib.request
import feedparser
import schedule
import re
from google import genai

# Récupération de la clé API depuis la variable d'environnement (Render / Système)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("⚠️ ATTENTION : La variable d'environnement GEMINI_API_KEY est introuvable !")

client = genai.Client(api_key=GEMINI_API_KEY)
current_headline = ""

def fetch_live_news():
    rss_sources = [
        # Généralistes & Breaking News France
        {"url": "https://www.francetvinfo.fr/titres.rss", "domain": "https://www.francetvinfo.fr"},
        {"url": "https://www.lefigaro.fr/rss/figaro_flash-actu.xml", "domain": "https://www.lefigaro.fr"},
        {"url": "https://www.lemonde.fr/en-direct/rss_full.xml", "domain": "https://www.lemonde.fr"},
        {"url": "https://www.bfmtv.com/rss/info/flux-rss/flux-toutes-les-actualites/", "domain": "https://www.bfmtv.com"},
        {"url": "https://www.ouest-france.fr/rss-en-continu.xml", "domain": "https://www.ouest-france.fr"},
        
        # International & Géopolitique (en Français)
        {"url": "https://www.rfi.fr/fr/contenu/general/rss", "domain": "https://www.rfi.fr"},
        {"url": "https://www.courrierinternational.com/feed/all/rss.xml", "domain": "https://www.courrierinternational.com"},
        {"url": "https://fr.euronews.com/rss", "domain": "https://fr.euronews.com"},
        
        # Économie & Business
        {"url": "https://www.lesechos.fr/rss/rss_une.xml", "domain": "https://www.lesechos.fr"},
        {"url": "https://www.latribune.fr/feed/full/rss.xml", "domain": "https://www.latribune.fr"}
    ]
    
    context = ssl._create_unverified_context()
    items = []
    seen_titles = set()

    for source in rss_sources:
        try:
            req = urllib.request.Request(
                source["url"], 
                headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
            )
            html = urllib.request.urlopen(req, context=context, timeout=5).read()
            feed = feedparser.parse(html)
            
            for entry in feed.entries[:5]:
                title = entry.title.replace("\n", " ").strip()
                link = entry.link.strip()
                
                if link.startswith("/"):
                    link = source["domain"] + link
                
                if title not in seen_titles:
                    seen_titles.add(title)
                    items.append(f"TITRE BRUT: {title} | LINK: {link}")
        except Exception:
            continue
                    
    return "\n".join(items)

def clean_url(raw_url):
    match = re.search(r'https?://[^\s"\'<>]+', raw_url)
    if match:
        return match.group(0)
    return raw_url.strip()

def update_html_file(headline=None, url=None):
    heure = time.strftime("%H:%M")
    
    try:
        with open("app.html", "r", encoding="utf-8") as f:
            content = f.read()

        if headline:
            content = re.sub(r'id="headline">.*?</h1>', f'id="headline">{headline}</h1>', content)
        if url:
            content = re.sub(r'href="[^"]*"\s+id="source-link"', f'href="{url}" id="source-link"', content)
            content = re.sub(r'id="source-link"\s+href="[^"]*"', f'id="source-link" href="{url}"', content)
        
        content = re.sub(r'id="time-indicator">.*?</span>', f'id="time-indicator">MàJ {heure}</span>', content)

        with open("app.html", "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"--> Erreur lors de la mise à jour de app.html : {e}")

def check_and_update():
    global current_headline
    
    print(f"[{time.strftime('%H:%M:%S')}] Évaluation éditoriale des dépêches...")
    news_list = fetch_live_news()
    
    if not news_list:
        print("--> Échec : Impossible d'obtenir les flux RSS.\n")
        update_html_file()
        return

    prompt = f"""
Voici le fil des dépêches de dernière minute :
{news_list}

L'information actuellement affichée sur l'écran est :
"{current_headline}"

RÔLE & MISSION :
Tu es le rédacteur en chef d'un média d'urgence. Évalue les dépêches ci-dessus et sélectionne celle qui obtient le MEILLEUR SCORE selon la grille (Multi-source, Impact direct, Proximité, Rupture).

RÈGLES DE RÉÉCRITURE DU TITRE :
1. Réécris le titre de la dépêche choisie pour qu'il soit ultra-percutant.
2. LIMITE STRICTE DE LONGUEUR : Maximum 75 caractères (espaces compris).
3. Style : Présent de l'indicatif, tournure active, aucun mot inutile.
4. Reste strictly fidèle aux faits bruts. N'invente aucune information.

CONSIGNE DE SORTIE :
- Si l'information actuellement affichée sur l'écran reste la plus urgente/importante, réponds strictement "NO_CHANGE".
- Sinon, renvoie le titre réécrit et l'URL exacte associée à la dépêche originale.

FORMAT STRICT DE RÉPONSE : TITRE_REECRIT|||LINK
"""

    models_to_try = ["gemini-3.6-flash", "gemini-3.5-flash"]
    response = None

    for m in models_to_try:
        try:
            res = client.models.generate_content(
                model=m,
                contents=prompt
            )
            if res and res.text:
                response = res
                print(f"--> Requête réussie via : {m}")
                break
        except Exception as err:
            print(f"--> Échec sur {m} : {err}")
            continue

    if not response or not response.text:
        print("--> Erreur : Aucun modèle Gemini n'a répondu.\n")
        update_html_file()
        return

    result = response.text.strip()

    if result != "NO_CHANGE" and "|||" in result:
        headline, raw_link = result.split("|||", 1)
        
        headline_clean = headline.replace("TITRE_REECRIT:", "").replace("TITRE:", "").strip()
        url_clean = clean_url(raw_link)
        
        current_headline = headline_clean
        update_html_file(current_headline, url_clean)
        print(f"--> NOUVEL INSTANT PUBLIÉ ({len(current_headline)} car.) : {current_headline}")
        print(f"--> LIEN SOURCE : {url_clean}\n")
    else:
        update_html_file()
        print(f"--> Pas de changement d'actu. Horodatage mis à jour à {time.strftime('%H:%M')}.\n")

# Lancement immédiat au démarrage
check_and_update()

# Programmation explicite aux minutes pile :00 et :30
schedule.every().hour.at(":00").do(check_and_update)
schedule.every().hour.at(":30").do(check_and_update)

print("--> Moteur actif. Prochains cycles programmés à :00 et :30 de chaque heure.\n")

# Boucle d'écoute non-bloquante
while True:
    schedule.run_pending()
    time.sleep(1)