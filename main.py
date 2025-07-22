from flask import Flask, request, jsonify
import requests
import os
import json
import base64
import logging

app = Flask(__name__)

# Replace with your actual credentials
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL")
JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")
print(JIRA_USER_EMAIL)
print(JIRA_API_TOKEN)


@app.route("/jira-webhook", methods=["POST"])
def jira_webhook():
    data = request.json
    print("Received payload:", data)  # <--- This line logs the JSON payload

    if not data:
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    try:
        issue = data.get("issue")
        if not issue or "key" not in issue:
            return jsonify({"status": "error", "message": "Missing issue or issue key"}), 400
        issue_key = issue["key"]
        print("Issue key:", issue_key)
        description = issue["fields"].get("description", "")
        print("Description:", description)
        if not description:
            return jsonify({"status": "skipped", "message": "Empty description. Skipping OpenAI call."}), 200

        if not issue_key:
            return jsonify({"status": "error", "message": "Missing issue key"}), 400


        # Prepare prompt for OpenAI
        prompt = f"""You are an experienced Jira assistant helping to write high-quality, detailed issue reports.
                     Given the following user-written bug report, your task is to:
                        1. Create a professional Jira issue title.
                        2. Write a detailed description including:
                                    - A clear summary of the issue
                                    - Fully detailed step-by-step reproduction steps
                                    - Specific form fields (e.g. project name, owner, description) where relevant
                                    - Expected and actual results

If the user did not specify exact field names, you may intelligently infer commonly required fields in enterprise applications.
Input:

\"\"\"
{description}
\"\"\"

Respond in JSON format:
{{
  "title": "<Improved title>",
  "description": "### Summary:\\n<summary>\\n\\n### Steps to Reproduce:\\n1. <step>\\n2. <step>\\n...\\n\\n### Expected Result:\\n<expected>\\n\\n### Actual Result:\\n<actual>"
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

        print("OpenAI status code:", openai_resp.status_code)
        print("OpenAI response:", openai_resp.text)

        openai_resp.raise_for_status()
        openai_content = openai_resp.json()["choices"][0]["message"]["content"]

        # Parse OpenAI JSON response
        parsed = json.loads(openai_content)
        new_title = parsed.get("title", "No Title Generated")
        new_description = parsed.get("description", "No Description Provided")

        # Encode Jira credentials
        credentials = f"{JIRA_USER_EMAIL}:{JIRA_API_TOKEN}"
        print("cred:", credentials)
        encoded_token = base64.b64encode(credentials.encode()).decode()
        print("base 64:", encoded_token)

        # Prepare headers
        headers = {
            "Authorization": f"Basic {encoded_token}",
            "Content-Type": "application/json"
        }

        update_url = f"{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}"
        print("Updating Jira Issue at:", update_url)  # Add this line
        # Update Jira issue
        update_resp = requests.put(update_url,
                                   headers=headers,
                                   json={
        "fields": {
            "summary": new_title,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": new_description
                            }
                        ]
                    }
                ]
            }
        }
    }
                                   )
        print("Jira status code:", update_resp.status_code)
        print("Jira response:", update_resp.text)
        update_resp.raise_for_status()
        if update_resp.status_code == 204 or not update_resp.text.strip():
            jira_response = {"message": "Issue updated successfully. (No content returned from Jira)"}
        else:
            jira_response = update_resp.json()
        return jsonify({"status": "success", "jira_response": jira_response})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    # Run on all interfaces, port 5000
    app.run(host="0.0.0.0", port=8000)