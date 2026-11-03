"""Build storms.json (active tropical cyclones, all basins) from GDACS.

GDACS (Global Disaster Alert and Coordination System, EC JRC / UN OCHA) republishes the official
JTWC / NHC / RSMC warnings as tracks. This script keeps the format the UA Maritime Distances page expects:
storm = {id,name,basin,agency,advisory,time,lat,lon,wind_kt,pressure_mb,past,forecast,note,sources}
past/forecast points = [time, lat, lon, wind_kt or class string ('TD','TS','HU'...)].
Usage: python update_storms.py [--offline list.json geom_dir]
"""
import json, sys, datetime as dt, urllib.request, time

UA = {'User-Agent': 'uamaritime-storm-feed (+https://uamaritime.com/distances/)'}
API = 'https://www.gdacs.org/gdacsapi/api'


def get(url, tries=3):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read())
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def basin(lat, lon):
    if lat >= 0:
        if 30 <= lon < 100: return 'North Indian Ocean'
        if 100 <= lon or lon < -180 + 0.01: return 'Western Pacific'
        if -180 <= lon < -140: return 'Central Pacific'
        if -140 <= lon < -100 or (lon < -80 and lat < 16): return 'Eastern Pacific'
        return 'Atlantic'
    if 20 <= lon < 90: return 'South Indian Ocean'
    if 90 <= lon < 160: return 'Australian region'
    if lon >= 160 or lon < -120: return 'South Pacific'
    return 'South Atlantic'


def iso(t):
    return t.strftime('%Y-%m-%dT%H:%MZ')


def parse_label(lbl, year, ref_month):
    # '01/11 00:00 UTC' -> datetime (day/month)
    d, rest = lbl.split('/', 1)
    m, hm = rest.split(' ')[0], rest.split(' ')[1]
    y = year + (1 if int(m) < ref_month - 6 else 0)
    return dt.datetime(y, int(m), int(d), int(hm[:2]), int(hm[3:5]))


def centroid(geom):
    ring = geom['coordinates'][0]
    xs = [p[0] for p in ring[:-1]]; ys = [p[1] for p in ring[:-1]]
    return sum(ys) / len(ys), sum(xs) / len(xs)


def build_storm(ev, geom):
    p = ev['properties']
    fs = geom['features']
    start = dt.datetime.fromisoformat(p['fromdate'][:19])
    lines = [f for f in fs if f['geometry']['type'] == 'LineString']
    ep_time = None
    for f in fs:
        pd = f['properties'].get('polygondate')
        if f['properties'].get('featuretype') == 'PointRadii' and pd:
            ep_time = dt.datetime.fromisoformat(pd[:19]); break
    pts = []
    for f in fs:
        pr = f['properties']
        if pr.get('featuretype') != 'PointRadii': continue
        t = parse_label(pr['polygonlabel'], start.year, start.month)
        la, lo = centroid(f['geometry'])
        pts.append([t, round(la, 2), round(lo, 2)])
    pts.sort(key=lambda x: x[0])
    # class of each point from the track segment that ends (past) or starts (forecast) at it
    def cls(la, lo):
        best, bd = None, 1e9
        for f in lines:
            for q in f['geometry']['coordinates']:
                d = abs(q[1] - la) + abs(q[0] - lo)
                if d < bd: bd, best = d, f['properties'].get('polygonlabel')
        return best if bd < 0.5 else None
    if ep_time is None:
        ep_time = dt.datetime.fromisoformat(p['todate'][:19])
    past = [q for q in pts if q[0] <= ep_time]
    fc = [q for q in pts if q[0] > ep_time]
    if not past:
        la, lo = p.get('lat'), p.get('lon')
        return None
    cur = past[-1]
    sev = (p.get('severitydata') or {})
    kmh = sev.get('severity')
    wind = round(kmh * 0.539957) if isinstance(kmh, (int, float)) else None
    name = p.get('eventname', '').rsplit('-', 1)[0].upper()
    def pt(q): return [iso(q[0]), q[1], q[2], cls(q[1], q[2]) or 'TS']
    lon = cur[2] if cur[2] <= 180 else cur[2] - 360
    return {
        'id': 'GDACS ' + str(p['eventid']), 'name': name, 'basin': basin(cur[1], lon),
        'agency': 'GDACS', 'advisory': f"GDACS episode {p.get('episodeid')} ({ep_time.strftime('%d %b %H:%M')} UTC)",
        'time': iso(cur[0]), 'lat': cur[1], 'lon': lon, 'wind_kt': wind, 'pressure_mb': None,
        'past': [pt(q) for q in past[:-1]][-40:], 'forecast': [pt(q) for q in fc],
        'note': 'Wind is the highest sustained wind reported for this storm so far (GDACS).',
        'sources': [p.get('url', {}).get('report') if isinstance(p.get('url'), dict) else 'https://www.gdacs.org/'],
    }


def main():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    offline = '--offline' in sys.argv
    if offline:
        lst = json.load(open(sys.argv[2])); geo = lambda e, ep: json.load(open(sys.argv[3]))
    else:
        frm = (now - dt.timedelta(days=12)).strftime('%Y-%m-%d')
        lst = get(f'{API}/events/geteventlist/SEARCH?eventlist=TC&alertlevel=Green;Orange;Red&fromdate={frm}&todate={now.strftime("%Y-%m-%d")}')
        geo = lambda e, ep: get(f'{API}/polygons/getgeometry?eventtype=TC&eventid={e}&episodeid={ep}')
    storms, errors = [], []
    for ev in lst.get('features', []):
        p = ev['properties']
        if str(p.get('iscurrent')).lower() != 'true': continue
        try:
            todate = dt.datetime.fromisoformat(p['todate'][:19])
            if now - todate > dt.timedelta(hours=36): continue
            s = build_storm(ev, geo(p['eventid'], p['episodeid']))
            if s: storms.append(s)
        except Exception as e:
            errors.append(f"{p.get('eventname')}: {e}")
    doc = {
        'updatedAt': iso(now),
        'note': 'Active tropical cyclones from GDACS (EC JRC / UN OCHA), which republishes JTWC, NHC and regional warnings. Refreshed about every 3 hours.',
        'basinsChecked': ['Atlantic', 'Eastern Pacific', 'Central Pacific', 'Western Pacific', 'North Indian Ocean', 'Southern Hemisphere'],
        'source': 'GDACS',
        'storms': storms,
    }
    for s in storms:
        assert abs(s['lat']) <= 60 and -180 <= s['lon'] <= 180
    json.dump(doc, open('storms.json', 'w'), ensure_ascii=False, separators=(',', ':'))
    print('storms:', [s['name'] for s in storms], 'errors:', errors)
    if errors and not storms and lst.get('features'):
        sys.exit(1)


if __name__ == '__main__':
    main()
