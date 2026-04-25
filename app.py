import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

BASE_DIR = Path(__file__).resolve().parent
SCREENSHOT_DIR = BASE_DIR / "static" / "fault_screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000").rstrip("/")
APP_ENVIRONMENT = os.getenv("APP_ENVIRONMENT", "UAT")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
AUTH_USER_HEADER = os.getenv("AUTH_USER_HEADER", "X-Forwarded-User")
DEFAULT_TEST_USER = os.getenv("DEFAULT_TEST_USER", "test.user")

CATEGORY_LABEL_MAP = {
    "visual issue": "ui-bug",
    "ui issue": "ui-bug",
    "data issue": "data-bug",
    "refresh issue": "refresh-problem",
    "performance": "performance",
    "access issue": "access",
    "incorrect kpi": "incorrect-kpi",
    "screenshot issue": "screenshot"
}


def get_authenticated_username():
    user = request.headers.get(AUTH_USER_HEADER)
    if user and user.strip():
        return user.strip()

    remote_user = request.environ.get("REMOTE_USER")
    if remote_user and remote_user.strip():
        return remote_user.strip()

    return DEFAULT_TEST_USER


def save_uploaded_screenshot(file_storage):
    today = datetime.now(timezone.utc)
    dated_dir = SCREENSHOT_DIR / today.strftime("%Y") / today.strftime("%m") / today.strftime("%d")
    dated_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4()}.png"
    file_path = dated_dir / filename
    file_storage.save(file_path)

    relative_path = file_path.relative_to(BASE_DIR / "static").as_posix()
    screenshot_url = f"{APP_BASE_URL}/static/{relative_path}"
    return str(file_path), screenshot_url


def build_labels(category, environment):
    labels = ["bug", "reported-from-dashboard", environment.lower()]

    if category:
        cat = category.strip().lower()
        mapped = CATEGORY_LABEL_MAP.get(cat)
        if mapped:
            labels.append(mapped)

    return labels


def build_issue_title(description, environment):
    clean = " ".join(description.split())
    short = clean[:70] if clean else "Dashboard fault reported"
    return f"[{environment}] Dashboard fault: {short}"


def build_issue_body(report):
    screenshot_markdown = f"![Fault Screenshot]({report['screenshot_url']})" if report.get("screenshot_url") else "_No screenshot available_"

    body = f"""## Summary
{report['description']}

## Category
{report['category'] or 'Unspecified'}

## Reported by
{report['username']}

## Reported at
{report['timestamp']}

## Page URL
{report['page_url'] or 'N/A'}

## Environment
{report['environment']}

## Browser
{report['user_agent'] or 'N/A'}

## Screenshot
{screenshot_markdown}

## Internal Metadata
- Report ID: {report['report_id']}
- Source: Dashboard Fault Reporter
"""
    return body


def create_github_issue(title, body, labels):
    if not GITHUB_TOKEN or not GITHUB_OWNER or not GITHUB_REPO:
        raise ValueError("GitHub configuration is missing. Check .env values.")

    api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/issues"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    payload = {
        "title": title,
        "body": body,
        "labels": labels
    }

    response = requests.post(api_url, headers=headers, json=payload, timeout=30)

    if response.status_code >= 400:
        raise RuntimeError(f"GitHub API error {response.status_code}: {response.text}")

    return response.json()


@app.route("/")
def index():
    return render_template(
        "dashboard.html",
        current_user=get_authenticated_username(),
        app_environment=APP_ENVIRONMENT
    )


@app.route("/api/report-fault", methods=["POST"])
def report_fault():
    try:
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        page_url = request.form.get("page_url", "").strip()
        user_agent = request.form.get("user_agent", "").strip()
        screenshot = request.files.get("screenshot")

        if not description:
            return jsonify({"success": False, "message": "Description is required"}), 400

        if not screenshot:
            return jsonify({"success": False, "message": "Screenshot is required"}), 400

        username = get_authenticated_username()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        report_id = f"FR-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8]}"

        saved_path, screenshot_url = save_uploaded_screenshot(screenshot)

        report = {
            "report_id": report_id,
            "description": description,
            "category": category,
            "username": username,
            "timestamp": timestamp,
            "page_url": page_url,
            "user_agent": user_agent,
            "environment": APP_ENVIRONMENT,
            "screenshot_url": screenshot_url,
            "saved_path": saved_path
        }

        issue_title = build_issue_title(description, APP_ENVIRONMENT)
        issue_body = build_issue_body(report)
        labels = build_labels(category, APP_ENVIRONMENT)

        issue = create_github_issue(issue_title, issue_body, labels)

        return jsonify({
            "success": True,
            "message": "Fault report submitted successfully",
            "report_id": report_id,
            "github_issue_number": issue["number"],
            "github_issue_url": issue["html_url"],
            "screenshot_url": screenshot_url
        })

    except Exception as exc:
        app.logger.exception("Failed to submit fault report")
        return jsonify({
            "success": False,
            "message": str(exc)
        }), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5000, debug=debug)