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

print("--> [START] Moteur Instant (Ajustement: Alertes budgétaires/institutionnelles permises)", flush=True)

current_news = {
    "FR": {"headline": "Analyse en cours...", "url": "https://news.google.fr"},
    "US": {"headline": "Analysis in progress...", "url": "https://news.google.com"}
}

SOURCES_FR = [
    {"name": "Le Monde", "url": "https://www.lemonde.fr/rss/une.xml", "domain": "https://www.lemonde.fr"},
    {"name": "Le Figaro", "url": "https://www.lefigaro.fr/rss/figaro_une.xml", "domain": "https://www.lefigaro.fr"},
    {"name": "France Info", "url": "https://www.francetvinfo.fr/titres.rss", "domain": "https://www.francetvinfo.fr"},
    {"name": "20 Minutes", "url": "https://www.20minutes.fr/feeds/rss-une.xml", "domain": "https://www.20minutes.fr"},
    {"name": "BFM TV", "url": "https://www.bfmtv.com/rss/info/flux-rss/flux-toutes-les-actualites/", "domain": "https://www.bfmtv.com"}
]

SOURCES_US = [
    {"name": "NY Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "domain": "https://www.nytimes.com"},
    {"name": "CNN", "url": "http://rss.cnn.com/rss/cnn_topstories.rss", "domain": "https://edition.cnn.com"},
    {"name": "Google News US", "url": "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en", "domain": "https://news.google.com"},
    {"name": "NPR News", "url": "https://feeds.npr.org/1001/rss.xml", "domain": "https://www.npr.org"},
    {"name": "CBS News", "url": "https://www.cbsnews.com/latest/rss/main", "domain": "https://www.cbsnews.com"}
]

COMMON_ACRONYMS = {
    "US", "USA", "UE", "EU", "ONU", "OTAN", "NATO", "IA", "AI",
    "GOP", "FBI", "CIA", "NSA", "DOJ", "DOGE", "SEC", "FDA", "CDC", "EPA", "FAA", "USS",
    "SNCF", "RATP", "EDF", "RN", "LFI", "PS", "LR", "EELV", "NFP", "PCF", "LREM",
    "CDI", "CDD", "PIB", "GDP", "TVA", "VAT", "CAC40", "CAC", "BCE", "FED", "FMI", "IMF",
    "OMS", "WHO", "OMC", "WTO", "JO", "OG", "IVG", "PMA", "PPR", "ZFE", "PASS",
    "PDG", "CEO", "CFO", "CTO", "COO", "DRH", "RH", "HR", "VIP", "TV", "BD",
    "COVID", "G7", "G20", "COP", "COP28", "COP29", "COP30", "LLM", "API", "RSS"
}

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
            html = urllib.request.urlopen(req, context=context, timeout=8).read()
            feed = feedparser.parse(html)
            
            for index, entry in enumerate(feed.entries[:5]):
                title = getattr(entry, 'title', '').replace("\n", " ").strip()
                link = getattr(entry, 'link', '').strip()
                if link.startswith("/"):
                    link = source["domain"] + link

                if title and title not in seen_titles:
                    seen_titles.add(title)
                    badge = "[TOP_HEADLINE]" if index < 2 else "[SECONDARY]"
                    items.append(f"{badge} [{source['name']}] TITRE: {title} | LINK: {link}")
        except Exception:
            continue
                    
    return "\n".join(items)

def clean_url(raw_url):
    match = re.search(r'https?://[^\s"\'<>]+', raw_url)
    return match.group(0) if match else raw_url.strip()

def sanitize_headline(text):
    text = text.strip().strip('"').strip("'")
    text = re.sub(r'(?i)\b(?:REWRITTEN_HEADLINE|TITRE_REECRIT|HEADLINE|TITRE|TITLE)\b[\s:]*', '', text)
    text = text.replace("|||", "").strip()
    text = text.replace("'", "’")

    words = text.split()
    letters = [c for c in text if c.isalpha()]
    if letters:
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_ratio > 0.55:
            new_words = []
            for w in words:
                clean_w = re.sub(r'[^\w]', '', w).upper()
                if clean_w in COMMON_ACRONYMS or re.match(r'^(?:[A-Z]\.){2,}$', w):
                    new_words.append(w.upper())
                else:
                    new_words.append(w.lower())
            text = " ".join(new_words)
            if len(text) > 0:
                text = text[0].upper() + text[1:]
        else:
            new_words = []
            for w in words:
                clean_w = re.sub(r'[^\w]', '', w).upper()
                if clean_w in COMMON_ACRONYMS:
                    new_words.append(re.sub(r'\b' + clean_w + r'\b', clean_w, w, flags=re.IGNORECASE))
                else:
                    new_words.append(w)
            text = " ".join(new_words)

    return text.strip()

def evaluate_news(lang, news_list):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ [GEMINI] Clé API absente.", flush=True)
        return None

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"❌ [GEMINI] Erreur SDK : {e}", flush=True)
        return None

    current_h = current_news[lang]["headline"]
    
    if lang == "FR":
        prompt = f"""
Voici la sélection des titres de la UNE des grands médias français :
{news_list}

Information actuellement affichée : "{current_h}"

RÔLE : Rédacteur en Chef d'un média d'urgence en France ("L'Information Évidence du Moment").
MISSION : Sélectionner l'unique information pivot d'envergure nationale ou internationale majeure.

DIRECTIVES ÉDITORIALES STRICTES :
1. INTERDICTION DES FAITS DIVERS : Rejette tout meurtre local, accident, agression ou fait divers isolé.
2. ÉVÉNEMENTS STRUCTURANTS ET ALERTES NATIONALES : Privilégie les actes majeurs, crises institutionnelles/géopolitiques ainsi que les alertes budgétaires, économiques et sécuritaires de premier plan. Rejette les polémiques partisanes secondaires et querelles de personnes.
3. STYLE & FORMAT : Sois direct et concis. L'utilisation d'un mot-clé thématique suivi de deux-points est autorisée pour synthétiser (ex : "Dette : Sébastien Lecornu met en garde...", "Ukraine : Emmanuel Macron annonce...").
4. NOMS PROPRES ET MAJUSCULES : Conserve scrupuleusement la majuscule à TOUS les prénoms, noms propres et pays, y compris après les deux-points (ex : "Ukraine : Emmanuel Macron").
5. LONGUEUR STRICTE : 50 à 75 caractères maximum.
6. SIGLES : MAJUSCULES obligatoires pour les sigles reconnus (RN, LFI, SNCF, UE, ONU, IA, PIB, OTAN, etc.).
7. TYPOGRAPHIE : Apostrophe courbe (’) obligatoire.

FORMAT DE SORTIE STRICT (AUCUN AUTRE MOT, AUCUNE BALISE) :
TITRE|||LINK
"""
    else:
        prompt = f"""
Here is the selection of top headlines from major US news outlets:
{news_list}

Current headline displayed: "{current_h}"

ROLE: Editor-in-Chief of a minimalist breaking news app ("The Essential Headline").
MISSION: Select the SINGLE MOST IMPORTANT structural national or global news story right now.

STRICT EDITORIAL DIRECTIVES:
1. NO LOCAL CRIME / FAITS DIVERS: Zero isolated accidents, crimes, or soft features.
2. STRUCTURAL EVENTS: Prioritize major policy, supreme court rulings, geopolitical shifts, or critical crises. Reject political feud chatter.
3. STYLE & FORMAT: Crisp and direct. Topic headers with a colon are permitted if they make the headline shorter (e.g. "Ukraine: White House approves...").
4. PROPER NOUNS: Keep accurate capitalization for all proper names and countries, even after a colon.
5. STRICT LENGTH: 50 to 75 characters maximum.
6. ACRONYMS: Preserve recognized acronyms in ALL CAPS (US, USA, EU, UN, FBI, CIA, USS, NATO, AI, GDP, etc.).
7. TYPOGRAPHY: Curly apostrophes (’).

STRICT OUTPUT FORMAT (NO INTRO, NO LABELS) :
TITLE|||LINK
"""

    models_to_try = [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-flash-lite-latest"
    ]

    for m in models_to_try:
        try:
            res = client.models.generate_content(model=m, contents=prompt)
            if res and res.text and "|||" in res.text:
                print(f"✅ [GEMINI OK] Modèle : {m} ({lang})", flush=True)
                return res.text.strip()
        except Exception as err:
            err_str = str(err)
            print(f"  ↳ Tentative {m} : {err_str[:120]}...", flush=True)
            continue
            
    print(f"❌ [GEMINI] Aucun modèle n'a pu répondre pour {lang}.", flush=True)
    return None

def update_html_files():
    json_payload = json.dumps(current_news, ensure_ascii=False)

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
        except Exception:
            pass

def check_and_update():
    print(f"\n[{time.strftime('%H:%M:%S')}] --- ÉVALUATION GEMINI ---", flush=True)
    
    # FR
    try:
        news_fr = fetch_rss_items(SOURCES_FR)
        res_fr = evaluate_news("FR", news_fr)
        if res_fr and "|||" in res_fr:
            h, u = res_fr.split("|||", 1)
            current_news["FR"] = {"headline": sanitize_headline(h), "url": clean_url(u)}
            print(f"📢 [FR] {current_news['FR']['headline']}", flush=True)
    except Exception as e:
        print(f"⚠️ Erreur FR : {e}", flush=True)

    # US
    try:
        news_us = fetch_rss_items(SOURCES_US)
        res_us = evaluate_news("US", news_us)
        if res_us and "|||" in res_us:
            h, u = res_us.split("|||", 1)
            current_news["US"] = {"headline": sanitize_headline(h), "url": clean_url(u)}
            print(f"📢 [US] {current_news['US']['headline']}", flush=True)
    except Exception as e:
        print(f"⚠️ Erreur US : {e}", flush=True)

    update_html_files()
    print("--- FIN ÉVALUATION ---\n", flush=True)

class InstantAppHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ['/ping', '/cron', '/refresh']:
            threading.Thread(target=check_and_update, daemon=True).start()
            self.send_response(200)
            self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b"OK - Refresh triggered")
            return

        if self.path.startswith('/api/news'):
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            self.wfile.write(json.dumps(current_news, ensure_ascii=False).encode('utf-8'))
            return

        if self.path == '/manifest.json':
            manifest_content = {
                "short_name": "Instant",
                "name": "INSTANT",
                "start_url": "/?pwa=1",
                "display": "standalone",
                "background_color": "#000000",
                "theme_color": "#000000"
            }
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(manifest_content, ensure_ascii=False).encode('utf-8'))
            return

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
        httpd.serve_forever()

threading.Thread(target=run_http_server, daemon=True).start()
threading.Thread(target=check_and_update, daemon=True).start()

schedule.every().hour.at(":00").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())
schedule.every().hour.at(":30").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())

while True:
    schedule.run_pending()
    time.sleep(1)
