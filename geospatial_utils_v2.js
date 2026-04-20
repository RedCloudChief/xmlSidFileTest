/**
 * Geospatial Utilities for Italian Cadastral XML Analysis
 * Centralizes Proj4 configurations, coordinate conversion, and boundary layers.
 */

const WGS84 = 'EPSG:4326';

const GB_PARAMS = {
    peninsula: { // Roma 40 to WGS84 - Mainland
        towgs84: '-104.1,-49.1,-9.9,0.971,-2.917,0.714,-11.68',
        note: 'Accuratezza stimata: < 1 metro.'
    },
    sardinia: { // Roma 40 to WGS84 - Sardinia
        towgs84: '-168.6,-34.0,-211.6,-0.37,2.43,-2.04,13.7',
        note: 'Parametri specifici per la Sardegna. Errore atteso < 1m.'
    },
    sicily: { // Roma 40 to WGS84 - Sicily
        towgs84: '-50.2,-50.4,84.8,-0.69,-2.01,1.17,-12.9',
        note: 'Parametri specifici per la Sicilia. Errore atteso < 1m.'
    }
};

/**
 * Returns the Proj4 string for Gauss-Boaga projection
 */
function getProjString(isEast, area = 'peninsula') {
    const params = GB_PARAMS[area] || GB_PARAMS.peninsula;
    const x0 = isEast ? '2520000' : '1500000';
    const lon0 = isEast ? '15' : '9';
    return `+proj=tmerc +lat_0=0 +lon_0=${lon0} +k=0.9996 +x_0=${x0} +y_0=0 +ellps=intl +towgs84=${params.towgs84} +units=m +no_defs`;
}

/**
 * Converts Gauss-Boaga coordinates to WGS84
 */
function convertToWgs84(n, e, area = 'peninsula') {
    if (isNaN(n) || isNaN(e)) return null;
    const projection = getProjString(e > 2000000, area);
    try {
        const [lng, lat] = proj4(projection, WGS84, [e, n]);
        return { lat, lng };
    } catch (err) {
        console.error("Conversion error:", err);
        return null;
    }
}

/**
 * Boundaries Manager
 * Manages specialized GeoJSON layers.
 */
/**
 * Boundaries Manager
 * Manages specialized GeoJSON layers with spatial filtering for performance.
 */
class BoundariesManager {
    constructor(map) {
        this.map = map;
        this.pointsBounds = null;
        this.layers = {
            sid_dividente: {
                url: "./data/sid_dividente.geojson",
                data: new google.maps.Data(),
                isVisible: false,
                isLoaded: false,
                isLoading: false,
                style: {
                    strokeColor: "#dc2626",
                    strokeWeight: 2,
                    strokeOpacity: 0.9,
                    fillOpacity: 0,
                    clickable: true
                },
                label: "Dividente Demaniale (SID)"
            },
            sid_costa_new: {
                url: "./data/sid_costa_new.geojson",
                data: new google.maps.Data(),
                isVisible: false,
                isLoaded: false,
                isLoading: false,
                style: {
                    strokeColor: "#06b6d4",
                    strokeWeight: 1.5,
                    strokeOpacity: 0.8,
                    fillOpacity: 0,
                    clickable: true
                },
                label: "Dividente della Costa"
            },
            sid_concessioni: {
                url: "./data/sid_concessioni.geojson",
                data: new google.maps.Data(),
                isVisible: false,
                isLoaded: false,
                isLoading: false,
                style: {
                    strokeColor: "#8b5cf6",
                    strokeWeight: 1,
                    strokeOpacity: 0.9,
                    fillColor: "#8b5cf6",
                    fillOpacity: 0.2,
                    clickable: true
                },
                label: "Concessioni (SID)"
            },
            sid_amministrazioni: {
                url: "./data/sid_amministrazioni.geojson",
                data: new google.maps.Data(),
                isVisible: false,
                isLoaded: false,
                isLoading: false,
                style: {
                    strokeColor: "#f59e0b",
                    strokeWeight: 2,
                    strokeOpacity: 0.8,
                    fillOpacity: 0,
                    clickable: true
                },
                label: "Amministrazioni (SID)"
            }
        };


        this.infoWindow = new google.maps.InfoWindow();

        const propertyLabels = {
            id_conc: "ID Concessione",
            oggetto: "Oggetto",
            amministr0: "Amministrazione",
            tipo: "Tipo Dato",
            com_name: "Comune",
            prov_name: "Provincia",
            reg_name: "Regione",
            amministra: "Codice Ente",
            uso: "Destinazione d'Uso",
            categoria: "Categoria",
            data_scade: "Scadenza Concessione",
            tipo_atto: "Tipo Atto",
            numero_atto: "Numero Atto",
            anno_atto: "Anno Atto"
        };

        Object.keys(this.layers).forEach(type => {
            const layer = this.layers[type];
            layer.isLoaded = false;
            layer.isLoading = false;
            layer.data.setStyle(layer.style);

            layer.data.addListener('click', (event) => {
                let rows = '';
                event.feature.forEachProperty((value, name) => {
                    const label = propertyLabels[name] || name;
                    // Skip technical or redundant properties if needed
                    if (name === 'amministra' && event.feature.getProperty('amministr0')) return;
                    
                    rows += `
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 4px 8px; font-size: 0.75rem; color: #666; font-weight: 500;">${label}</td>
                            <td style="padding: 4px 8px; font-size: 0.75rem; color: #333;">${value}</td>
                        </tr>
                    `;
                });

                const content = `
                    <div style="font-family: Segoe UI, sans-serif; padding: 5px; min-width: 200px;">
                        <div style="margin-bottom: 8px; padding-bottom: 4px; border-bottom: 2px solid ${layer.style.strokeColor}; font-weight: bold; color: ${layer.style.strokeColor};">
                            ${layer.label}
                        </div>
                        <table style="width: 100%; border-collapse: collapse;">
                            ${rows}
                        </table>
                    </div>
                `;

                this.infoWindow.setContent(content);
                this.infoWindow.setPosition(event.latLng);
                this.infoWindow.open(this.map);
            });


            layer.data.addListener('mouseover', (event) => {
                layer.data.overrideStyle(event.feature, {
                    fillOpacity: 0.1,
                    strokeWeight: layer.style.strokeWeight + 1
                });
            });

            layer.data.addListener('mouseout', () => {
                layer.data.revertStyle();
            });
        });
    }

    setPointsBounds(bounds) {
        this.pointsBounds = bounds;
        console.log("Bounds updated for BoundariesManager filtering.");
    }

    async toggle(type) {
        const layer = this.layers[type];
        if (!layer) return;

        layer.isVisible = !layer.isVisible;
        if (layer.isVisible) {
            if (layer.isLoaded) {
                layer.data.setMap(this.map);
                this.maybeZoomToLayer(type);
            } else {
                await this.load(type);
                this.maybeZoomToLayer(type);
            }
        } else {
            layer.data.setMap(null);
            this.infoWindow.close();
        }
    }

    maybeZoomToLayer(type) {
        // If XML points are loaded, trust the existing bounds
        if (this.pointsBounds) return;

        const layer = this.layers[type];
        const bounds = new google.maps.LatLngBounds();
        let hasFeatures = false;

        layer.data.forEach(f => {
            const bbox = this.calculateFeatureBBox(f);
            if (bbox) {
                bounds.extend({ lat: bbox.minLat, lng: bbox.minLng });
                bounds.extend({ lat: bbox.maxLat, lng: bbox.maxLng });
                hasFeatures = true;
            }
        });

        if (hasFeatures) {
            console.log(`Auto-zooming to layer ${type}...`);
            this.map.fitBounds(bounds);
        }
    }


    async load(type) {
        const layer = this.layers[type];
        if (layer.isLoading || layer.isLoaded) return;

        try {
            layer.isLoading = true;
            console.log(`Loading ${type} from ${layer.url}...`);
            const response = await fetch(layer.url);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

            const geoJson = await response.json();
            
            // Clear any existing data
            layer.data.forEach(f => layer.data.remove(f));

            // Spatial filtering for large files
            if (this.pointsBounds && (type === 'sid_dividente' || type === 'sid_costa_new' || type === 'sid_concessioni' || type === 'sid_amministrazioni')) {
                console.log(`Filtering ${type} data for current area...`);

                const buffer = 0.5; // ~50km buffer
                const pSW = this.pointsBounds.getSouthWest();
                const pNE = this.pointsBounds.getNorthEast();

                const filteredFeatures = geoJson.features.filter(f => {
                    const bbox = this.calculateFeatureBBox(f);
                    if (!bbox) return true;

                    return (bbox.maxLat >= pSW.lat() - buffer &&
                        bbox.minLat <= pNE.lat() + buffer &&
                        bbox.maxLng >= pSW.lng() - buffer &&
                        bbox.minLng <= pNE.lng() + buffer);
                });

                if (filteredFeatures.length === 0 && geoJson.features.length > 0) {
                    console.warn(`No features of ${type} found in the current filtered area.`);
                    // Optionally alert or show a small toast?
                }

                const filtered = {
                    type: "FeatureCollection",
                    features: filteredFeatures
                };
                layer.data.addGeoJson(filtered);
                console.log(`Loaded ${filteredFeatures.length} features for ${type} after filtering.`);
            } else {
                layer.data.addGeoJson(geoJson);
                console.log(`Loaded ${geoJson.features.length} features for ${type} (unfiltered).`);
            }

            layer.isLoaded = true;
            if (layer.isVisible) layer.data.setMap(this.map);
        } catch (err) {
            console.error(`Load failed for ${type}:`, err);
            alert(`Errore nel caricamento di ${layer.label}: ${err.message}`);
            layer.isVisible = false;
        } finally {
            layer.isLoading = false;
        }
    }

    calculateFeatureBBox(f) {
        let minLat = 90, maxLat = -90, minLng = 180, maxLng = -180;
        let hasCoords = false;

        const processPoint = (p) => {
            if (!p || p.length < 2) return;
            const lng = p[0], lat = p[1];
            if (lat < minLat) minLat = lat;
            if (lat > maxLat) maxLat = lat;
            if (lng < minLng) minLng = lng;
            if (lng > maxLng) maxLng = lng;
            hasCoords = true;
        };

        const processGoogleLatLng = (latLng) => {
            const lat = latLng.lat(), lng = latLng.lng();
            if (lat < minLat) minLat = lat;
            if (lat > maxLat) maxLat = lat;
            if (lng < minLng) minLng = lng;
            if (lng > maxLng) maxLng = lng;
            hasCoords = true;
        };

        // Handle Google Maps Data.Feature
        if (f.getGeometry && typeof f.getGeometry === 'function') {
            const geom = f.getGeometry();
            if (!geom) return null;
            
            geom.forEachLatLng(processGoogleLatLng);
        } 
        // Handle raw GeoJSON Feature
        else if (f.geometry && f.geometry.coordinates) {
            const geom = f.geometry;
            const type = geom.type;
            const coords = geom.coordinates;

            if (type === "Point") {
                processPoint(coords);
            } else if (type === "LineString" || type === "Polygon") {
                const ring = type === "Polygon" ? coords[0] : coords;
                ring.forEach(processPoint);
            } else if (type === "MultiLineString" || type === "MultiPolygon") {
                coords.forEach(comp => {
                    const ring = type === "MultiPolygon" ? comp[0] : comp;
                    ring.forEach(processPoint);
                });
            }
        } else {
            return null;
        }

        return hasCoords ? { minLat, maxLat, minLng, maxLng } : null;
    }
}



