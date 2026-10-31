#!/usr/bin/env python3
"""DDOT Phase 2 corridor enrichment — real DCGIS open data, no synthetic metrics.

Workflow:
  python download_ddot_data.py --start 2023-01-01 --end 2026-09-30 --output ddot_crashes.csv
  python enrich_ddot_safety.py --crashes ddot_crashes.csv --outdir phase2

Optional offline inputs:
  --hin-geojson hin.geojson --aadt-geojson aadt.geojson --workorders-geojson wo.geojson
  --verified-projects reviewed_safety_projects.csv

Outputs:
  ddot_crashes_enriched.csv; ddot_hin_corridors.csv;
  ddot_safety_workorders.csv; ddot_verified_projects.csv;
  ddot_safety_bundle.json; ddot_data_quality.json

Requires only Python >=3.9 standard library. Live source needs internet.
A crash within 35 m of an HIN segment is *proximity matched*, not
an authoritative attribution. AADT is context, NOT an exposure rate.
Work orders are NOT installations. Installation metrics require a
separately reviewed project register with evidence and install dates.
"""
import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import math
import sys
import time
import urllib.parse
import urllib.request

BASE="https://rh.dcgis.dc.gov/dcgis/rest/services/DDOT/DDOTLRS/FeatureServer"
HIN_URL=BASE+"/142"
AADT_URL=BASE+"/24"
WO_URL="https://maps2.dcgis.dc.gov/dcgis/rest/services/DDOT/Cityworks/FeatureServer/3"
SOURCES={"hin":HIN_URL,"aadt":AADT_URL,"workorders":WO_URL}
R=6371008.8

def http_json(url, params, retries=4):
    # POST: long objectIds lists overflow GET URL limits on some DCGIS servers (HTTP 404).
    body=urllib.parse.urlencode(params).encode()
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,data=body,headers={"User-Agent":"DDOT-Tableau-POC/2.0","Accept":"application/json",
                "Content-Type":"application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req,timeout=90) as resp: obj=json.load(resp)
            if isinstance(obj,dict) and "error" in obj: raise RuntimeError(str(obj["error"]))
            return obj
        except Exception as exc:
            if attempt==retries-1: raise RuntimeError(f"ArcGIS unavailable: {url}: {exc}") from exc
            time.sleep(min(2**attempt,8))

def download_layer(url):
    ids_obj=http_json(url+"/query",{"where":"1=1","returnIdsOnly":"true","f":"json"})
    ids=ids_obj.get("objectIds") or []
    if not ids: return {"type":"FeatureCollection","features":[]}
    ids=sorted(ids)
    features=[]
    for i in range(0,len(ids),250):
        data=http_json(url+"/query",{"f":"geojson","objectIds":",".join(map(str,ids[i:i+250])),
           "outFields":"*","returnGeometry":"true","outSR":4326})
        if data.get("type")!="FeatureCollection":
            raise RuntimeError(f"Expected FeatureCollection for {url} page {i//250+1}")
        features.extend(data.get("features",[]))
        print(f"  {url.rsplit('/',1)[-1]}: {min(i+250,len(ids)):,}/{len(ids):,}",end="\r",flush=True)
    print()
    # FeatureServer may mutate during pagination. Deduplicate deterministically.
    key=ids_obj.get("objectIdFieldName") or "OBJECTID"
    keyed={}
    for idx,f in enumerate(features):
        oid=f.get("properties",{}).get(key) or f.get("properties",{}).get("OBJECTID")
        keyed[str(oid) if oid is not None else f"anonymous_{idx}"]=f
    return {"type":"FeatureCollection","features":list(keyed.values())}

def load_fc(path, url):
    if path:
        with open(path,encoding="utf-8") as f: data=json.load(f)
        if data.get("type")!="FeatureCollection": raise ValueError(f"Expected GeoJSON FeatureCollection: {path}")
        return data
    print("Retrieving",url)
    return download_layer(url)

def prop(f,k,default=None):
    p=f.get("properties",{})
    if k in p: return p[k]
    upper={a.upper():v for a,v in p.items()}
    return upper.get(k.upper(),default)

def number(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError): return None

def integer(x):
    try: return int(float(x))
    except (TypeError,ValueError): return None

def day(x):
    if x is None or x=="": return ""
    if isinstance(x,(int,float)) or (isinstance(x,str) and x.isdigit() and len(x)>11):
        return datetime.fromtimestamp(int(x)/1000,timezone.utc).date().isoformat()
    return str(x)[:10]

def paths(geometry):
    if not geometry: return []
    t=geometry.get("type");coords=geometry.get("coordinates") or []
    if t=="LineString": return [coords]
    if t=="MultiLineString": return coords
    return []

def coord_ok(lat,lon):
    return lat is not None and lon is not None and 38.7<=lat<=39.1 and -77.3<=lon<=-76.7

def meters_segment(lon,lat,p,q):
    # Local tangent-plane projection around the point; accurate to <0.1% within DC.
    kx=111320*math.cos(math.radians(lat));ky=111132
    ax=(p[0]-lon)*kx;ay=(p[1]-lat)*ky
    bx=(q[0]-lon)*kx;by=(q[1]-lat)*ky
    dx=bx-ax;dy=by-ay;den=dx*dx+dy*dy
    t=max(0,min(1,-(ax*dx+ay*dy)/den)) if den else 0
    return math.hypot(ax+t*dx,ay+t*dy)

def geometry_distance(lat,lon,geometry):
    best=float("inf")
    for path in paths(geometry):
        for i in range(len(path)-1):
            p,q=path[i],path[i+1]
            if len(p)<2 or len(q)<2: continue
            best=min(best,meters_segment(lon,lat,p,q))
    return best

def geodist(p,q):
    x1,y1=math.radians(p[0]),math.radians(p[1]);x2,y2=math.radians(q[0]),math.radians(q[1])
    a=math.sin((y2-y1)/2)**2+math.cos(y1)*math.cos(y2)*math.sin((x2-x1)/2)**2
    return 2*R*math.asin(min(1,math.sqrt(a)))

def feature_length_m(f):
    return sum(geodist(a,b) for path in paths(f.get("geometry")) for a,b in zip(path,path[1:]))

def feature_mid(f):
    # Representative polyline midpoint (approximate); only for traffic-volume screening.
    long_paths=sorted(paths(f.get("geometry")),key=lambda a:len(a),reverse=True)
    if not long_paths or not long_paths[0]: return None
    path=long_paths[0]
    p=path[len(path)//2]
    return (p[1],p[0])

def point_of(f):
    g=f.get("geometry") or {}
    if g.get("type")!="Point" or not g.get("coordinates"): return None
    lon,lat=g["coordinates"][:2]
    return (lat,lon) if coord_ok(number(lat),number(lon)) else None

def bbox(f):
    pts=[a for path in paths(f.get("geometry")) for a in path if len(a)>1]
    return (min(p[0] for p in pts),min(p[1] for p in pts),
            max(p[0] for p in pts),max(p[1] for p in pts)) if pts else None

class LineLookup:
    def __init__(self,items,cell=0.004):
        self.items=items;self.cell=cell;self.cells=defaultdict(list)
        for i,x in enumerate(items):
            bb=bbox(x["feature"])
            if bb is None: continue
            a,b,c,d=[math.floor(v/cell) for v in bb]
            for X in range(a,c+1):
                for Y in range(b,d+1):self.cells[(X,Y)].append(i)
    def nearby(self,lat,lon,padding=0.0015):
        c=self.cell
        x0=math.floor((lon-padding)/c);x1=math.floor((lon+padding)/c)
        y0=math.floor((lat-padding)/c);y1=math.floor((lat+padding)/c)
        ids={i for x in range(x0,x1+1) for y in range(y0,y1+1) for i in self.cells.get((x,y),[])}
        return [self.items[i] for i in ids]
    def nearest(self,lat,lon,max_m=35,route=None):
        options=[]
        for item in self.nearby(lat,lon):
            d=geometry_distance(lat,lon,item["feature"].get("geometry"))
            if d<=max_m:
                same=bool(route and item.get("route_id") and str(route).strip().upper()==item["route_id"])
                options.append((0 if same else 1,d,item))
        if not options:return None,None,False
        options.sort(key=lambda x:(x[0],x[1]))
        a=options[0]
        return a[2],round(a[1],1),a[0]==0

def read_csv(path):
    with open(path,newline="",encoding="utf-8-sig") as f:return list(csv.DictReader(f))

def csv_out(path,records,columns):
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=columns,extrasaction="ignore")
        w.writeheader();w.writerows(records)

def make_hin(fc):
    data=[]
    for i,f in enumerate(fc.get("features",[])):
        p=f.get("properties",{})
        if not paths(f.get("geometry")):continue
        cid=str(prop(f,"CORRIDORID") or "").strip()
        route=str(prop(f,"ROUTEID") or "").strip().upper()
        tier=1 if integer(prop(f,"TIER_1"))==1 else 2 if integer(prop(f,"TIER_2"))==1 else 3 if integer(prop(f,"TIER_3"))==1 else None
        if tier is None: continue
        name=str(prop(f,"ROUTENAME") or "Unlabeled corridor")
        fr=str(prop(f,"FROMSTREET") or "").strip();to=str(prop(f,"TOSTREET") or "").strip()
        # Use stable corridor identity. Fall back to segment ID to avoid combining unrelated unknowns.
        eid=str(prop(f,"OBJECTID") or f"f{i}")
        if not cid: cid="UNKEYED-"+eid
        length=feature_length_m(f)/1609.344
        if length<=0:continue
        data.append(dict(feature=f,segment_id=eid,corridor_id=cid,route_id=route,
            corridor_name=name,from_street=fr,to_street=to,tier=tier,length_mi=length))
    return data

def make_aadt(fc):
    out=[]
    for f in fc.get("features",[]):
        a=integer(prop(f,"AADT"));yr=integer(prop(f,"AADT_YEAR"))
        if a is None or a<=0 or not paths(f.get("geometry")):continue
        out.append(dict(feature=f,segment_id=str(prop(f,"OBJECTID") or ""),
           route_id=str(prop(f,"ROUTEID") or "").strip().upper(),
           aadt=a,aadt_year=yr))
    return out

def match_aadt(h,search,max_m):
    pos=feature_mid(h["feature"])
    if not pos:return None,None,None
    match,d,same=search.nearest(*pos,max_m=max_m,route=h["route_id"])
    if not match:return None,None,None
    quality="route-and-proximity" if same else "proximity-only"
    return match,d,quality

def workorder_rows(fc,lookup,dist_m):
    out=[]
    for f in fc.get("features",[]):
        pt=point_of(f)
        if not pt:continue
        h,d,same=lookup.nearest(*pt,max_m=dist_m)
        out.append(dict(workorder_id=str(prop(f,"OBJECTID") or ""),
            request_id=str(prop(f,"REQUESTID") or ""),
            description=str(prop(f,"requestdesc") or prop(f,"PROBLEMCODE") or ""),
            status=str(prop(f,"status") or prop(f,"METStatus") or "Unknown"),
            closed_date=day(prop(f,"DATETIMECLOSED")),initiated_date=day(prop(f,"datetimeinit")),
            latitude=pt[0],longitude=pt[1],
            matched_corridor_id=h["corridor_id"] if h else "",
            matched_hin_tier=h["tier"] if h else "",
            distance_to_hin_m=d if h else "",
            status_interpretation="Work-order record only; closing does not verify installation",
            source_url=WO_URL))
    return out

def verified_rows(path,lookup,dist_m):
    if not path:return []
    required={"project_id","project_name","status","latitude","longitude","install_date","verification_source"}
    raw=read_csv(path)
    if not raw:return []
    missing=required-set(raw[0])
    if missing:raise ValueError("Verified projects CSV missing: "+", ".join(sorted(missing)))
    out=[]
    for r in raw:
        lat=number(r.get("latitude"));lon=number(r.get("longitude"))
        if not coord_ok(lat,lon):continue
        h,d,_=lookup.nearest(lat,lon,max_m=dist_m)
        status=str(r.get("status") or "").strip()
        installed=bool(status.lower() in ("installed","completed","in service") and
              r.get("install_date") and r.get("verification_source"))
        out.append(dict(project_id=r["project_id"],project_name=r["project_name"],
            status=status,install_date=r.get("install_date",""),
            verification_source=r.get("verification_source",""),
            latitude=lat,longitude=lon,
            verified_installed=int(installed),
            matched_corridor_id=h["corridor_id"] if h else "",
            matched_hin_tier=h["tier"] if h else "",
            distance_to_hin_m=d if h else ""))
    return out

def produce(crashes,hin_fc,aadt_fc,workorders_fc,projects_path=None,
            hin_distance=35,aadt_distance=25,activity_distance=50,run_at=None):
    hin=make_hin(hin_fc);aadt=make_aadt(aadt_fc)
    hlookup=LineLookup(hin);alookup=LineLookup(aadt)
    for h in hin:
        m,d,q=match_aadt(h,alookup,aadt_distance)
        h["aadt"]=m["aadt"] if m else None
        h["aadt_year"]=m["aadt_year"] if m else None
        h["aadt_quality"]=q or "unmatched"
        h["aadt_distance_m"]=d
    corridor_map={}
    for h in hin:
        cid=h["corridor_id"]
        if cid not in corridor_map:
            corridor_map[cid]=dict(corridor_id=cid,corridor_name=h["corridor_name"],
                route_id=h["route_id"],hin_tier=h["tier"],hin_miles=0.0,
                aadt_covered_miles=0.0,aadt_weighted_numerator=0.0,
                aadt_min_year=None,aadt_max_year=None,aadt_proximity_only_miles=0.0,
                segments=0,geometry={"type":"MultiLineString","coordinates":[]})
        v=corridor_map[cid];length=h["length_mi"]
        v["segments"]+=1;v["hin_miles"]+=length;v["hin_tier"]=min(v["hin_tier"],h["tier"])
        v["geometry"]["coordinates"].extend(paths(h["feature"].get("geometry")))
        if h["aadt"]:
            v["aadt_covered_miles"]+=length
            v["aadt_weighted_numerator"]+=length*h["aadt"]
            if h["aadt_quality"]=="proximity-only":v["aadt_proximity_only_miles"]+=length
            yr=h["aadt_year"]
            if yr:
                v["aadt_min_year"]=min(v["aadt_min_year"] or yr,yr)
                v["aadt_max_year"]=max(v["aadt_max_year"] or yr,yr)
    corridors=[]
    for c in corridor_map.values():
        c["aadt_coverage_pct"]=round(100*c["aadt_covered_miles"]/c["hin_miles"],1) if c["hin_miles"] else 0
        c["aadt_context"]=round(c["aadt_weighted_numerator"]/c["aadt_covered_miles"]) if c["aadt_covered_miles"] else None
        c["hin_miles"]=round(c["hin_miles"],3)
        c["aadt_covered_miles"]=round(c["aadt_covered_miles"],3)
        c["aadt_proximity_only_miles"]=round(c["aadt_proximity_only_miles"],3)
        del c["aadt_weighted_numerator"]
        corridors.append(c)
    corridors.sort(key=lambda c:(c["hin_tier"],c["corridor_name"],c["corridor_id"]))
    corridor_ref={c["corridor_id"]:c for c in corridors}
    enriched=[];matched=0;coords_missing=0
    for r in crashes:
        q=dict(r);lat=number(q.get("latitude"));lon=number(q.get("longitude"))
        h=d=same=None
        if coord_ok(lat,lon):h,d,same=hlookup.nearest(lat,lon,hin_distance)
        else:coords_missing+=1
        if h:matched+=1
        c=corridor_ref.get(h["corridor_id"]) if h else None
        q.update(hin_corridor_id=h["corridor_id"] if h else "",
            hin_corridor_name=h["corridor_name"] if h else "",
            hin_tier=h["tier"] if h else "",
            hin_distance_m=d if h else "",
            hin_spatial_match="within "+str(hin_distance)+" m" if h else "unmatched",
            hin_match_quality="proximity only; not verified segment attribution" if h else "unmatched",
            corridor_aadt_context=c["aadt_context"] if c else "",
            corridor_aadt_coverage_pct=c["aadt_coverage_pct"] if c else "",
            corridor_aadt_year_min=c["aadt_min_year"] if c else "",
            corridor_aadt_year_max=c["aadt_max_year"] if c else "")
        enriched.append(q)
    workorders=workorder_rows(workorders_fc,hlookup,activity_distance)
    projects=verified_rows(projects_path,hlookup,activity_distance)
    quality={"created_utc":run_at or datetime.now(timezone.utc).isoformat(),
      "sources":SOURCES|{"crashes":"https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/FeatureServer/24"},
      "crash_rows":len(enriched),"hin_segments":len(hin),"hin_corridors":len(corridors),
      "aadt_segments":len(aadt),"traffic_safety_workorder_points":len(workorders),
      "verified_project_rows":len(projects),"crashes_near_hin":matched,
      "crashes_no_valid_coordinates":coords_missing,
      "hin_segment_miles":round(sum(h["length_mi"] for h in hin),2),
      "aadt_matched_hin_miles":round(sum(c["aadt_covered_miles"] for c in corridors),2),
      "join_thresholds_m":{"crash_to_hin":hin_distance,"hin_to_aadt":aadt_distance,"workorder_to_hin":activity_distance},
      "methodology_notes":[
        "Matches are proximity-only spatial screens; parallel roads and intersections may misassign a corridor.",
        "HIN corridor features may overlap and total segment mileage may double-count overlapping linework.",
        "AADT context is length-weighted across matched HIN lines, not a validated VMT denominator.",
        "Traffic Safety Work Orders are operational activity and cannot demonstrate installed treatments.",
        "Verified completed projects require a human-reviewed register with installation evidence.",
        "Crash dates are REPORTDATE, not guaranteed incident dates.",
        "Year-over-year comparison must use equivalent month/day windows; source completeness still requires review."
      ]}
    return enriched,corridors,workorders,projects,quality

def write_outputs(outdir,results):
    outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
    enriched,corridors,workorders,projects,quality=results
    base_cols=list(enriched[0]) if enriched else [
       "crash_id","report_date","ward","latitude","longitude","fatalities","major_injuries","ksi_crash",
       "hin_corridor_id","hin_corridor_name","hin_tier","hin_distance_m","corridor_aadt_context"]
    csv_out(outdir/"ddot_crashes_enriched.csv",enriched,base_cols)
    csv_out(outdir/"ddot_hin_corridors.csv",corridors,[
        "corridor_id","corridor_name","route_id","hin_tier","hin_miles","segments","aadt_context",
        "aadt_coverage_pct","aadt_covered_miles","aadt_proximity_only_miles",
        "aadt_min_year","aadt_max_year"])
    csv_out(outdir/"ddot_safety_workorders.csv",workorders,[
        "workorder_id","request_id","description","status","initiated_date","closed_date",
        "latitude","longitude","matched_corridor_id","matched_hin_tier","distance_to_hin_m",
        "status_interpretation","source_url"])
    csv_out(outdir/"ddot_verified_projects.csv",projects,[
        "project_id","project_name","status","install_date","verification_source","latitude",
        "longitude","verified_installed","matched_corridor_id","matched_hin_tier","distance_to_hin_m"])
    geo={"type":"FeatureCollection","features":[{"type":"Feature","properties":{
        k:v for k,v in c.items() if k!="geometry"},
        "geometry":c["geometry"]} for c in corridors]}
    with open(outdir/"ddot_hin_geometry.geojson","w",encoding="utf-8") as f:json.dump(geo,f)
    with open(outdir/"ddot_data_quality.json","w",encoding="utf-8") as f:json.dump(quality,f,indent=2)
    with open(outdir/"ddot_safety_bundle.json","w",encoding="utf-8") as f:
        json.dump({"meta":quality,"crashes":enriched,"corridors":corridors,
                   "workorders":workorders,"verified_projects":projects},f,separators=(",",":"))
    print("Outputs in",outdir)
    print(f"{len(enriched):,} crash rows; {len(corridors):,} HIN corridors; "
          f"{len(workorders):,} safety work orders; {len(projects):,} verified-project records.")

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--crashes",required=True,help="CSV created with download_ddot_data.py")
    p.add_argument("--outdir",default="ddot_phase2_output")
    p.add_argument("--hin-geojson");p.add_argument("--aadt-geojson");p.add_argument("--workorders-geojson")
    p.add_argument("--verified-projects",help="Reviewed CSV. Never substitute work orders for installed projects.")
    p.add_argument("--hin-distance",type=float,default=35)
    p.add_argument("--aadt-distance",type=float,default=25)
    p.add_argument("--activity-distance",type=float,default=50)
    args=p.parse_args(argv)
    for n in ("hin_distance","aadt_distance","activity_distance"):
        if not 0<getattr(args,n)<=200:p.error(f"--{n.replace('_','-')} must be >0 and <=200 meters")
    crashes=read_csv(args.crashes)
    if crashes and not {"crash_id","report_date","latitude","longitude"}.issubset(crashes[0]):
        p.error("Input CSV must come from download_ddot_data.py (required crash_id, report_date, lat/lon)")
    layers=[load_fc(args.hin_geojson,HIN_URL),load_fc(args.aadt_geojson,AADT_URL),
        load_fc(args.workorders_geojson,WO_URL)]
    results=produce(crashes,*layers,projects_path=args.verified_projects,
       hin_distance=args.hin_distance,aadt_distance=args.aadt_distance,
       activity_distance=args.activity_distance)
    write_outputs(args.outdir,results)
    if not results[1]:
        print("WARNING: No HIN corridors loaded. Check ArcGIS access or GeoJSON inputs.",file=sys.stderr)

if __name__=="__main__":
    main()
