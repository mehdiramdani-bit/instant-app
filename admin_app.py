import json
import os
import subprocess
import feedparser
from flask import Flask, render_template_string, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "instant-news-local-secret"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "news_categories.json")

FEEDS = {
    "FR": {
        "general": "https://www.lemonde.fr/rss/une.xml",
        "monde": "https://www.courrierinternational.com/feed/all/rss.xml",
        "eco": "https://www.lesechos.fr/rss/rss_une.xml",
        "tech": "https://www.usine-digitale.fr/rss",
        "sciences": "https://www.pourlascience.fr/rss/toutes-les-actualites.xml",
    },
    "US": {
        "general": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "monde": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "eco": "https://feeds.bloomberg.com/economics/news.rss",
        "tech": "https://techcrunch.com/feed/",
        "sciences": "https://www.sciencedaily.com/rss/all.xml",
    },
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Instant News — Back-office</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --accent: #38bdf8; --border: #334155; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 2rem; margin: 0; }
        .container { max-width: 1000px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 1rem; margin-bottom: 2rem; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }
        h2 { margin-top: 0; color: var(--accent); font-size: 1.25rem; }
        .rss-list { margin: 1rem 0; background: #0b1120; border-radius: 6px; padding: 0.75rem; border: 1px solid #1e293b; }
        .rss-item { padding: 0.5rem 0.25rem; border-bottom: 1px solid #1e293b; cursor: pointer; color: #cbd5e1; font-size: 0.9rem; }
        .rss-item:last-child { border-bottom: none; }
        .rss-item:hover { color: var(--accent); background: rgba(56, 189, 248, 0.05); }
        .form-group { margin-bottom: 1rem; }
        label { display: block; margin-bottom: 0.4rem; font-size: 0.85rem; color: #94a3b8; font-weight: 500; }
        input[type="text"] { width: 100%; padding: 0.6rem; border-radius: 4px; border: 1px solid var(--border); background: #0f172a; color: white; box-sizing: border-box; }
        button { background: var(--accent); color: #0f172a; border: none; padding: 0.6rem 1.2rem; border-radius: 4px; font-weight: bold; cursor: pointer; }
        .btn-git { background: #22c55e; color: white; padding: 0.75rem 1.5rem; font-size: 1rem; }
        .flash { background: #065f46; color: #d1fae5; padding: 0.75rem; border-radius: 4px; margin-bottom: 1rem; }
    </style>
</head>
<body>
<div class="container">
    <header>
        <h1>⚡ Instant News — Back-office</h1>
        <form method="POST" action="/push-git">
            <button type="submit" class="btn-git">🚀 Enregistrer & Pousser sur Render</button>
        </form>
    </header>

    {% with messages = get_flashed_messages() %}
        {% if messages %}
            {% for message in messages %}
                <div class="flash">{{ message }}</div>
            {% endfor %}
        {% endif %}
    {% endwith %}

    <form method="POST" action="/save-all">
        {% for lang, cats in feeds.items() %}
            {% for cat, url in cats.items() %}
                {% set key = lang + '-' + cat %}
                <div class="card">
                    <h2>[{{ lang }}] {{ cat | upper }}</h2>
                    <div class="form-group">
                        <label>Titre actuel publié :</label>
                        <input type="text" name="{{ key }}_headline" id="{{ key }}_headline" value="{{ current_data.get(lang, {}).get(cat, {}).get('headline', '') }}" required>
                    </div>
                    <div class="form-group">
                        <label>Source :</label>
                        <input type="text" name="{{ key }}_source" id="{{ key }}_source" value="{{ current_data.get(lang, {}).get(cat, {}).get('source', '') }}">
                    </div>
                    <div class="rss-list">
                        <label>Dépêches RSS récentes (cliquer pour charger) :</label>
                        {% set items = rss_items.get(key, []) %}
                        {% if items %}
                            {% for item in items %}
                                <div class="rss-item" onclick="document.getElementById('{{ key }}_headline').value = '{{ item.title | replace("'", "\\\\'") }}'; document.getElementById('{{ key }}_source').value = '{{ item.source }}';">
                                    📌 {{ item.title }}
                                </div>
                            {% endfor %}
                        {% else %}
                            <div style="color: #64748b; font-size: 0.85rem; padding: 0.25rem;">Aucun flux récupéré pour le moment.</div>
                        {% endif %}
                    </div>
                </div>
            {% endfor %}
        {% endfor %}
        <button type="submit">💾 Sauvegarder les modifications en local</button>
    </form>
</div>
</body>
</html>
"""

def fetch_rss_sample(url, limit=4):
    try:
        # User-Agent navigateur pour éviter les blocages 403
        d = feedparser.parse(url, agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")
        items = []
        source_name = url.split("/")[2].replace("www.", "")
        for entry in d.entries[:limit]:
            title = entry.get("title", "").strip()
            if title:
                items.append({"title": title, "source": source_name})
        return items
    except Exception as e:
        print(f"Erreur RSS {url}: {e}")
        return []

def load_data():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"FR": {}, "US": {}}

@app.route("/")
def index():
    current_data = load_data()
    rss_items = {}
    for lang, cats in FEEDS.items():
        for cat, url in cats.items():
            rss_items[f"{lang}-{cat}"] = fetch_rss_sample(url, limit=4)
    return render_template_string(HTML_TEMPLATE, feeds=FEEDS, current_data=current_data, rss_items=rss_items)

@app.route("/save-all", methods=["POST"])
def save_all():
    data = load_data()
    for lang, cats in FEEDS.items():
        if lang not in data:
            data[lang] = {}
        for cat in cats.keys():
            key = f"{lang}-{cat}"
            headline = request.form.get(f"{key}_headline", "").strip()
            if headline.endswith("."):
                headline = headline[:-1]
            source = request.form.get(f"{key}_source", "").strip()
            data[lang][cat] = {
                "headline": headline,
                "source": source,
                "updated_at": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip()
            }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    flash("✅ Modifications enregistrées localement dans news_categories.json")
    return redirect(url_for("index"))

@app.route("/push-git", methods=["POST"])
def push_git():
    try:
        subprocess.run(["git", "add", "news_categories.json"], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "commit", "-m", "curation: mise a jour manuelle des titres"], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=BASE_DIR, check=True)
        flash("🚀 Déploiement déclenché sur Render avec succès !")
    except Exception as e:
        flash(f"⚠️ Erreur Git : {e}")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(port=5001, debug=True)
