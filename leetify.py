import os
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# CONFIG
# ============================================================
API = "https://api.cs-prod.leetify.com/api/games"
HISTORY_URL = "https://api.cs-prod.leetify.com/api/v2/games/history"

LEETIFY_TOKEN = "PUT_YOUR_TOKEN_HERE"

BASE_DIR = "leetify"
MAX_WORKERS = 3   # SAFE for Leetify

HEADERS = {
    "Authorization": f"Bearer {LEETIFY_TOKEN}"
}

# ============================================================
# LOCAL LLM CONFIG (OLLAMA)
# ============================================================
OLLAMA_MODEL = "llama3.1:8b"
OLLAMA_URL = "http://localhost:11434/api/generate"

LLM_MATCH_PROMPT = """
You are a CS2 performance analyst.

Analyze the following match report.
Focus on:
- Player consistency
- Strengths and weaknesses
- Impact rounds
- Tactical or economic patterns

Be concise but insightful.
"""

LLM_GLOBAL_PROMPT = """
You are a CS2 analyst.

Given multiple match analyses, provide:
- Overall trends
- Repeating strengths
- Repeating weaknesses
- Suggested improvement focus areas

Summarize clearly.
"""

# ============================================================
# SESSION (retry + backoff)
# ============================================================
session = requests.Session()
session.headers.update(HEADERS)

retry = Retry(
    total=5,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)

adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)

# ============================================================
# LOCAL LLM CALL
# ============================================================
def run_local_llm(prompt, content):
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{prompt}\n\n{content}",
        "stream": False,
    }

    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=180)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except requests.RequestException as e:
        return f"LLM ERROR: {e}"

# ============================================================
# FORMATTERS
# ============================================================
def section(title):
    return f"\n{'=' * 8} {title.upper()} {'=' * 8}\n"

def format_clutches(data):
    lines = []
    for c in data:
        handicap = abs(c["handicap"]) + 1
        result = "WON" if c["clutchesWon"] else "LOST"
        trade = " | Trade start" if c.get("startedWithTrade") else ""
        lines.append(
            f"Round {c['roundNumber']} | Team {c['teamNumber']} | "
            f"Steam {c['steam64Id']} | 1v{handicap} | "
            f"{result} | Kills {c['totalKills']}{trade}"
        )
    return "\n".join(lines)

def format_opening_duels(data):
    lines = []
    for e in data:
        t = f"{e['roundTime']//60}:{e['roundTime']%60:02d}"
        traded = "Traded" if e.get("traded") else "Not traded"
        weapon = e.get("attackerWeapon", {}).get("itemName", "Unknown")
        lines.append(
            f"Round {e['round']} @ {t} | "
            f"{e['attackerName']} vs {e['victimName']} | "
            f"{weapon} | {traded}"
        )
    return "\n".join(lines)

def timeline(players, label, delta_label):
    lines = [label]
    for p in players:
        lines.append(f"{p['name']} ({p['steam64Id']})")
        prev = 0
        for r, v in sorted((int(r), v) for r, v in p["rounds"].items()):
            if v > prev:
                lines.append(f"  Round {r}: {v-prev} {delta_label}")
            prev = v
        lines.append("")
    return "\n".join(lines).rstrip()

def format_kills_timeline(d): return timeline(d["players"], "Kills Timeline", "kill(s)")
def format_deaths_timeline(d): return timeline(d["players"], "Deaths Timeline", "death")
def format_damage_timeline(d): return timeline(d["players"], "Damage Timeline", "damage")
def format_enemies_flashed_timeline(d): return timeline(d["players"], "Enemies Flashed", "enemy(s)")

def format_awp_kills(d):
    lines = ["AWP Kills Timeline"]
    for p in d.get("players", []):
        prev = 0
        lines.append(f"{p['name']} ({p['steam64Id']})")
        for r, v in sorted((int(r), v) for r, v in p["rounds"].items()):
            if v > prev:
                lines.append(f"  Round {r}: +{v-prev} (total {v})")
            prev = v
        lines.append("")
    return "\n".join(lines).rstrip()

def format_round_difference_timeline(d):
    lines = ["Round Difference Timeline"]
    for t in d.get("teams", []):
        lines.append(f"Team {t['initialTeamNumber']}")
        prev = 0
        for r, diff in sorted((int(r), v) for r, v in t["rounds"].items()):
            outcome = "WON" if diff > prev else "LOST" if diff < prev else "NO CHANGE"
            lines.append(f"  Round {r}: {outcome} (diff {diff})")
            prev = diff
        lines.append("")
    return "\n".join(lines).rstrip()

def format_team_economy_timeline(d):
    lines = ["Team Economy Timeline"]
    for t in d.get("teams", []):
        lines.append(f"Team {t['initialTeamNumber']}")
        prev = None
        for r, money in sorted((int(r), v) for r, v in t["rounds"].items()):
            delta = "—" if prev is None else f"{money-prev:+}"
            buy = (
                "ECO" if money < 10000 else
                "FORCE" if money < 20000 else
                "HALF" if money < 30000 else
                "FULL"
            )
            lines.append(f"  Round {r}: ${money} | {buy} | Δ {delta}")
            prev = money
        lines.append("")
    return "\n".join(lines).rstrip()

def format_your_match(d):
    lines = [
        f"Steam64: {d.get('steam64Id')}",
        f"Tracked Matches: {d.get('recentMatchCount')}",
        ""
    ]
    for stat in d.get("identityStats", []):
        lines.append(f"{stat['skillId']}: {stat['value']} (avg {round(stat['average'],3)})")
    return "\n".join(lines)

# ============================================================
# ENDPOINT REGISTRY
# ============================================================
ENDPOINTS = [
    ("Your Match Summary", "/your-match", format_your_match, dict),
    ("Opening Duels", "/opening-duels", format_opening_duels, list),
    ("Clutches", "/clutches", format_clutches, list),
    ("Kills Timeline", "/timelines/kills", format_kills_timeline, dict),
    ("Deaths Timeline", "/timelines/deaths", format_deaths_timeline, dict),
    ("Damage Timeline", "/timelines/damage", format_damage_timeline, dict),
    ("AWP Kills", "/timelines/awp-kills", format_awp_kills, dict),
    ("Enemies Flashed", "/timelines/enemies-flashed", format_enemies_flashed_timeline, dict),
    ("Round Difference", "/timelines/round-difference", format_round_difference_timeline, dict),
    ("Team Economy", "/timelines/team-economy", format_team_economy_timeline, dict),
]

# ============================================================
# BUILD MATCH REPORT + LLM ANALYSIS
# ============================================================
def build_match_report(match_id):
    report = [f"MATCH ID: {match_id}\n"]

    for title, suffix, formatter, expected in ENDPOINTS:
        url = f"{API}/{match_id}{suffix}"

        try:
            r = session.get(url, timeout=(5, 20))
        except requests.RequestException as e:
            report.append(section(title))
            report.append(f"Request failed: {e}\n")
            continue

        if r.status_code != 200:
            report.append(section(title))
            report.append(f"HTTP {r.status_code}\n")
            continue

        try:
            data = r.json()
        except ValueError:
            report.append(section(title))
            report.append("Invalid JSON\n")
            continue

        if not isinstance(data, expected):
            report.append(section(title))
            report.append("Unexpected data format\n")
            continue

        report.append(section(title))
        report.append(formatter(data))
        report.append("")

    match_dir = os.path.join(BASE_DIR, match_id)
    os.makedirs(match_dir, exist_ok=True)

    report_path = os.path.join(match_dir, "match_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report).rstrip())

    # ==========================
    # PER-MATCH LOCAL LLM
    # ==========================
    with open(report_path, "r", encoding="utf-8") as f:
        report_text = f.read()

    llm_analysis = run_local_llm(LLM_MATCH_PROMPT, report_text)

    with open(os.path.join(match_dir, "llm_analysis.txt"), "w", encoding="utf-8") as f:
        f.write(llm_analysis)

    return f"✅ {match_id}"

# ============================================================
# MAIN
# ============================================================
history = session.get(HISTORY_URL, timeout=10).json()
match_ids = [g["id"] for g in history.get("games", [])]

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = [pool.submit(build_match_report, mid) for mid in match_ids]
    for f in as_completed(futures):
        print(f.result())

# ============================================================
# GLOBAL LLM SUMMARY
# ============================================================
all_llm_reports = []

for mid in match_ids:
    path = os.path.join(BASE_DIR, mid, "llm_analysis.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            all_llm_reports.append(f"Match {mid}:\n{f.read()}")

global_summary = run_local_llm(
    LLM_GLOBAL_PROMPT,
    "\n\n".join(all_llm_reports)
)

with open(os.path.join(BASE_DIR, "GLOBAL_LLM_SUMMARY.txt"), "w", encoding="utf-8") as f:
    f.write(global_summary)

print("🚀 All match reports and LLM summaries generated")
