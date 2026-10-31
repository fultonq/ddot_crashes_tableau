#!/usr/bin/env python3
"""Package DCGIS crash data into a Tableau packaged workbook (.twbx).

Workflow:
  python download_ddot_data.py --start 2023-01-01 --end 2026-09-30 --output data/ddot_crashes_real.csv
  python enrich_ddot_safety.py --crashes data/ddot_crashes_real.csv --outdir data/phase2
  python build_tableau_workbook.py --phase2 data/phase2 --output DDOT_Crash_Safety_Dashboard.twbx

Data sources inside the workbook:
  * DC Crashes      — ddot_crashes_enriched.csv, one row per crash (OBJECTID)
  * HIN Corridors   — ddot_hin_corridor_summary.csv, built here from the
                      enriched crashes + ddot_hin_corridors.csv (proximity matched)
Requires only the Python >=3.9 standard library.
"""
import argparse
import csv
from collections import defaultdict
from html import escape
import io
from pathlib import Path
import zipfile

CR = "federated.dc_crashes"
CO = "federated.hin_corridors"
NAVY, INK, MUTED = "#16253C", "#16253C", "#52677C"
SEVERITY_COLORS = {"Fatal": "#B2182B", "Major injury": "#EF8A62", "Minor / no injury": "#BDBDBD"}
TIER_COLORS = {"1": "#16253C", "2": "#3E7CB1", "3": "#9CC3E4"}

CRASH_TYPES = {
    "crash_id": "string", "object_id": "integer", "report_date": "date", "report_month": "string",
    "ward": "string", "latitude": "real", "longitude": "real", "nearest_street": "string",
    "hin_corridor_id": "string", "hin_corridor_name": "string", "hin_tier": "string",
    "hin_distance_m": "real", "hin_spatial_match": "string", "hin_match_quality": "string",
    "corridor_aadt_context": "integer", "corridor_aadt_coverage_pct": "real",
    "corridor_aadt_year_min": "integer", "corridor_aadt_year_max": "integer",
}
CORRIDOR_COLUMNS = ["corridor_id", "corridor_label", "corridor_name", "hin_tier", "hin_miles",
                    "aadt_context", "crashes", "ksi_crashes", "fatalities", "major_injuries",
                    "pedestrian_crashes", "bicyclist_crashes", "ksi_per_mile", "crashes_per_mile",
                    "rate_eligible"]
CORRIDOR_TYPES = {"corridor_id": "string", "corridor_label": "string", "corridor_name": "string",
                  "hin_tier": "string", "hin_miles": "real", "aadt_context": "integer",
                  "ksi_per_mile": "real", "crashes_per_mile": "real", "rate_eligible": "string"}
MIN_RATE_MILES = 0.5


def a(s):
    return escape(str(s), quote=True)


# ---------------------------------------------------------------- data prep
def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_csv_bytes(rows, columns):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def corridor_summary(crashes, corridors):
    agg = defaultdict(lambda: defaultdict(int))
    for r in crashes:
        cid = r.get("hin_corridor_id")
        if not cid:
            continue
        g = agg[cid]
        g["crashes"] += 1
        g["ksi_crashes"] += int(r["ksi_crash"] or 0)
        g["fatalities"] += int(r["fatalities"] or 0)
        g["major_injuries"] += int(r["major_injuries"] or 0)
        g["pedestrian_crashes"] += int(r["pedestrian_involved"] or 0)
        g["bicyclist_crashes"] += int(r["bicyclist_involved"] or 0)
    name_counts = defaultdict(int)
    for c in corridors:
        name_counts[c["corridor_name"]] += 1
    out = []
    for c in corridors:
        g = agg.get(c["corridor_id"], {})
        miles = float(c["hin_miles"] or 0)
        label = c["corridor_name"]
        if name_counts[label] > 1:
            label += " [" + c["corridor_id"] + "]"
        row = {"corridor_id": c["corridor_id"], "corridor_label": label,
               "corridor_name": c["corridor_name"], "hin_tier": c["hin_tier"], "hin_miles": miles,
               "aadt_context": c["aadt_context"],
               "rate_eligible": "Yes" if miles >= MIN_RATE_MILES else "No"}
        for k in ["crashes", "ksi_crashes", "fatalities", "major_injuries",
                  "pedestrian_crashes", "bicyclist_crashes"]:
            row[k] = g.get(k, 0)
        row["ksi_per_mile"] = round(row["ksi_crashes"] / miles, 3) if miles else ""
        row["crashes_per_mile"] = round(row["crashes"] / miles, 3) if miles else ""
        out.append(row)
    return out


# ---------------------------------------------------------------- datasources
CRASH_CALCS = [
    # (name, caption, datatype, role, type, formula, default-format)
    ("Calculation_Crashes", "Crashes", "integer", "measure", "quantitative", "COUNT([crash_id])", "n#,##0"),
    ("Calculation_Fatalities", "Traffic Fatalities", "integer", "measure", "quantitative", "SUM([fatalities])", "n#,##0"),
    ("Calculation_MajorInjuries", "Major Injuries", "integer", "measure", "quantitative", "SUM([major_injuries])", "n#,##0"),
    ("Calculation_KSI", "KSI Crashes", "integer", "measure", "quantitative", "SUM([ksi_crash])", "n#,##0"),
    ("Calculation_KSIShareHIN", "Share of KSI Crashes within 35 m of HIN", "real", "measure", "quantitative",
     'SUM(IF [hin_spatial_match] = "within 35 m" THEN [ksi_crash] ELSE 0 END) / SUM([ksi_crash])', "p0.0%"),
    ("Calculation_Severity", "Crash Severity", "string", "dimension", "nominal",
     'IF [fatalities] > 0 THEN "Fatal" ELSEIF [major_injuries] > 0 THEN "Major injury" ELSE "Minor / no injury" END', None),
    ("Calculation_Mode", "Road User Involved", "string", "dimension", "nominal",
     'IF [pedestrian_involved] = 1 THEN "Pedestrian" ELSEIF [bicyclist_involved] = 1 THEN "Bicyclist" ELSE "Motor vehicle only" END', None),
]


def relation_columns(columns, types, default="integer"):
    return "\n".join(
        f'            <column name="{a(c)}" datatype="{types.get(c, default)}" ordinal="{i}" />'
        for i, c in enumerate(columns))


def metadata_column(c, dtype, caption=None, extra=""):
    role, typ = ("dimension", "nominal") if dtype == "string" else ("measure", "quantitative")
    if dtype == "date":
        role, typ = "dimension", "ordinal"
    cap = f' caption="{a(caption)}"' if caption else ""
    return f'      <column name="[{a(c)}]" datatype="{dtype}" role="{role}" type="{typ}"{cap}{extra} />'


def calc_column(name, caption, dtype, role, typ, formula, fmt, indent="      "):
    f = f' default-format="{a(fmt)}"' if fmt else ""
    return (f'{indent}<column name="[{name}]" caption="{a(caption)}" datatype="{dtype}" role="{role}" type="{typ}"{f}>\n'
            f'{indent}  <calculation class="tableau" formula="{a(formula)}" />\n{indent}</column>')


def palette_rule(field, colors):
    maps = "\n".join(f'          <map to="{col}">\n            <bucket>&quot;{a(k)}&quot;</bucket>\n          </map>'
                     for k, col in colors.items())
    return (f'        <encoding attr="color" field="{field}" type="palette">\n{maps}\n        </encoding>')


def datasource_xml(name, caption, conn, filename, columns, types, default, extra_meta, calcs, palettes):
    meta = []
    for c in columns:
        extra = ""
        if c == "latitude":
            extra = ' semantic-role="[Geographical].[Latitude]"'
        elif c == "longitude":
            extra = ' semantic-role="[Geographical].[Longitude]"'
        meta.append(metadata_column(c, types.get(c, default), extra_meta.get(c), extra))
    meta += [calc_column(*c) for c in calcs]
    style = ""
    if palettes:
        style = ("      <style>\n        <style-rule element=\"mark\">\n" +
                 "\n".join(palette_rule(f, cols) for f, cols in palettes) +
                 "\n        </style-rule>\n      </style>")
    return f'''    <datasource name="{name}" caption="{a(caption)}" inline="true" version="18.1">
      <connection class="federated">
        <named-connections>
          <named-connection caption="{a(conn)}" name="textscan.{conn}">
            <connection class="textscan" directory="Data" filename="{a(filename)}" workgroup-auth-mode="as-is" />
          </named-connection>
        </named-connections>
        <relation connection="textscan.{conn}" name="{a(filename)}" table="[{a(filename.replace('.', '#'))}]" type="table">
          <columns character-set="UTF-8" header="yes" locale="en_US" separator=",">
{relation_columns(columns, types, default)}
          </columns>
        </relation>
      </connection>
      <aliases enabled="yes" />
{chr(10).join(meta)}
{style}
    </datasource>'''


# ---------------------------------------------------------------- worksheets
class Sheet:
    """Minimal worksheet spec rendered into Tableau XML."""

    def __init__(self, name, ds, mark, rows="", cols="", deps=(), encodings="", filters="",
                 sorts="", label=None, mark_color=None, show_labels=False, extra_style="",
                 title_note="", shared_filters=True, fit="entire-view"):
        self.__dict__.update(locals())
        del self.__dict__["self"]


def dep_xml(ds, deps):
    """deps: list of (column-xml-or-None, instance tuple(column, derivation, name, type))"""
    cols, inst = [], []
    seen = set()
    for col_xml, ci in deps:
        if col_xml and col_xml not in seen:
            seen.add(col_xml)
            cols.append("            " + col_xml.strip().replace("\n", "\n            "))
        if ci:
            c, d, n, t = ci
            line = f'            <column-instance column="{c}" derivation="{d}" name="{n}" pivot="key" type="{t}" />'
            if line not in inst:
                inst.append(line)
    return f'          <datasource-dependencies datasource="{ds}">\n' + "\n".join(cols + inst) + "\n          </datasource-dependencies>"


def raw_col(name, dtype, extra=""):
    role, typ = ("dimension", "nominal") if dtype == "string" else ("measure", "quantitative")
    if dtype == "date":
        role, typ = "dimension", "ordinal"
    return f'<column name="[{name}]" datatype="{dtype}" role="{role}" type="{typ}"{extra} />'


def calc_dep(name):
    c = next(c for c in CRASH_CALCS if c[0] == name)
    return calc_column(*c, indent="").replace("\n", " ")


def year_ward_filters(ds):
    """Quick filters shared across every crash worksheet (filter-group = all using this data source)."""
    return (f'''        <filter class="categorical" column="[{ds}].[yr:report_date:ok]" filter-group="2">
          <groupfilter function="level-members" level="[yr:report_date:ok]" user:ui-enumeration="all" user:ui-marker="enumerate" />
        </filter>
        <filter class="categorical" column="[{ds}].[none:ward:nk]" filter-group="3">
          <groupfilter function="level-members" level="[none:ward:nk]" user:ui-enumeration="all" user:ui-marker="enumerate" />
        </filter>''', [f"[{ds}].[yr:report_date:ok]", f"[{ds}].[none:ward:nk]"])


def worksheet_xml(s):
    filters, slices = "", []
    deps = list(s.deps)
    if s.shared_filters:
        filters, slices = year_ward_filters(s.ds)
        deps += [(raw_col("report_date", "date"), ("[report_date]", "Year", "[yr:report_date:ok]", "ordinal")),
                 (raw_col("ward", "string"), ("[ward]", "None", "[none:ward:nk]", "nominal"))]
    if s.filters:
        filters = s.filters[0] + ("\n" + filters if filters else "")
        slices = s.filters[1] + slices
    slices_xml = ("          <slices>\n" + "\n".join(f"            <column>{x}</column>" for x in slices) +
                  "\n          </slices>\n") if slices else ""
    label_xml = ""
    if s.label:
        label_xml = f'''            <customized-label>
              <formatted-text>
                <run fontcolor="{s.label[2]}" fontname="Tableau Semibold" fontsize="{s.label[1]}">&lt;{s.label[0]}&gt;</run>
              </formatted-text>
            </customized-label>
'''
    mark_fmt = ""
    if s.mark_color:
        mark_fmt += f'\n            <format attr="mark-color" value="{s.mark_color}" />'
    if s.show_labels:
        mark_fmt += '\n            <format attr="mark-labels-show" value="true" />\n            <format attr="mark-labels-cull" value="true" />'
    title = f'<run bold="true" fontname="Tableau Medium" fontsize="11" fontcolor="{NAVY}">{a(s.name)}</run>'
    if s.title_note:
        title += f'<run fontname="Tableau Book" fontsize="9" fontcolor="{MUTED}">{a(chr(10) + s.title_note)}</run>'
    return f'''    <worksheet name="{a(s.name)}">
      <layout-options>
        <title>
          <formatted-text>
            {title}
          </formatted-text>
        </title>
      </layout-options>
      <table>
        <view>
          <datasources>
            <datasource name="{s.ds}" />
          </datasources>
{dep_xml(s.ds, deps)}
{filters}
{s.sorts}
{slices_xml}          <aggregation value="true" />
        </view>
        <style>
          <style-rule element="gridline">
            <format attr="line-visibility" value="off" />
          </style-rule>
          <style-rule element="mark">{mark_fmt}
          </style-rule>
          <style-rule element="worksheet">
            <format attr="font-family" value="Tableau Book" />
            <format attr="color" value="{INK}" />
          </style-rule>{s.extra_style}
        </style>
        <panes>
          <pane selection-relaxation-option="selection-relaxation-allow">
            <view>
              <breakdown value="auto" />
            </view>
            <mark class="{s.mark}" />
            <encodings>
{s.encodings}
            </encodings>
{label_xml}          </pane>
        </panes>
        <rows>{s.rows}</rows>
        <cols>{s.cols}</cols>
      </table>
    </worksheet>'''


def build_sheets():
    C = CR
    sheets = []

    def kpi(name, calc, color, note=""):
        n = f"[usr:{calc}:qk]"
        sheets.append(Sheet(
            name, C, "Text", deps=[(calc_dep(calc), (f"[{calc}]", "User", n, "quantitative"))],
            encodings=f'              <text column="[{C}].{n}" />',
            label=(f"[{C}].{n}", 26, color), title_note=note))

    kpi("Reported Crashes", "Calculation_Crashes", NAVY, "one row per crash record")
    kpi("Traffic Fatalities", "Calculation_Fatalities", "#B2182B", "people killed")
    kpi("Major Injuries", "Calculation_MajorInjuries", "#D6604D", "people seriously injured")
    kpi("KSI Crashes", "Calculation_KSI", "#B2182B", "crashes with ≥1 fatal / major injury")
    kpi("KSI Crashes Near HIN", "Calculation_KSIShareHIN", "#3E7CB1", "share within 35 m of High-Injury Network")

    sev = "[none:Calculation_Severity:nk]"
    sev_dep = (calc_dep("Calculation_Severity"), ("[Calculation_Severity]", "None", sev, "nominal"))
    ksi_only = (f'''        <filter class="categorical" column="[{C}].{sev}">
          <groupfilter function="union" user:op="manual">
            <groupfilter function="member" level="{sev}" member="&quot;Fatal&quot;" />
            <groupfilter function="member" level="{sev}" member="&quot;Major injury&quot;" />
          </groupfilter>
        </filter>''', [f"[{C}].{sev}"])
    sheets.append(Sheet(
        "KSI Crash Locations", C, "Circle",
        rows=f"[{C}].[avg:latitude:qk]", cols=f"[{C}].[avg:longitude:qk]",
        deps=[(raw_col("latitude", "real", ' semantic-role="[Geographical].[Latitude]"'), ("[latitude]", "Avg", "[avg:latitude:qk]", "quantitative")),
              (raw_col("longitude", "real", ' semantic-role="[Geographical].[Longitude]"'), ("[longitude]", "Avg", "[avg:longitude:qk]", "quantitative")),
              (raw_col("crash_id", "string"), ("[crash_id]", "None", "[none:crash_id:nk]", "nominal")),
              (raw_col("nearest_street", "string"), ("[nearest_street]", "Attribute", "[attr:nearest_street:nk]", "nominal")),
              (raw_col("report_date", "date"), ("[report_date]", "Attribute", "[attr:report_date:ok]", "ordinal")),
              sev_dep],
        encodings=f'''              <color column="[{C}].{sev}" />
              <lod column="[{C}].[none:crash_id:nk]" />
              <tooltip column="[{C}].[attr:nearest_street:nk]" />
              <tooltip column="[{C}].[attr:report_date:ok]" />''',
        filters=ksi_only, title_note="fatal and major-injury crashes, by report location",
        extra_style='''
          <style-rule element="map">
            <format attr="washout" value="0.0" />
          </style-rule>'''))

    crashes = "[usr:Calculation_Crashes:qk]"
    sheets.append(Sheet(
        "Monthly Crashes and KSI Crashes", C, "Line",
        rows=f"([{C}].{crashes} / [{C}].[usr:Calculation_KSI:qk])", cols=f"[{C}].[tmn:report_date:qk]",
        deps=[(calc_dep("Calculation_Crashes"), ("[Calculation_Crashes]", "User", crashes, "quantitative")),
              (calc_dep("Calculation_KSI"), ("[Calculation_KSI]", "User", "[usr:Calculation_KSI:qk]", "quantitative")),
              (raw_col("report_date", "date"), ("[report_date]", "Month-Trunc", "[tmn:report_date:qk]", "quantitative"))],
        mark_color="#3E7CB1", title_note="by report month; 2026 is January–September only"))

    ksi = "[usr:Calculation_KSI:qk]"
    ksi_dep = (calc_dep("Calculation_KSI"), ("[Calculation_KSI]", "User", ksi, "quantitative"))
    sheets.append(Sheet(
        "KSI Crashes by Ward", C, "Bar", rows=f"[{C}].[none:ward:nk]", cols=f"[{C}].{ksi}",
        deps=[ksi_dep],
        sorts=f'        <sort class="computed" column="[{C}].[none:ward:nk]" direction="DESC" using="[{C}].{ksi}" />',
        mark_color="#B2182B", show_labels=True))

    mode = "[none:Calculation_Mode:nk]"
    sheets.append(Sheet(
        "KSI Crashes by Road User", C, "Bar", rows=f"[{C}].{mode}", cols=f"[{C}].{ksi}",
        deps=[ksi_dep, (calc_dep("Calculation_Mode"), ("[Calculation_Mode]", "None", mode, "nominal"))],
        sorts=f'        <sort class="computed" column="[{C}].{mode}" direction="DESC" using="[{C}].{ksi}" />',
        mark_color="#D6604D", show_labels=True, title_note="pedestrian > bicyclist > motor vehicle only"))

    name = "[none:hin_corridor_name:nk]"
    tier = "[none:hin_tier:nk]"
    hin_filters = (f'''        <filter class="categorical" column="[{C}].[none:hin_spatial_match:nk]" context="true">
          <groupfilter function="member" level="[none:hin_spatial_match:nk]" member="&quot;within 35 m&quot;" user:ui-domain="database" user:ui-enumeration="inclusive" user:ui-marker="enumerate" />
        </filter>
        <filter class="categorical" column="[{C}].{name}">
          <groupfilter count="15" end="top" function="end" units="records" user:ui-marker="end" user:ui-top-by-field="true">
            <groupfilter direction="DESC" expression="SUM([ksi_crash])" function="order" user:ui-marker="order">
              <groupfilter function="level-members" level="{name}" user:ui-enumeration="all" user:ui-marker="enumerate" />
            </groupfilter>
          </groupfilter>
        </filter>''', [f"[{C}].[none:hin_spatial_match:nk]", f"[{C}].{name}"])
    sheets.append(Sheet(
        "Top HIN Corridors by KSI Crashes", C, "Bar", rows=f"[{C}].{name}", cols=f"[{C}].{ksi}",
        deps=[ksi_dep,
              (raw_col("hin_corridor_name", "string"), ("[hin_corridor_name]", "None", name, "nominal")),
              (raw_col("hin_spatial_match", "string"), ("[hin_spatial_match]", "None", "[none:hin_spatial_match:nk]", "nominal")),
              (raw_col("ksi_crash", "integer"), None)],
        filters=hin_filters,
        sorts=f'        <sort class="computed" column="[{C}].{name}" direction="DESC" using="[{C}].{ksi}" />',
        mark_color="#16253C", show_labels=True, title_note="crashes within 35 m (proximity match, not verified attribution)"))

    K = CO
    lab, rate = "[none:corridor_label:nk]", "[sum:ksi_per_mile:qk]"
    rate_filters = (f'''        <filter class="categorical" column="[{K}].[none:rate_eligible:nk]">
          <groupfilter function="member" level="[none:rate_eligible:nk]" member="&quot;Yes&quot;" user:ui-domain="database" user:ui-enumeration="inclusive" user:ui-marker="enumerate" />
        </filter>
        <filter class="categorical" column="[{K}].{lab}">
          <groupfilter count="15" end="top" function="end" units="records" user:ui-marker="end" user:ui-top-by-field="true">
            <groupfilter direction="DESC" expression="SUM([ksi_per_mile])" function="order" user:ui-marker="order">
              <groupfilter function="level-members" level="{lab}" user:ui-enumeration="all" user:ui-marker="enumerate" />
            </groupfilter>
          </groupfilter>
        </filter>''', [f"[{K}].[none:rate_eligible:nk]", f"[{K}].{lab}"])
    sheets.append(Sheet(
        "KSI Crashes per HIN Mile", K, "Bar", rows=f"[{K}].{lab}", cols=f"[{K}].{rate}",
        deps=[(raw_col("corridor_label", "string"), ("[corridor_label]", "None", lab, "nominal")),
              (raw_col("ksi_per_mile", "real"), ("[ksi_per_mile]", "Sum", rate, "quantitative")),
              (raw_col("hin_tier", "string"), ("[hin_tier]", "None", tier, "nominal")),
              (raw_col("hin_miles", "real"), ("[hin_miles]", "Sum", "[sum:hin_miles:qk]", "quantitative")),
              (raw_col("ksi_crashes", "integer"), ("[ksi_crashes]", "Sum", "[sum:ksi_crashes:qk]", "quantitative")),
              (raw_col("aadt_context", "integer"), ("[aadt_context]", "Sum", "[sum:aadt_context:qk]", "quantitative")),
              (raw_col("rate_eligible", "string"), ("[rate_eligible]", "None", "[none:rate_eligible:nk]", "nominal"))],
        encodings=f'''              <color column="[{K}].{tier}" />
              <tooltip column="[{K}].[sum:hin_miles:qk]" />
              <tooltip column="[{K}].[sum:ksi_crashes:qk]" />
              <tooltip column="[{K}].[sum:aadt_context:qk]" />''',
        filters=rate_filters,
        sorts=f'        <sort class="computed" column="[{K}].{lab}" direction="DESC" using="[{K}].{rate}" />',
        show_labels=True, shared_filters=False,
        title_note=f"all years, all wards; corridors ≥ {MIN_RATE_MILES} mi; color = HIN tier"))
    return sheets


# ---------------------------------------------------------------- dashboard
def zone_style(bg, margin=6):
    return f'''            <zone-style>
              <format attr="border-style" value="none" />
              <format attr="margin" value="{margin}" />
              <format attr="background-color" value="{bg}" />
            </zone-style>'''


def text_zone(zid, x, y, w, h, runs, bg, margin=10):
    r = "".join(f'<run fontsize="{fs}" fontname="Tableau {fn}" fontcolor="{fc}">{a(t)}</run>' for t, fs, fn, fc in runs)
    return f'''          <zone id="{zid}" x="{x}" y="{y}" w="{w}" h="{h}" type-v2="text" forceUpdate="true">
            <formatted-text>{r}</formatted-text>
{zone_style(bg, margin)}
          </zone>'''


def sheet_zone(zid, x, y, w, h, name, bg="#FFFFFF"):
    return f'''          <zone id="{zid}" x="{x}" y="{y}" w="{w}" h="{h}" name="{a(name)}" show-title="true">
{zone_style(bg)}
          </zone>'''


def filter_zone(zid, x, y, w, h, sheet, param):
    return f'''          <zone id="{zid}" x="{x}" y="{y}" w="{w}" h="{h}" name="{a(sheet)}" param="{param}" type-v2="filter" mode="checkdropdown" values="database">
{zone_style("#172D46", 4)}
          </zone>'''


DASH = "DC Crash Safety Overview"


def dashboard_xml(period, n_rows):
    z = []
    z.append(text_zone(2, 0, 0, 60000, 9000, [
        ("DC TRAFFIC CRASH SAFETY", 20, "Semibold", "#FFFFFF"),
        (f"\nDCGIS / MPD Crashes in DC  •  {period}  •  {n_rows:,} crash records", 10, "Book", "#C9D6E3")], "#172D46"))
    z.append(filter_zone(3, 60000, 0, 20000, 9000, "Reported Crashes", f"[{CR}].[yr:report_date:ok]"))
    z.append(filter_zone(4, 80000, 0, 20000, 9000, "Reported Crashes", f"[{CR}].[none:ward:nk]"))
    kpis = ["Reported Crashes", "Traffic Fatalities", "Major Injuries", "KSI Crashes", "KSI Crashes Near HIN"]
    for i, k in enumerate(kpis):
        z.append(sheet_zone(10 + i, i * 20000, 9000, 20000, 13000, k, "#F3F7FA"))
    z.append(sheet_zone(20, 0, 22000, 45000, 73000, "KSI Crash Locations"))
    z.append(sheet_zone(21, 45000, 22000, 55000, 24000, "Monthly Crashes and KSI Crashes"))
    z.append(sheet_zone(22, 45000, 46000, 27500, 21000, "KSI Crashes by Ward"))
    z.append(sheet_zone(23, 72500, 46000, 27500, 21000, "KSI Crashes by Road User"))
    z.append(sheet_zone(24, 45000, 67000, 27500, 28000, "Top HIN Corridors by KSI Crashes"))
    z.append(sheet_zone(25, 72500, 67000, 27500, 28000, "KSI Crashes per HIN Mile"))
    z.append(text_zone(30, 0, 95000, 100000, 5000, [(
        "Source: DCGIS Public_Safety FeatureServer/24 (MPD-sourced, DDOT-maintained); HIN DDOTLRS/142; AADT DDOTLRS/24.  "
        "KSI = crash with ≥1 fatality or major injury; fatality/injury counts are people.  Dates are REPORTDATE.  "
        "HIN matches are 35 m proximity screens, not verified attribution.  AADT is context, not an exposure rate.",
        8, "Book", MUTED)], "#F0F4F8", 6))
    return f'''  <dashboards>
    <dashboard name="{DASH}">
      <style />
      <size maxheight="1000" maxwidth="1500" minheight="1000" minwidth="1500" sizing-mode="fixed" />
      <zones>
        <zone type-v2="layout-basic" id="1" x="0" y="0" w="100000" h="100000">
{chr(10).join(z)}
        </zone>
      </zones>
    </dashboard>
  </dashboards>'''


def windows_xml(sheets):
    vps = "\n".join(f'        <viewpoint name="{a(s.name)}">\n          <zoom type="{s.fit}" />\n        </viewpoint>' for s in sheets)
    ws = "\n".join(f'''    <window class="worksheet" name="{a(s.name)}">
      <cards>
        <edge name="left">
          <strip size="160">
            <card type="pages" />
            <card type="filters" />
            <card type="marks" />
          </strip>
        </edge>
        <edge name="top">
          <strip size="2147483647">
            <card type="columns" />
          </strip>
          <strip size="2147483647">
            <card type="rows" />
          </strip>
          <strip size="31">
            <card type="title" />
          </strip>
        </edge>
      </cards>
      <viewpoint>
        <zoom type="{s.fit}" />
      </viewpoint>
    </window>''' for s in sheets)
    return f'''  <windows>
    <window class="dashboard" maximized="true" name="{DASH}">
      <viewpoints>
{vps}
      </viewpoints>
      <active id="-1" />
    </window>
{ws}
  </windows>'''


def workbook_xml(crash_cols, corridor_cols, period, n_rows):
    sheets = build_sheets()
    crash_ds = datasource_xml(
        CR, "DC Crashes (DCGIS)", "dc_crashes", "ddot_crashes_enriched.csv", crash_cols, CRASH_TYPES, "integer",
        {"report_date": "Report Date", "ward": "Ward", "nearest_street": "Nearest Street",
         "hin_corridor_name": "HIN Corridor", "hin_tier": "HIN Tier"},
        CRASH_CALCS, [("[none:Calculation_Severity:nk]", SEVERITY_COLORS)])
    corr_ds = datasource_xml(
        CO, "HIN Corridors (proximity-matched)", "hin_corridors", "ddot_hin_corridor_summary.csv", corridor_cols,
        CORRIDOR_TYPES, "integer",
        {"corridor_label": "HIN Corridor", "hin_tier": "HIN Tier", "ksi_per_mile": "KSI Crashes per Mile",
         "hin_miles": "HIN Miles", "ksi_crashes": "KSI Crashes", "aadt_context": "AADT (context only)"},
        [], [("[none:hin_tier:nk]", TIER_COLORS)])
    return f'''<?xml version='1.0' encoding='utf-8'?>
<workbook original-version="18.1" source-build="2025.3.3 (20253.26.0206.0336)" source-platform="mac" version="18.1" locale="en_US" xmlns:user="http://www.tableausoftware.com/xml/user">
  <document-format-change-manifest>
    <ObjectModelEncapsulateLegacy />
  </document-format-change-manifest>
  <preferences />
  <style>
    <style-rule element="gridline">
      <format attr="line-visibility" value="off" />
    </style-rule>
  </style>
  <datasources>
{crash_ds}
{corr_ds}
  </datasources>
  <mapsources>
    <mapsource name="Tableau" />
  </mapsources>
  <worksheets>
{chr(10).join(worksheet_xml(s) for s in sheets)}
  </worksheets>
{dashboard_xml(period, n_rows)}
{windows_xml(sheets)}
</workbook>
'''


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--phase2", default="data/phase2", help="Output folder of enrich_ddot_safety.py")
    p.add_argument("--output", default="DDOT_Crash_Safety_Dashboard.twbx")
    args = p.parse_args()
    src = Path(args.phase2)
    crashes = read_rows(src / "ddot_crashes_enriched.csv")
    if not crashes:
        raise SystemExit("No crash rows found; run download_ddot_data.py and enrich_ddot_safety.py first.")
    crash_cols = list(crashes[0].keys())
    corridors = corridor_summary(crashes, read_rows(src / "ddot_hin_corridors.csv"))
    dates = sorted(r["report_date"] for r in crashes if r["report_date"])
    period = f"{dates[0]} to {dates[-1]}"
    twb = workbook_xml(crash_cols, CORRIDOR_COLUMNS, period, len(crashes))
    out = Path(args.output)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(out.stem + ".twb", twb.encode("utf-8"))
        z.writestr("Data/ddot_crashes_enriched.csv", to_csv_bytes(crashes, crash_cols))
        z.writestr("Data/ddot_hin_corridor_summary.csv", to_csv_bytes(corridors, CORRIDOR_COLUMNS))
    print(f"Wrote {out} — {len(crashes):,} crashes ({period}), {len(corridors)} HIN corridors.")


if __name__ == "__main__":
    main()
