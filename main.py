from flask import Flask, request, jsonify
import requests
import os
import json

app = Flask(__name__)

# Replace with your actual credentials
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN ")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL")
JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")


@app.route("/jira-webhook", methods=["POST"])
def jira_webhook():
    data = request.json

    try:
        issue_key = data["issue"]["key"]
        description = data["issue"]["fields"].get("description", "")

        # Prepare prompt for OpenAI
        prompt = f"""Extract a clean, clear Jira ticket title and steps to reproduce from the following description:

\"\"\"
{description}
\"\"\"

Respond in JSON format exactly like this:
{{
  "title": "<Improved title>",
  "steps": ["Step 1", "Step 2", "..."]
}}"""

        # Call OpenAI Chat Completion API
        openai_resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-3.5-turbo",  # or "gpt-4"
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }
        )

        openai_resp.raise_for_status()
        openai_content = openai_resp.json()["choices"][0]["message"]["content"]

        # Parse OpenAI JSON response
        parsed = json.loads(openai_content)
        new_title = parsed.get("title", "No Title Generated")
        steps = parsed.get("steps", [])
        new_description = "### Steps to Reproduce:\n" + "\n".join([f"{i+1}. {step}" for i, step in enumerate(steps)])

        # Update Jira issue
        update_resp = requests.put(
            f"{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}",
            auth=(JIRA_USER_EMAIL, JIRA_API_TOKEN),
            headers={"Content-Type": "application/json"},
            json={
                "fields": {
                    "summary": new_title,
                    "description": new_description
                }
            }
        )
        update_resp.raise_for_status()

        return jsonify({"status": "success", "jira_response": update_resp.json()})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    # Run on all interfaces, port 5000
    app.run(host="0.0.0.0", port=8000)