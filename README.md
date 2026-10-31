# DDOT Road Safety POC — Quick Start

This package contains a **browser-based proof-of-concept visualization** and the assets to **recreate the same dashboards in Tableau Desktop** using real District of Columbia crash open data. It is not a DDOT-issued product and does not include an author-tested native Tableau `.twb` / `.twbx`.

## Package files

- `ddot_crash_dashboard.html` — browser version of the Tableau dashboard. Loads `data/phase2/ddot_crashes_enriched.csv` + `ddot_hin_geometry.geojson` when served locally, otherwise falls back to live DCGIS crash + HIN data (matched to the HIN in the browser); **Open files…** accepts pipeline CSV/GeoJSON output.
- `DDOT_Crash_Safety_Dashboard.twbx` — Tableau packaged workbook built by `build_tableau_workbook.py` from the `data/phase2` output.
- `download_ddot_data.py` — dependency-free Python 3.9+ extractor that reads the same authoritative ArcGIS FeatureServer and writes normalized **Tableau-ready** CSV records.
- `enrich_ddot_safety.py` — adds HIN corridor proximity matches, AADT context and work orders (`data/phase2`).
- `TABLEAU_BUILD_GUIDE.md` — detailed Tableau data model, field calculations, drag-and-drop worksheet steps, dashboard layout, QA, publishing guidance.
- `README.md` — this file.

## Start with the live visual concept

1. In this folder run `python3 -m http.server 8000` and open http://localhost:8000/ddot_crash_dashboard.html — it loads the local `data/phase2` CSV.
2. Opened directly as a file (or with `?source=live`), it downloads live records from DCGIS for the date range in the header instead; **↻ Load live** reloads them.
3. Filter by year, ward and road user; click ward, road-user or corridor bars to cross-filter or zoom the map; **↓ CSV** exports the filtered rows.

## Generate the real Tableau source CSV

In this directory, run:

```bash
python download_ddot_data.py --start 2026-07-01 --end 2026-09-30 --output ddot_crashes_q3_2026.csv
```

This requires internet access to `maps2.dcgis.dc.gov`. All statistics in the dashboard must derive from downloaded, verified source records. No example counts are embedded in the deliverable. This package does **not** include a predownloaded live dataset because direct access to the source API was unavailable in the creation environment.

## Build in Tableau Desktop

Follow `TABLEAU_BUILD_GUIDE.md`, starting at Section 6:
- Connect to the extracted CSV as a **Text File**.
- Verify field types and create the KSI and spatial calculations.
- Build four KPI tiles, a location map, time trend, injury-by-road-user bars, KSI-by-ward bars, and nearest-street detail table.
- Assemble the dashboard using a 1440 × 900 starting canvas and add **ward filtering** actions.
- Validate against the raw export, save as `.twbx` in Tableau Desktop, then pilot on Tableau Server/Cloud.

## Definitions

- **Reported crashes** = distinct `CRIMEID` values, with `OBJECTID` fallback for blank crash IDs.
- **Fatalities** and **major injuries** = **counts of people** across driver, passenger, pedestrian, bicyclist, and other groups.
- **KSI crashes** = **counts of crash records** with at least one fatal or major injury.
- **Road user involvement** = overlapping flags; modes do not form a disjoint partition.
- **Street ranking** uses `NEARESTINTSTREETNAME`; these are not uniquely coded intersections.

## Source

Official dataset: https://opendata.dc.gov/datasets/DCGIS::crashes-in-dc/about  
Feature schema: https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/FeatureServer/24?f=pjson  
Published metadata: https://catalog.data.gov/dataset/crashes-in-dc

Dataset metadata checked 2026-10-09, last-modified date listed as 2026-10-06. Attribution: District of Columbia MPD; DDOT roadway maintenance context; DCGIS Open Data.

**Caution:** This is a proof of concept, not an official fatality report. Located crash records can be incomplete, delayed, and retrospectively revised; fatality counts may differ from other DDOT/MPD publications with different sources, definitions, and cutoffs.
