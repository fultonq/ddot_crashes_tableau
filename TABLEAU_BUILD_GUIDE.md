# DDOT District Road Safety | Tableau Proof of Concept
**Version:** 1.0 · 9 October 2026  
**Agency / program context:** District Department of Transportation (DDOT), Washington, DC / Vision Zero  
**Data publisher:** District of Columbia Metropolitan Police Department (MPD); crash layer documented as DDOT-maintained  
**Purpose:** Decision-support prototype for reported crashes, fatal/major injuries, road-user groups, and ward-level geographic patterns

> **Deliverable scope.** `download_ddot_data.py` creates the *real* CSV for Tableau from the official open-data ArcGIS service. This guide describes building the workbook by hand; a generated workbook (`DDOT_Crash_Safety_Dashboard.twbx`, from `build_tableau_workbook.py`) and its browser counterpart (`ddot_crash_dashboard.html`) are also in the project. No crash counts were fabricated or hardcoded.

---

## 1. Business objective and intended users

**Business question:** Where, when, and to whom is serious traffic harm occurring in Washington, DC, and where should DDOT analysts investigate further?

**Personas:** DDOT Traffic Safety Division analysts; Vision Zero program staff; ward-facing management; performance-reporting staff.

**Decisions this dashboard supports:**
1. Monitor reported crash events separately from the numbers of injured and killed persons.
2. Compare KSI (killed-or-seriously-injured) *crashes* across time and wards.
3. Examine the types of road users killed or seriously injured.
4. Identify street references and locations worth validation and deeper corridor/intersection analysis.
5. Screen for possible safety patterns—not infer causation or make an automatic capital-project ranking.

**Out of scope for v1:** crash *rates* (no traffic volumes / exposure denominator), crash responsibility, enforcement performance, validated high-injury-network attribution, project outcomes, adjusted year-over-year comparisons, and official fatality tallies.

## 2. Official data (verified source and fields)

| Dataset | Link | Intended role |
|---|---|---|
| **Crashes in DC** | https://opendata.dc.gov/datasets/DCGIS::crashes-in-dc/about | Primary event-level dataset, one crash location/event per source feature |
| **FeatureServer layer 24** | https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/FeatureServer/24?f=pjson | Auditable metadata, field schema, query endpoint |
| **Official CSV download** | https://opendata.dc.gov/api/download/v1/items/70392a096a8e431381f1f692aaa06afd/csv?layers=24 | Alternative manual download of the published raw source |
| **Crash Details table, layer 25** | https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/FeatureServer/25 | Optional v2: participant-level drill-down; *one crash to many participant rows* |

From the official schema: `CRIMEID` (display alias `CRASHID`), `OBJECTID`, `REPORTDATE`, `WARD`, `LATITUDE`, `LONGITUDE`, `NEARESTINTSTREETNAME`, the `FATAL_*` and `MAJORINJURIES_*` subtypes, `TOTAL_PEDESTRIANS`, `TOTAL_BICYCLES`, `TOTAL_VEHICLES`, `SPEEDING_INVOLVED`. The data catalog reported a dataset modification time of **October 6, 2026** when checked for this build. This is metadata freshness—not a guarantee that all 2026 incidents have been received.

**Critical data caveats:** The source contains *located reported crashes*, and records without complete enough location information can be absent. `REPORTDATE` is a report date rather than a verified precise occurrence datetime. MPD derives these data from COBALT; later updates can restate historical records. Do not claim 2026 YTD is comparable to a fully matured 2025 year without a consistent cutoff and reporting-lag control.

## 3. Tableau-ready data model

### 3.1 Recommended model (v1)

A single **fact_crash** table extracted by `download_ddot_data.py`, at grain **one `OBJECTID` / crash feature**.

- **Keys:** `object_id` identifies unique source features; `crash_id` uses `CRIMEID` and substitutes `OID-{OBJECTID}` if absent. Test whether multiple features share the same `CRIMEID`; `COUNTD(crash_id)` can differ from source feature count.
- **Date:** `report_date` is a local Eastern calendar date (`YYYY-MM-DD`), `report_month` is `YYYY-MM`.
- **Location:** `ward`, numeric `latitude` / `longitude`, `nearest_street`.
- **Crash-level flags:** `ksi_crash`, `pedestrian_involved`, `bicyclist_involved`, `motor_vehicle_involved`, `speeding_involved`.
- **Person counts:** `fatalities`, `major_injuries`, plus underlying fatal and major counts by road-user group.
- **Important grain distinction:** `SUM(fatalities)` and `SUM(major_injuries)` count *persons*; `SUM(ksi_crash)` counts *crash features*. Avoid treating a crash with two major injuries as two crashes.

### 3.2 Optional v2 relational model

Create a Tableau **relationship** between crash table and Crash Details on the physical key identified by the API metadata (`CRIMEID` in the crash layer; confirm the real physical key in layer 25 before joining). Do **not** make a flat physical many-to-one join before computing crash counts; participant rows multiply crash-level sums. If in doubt, create separate event-level and participant-level data sources and reconcile counts explicitly.

## 4. Generate the authentic DDOT CSV

**Prerequisites:** Python 3.9+ and internet access, including access to the DCGIS ArcGIS server. No external Python packages.

Open Command Prompt/PowerShell/Terminal inside the downloaded project folder:

```bash
python download_ddot_data.py --start 2026-07-01 --end 2026-09-30 --output ddot_crashes_q3_2026.csv
```

Other examples:

```bash
python download_ddot_data.py --start 2025-01-01 --end 2025-12-31 --output ddot_crashes_2025.csv
python download_ddot_data.py --start 2026-01-01 --end 2026-10-09 --output ddot_crashes_2026_ytd.csv
```

The script queries layer 24 using the source-reported count, paginates 1,000 rows at a time (ArcGIS `maxRecordCount = 1000`), orders by `OBJECTID`, retries transient failures, normalizes ward/date fields, computes injury counts and flags, and writes UTF-8 CSV. If internet access or the public API is blocked, use the official raw CSV link above and adapt its native field names in Tableau (Section 6).

**For the browser demonstration:** use `ddot_crash_dashboard.html`. Served from this folder (`python3 -m http.server 8000`, then http://localhost:8000/ddot_crash_dashboard.html) it loads the local `data/phase2` CSV; opened directly as a file it downloads the live period set in its header from the same official endpoint. If firewalls or internet access block that, click **Open files…** and select the CSV produced by the Python scripts. The map uses Leaflet with OpenStreetMap tiles; without the map library the charts still show all loaded records.

## 5. Data preparation and QA before Tableau

Open the exported file with a text/CSV viewer or Tableau's Data Source screen and confirm:

1. **One feature per row:** `COUNT([object_id]) = COUNTD([object_id])`. If not, investigate page overlap or source updates.
2. **Date range:** `MIN(report_date)` and `MAX(report_date)` fall inside the requested dates. Records can be missing for a period.
3. **Coordinate bounds:** DC is roughly around latitude 38.8–39.0 and longitude -77.2 to -76.9. Check null/out-of-range coordinates separately instead of removing them from the entire dashboard.
4. **Nullable counts:** The exporter interprets missing subtype counts as zero for summation. Validate this interpretation against DDOT data dictionary before formal reporting.
5. **Eight wards:** Ward 1 through Ward 8 plus possible `Unknown`; keep `Unknown` visible in data quality checks.
6. **Reconciliation:** Compare source feature count with export row count, number of unique `crash_id`s, number of fatalities, and number of major injuries. Record any mismatch. The official Vision Zero dashboard may apply additional business rules and date windows and need not equal a raw source sum.
7. **Reporting lag:** Display the maximum report date in the dashboard footer as a visible data-freshness qualifier.

## 6. Step-by-step Tableau Desktop development

The following build assumes the standardized CSV generated by the companion script.

### Step 1 — Establish the connection

1. Open **Tableau Desktop** → **Connect** → **Text File**.
2. Select `ddot_crashes_q3_2026.csv`.
3. Name the data source **DDOT | Crashes in DC | Fact**.
4. On the **Data Source** tab, check data types:
   - `report_date`: **Date** (if interpreted as String, select Data Type → Date);
   - `latitude`, `longitude`: **Number (decimal)**;
   - person/count flags and `object_id`: **Number (whole)**;
   - `crash_id`, `ward`, `nearest_street`: **String**.
5. Switch **Live → Extract** for larger data. Add an extract filter on `report_date` if needed, then save the extract.

> CSV is a local file connection. A Tableau Server/Cloud scheduled refresh does **not** magically re-run this Python exporter. For a production pipeline, stage data in a supported database/cloud store or implement a scheduled export plus an approved Tableau Bridge or extract-refresh workflow.

### Step 2 — Organize dimensions and measures

In Tableau's Data pane, create folders **Crash identification**, **Geography**, **Injury outcomes**, **Road-user involvement**, and **Date analysis**. Hide columns not used in the dashboard only after validation. Rename fields for friendly labels without breaking underlying calculations.

### Step 3 — Create calculated fields

Select **Analysis → Create Calculated Field** (or right-click in the Data pane). Enter each expression below exactly, adjusting field-name capitalization if Tableau renamed the CSV fields.

**[Crash Events]** — keep a distinct event ID KPI:

```tableau
COUNTD([crash_id])
```

**[Fatal Persons]**:

```tableau
SUM([fatalities])
```

**[Major-Injured Persons]**:

```tableau
SUM([major_injuries])
```

**[KSI Crash Events]**:

```tableau
SUM([ksi_crash])
```

**[Severity Class]** (row-level):

```tableau
IF [fatalities] > 0 OR [major_injuries] > 0 THEN
    "Fatal / Major Injury"
ELSE
    "Other Reported Crash"
END
```

**[Crash Location]** (spatial point; CSV latitude/longitude are native numeric columns):

```tableau
IF NOT ISNULL([latitude]) AND NOT ISNULL([longitude]) THEN
    MAKEPOINT([latitude], [longitude])
END
```

**[Pedestrian KSI Persons]** (row-level; subsequently use SUM):

```tableau
ZN([fatal_pedestrian]) + ZN([major_pedestrian])
```

**[Bicyclist KSI Persons]**:

```tableau
ZN([fatal_bicyclist]) + ZN([major_bicyclist])
```

**[Driver KSI Persons]**:

```tableau
ZN([fatal_driver]) + ZN([major_driver])
```

**[Passenger KSI Persons]**:

```tableau
ZN([fatal_passenger]) + ZN([major_passenger])
```

**[Other KSI Persons]**:

```tableau
ZN([fatal_other]) + ZN([major_other])
```

**[KSI Crash Share]** (aggregate; use when looking at aggregate groups):

```tableau
IF COUNTD([crash_id]) = 0 THEN
    0
ELSE
    SUM([ksi_crash]) / COUNTD([crash_id])
END
```

> **Note:** `SUM([ksi_crash])` assumes exactly one feature per unique crash. If CRIMEID duplicates are observed across source features, review the event definition and de-duplicate by source ID before calculating KPI ratios. A safe count of distinct KSI crashes on a raw source table is `COUNTD(IF [ksi_crash] = 1 THEN [crash_id] END)`.

### Step 4 — Build four KPI tiles

Make these four separate worksheets, with a large centered number and no visible axis/header:

- **KPI | Reported Crashes**: Marks → Text → `[Crash Events]`.
- **KPI | Fatalities**: Text → `[Fatal Persons]`; red accent.
- **KPI | Major Injuries**: Text → `[Major-Injured Persons]`; orange/gold accent.
- **KPI | KSI Crashes**: Text → `[KSI Crash Events]`; teal accent.

On each sheet, **Marks → Text**, edit the font to about 30–36 pt bold, and remove gridlines, dividers, and unnecessary titles. Create human-readable sheet titles on the final dashboard with Text objects, or use worksheet titles.

### Step 5 — Build the mapped crash location worksheet

1. Create sheet **MAP | Crash Locations**.
2. Double-click `[Crash Location]` (or drag it into the view); Tableau creates a spatial map.
3. Drag `[crash_id]` to **Marks → Detail** to draw distinct crash events; ensure the granularity remains crash-level.
4. Set Marks type to **Circle**.
5. Drag `[Severity Class]` to **Color** and choose:
   - Fatal / Major Injury = `#C74853`
   - Other Reported Crash = `#4A879E`
6. Set mark Size relatively small; adjust opacity to 35–65% depending on volume.
7. Add `[report_date]`, `[ward]`, `[nearest_street]`, `[fatalities]`, and `[major_injuries]` to **Tooltip**. Edit the tooltip for clarity.
8. Use **Map → Map Layers** to select a restrained basemap. Avoid showing exact sensitive personal information; these are public crash locations.

**Performance:** Maps can be slow with tens of thousands of marks. Start with a quarter-year extract or replace the raw-dot layer with an aggregated hexbin/density layer for production. The HTML concept deliberately samples at most 1,500 markers; Tableau can show all if performance permits.

### Step 6 — Build the time trend worksheet

1. Create **TREND | Crashes Over Time**.
2. Drag `report_date` to **Columns**; choose **WEEK(report_date)** for 2026 Q3, or **MONTH(report_date)** for a full-year view.
3. Drag `[Crash Events]` to **Rows**.
4. Set Marks type to **Bar**.
5. Drag `[Severity Class]` to **Color** and enable **Analysis → Stack Marks → On**.
6. Sort dates ascending and format the axis labels.
7. Ensure the total of stacked bars matches the unstacked count; if the same `crash_id` can occupy multiple classes due to duplication, investigate before publishing.
8. Optional: add a reference line for average weekly/monthly crash events.

### Step 7 — Build “KSI Persons by Road User”

1. Create worksheet **BAR | KSI Persons by Road User**.
2. Drag **Measure Names** to **Rows** and **Measure Values** to **Columns**.
3. In the **Measure Values** shelf, keep only the five new row-level measures:
   - `SUM([Pedestrian KSI Persons])`
   - `SUM([Bicyclist KSI Persons])`
   - `SUM([Driver KSI Persons])`
   - `SUM([Passenger KSI Persons])`
   - `SUM([Other KSI Persons])`
4. Drag **Measure Names** to **Filters** and select exactly these five measures.
5. Use **Bar** marks, horizontal orientation, end-of-bar labels, and a simple color hierarchy. Rename friendly labels as needed.
6. Add a caption: **“People killed or major injured; not number of crashes.”**

### Step 8 — Build the ward comparison

1. Create **BAR | KSI Crashes by Ward**.
2. Drag `[ward]` to **Rows**.
3. Drag `SUM([ksi_crash])` to **Columns**.
4. Sort descending by sum.
5. Set horizontal bars, show labels, and highlight the selected ward via the dashboard filter action.
6. Keep an `Unknown` ward in QA; show or explicitly suppress it in a clearly documented way.

### Step 9 — Build nearest-intersection street reference table

1. Create **TABLE | Street References**.
2. Drag `[nearest_street]` to **Rows**.
3. Add `[Crash Events]`, `[KSI Crash Events]`, and `[KSI Crash Share]` to **Measure Values** (use **Measure Names** on Columns).
4. Sort by `[KSI Crash Events]` descending and apply **Top 5** to `[nearest_street]`, using sum of `ksi_crash` as the measure; verify filters/order-of-operations if contextual filters change the ranking.
5. Format `[KSI Crash Share]` as percentage with 1 decimal.
6. Caption this as nearest **street reference**, not a uniquely identified intersection or validated high-injury corridor.

### Step 10 — Create dashboard filters

Use **Show Filter** on `report_date`, `ward`, `[Severity Class]`, and road-user flags. For mode selection in one compact control, create a parameter **[Road User Selector]** with allowed values `All`, `Pedestrian`, `Bicyclist`, `Motor vehicle`, then a Boolean calculated field **[Selected Mode Filter]**:

```tableau
CASE [Road User Selector]
WHEN "Pedestrian" THEN [pedestrian_involved] = 1
WHEN "Bicyclist" THEN [bicyclist_involved] = 1
WHEN "Motor vehicle" THEN [motor_vehicle_involved] = 1
ELSE TRUE
END
```

Place `[Selected Mode Filter]` on Filters (True) on all six/seven constituent views. Show the parameter control on the dashboard.

**Note:** The three involvement flags are **not mutually exclusive**. A crash involving both a car and a pedestrian is included in both relevant selections.

### Step 11 — Assemble the dashboard (pixel-perfect layout)

Create **Dashboard → New Dashboard** and use **Fixed size: 1440 × 900** as the initial desktop POC target. Use tiled **vertical and horizontal containers**; pad elements by 12–16 px. Suggested structure:

```text
[ DDOT / ROAD SAFETY ]                          [ Source and freshness ]
[ Date range ] [ Ward ] [ Severity ] [ Road user selector ]
┌────────────────┬────────────────┬────────────────┬───────────────────┐
│ Reported crash │ Fatal persons  │ Major injured  │ KSI crash events  │
└────────────────┴────────────────┴────────────────┴───────────────────┘
┌────────────────────────────────┬───────────────────────────────────────┐
│ Crash location map             │ KSI persons by road-user group         │
│                                │                                       │
└────────────────────────────────┴───────────────────────────────────────┘
┌────────────────────────────────┬───────────────────────────────────────┐
│ Stacked weekly/monthly trend   │ KSI crashes by ward                    │
└────────────────────────────────┴───────────────────────────────────────┘
[ Nearest-intersection street references | five-row detail table ]
[ Source / limitations / max report date / DDOT nonofficial POC footer ]
```

Use a white dashboard surface with cool-gray panel boundaries, deep navy typography (`#142A3B`), red highlight (`#C74853`), and teal or slate for supporting encodings. Keep the color of KSI crashes consistent across all worksheets.

### Step 12 — Add interactions

1. From **Dashboard → Actions → Add Action → Filter**, choose **BAR | KSI Crashes by Ward** as source; target Map, Trend, Road User, KPI, and Street worksheets.
2. Run action on **Select**; when selection is cleared, **Show all values**.
3. In **Target Filters**, select **Selected Fields** and map `ward → ward`. This avoids passing extra Tableau mark fields unintentionally.
4. Optionally add a second action from the trend chart to filter other views by date bucket, and a Highlight action on the map rather than filtering to a single crash.
5. Display tooltips that distinguish **Crash Events**, **Fatal Persons**, and **Major-Injured Persons**.
6. Test selecting a ward and clearing it. Confirm all KPI totals change and restore.

References: Tableau Filter Actions https://help.tableau.com/current/pro/desktop/en-us/actions_filter.htm and Tableau spatial functions https://help.tableau.com/current/pro/desktop/en-us/functions_functions_spatial.htm.

### Step 13 — Validate the completed Tableau dashboard

Perform acceptance testing on the actual saved workbook, with a documented reporting cutoff:

| Test | Expected result |
|---|---|
| Unfiltered KPI reconciliation | Workbook crash count and person-count metrics equal independent CSV aggregations |
| Ward filter | Map, trend, KSI by group, and KPIs respond to Ward 3 selection; clearing restores all |
| KSI only | Every displayed crash has `ksi_crash = 1`; fatality/major counts remain person-based |
| Fatal only | Every displayed crash has at least one fatal person |
| Involvement modes | Pedestrian/bicycle/motor-vehicle filters can overlap; not a mode share of 100% |
| Null coordinate | Record still counted in KPIs but absent from point map |
| Severity totals | `fatalities + major_injuries` equals sum of the five road-user KSI persons |
| Unique event test | Crash ID cardinality has been checked before choosing `COUNTD` as the source-of-truth event count |
| Time coverage | Data date range and max report date appear in provenance |
| QA date | 2025 full year is not incorrectly labeled comparable to partial 2026 YTD |

### Step 14 — Publish a controlled POC

1. Save the workbook as `DDOT_Road_Safety_POC.twbx` **in Tableau Desktop** after connecting the generated CSV; this packages local extracts where supported.
2. If DDOT has Tableau Server or Tableau Cloud, publish to a test project (not an official production site).
3. Check map tile availability, permissions, workbook refresh strategy, and performance.
4. Label the workbook **“Proof of concept · Not official statistics.”**
5. For production, replace the local file input with a governed ingestion process that captures source retrieval time, latest report date, row counts, schema versions, and refresh failures.

---

## 7. Practical roadmap after the initial POC

**Next iteration:** add a proper ward polygon spatial join for normalized choropleth views, High Injury Network spatial comparison, DDOT safety engineering project layers, crash exposure denominators (e.g., VMT or intersection volumes), prior-year same-date-lag comparators, and a drill-through to (privacy-screened) participant details. Do not combine crash frequency, injuries, traffic volumes, and project prioritization into a single unsupported “risk score.”

**Success definition:** An analyst can open the generated CSV in Tableau, reproduce the core views, filter by period/ward/severity/road-user involvement, trace every number back to a source field, and identify data limitations without inferring causation or claiming the prototype is an official DDOT reporting tool.

## 8. Source attribution / legal

Official published dataset: “Crashes in DC,” Metropolitan Police Department / DCGIS, on DDOT roadway network. The catalog lists **CC BY 4.0**; follow the applicable source attribution and licensing instructions when redistributing datasets. See https://catalog.data.gov/dataset/crashes-in-dc.

The included browser POC is a demonstration of what to recreate in **Tableau**; no native workbook has been built or tested in Tableau Desktop in this environment.
