import os
import time
import ssl
import urllib.request
import feedparser
import schedule
import re
import threading
import http.server
import socketserver
from google import genai

# Forcer le fuseau horaire de Paris
os.environ['TZ'] = 'Europe/Paris'
if hasattr(time, 'tzset'):
    time.tzset()

# 1. Gestionnaire HTTP pour Render
class InstantAppHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/index.html', '/app.html']:
            filename = "app.html" if os.path.exists("app.html") else "index.html"
            if os.path.exists(filename):
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                with open(filename, 'rb') as f:
                    self.wfile.write(f.read())
                return
        return super().do_GET()

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    with socketserver.TCPServer(("", port), InstantAppHandler) as httpd:
        print(f"--> Serveur HTTP actif sur le port {port}")
        httpd.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# 2. Clé API Gemini
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("⚠️ ATTENTION : La variable d'environnement GEMINI_API_KEY est introuvable !")

client = genai.Client(api_key=GEMINI_API_KEY)
current_headline = ""

def fetch_live_news():
    # Sources resserrées sur le grand public national (RFI et Courrier International retirés)
    rss_sources = [
        {"url": "https://www.francetvinfo.fr/titres.rss", "domain": "https://www.francetvinfo.fr"},
        {"url": "https://www.lefigaro.fr/rss/figaro_flash-actu.xml", "domain": "https://www.lefigaro.fr"},
        {"url": "https://www.lemonde.fr/en-direct/rss_full.xml", "domain": "https://www.lemonde.fr"},
        {"url": "https://www.bfmtv.com/rss/info/flux-rss/flux-toutes-les-actualites/", "domain": "https://www.bfmtv.com"},
        {"url": "https://www.ouest-france.fr/rss-en-continu.xml", "domain": "https://www.ouest-france.fr"},
        {"url": "https://fr.euronews.com/rss", "domain": "https://fr.euronews.com"},
        {"url": "https://www.lesechos.fr/rss/rss_une.xml", "domain": "https://www.lesechos.fr"}
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
            
            for entry in feed.entries[:6]:
                title = entry.title.replace("\n", " ").strip()
                link = entry.link.strip()
                
                if link.startswith("/"):
                    link = source["domain"] + link
                
                if title not in seen_titles:
                    seen_titles.add(title)
                    items.append(f"[{source['domain'].replace('https://www.', '').replace('https://', '')}] TITRE: {title} | LINK: {link}")
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
    
    for filename in ["app.html", "index.html"]:
        if not os.path.exists(filename):
            continue
        try:
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()

            if headline:
                content = re.sub(r'id="headline">.*?</h1>', f'id="headline">{headline}</h1>', content)
            if url:
                content = re.sub(r'href="[^"]*"\s+id="source-link"', f'href="{url}" id="source-link"', content)
                content = re.sub(r'id="source-link"\s+href="[^"]*"', f'id="source-link" href="{url}"', content)
            
            content = re.sub(r'id="time-indicator">.*?</span>', f'id="time-indicator">MàJ {heure}</span>', content)

            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"--> Erreur lors de la mise à jour de {filename} : {e}")

def check_and_update():
    global current_headline
    
    print(f"[{time.strftime('%H:%M:%S')}] Évaluation éditoriale des dépêches...")
    news_list = fetch_live_news()
    
    if not news_list:
        print("--> Échec : Impossible d'obtenir les flux RSS.\n")
        update_html_file()
        return

    prompt = f"""
Voici la liste des dépêches récentes issues de plusieurs médias majeurs :
{news_list}

L'information actuellement affichée à l'écran est :
"{current_headline}"

RÔLE & MISSION :
Tu es le Rédacteur en Chef d'un média d'urgence nationale ("L'Information Évidente du Moment").
Ta mission absolue est de sélectionner L'UNE ET UNIQUE grande information nationale ou internationale majeure qui s'impose à tous ce matin/ce jour.

GRILLE DE SELECTION STRICTE (PAR ORDRE DE PRIORITÉ) :
1. CONSENSUS MULTI-MÉDIAS (CRITÈRE N°1) : Identifie le SUJET RÉCURRENT qui est traité simultanément par plusieurs médias différents dans la liste. C'est le signal absolu de la "Grosse Actu".
2. IMPACT NATIONAL VS. LOCAL : Privilégie strictement les enjeux nationaux/globaux (ex: crise climatique/sécheresse, politique nationale majeure, urgence internationale, économie globale).
3. EXCLUSIONS STRICTES : Exclus impérativement :
   - Les informations régionales ou locales (ex: arrêtés préfectoraux, limitations de vitesse régionales, faits divers locaux, transports locaux).
   - Les faits divers isolés sans portée nationale.
   - Les annonces de sorties culturelles, sportives secondaires ou rubriques "art de vivre".

CONSIGNE D'ÉVALUATION DE L'EXISTANT :
- Si l'information actuellement affichée ("{current_headline}") traite DÉJÀ du sujet majeur identifié dans la liste, réponds strictement "NO_CHANGE".
- Ne change l'information que si un NOUVEAU sujet d'ampleur supérieure apparaît ou si le sujet majeur n'était pas encore affiché.

RÈGLES DE RÉÉCRITURE (SI CHANGEMENT) :
- Limite stricte : Maximum 75 caractères (espaces compris).
- Style : Présent de l'indicatif, percutant, factuel, tournure active.

FORMAT STRICT DE RÉPONSE :
TITRE_REECRIT|||LINK
(ou uniquement la chaîne "NO_CHANGE" si aucun changement)
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

if os.path.exists("app.html") and not os.path.exists("index.html"):
    with open("app.html", "r", encoding="utf-8") as f_in:
        with open("index.html", "w", encoding="utf-8") as f_out:
            f_out.write(f_in.read())

check_and_update()

schedule.every().hour.at(":00").do(check_and_update)
schedule.every().hour.at(":30").do(check_and_update)

print("--> Moteur actif. Prochains cycles programmés à :00 et :30 de chaque heure.\n")

while True:
    schedule.run_pending()
    time.sleep(1)