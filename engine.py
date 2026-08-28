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
import hashlib
from datetime import datetime, timezone
from feeds_config import FEEDS

# ============================================================
# CONFIGURATION
# ============================================================

os.environ["TZ"] = "Europe/Paris"
if hasattr(time, "tzset"):
    time.tzset()

print("--> [START] INSTANT Multi-Catégories — Production Engine", flush=True)

STATE_FILE = "instant_categories_state.json"
OUTPUT_JSON = "news_categories.json"
MAX_STORIES = 30
MAX_HISTORY = 20
MIN_STORY_HOLD_MINUTES = 20
ARTICLES_PER_SOURCE = 7
MAX_STORIES_FOR_GEMINI = 12

MODELS_TO_TRY = [
    "gemini-flash-lite-latest",
]

CATEGORIES = ["general", "monde", "eco", "tech", "sciences"]

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

current_news = {
    "FR": {cat: {"headline": "Analyse en cours…", "url": "", "source": "", "article_id": None, "story_id": None, "updated_at": None} for cat in CATEGORIES},
    "US": {cat: {"headline": "Analysis in progress…", "url": "", "source": "", "article_id": None, "story_id": None, "updated_at": None} for cat in CATEGORIES}
}

story_memory = {
    "FR": {cat: {} for cat in CATEGORIES},
    "US": {cat: {} for cat in CATEGORIES}
}

history = {
    "FR": {cat: [] for cat in CATEGORIES},
    "US": {cat: [] for cat in CATEGORIES}
}

# ============================================================
# PERSISTANCE & EXPORT
# ============================================================

def export_news_for_ui():
    ui_payload = {"FR": [], "US": []}
    for lang in ["FR", "US"]:
        for cat in CATEGORIES:
            item = current_news[lang][cat]
            ui_payload[lang].append({
                "category": cat,
                "headline": item["headline"],
                "url": item["url"],
                "source": item["source"],
                "updated_at": item["updated_at"]
            })
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(ui_payload, f, ensure_ascii=False, indent=2)

def load_state():
    global current_news, story_memory, history
    if not os.path.exists(STATE_FILE):
        print("💾 [STATE] Aucun état précédent. Initialisation.", flush=True)
        export_news_for_ui()
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)

        if not isinstance(saved, dict):
            export_news_for_ui()
            return

        for lang in ["FR", "US"]:
            if lang in saved.get("current_news", {}):
                for cat in CATEGORIES:
                    if cat in saved["current_news"][lang]:
                        current_news[lang][cat].update(saved["current_news"][lang][cat])

            if lang in saved.get("story_memory", {}):
                for cat in CATEGORIES:
                    if cat in saved["story_memory"][lang]:
                        story_memory[lang][cat].update(saved["story_memory"][lang][cat])

            if lang in saved.get("history", {}):
                for cat in CATEGORIES:
                    if cat in saved["history"][lang]:
                        history[lang][cat] = saved["history"][lang][cat][-MAX_HISTORY:]

        print("💾 [STATE] État restauré.", flush=True)
        export_news_for_ui()
    except Exception as e:
        print(f"⚠️ [STATE] Erreur lecture : {e}", flush=True)
        export_news_for_ui()

def save_state():
    try:
        payload = {
            "current_news": current_news,
            "story_memory": story_memory,
            "history": history,
        }
        temp_file = STATE_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, STATE_FILE)
        export_news_for_ui()
    except Exception as e:
        print(f"⚠️ [STATE] Erreur sauvegarde : {e}", flush=True)

# ============================================================
# UTILITAIRES TEXTE & CLUSTERING
# ============================================================

def clean_typography(title: str, lang: str = "FR") -> str:
    if not title:
        return ""
    title = title.strip()
    title = re.sub(r'[«»“”„‟]', '"', title)
    title = re.sub(r'"\s+', '"', title)
    title = re.sub(r'\s+"', '"', title)
    return title

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def parse_iso(iso_str):
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None

def minutes_since(iso_str):
    dt = parse_iso(iso_str)
    if not dt:
        return None
    return max(0, int((datetime.now(timezone.utc) - dt).total_seconds() / 60))

STOPWORDS_FR = {
    "les", "des", "une", "un", "dans", "pour", "avec", "sur", "par",
    "aux", "ses", "son", "sa", "leur", "leurs", "qui", "que", "quoi",
    "est", "sont", "être", "avoir", "après", "avant", "plus", "moins",
    "cette", "ce", "cet", "ces", "du", "de", "la", "le", "et", "ou",
    "en", "au", "a", "à", "se", "d", "l", "ne", "pas", "mais", "comme",
    "selon", "face", "vers", "entre", "contre", "depuis",
    "tout", "tous", "toute", "toutes"
}

STOPWORDS_EN = {
    "the", "a", "an", "of", "in", "on", "for", "to", "with", "from",
    "by", "at", "as", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "and", "or", "but", "who",
    "what", "how", "after", "before", "more", "less", "over",
    "under", "into", "amid", "says", "said", "new"
}

GENERIC_WORDS = {
    "breaking", "latest", "update", "updates", "news", "live",
    "report", "reports", "according", "reveals", "announces",
    "announced", "major", "new", "today", "now"
}

def clean_url(raw_url):
    if not raw_url:
        return ""
    match = re.search(r"https?://[^\s\"'<>]+", raw_url.strip())
    return match.group(0) if match else raw_url.strip()

def sanitize_headline(text):
    if not text:
        return ""
    text = text.strip().strip('"').strip("'")
    text = text.replace("|||", "")
    text = text.replace("'", "’")
    words = text.split(" ")
    fixed = []
    for w in words:
        if fixed and fixed[-1].isdigit() and w.isdigit() and len(w) == 3:
            fixed[-1] = fixed[-1] + " " + w
        else:
            fixed.append(w)
    text = " ".join(fixed)
    if ":" in text:
        text = re.sub(r"\s*:\s*", " : ", text)
        def _lower_after(m):
            w = m.group(1)
            if w.lower() in ["les", "la", "le", "un", "une", "des", "ce", "cette", "ces", "son", "sa", "ses", "leur", "leurs", "vers", "pour", "selon", "comment", "pourquoi"]:
                return " : " + w.lower()
            return m.group(0)
        text = re.sub(r" :\s+([A-Za-zÀ-ÖØ-öø-ÿ]+)", _lower_after, text)
    return clean_typography(text.strip())

def validate_headline(headline):
    if not headline:
        return False
    length = len(headline.strip())
    if length < 50 or length > 80:
        return False
    return True

def normalize_text(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    text = text.replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()

def meaningful_tokens(text, lang):
    normalized = normalize_text(text)
    stopwords = STOPWORDS_FR if lang == "FR" else STOPWORDS_EN
    tokens = [t for t in normalized.split() if len(t) >= 3 and t not in stopwords and t not in GENERIC_WORDS and not t.isdigit()]
    return set(tokens)

def token_similarity(tokens_a, tokens_b):
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a.intersection(tokens_b)
    if not intersection:
        return 0.0
    union = tokens_a.union(tokens_b)
    return len(intersection) / len(union) if union else 0.0

def strong_token_similarity(tokens_a, tokens_b):
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a.intersection(tokens_b)
    if len(intersection) >= 3:
        return 1.0
    if len(intersection) == 2:
        return 0.65
    if len(intersection) == 1:
        return 0.25
    return 0.0

def titles_are_same_story(title_a, title_b, lang):
    tokens_a = meaningful_tokens(title_a, lang)
    tokens_b = meaningful_tokens(title_b, lang)
    if not tokens_a or not tokens_b:
        return False
    jaccard = token_similarity(tokens_a, tokens_b)
    strong = strong_token_similarity(tokens_a, tokens_b)
    if jaccard >= 0.35 or strong >= 1.0:
        return True
    if strong >= 0.65 and min(len(tokens_a), len(tokens_b)) <= 5:
        return True
    return False

def make_story_id(title, lang, cat):
    tokens = sorted(meaningful_tokens(title, lang))
    signature = "|".join(tokens[:8])
    if not signature:
        signature = normalize_text(title)[:100]
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:10]
    return f"{lang.lower()}_{cat}_{digest}"

def create_story(article, lang, cat):
    story_id = make_story_id(article["title"], lang, cat)
    return {
        "story_id": story_id,
        "first_seen_at": now_iso(),
        "last_seen_at": now_iso(),
        "last_selected_at": None,
        "seen_count": 1,
        "source_count": 1,
        "sources": [article["source"]],
        "headline": article["title"],
        "articles": [article],
    }

def update_story(story, article):
    story["last_seen_at"] = now_iso()
    story["seen_count"] = story.get("seen_count", 0) + 1
    if article["source"] not in story.get("sources", []):
        story.setdefault("sources", []).append(article["source"])
    story["source_count"] = len(story.get("sources", []))
    articles = story.setdefault("articles", [])
    existing_urls = {a.get("url") for a in articles if a.get("url")}
    if article.get("url") not in existing_urls:
        articles.append(article)
    story["articles"] = articles[-10:]
    story["headline"] = article["title"]

def cluster_articles(items, lang, cat):
    stories = []
    for item in items:
        matched_story = None
        best_similarity = 0.0
        for story in stories:
            representative_title = story["headline"]
            tokens_a = meaningful_tokens(item["title"], lang)
            tokens_b = meaningful_tokens(representative_title, lang)
            similarity = max(
                token_similarity(tokens_a, tokens_b),
                strong_token_similarity(tokens_a, tokens_b) * 0.45
            )
            if titles_are_same_story(item["title"], representative_title, lang):
                if similarity > best_similarity:
                    best_similarity = similarity
                    matched_story = story

        if matched_story:
            update_story(matched_story, item)
        else:
            stories.append(create_story(item, lang, cat))

    for story in stories:
        story["story_id"] = make_story_id(story["headline"], lang, cat)

    stories.sort(
        key=lambda s: (s.get("source_count", 0), s.get("seen_count", 0)),
        reverse=True
    )
    return stories

def merge_stories_into_memory(lang, cat, detected_stories):
    memory = story_memory[lang][cat]
    current_cycle = {}

    for detected in detected_stories:
        story_id = detected["story_id"]
        existing = memory.get(story_id)

        if existing is None:
            for old_id, old_story in memory.items():
                if titles_are_same_story(detected["headline"], old_story.get("headline", ""), lang):
                    existing = old_story
                    story_id = old_id
                    break

        if existing is None:
            existing = {
                "story_id": story_id,
                "first_seen_at": now_iso(),
                "last_seen_at": now_iso(),
                "last_selected_at": None,
                "seen_count": 0,
                "source_count": 0,
                "sources": [],
                "headline": detected["headline"],
                "articles": [],
            }

        existing["last_seen_at"] = now_iso()
        existing["seen_count"] = max(existing.get("seen_count", 0), detected.get("seen_count", 1))
        existing["source_count"] = max(existing.get("source_count", 0), detected.get("source_count", 1))
        existing["sources"] = list(dict.fromkeys(existing.get("sources", []) + detected.get("sources", [])))
        existing["headline"] = detected["headline"]
        existing["articles"] = detected.get("articles", existing.get("articles", []))[-10:]
        current_cycle[story_id] = existing

    for old_id, old_story in memory.items():
        if old_id not in current_cycle:
            current_cycle[old_id] = old_story

    sorted_memory = sorted(
        current_cycle.items(),
        key=lambda pair: pair[1].get("last_seen_at", ""),
        reverse=True
    )
    story_memory[lang][cat] = dict(sorted_memory[:MAX_STORIES])

    return {
        story_id: story
        for story_id, story in story_memory[lang][cat].items()
        if story_id in {s["story_id"] for s in detected_stories}
    }

def story_age_minutes(story):
    return minutes_since(story.get("first_seen_at"))

def story_last_seen_minutes(story):
    return minutes_since(story.get("last_seen_at"))

def current_story_age_minutes(lang, cat):
    return minutes_since(current_news[lang][cat].get("updated_at"))

def story_is_current(lang, cat, story_id):
    return current_news[lang][cat].get("story_id") == story_id

def rank_stories(stories, lang, cat):
    return sorted(
        stories,
        key=lambda s: (s.get("source_count", 0), s.get("seen_count", 0)),
        reverse=True
    )

def fetch_rss_items(sources):
    items = []
    seen_titles = set()
    item_id = 1

    for source in sources:
        try:
            req = urllib.request.Request(
                source["url"],
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                }
            )
            response = urllib.request.urlopen(req, context=ssl_context, timeout=8)
            feed = feedparser.parse(response.read())

            for entry in feed.entries[:ARTICLES_PER_SOURCE]:
                title = getattr(entry, "title", "").replace("\n", " ").strip()
                link = getattr(entry, "link", "").strip()

                if not title or not link:
                    continue

                if title.strip().endswith("?") or title.strip().endswith("? »") or "a-t-il" in title.lower() or "a-t-elle" in title.lower() or "pourquoi le " in title.lower():
                    continue

                t_lower = title.lower()
                if any(x in t_lower for x in [
                    "l'éco du monde", "good morning business", "les experts :", 
                    "intégrale bourse", "le grand journal de l'éco", "la quotidienne",
                    "podcast", "replay"
                ]) or re.search(r"-\s*\d{2}/\d{2}$", title.strip()):
                    continue

                norm_key = re.sub(r"[^\w\s]", "", title.lower()).strip()
                if norm_key in seen_titles:
                    continue
                seen_titles.add(norm_key)

                items.append({
                    "id": item_id,
                    "source": source["source"],
                    "title": title,
                    "url": clean_url(link),
                })
                item_id += 1
        except Exception:
            continue

    return items

def format_stories_for_prompt(stories, lang, cat):
    if not stories:
        return "NO STORIES AVAILABLE"
    blocks = []
    for index, story in enumerate(stories[:MAX_STORIES_FOR_GEMINI], start=1):
        source_names = ", ".join(story.get("sources", []))
        age = story_age_minutes(story)
        last_seen = story_last_seen_minutes(story)
        is_current = story_is_current(lang, cat, story["story_id"])
        articles = story.get("articles", [])
        article_lines = [f"- [{a['id']}] {a['source']}: {a['title']}" for a in articles[:5]]

        block = f"""STORY {index}
STORY_ID: {story['story_id']}
CURRENTLY_DISPLAYED: {"YES" if is_current else "NO"}
SOURCES: {source_names}
SOURCE_COUNT: {story.get('source_count', 0)}
SEEN_COUNT: {story.get('seen_count', 0)}
FIRST_SEEN: {age if age is not None else "unknown"} minutes ago
LAST_SEEN: {last_seen if last_seen is not None else "unknown"} minutes ago

HEADLINES:
{chr(10).join(article_lines)}""".strip()
        blocks.append(block)

    return "\n\n".join(blocks)

# ============================================================
# PROMPTS ÉDITORIAUX STRICTS
# ============================================================

CATEGORY_CRITERIA_FR = {
    "general": """PRIORITÉ NATIONALE STRICTE & ANCRAGE FRANCE
- Priorité absolue aux faits majeurs nationaux, vie publique, société, institutions et événements ayant un impact direct sur le territoire français.
- Seuil d'exception internationale STRICTISSIME : toléré UNIQUEMENT en cas d'onde de choc planétaire historique éclipsant totalement l'actualité nationale (déclenchement d'un conflit mondial, attentat historique mondial, crise sanitaire globale majeure).
- VERROU ANTI-DOUBLON (STRICT) : INTERDICTION FORMELLE d'importer une actualité étrangère ou géopolitique sous prétexte d'un angle secondaire. L'international pur relève EXCLUSIVEMENT de la rubrique Monde.
- Écarte : faits divers locaux sans portée nationale, météo ordinaire, querelles partisanes mineures.""",

    "monde": """GÉOPOLITIQUE, RELATIONS INTERNATIONALES ET CONFLITS MONDIAUX
- Événements géopolitiques majeurs, traités, conflits armés, crises démocratiques ou humanitaires mondiales hors de France.
- Priorité à l'actualité internationale à l'impact planétaire le plus déterminant aujourd'hui.
- Écarte : débats de politique intérieure française et faits divers régionaux étrangers sans portée globale.""",

    "eco": """MACROÉCONOMIE, GRANDES ENTREPRISES & IMPACT FINANCIER RÉEL
- Décisions budgétaires d'État, inflation, pouvoir d'achat, taux directeurs, réformes structurelles, fusions/acquisitions majeures et emploi.
- ÉCARTE IMPÉRATIVEMENT : joutes électorales, élections partielles ou locales, promesses de campagne et déclarations partisanes sans décision économique exécutoire immédiate.
- Écarte également : conseils en gestion de patrimoine individuel, micro-cours de bourse quotidiens et publireportages.""",

    "tech": """RUPTURES TECHNOLOGIQUES, IA ET ENJEUX INDUSTRIELS
- Percées en IA, régulation numérique, cybersécurité critique, puces/semi-conducteurs, stratégies des géants de la tech.
- Écarte strictement : promotions e-commerce, tests de gadgets, rumeurs matérielles et tutoriels produits.""",

    "sciences": """CLIMAT, BIODIVERSITÉ, SANTÉ PUBLIQUE ET RECHERCHE
- Rapports climatiques majeurs, transitions énergétiques de fond, découvertes médicales ou spatiales évaluées par les pairs.
- Écarte : astuces lifestyle/nutrition, marronniers saisonniers et météo de routine."""
}

CATEGORY_CRITERIA_US = {
    "general": """STRICT NATIONAL PROXIMITY & US INSTITUTIONS
- Absolute priority to major national news, domestic policy, federal institutions, and society events directly impacting the United States.
- STRICT INTERNATIONAL EXCEPTION: Allowed ONLY for seismic global events that completely overshadow domestic news.
- ANTI-DUPLICATE LOCK: STRICTLY FORBIDDEN to import foreign or international events using minor US angles.
- Exclude: routine local crime, isolated weather advisories, minor political infighting.""",

    "monde": """GLOBAL GEOPOLITICS, INTERNATIONAL CONFLICTS & DIPLOMACY
- Major cross-border conflicts, international treaties, global summits, humanitarian and democratic crises outside domestic US borders.
- Prioritize the single international development with the highest global consequence today.
- Exclude: purely domestic US politics and minor local foreign events.""",

    "eco": """MACROECONOMICS, CORPORATE DEVELOPMENTS & FISCAL IMPACT
- Federal Reserve policy, inflation, labor market benchmarks, national fiscal legislation, and major corporate shifts.
- STRICTLY EXCLUDE: political campaigns, local/special elections, stump speeches, and partisan claims lacking direct macroeconomic impact.
- Exclude: personal investment advice, intraday single-stock noise, and sponsored content.""",

    "tech": """TECHNOLOGICAL BREAKTHROUGHS, AI & INFRASTRUCTURE
- Frontier AI, federal antitrust & digital regulation, semiconductor supply chains, critical enterprise cybersecurity.
- Strictly exclude: consumer deals, product reviews, unverified hardware leaks, and minor app updates.""",

    "sciences": """CLIMATE SCIENCE, SPACE EXPLORATION & PEER-REVIEWED DISCOVERIES
- Landmark climate benchmarks, clean energy infrastructure, peer-reviewed medical breakthroughs, major space missions.
- Exclude: wellness/diet trends, routine weather patterns, and unverified claims."""
}

def build_prompt_fr(cat, stories_text, current):
    current_age = minutes_since(current.get("updated_at"))
    current_story_id = current.get("story_id") or "none"
    criteria = CATEGORY_CRITERIA_FR[cat]

    return f"""Tu es le rédacteur en chef d'INSTANT France pour la rubrique [{cat.upper()}].

PROMESSE PRODUIT
INSTANT montre UNE SEULE information par rubrique : celle qui compte le plus pour le lecteur en ce moment.
Boussole éditoriale : si le lecteur ne devait retenir qu’une seule information de cette rubrique en ce moment, quelle est celle qu’il regretterait le plus d’avoir manquée ?
Le principal risque est le bruit : changer de sujet trop vite dès qu'une nouvelle dépêche apparaît.
Privilégie la PERTINENCE, la VÉRIFIABILITÉ et la STABILITÉ plutôt que la nouveauté brute.

============================================================
STORIES DÉTECTÉES
============================================================
{stories_text}

============================================================
STORY ACTUELLE
============================================================
STORY_ID: {current_story_id}
HEADLINE: "{current.get("headline", "")}"
AFFICHÉE DEPUIS: {current_age if current_age is not None else "inconnu"} minutes

============================================================
MISSION & CONSENSUS ÉDITORIAL (RÈGLE ABSOLUE)
============================================================
1. CONSENSUS ÉDITORIAL ABSOLU : Choisis TOUJOURS la story portée par le plus grand nombre de sources distinctes (SOURCE_COUNT le plus élevé).
2. SELECTION DE L'ÉVÉNEMENT MAJEUR : Choisis le fait brut le plus lourd en impact direct.
3. INTERDICTION DES TITRES INTERROGATIFS OU D'OPINION : Ton titre ne doit jamais être une question ni une analyse subjective.

Choisis UNE story.
- Si STORY_ID actuel est "none" ou vide : Tu DOIS obligatoirement faire une action CHANGE pour sélectionner et rédiger la première actualité de référence.
- Si une story est déjà affichée :
  1. KEEP : La story actuelle reste le meilleur choix.
  2. CHANGE : Une autre story est nettement supérieure pour justifier un changement.

SUJET UNIQUE ET STRICT (INTERDICTION DES TITRES CHIMÈRES / HYBRIDES)
- Le titre doit porter sur UN SEUL et UNIQUE événement précis.
- INTERDICTION ABSOLUE de fusionner deux actualités distinctes dans le même titre.

{criteria}

HEADLINE
Si CHANGE, rédige un titre :
- RÉÉCRITURE OBLIGATOIRE : Interdiction de recopier le titre brut d'un flux.
- PHRASE INTÉGRALE OBLIGATOIRE : Le titre doit former une phrase syntaxiquement finie et complète. Interdiction absolue de laisser un mot ou une proposition en suspens.
- Style grand quotidien : fluide, percutant et autonome.
- Longueur cible : STRICTEMENT entre 65 et 75 caractères (espaces compris, idéalement 70 car.).
- Utilise l'apostrophe courbe (’).

SORTIE STRICTE
Retourne exactement une seule ligne :
ACTION|||STORY_ID|||HEADLINE
"""

def build_prompt_us(cat, stories_text, current):
    current_age = minutes_since(current.get("updated_at"))
    current_story_id = current.get("story_id") or "none"
    criteria = CATEGORY_CRITERIA_US[cat]

    return f"""You are the Editor-in-Chief of INSTANT US for the [{cat.upper()}] category.

PRODUCT PROMISE
INSTANT shows ONE story per category: the single most critical story that matters right now.
Editorial compass: If the reader could know only one story from this category right now, which one would they regret missing the most?
The biggest risk is noise: changing stories too often for superficial updates.
Prioritize RELEVANCE, NATIONAL PROXIMITY and STABILITY over freshness for its own sake.

============================================================
DETECTED STORIES
============================================================
{stories_text}

============================================================
CURRENT STORY
============================================================
STORY_ID: {current_story_id}
HEADLINE: "{current.get("headline", "")}"
DISPLAYED FOR: {current_age if current_age is not None else "unknown"} minutes

============================================================
MISSION & MANDATORY EDITORIAL CONSENSUS
============================================================
1. STRICT CONSENSUS MANDATE : ALWAYS select the story backed by the highest number of distinct outlets.
2. BREAKING FACTUAL IMPACT: Choose the single biggest verified hard news event over cold analysis.
3. NO QUESTIONS OR CLICKBAIT: Never output a headline formatted as a question, rumor, or subjective opinion.

Choose ONE story.
- If current STORY_ID is "none" or empty : You MUST choose ACTION CHANGE.
- If a story is already displayed :
  1. KEEP: Current story remains the best choice.
  2. CHANGE: Another story is clearly superior.

STRICT SINGLE STORY RULE (NO HYBRID / FRANKENSTEIN HEADLINES)
- The headline must focus on ONE SINGLE event.

{criteria}

HEADLINE
If CHANGE, write a headline:
- MANDATORY REWRITE: Do not copy verbatim raw RSS feed titles.
- MANDATORY COMPLETE SENTENCE: Headline must be fully grammatically self-contained. Never leave a dangling word or unfinished clause.
- Crisp, authoritative major newspaper style, completely self-contained.
- Target: STRICTLY between 65 and 75 characters (including spaces, ideally 70 chars).
- Use curly apostrophes (’ only).

STRICT OUTPUT
Return exactly one single line:
ACTION|||STORY_ID|||HEADLINE
"""

# ============================================================
# GEMINI ENGINE
# ============================================================

def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("❌ [GEMINI] GEMINI_API_KEY absente.", flush=True)
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        print(f"❌ [GEMINI] SDK error : {e}", flush=True)
        return None

def parse_gemini_decision(raw):
    if not raw:
        return None
    raw = raw.strip().replace("```", "").strip()
    parts = raw.split("|||", 2)
    if len(parts) != 3:
        return None
    action = parts[0].strip().upper()
    story_id = parts[1].strip()
    headline = sanitize_headline(parts[2])
    if action not in {"KEEP", "CHANGE"} or not story_id:
        return None
    return {"action": action, "story_id": story_id, "headline": headline}

def choose_best_article(story):
    articles = story.get("articles", [])
    return articles[-1] if articles else None

def should_block_switch(lang, cat, selected_story):
    current = current_news[lang][cat]
    current_story_id = current.get("story_id")
    if not current_story_id or selected_story["story_id"] == current_story_id:
        return False
    current_age = current_story_age_minutes(lang, cat)
    if current_age is not None and current_age < MIN_STORY_HOLD_MINUTES:
        if selected_story.get("source_count", 0) >= 4:
            return False
        return True
    return False

def evaluate_category(lang, cat, stories):
    client = get_gemini_client()
    if client is None:
        return None

    current = current_news[lang][cat]
    multi_source = [s for s in stories if s.get("source_count", 0) >= 2]
    candidate_stories = multi_source if multi_source else stories
    ranked_stories = rank_stories(candidate_stories, lang, cat)[:MAX_STORIES_FOR_GEMINI]
    if not ranked_stories:
        return None

    stories_text = format_stories_for_prompt(ranked_stories, lang, cat)
    prompt = build_prompt_fr(cat, stories_text, current) if lang == "FR" else build_prompt_us(cat, stories_text, current)

    for model_name in MODELS_TO_TRY:
        try:
            print(f"🤖 [GEMINI] [{lang} - {cat.upper()}] → {model_name}", flush=True)
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"http_options": {"timeout": 45000}}
            )
            if not response or not response.text:
                continue

            decision = parse_gemini_decision(response.text)
            if not decision:
                continue

            action, story_id, headline = decision["action"], decision["story_id"], decision["headline"]

            if action == "KEEP":
                if not current.get("story_id"):
                    continue
                print(f"↩️ [GEMINI] KEEP [{lang} - {cat}] : {current['headline'][:50]}...", flush=True)
                return {"action": "KEEP", "story_id": current["story_id"], "headline": current["headline"]}

            selected_story = next((s for s in ranked_stories if s["story_id"] == story_id), None)
            if not selected_story:
                selected_story = story_memory[lang][cat].get(story_id)

            if not selected_story:
                continue

# Pas de découpage de phrase mécanique

            if not validate_headline(headline):
                continue

            if should_block_switch(lang, cat, selected_story):
                print(f"🛑 [STABILITY] Changement bloqué (inertie) pour [{lang} - {cat}] : {headline}", flush=True)
                return {"action": "KEEP", "story_id": current.get("story_id"), "headline": current.get("headline")}

            article = choose_best_article(selected_story)
            if not article:
                continue

            print(f"🔄 [GEMINI] CHANGE [{lang} - {cat}] → {headline}", flush=True)
            return {
                "action": "CHANGE",
                "story_id": selected_story["story_id"],
                "headline": headline,
                "url": article["url"],
                "source": article["source"],
                "article_id": article["id"],
            }
        except Exception as err:
            err_msg = str(err)
            print(f"↳ Tentative {model_name} : {err_msg[:100]}...", flush=True)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                time.sleep(7.0)
            continue

    print(f"❌ [GEMINI] Aucun modèle n'a pu répondre pour [{lang} - {cat}].", flush=True)
    return None

def apply_result(lang, cat, result):
    if not result or result.get("action") != "CHANGE":
        return

    old = current_news[lang][cat].copy()
    new_story_id = result.get("story_id")
    selected_story = story_memory[lang][cat].get(new_story_id)
    if not selected_story:
        return

    current_news[lang][cat] = {
        "headline": result["headline"],
        "url": result["url"],
        "source": result["source"],
        "article_id": result["article_id"],
        "story_id": new_story_id,
        "updated_at": now_iso(),
    }

    selected_story["last_selected_at"] = now_iso()
    story_memory[lang][cat][new_story_id] = selected_story

    history[lang][cat].append({
        "changed_at": now_iso(),
        "from_story_id": old.get("story_id"),
        "from_headline": old.get("headline"),
        "to_story_id": new_story_id,
        "to_headline": result["headline"],
    })
    history[lang][cat] = history[lang][cat][-MAX_HISTORY:]

    print(f"📢 [{lang} - {cat.upper()}] {result['headline']}", flush=True)
    print(f"   ↳ {result['source']} → {result['url']}", flush=True)

# ============================================================
# CYCLE PRINCIPAL & SERVEUR HTTP
# ============================================================

refresh_lock = threading.Lock()

def process_category(lang, cat):
    try:
        sources = FEEDS[lang].get(cat, [])
        items = fetch_rss_items(sources)
        if not items:
            return

        detected_stories = cluster_articles(items, lang, cat)
        active_stories = merge_stories_into_memory(lang, cat, detected_stories)
        active_story_list = list(active_stories.values())
        if not active_story_list:
            return

        result = evaluate_category(lang, cat, active_story_list)
        apply_result(lang, cat, result)
    except Exception as e:
        print(f"⚠️ [{lang} - {cat}] Erreur : {e}", flush=True)

def check_and_update():
    if not refresh_lock.acquire(blocking=False):
        print("⏳ [REFRESH] Évaluation déjà en cours.", flush=True)
        return

    try:
        print(f"\n[{time.strftime('%H:%M:%S')}] --- ÉVALUATION INSTANT MULTI-CATÉGORIES ---", flush=True)
        for lang in ["FR", "US"]:
            for cat in CATEGORIES:
                process_category(lang, cat)
                time.sleep(7.0)
        save_state()
        print("--- FIN ÉVALUATION ---\n", flush=True)
    finally:
        refresh_lock.release()

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

        return super().do_GET()

def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), InstantAppHandler) as httpd:
        print(f"🌐 [SERVER] Serveur actif sur le port {port}", flush=True)
        httpd.serve_forever()

# ============================================================
# DÉMARRAGE
# ============================================================

if __name__ == "__main__":
    load_state()

    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=check_and_update, daemon=True).start()

    schedule.every().hour.at(":00").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())
    schedule.every().hour.at(":30").do(lambda: threading.Thread(target=check_and_update, daemon=True).start())

    print("⏱️ [SCHEDULER] Refresh actif toutes les 30 minutes\n", flush=True)

    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 [STOP] Arrêt demandé.", flush=True)
            break
        except Exception as e:
            print(f"⚠️ [MAIN LOOP] {e}", flush=True)
            time.sleep(7.0)
