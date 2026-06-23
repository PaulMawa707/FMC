# FMC Logistics — Route Dispatch

Flask web app for Farmers Choice route planning and Wialon Logistics dispatch.

Repository: [github.com/PaulMawa707/FMC](https://github.com/PaulMawa707/FMC)

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env     # then edit credentials
python app.py
```

Open http://localhost:5000

## Deploy to Vercel

1. Push this repo to GitHub (`PaulMawa707/FMC`)
2. Import the project at [vercel.com/new](https://vercel.com/new)
3. Add environment variables from `.env.example` in **Vercel → Settings → Environment Variables**
4. Deploy (uses `vercel.json` + `api/index.py`)

Required env vars:

| Variable | Description |
|----------|-------------|
| `FLASK_SECRET_KEY` | Random secret for sessions |
| `LOGISTICS_APP_USERNAME` | Staff login username |
| `LOGISTICS_APP_PASSWORD` | Staff login password |
| `LOGISTICS_TOKEN` | Wialon API token |
| `LOGISTICS_RESOURCE_ID` | Wialon resource ID |

## Project layout

| Path | Purpose |
|------|---------|
| `app.py` | Flask routes and API |
| `fmc_route_dispatch.py` | Workbook parsing, DEL/COL logic, Wialon dispatch |
| `templates/` | Login + dashboard HTML |
| `static/` | CSS, JS, brand images |
| `api/index.py` | Vercel serverless entry |
| `web/` | Next.js rebuild (optional future migration) |

## Data files (committed)

- `route coordinates (004).xlsx` — route sheets (eastlands, ngong rd, southlands)
- `FCL_Vehicles.xlsx` — fleet unit IDs

## Legacy Streamlit UI

Still available if needed:

```bash
pip install streamlit
streamlit run fmc_route_dispatch.py
```
