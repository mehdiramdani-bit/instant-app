import os
import time
import ssl
import urllib.request
import feedparser
import schedule
import re
import json
import threading
import http.server
import socketserver
from google import genai

os.environ['TZ'] = 'Europe/Paris'
if hasattr(time, 'tzset'):
    time.tzset()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
client = None
if GEMINI_API_KEY:
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"⚠️ Erreur Gemini : {e}")

current_news = {
    "FR": {"headline": "Analyse de l'actualité en cours...", "url": "https://news.google.fr"},
    "US": {"headline": "Analyzing top breaking news...", "url": "https://news.google.com"}
}

SOURCES_FR = [
    {"name": "Le Monde", "url": "https://www.lemonde.fr/rss/une.xml", "domain": "https://www.lemonde.fr"},
    {"name": "Le Figaro", "url": "https://www.lefigaro.fr/rss/figaro_une.xml", "domain": "https://www.lefigaro.fr"},
    {"name": "France Info", "url": "https://www.francetvinfo.fr/titres.rss", "domain": "https://www.francetvinfo.fr"},
    {"name": "BFM TV", "url": "https://www.bfmtv.com/rss/info/flux-rss/flux-toutes-les-actualites/", "domain": "https://www.bfmtv.com"},
    {"name": "Les Echos", "url": "https://www.lesechos.fr/rss/rss_une.xml", "domain": "https://www.lesechos.fr"}
]

SOURCES_US = [
    {"name": "AP News", "url": "https://feedx.net/rss/ap.xml", "domain": "https://apnews.com"},
    {"name": "NY Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "domain": "https://www.nytimes.com"},
    {"name": "NPR", "url": "https://feeds.npr.org/1001/rss.xml", "domain": "https://www.npr.org"},
    {"name": "CNN", "url": "http://rss.cnn.com/rss/cnn_topstories.rss", "domain": "https://www.cnn.com"},
    {"name": "WSJ", "url": "https://feeds.a.dj.com/rss/WSJNewsPlus.xml", "domain": "https://www.wsj.com"}
]

def fetch_rss_items(sources):
    context = ssl._create_unverified_context()
    items = []
    seen_titles = set()
    first_fallback = None

    for source in sources:
        try:
            req = urllib.request.Request(
                source["url"], 
                headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
            )
            html = urllib.request.urlopen(req, context=context, timeout=5).read()
            feed = feedparser.parse(html)
            
            for index, entry in enumerate(feed.entries[:4]):
                title = entry.title.replace("\n", " ").strip()
                link = entry.link.strip()
                
                if link.startswith("/"):
                    link = source["domain"] + link
                
                if not first_fallback and title:
                    first_fallback = (title, link)

                if title not in seen_titles:
                    seen_titles.add(title)
                    badge = "[TOP_HEADLINE]" if index < 2 else "[SECONDARY]"
                    items.append(f"{badge} [{source['name']}] TITRE: {title} | LINK: {link}")
        except Exception:
            continue
                    
    return "\n".join(items), first_fallback

def clean_url(raw_url):
    match = re.search(r'https?://[^\s"\'<>]+', raw_url)
    if match:
        return match.group(0)
    return raw_url.strip()

def evaluate_news(lang, news_list):
    if not client or not news_list:
        return "NO_CHANGE"

    current_h = current_news[lang]["headline"]
    
    if lang == "FR":
        prompt = f"""
Voici la sélection des titres issus de la UNE des grands journaux nationaux français :
{news_list}

Information actuelle : "{current_h}"

RÔLE : Rédacteur en Chef d'un média d'urgence ("L'Information Évidente du Moment").
Mission : Choisir L'UNIQUE sujet majeur qui domine l'actualité en France aujourd'hui.

CRITÈRES :
1. PRIORITÉ AUX TAGS [TOP_HEADLINE].
2. CONSENSUS MULTI-MÉDIAS (sujet apparaissant dans au moins 2 sources).
3. LOI DE PROXIMITÉ ÉDITORIALE : Privilégier les enjeux impactant directement la France.
4. EXCLUSIONS STRICTES : faits divers régionaux, météo, culture/sports.

RÈGLES D'ÉVALUATION :
- Si l'information actuellement affichée traite DÉJÀ du sujet majeur, réponds "NO_CHANGE".
- Sinon réécris la nouvelle info : Max 75 caractères, présent de l'indicatif, percutant.

FORMAT DE RÉPONSE :
TITRE_REECRIT|||LINK
"""
    else:
        prompt = f"""
Here is the selection of top headlines from major US news outlets:
{news_list}

Current headline displayed: "{current_h}"

ROLE: Editor-in-Chief of a high-urgency news app ("The Essential News Right Now").
Mission: Pick the SINGLE most critical news story dominating US media attention today.

CRITERIA:
1. PRIORITY TO [TOP_HEADLINE] tags.
2. MULTI-MEDIA CONSENSUS (stories reported by 2+ distinct US outlets).
3. PROXIMITY RULE: Prioritize stories directly affecting the US or American public.
4. STRICT EXCLUSIONS: local crime, state-level politics, sports, entertainment.

EVALUATION:
- If current headline ALREADY covers the dominant story, reply "NO_CHANGE".
- Otherwise rewrite the new story: Max 75 characters, active voice, present tense, crisp style.

RESPONSE FORMAT:
REWRITTEN_HEADLINE|||LINK
"""

    models_to_try = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]
    for m in models_to_try:
        try:
            res = client.models.generate_content(model=m, contents=prompt)
            if res and res.text:
                return res.text.strip()
        except Exception as e:
            print(f"⚠️ Test modèle {m} ({lang}) : {e}")
            continue
    return "NO_CHANGE"

def check_and_update():
    print(f"[{time.strftime('%H:%M:%S')}] --- ÉVALUATION FR/US ---")
    
    # Traitement FR
    try:
        news_fr, fallback_fr = fetch_rss_items(SOURCES_FR)
        if fallback_fr and ("en cours" in current_news["FR"]["headline"] or not current_news["FR"]["headline"]):
            current_news["FR"] = {"headline": fallback_fr[0][:80], "url": fallback_fr[1]}

        res_fr = evaluate_news("FR", news_fr)
        if res_fr != "NO_CHANGE" and "|||" in res_fr:
            h, u = res_fr.split("|||", 1)
            current_news["FR"] = {"headline": h.strip(), "url": clean_url(u)}
    except Exception as e:
        print(f"⚠️ Erreur FR : {e}")

    # Traitement US
    try:
        news_us, fallback_us = fetch_rss_items(SOURCES_US)
        if fallback_us and ("Analyzing" in current_news["US"]["headline"] or not current_news["US"]["headline"]):
            current_news["US"] = {"headline": fallback_us[0][:80], "url": fallback_us[1]}

        res_us = evaluate_news("US", news_us)
        if res_us != "NO_CHANGE" and "|||" in res_us:
            h, u = res_us.split("|||", 1)
            current_news["US"] = {"headline": h.strip(), "url": clean_url(u)}
    except Exception as e:
        print(f"⚠️ Erreur US : {e}")

    print("--> Mise à jour FR/US terminée.\n")

class InstantAppHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/news':
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            payload = json.dumps(current_news, ensure_ascii=False)
            self.wfile.write(payload.encode('utf-8'))
            return
            
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
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), InstantAppHandler) as httpd:
        print(f"--> Serveur HTTP actif sur le port {port}")
        httpd.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()

# Lancement immédiat de l'actualisation
threading.Thread(target=check_and_update, daemon=True).start()

schedule.every().hour.at(":00").do(check_and_update)
schedule.every().hour.at(":30").do(check_and_update)

print("--> Moteur prêt sur Render.\n")

while True:
    schedule.run_pending()
    time.sleep(1)
