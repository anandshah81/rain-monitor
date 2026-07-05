# Rain Monitor — India SW Monsoon Tracker

Real-time tracking of the Southwest Monsoon (June 1 – September 30) across all 36 IMD meteorological subdivisions.

**Live dashboard:** https://anandshah81.github.io/rain-monitor/

## What it tracks

- **Cumulative rainfall** since June 1, per subdivision and pan-India
- **Deviation from LPA** (Long Period Average, proxied by 5-year mean 2021–2025)
- **YoY comparison** vs same-window prior monsoon
- **IMD deficit categories**: Large Excess (≥+60%), Excess (+20 to +59%), Normal (−19 to +19%), Deficient (−59 to −20%), Large Deficient (≤−60%)
- **Spatial dispersion** — how many subdivisions in each category, by broad region (NW / Central / South / NE)
- **Cumulative charts** — daily rainfall accumulation vs LPA and last year

## Data source

- **Open-Meteo** (ERA5 + high-res regional models) — daily precipitation for 100+ representative points across the 36 IMD subdivisions
- **5-year LPA proxy**: 2021–2025 daily precipitation baseline (not IMD's official 1971–2020 LPA, but directionally consistent)

## Workflow

```
refresh.bat        # runs script, regenerates dashboard, pushes to GitHub Pages
```

The batch script:
1. Runs `rain_monitor.py` to fetch fresh data and generate `rain_monsoon_monitor.html`
2. Copies to `index.html` (with size-equality safety check)
3. Commits and pushes to `main`

## Caveats

- **Not official IMD data.** Open-Meteo grid resolution is ~14 km, which cannot resolve fine coastal or ghat effects. Relative signals (vs LPA, YoY) are internally consistent since same grid cells are compared across years.
- **5yr LPA proxy** underestimates the true 50-year LPA in wet years and overestimates in dry years — use as a directional signal, not for absolute claims.
- **Subdivision aggregates** are simple averages of 2–4 representative points per subdivision, not area-weighted like IMD's official method.
