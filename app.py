import os
import uuid
from pathlib import Path
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

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


def validate_github_config():
    missing = []
    for key, value in {
        "GITHUB_TOKEN": GITHUB_TOKEN,
        "GITHUB_OWNER": GITHUB_OWNER,
        "GITHUB_REPO": GITHUB_REPO,
    }.items():
        if not value:
            missing.append(key)

    if missing:
        raise ValueError(f"Missing GitHub configuration: {', '.join(missing)}")


def save_uploaded_screenshot(file_storage, report_id):
    now = datetime.now(timezone.utc)
    dated_dir = SCREENSHOT_DIR / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
    dated_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{report_id}.png"
    full_path = dated_dir / filename
    file_storage.save(full_path)

    relative_path = full_path.relative_to(BASE_DIR / "static").as_posix()
    screenshot_url = f"{APP_BASE_URL}/static/{relative_path}"

    return {
        "full_path": str(full_path),
        "relative_path": relative_path,
        "url": screenshot_url
    }


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
    screenshot_section = "_No screenshot available_"

    if report.get("screenshot_url"):
        screenshot_section = (
            f"![Fault Screenshot]({report['screenshot_url']})\n\n"
            f"[Open Screenshot File]({report['screenshot_url']})"
        )

    extra_metadata_lines = ""
    if report.get("extra_metadata"):
        for key, value in report["extra_metadata"].items():
            extra_metadata_lines += f"- {key}: {value}\n"

    body = f"""## Summary
{report['description']}

## Category
{report['category'] or 'Unspecified'}

## Application
{report.get('app_name') or 'Unknown'}

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
{screenshot_section}

## Internal Metadata
- Report ID: {report['report_id']}
- Source: Dashboard Fault Reporter
{extra_metadata_lines if extra_metadata_lines else ""}
"""
    return body


def create_github_issue(title, body, labels):
    validate_github_config()

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
        raise RuntimeError(f"GitHub issue creation failed {response.status_code}: {response.text}")

    return response.json()


def parse_extra_metadata(raw_value):
    if not raw_value:
        return {}

    try:
        import json
        parsed = json.loads(raw_value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


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
        app_name = request.form.get("app_name", "").strip()
        frontend_environment = request.form.get("environment", "").strip()
        extra_metadata_raw = request.form.get("extra_metadata", "").strip()
        screenshot = request.files.get("screenshot")

        if not description:
            return jsonify({"success": False, "message": "Description is required"}), 400

        if not screenshot:
            return jsonify({"success": False, "message": "Screenshot is required"}), 400

        username = get_authenticated_username()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        report_id = f"FR-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8]}"

        environment = frontend_environment or APP_ENVIRONMENT
        extra_metadata = parse_extra_metadata(extra_metadata_raw)

        screenshot_info = save_uploaded_screenshot(screenshot, report_id)

        report = {
            "report_id": report_id,
            "description": description,
            "category": category,
            "username": username,
            "timestamp": timestamp,
            "page_url": page_url,
            "user_agent": user_agent,
            "environment": environment,
            "app_name": app_name,
            "extra_metadata": extra_metadata,
            "screenshot_url": screenshot_info["url"]
        }

        issue_title = build_issue_title(description, environment)
        issue_body = build_issue_body(report)
        labels = build_labels(category, environment)

        issue = create_github_issue(issue_title, issue_body, labels)

        return jsonify({
            "success": True,
            "message": "Fault report submitted successfully",
            "report_id": report_id,
            "github_issue_number": issue["number"],
            "github_issue_url": issue["html_url"],
            "screenshot_url": screenshot_info["url"]
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