"""
RideToMap Builder - Heat Edition
--------------------------------
Experimentelle Variante des bestehenden Builders:
- helle CARTO-Positron-Karte als Standard
- orange SVG-Tracks mit Multiply-Blending
- eigene Ausgabedateien, damit der bisherige Stand unverändert bleibt
"""

from pathlib import Path
import re

import builder_wt_blur as base


# Eigene Ausgaben: index.html und map_part.html werden nicht überschrieben.
base.AUSGABE_DATEI = "index_heat.html"
base.KARTE_DATEI = "map_part_heat.html"

# Warmer, auf hellen Karten gut sichtbarer Heat-Look.
base.LINIEN_FARBE = "#ff5a1f"
base.LINIEN_BREITE = 2.0
base.LINIEN_OPACITY = 0.25


MULTIPLY_STYLE = """
    <style id="ride-to-map-heat-style">
        /* No multiply: on mobile it turns orange tracks black over dark tiles. */
        body.heat-base-light .leaflet-overlay-pane svg path {
            mix-blend-mode: normal;
        }
        body.heat-base-dark .leaflet-overlay-pane svg path {
            mix-blend-mode: screen;
        }
        body.heat-zoom-far .leaflet-overlay-pane svg path {
            stroke-width: 1.4px !important;
            stroke-opacity: 0.22 !important;
        }
        body.heat-zoom-medium .leaflet-overlay-pane svg path {
            stroke-width: 1.8px !important;
            stroke-opacity: 0.32 !important;
        }
        body.heat-zoom-near .leaflet-overlay-pane svg path {
            stroke-width: 2.2px !important;
            stroke-opacity: 0.42 !important;
        }
    </style>
"""


def use_positron_as_default():
    """Ersetzt nur den bisherigen hellen Voyager-Layer durch Positron."""
    original_tile_layer = base.folium.TileLayer

    def heat_tile_layer(tiles=None, *args, **kwargs):
        if tiles == "cartodbvoyager":
            tiles = "cartodbpositron"
        return original_tile_layer(tiles, *args, **kwargs)

    base.folium.TileLayer = heat_tile_layer
    return original_tile_layer


def inject_multiply_style():
    """Injiziert Blend-Modus und zoomabhängige Darstellung ins iframe."""
    map_path = Path(base.KARTE_DATEI)
    html = map_path.read_text(encoding="utf-8")

    if "</head>" not in html:
        raise RuntimeError(f"Kein </head>-Element in {map_path} gefunden.")

    map_match = re.search(r"var (map_[a-f0-9]+) = L\.map", html)
    if not map_match:
        raise RuntimeError(f"Leaflet-Kartenvariable in {map_path} nicht gefunden.")

    map_variable = map_match.group(1)
    zoom_script = f"""
    <script id="ride-to-map-heat-zoom">
        (function () {{
            const heatMap = {map_variable};
            const zoomClasses = [
                "heat-zoom-far",
                "heat-zoom-medium",
                "heat-zoom-near"
            ];
            const baseClasses = ["heat-base-light", "heat-base-dark"];

            function updateHeatZoom() {{
                const zoom = heatMap.getZoom();
                document.body.classList.remove.apply(document.body.classList, zoomClasses);
                document.body.classList.add(
                    zoom <= 7
                        ? "heat-zoom-far"
                        : zoom <= 10
                            ? "heat-zoom-medium"
                            : "heat-zoom-near"
                );
            }}

            function updateHeatBase() {{
                let isDark = false;
                heatMap.eachLayer(function (layer) {{
                    if (layer instanceof L.TileLayer) {{
                        const url = layer._url || "";
                        if (url.indexOf("dark_all") !== -1 && heatMap.hasLayer(layer)) {{
                            isDark = true;
                        }}
                    }}
                }});
                document.body.classList.remove.apply(document.body.classList, baseClasses);
                document.body.classList.add(isDark ? "heat-base-dark" : "heat-base-light");
            }}

            heatMap.on("zoomend", updateHeatZoom);
            heatMap.on("baselayerchange", updateHeatBase);
            updateHeatZoom();
            updateHeatBase();

            // #region agent log
            function sendHeatLog(hypothesisId, message, data) {{
                fetch('http://127.0.0.1:7754/ingest/50f6dd20-af97-42cb-b10b-c0f385206d58', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-Debug-Session-Id': '629707'
                    }},
                    body: JSON.stringify({{
                        sessionId: '629707',
                        runId: 'post-fix',
                        hypothesisId: hypothesisId,
                        location: 'map_part_heat.html:paths',
                        message: message,
                        data: data,
                        timestamp: Date.now()
                    }})
                }}).catch(function () {{}});
            }}

            function samplePaths() {{
                var paths = document.querySelectorAll('.leaflet-overlay-pane svg path');
                var zoomClass = Array.from(document.body.classList).find(function (c) {{
                    return c.indexOf('heat-zoom-') === 0;
                }}) || null;
                var baseClass = Array.from(document.body.classList).find(function (c) {{
                    return c.indexOf('heat-base-') === 0;
                }}) || null;
                var samples = [];
                for (var i = 0; i < Math.min(paths.length, 8); i++) {{
                    var cs = getComputedStyle(paths[i]);
                    samples.push({{
                        index: i,
                        stroke: cs.stroke,
                        strokeOpacity: cs.strokeOpacity,
                        mixBlendMode: cs.mixBlendMode,
                        filter: cs.filter
                    }});
                }}
                sendHeatLog('D', 'path style sample', {{
                    pathCount: paths.length,
                    zoom: heatMap.getZoom(),
                    zoomClass: zoomClass,
                    baseClass: baseClass,
                    samples: samples
                }});
            }}

            heatMap.whenReady(function () {{
                setTimeout(samplePaths, 800);
                heatMap.on('baselayerchange', function () {{
                    setTimeout(samplePaths, 300);
                }});
                heatMap.on('zoomend', function () {{
                    setTimeout(samplePaths, 200);
                }});
            }});
            // #endregion
        }})();
    </script>
"""

    html = re.sub(
        r'<style id="ride-to-map-heat-style">[\s\S]*?</style>\s*',
        "",
        html,
        count=1,
    )
    html = re.sub(
        r'<script id="ride-to-map-heat-zoom">[\s\S]*?</script>\s*',
        "",
        html,
        count=1,
    )
    html = html.replace("</head>", f"{MULTIPLY_STYLE}\n</head>", 1)
    html = html.replace("</html>", f"{zoom_script}\n</html>", 1)
    map_path.write_text(html, encoding="utf-8")


def main():
    original_tile_layer = use_positron_as_default()
    try:
        base.main()
        inject_multiply_style()
    finally:
        base.folium.TileLayer = original_tile_layer

    print(
        "Heat-Version fertig: "
        f"{base.AUSGABE_DATEI} und {base.KARTE_DATEI}"
    )


if __name__ == "__main__":
    main()
