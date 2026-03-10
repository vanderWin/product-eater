# Product Feed Eater

A web tool for cleaning and enriching Google Merchant Center product feeds. Upload a TSV, CSV, or Excel feed, normalise columns, map colours and categories, build keyword lists, and fetch Google Ads search volume data — then download a sales opportunity sheet.

Built with Flask + HTMX. Deployed on Replit (Cloud Run).

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (copy and fill in .env.example)
cp .env.example .env

# Run
python flask_app.py
# → http://localhost:8080
```

---

## Replit Deployment

### 1. Import the repo
In Replit, create a new Repl → **Import from GitHub** → paste the repo URL.

### 2. Add Secrets
Go to **Tools → Secrets** and add each of the following:

| Secret key | Description |
|---|---|
| `FLASK_SECRET_KEY` | Any random 32+ character string |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | From Google Ads API Centre |
| `GOOGLE_ADS_CLIENT_ID` | OAuth 2.0 client ID |
| `GOOGLE_ADS_CLIENT_SECRET` | OAuth 2.0 client secret |
| `GOOGLE_ADS_REFRESH_TOKEN` | Long-lived OAuth refresh token |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCC / manager account ID (digits only) |
| `GOOGLE_ADS_CLIENT_CUSTOMER_ID` | The ad account to query (digits only) |

### 3. Deploy
Click **Deploy** → **Cloud Run**. The `.replit` file is already configured:

```
gunicorn -w 1 -b 0.0.0.0:8080 flask_app:app --timeout 300
```

> **Important:** Only 1 gunicorn worker (`-w 1`). The app uses filesystem-based sessions
> and a background SSE thread — multiple workers would cause session conflicts.

---

## Architecture

| Concern | Approach |
|---|---|
| Server | Flask 3, gunicorn |
| Interactivity | HTMX — each workflow step POSTs to a route and swaps an HTML fragment |
| Session state | Flask-Session (filesystem) for small values; large DataFrames as Parquet in `session_store/{uuid}/` |
| Charts | Chart.js — configs built in Python (`core/chart_data.py`), rendered client-side |
| Google Ads progress | Server-Sent Events (SSE) streaming from a background thread |
| Session cleanup | 4-hour TTL, cleaned up on each new upload |

---

## Key Files

| File | Purpose |
|---|---|
| `flask_app.py` | All Flask routes |
| `config.py` | Flask config — reads env vars / Replit Secrets |
| `session_helpers.py` | Parquet + session storage helpers |
| `core/` | All business logic (framework-agnostic) |
| `templates/` | Jinja2 templates (`base.html` + `index.html` + partials) |
| `static/css/app.css` | Brand styles |
| `static/js/htmx.min.js` | Self-hosted HTMX 1.9.12 |
| `.replit` | Replit run + deployment config |
| `SCORING.md` | Documents the Opportunity Score formula (`core/gads_opportunity.py`) |
| `BRANDING.md` | Brand colour palette reference |
