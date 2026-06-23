# Cargonz template setup

This project is built to run on **Vercel** with the **Cargonz** logistics template skin.

## 1. Purchase and download Cargonz

Cargonz is sold on **ThemeForest** (not included in Envato Elements subscription):

1. Open [Cargonz – Logistics & Transportation NextJs Template](https://themeforest.net/item/cargonz-logistics-transportation-nextjs-template/62724105)
2. Sign in / create a ThemeForest account
3. Add to cart and complete purchase (~$29 one-time)
4. Go to **Downloads** → download **All files & documentation**
5. Extract the ZIP on your machine

> **Envato Elements alternative:** If you prefer a subscription-only template, use **Logistiq** or **Trastek** from [elements.envato.com/web-templates/logistics](https://elements.envato.com/web-templates/logistics) — the merge steps below are similar.

## 2. Merge Cargonz into this project

After extracting Cargonz, copy these into **this** `web/` folder (keep our custom routes):

| From Cargonz ZIP | Into this project |
|------------------|-------------------|
| `public/css/` | `web/public/css/` |
| `public/fonts/`, `public/images/` | `web/public/` |
| `src/components/` (Header, Footer, etc.) | `web/src/components/cargonz/` |
| Layout patterns from `src/app/layout.tsx` | Merge branding into `web/src/app/layout.tsx` |

**Do not overwrite:**

- `web/src/app/dispatch/` — FMC dispatch console
- `web/src/app/login/` — staff login
- `web/src/app/api/` — Wialon + workbook APIs
- `web/src/lib/` — dispatch business logic

## 3. Wire the dispatch page into Cargonz navigation

Add a link in the Cargonz header (after merge):

```tsx
<Link href="/dispatch">Route Dispatch</Link>
```

Style the dispatch page with Cargonz classes from `public/css/` once copied.

## 4. Deploy to Vercel

```bash
cd web
npx vercel login
npx vercel link
npx vercel env pull .env.local
# Add all variables from .env.example in Vercel dashboard → Settings → Environment Variables
npx vercel --prod
```

Or connect the GitHub repo in [vercel.com/new](https://vercel.com/new) with **Root Directory** = `web`.

## 5. Data files

Copy into `web/data/` (committed or uploaded to Vercel Blob later):

- `route coordinates (004).xlsx`
- `FCL_Vehicles.xlsx`
