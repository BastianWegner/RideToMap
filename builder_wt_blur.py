"""
RideToMap Builder
-----------------
Dieses Skript liest GPX-Dateien ein, analysiert sie und erstellt eine
interaktive HTML-Karte mit Statistiken und einem Netzwerk-Graphen.

Features:
- Multicore-Processing für hohe Geschwindigkeit
- Datenschutz-Zone (Privacy Circle) um den Wohnort
- Erkennung von Bundesländern/Ländern (Reverse Geocoding)
- Interaktives Dashboard (Karte/Liste/Graph umschaltbar)
"""

import folium
import gpxpy
import os
import datetime
import argparse
import math
import reverse_geocoder as rg
from collections import Counter
import itertools
import json
from multiprocessing import Pool 

# ==========================================
# KONFIGURATION & KONSTANTEN
# ==========================================

GPX_ORDNER = "tracks"
AUSGABE_DATEI = "index.html"
KARTE_DATEI = "map_part.html"

# Kartendesign (Neon/Heatmap Look)
LINIEN_FARBE = "#ff3300" 
LINIEN_BREITE = 1.4      
LINIEN_OPACITY = 0.15    

# Kartenstart
START_KOORDINATEN = [50.0556, 8.4009] # Mittelpunkt FFM
ZOOM_START = 9

# Privatsphäre (Zuhause): bewusst nicht im Quellcode speichern.
# Alternativ zu den CLI-Argumenten können diese Umgebungsvariablen gesetzt werden.
HOME_LAT = float(os.environ["RIDETOMAP_HOME_LAT"]) if os.environ.get("RIDETOMAP_HOME_LAT") else None
HOME_LON = float(os.environ["RIDETOMAP_HOME_LON"]) if os.environ.get("RIDETOMAP_HOME_LON") else None

# Mapping: Wandelt englische/kurze Namen in schöne deutsche Namen um
STATE_MAPPING = {
    "Bavaria": "Bayern", "Hesse": "Hessen", "North Rhine-Westphalia": "NRW",
    "Rhineland-Palatinate": "RLP", "Saxony": "Sachsen",
    "Lower Saxony": "Niedersachsen", "Thuringia": "Thüringen",
    "Baden-Wurttemberg": "BaWü", "Mecklenburg-Vorpommern": "MeckPomm",
    "Saxony-Anhalt": "Sachsen-Anhalt", "Brandenburg": "Brandenburg",
    "Schleswig-Holstein": "Schleswig-Holstein", 
    "Saarland": "Saarland", "Berlin": "Berlin", "Hamburg": "Hamburg", "Bremen": "Bremen"
}

COUNTRY_MAPPING = {
    "AT": "Österreich", "CH": "Schweiz", "FR": "Frankreich", "NL": "Niederlande",
    "BE": "Belgien", "LU": "Luxemburg", "DK": "Dänemark", "PL": "Polen",
    "CZ": "Tschechien", "IT": "Italien", "ES": "Spanien",
    "AD": "Andorra",
}

# ==========================================
# HILFSFUNKTIONEN
# ==========================================

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Berechnet die Entfernung zwischen zwei Koordinaten in Metern.
    Verwendet die Haversine-Formel für die Erdkrümmung.
    """
    R = 6371000  # Erdradius in Metern
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

def get_location_info(lat, lon):
    """
    Ermittelt anhand von Koordinaten das Bundesland oder Land.
    Nutzt 'reverse_geocoder' (Offline-Bibliothek, sehr schnell).
    """
    try:
        # mode=1 sucht nur das nächste Ergebnis (schneller)
        results = rg.search((lat, lon), mode=1)
        if results:
            data = results[0]
            cc = data['cc']      # Country Code (z.B. DE)
            admin = data['admin1'] # Verwaltungsbezirk (z.B. Hesse)
            
            if cc == 'DE': 
                return STATE_MAPPING.get(admin, admin)
            else: 
                return COUNTRY_MAPPING.get(cc, cc)
    except Exception: 
        return None
    return None

# ==========================================
# KERNOPEARTION (Wird parallel ausgeführt)
# ==========================================

def process_file(filename, privacy_radius, home_lat, home_lon):
    """ 
    Verarbeitet eine einzelne GPX-Datei. 
    Wird von multiprocessing.Pool auf einem eigenen CPU-Kern ausgeführt.
    
    Returns:
        Dictionary mit Metadaten und Segmenten oder None bei Fehler.
    """
    path = os.path.join(GPX_ORDNER, filename)

    try:
        with open(path, 'r', encoding='utf-8') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
            
            # --- 1. Metadaten extrahieren ---
            km = round(gpx.length_2d() / 1000, 2)
            
            # Name aus dem Track holen, fallback auf Dateinamen
            track_name = filename
            if gpx.tracks and gpx.tracks[0].name:
                track_name = gpx.tracks[0].name.strip()
            
            # Datum ermitteln
            time_bounds = gpx.get_time_bounds()
            if time_bounds and time_bounds.start_time:
                dt_obj = time_bounds.start_time
            else:
                dt_obj = datetime.datetime.min
            date_str = dt_obj.strftime("%m.%Y")
            
            # --- 2. Track Analyse ---
            found_regions = set()
            polyline_segments = []
            last_pt = None
            cumulative_dist = 0.0
            last_check_dist = 0.0

            # Startpunkt prüfen (um Startregion sicher zu haben)
            if gpx.tracks and gpx.tracks[0].segments and gpx.tracks[0].segments[0].points:
                start_pt = gpx.tracks[0].segments[0].points[0]
                loc = get_location_info(start_pt.latitude, start_pt.longitude)
                if loc: found_regions.add(loc)

            for track in gpx.tracks:
                # OPTIMIERUNG: Track vereinfachen, bevor wir iterieren.
                # Reduziert Punkte, die auf einer geraden Linie liegen.
                track.simplify(max_distance=10)
                
                for segment in track.segments:
                    current_segment = []
                    
                    for point in segment.points:
                        lat, lon = point.latitude, point.longitude

                        # OPTIMIERUNG: Region nur alle 10km prüfen (spart Rechenzeit beim Geocoding)
                        if last_pt:
                            dist = haversine_distance(last_pt.latitude, last_pt.longitude, point.latitude, point.longitude)
                            cumulative_dist += dist
                            
                            if (cumulative_dist - last_check_dist) >= 10000:
                                loc = get_location_info(point.latitude, point.longitude)
                                if loc: found_regions.add(loc)
                                last_check_dist = cumulative_dist
                        
                        # PRIVATSPHÄRE: Check ob innerhalb des Radius um Zuhause
                        in_privacy_zone = False
                        if privacy_radius > 0:
                            dist_to_home = haversine_distance(lat, lon, home_lat, home_lon)
                            if dist_to_home < privacy_radius:
                                in_privacy_zone = True
                        
                        if in_privacy_zone:
                            # Wenn wir in die Zone eintauchen, beenden wir das aktuelle Segment (Linie unterbrechen)
                            if current_segment:
                                polyline_segments.append(current_segment)
                                current_segment = [] 
                        else:
                            # Außerhalb der Zone: Punkt hinzufügen
                            current_segment.append((lat, lon))
                        
                        last_pt = point 
                    
                    # Reste speichern
                    if current_segment: polyline_segments.append(current_segment)

            region_list = sorted(list(found_regions))
            
            return {
                "metadata": {
                    "name": track_name, 
                    "km": km, 
                    "date": date_str, 
                    "dt_obj": dt_obj, 
                    "region_list": region_list, 
                    "filename": filename
                },
                "segments": polyline_segments
            }

    except Exception as e:
        # Bei defekten Dateien nicht abstürzen, sondern überspringen
        print(f"Fehler bei Datei {filename}: {e}")
        return None 

# ==========================================
# HAUPTPROGRAMM
# ==========================================

def main():
    # Argumente verarbeiten (z.B. für Tests oder Privacy-Radius)
    parser = argparse.ArgumentParser(description="Erstellt eine Web-Karte aus GPX Tracks")
    parser.add_argument("-t", "--test", type=int, default=0, help="Anzahl der Dateien beschränken (für schnelle Tests)")
    parser.add_argument("-p", "--privacy", type=int, default=0, help="Radius in Metern um Home, der ausgeblendet wird")
    parser.add_argument("--home-lat", type=float, default=HOME_LAT, help="Breitengrad der Privacy-Zone (alternativ RIDETOMAP_HOME_LAT)")
    parser.add_argument("--home-lon", type=float, default=HOME_LON, help="Längengrad der Privacy-Zone (alternativ RIDETOMAP_HOME_LON)")
    args = parser.parse_args()

    if args.privacy > 0 and (args.home_lat is None or args.home_lon is None):
        parser.error(
            "--privacy benötigt --home-lat und --home-lon "
            "(oder RIDETOMAP_HOME_LAT/RIDETOMAP_HOME_LON)."
        )

    print(f"--- RideToMap Builder gestartet ---")

    # Dateien suchen
    if not os.path.exists(GPX_ORDNER):
        print(f"Fehler: Ordner '{GPX_ORDNER}' nicht gefunden.")
        return

    files = [f for f in os.listdir(GPX_ORDNER) if f.lower().endswith(".gpx")]
    if args.test > 0: 
        print(f"Testmodus: Verarbeite nur die ersten {args.test} Dateien.")
        files = files[:args.test]

    # Datenpaket für die Worker schnüren
    worker_data = [
        (filename, args.privacy, args.home_lat, args.home_lon)
        for filename in files
    ]
    
    # ------------------------------------------
    # 1. PARALLELE VERARBEITUNG STARTEN
    # ------------------------------------------
    results = [] 
    cpu_cores = os.cpu_count() or 1
    
    if worker_data:
        print(f"Starte Verarbeitung auf {cpu_cores} CPU-Kernen für {len(worker_data)} Dateien...")
        with Pool(processes=cpu_cores) as pool:
            # starmap erlaubt die Übergabe mehrerer Argumente an die Worker-Funktion
            results = pool.starmap(process_file, worker_data)
    else:
        print("Keine GPX-Dateien gefunden.")
        return

    # Fehlerhafte Ergebnisse (None) herausfiltern
    valid_results = [r for r in results if r is not None]
    print(f"Verarbeitung abgeschlossen. {len(valid_results)} Tracks erfolgreich analysiert.")

    # ------------------------------------------
    # 2. DATEN AGGREGIEREN & KARTE ERSTELLEN
    # ------------------------------------------
    print("Erstelle Karte und Statistiken...")

    touren_liste = []
    node_counts = Counter()
    edge_counts = Counter()
    
    # Basiskarte initialisieren
    m = folium.Map(location=START_KOORDINATEN, zoom_start=ZOOM_START, tiles=None)
    folium.TileLayer('cartodbvoyager', name='Clear', control=True, show=True).add_to(m)
    folium.TileLayer('cartodbdark_matter', name='Black', control=True, show=False).add_to(m)
    
    for result in valid_results:
        meta = result['metadata']
        segments = result['segments']
        
        # Liste für Tabelle
        touren_liste.append({
            "name": meta['name'], 
            "km": meta['km'], 
            "datum_str": meta['date'],
            "datum_obj": meta['dt_obj'], 
            "region": ", ".join(meta['region_list']),
            "filename": meta['filename']
        })
        
        # Graph-Daten: Knoten zählen
        for r in meta['region_list']: 
            node_counts[r] += 1
        
        # Graph-Daten: Verbindungen (Kanten) zählen
        sorted_regions = sorted(meta['region_list'])
        for r1, r2 in itertools.combinations(sorted_regions, 2):
            edge_counts[(r1, r2)] += 1

        # Track auf Karte zeichnen
        for seg_points in segments:
            if len(seg_points) > 1:
                folium.PolyLine(
                    seg_points, 
                    color=LINIEN_FARBE, 
                    weight=LINIEN_BREITE, 
                    opacity=LINIEN_OPACITY, 
                    tooltip=f"{meta['date']}: {meta['name']}"
                ).add_to(m)

    # ------------------------------------------
    # 3. KOSMOS GRAPH (ECHARTS) VORBEREITEN
    # ------------------------------------------
    
    # Knoten erstellen
    echarts_nodes = []
    max_count = max(node_counts.values()) if node_counts else 1
    
    for region, count in node_counts.items():
        # Größe dynamisch berechnen (min 15, max 75)
        size = 15 + (count / max_count) * 60 
        echarts_nodes.append({
            "id": region, 
            "name": region, 
            "symbolSize": size, 
            "value": count, 
            "category": 0
        })
    
    # Kanten erstellen
    echarts_links = []
    for (source, target), weight in edge_counts.items():
        echarts_links.append({
            "source": source, 
            "target": target, 
            "value": weight, 
            "lineStyle": {"width": 0.5 + (weight * 0.3)}
        })

    nodes_json = json.dumps(echarts_nodes)
    links_json = json.dumps(echarts_links)

    # Karte speichern
    folium.LayerControl(position='topright', collapsed=False).add_to(m)
    m.save(KARTE_DATEI)
    
    # ------------------------------------------
    # 4. HTML ZUSAMMENBAUEN
    # ------------------------------------------
    
    # Sortieren nach Datum
    touren_liste.sort(key=lambda x: x['datum_obj'])

    # Tabellenzeilen generieren (KEIN FILTER MEHR AKTIV)
    table_rows = ""
    for tour in touren_liste:
        table_rows += f"""
            <tr>
                <td class="col-name" title="{tour['name']}">{tour['name']}</td>
                <td class="col-region" title="{tour['region']}">{tour['region']}</td>
                <td class="col-km">{tour['km']} km</td>
                <td class="col-date">{tour['datum_str']}</td>
            </tr>
            """

    # Das finale HTML Template
    html_content = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
        <title>RIDE TO MAP</title>
        <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
        
        <style>
            /* --- CSS STYLES --- */
            
            /* ZUSTÄNDE FÜR DAS LAYOUT (Maximieren/Minimieren) */
            
            /* 1. Karte Maximiert (Standard) */
            body.map-maximized #map-container {{ flex: 1 1 auto; height: auto; min-height: 0; border-bottom: none; }}
            body.map-maximized #content-area {{ flex: 0 0 0; height: 0; min-height: 0; border-top: none; }}
            body.map-maximized #control-bar {{ border-bottom: none; }}
            
            /* 2. Inhalt Maximiert (Tabelle/Graph groß) */
            body.content-maximized #map-container {{ flex: 0 0 48px; height: 48px; min-height: 48px; border-bottom: 1px solid #e0e0e0; }}
            body.content-maximized #content-area {{ flex: 1 1 auto; height: auto; min-height: 0; border-top: 1px solid #e0e0e0; }}
            
            /* Allgemeines Layout */
            html, body {{ margin: 0; padding: 0; height: 100%; }}
            body {{
                height: 100dvh;
                max-height: 100dvh;
                display: flex;
                flex-direction: column;
                overflow: hidden;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: #fff;
                color: #333;
            }}
            
            #map-container {{ flex: 1 1 auto; width: 100%; min-height: 0; border-bottom: 1px solid #e0e0e0; }}
            iframe {{ width: 100%; height: 100%; border: none; display: block; }}
            
            /* Steuerleiste */
            #control-bar {{
                flex: 0 0 auto;
                height: 48px;
                min-height: 48px;
                background-color: #f9f9f9;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 0 15px;
                gap: 15px;
                z-index: 20;
                padding-bottom: env(safe-area-inset-bottom, 0px);
            }}
            
            /* Toggle Button (Pfeil) */
            #btn-toggle-main {{
                background: #fff; color: #555; font-size: 16px; font-weight: 700;
                padding: 4px 10px; border: 1px solid #ccc; border-radius: 6px; 
                cursor: pointer; user-select: none; transition: background 0.2s, color 0.2s;
            }}
            #btn-toggle-main:hover {{ background: #eee; color: #333; }}
            
            /* Umschalter Logbuch/Kosmos */
            .segmented-control {{ display: flex; background: #e0e0e0; border-radius: 8px; padding: 3px; }}
            .toggle-btn {{
                padding: 4px 20px; font-size: 12px; font-weight: 600; color: #777; cursor: pointer; border-radius: 6px; 
                user-select: none; transition: all 0.2s; text-transform: uppercase; letter-spacing: 0.5px;
            }}
            .toggle-btn.active {{
                background: #fff; color: #000; box-shadow: 0 1px 4px rgba(0,0,0,0.15);
            }}

            /* Inhaltsbereich */
            #content-area {{ position: relative; width: 100%; overflow: hidden; min-height: 0; }}
            
            #list-view {{ height: 100%; width: 100%; overflow-y: auto; background: #fff; display: block; -webkit-overflow-scrolling: touch; }}
            #graph-view {{ height: 100%; width: 100%; display: none; background-color: #fcfcfc; }}
            
            /* Tabelle */
            table {{ width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }}
            th {{ text-align: left; padding: 10px 15px; color: #999; font-weight: 500; font-size: 11px; letter-spacing: 1px; text-transform: uppercase; border-bottom: 1px solid #f0f0f0; position: sticky; top: 0; background: #fff; z-index: 10; }}
            td {{ padding: 8px 15px; border-bottom: 1px solid #f8f8f8; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
            tr:hover td {{ background: #f5f5f5; cursor: pointer; color: #000; }}
            
            .col-name {{ width: 45%; }} .col-region {{ width: 25%; color: #666; font-size: 11px; text-transform: uppercase; }}
            .col-km {{ width: 15%; text-align: right; color: #888; }} .col-date {{ width: 15%; text-align: right; color: #aaa; }}
            ::-webkit-scrollbar {{ width: 6px; }} ::-webkit-scrollbar-thumb {{ background: #eee; border-radius: 3px; }}
        </style>
    </head>
    <body class="map-maximized">
        <div id="map-container"><iframe src="{KARTE_DATEI}"></iframe></div>
        
        <div id="control-bar">
            <div id="btn-toggle-main" onclick="toggleMaximize()" title="Ansicht vergrößern/verkleinern">
                <span id="toggle-icon">▼</span>
            </div>

            <div class="segmented-control">
                <div id="btn-list" class="toggle-btn active" onclick="switchView('list')">Logbuch</div>
                <div id="btn-graph" class="toggle-btn" onclick="switchView('graph')">Kosmos</div>
            </div>
        </div>
        
        <div id="graph-info" style="display:none; text-align:center; font-size:11px; color:#999; margin-top:0; padding:5px 0; background:#f9f9f9; border-bottom:1px solid #e0e0e0;">
            Kreise zeigen die Häufigkeit der Tour im Bundesland &middot; Linien markieren grenzüberschreitende Fahrten.
        </div>

        <div id="content-area">
            <div id="list-view">
                <table>
                    <thead><tr><th class="col-name">Name</th><th class="col-region">Region</th><th class="col-km" style="text-align:right">Distanz</th><th class="col-date" style="text-align:right">Datum</th></tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            </div>
            <div id="graph-view"></div>
        </div>

        <script type="text/javascript">
            // ECharts Initialisierung
            var chartDom = document.getElementById('graph-view');
            var myChart = echarts.init(chartDom);
            var nodes = {nodes_json};
            var links = {links_json};

            var option = {{
                backgroundColor: '#fcfcfc',
                tooltip: {{}},
                series: [
                    {{
                        type: 'graph', layout: 'force', data: nodes, links: links, roam: true, draggable: true,
                        label: {{ show: true, position: 'right', formatter: '{{b}}', color: '#333', fontSize: 11, fontFamily: 'sans-serif' }},
                        itemStyle: {{ color: '#ff3300', shadowBlur: 0, borderColor: '#fff', borderWidth: 1 }},
                        lineStyle: {{ color: '#ccc', curveness: 0.1, width: 1 }},
                        force: {{ repulsion: 350, gravity: 0.1, edgeLength: 50, layoutAnimation: true }}
                    }}
                ]
            }};

            myChart.setOption(option);

            // Leaflet Karte neu berechnen (da im IFrame)
            function resizeMap() {{
                const iframe = document.querySelector('#map-container iframe');
                if (iframe && iframe.contentWindow && iframe.contentWindow.map && iframe.contentWindow.map.invalidateSize) {{
                    iframe.contentWindow.map.invalidateSize();
                }}
            }}

            // Umschalten zwischen Logbuch und Kosmos
            function switchView(view) {{
                var listDiv = document.getElementById('list-view');
                var graphDiv = document.getElementById('graph-view');
                var infoDiv = document.getElementById('graph-info');
                var btnList = document.getElementById('btn-list');
                var btnGraph = document.getElementById('btn-graph');

                if (view === 'list') {{
                    listDiv.style.display = 'block'; graphDiv.style.display = 'none'; infoDiv.style.display = 'none';
                    btnList.classList.add('active'); btnGraph.classList.remove('active');
                }} else {{
                    listDiv.style.display = 'none'; graphDiv.style.display = 'block'; infoDiv.style.display = 'block';
                    btnList.classList.remove('active'); btnGraph.classList.add('active');
                    myChart.resize();
                }}
            }}

            // Layout umschalten (Karte groß <-> Inhalt groß)
            function toggleMaximize() {{
                const body = document.body;
                const icon = document.getElementById('toggle-icon');

                if (body.classList.contains('map-maximized')) {{
                    body.classList.remove('map-maximized');
                    body.classList.add('content-maximized');
                    icon.innerHTML = '▲'; 
                }} else {{
                    body.classList.remove('content-maximized');
                    body.classList.add('map-maximized');
                    icon.innerHTML = '▼'; 
                }}

                setTimeout(() => {{
                    myChart.resize();
                    resizeMap();
                }}, 300);
            }}

            window.addEventListener('resize', function() {{ myChart.resize(); resizeMap(); }});
            
            // Start-Icon setzen
            window.onload = function() {{
                document.getElementById('toggle-icon').innerHTML = '▼';
                resizeMap();
            }};
        </script>
    </body>
    </html>
    """
    
    with open(AUSGABE_DATEI, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"Fertig! Karte wurde erstellt: {AUSGABE_DATEI}")

if __name__ == "__main__":
    main()