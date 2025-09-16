import os
import time
import json
import base64
import logging
import argparse
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
#
# # Load .env file if it exists
load_dotenv()

# Configure logging to console and file
log_file = "app.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),  # Console
        logging.FileHandler(log_file)  # File
    ]
)

app = Flask(__name__)
# Replace with your actual credentials
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_USER_EMAIL = os.getenv("JIRA_USER_EMAIL")
JIRA_DOMAIN = os.getenv("JIRA_DOMAIN")

def call_openai_with_retry(payload, headers, max_retries=5):
    """Call OpenAI with retry on 429 Too Many Requests."""
    url = "https://api.openai.com/v1/chat/completions"
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                wait_time = 2 ** attempt  # exponential backoff
                logging.warning(f"Rate limited (429). Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                continue
            resp.raise_for_status()
            logging.info("OpenAI API call successful")
            return resp
        except requests.exceptions.RequestException as e:
            logging.error(f"OpenAI API request failed: {e}")
            time.sleep(2 ** attempt)
    raise Exception("Failed after retries due to rate limits")


def generate_report(description):
    """Call OpenAI to generate a structured Jira report from description."""
    prompt = f"""You are an experienced Jira assistant helping to write high-quality, detailed issue reports.
                     Given the following user-written bug report, your task is to:
                        1. Create a professional Jira issue title.
                        2. Write a detailed description including:
                                    - A clear summary of the issue
                                    - Fully detailed step-by-step reproduction steps
                                    - Specific form fields (e.g. project name, owner, description) where relevant
                                    - Expected and actual results

        Input:

        \"\"\"{description}\"\"\"

        Respond in JSON format:
        {{
          "title": "<Improved title>",
          "description": "### Summary:\\n<summary>\\n\\n### Steps to Reproduce:\\n1. <step>\\n2. <step>\\n...\\n\\n### Expected Result:\\n<expected>\\n\\n### Actual Result:\\n<actual>"
        }}"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3
    }

    openai_resp = call_openai_with_retry(payload, headers)
    openai_content = openai_resp.json()["choices"][0]["message"]["content"]
    parsed = json.loads(openai_content)
    logging.info("OpenAI report parsed successfully")
    return parsed


def update_jira(issue_key, new_title, new_description):
    """Update a Jira issue with the generated content."""
    logging.info(f"Updating Jira issue {issue_key}")
    credentials = f"{JIRA_USER_EMAIL}:{JIRA_API_TOKEN}"
    encoded_token = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded_token}",
        "Content-Type": "application/json"
    }

    update_url = f"{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}"
    logging.info(f"PUT {update_url}")
    # print("🔄 Updating Jira Issue at:", update_url)

    update_resp = requests.put(
        update_url,
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
                                {"type": "text", "text": new_description}
                            ]
                        }
                    ]
                }
            }
        }
    )
    logging.info(f"Jira response status: {update_resp.status_code}")
    logging.info(f"Jira response text: {update_resp.text}")
    update_resp.raise_for_status()
    return update_resp


@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    # print("📩 Received payload:", data)
    logging.info(f"Received webhook payload: {data}")

    if not data:
        logging.error("No JSON payload received")
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    try:
        description = None
        issue_key = None

        # Case 1: Jira webhook payload
        if "issue" in data and "key" in data["issue"]:
            issue = data["issue"]
            issue_key = issue["key"]
            description = issue["fields"].get("description", "")
        # Case 2: Direct input (no Jira key)
        elif "description" in data:
            description = data["description"]

        if not description:
            logging.warning("Empty description, skipping OpenAI call")
            return jsonify({"status": "skipped", "message": "Empty description"}), 200

        parsed = generate_report(description)
        new_title = parsed.get("title", "No Title Generated")
        new_description = parsed.get("description", "No Description Provided")

        if not issue_key:  # Console-only mode
            logging.info("Outputting generated report to console (no Jira update)")
            print("\n===== Generated Output (API Console Mode) =====")
            print("Title:", new_title)
            print("Description:\n", new_description)
            return jsonify({"status": "success", "title": new_title, "description": new_description}), 200

        # Otherwise update Jira
        update_resp = update_jira(issue_key, new_title, new_description)
        if update_resp.status_code == 204 or not update_resp.text.strip():
            jira_response = {"message": "Issue updated successfully. (No content returned from Jira)"}
        else:
            jira_response = update_resp.json()

        return jsonify({"status": "success", "jira_response": jira_response})

    except Exception as e:
        logging.exception("Error processing request")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--description", type=str, help="Bug description to process without Jira")
    parser.add_argument("--port", type=int, default=8000, help="Port for Flask server")

    args = parser.parse_args()

    if args.description:
        # Command-line mode
        logging.info("Running in CLI mode")
        # print("🚀 Running in CLI mode...\n")
        parsed = generate_report(args.description)
        print("===== Generated Output (CLI Mode) =====")
        print("Title:", parsed.get("title"))
        print("Description:\n", parsed.get("description"))
    else:
        # Run as Flask API
        logging.info(f"Starting Flask server on port {args.port}")
        # print(f"🌍 Starting Flask server on port {args.port}")
        app.run(host="0.0.0.0", port=args.port)

# @app.route("/jira-webhook", methods=["POST"])
# def jira_webhook():
#     data = request.json
#     print("Received payload:", data)  # <--- This line logs the JSON payload
#
#     if not data:
#         return jsonify({"status": "error", "message": "No JSON payload received"}), 400
#
#     try:
#         issue = data.get("issue")
#         if not issue or "key" not in issue:
#             return jsonify({"status": "error", "message": "Missing issue or issue key"}), 400
#         issue_key = issue["key"]
#         print("Issue key:", issue_key)
#         description = issue["fields"].get("description", "")
#         print("Description:", description)
#         if not description:
#             return jsonify({"status": "skipped", "message": "Empty description. Skipping OpenAI call."}), 200
#
#         if not issue_key:
#             return jsonify({"status": "error", "message": "Missing issue key"}), 400
#
#
#         # Prepare prompt for OpenAI
#         prompt = f"""You are an experienced Jira assistant helping to write high-quality, detailed issue reports.
#                      Given the following user-written bug report, your task is to:
#                         1. Create a professional Jira issue title.
#                         2. Write a detailed description including:
#                                     - A clear summary of the issue
#                                     - Fully detailed step-by-step reproduction steps
#                                     - Specific form fields (e.g. project name, owner, description) where relevant
#                                     - Expected and actual results
#
# If the user did not specify exact field names, you may intelligently infer commonly required fields in enterprise applications.
# Input:
#
# \"\"\"
# {description}
# \"\"\"
#
# Respond in JSON format:
# {{
#   "title": "<Improved title>",
#   "description": "### Summary:\\n<summary>\\n\\n### Steps to Reproduce:\\n1. <step>\\n2. <step>\\n...\\n\\n### Expected Result:\\n<expected>\\n\\n### Actual Result:\\n<actual>"
# }}"""
#
#         # Call OpenAI Chat Completion API
#         openai_resp = requests.post(
#             "https://api.openai.com/v1/chat/completions",
#             headers={
#                 "Authorization": f"Bearer {OPENAI_API_KEY}",
#                 "Content-Type": "application/json"
#             },
#             json={
#                 "model": "gpt-3.5-turbo",  # or "gpt-4"
#                 "messages": [{"role": "user", "content": prompt}],
#                 "temperature": 0.3
#             }
#         )
#
#         print("OpenAI status code:", openai_resp.status_code)
#         print("OpenAI response:", openai_resp.text)
#
#         openai_resp.raise_for_status()
#         openai_content = openai_resp.json()["choices"][0]["message"]["content"]
#
#         # Parse OpenAI JSON response
#         parsed = json.loads(openai_content)
#         new_title = parsed.get("title", "No Title Generated")
#         new_description = parsed.get("description", "No Description Provided")
#
#         # Encode Jira credentials
#         credentials = f"{JIRA_USER_EMAIL}:{JIRA_API_TOKEN}"
#         print("cred:", credentials)
#         encoded_token = base64.b64encode(credentials.encode()).decode()
#         print("base 64:", encoded_token)
#
#         # Prepare headers
#         headers = {
#             "Authorization": f"Basic {encoded_token}",
#             "Content-Type": "application/json"
#         }
#
#         update_url = f"{JIRA_DOMAIN}/rest/api/3/issue/{issue_key}"
#         print("Updating Jira Issue at:", update_url)  # Add this line
#         # Update Jira issue
#         update_resp = requests.put(update_url,
#                                    headers=headers,
#                                    json={
#         "fields": {
#             "summary": new_title,
#             "description": {
#                 "type": "doc",
#                 "version": 1,
#                 "content": [
#                     {
#                         "type": "paragraph",
#                         "content": [
#                             {
#                                 "type": "text",
#                                 "text": new_description
#                             }
#                         ]
#                     }
#                 ]
#             }
#         }
#     }
#                                    )
#         print("Jira status code:", update_resp.status_code)
#         print("Jira response:", update_resp.text)
#         update_resp.raise_for_status()
#         if update_resp.status_code == 204 or not update_resp.text.strip():
#             jira_response = {"message": "Issue updated successfully. (No content returned from Jira)"}
#         else:
#             jira_response = update_resp.json()
#         return jsonify({"status": "success", "jira_response": jira_response})
#
#     except Exception as e:
#         return jsonify({"status": "error", "message": str(e)}), 500
#
#
# if __name__ == "__main__":
#     # Open ngrok tunnel
#     app.run(host="0.0.0.0", port=8000)