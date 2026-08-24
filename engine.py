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

# ============================================================
# CONFIGURATION
# ============================================================

os.environ["TZ"] = "Europe/Paris"
if hasattr(time, "tzset"):
    time.tzset()

print("--> [START] Instant V2 — Story Engine (Gemini 3.6 / 3.5 / Lite-latest)", flush=True)

STATE_FILE = "instant_state.json"
MAX_STORIES = 30
MAX_HISTORY = 20
MIN_STORY_HOLD_MINUTES = 20
ARTICLES_PER_SOURCE = 7
MAX_STORIES_FOR_GEMINI = 12

MODELS_TO_TRY = [
    os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
    "gemini-3.5-flash",
    "gemini-flash-lite-latest",
]

# ============================================================
# ÉTAT COURANT
# ============================================================

current_news = {
    "FR": {
        "headline": "Analyse en cours…",
        "url": "https://www.lemonde.fr",
        "source": "Le Monde",
        "article_id": None,
        "story_id": None,
        "updated_at": None,
    },
    "US": {
        "headline": "Analysis in progress…",
        "url": "https://www.nytimes.com",
        "source": "NY Times",
        "article_id": None,
        "story_id": None,
        "updated_at": None,
    },
}

story_memory = {
    "FR": {},
    "US": {},
}

history = {
    "FR": [],
    "US": [],
}

# ============================================================
# PERSISTANCE
# ============================================================

def load_state():
    global current_news, story_memory, history
    if not os.path.exists(STATE_FILE):
        print("💾 [STATE] Aucun état précédent.", flush=True)
        return

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)

        if not isinstance(saved, dict):
            return

        saved_current = saved.get("current_news")
        saved_stories = saved.get("story_memory")
        saved_history = saved.get("history")

        if saved_current is None and "FR" in saved:
            saved_current = saved

        if isinstance(saved_current, dict):
            for lang in ["FR", "US"]:
                if isinstance(saved_current.get(lang), dict):
                    current_news[lang].update(saved_current[lang])

        if isinstance(saved_stories, dict):
            for lang in ["FR", "US"]:
                if isinstance(saved_stories.get(lang), dict):
                    story_memory[lang].update(saved_stories[lang])

        if isinstance(saved_history, dict):
            for lang in ["FR", "US"]:
                if isinstance(saved_history.get(lang), list):
                    history[lang] = saved_history[lang][-MAX_HISTORY:]

        print("💾 [STATE] État restauré depuis le disque.", flush=True)
    except Exception as e:
        print(f"⚠️ [STATE] Erreur lecture : {e}", flush=True)

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
    except Exception as e:
        print(f"⚠️ [STATE] Erreur sauvegarde : {e}", flush=True)

# ============================================================
# UTILITAIRES TEMPS
# ============================================================

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
# UTILITAIRES TEXTE & CLUSTERING
# ============================================================

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

def normalize_text(text):
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text, flags=re.UNICODE)
    text = text.replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()

def meaningful_tokens(text, lang):
    normalized = normalize_text(text)
    stopwords = STOPWORDS_FR if lang == "FR" else STOPWORDS_EN
    tokens = []
    for token in normalized.split():
        if len(token) < 3 or token in stopwords or token in GENERIC_WORDS or token.isdigit():
            continue
        tokens.append(token)
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

def make_story_id(title, lang):
    tokens = sorted(meaningful_tokens(title, lang))
    signature = "|".join(tokens[:8])
    if not signature:
        signature = normalize_text(title)[:100]
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
    return f"{lang.lower()}_{digest}"

def create_story(article, lang):
    story_id = make_story_id(article["title"], lang)
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

def cluster_articles(items, lang):
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
            stories.append(create_story(item, lang))

    for story in stories:
        story["story_id"] = make_story_id(story["headline"], lang)

    stories.sort(
        key=lambda s: (s.get("source_count", 0), s.get("seen_count", 0)),
        reverse=True
    )
    return stories

def merge_stories_into_memory(lang, detected_stories):
    memory = story_memory[lang]
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
    story_memory[lang] = dict(sorted_memory[:MAX_STORIES])

    return {
        story_id: story
        for story_id, story in story_memory[lang].items()
        if story_id in {s["story_id"] for s in detected_stories}
    }

def story_age_minutes(story):
    return minutes_since(story.get("first_seen_at"))

def story_last_seen_minutes(story):
    return minutes_since(story.get("last_seen_at"))

def current_story_age_minutes(lang):
    return minutes_since(current_news[lang].get("updated_at"))

def story_is_current(lang, story_id):
    return current_news[lang].get("story_id") == story_id

def basic_story_score(story):
    sources = min(story.get("source_count", 0), 5)
    seen_count = min(story.get("seen_count", 0), 5)
    return sources * 10 + seen_count * 3

def rank_stories(stories, lang):
    ranked = []
    for story in stories:
        score = basic_story_score(story)
        if story_is_current(lang, story["story_id"]):
            score += 8
        ranked.append((score, story))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [story for score, story in ranked]

# ============================================================
# RSS FETCH
# ============================================================

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

            for entry in feed.entries[:ARTICLES_PER_SOURCE]:
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

def format_stories_for_prompt(stories, lang):
    if not stories:
        return "NO STORIES AVAILABLE"
    blocks = []
    for index, story in enumerate(stories[:MAX_STORIES_FOR_GEMINI], start=1):
        source_names = ", ".join(story.get("sources", []))
        age = story_age_minutes(story)
        last_seen = story_last_seen_minutes(story)
        is_current = story_is_current(lang, story["story_id"])
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
# PROMPTS
# ============================================================

def build_prompt_fr(stories_text, current):
    current_age = minutes_since(current.get("updated_at"))
    current_story_id = current.get("story_id") or "none"

    return f"""Tu es le rédacteur en chef d'INSTANT, une application de news minimaliste.

PROMESSE PRODUIT
INSTANT montre UNE SEULE information : celle qui compte le plus pour le lecteur maintenant.
Le principal risque est le bruit : changer de sujet trop vite dès qu'une nouvelle dépêche apparaît.
Privilégie la PERTINENCE et la STABILITÉ plutôt que la nouveauté brute.

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
MISSION
============================================================
Choisis UNE story. Deux possibilités :
1. KEEP : La story actuellement affichée reste le meilleur choix.
2. CHANGE : Une autre story est nettement supérieure pour justifier un changement.

Pour changer, il faut une vraie supériorité éditoriale (urgence nationale, gravité supérieure). Si deux stories sont proches, KEEP.

PRIORITÉS
- Catastrophe majeure, guerre, attaque, crise géopolitique majeure.
- Décision gouvernementale aux conséquences immédiates, loi majeure, décision suprême de justice.
- Événement économique ou institutionnel majeur affectant directement la population.

MÉTÉO
Retiens les événements météo uniquement en cas de crise de sécurité publique avérée (victimes, destructions majeures, paralysie, alerte ROUGE absolue).
Écarte impérativement les vigilances orange ordinaires et la météo de routine.

JUSTICE
Retiens uniquement les affaires visant des personnalités de tout premier plan de l'État ou arrêts suprêmes. Écarte les faits divers et affaires de particuliers.

EXCLUSIONS
Écarte : météo ordinaire, politique spéculative, petites phrases, faits divers locaux, sport ordinaire, culture, lifestyle.

HEADLINE
Si CHANGE, rédige un titre :
- Direct, factuel, dense, percutant.
- Longueur cible : 65 à 75 caractères (idéalement ~70 car.).
- Structure : CE QUI S'EST PASSÉ + ACTEURS + CONSÉQUENCE.
- Utilise l'apostrophe courbe (’).

SORTIE STRICTE
Retourne exactement une seule ligne :
ACTION|||STORY_ID|||HEADLINE

Exemples :
KEEP|||{current_story_id}|||{current.get("headline", "")}
CHANGE|||fr_abc123def456|||Titre réécrit de 65 à 75 caractères factuel et percutant"""

def build_prompt_us(stories_text, current):
    current_age = minutes_since(current.get("updated_at"))
    current_story_id = current.get("story_id") or "none"

    return f"""You are the Editor-in-Chief of INSTANT, a minimalist news app.

PRODUCT PROMISE
INSTANT shows ONE story: the single most critical story that matters right now.
The biggest risk is noise: changing stories too often for superficial updates.
Prioritize RELEVANCE and STABILITY over freshness for its own sake.

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
MISSION
============================================================
Choose ONE story. Two actions:
1. KEEP: Current story remains the best choice.
2. CHANGE: Another story is clearly superior and justifies a switch.

If two stories are close in importance, KEEP.

PRIORITIES
- Major disasters, war, attacks, major geopolitical crises.
- Major government/executive actions, legislation passed, Supreme Court decisions.
- Major economic shocks or events affecting everyday life for the US population.

WEATHER
Include weather ONLY for genuine catastrophic emergencies (mass casualties, major destruction, Red-level alerts).
Strictly exclude routine seasonal advisories and summer storms.

LEGAL & POLITICS
Include legal proceedings ONLY for top-tier national leaders or Supreme Court decisions. Exclude ordinary criminal cases, local crimes, and political horse-race speculation.

HEADLINE
If CHANGE, write a US headline:
- Direct, factual, authoritative, information-dense.
- Target: 65 to 75 characters (approx. ~70 chars).
- Structure: WHAT HAPPENED + KEY ACTOR + KEY CONSEQUENCE.
- Use curly apostrophes (’ only).

STRICT OUTPUT
Return exactly one single line:
ACTION|||STORY_ID|||HEADLINE

Examples:
KEEP|||{current_story_id}|||{current.get("headline", "")}
CHANGE|||us_abc123def456|||Factual and authoritative headline between 65 and 75 chars"""

# ============================================================
# GEMINI ENGINE
# ============================================================

def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ [GEMINI] GEMINI_API_KEY absente.", flush=True)
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        print(f"❌ [GEMINI] Erreur SDK : {e}", flush=True)
        return None

def parse_gemini_decision(raw):
    if not raw:
        return None
    raw = raw.strip().replace("```", "").strip()
    parts = raw.split("|||", 2)
    if len(parts) != 3:
        print(f"⚠️ [GEMINI] Format invalide : {raw[:150]}", flush=True)
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

def should_block_switch(lang, selected_story):
    current = current_news[lang]
    current_story_id = current.get("story_id")
    if not current_story_id or selected_story["story_id"] == current_story_id:
        return False
    current_age = current_story_age_minutes(lang)
    if current_age is not None and current_age < MIN_STORY_HOLD_MINUTES:
        if selected_story.get("source_count", 0) >= 4:
            return False
        return True
    return False

def evaluate_news(lang, stories):
    client = get_gemini_client()
    if client is None:
        return None

    current = current_news[lang]
    ranked_stories = rank_stories(stories, lang)[:MAX_STORIES_FOR_GEMINI]
    if not ranked_stories:
        return None

    stories_text = format_stories_for_prompt(ranked_stories, lang)
    prompt = build_prompt_fr(stories_text, current) if lang == "FR" else build_prompt_us(stories_text, current)

    for model_name in MODELS_TO_TRY:
        try:
            print(f"🤖 [GEMINI] {lang} → {model_name}", flush=True)
            response = client.models.generate_content(model=model_name, contents=prompt)
            if not response or not response.text:
                continue

            decision = parse_gemini_decision(response.text)
            if not decision:
                continue

            action, story_id, headline = decision["action"], decision["story_id"], decision["headline"]

            if action == "KEEP":
                if not current.get("story_id"):
                    continue
                print(f"↩️ [GEMINI] KEEP {lang} : {current['headline']}", flush=True)
                return {"action": "KEEP", "story_id": current["story_id"], "headline": current["headline"]}

            selected_story = next((s for s in ranked_stories if s["story_id"] == story_id), None)
            if not selected_story:
                selected_story = story_memory[lang].get(story_id)

            if not selected_story or not validate_headline(headline):
                continue

            if should_block_switch(lang, selected_story):
                print(f"🛑 [STABILITY] Changement bloqué (inertie) pour {lang} : {headline}", flush=True)
                return {"action": "KEEP", "story_id": current.get("story_id"), "headline": current.get("headline")}

            article = choose_best_article(selected_story)
            if not article:
                continue

            print(f"🔄 [GEMINI] CHANGE {lang} → {headline}", flush=True)
            return {
                "action": "CHANGE",
                "story_id": selected_story["story_id"],
                "headline": headline,
                "url": article["url"],
                "source": article["source"],
                "article_id": article["id"],
            }
        except Exception as err:
            print(f"↳ Tentative {model_name} : {str(err)[:120]}...", flush=True)
            continue

    print(f"❌ [GEMINI] Aucun modèle n'a pu répondre pour {lang}.", flush=True)
    return None

def apply_result(lang, result):
    if not result or result.get("action") != "CHANGE":
        return

    old = current_news[lang].copy()
    new_story_id = result.get("story_id")
    selected_story = story_memory[lang].get(new_story_id)
    if not selected_story:
        return

    current_news[lang] = {
        "headline": result["headline"],
        "url": result["url"],
        "source": result["source"],
        "article_id": result["article_id"],
        "story_id": new_story_id,
        "updated_at": now_iso(),
    }

    selected_story["last_selected_at"] = now_iso()
    story_memory[lang][new_story_id] = selected_story

    history[lang].append({
        "changed_at": now_iso(),
        "from_story_id": old.get("story_id"),
        "from_headline": old.get("headline"),
        "to_story_id": new_story_id,
        "to_headline": result["headline"],
    })
    history[lang] = history[lang][-MAX_HISTORY:]

    print(f"📢 [{lang}] {result['headline']}", flush=True)
    print(f"   ↳ {result['source']} → {result['url']}", flush=True)

# ============================================================
# HTML & HTTP SERVER
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

        if self.path.startswith("/api/history"):
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(history, ensure_ascii=False).encode("utf-8"))
            return

        if self.path.startswith("/api/stories"):
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(json.dumps(story_memory, ensure_ascii=False).encode("utf-8"))
            return

        if self.path == "/manifest.json":
            manifest = {
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
            self.wfile.write(json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
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
# CYCLE PRINCIPAL
# ============================================================

refresh_lock = threading.Lock()

def process_language(lang, sources):
    try:
        items = fetch_rss_items(sources)
        print(f"📰 [{lang}] {len(items)} articles récupérés", flush=True)
        if not items:
            return

        detected_stories = cluster_articles(items, lang)
        print(f"🧩 [{lang}] {len(items)} articles → {len(detected_stories)} stories", flush=True)

        active_stories = merge_stories_into_memory(lang, detected_stories)
        active_story_list = list(active_stories.values())
        if not active_story_list:
            return

        result = evaluate_news(lang, active_story_list)
        apply_result(lang, result)
    except Exception as e:
        print(f"⚠️ [{lang}] Erreur : {e}", flush=True)

def check_and_update():
    if not refresh_lock.acquire(blocking=False):
        print("⏳ [REFRESH] Évaluation déjà en cours.", flush=True)
        return

    try:
        print(f"\n[{time.strftime('%H:%M:%S')}] --- ÉVALUATION INSTANT V2 ---", flush=True)
        process_language("FR", SOURCES_FR)
        process_language("US", SOURCES_US)
        save_state()
        update_html_files()
        print("--- FIN ÉVALUATION ---\n", flush=True)
    finally:
        refresh_lock.release()

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
