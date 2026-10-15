import type { MapAdapter, MapLine, MapPoint, MyLocation, StayPoint } from "../model";
import { linePieces, lineStyle, linesKey, walkStyle } from "./lines";
import { ACCURACY_STYLE, accuracyRadius, createMeDot, createStayDot, ME_BOX, meKey, STAY_BOX, staysKey } from "./me";
import { createPin, createPinLeader, geometryKey, layoutPins, paintPinGroups, pinArea, PIN_BOX, pinPriority, placePin, pointLabel, popPin, pulsePin, setPinSelected, type PinSlot } from "./pin";
import { setMarkerHidden } from "./visibility";

/**
 * Free map: OpenStreetMap standard tiles drawn with Leaflet.
 *
 * ★OSM tile policy (operations.osmfoundation.org/policies/tiles, read 2026-09-29):
 *   - visible attribution「© OpenStreetMap contributors」 — Leaflet's attribution control, never hidden;
 *   - https only; the browser's normal Referer/User-Agent go through (we set no Referrer-Policy);
 *   - only the tiles in view are fetched — no prefetching, no "download for offline", no cache-busting headers;
 *   - no SLA: the service may block or slow us. That is why this is the development default, not a promise to customers.
 */
export const OSM_ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
  + ' · <a href="https://www.openstreetmap.org/fixthemap" target="_blank" rel="noopener">지도 오류 신고</a>';

const SEOUL = { lat: 37.5665, lng: 126.978 };

export function createOsmAdapter(tileUrl: string): MapAdapter {
  return {
    async create(container, options) {
      const L = (await import("leaflet")).default;
      // ★`[2026-10-05 사용자 선택]` The map's buttons (+ − the scale ruler, all stops, my place) are drawn by the page over every provider the same way (`map-controls.tsx`): no Leaflet control here.
      const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
      const map = L.map(container, { center: options.points[0]?.coordinates ?? SEOUL, zoom: 14, minZoom: 3, maxZoom: 18, zoomControl: false,
        zoomAnimation: !reducedMotion(), markerZoomAnimation: !reducedMotion(), fadeAnimation: !reducedMotion() });
      const layer = L.tileLayer(tileUrl, { maxZoom: 19, attribution: OSM_ATTRIBUTION, crossOrigin: false });
      // ★One missing tile is not a broken map (the pins still show). Only when nothing has loaded after a few failures
      //   does the screen say the map could not be shown — never a blank grey box that looks like a map.
      let loaded = false;
      let failures = 0;
      layer.on("tileload", () => { loaded = true; });
      layer.on("tileerror", () => {
        failures += 1;
        if (!loaded && failures === 3) options.onError(new Error("무료 지도 타일을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요."));
      });
      layer.addTo(map);

      let destroyed = false;
      let points: MapPoint[] = [];
      let previousData = "";
      let previousGeometry = "";
      let selectedId: string | undefined;
      let needsFit = false;
      // ★`[2026-10-06 사용자 지적 — 지도가 딱딱 끊어지게 이동한다]` After the first fit the camera FLIES (a smooth, eased flight that starts from wherever the map is, and starts over smoothly if another pin arrives meanwhile)
      //   instead of cutting to the new view. The first fit and a screen that asks for less motion still cut.
      let fitted = false;
      // Geographic travel is longer than a UI fade, but settles before the 800ms card hold ends.
      const FLIGHT = { duration: 0.65, easeLinearity: 0.35 } as const;
      let centredOnMe = false;           // [2026-10-05] an empty map is centred on the customer once; pins (a different set of them) reset it
      // ★The box can change size after the first fit (the sheet over the map goes up and down, a tab opens). Until the customer
      //   moves or zooms the map themselves, a new size fits the pins again; after that the map stays where they put it.
      let userMoved = false;
      let programmatic = false;
      map.on("movestart zoomstart", () => options.onInteractionChange?.(true));
      map.on("moveend zoomend", () => options.onInteractionChange?.(false));
      map.on("dragstart", () => { userMoved = true; });
      map.on("zoomstart", () => { if (!programmatic) userMoved = true; });
      let markers: { id: string; paint: string; marker: ReturnType<typeof L.marker>; pin: HTMLElement; leaderMarker: ReturnType<typeof L.marker>; leaderBox: HTMLElement }[] = [];
      let hiddenPoints = new Set<string>();
      function setHiddenPoints(ids: readonly string[]) {
        // A React chip update may refer to the viewport just before an instant SDK move.
        // Never let that older chip hide a coordinate already inside the current viewport.
        const bounds = map.getBounds();
        hiddenPoints = new Set(ids.filter(id => {
          const point = points.find(point => point.id === id);
          return point && !bounds.contains(point.coordinates);
        }));
        markers.forEach(({ id, marker, leaderMarker }) => {
          setMarkerHidden(marker.getElement(), hiddenPoints.has(id));
          setMarkerHidden(leaderMarker.getElement(), hiddenPoints.has(id));
        });
      }
      // Route lines sit in their own pane under the pins, so a pin is always pressable and a line never covers one.
      map.createPane("routes").style.zIndex = "350";
      // ★`[2026-10-06 사용자 지적]` The thin lines back to a pushed-aside pin's coordinate (and their dots) stand in a layer of their own under ALL the pins (the marker pane is 600), above the route lines.
      map.createPane("pinLeaders").style.zIndex = "580";
      const routes = L.layerGroup().addTo(map);
      let previousLines = "";
      // `[2026-10-06 사용자 지시 — 경로를 누르면 그 경로가 나온다]` Each route is two lines: the one drawn, and a wider invisible one over it that takes the press (a thin road is hard to hit, and a phone has no hover).
      let lineLayers: { id: string; shown: ReturnType<typeof L.polyline>; dashed: boolean; mode: MapLine["mode"]; walk: boolean }[] = [];
      let pickedLine: string | undefined;
      function styleLines() {
        lineLayers.forEach(({ id, shown, dashed, mode, walk }) => {
          const picked = id === pickedLine;
          const base = walk ? walkStyle(container) : lineStyle(container, { id, points: [], dashed, mode, title: "" });
          const accent = getComputedStyle(container).getPropertyValue("--color-selected").trim();
          shown.setStyle(picked ? { weight: base.weight + 3, opacity: 1, ...(accent ? { color: accent } : {}) } : { weight: base.weight, opacity: base.opacity, color: base.color });
          if (picked) shown.bringToFront();
        });
      }

      function clearMarkers() {
        markers.forEach(({ marker, leaderMarker }) => { marker.remove(); leaderMarker.remove(); });
        markers = [];
      }

      // ★`[2026-10-04]` Each pin stands on the side of its coordinate where it overlaps the fewest others (`layoutPins`); worked out again when the map moves or zooms.
      let slots: Record<string, PinSlot> = {};
      function relayout() {
        if (destroyed || !markers.length || !container.clientWidth || !container.clientHeight) return;
        slots = layoutPins(points.filter((point) => !hiddenPoints.has(point.id)).sort((a, b) => pinPriority(a, b, selectedId)).map((point) => {
          const at = map.latLngToContainerPoint([point.coordinates.lat, point.coordinates.lng]);
          return { id: point.id, x: at.x, y: at.y };
        }), pinArea(container, options), slots);
        markers.forEach(({ id, pin, leaderBox }) => { if (slots[id]) placePin(pin, slots[id], leaderBox); });
        paintPinGroups(markers, slots);
      }
      map.on("moveend zoomend resize", relayout);
      // [2026-10-05] The zoom level goes to the screen (it asks for the detailed route lines when zoomed in).
      const sayZoom = () => options.onZoom?.(map.getZoom());
      map.on("zoomend", sayZoom);
      sayZoom();
      // [2026-10-05] What the map shows goes to the screen, for the scale ruler and the chips of the stops out of view.
      const sayView = () => {
        if (destroyed || !container.clientWidth || !container.clientHeight) return;
        setHiddenPoints([...hiddenPoints]);
        const bounds = map.getBounds();
        options.onView?.({ south: bounds.getSouth(), west: bounds.getWest(), north: bounds.getNorth(), east: bounds.getEast(), width: container.clientWidth, height: container.clientHeight, zoom: map.getZoom() });
      };
      // Publish the same move that positions the markers; a second animation frame leaves chips on the previous viewport.
      map.on("move zoom moveend zoomend resize", sayView);

      // ★`[2026-10-05 사용자 지시]` 「내 위치」: a dot under the pins (a marker that takes no press) and the accuracy circle in the vector layer.
      let me: MyLocation | null = null;
      let meMarker: ReturnType<typeof L.marker> | null = null;
      let meCircle: ReturnType<typeof L.circle> | null = null;
      let previousMe = "";
      const stayLayer = L.layerGroup().addTo(map);
      let previousStays = "";

      function fit() {
        if (!needsFit || !container.clientWidth || !container.clientHeight) return;
        // ★`[2026-10-05]` Leaflet drops a new view while a zoom animation runs (`_animatingZoom`), and a map's first fit is such an animation (zoom 14 → the pins' zoom):
        //   a day opened in the quarter second after it kept the old view. A fit that comes then waits for the animation to end (found with an empty day that never
        //   centred on 「내 위치」 - its `setView` had been dropped).
        if ((map as unknown as { _animatingZoom?: boolean })._animatingZoom) { map.once("zoomend", () => { if (!destroyed) fit(); }); return; }
        // `[2026-10-05]` No pins:the customer's own position is the picture (once — the map does not chase them); nothing at all: wait.
        if (!points.length) {
          if (!me) return;
          needsFit = false;
          centredOnMe = true;
          programmatic = true;
          try { map.setView(me.coordinates, 15, reducedMotion() ? { animate: false } : undefined); } finally { programmatic = false; }
          return;
        }
        needsFit = false;
        programmatic = true;
        try {
          const fly = fitted && !reducedMotion();
          const focus = points.find(point => point.revealFocus);
          if (focus || points.length === 1) { const at = (focus ?? points[0]).coordinates; if (fly) map.flyTo(at, 15, FLIGHT); else map.setView(at, 15, { animate: false }); }
          else {
            const bounds = L.latLngBounds(points.map(({ coordinates }) => [coordinates.lat, coordinates.lng] as [number, number]));
            const padding = { paddingTopLeft: [50, 50 + (options.topInset ?? 0)] as [number, number], paddingBottomRight: [50, 50 + (options.bottomInset ?? 0)] as [number, number] };
            if (fly) map.flyToBounds(bounds, { ...padding, ...FLIGHT }); else map.fitBounds(bounds, { ...padding, animate: false });
          }
          fitted = true;
        } finally { programmatic = false; }
      }

      // ★`[2026-10-04 사용자 지시]` 「모든 일정 보기」: every pin back in view, at the place and zoom the map first showed them.
      //   ★Leaflet silently drops a new view while a zoom animation is running (`_animatingZoom`; `fitBounds` answers "done" without moving), so a press that comes a quarter
      //   of a second after +/- would do nothing - it waits for the zoom to end and then fits.
      function fitAll() {
        userMoved = false; needsFit = true;
        fit();                                                       // (it waits by itself while a zoom animation runs)
      }
      // `[2026-10-05 사용자 선택 — 첫 지도 시점 안 C]` A day is shown whole and its first stop is pointed out once, when the camera has come to rest.
      let pulseTimer: ReturnType<typeof setTimeout> | undefined;
      function pulseFirst() {
        clearTimeout(pulseTimer);
        pulseTimer = setTimeout(() => {
          if (destroyed) return;
          const first = points.find((point) => point.order === 1 && !point.tone);
          const marker = first && markers.find((entry) => entry.id === first.id);
          if (marker) pulsePin(marker.pin);
        }, 900);
      }

      function drawLines(nextLines: MapLine[]) {
        const key = linesKey(nextLines);
        if (key === previousLines) return;
        previousLines = key;
        routes.clearLayers();
        lineLayers = [];
        nextLines.forEach((line) => {
          const style = lineStyle(container, line);
          const path = line.points.map(({ lat, lng }) => [lat, lng] as [number, number]);
          // `className` hooks the screen own CSS and the tests; the options are what Leaflet draws.
          // `[2026-10-15 ST4F-495]` A walked stretch is its own piece: round dots (a near-zero dash with a round cap is a dot) in the walking colour.
          linePieces(line).forEach(({ points, walk }) => {
            const dots = walk ? walkStyle(container) : null;
            const shown = L.polyline(points.map(({ lat, lng }) => [lat, lng] as [number, number]), {
              pane: "routes", color: dots?.color ?? style.color, weight: dots?.weight ?? style.weight, opacity: dots?.opacity ?? style.opacity, lineCap: "round", lineJoin: "round",
              ...(dots ? { dashArray: `0.1 ${dots.dots}` } : style.dash ? { dashArray: style.dash.join(" ") } : {}), interactive: false,
              className: `trip-route-line${line.dashed ? " trip-route-line--dashed" : ""}${walk ? " trip-route-line--walk" : ""}`,
            }).addTo(routes);
            lineLayers.push({ id: line.id, shown, dashed: line.dashed, mode: line.mode, walk });
            if (!options.onSelectLine) shown.bindTooltip(line.title, { sticky: true });
          });
          if (!options.onSelectLine) return;
          L.polyline(path, { pane: "routes", color: "#000", weight: 22, opacity: 0, lineCap: "round", lineJoin: "round", interactive: true, className: "trip-route-hit" })
            .bindTooltip(line.title, { sticky: true }).on("click", () => options.onSelectLine?.(line.id)).addTo(routes);
        });
        styleLines();
      }

      function update(nextPoints: MapPoint[], nextSelectedId?: string, nextLines: MapLine[] = [], nextLineId?: string) {
        if (destroyed) return;
        drawLines(nextLines);
        if (nextLineId !== pickedLine) { pickedLine = nextLineId; styleLines(); }
        const data = JSON.stringify(nextPoints);
        const geometry = geometryKey(nextPoints);
        const changed = geometry !== previousGeometry;
        points = nextPoints;
        if (data !== previousData) {
          // A stop the map has not shown before pops in (not the first set of pins, and not the ones that were already there).
          const old = new Map(markers.map(entry => [entry.id, entry]));
          const popIn = previousData !== "";
          markers = [];
          points.forEach((point) => {
            const paint = JSON.stringify({ ...point, revealFocus: undefined });
            const prior = old.get(point.id);
            old.delete(point.id);
            if (prior?.paint === paint) { markers.push(prior); return; }
            prior?.marker.remove(); prior?.leaderMarker.remove();
            const pin = createPin(point, true);
            const leaderBox = createPinLeader(pin);
            const leaderIcon = L.divIcon({ html: leaderBox, className: "", iconSize: [PIN_BOX, PIN_BOX], iconAnchor: [PIN_BOX / 2, PIN_BOX / 2] });
            const leaderMarker = L.marker(point.coordinates, { icon: leaderIcon, pane: "pinLeaders", interactive: false, keyboard: false }).addTo(map);
            const icon = L.divIcon({ html: pin, className: "", iconSize: [PIN_BOX, PIN_BOX], iconAnchor: [PIN_BOX / 2, PIN_BOX / 2] });
            const marker = L.marker(point.coordinates, { icon, title: pointLabel(point), alt: pointLabel(point), keyboard: true, riseOnHover: true });
            marker.on("click", () => options.onSelect(point.id));
            marker.addTo(map);
            // The marker's box is wider than the pin: only the pin takes presses (the rest of the box leaves the map draggable).
            const element = marker.getElement();
            if (element) element.style.pointerEvents = "none";
            markers.push({ id: point.id, paint, marker, pin, leaderMarker, leaderBox });
            if (popIn && !prior) popPin(pin);
          });
          old.forEach(({ marker, leaderMarker }) => { marker.remove(); leaderMarker.remove(); });
          previousData = data;
          setHiddenPoints([...hiddenPoints]);
          relayout();
        }
        const revealId = points.find(point => point.revealFocus)?.id;   // `[2026-10-13]` 검증 로딩 중 지금 드러나는 마커
        markers.forEach(({ id, pin, marker }) => {
          const selected = id === nextSelectedId;
          setPinSelected(pin, selected, id === revealId);
          marker.getElement()?.setAttribute("aria-pressed", String(selected));
          marker.setZIndexOffset(selected || id === revealId ? 1000 : 0);
        });
        if (changed) {
          previousGeometry = geometry;
          needsFit = true;
          centredOnMe = false;          // [2026-10-05] a different set of pins: an empty day after it centres on the customer again
          userMoved = false;            // different pins: the old view means nothing
          fit();
          relayout();
          if (!points.some(point => point.revealFocus)) pulseFirst();
        } else if (nextSelectedId !== selectedId) {
          const selected = points.find(({ id }) => id === nextSelectedId);
          if (selected) map.panTo(selected.coordinates, reducedMotion() ? { animate: false } : undefined);
        }
        selectedId = nextSelectedId;
        relayout();
      }

      function setMe(next: MyLocation | null) {
        if (destroyed) return;
        me = next;
        const key = meKey(next);
        if (key === previousMe) return;
        previousMe = key;
        if (!next) {
          meMarker?.remove(); meCircle?.remove();
          meMarker = meCircle = null;
          return;
        }
        const at: [number, number] = [next.coordinates.lat, next.coordinates.lng];
        if (meMarker) meMarker.setLatLng(at);
        else {
          const icon = L.divIcon({ html: createMeDot(), className: "", iconSize: [ME_BOX, ME_BOX], iconAnchor: [ME_BOX / 2, ME_BOX / 2] });
          // Not interactive and far under the pins (`zIndexOffset`): a pin on top of it is pressed, never the dot.
          meMarker = L.marker(at, { icon, interactive: false, keyboard: false, zIndexOffset: -1000 }).addTo(map);
          const element = meMarker.getElement();
          if (element) element.style.pointerEvents = "none";
        }
        const radius = accuracyRadius(next);
        if (radius === null) { meCircle?.remove(); meCircle = null; }
        else if (meCircle) { meCircle.setLatLng(at); meCircle.setRadius(radius); }
        else {
          meCircle = L.circle(at, { radius, interactive: false, color: ACCURACY_STYLE.color, weight: ACCURACY_STYLE.weight, opacity: ACCURACY_STYLE.opacity,
            fillColor: ACCURACY_STYLE.color, fillOpacity: ACCURACY_STYLE.fillOpacity, className: "my-location-accuracy" }).addTo(map);
        }
        if (!points.length && !centredOnMe) needsFit = true;          // [2026-10-05] a map that never had pins was never fitted: the first position it learns centres it, once
        if (!points.length && needsFit) fit();
      }

      function setStays(stays: StayPoint[]) {
        if (destroyed) return;
        const key = staysKey(stays);
        if (key === previousStays) return;
        previousStays = key;
        stayLayer.clearLayers();
        stays.forEach((stay) => {
          const icon = L.divIcon({ html: createStayDot(stay), className: "", iconSize: [STAY_BOX, STAY_BOX], iconAnchor: [STAY_BOX / 2, STAY_BOX / 2] });
          const marker = L.marker(stay.coordinates, { icon, interactive: false, keyboard: false, zIndexOffset: -2000 }).addTo(stayLayer);
          const element = marker.getElement();
          if (element) element.style.pointerEvents = "none";
        });
      }

      // `[2026-10-05]` 「내 위치」 button: the camera to the customer, at least street level, and from then on the customer's own camera.
      function centerOn(at: { lat: number; lng: number }, keepZoom = false) {
        if (destroyed) return;
        // Leaflet drops a new view while a zoom animation runs: wait for it to end.
        if ((map as unknown as { _animatingZoom?: boolean })._animatingZoom) { map.once("zoomend", () => centerOn(at, keepZoom)); return; }
        userMoved = true;
        const motion = reducedMotion() ? { animate: false } : undefined;
        if (keepZoom) map.panTo([at.lat, at.lng], motion);
        else map.setView([at.lat, at.lng], Math.max(map.getZoom(), 15), motion);
      }

      try { update(options.points, options.selectedId, options.lines); setMe(options.me ?? null); setStays(options.stays ?? []); }
      catch (error) { clearMarkers(); map.remove(); throw error; }
      sayView();

      return {
        update,
        setHiddenPoints,
        relayout,
        setMe,
        setStays,
        fit: fitAll,
        zoomBy(delta) {
          if (destroyed) return;
          const zoom = Math.min(map.getMaxZoom(), Math.max(map.getMinZoom(), Math.round(map.getZoom()) + delta));
          if (zoom === map.getZoom()) return;
          // CSS zoom transforms markers before Leaflet updates its bounds. flyTo publishes each visual move instead.
          if (reducedMotion()) map.setZoom(zoom, { animate: false });
          else map.flyTo(map.getCenter(), zoom, FLIGHT);
        },
        centerOn,
        resize() {
          if (destroyed || !container.clientWidth || !container.clientHeight) return;
          map.invalidateSize();
          if (!userMoved) needsFit = true;
          fit();
        },
        destroy() {
          if (destroyed) return;
          destroyed = true;
          clearTimeout(pulseTimer);
          clearMarkers();
          map.remove();
          container.replaceChildren();
        },
      };
    },
  };
}
