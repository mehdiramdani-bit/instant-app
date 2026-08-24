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
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================

os.environ["TZ"] = "Europe/Paris"
if hasattr(time, "tzset"):
    time.tzset()

print("--> [START] Instant — Moteur Consolidé avec Persistance d'État", flush=True)

STATE_FILE = "instant_state.json"

# ============================================================
# ÉTAT COURANT & PERSISTANCE
# ============================================================

current_news = {
    "FR": {
        "headline": "Analyse en cours…",
        "url": "https://www.lemonde.fr",
        "source": "Le Monde",
        "article_id": None,
        "updated_at": None,
    },
    "US": {
        "headline": "Analysis in progress…",
        "url": "https://www.nytimes.com",
        "source": "NY Times",
        "article_id": None,
        "updated_at": None,
    },
}

def load_state():
    global current_news
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    current_news.update(saved)
            print("💾 [STATE] État restauré depuis le disque.", flush=True)
        except Exception as e:
            print(f"⚠️ [STATE] Erreur lecture : {e}", flush=True)

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(current_news, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ [STATE] Erreur sauvegarde : {e}", flush=True)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def minutes_since(iso_str):
    if not iso_str:
        return "inconnu"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
        return f"{mins} minutes"
    except Exception:
        return "inconnu"

# ============================================================
# SOURCES (5 FR / 5 US)
# ============================================================

SOURCES_FR = [
    {"name": "Le Monde", "url": "https://www.lemonde.fr/rss/une.xml", "domain": "https://www.lemonde.fr"},
    {"name": "Le Figaro", "url": "https://www.lefigaro.fr/rss/figaro_une.xml", "domain": "https://www.lefigaro.fr"},
    {"name": "France Info", "url": "https://www.francetvinfo.fr/titres.rss", "domain": "https://www.francetvinfo.fr"},
    {"name": "20 Minutes", "url": "https://www.20minutes.fr/feeds/rss-une.xml", "domain": "https://www.20minutes.fr"},
    {"name": "BFM TV", "url": "https://www.bfmtv.com/rss/info/flux-rss/flux-toutes-les-actualites/", "domain": "https://www.bfmtv.com"},
]

SOURCES_US = [
    {"name": "NY Times", "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "domain": "https://www.nytimes.com"},
    {"name": "Washington Post", "url": "https://feeds.washingtonpost.com/rss/national", "domain": "https://www.washingtonpost.com"},
    {"name": "CNN", "url": "http://rss.cnn.com/rss/cnn_topstories.rss", "domain": "https://edition.cnn.com"},
    {"name": "NPR News", "url": "https://feeds.npr.org/1001/rss.xml", "domain": "https://www.npr.org"},
    {"name": "CBS News", "url": "https://www.cbsnews.com/latest/rss/main", "domain": "https://www.cbsnews.com"},
]

# ============================================================
# UTILITAIRES DE VALIDATION & RSS
# ============================================================

def clean_url(raw_url):
    if not raw_url:
        return ""
    match = re.search(r"https?://[^\s\"'<>]+", raw_url.strip())
    return match.group(0) if match else raw_url.strip()

def sanitize_headline(text):
    if not text:
        return ""
    text = text.strip().strip('"').strip("'")
    text = re.sub(r"(?i)^(?:REWRITTEN_HEADLINE|TITRE_REECRIT|HEADLINE|TITRE|TITLE)\s*:\s*", "", text)
    text = text.replace("|||", "")
    text = text.replace("'", "’")
    return re.sub(r"\s+", " ", text).strip()

def validate_headline(headline):
    if not headline:
        return False
    length = len(headline.strip())
    if length < 40 or length > 90:
        print(f"⚠️ [VALIDATION] Longueur invalide ({length} car.) : {headline}", flush=True)
        return False
    return True

def fetch_rss_items(sources):
    context = ssl._create_unverified_context()
    items = []
    seen_titles = set()
    item_id = 1

    for source in sources:
        try:
            req = urllib.request.Request(
                source["url"],
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
            )
            response = urllib.request.urlopen(req, context=context, timeout=8)
            feed = feedparser.parse(response.read())

            for entry in feed.entries[:7]:
                title = getattr(entry, "title", "").replace("\n", " ").strip()
                link = getattr(entry, "link", "").strip()

                if not title:
                    continue

                if link.startswith("/"):
                    link = source["domain"] + link

                norm_key = re.sub(r"[^\w\s]", "", title.lower()).strip()
                if norm_key in seen_titles:
                    continue
                seen_titles.add(norm_key)

                items.append({
                    "id": item_id,
                    "source": source["name"],
                    "title": title,
                    "url": clean_url(link),
                })
                item_id += 1
        except Exception as e:
            print(f"⚠️ [RSS] {source['name']} : {str(e)[:80]}", flush=True)
            continue

    return items

def format_news_for_prompt(items):
    if not items:
        return "NO NEWS AVAILABLE"
    return "\n".join([f"[{item['id']}] SOURCE: {item['source']} | HEADLINE: {item['title']}" for item in items])

# ============================================================
# PROMPTS ÉDITORIAUX
# ============================================================

def build_prompt_fr(news_list, current):
    current_headline = current["headline"]
    current_age = minutes_since(current["updated_at"])

    return f"""Voici les titres actuellement présents dans plusieurs grands médias français :

{news_list}

TITRE ACTUELLEMENT AFFICHÉ :
"{current_headline}"

ÂGE DU TITRE ACTUEL :
Affiché depuis environ {current_age}.

RÔLE
Tu es le rédacteur en chef d’une application de breaking news minimaliste.
Sa promesse : ne montrer que l’information qui compte vraiment maintenant.

MISSION
À partir des titres fournis, identifie UNE SEULE information qui mérite d’être affichée maintenant.

Le bon choix est l’événement concret qui présente le meilleur mélange de :
1. IMPACT : Conséquences réelles pour le pays, la population, l’économie, les institutions ou la sécurité.
2. FRAÎCHEUR : Événement nouveau ou développement significatif très récent.
3. PORTÉE : Nombre de personnes potentiellement concernées.
4. CONSENSUS : Plusieurs rédactions indépendantes couvrent le même événement.
5. GRAVITÉ : Importance intrinsèque de l’événement, même si la couverture médiatique est encore limitée.

IMPORTANT
Le consensus médiatique est un SIGNAL, pas une condition obligatoire.
Une information majeure peut être sélectionnée même si elle n’apparaît encore que dans un seul flux.
Ne confonds jamais volume médiatique et importance réelle.

COMPARAISON AVEC LE TITRE ACTUEL
Ne remplace pas le titre actuel simplement parce qu’une autre information est importante.
Le nouveau sujet doit clairement être :
- plus important,
- ou plus récent et significatif,
- ou plus susceptible d’avoir des conséquences immédiates.

Ne remplace jamais le titre actuel pour une amélioration marginale.
Si deux sujets sont proches en importance, CONSERVE LE TITRE ACTUEL.
Si aucune information ne constitue une amélioration claire, conserve le titre actuel.

PRIORITÉS
Privilégie notamment :
- catastrophe ou alerte majeure,
- guerre, attaque ou crise géopolitique majeure,
- décision gouvernementale ou institutionnelle ayant des conséquences immédiates,
- loi ou vote majeur,
- décision majeure du Conseil constitutionnel ou du Conseil d’État,
- crise économique ou financière majeure,
- changement majeur affectant la vie quotidienne d’une large partie de la population,
- événement international ayant des conséquences importantes pour la France.

JUSTICE ET PROCÉDURES
Retiens uniquement les affaires judiciaires visant des personnalités de tout premier plan de l'État ou les décisions majeures des juridictions suprêmes.
Écarte systématiquement : affaires pénales de particuliers, gardes à vue, détentions provisoires, procès de particuliers, faits divers judiciaires, figures secondaires.

POLITIQUE
Les sujets politiques sont pertinents lorsqu’ils correspondent à un changement concret de pouvoir, de gouvernement, de politique publique, d’institution ou de stabilité nationale.

EXCLUSIONS
Écarte : spéculations électorales, stratégies pour des scrutins futurs, candidatures et ambitions politiques, petites phrases, déclarations sans conséquence concrète, querelles partisanes, faits divers locaux ou individuels sans portée nationale, résultats sportifs ordinaires, lifestyle, culture, divertissement, sujets magazine, informations anciennes simplement remises en avant.

RÈGLES FACTUELLES
- Ne déduis aucun fait qui n’est pas suffisamment étayé par les titres.
- Si plusieurs médias décrivent le même événement avec des détails différents, conserve uniquement les faits compatibles entre eux.
- Ne transforme pas une déclaration en décision.
- Ne transforme pas une intention en événement accompli.
- Ne dramatise jamais artificiellement une information.
- N'invente aucune information absente des sources.

STRUCTURE DU TITRE
Direct, factuel, percutant et immédiatement compréhensible.
Quand l’espace le permet, priorise :
1. CE QUI s’est passé
2. QUI ou QUOI est impliqué
3. La CONSÉQUENCE ou le CONTEXTE déterminant

LONGUEUR DU TITRE
Cible : idéalement environ 70 caractères.
Fourchette visée : 65 à 75 caractères.
Utilise l’espace disponible pour maximiser la densité d’information sans surcharger.
Ne raccourcis pas un titre uniquement pour le rendre plus bref si un fait supplémentaire le rend plus informatif.
N’ajoute jamais de mots de remplissage pour atteindre artificiellement la longueur cible.
La clarté et la densité d’information priment sur le décompte exact.

TYPOGRAPHIE
Conserve les majuscules des noms propres, pays et sigles réels (RN, LFI, SNCF, UE, ONU, etc.).
Conserve la majuscule aux noms propres même après un deux-points.
Utilise l’apostrophe courbe (’).

SÉLECTION DE LA SOURCE
Tu dois retourner l'ID de l'article qui représente le mieux l'information sélectionnée.
Ne retourne jamais directement une URL.
Si tu conserves le titre actuel, retourne l'ID de sa source si elle est disponible dans la liste. Sinon retourne 0.

SORTIE STRICTE
Retourne exactement :

TITRE|||ARTICLE_ID

Aucun autre texte."""

def build_prompt_us(news_list, current):
    current_headline = current["headline"]
    current_age = minutes_since(current["updated_at"])

    return f"""Here are the headlines currently appearing across major US news outlets:

{news_list}

CURRENT HEADLINE DISPLAYED:
"{current_headline}"

CURRENT HEADLINE AGE:
Displayed for approximately {current_age}.

ROLE
You are the Editor-in-Chief of a minimalist breaking news app.
Its promise: show only the information that genuinely matters right now.

MISSION
From the headlines provided, identify ONE SINGLE story that deserves to be displayed now.

The right choice is the concrete event with the strongest combination of:
1. IMPACT: Real consequences for people, the country, the economy, institutions, or national security.
2. FRESHNESS: A new event or a significant recent development.
3. REACH: The number of people potentially affected.
4. CONSENSUS: Independent newsrooms covering the same event.
5. SEVERITY: The intrinsic importance of the event, even when media coverage is still limited.

IMPORTANT
Media consensus is a SIGNAL, not a mandatory requirement.
A major breaking story can be selected even if it currently appears in only one feed.
Never confuse media volume with actual importance.

COMPARISON WITH THE CURRENT HEADLINE
Do not replace the current headline simply because another story is important.
The new story must clearly be:
- more important,
- or more recent and significant,
- or more likely to have immediate consequences.

Do not replace the current headline for a marginal improvement.
If two stories are close in importance, KEEP THE CURRENT HEADLINE.
If no story is a clear improvement over the current headline, keep the current headline.

PRIORITIES
Prioritize:
- major disasters or critical alerts,
- war, attacks, or major geopolitical crises,
- major government or institutional decisions with immediate consequences,
- major legislation or Supreme Court decisions,
- major economic or financial developments,
- major changes affecting everyday life for a large part of the population,
- major international events with significant consequences for the US.

LEGAL & JUDICIAL PROCEEDINGS
Include legal or criminal proceedings ONLY when they involve top-tier national figures or major Supreme Court decisions.
Strictly exclude criminal cases involving private citizens, local arrests, or isolated local incidents.

POLITICS
Political stories qualify when they represent a concrete change in government, policy, institutional power, law, public order, or national stability.

EXCLUSIONS
Reject: future electoral speculation, campaign strategy, candidate positioning, early campaign moves, partisan horse-race coverage, political soundbites without concrete consequences, political feuds, isolated local crime/accidents, routine sports, lifestyle, entertainment, soft magazine stories, old stories merely receiving renewed coverage.

FACTUAL RULES
- Do not infer facts that are not sufficiently supported by the headlines.
- When multiple outlets describe the same event differently, use only facts that are consistent across sources.
- Never turn a statement into a decision.
- Never turn an intention into an accomplished event.
- Never artificially dramatize a story.
- Never invent information absent from the sources.

HEADLINE STRUCTURE
Direct, factual, authoritative, and immediately understandable.
When space allows, prioritize:
1. WHAT happened
2. WHO or WHAT is involved
3. KEY consequence or context

HEADLINE LENGTH
Target approximately 70 characters.
Aim for 65-75 characters.
Use the available space to maximize useful information without overloading.
Do not shorten a headline merely to make it more concise if additional factual information makes it more useful.
Do not add filler words just to reach the target length.
Clarity and information density take priority over exact character count.

CAPITALIZATION & TYPOGRAPHY
Preserve correct capitalization for proper nouns, countries, and genuine acronyms (US, USA, EU, UN, FBI, CIA, NATO, AI, GDP, etc.).
Preserve capitalization on proper nouns even following a colon.
Use curly apostrophes (’) only.

SOURCE SELECTION
Return the ID of the article that best represents the selected story.
Never return a URL.
If you keep the current headline, return its source ID if available in the list. Otherwise return 0.

STRICT OUTPUT
Return exactly:

TITLE|||ARTICLE_ID

No other text."""

# ============================================================
# GEMINI ENGINE
# ============================================================

def evaluate_news(lang, items):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ [GEMINI] GEMINI_API_KEY absente.", flush=True)
        return None

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"❌ [GEMINI] Erreur SDK : {e}", flush=True)
        return None

    current = current_news[lang]
    news_list = format_news_for_prompt(items)
    prompt = build_prompt_fr(news_list, current) if lang == "FR" else build_prompt_us(news_list, current)

    models_to_try = [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-flash-lite-latest",
    ]

    for model_name in models_to_try:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            if not response or not response.text:
                continue

            raw = response.text.strip()
            if "|||" not in raw:
                continue

            headline, article_id_raw = raw.split("|||", 1)
            headline = sanitize_headline(headline)

            try:
                article_id = int(re.search(r"\d+", article_id_raw).group())
            except Exception:
                continue

            if not validate_headline(headline):
                continue

            if article_id == 0:
                print(f"↩️ [GEMINI] Conservation du titre actuel ({lang})", flush=True)
                return {
                    "headline": current["headline"],
                    "url": current["url"],
                    "source": current["source"],
                    "article_id": current["article_id"],
                    "keep_current": True,
                }

            selected_item = next((item for item in items if item["id"] == article_id), None)
            if not selected_item:
                continue

            print(f"✅ [GEMINI OK] {model_name} ({lang})", flush=True)
            return {
                "headline": headline,
                "url": selected_item["url"],
                "source": selected_item["source"],
                "article_id": selected_item["id"],
                "keep_current": False,
            }
        except Exception as err:
            print(f"↳ Tentative {model_name} : {str(err)[:120]}...", flush=True)
            continue

    print(f"❌ [GEMINI] Aucun modèle n'a pu répondre pour {lang}.", flush=True)
    return None

def apply_result(lang, result):
    if not result or result.get("keep_current"):
        return

    current_news[lang] = {
        "headline": result["headline"],
        "url": result["url"],
        "source": result["source"],
        "article_id": result["article_id"],
        "updated_at": now_iso(),
    }
    print(f"📢 [{lang}] {current_news[lang]['headline']}", flush=True)
    print(f"   ↳ {result['source']} → {result['url']}", flush=True)

# ============================================================
# HTML & SYNC
# ============================================================

def update_html_files():
    json_payload = json.dumps(current_news, ensure_ascii=False)
    for filename in ["app.html", "index.html"]:
        if not os.path.exists(filename):
            continue
        try:
            with open(filename, "r", encoding="utf-8") as file:
                content = file.read()

            pattern = r'(<script id="news-data" type="application/json">).*?(</script>)'
            replacement = rf'\1\n  {json_payload}\n  \2'
            new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

            with open(filename, "w", encoding="utf-8") as file:
                file.write(new_content)
        except Exception as e:
            print(f"⚠️ [HTML] {filename} : {str(e)[:80]}", flush=True)

# ============================================================
# CYCLE PRINCIPAL
# ============================================================

refresh_lock = threading.Lock()

def check_and_update():
    if not refresh_lock.acquire(blocking=False):
        print("⏳ [REFRESH] Évaluation déjà en cours.", flush=True)
        return

    try:
        print(f"\n[{time.strftime('%H:%M:%S')}] --- ÉVALUATION GEMINI ---", flush=True)

        # FR
        try:
            news_fr = fetch_rss_items(SOURCES_FR)
            print(f"📰 [FR] {len(news_fr)} articles récupérés", flush=True)
            result_fr = evaluate_news("FR", news_fr)
            apply_result("FR", result_fr)
        except Exception as e:
            print(f"⚠️ [FR] Erreur : {e}", flush=True)

        # US
        try:
            news_us = fetch_rss_items(SOURCES_US)
            print(f"📰 [US] {len(news_us)} articles récupérés", flush=True)
            result_us = evaluate_news("US", news_us)
            apply_result("US", result_us)
        except Exception as e:
            print(f"⚠️ [US] Erreur : {e}", flush=True)

        save_state()
        update_html_files()
        print("--- FIN ÉVALUATION ---\n", flush=True)
    finally:
        refresh_lock.release()

# ============================================================
# SERVEUR HTTP
# ============================================================

class InstantAppHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ["/ping", "/cron", "/refresh"]:
            threading.Thread(target=check_and_update, daemon=True).start()
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK - Refresh triggered")
            return

        if self.path.startswith("/api/news"):
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(current_news, ensure_ascii=False).encode("utf-8"))
            return

        if self.path == "/manifest.json":
            manifest_content = {
                "short_name": "Instant",
                "name": "INSTANT",
                "start_url": "/?pwa=1",
                "display": "standalone",
                "background_color": "#000000",
                "theme_color": "#000000",
            }
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(manifest_content, ensure_ascii=False).encode("utf-8"))
            return

        if self.path in ["/", "/index.html", "/app.html"]:
            filename = "app.html" if os.path.exists("app.html") else "index.html"
            if os.path.exists(filename):
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                with open(filename, "rb") as file:
                    self.wfile.write(file.read())
                return

        return super().do_GET()

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), InstantAppHandler) as httpd:
        print(f"🌐 [SERVER] Port {port}", flush=True)
        httpd.serve_forever()

# ============================================================
# DÉMARRAGE
# ============================================================

load_state()

threading.Thread(target=run_http_server, daemon=True).start()
threading.Thread(target=check_and_update, daemon=True).start()

schedule.every().hour.at(":00").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())
schedule.every().hour.at(":30").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())

print("⏱️ [SCHEDULER] Refresh toutes les 30 minutes", flush=True)

while True:
    try:
        schedule.run_pending()
        time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 [STOP] Arrêt demandé.", flush=True)
        break
    except Exception as e:
        print(f"⚠️ [MAIN LOOP] {e}", flush=True)
        time.sleep(2)
