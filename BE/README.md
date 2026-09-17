# Recruitment API — Backend

FastAPI-based recruitment automation backend that scrapes job listings, applies title/industry filtering, deduplicates entries, and stores results in MongoDB.

## Tech Stack

| Layer       | Technology                     |
|-------------|-------------------------------|
| Framework   | FastAPI + Uvicorn              |
| Database    | MongoDB Atlas (via Motor async)|
| Scraping    | python-jobspy                  |
| LinkedIn    | linkedin-api                   |
| Validation  | Pydantic v2                    |
| Runtime     | Python 3.12                    |

## API Endpoints

| Method | Path                          | Description                     |
|--------|-------------------------------|---------------------------------|
| GET    | `/health`                     | Health check                    |
| GET    | `/api/v1/icp/config`          | Fetch active ICP configuration  |
| POST   | `/api/v1/runs/start`          | Start a new scraping run        |
| GET    | `/api/v1/runs`                | List all runs (paginated)       |
| GET    | `/api/v1/runs/{id}`           | Get run details                 |
| GET    | `/api/v1/runs/{id}/jobs`      | Get jobs for a run (paginated)  |
| GET    | `/api/v1/jobs`                | List all jobs (paginated)       |
| GET    | `/api/v1/analytics/jobs`      | Job analytics (date range)      |
| GET    | `/api/v1/analytics/companies` | Company analytics               |

## Project Structure

```
BE/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py             # Settings (pydantic-settings)
│   ├── database.py           # MongoDB connection (Motor)
│   ├── api/
│   │   ├── deps.py           # Shared dependencies
│   │   └── v1/
│   │       ├── router.py     # Aggregated v1 router
│   │       ├── analytics.py  # Analytics endpoints
│   │       ├── icp.py        # ICP config endpoints
│   │       ├── jobs.py       # Jobs endpoints
│   │       └── runs.py       # Runs endpoints
│   ├── models/               # MongoDB document models
│   ├── schemas/              # Pydantic request/response schemas
│   └── services/
│       ├── orchestrator.py       # Pipeline coordinator
│       ├── jobspy_service.py     # Job scraping + dedup
│       ├── linkedin_service.py   # LinkedIn company lookup
│       └── rejection_service.py  # Title-based filtering
├── Dockerfile
├── .dockerignore
├── requirements.txt
└── README.md
```

---

## Local Development

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate        # Linux/Mac
# venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variables (copy and edit)
cp app/.env.example app/.env    # or create app/.env manually

# 4. Run the server
uvicorn app.main:app --reload --port 8000
```

---

## Deploy to Google Cloud Run

### Prerequisites

- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) installed and authenticated
- A GCP project with **Cloud Run**, **Artifact Registry**, and **Cloud Build** APIs enabled

### Step-by-step

```bash
# 1. Set your GCP project
gcloud config set project YOUR_PROJECT_ID

# 2. Build & push the image via Cloud Build
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/recruitment-api

# 3. Deploy to Cloud Run
gcloud run deploy recruitment-api \
  --image gcr.io/YOUR_PROJECT_ID/recruitment-api \
  --platform managed \
  --region asia-south1 \
  --allow-unauthenticated \
  --set-env-vars "MONGODB_URI=mongodb+srv://...,DATABASE_NAME=Job-Hunt,CORS_ORIGINS=[\"https://yourfrontend.com\"]" \
  --set-env-vars "LINKEDIN_EMAIL=your@email.com,LINKEDIN_PASSWORD=yourpass" \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 5 \
  --timeout 300
```

> **⚠️ Important:** Never hardcode secrets. Use `--set-env-vars` or, better yet, [Secret Manager](https://cloud.google.com/secret-manager) integration:
> ```bash
> gcloud run deploy recruitment-api \
>   --set-secrets "MONGODB_URI=mongodb-uri:latest,LINKEDIN_PASSWORD=linkedin-pass:latest"

### Graph Analyst (Neo4j Aura)

The AI workspace includes a second, thread-scoped agent that answers natural-
language questions about the talent knowledge graph. Configure it with:

```env
NEO4J_URI=neo4j+s://<aura-id>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=<secret>
NEO4J_DATABASE=neo4j
GRAPH_AGENT_MODEL=
GRAPH_QUERY_MAX_ROWS=200
GRAPH_QUERY_TIMEOUT_S=20
```

`GRAPH_AGENT_MODEL` falls back to `AGENT_MODEL` when blank. For production,
store `NEO4J_PASSWORD` in Secret Manager and grant the configured Neo4j user
read-only database privileges. The application also rejects all generated
Cypher containing write clauses, procedures (`CALL`), or admin commands.

Cloud Build preserves the Cloud Run service's existing environment. Configure
the service once, outside the image build:

```bash
gcloud run services update powerhaus-recruitr \
  --region=europe-west1 \
  --set-env-vars="NEO4J_URI=neo4j+s://<aura-id>.databases.neo4j.io,NEO4J_USER=neo4j,NEO4J_DATABASE=neo4j" \
  --set-secrets="NEO4J_PASSWORD=neo4j-password:latest"
```
> ```

### Environment Variables

| Variable            | Required | Description                        |
|---------------------|----------|------------------------------------|
| `MONGODB_URI`       | ✅       | MongoDB Atlas connection string    |
| `DATABASE_NAME`     | ✅       | MongoDB database name              |
| `CORS_ORIGINS`      | ✅       | JSON array of allowed origins      |
| `LINKEDIN_EMAIL`    | ❌       | LinkedIn login for company lookup  |
| `LINKEDIN_PASSWORD` | ❌       | LinkedIn password                  |
| `PORT`              | Auto     | Injected by Cloud Run (default 8080) |

---

## Docker (Local Testing)

```bash
# Build
docker build -t recruitment-api .

# Run
docker run -p 8080:8080 --env-file app/.env recruitment-api

# Test
curl http://localhost:8080/health
```
