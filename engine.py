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

print("--> [START] Moteur Instant (Calibrage 65-75 car. / idéal ~70 car.)", flush=True)

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
        prompt = f"""Voici les titres actuellement présents à la UNE de plusieurs grands médias français :
{news_list}

Titre actuellement affiché dans l’application :
"{current_h}"

RÔLE
Tu es le rédacteur en chef d’une application de breaking news minimaliste.
Sa promesse : ne montrer que l’information qui compte vraiment maintenant.

MISSION
À partir des titres fournis, identifie UNE SEULE information qui mérite d’être affichée maintenant.

Le bon choix est l’événement concret qui, à cet instant, a le plus fort mélange de :
1. IMPACT : conséquences réelles pour le pays, la population, l’économie, les institutions ou la sécurité.
2. FRAÎCHEUR : événement nouveau ou développement significatif très récent.
3. PORTÉE : nombre de personnes potentiellement concernées.
4. CONSENSUS : plusieurs rédactions indépendantes couvrent le même événement.
5. GRAVITÉ : importance intrinsèque de l’événement, même si la couverture médiatique est encore limitée.

IMPORTANT
Le consensus médiatique est un SIGNAL, pas une condition obligatoire.
Une information majeure peut être sélectionnée même si elle n’apparaît encore que dans un seul flux.

COMPARAISON AVEC LE TITRE ACTUEL
Ne remplace pas le titre actuel simplement parce qu’une autre information est importante.

Le nouveau sujet doit clairement être :
- plus important,
- ou plus récent et significatif,
- ou plus susceptible d’avoir des conséquences immédiates.

Si aucune information ne constitue une amélioration claire par rapport au titre actuel, conserve le titre actuel.

PRIORITÉS
Privilégie notamment :
- catastrophe ou alerte majeure,
- guerre, attaque ou crise géopolitique majeure,
- décision gouvernementale ou institutionnelle ayant des conséquences immédiates,
- loi, vote ou décision de justice majeure,
- crise économique ou financière majeure,
- changement majeur affectant la vie quotidienne d’une large partie de la population,
- événement international ayant des conséquences importantes pour la France.

POLITIQUE
Les sujets politiques sont pertinents lorsqu’ils correspondent à un changement concret de pouvoir, de gouvernement, de politique publique, d’institution ou de stabilité nationale.

EXCLUSIONS
Écarte :
- spéculations électorales et stratégies pour des scrutins futurs,
- candidatures et ambitions politiques,
- petites phrases et déclarations sans conséquence concrète,
- querelles partisanes,
- faits divers locaux sans portée nationale,
- résultats sportifs ordinaires,
- sujets lifestyle, culturels ou magazine,
- informations anciennes simplement remises en avant.

RÈGLES FACTUELLES
- Ne déduis aucun fait qui n’est pas suffisamment étayé par les titres fournis.
- Si plusieurs médias décrivent le même événement avec des détails différents, ne conserve que les faits compatibles entre eux.
- Ne transforme pas une déclaration en décision.
- Ne transforme pas une intention en événement accompli.
- Ne dramatise jamais artificiellement une information.

STRUCTURE DU TITRE
Direct, factuel, percutant et immédiatement compréhensible.
Un format avec deux-points est autorisé.
Évite le sensationnalisme, le clickbait et le jargon inutile.

HIÉRARCHIE DE L'INFORMATION
Quand l’espace le permet, priorise les informations dans cet ordre :
1. CE QUI s’est passé (l’événement central)
2. QUI ou QUOI est impliqué (les acteurs clés)
3. La CONSÉQUENCE ou le CONTEXTE déterminant

Intègre la conséquence ou le contexte dès lors qu’il améliore nettement la compréhension.
Supprime les détails secondaires avant de rogner sur l’événement principal.

LONGUEUR DU TITRE
Cible : idéalement environ 70 caractères.
Fourchette visée : 65 à 75 caractères.
Exploite l’espace disponible pour maximiser l’information utile sans surcharger.

Ne raccourcis pas un titre dans le seul but de le rendre plus bref si un fait supplémentaire le rend plus informatif.
N’ajoute aucun mot de remplissage pour atteindre artificiellement la longueur cible.
La clarté, l’exactitude factuelle et la densité d’information priment sur le décompte exact de caractères.
Ne produis jamais un titre vague si les informations fournies permettent d’être précis.

TYPOGRAPHIE
Conserve les majuscules des noms propres, pays et sigles réels (RN, LFI, SNCF, UE, ONU, etc.).
Conserve la majuscule aux noms propres même après un deux-points.
Utilise l’apostrophe courbe (’).

SORTIE STRICTE
Retourne exactement :

TITRE|||LINK

Aucun autre texte."""
    else:
        prompt = f"""Here are the headlines currently appearing on the front pages of major US news outlets:
{news_list}

Current headline displayed in the app:
"{current_h}"

ROLE
You are the Editor-in-Chief of a minimalist breaking news app.
Its promise: show only the information that genuinely matters right now.

MISSION
From the headlines provided, identify ONE SINGLE story that deserves to be displayed now.

The right choice is the concrete event with the strongest combination of:
1. IMPACT: real consequences for people, the country, the economy, institutions, or national security.
2. FRESHNESS: a new event or a significant recent development.
3. REACH: the number of people potentially affected.
4. CONSENSUS: independent newsrooms covering the same event.
5. SEVERITY: the intrinsic importance of the event, even when media coverage is still limited.

IMPORTANT
Media consensus is a SIGNAL, not a mandatory requirement.
A major breaking story can be selected even if it currently appears in only one feed.

COMPARISON WITH THE CURRENT HEADLINE
Do not replace the current headline simply because another story is important.

The new story must clearly be:
- more important,
- or more recent and significant,
- or more likely to have immediate consequences.

If no story is a clear improvement over the current headline, keep the current headline.

PRIORITIES
Prioritize:
- major disasters or critical alerts,
- war, attacks, or major geopolitical crises,
- major government or institutional decisions with immediate consequences,
- major legislation passed or major Supreme Court decisions,
- major economic or financial developments,
- major changes affecting everyday life for a large part of the population,
- major international events with significant consequences for the United States.

POLITICS
Political stories qualify when they represent a concrete change in government, policy, institutional power, law, public order, or national stability.

EXCLUSIONS
Reject:
- future electoral speculation and campaign strategy,
- candidate positioning and early campaign moves,
- partisan horse-race coverage,
- political soundbites or statements without concrete consequences,
- political feuds and rhetorical clashes,
- isolated local crime or accidents,
- routine sports results,
- lifestyle, entertainment, or soft magazine stories,
- old stories merely receiving renewed coverage.

FACTUAL RULES
- Do not infer facts that are not sufficiently supported by the supplied headlines.
- When multiple outlets describe the same event differently, use only facts that are consistent across sources.
- Never turn a statement into a decision.
- Never turn an intention into an accomplished event.
- Never artificially dramatize a story.

HEADLINE
Direct, factual, authoritative, and immediately understandable.
A topic header followed by a colon is allowed.
Avoid sensationalism, clickbait, and unnecessary jargon.

HEADLINE STRUCTURE
When space allows, prioritize information in this order:
1. WHAT happened
2. WHO or WHAT is involved
3. KEY consequence or context

Include the consequence or context when it materially improves understanding.
Remove secondary details before removing the core event.

HEADLINE LENGTH
Target approximately 70 characters.

Aim for 65–75 characters, with 70 characters as the preferred target.
Use the available space to maximize useful information without overloading.

Do not shorten a headline merely to make it more concise if additional factual information would make it more useful.
Do not add filler words just to reach the target length.

Clarity, factual accuracy, and information density take priority over exact character count.
Never use a vague headline when the supplied information allows for a more specific one.

CAPITALIZATION & TYPOGRAPHY
Preserve correct capitalization for proper nouns, countries, and genuine acronyms (US, USA, EU, UN, FBI, CIA, NATO, AI, GDP, etc.).
Preserve capitalization on proper nouns even following a colon.
Use curly apostrophes (’) only.

STRICT OUTPUT
Return exactly:

TITLE|||LINK

No other text."""

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
