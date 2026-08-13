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

os.environ['TZ'] = 'Europe/Paris'
if hasattr(time, 'tzset'):
    time.tzset()

print("--> [START] Démarrage du script engine.py")

current_news = {
    "FR": {"headline": "", "url": ""},
    "US": {"headline": "", "url": ""}
}

SOURCES_FR = [
    {"name": "Le Monde", "url": "https://www.lemonde.fr/rss/une.xml", "domain": "https://www.lemonde.fr"},
    {"name": "Le Figaro", "url": "https://www.lefigaro.fr/rss/figaro_une.xml", "domain": "https://www.lefigaro.fr"},
    {"name": "France Info", "url": "https://www.francetvinfo.fr/titres.rss", "domain": "https://www.francetvinfo.fr"},
    {"name": "BFM TV", "url": "https://www.bfmtv.com/rss/info/flux-rss/flux-toutes-les-actualites/", "domain": "https://www.bfmtv.com"},
    {"name": "Les Echos", "url": "https://www.lesechos.fr/rss/rss_une.xml", "domain": "https://www.lesechos.fr"}
]

SOURCES_US = [
    {"name": "NY Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "domain": "https://www.nytimes.com"},
    {"name": "BBC US", "url": "http://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", "domain": "https://www.bbc.com"},
    {"name": "WSJ", "url": "https://feeds.a.dj.com/rss/WSJNewsPlus.xml", "domain": "https://www.wsj.com"}
]

def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("⚠️ [GEMINI] GEMINI_API_KEY introuvable dans l'environnement !")
        return None
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        return client
    except Exception as e:
        print(f"⚠️ [GEMINI] Erreur lors du chargement du SDK : {e}")
        return None

def fetch_rss_items(sources):
    context = ssl._create_unverified_context()
    items = []
    seen_titles = set()

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
                
                if title not in seen_titles:
                    seen_titles.add(title)
                    badge = "[TOP_HEADLINE]" if index < 2 else "[SECONDARY]"
                    items.append(f"{badge} [{source['name']}] TITRE: {title} | LINK: {link}")
        except Exception as e:
            print(f"⚠️ Erreur RSS {source['name']} : {e}")
            continue
                    
    return "\n".join(items)

def clean_url(raw_url):
    match = re.search(r'https?://[^\s"\'<>]+', raw_url)
    return match.group(0) if match else raw_url.strip()

def evaluate_news(client, lang, news_list):
    if not client or not news_list:
        print(f"⚠️ [EVAL SKIP] {lang} ignoré (Client API inactif ou flux vide).")
        return None

    current_h = current_news[lang]["headline"]
    
    if lang == "FR":
        prompt = f"""
Voici la sélection des titres issus de la UNE des grands journaux nationaux français :
{news_list}

Information actuellement affichée : "{current_h}"

RÔLE : Rédacteur en Chef d'un média d'urgence ("L'Information Évidence du Moment").
Mission : Choisir L'UNIQUE sujet national ou international majeur qui domine les Unes aujourd'hui.

CRITÈRES :
1. PRIORITÉ AUX TAGS [TOP_HEADLINE].
2. CONSENSUS MULTI-MÉDIAS (sujet apparaissant dans au moins 2 sources).
3. EXCLUSIONS STRICTES : faits divers régionaux, météo, culture/sports.

FORMAT DE RÉPONSE :
TITRE_REECRIT|||LINK
"""
    else:
        prompt = f"""
Here is the selection of top headlines from major US news outlets:
{news_list}

Current headline displayed: "{current_h}"

ROLE: Editor-in-Chief of a high-urgency news app ("The Essential News Right Now").
Mission: Pick the SINGLE most critical news story dominating US front pages today.

CRITERIA:
1. PRIORITY TO [TOP_HEADLINE] tags.
2. MULTI-MEDIA CONSENSUS.
3. STRICT EXCLUSIONS: local crime, state politics, sports.

RESPONSE FORMAT:
REWRITTEN_HEADLINE|||LINK
"""

    models_to_try = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
    for m in models_to_try:
        try:
            res = client.models.generate_content(model=m, contents=prompt)
            if res and res.text and "|||" in res.text:
                print(f"--> [GEMINI SUCCESS] {m} ({lang})")
                return res.text.strip()
        except Exception as e:
            print(f"⚠️ [GEMINI ERR] Modèle {m} ({lang}) : {e}")
            continue
    return None

def update_html_files():
    time_str = time.strftime("%H:%M")
    
    json_payload = json.dumps({
        "time": time_str,
        "FR": current_news["FR"],
        "US": current_news["US"]
    }, ensure_ascii=False)

    for filename in ["app.html", "index.html"]:
        if not os.path.exists(filename):
            continue
        try:
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()

            new_content = re.sub(
                r'id="news-data"[^>]*>.*.*?/script>',
                f'id="news-data" type="application/json">{json_payload}</script>',
                content,
                flags=re.DOTALL
            )
            
            with open(filename, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"--> [HTML OK] {filename} mis à jour à {time_str}.")
        except Exception as e:
            print(f"⚠️ [HTML ERR] {filename} : {e}")

def check_and_update():
    print(f"\n[{time.strftime('%H:%M:%S')}] --- DEBUT EVALUATION GEMINI ---")
    client = get_gemini_client()
    
    # FR
    try:
        news_fr = fetch_rss_items(SOURCES_FR)
        res_fr = evaluate_news(client, "FR", news_fr)
        if res_fr and "|||" in res_fr:
            h, u = res_fr.split("|||", 1)
            current_news["FR"] = {"headline": h.strip(), "url": clean_url(u)}
    except Exception as e:
        print(f"⚠️ Erreur FR : {e}")

    # US
    try:
        news_us = fetch_rss_items(SOURCES_US)
        res_us = evaluate_news(client, "US", news_us)
        if res_us and "|||" in res_us:
            h, u = res_us.split("|||", 1)
            current_news["US"] = {"headline": h.strip(), "url": clean_url(u)}
    except Exception as e:
        print(f"⚠️ Erreur US : {e}")

    update_html_files()
    print("--- FIN EVALUATION GEMINI ---\n")

class InstantAppHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/', '/index.html', '/app.html']:
            filename = "app.html" if os.path.exists("app.html") else "index.html"
            if os.path.exists(filename):
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.end_headers()
                with open(filename, 'rb') as f:
                    self.wfile.write(f.read())
                return
        return super().do_GET()

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), InstantAppHandler) as httpd:
        print(f"--> [SERVER] Serveur Web à l'écoute sur le port {port}")
        httpd.serve_forever()

# Démarrer le serveur HTTP dans son thread
threading.Thread(target=run_http_server, daemon=True).start()

# Lancer immédiatement l'évaluation au démarrage
check_and_update()

schedule.every().hour.at(":00").do(check_and_update)
schedule.every().hour.at(":30").do(check_and_update)

while True:
    schedule.run_pending()
    time.sleep(1)
