# FMC Route Dispatch (Vercel + Next.js)

Next.js rebuild of the Streamlit `fmc_route_dispatch.py` app, designed for **Vercel** deployment with the **Cargonz** logistics template skin.

## Quick start

```bash
cd web
cp .env.example .env.local
# Edit .env.local — set AUTH_*, SESSION_SECRET, LOGISTICS_TOKEN

npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) → login → `/dispatch`.

## Cargonz template

See **[docs/CARGONZ_SETUP.md](./docs/CARGONZ_SETUP.md)** for:

1. Purchasing Cargonz on ThemeForest
2. Merging CSS/components into this project
3. Deploying to Vercel

## Deploy to Vercel

1. Push repo to GitHub
2. Import at [vercel.com/new](https://vercel.com/new) with **Root Directory** = `web`
3. Add environment variables from `.env.example`
4. Deploy

Or from CLI:

```bash
cd web
npx vercel
```

## Project layout

| Path | Purpose |
|------|---------|
| `src/app/dispatch/` | Route dispatch console (replaces Streamlit UI) |
| `src/app/api/` | Auth, routes, fleet, Wialon dispatch |
| `src/lib/` | Workbook parsing, Wialon client |
| `data/` | Route workbook + fleet Excel files |

## Legacy app

The original Python Streamlit app remains at `../fmc_route_dispatch.py` for reference during migration.
