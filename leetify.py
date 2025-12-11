import json
import requests
import os
import subprocess

API = "https://api.cs-prod.leetify.com/api/games"
TOKEN = "USER_TOKEN"

headers = {"Authorization": f"Bearer {TOKEN}"}

tURL = "https://api.cs-prod.leetify.com/api/v2/games/history"
t = requests.get(tURL, headers=headers)
data = t.json()

match_ids = [game.get("id") for game in data.get("games", []) if "id" in game]

all_analyses = []  # collect per-file analyses here

for match_id in match_ids:
    endpoints = [
        f"{API}/{match_id}/your-match",
        f"{API}/{match_id}",
        f"{API}/{match_id}/opening-duels",
        f"{API}/{match_id}/clutches",
    ]

    match_folder = os.path.join("leetify", match_id)
    os.makedirs(match_folder, exist_ok=True)

    for url in endpoints:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            api_path = url[url.find("/api/"):]
            filename = api_path.replace("/", "_").strip("_") + ".json"
            filepath = os.path.join(match_folder, filename)

            with open(filepath, "w") as f:
                json.dump(r.json(), f, indent=4)

            # --- Analyze with local LLM ---
            with open(filepath) as f:
                content = f.read()

            prompt = f"Analyze this CS2 match JSON and summarize key player performance:\n\n{content}"
            result = subprocess.run(
                ["ollama", "run", "llama2"],
                input=prompt.encode("utf-8"),
                capture_output=True
            )
            analysis = result.stdout.decode("utf-8")

            # Save analysis per file
            analysis_file = filepath.replace(".json", "_analysis.txt")
            with open(analysis_file, "w") as f:
                f.write(analysis)

            # Collect for global summary
            all_analyses.append(f"{filename}:\n{analysis}\n")

# --- Global summary step ---
global_prompt = "Create a global summary of these individual match analyses:\n\n" + "\n\n".join(all_analyses)

global_result = subprocess.run(
    ["ollama", "run", "llama2"],
    input=global_prompt.encode("utf-8"),
    capture_output=True
)
global_summary = global_result.stdout.decode("utf-8")

# Save global summary
with open("leetify/global_summary.txt", "w") as f:
    f.write(global_summary)

print("Done with global analysis!")
