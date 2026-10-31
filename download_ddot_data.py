#!/usr/bin/env python3
"""Export authoritative DCGIS/DDOT crash feature records into a Tableau-ready CSV.

Usage:
 python download_ddot_data.py --start 2026-07-01 --end 2026-09-30 --output ddot_crashes_q3_2026.csv
Requires Python 3.9+ and an internet connection. No pip dependencies.
Dates are inclusive in America/New_York. The dataset is MPD-sourced and DDOT-maintained.
"""
import argparse
import csv
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

SERVICE = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/FeatureServer/24/query"
TZ = ZoneInfo("America/New_York")
FIELDS = [
    "OBJECTID", "CRIMEID", "REPORTDATE", "WARD", "LATITUDE", "LONGITUDE",
    "NEARESTINTSTREETNAME", "TOTAL_VEHICLES", "TOTAL_BICYCLES", "TOTAL_PEDESTRIANS",
    "SPEEDING_INVOLVED", "FATAL_PEDESTRIAN", "FATAL_BICYCLIST", "FATAL_DRIVER",
    "FATALPASSENGER", "FATALOTHER", "MAJORINJURIES_PEDESTRIAN",
    "MAJORINJURIES_BICYCLIST", "MAJORINJURIES_DRIVER", "MAJORINJURIESPASSENGER",
    "MAJORINJURIESOTHER"
]
COLUMNS = [
    "crash_id","object_id","report_date","report_month","ward","latitude","longitude",
    "nearest_street","total_vehicles","total_bicycles","total_pedestrians",
    "speeding_involved","fatal_pedestrian","fatal_bicyclist","fatal_driver","fatal_passenger",
    "fatal_other","major_pedestrian","major_bicyclist","major_driver","major_passenger",
    "major_other","fatalities","major_injuries","ksi_crash","pedestrian_involved",
    "bicyclist_involved","motor_vehicle_involved"
]

def request_json(params, attempts=5):
    query = urllib.parse.urlencode(params)
    url = SERVICE + "?" + query
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DDOT-Tableau-POC/1.0", "Accept":"application/json"})
            with urllib.request.urlopen(req, timeout=65) as res:
                payload = json.load(res)
            if "error" in payload:
                raise RuntimeError("ArcGIS error: " + str(payload["error"]))
            return payload
        except Exception as err:
            if attempt == attempts - 1:
                raise RuntimeError("ArcGIS query failed after retries: " + str(err)) from err
            time.sleep(min(2**attempt, 15))

def as_num(x):
    try:
        return max(0, int(float(x))) if x is not None and x != "" else 0
    except (TypeError, ValueError):
        return 0

def valid_ward(x):
    s = str(x or "").strip()
    n = ''.join(c for c in s if c.isdigit())
    if n.isdigit() and 1 <= int(n) <= 8:
        return "Ward " + str(int(n))
    return "Unknown"

def normalize(a):
    stamp = a.get("REPORTDATE")
    when = dt.datetime.fromtimestamp(stamp / 1000, dt.timezone.utc).astimezone(TZ) if stamp is not None else None
    fatal = [as_num(a.get(k)) for k in ["FATAL_PEDESTRIAN","FATAL_BICYCLIST","FATAL_DRIVER","FATALPASSENGER","FATALOTHER"]]
    major = [as_num(a.get(k)) for k in ["MAJORINJURIES_PEDESTRIAN","MAJORINJURIES_BICYCLIST","MAJORINJURIES_DRIVER","MAJORINJURIESPASSENGER","MAJORINJURIESOTHER"]]
    tv, tb, tp = (as_num(a.get(k)) for k in ["TOTAL_VEHICLES","TOTAL_BICYCLES","TOTAL_PEDESTRIANS"])
    oid = a.get("OBJECTID")
    cid = str(a.get("CRIMEID") or "").strip() or ("OID-"+str(oid))
    return dict(zip(COLUMNS,[
        cid, oid, when.strftime("%Y-%m-%d") if when else "",
        when.strftime("%Y-%m") if when else "",
        valid_ward(a.get("WARD")), a.get("LATITUDE"), a.get("LONGITUDE"),
        a.get("NEARESTINTSTREETNAME") or "(Not reported)",
        tv,tb,tp,as_num(a.get("SPEEDING_INVOLVED")),
        *fatal,*major,sum(fatal),sum(major),int(sum(fatal)+sum(major)>0),
        int(tp>0), int(tb>0), int(tv>0)
    ]))

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--start", default="2026-07-01", help="First report date, YYYY-MM-DD")
    p.add_argument("--end", default="2026-09-30", help="Last report date inclusive, YYYY-MM-DD")
    p.add_argument("--output", default="ddot_crashes_q3_2026.csv", help="CSV file to create")
    args = p.parse_args()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if end < start: p.error("--end cannot be earlier than --start")
    stop = end + dt.timedelta(days=1)
    where = f"REPORTDATE >= timestamp '{start} 00:00:00' AND REPORTDATE < timestamp '{stop} 00:00:00'"
    base = {"where":where, "f":"json"}
    total = request_json({**base,"returnCountOnly":"true"})["count"]
    print(f"Source reported {total:,} rows for {start} to {end} (report-date filter).", flush=True)
    rows = {}
    page_size = 1000
    for offset in range(0,total,page_size):
        data=request_json({**base,"outFields":",".join(FIELDS),"returnGeometry":"false",
             "orderByFields":"OBJECTID ASC","resultOffset":offset,"resultRecordCount":page_size})
        features=data.get("features", [])
        if not features and offset < total:
            raise RuntimeError(f"Unexpected empty page at offset {offset}; no partial CSV has been written.")
        for feat in features:
            a=feat["attributes"]
            if a.get("OBJECTID") is not None:
                rows[a["OBJECTID"]]=normalize(a)
        print(f"Retrieved {min(offset+page_size,total):,} / {total:,} source rows", end="\r", flush=True)
    if len(rows) != total:
        print(f"\nWARNING: {len(rows):,} distinct object IDs vs {total:,} expected. The live dataset may have changed during pagination.", file=sys.stderr)
    ordered=[rows[k] for k in sorted(rows)]
    with open(args.output,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(ordered)
    badcoords=sum(not (isinstance(r["latitude"],(int,float)) and isinstance(r["longitude"],(int,float)) and
                     38.7 < r["latitude"] < 39.1 and -77.3 < r["longitude"] < -76.7) for r in ordered)
    nodates=sum(not r["report_date"] for r in ordered)
    print(f"\nSaved {len(ordered):,} crashes to {args.output}. Blank/out-of-range coordinates: {badcoords:,}; missing dates: {nodates:,}.")
    print("KSI = fatal or major-injury crash; fatality/major-injury fields count people, not crashes.")
    print("Source: DCGIS/MPD Crashes in DC, DDOT-maintained, FeatureServer layer 24.")
if __name__=="__main__":
    main()
