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

print("--> [START] Moteur Instant démarré (Style Dépêche optimisé)", flush=True)

current_news = {
    "FR": {"headline": "Analyse Gemini en cours...", "url": "https://news.google.fr"},
    "US": {"headline": "Gemini analysis in progress...", "url": "https://news.google.com"}
}

SOURCES_FR = [
    {"name": "Le Monde", "url": "https://www.lemonde.fr/rss/une.xml", "domain": "https://www.lemonde.fr"},
    {"name": "Le Figaro", "url": "https://www.lefigaro.fr/rss/figaro_une.xml", "domain": "https://www.lefigaro.fr"},
    {"name": "20 Minutes", "url": "https://www.20minutes.fr/feeds/rss-une.xml", "domain": "https://www.20minutes.fr"},
    {"name": "France Info", "url": "https://www.francetvinfo.fr/titres.rss", "domain": "https://www.francetvinfo.fr"},
    {"name": "BFM TV", "url": "https://www.bfmtv.com/rss/info/flux-rss/flux-toutes-les-actualites/", "domain": "https://www.bfmtv.com"}
]

SOURCES_US = [
    {"name": "NY Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "domain": "https://www.nytimes.com"},
    {"name": "Washington Post", "url": "https://feeds.washingtonpost.com/rss/national", "domain": "https://www.washingtonpost.com"},
    {"name": "AP News", "url": "https://feedx.net/rss/ap.xml", "domain": "https://apnews.com"},
    {"name": "NPR News", "url": "https://feeds.npr.org/1001/rss.xml", "domain": "https://www.npr.org"},
    {"name": "CBS News", "url": "https://www.cbsnews.com/latest/rss/main", "domain": "https://www.cbsnews.com"}
]

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
            print(f"⚠️ RSS {source['name']} : {e}", flush=True)
            continue
                    
    return "\n".join(items)

def clean_url(raw_url):
    match = re.search(r'https?://[^\s"\'<>]+', raw_url)
    return match.group(0) if match else raw_url.strip()

def evaluate_news(lang, news_list):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ [GEMINI STRICT] Clé API introuvable.", flush=True)
        return None

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"❌ [GEMINI STRICT] Échec import SDK: {e}", flush=True)
        return None

    current_h = current_news[lang]["headline"]
    
    if lang == "FR":
        prompt = f"""
Voici la sélection des titres issus de la UNE des grands journaux nationaux français :
{news_list}

Information actuellement affichée : "{current_h}"

RÔLE : Rédacteur en Chef d'un média d'urgence en France ("L'Information Évidence du Moment").
MISSION : Sélectionner l'actualité dominante avec une FORTE PRIORITÉ NATIONALE et rédiger un titre incisif.

HIÉRARCHIE D'ARBITRAGE :
1. PRIORITÉ NATIONALE : Privilégie les événements qui impactent directement la France, les citoyens ou la vie politique/économique nationale.
2. FILTRE INTERNATIONAL STRICT : Ne choisis un sujet international QUE s'il s'agit d'une rupture historique majeure ou d'un événement d'une gravité exceptionnelle. Évite les développements de routine de crises lointaines.
3. CONSENSUS : Présent dans au moins 2 sources.

CONSIGNES DE STYLE ET DE FORME :
- Limite : 80 caractères maximum (espaces compris).
- Style dépêche naturel : Privilégie la voix active OU le style nominal/participe direct (ex: "474 personnes interpellées..." au lieu de "474 personnes sont interpellées...").
- Évite les tournures passives lourdes avec l'auxiliaire être ("est voté", "sont annoncés"). Sois percutant et fluide.

FORMAT DE RÉPONSE EXIGÉ :
TITRE_REECRIT|||LINK
"""
    else:
        prompt = f"""
Here is the selection of top headlines from major domestic US news outlets:
{news_list}

Current headline displayed: "{current_h}"

ROLE: Editor-in-Chief of a high-urgency US news app ("The Essential News Right Now").
MISSION: Select the dominant news story with a STRONG DOMESTIC NATIONAL PRIORITY and craft a sharp headline.

HIERARCHY RULES:
1. DOMESTIC PRIORITY: Strong preference for major stories directly impacting the US (federal government, economy, critical national events).
2. STRICT INTERNATIONAL FILTER: Only select foreign news if it represents a major global breaking event or directly threatens national security. Avoid incremental foreign updates.
3. CONSENSUS: Must be confirmed by at least 2 major outlets.

STYLE RULES:
- Limit: 80 characters maximum (including spaces).
- Wire style: Use active voice OR concise participial/noun phrase (e.g., "474 people arrested..." rather than "474 people are arrested...").
- Avoid clumsy passive voice with "is/are". Keep it natural, sharp, and impactful.

REQUIRED RESPONSE FORMAT:
REWRITTEN_HEADLINE|||LINK
"""

    preferred_models = ["gemini-3.6-flash", "gemini-3.5-flash"]
    
    available_models = []
    try:
        for m in client.models.list():
            name = m.name.replace("models/", "")
            if "flash" in name or "pro" in name:
                available_models.append(name)
    except Exception:
        pass

    models_to_try = list(dict.fromkeys(preferred_models + available_models))

    for m in models_to_try:
        try:
            res = client.models.generate_content(model=m, contents=prompt)
            if res and res.text and "|||" in res.text:
                print(f"✅ [GEMINI VALIDÉ] Modèle {m} a généré la synthèse ({lang})", flush=True)
                return res.text.strip()
        except Exception as e:
            err_msg = str(e)
            print(f"⚠️ [GEMINI ÉCHEC] Modèle {m} ({lang}) : {err_msg[:100]}...", flush=True)
            continue
            
    print(f"❌ [GEMINI ÉCHEC] Aucun modèle n'a pu répondre pour {lang}.", flush=True)
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

            pattern = r'(<script id="news-data" type="application/json">).*?(</script>)'
            replacement = rf'\1\n  {json_payload}\n  \2'
            
            new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
            
            with open(filename, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"--> [HTML OK] {filename} mis à jour à {time_str}", flush=True)
        except Exception as e:
            print(f"⚠️ [HTML ERR] {filename} : {e}", flush=True)

def check_and_update():
    print(f"[{time.strftime('%H:%M:%S')}] --- ÉVALUATION STRICTE GEMINI ---", flush=True)
    
    # FR
    try:
        news_fr = fetch_rss_items(SOURCES_FR)
        res_fr = evaluate_news("FR", news_fr)
        if res_fr and "|||" in res_fr:
            h, u = res_fr.split("|||", 1)
            current_news["FR"] = {"headline": h.strip(), "url": clean_url(u)}
    except Exception as e:
        print(f"⚠️ Erreur FR : {e}", flush=True)

    # US
    try:
        news_us = fetch_rss_items(SOURCES_US)
        res_us = evaluate_news("US", news_us)
        if res_us and "|||" in res_us:
            h, u = res_us.split("|||", 1)
            current_news["US"] = {"headline": h.strip(), "url": clean_url(u)}
    except Exception as e:
        print(f"⚠️ Erreur US : {e}", flush=True)

    update_html_files()
    print("--- FIN ÉVALUATION GEMINI ---\n", flush=True)

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
        print(f"--> [SERVER] HTTP actif sur le port {port}", flush=True)
        httpd.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()
threading.Thread(target=check_and_update, daemon=True).start()

schedule.every().hour.at(":00").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())
schedule.every().hour.at(":30").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())

while True:
    schedule.run_pending()
    time.sleep(1)
