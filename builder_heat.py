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
        .leaflet-overlay-pane svg path {
            mix-blend-mode: multiply;
        }
        body.heat-zoom-far .leaflet-overlay-pane svg path {
            stroke-width: 1.2px !important;
            stroke-opacity: 0.08 !important;
            filter: blur(0.65px);
        }
        body.heat-zoom-medium .leaflet-overlay-pane svg path {
            stroke-width: 1.6px !important;
            stroke-opacity: 0.15 !important;
            filter: blur(0.3px);
        }
        body.heat-zoom-near .leaflet-overlay-pane svg path {
            stroke-width: 2px !important;
            stroke-opacity: 0.25 !important;
            filter: none;
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

    if 'id="ride-to-map-heat-style"' in html:
        return
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

            function updateHeatZoom() {{
                const zoom = heatMap.getZoom();
                document.body.classList.remove(...zoomClasses);
                document.body.classList.add(
                    zoom <= 7
                        ? "heat-zoom-far"
                        : zoom <= 10
                            ? "heat-zoom-medium"
                            : "heat-zoom-near"
                );
            }}

            heatMap.on("zoomend", updateHeatZoom);
            updateHeatZoom();
        }})();
    </script>
"""

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
