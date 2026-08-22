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

print("--> [START] Moteur Instant (Filtre strict Faits concrets vs Petites phrases)", flush=True)

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
    "US", "USA", "UE", "EU", "ONU", "UN", "OTAN", "NATO", "IA", "AI",
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

    def fix_colon(match):
        prefix, char, rest = match.group(1), match.group(2), match.group(3)
        word = char + rest
        clean_word = re.sub(r'[^\w]', '', word).upper()
        if clean_word in COMMON_ACRONYMS:
            return prefix + word.upper()
        return prefix + char.lower() + rest

    text = re.sub(r'(:\s+)([A-ZÀ-Ý])([a-zA-ZÀ-ÿ]*)', fix_colon, text)
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
MISSION : Sélectionner l'unique fait concret dominant en France à cet instant précis.

HIÉRARCHIE ET DIRECTIVES ÉDITORIALES STRICTES :
1. FAITS CONCRETS VS DÉCLARATIONS : Privilégie les actes et événements réels (vote de loi, remaniement acté, crise sociale/grève, décision de justice majeure, catastrophe, traité signé). REJETTE FORMELLEMENT les petites phrases, réactions politiques, clashs, postures, déclarations d'intention sans acte concret ou commentaires d'opinion ("X tacle Y", "Z estime que", "A fustige B").
2. PROXIMITÉ & CONSENSUS : Choisis en priorité un événement pivot touchant directement la France ou l'Europe qui fait la Une simultanée de plusieurs rédactions.
3. INTERNATIONAL : Ne retiens une actualité étrangère que si c'est une rupture historique mondiale majeure partagée unanimement par les Unes.
4. SYNTHÈSE FACTUELLE : Rédige une phrase autonome, nette et factuelle (Sujet + Verbe d'action + Objet).
5. LONGUEUR : 85 caractères maximum.
6. CASSE : Majuscule au début et aux noms propres uniquement. Tout le reste en minuscules.
7. SIGLES : Conserve en MAJUSCULES les sigles légitimes (RN, LFI, SNCF, UE, ONU, IA, PIB, OTAN, etc.).
8. TYPOGRAPHIE : Apostrophe courbe (’) obligatoire.

FORMAT DE SORTIE STRICT (AUCUN AUTRE MOT, AUCUNE BALISE) :
TITRE|||LINK
"""
    else:
        prompt = f"""
Here is the selection of top headlines from major US news outlets:
{news_list}

Current headline displayed: "{current_h}"

ROLE: Editor-in-Chief of a minimalist breaking news app ("The Essential Headline").
MISSION: Select the SINGLE MOST IMPORTANT concrete national news event in the US right now.

STRICT EDITORIAL DIRECTIVES:
1. HARD FACTS OVER POLITICAL RHETORIC: Prioritize tangible events (laws passed, executive orders signed, official court rulings, crises, disaster alerts). STRICTLY REJECT political chatter, soundbites, feud headlines, and pundit reactions ("X slams Y", "Z criticizes", "A reacts to").
2. PROXIMITY & CONSENSUS: Focus on national breaking stories covered simultaneously across multiple front pages.
3. WORLD EVENTS: Retain international news only if it represents a major global crisis or historic turning point.
4. FACTUAL PUNCH: Craft a single, crisp sentence stating the tangible event (Subject + Action Verb).
5. LENGTH: 85 characters maximum.
6. SENTENCE CASE ONLY: Capitalize only the first letter and proper nouns.
7. ACRONYMS: Preserve in ALL CAPS standard acronyms (US, USA, EU, UN, FBI, CIA, USS, NATO, AI, GDP, etc.).
8. TYPOGRAPHY: Curly apostrophes (’).

STRICT OUTPUT FORMAT (NO INTRO, NO LABELS) :
TITLE|||LINK
"""

    models_to_try = [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-2.0-flash"
    ]

    for m in models_to_try:
        try:
            res = client.models.generate_content(model=m, contents=prompt)
            if res and res.text and "|||" in res.text:
                print(f"✅ [GEMINI OK] Modèle {m} ({lang})", flush=True)
                return res.text.strip()
        except Exception as err:
            print(f"  ↳ Tentative {m} : {err}", flush=True)
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
