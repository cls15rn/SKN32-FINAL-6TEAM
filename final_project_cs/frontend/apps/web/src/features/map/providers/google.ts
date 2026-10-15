import type { Coordinates, MapAdapter, MapLine, MapPoint, MyLocation, StayPoint } from "../model";
import { linePieces, lineStyle, linesKey, walkStyle } from "./lines";
import { ACCURACY_STYLE, accuracyRadius, createMeDot, createStayDot, ME_LABEL, meKey, staysKey } from "./me";
import { createPin, geometryKey, layoutPins, paintPinGroups, pinArea, pinPriority, placePin, pointLabel, pulsePin, setPinSelected, type PinSlot } from "./pin";
import { createSdkLoader } from "./sdk-loader";
import { setMarkerHidden } from "./visibility";

interface GoogleBounds { extend(position: Coordinates): void }
interface GoogleLatLng { lat(): number; lng(): number }
/** The world-coordinate projection of the map (Mercator, 256 px at zoom 0): enough to work out where a coordinate stands on screen. */
interface GoogleProjection { fromLatLngToPoint(position: Coordinates | GoogleLatLng): { x: number; y: number } | null }
interface GoogleMap {
  panTo(position: Coordinates): void;
  setCenter(position: Coordinates | GoogleLatLng): void;
  getCenter(): GoogleLatLng | undefined;
  setZoom(zoom: number): void;
  /** Optional: a build of the SDK without them still shows the pins (they then keep their first side). */
  getZoom?(): number | undefined;
  getProjection?(): GoogleProjection | undefined;
  /** Optional: without it the page draws no scale ruler and no chips for stops out of view. */
  getBounds?(): { toJSON(): { south: number; west: number; north: number; east: number } } | undefined;
  fitBounds(bounds: GoogleBounds, padding: number | { top: number; right: number; bottom: number; left: number }): void;
  unbindAll(): void;
}
interface GoogleMarker extends HTMLElement { map: GoogleMap | null; zIndex: number; position?: Coordinates | null }
interface GooglePolyline { setMap(map: GoogleMap | null): void }
/** `[2026-10-05]` The accuracy circle of 「내 위치」 (`google.maps.Circle`, radius in metres). */
interface GoogleCircle { setMap(map: GoogleMap | null): void; setCenter(center: Coordinates): void; setRadius(radius: number): void }
interface GoogleCircleOptions {
  map: GoogleMap; center: Coordinates; radius: number; strokeColor: string; strokeOpacity: number; strokeWeight: number;
  fillColor: string; fillOpacity: number; clickable: boolean; zIndex: number;
}
interface GooglePolylineOptions {
  map: GoogleMap; path: Coordinates[]; strokeColor: string; strokeOpacity: number; strokeWeight: number; clickable: boolean;
  icons?: { icon: { path: string | number; strokeOpacity: number; scale: number; fillColor?: string; fillOpacity?: number }; offset: string; repeat: string }[];
}
interface GoogleSdk {
  Map: new (container: HTMLElement, options: {
    center: Coordinates; zoom: number; minZoom: number; maxZoom: number; mapId: string;
    mapTypeControl: boolean; streetViewControl: boolean; fullscreenControl: boolean; zoomControl: boolean; gestureHandling: string;
  }) => GoogleMap;
  LatLngBounds: new () => GoogleBounds;
  /** Optional: a build of the SDK without lines still shows the pins. */
  Polyline?: new (options: GooglePolylineOptions) => GooglePolyline;
  /** Optional: without it 「내 위치」 is the dot alone. */
  Circle?: new (options: GoogleCircleOptions) => GoogleCircle;
  marker: {
    AdvancedMarkerElement: new (options: { map: GoogleMap; position: Coordinates; title: string; gmpClickable: boolean }) => GoogleMarker;
  };
  event: { trigger(target: GoogleMap, event: "resize"): void; clearInstanceListeners(target: GoogleMap): void; addListener?(target: GoogleMap, event: "idle" | "bounds_changed", callback: () => void): unknown };
}

const loader = createSdkLoader<GoogleSdk>({
  provider: "google", label: "Google 지도", authCallback: "gm_authFailure",
  read: () => {
    const sdk = (window as Window & { google?: { maps?: GoogleSdk } }).google?.maps;
    return sdk?.Map && sdk.LatLngBounds && sdk.marker?.AdvancedMarkerElement && sdk.event ? sdk : undefined;
  },
  url: (key, callback) => `https://maps.googleapis.com/maps/api/js?${new URLSearchParams({
    key, callback, loading: "async", libraries: "marker", v: "weekly", language: "ko", region: "KR",
  })}`,
});

export function createGoogleAdapter(apiKey: string, mapId: string): MapAdapter {
  return {
    async create(container, options) {
      if (!mapId.trim()) throw new Error("Google 지도 Map ID가 설정되지 않았습니다.");
      const sdk = await loader.load(apiKey);
      const map = new sdk.Map(container, {
        center: options.points[0]?.coordinates ?? { lat: 37.5665, lng: 126.978 },
        zoom: 14, minZoom: 3, maxZoom: 18, mapId,
        mapTypeControl: false, streetViewControl: false, fullscreenControl: false, zoomControl: false, gestureHandling: "cooperative",
      });
      let destroyed = false;
      let points: MapPoint[] = [];
      let previousData = "";
      let previousGeometry = "";
      let selectedId: string | undefined;
      let needsFit = false;
      let centredOnMe = false;           // [2026-10-05] an empty map is centred on the customer once; pins (a different set of them) reset it
      let markers: { id: string; marker: GoogleMarker; pin: HTMLElement; onClick: () => void }[] = [];
      let routes: GooglePolyline[] = [];
      let previousLines = "";
      const unwatch = loader.watchAuthFailure(options.onError);

      // ★`[2026-10-04]` Each pin stands on the side of its coordinate where it overlaps the fewest others (`layoutPins`); worked out again when the map comes to rest.
      let slots: Record<string, PinSlot> = {};
      let hiddenPoints = new Set<string>();
      function setHiddenPoints(ids: readonly string[]) {
        hiddenPoints = new Set(ids);
        markers.forEach(({ id, marker }) => setMarkerHidden(marker, hiddenPoints.has(id)));
      }
      function relayout() {
        const projection = map.getProjection?.(), center = map.getCenter(), zoom = map.getZoom?.();
        const middle = projection && center ? projection.fromLatLngToPoint(center) : null;
        if (destroyed || !projection || !middle || typeof zoom !== "number" || !markers.length || !container.clientWidth || !container.clientHeight) return;
        const scale = 2 ** zoom;
        const placed = points.filter((point) => !hiddenPoints.has(point.id)).sort((a, b) => pinPriority(a, b, selectedId)).flatMap((point) => {
          const world = projection.fromLatLngToPoint(point.coordinates);
          return world ? [{ id: point.id, x: (world.x - middle.x) * scale + container.clientWidth / 2, y: (world.y - middle.y) * scale + container.clientHeight / 2 }] : [];
        });
        slots = layoutPins(placed, pinArea(container, options), slots);
        markers.forEach(({ id, pin }) => { if (slots[id]) placePin(pin, slots[id]); });
        paintPinGroups(markers, slots);
      }
      sdk.event.addListener?.(map, "bounds_changed", () => options.onInteractionChange?.(true));
      sdk.event.addListener?.(map, "idle", () => { options.onInteractionChange?.(false); relayout(); });
      // [2026-10-05] The zoom level goes to the screen (it asks for the detailed route lines when zoomed in).
      sdk.event.addListener?.(map, "idle", () => { const zoom = map.getZoom?.(); if (typeof zoom === "number") options.onZoom?.(zoom); });
      // [2026-10-05] What the map shows goes to the screen (the scale ruler, the chips for stops out of view) - nothing when this build of the SDK cannot say it.
      const sayView = () => {
        const bounds = map.getBounds?.()?.toJSON(), zoom = map.getZoom?.();
        if (destroyed || !bounds || typeof zoom !== "number" || !container.clientWidth || !container.clientHeight) return;
        options.onView?.({ ...bounds, width: container.clientWidth, height: container.clientHeight, zoom });
      };
      sdk.event.addListener?.(map, "idle", sayView);
      sdk.event.addListener?.(map, "bounds_changed", sayView);

      function clearMarkers() {
        markers.forEach(({ marker, onClick }) => {
          marker.removeEventListener("gmp-click", onClick);
          marker.map = null;
          marker.remove();
        });
        markers = [];
      }

      // ★`[2026-10-05 사용자 지시]` 「내 위치」: a marker that takes no press, under the pins (`zIndex` 0; pins are 1 and 100; stays −1), and its accuracy circle.
      let me: MyLocation | null = null;
      let meMarker: GoogleMarker | null = null;
      let meCircle: GoogleCircle | null = null;
      let previousMe = "";
      let stayMarkers: GoogleMarker[] = [];
      let previousStays = "";

      function quietMarker(position: Coordinates, title: string, content: HTMLElement, zIndex: number): GoogleMarker {
        const marker = new sdk.marker.AdvancedMarkerElement({ map, position, title, gmpClickable: false });
        content.style.transform = "translateY(50%)";        // the marker stands the middle of its bottom edge on the coordinate: centre the dot on it instead
        marker.append(content);
        marker.zIndex = zIndex;
        marker.style.pointerEvents = "none";
        return marker;
      }

      function removeMe() {
        if (meMarker) { meMarker.map = null; meMarker.remove(); }
        meCircle?.setMap(null);
        meMarker = null;
        meCircle = null;
      }

      function clearStays() {
        stayMarkers.forEach((marker) => { marker.map = null; marker.remove(); });
        stayMarkers = [];
      }

      function fit() {
        if (!needsFit || !container.clientWidth || !container.clientHeight) return;
        // `[2026-10-05]` No pins: the customer's own position is the picture (once — the map does not chase them); nothing at all: wait.
        if (!points.length) {
          if (!me) return;
          needsFit = false;
          centredOnMe = true;
          map.setCenter(me.coordinates);
          map.setZoom(15);
          return;
        }
        needsFit = false;
        const focus = points.find(point => point.revealFocus);
        if (focus || points.length === 1) {
          const at = (focus ?? points[0]).coordinates;
          if (focus && previousGeometry && !matchMedia("(prefers-reduced-motion: reduce)").matches) map.panTo(at);
          else map.setCenter(at);
          map.setZoom(15);
        } else {
          const bounds = new sdk.LatLngBounds();
          points.forEach(({ coordinates }) => bounds.extend(coordinates));
          map.fitBounds(bounds, { top: 50 + (options.topInset ?? 0), right: 50, bottom: 50 + (options.bottomInset ?? 0), left: 50 });
        }
      }

      function clearLines() {
        routes.forEach((route) => route.setMap(null));
        routes = [];
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
        clearLines();
        const Polyline = sdk.Polyline;
        if (!Polyline) return;
        routes = nextLines.flatMap((line) => linePieces(line).map(({ points, walk }) => {
          const style = lineStyle(container, line);
          const dots = walk ? walkStyle(container) : null;
          // A dashed line is drawn as repeated dots of an icon (the way the SDK does it): the line itself is invisible.
          // `[2026-10-15 ST4F-495]` A walked stretch is a row of round dots — the SDK's circle symbol (`google.maps.SymbolPath.CIRCLE` is 0) — in the walking colour.
          const route = new Polyline({
            map, path: points, strokeColor: dots?.color ?? style.color, strokeWeight: dots?.weight ?? style.weight, clickable: Boolean(options.onSelectLine),
            strokeOpacity: dots || style.dash ? 0 : style.opacity,
            ...(dots ? { icons: [{ icon: { path: 0, scale: dots.weight / 2, fillColor: dots.color, fillOpacity: dots.opacity, strokeOpacity: 0 }, offset: "0", repeat: `${dots.dots}px` }] }
              : style.dash ? { icons: [{ icon: { path: "M 0,-1 0,1", strokeOpacity: style.opacity, scale: 3 }, offset: "0", repeat: `${style.dash[0] + style.dash[1]}px` }] } : {}),
          });
          // `[2026-10-06 사용자 지시]` A press on a route line picks it (the SDK's own listener; a test double without one simply does not take the press).
          (route as unknown as { addListener?: (name: string, run: () => void) => void }).addListener?.("click", () => options.onSelectLine?.(line.id));
          return route;
        }));
      }

      function update(nextPoints: MapPoint[], nextSelectedId?: string, nextLines: MapLine[] = []) {
        if (destroyed) return;
        drawLines(nextLines);
        const data = JSON.stringify(nextPoints);
        const geometry = geometryKey(nextPoints);
        const changed = geometry !== previousGeometry;
        points = nextPoints;
        if (data !== previousData) {
          clearMarkers();
          points.forEach((point) => {
            const pin = createPin(point);
            pin.style.transform = "translateY(50%)";          // the marker puts the middle of its bottom edge on the coordinate: the pin's box is centred on it instead
            const marker = new sdk.marker.AdvancedMarkerElement({
              map, position: point.coordinates, title: pointLabel(point), gmpClickable: true,
            });
            marker.append(pin);
            marker.setAttribute("aria-label", pointLabel(point));
            const onClick = () => options.onSelect(point.id);
            marker.addEventListener("gmp-click", onClick);
            markers.push({ id: point.id, marker, pin, onClick });
          });
          previousData = data;
          setHiddenPoints([...hiddenPoints]);
          relayout();
        }
        const revealId = points.find(point => point.revealFocus)?.id;   // `[2026-10-13]` 검증 로딩 중 지금 드러나는 마커
        markers.forEach(({ id, pin, marker }) => {
          const selected = id === nextSelectedId;
          setPinSelected(pin, selected, id === revealId);
          marker.setAttribute("aria-pressed", String(selected));
          marker.zIndex = selected || id === revealId ? 100 : 1;
        });
        if (changed) {
          previousGeometry = geometry;
          needsFit = true;
          centredOnMe = false;          // [2026-10-05] a different set of pins: an empty day after it centres on the customer again
          fit();
          pulseFirst();
        } else if (nextSelectedId !== selectedId) {
          const selected = points.find(({ id }) => id === nextSelectedId);
          if (selected) map.panTo(selected.coordinates);
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
        if (!next) { removeMe(); return; }
        if (meMarker) meMarker.position = next.coordinates;
        else meMarker = quietMarker(next.coordinates, ME_LABEL, createMeDot(), 0);
        const radius = accuracyRadius(next);
        if (radius === null) { meCircle?.setMap(null); meCircle = null; }
        else if (meCircle) { meCircle.setCenter(next.coordinates); meCircle.setRadius(radius); }
        else if (sdk.Circle) {
          meCircle = new sdk.Circle({
            map, center: next.coordinates, radius, strokeColor: ACCURACY_STYLE.color, strokeOpacity: ACCURACY_STYLE.opacity, strokeWeight: ACCURACY_STYLE.weight,
            fillColor: ACCURACY_STYLE.color, fillOpacity: ACCURACY_STYLE.fillOpacity, clickable: false, zIndex: 0,
          });
        }
        if (!points.length && !centredOnMe) needsFit = true;          // [2026-10-05] a map that never had pins was never fitted: the first position it learns centres it, once
        if (!points.length && needsFit) fit();
      }

      function setStays(stays: StayPoint[]) {
        if (destroyed) return;
        const key = staysKey(stays);
        if (key === previousStays) return;
        previousStays = key;
        clearStays();
        stayMarkers = stays.map((stay) => quietMarker(stay.coordinates, stay.label, createStayDot(stay), -1));
      }

      function cleanMap() {
        clearMarkers();
        clearLines();
        removeMe();
        clearStays();
        unwatch();
        sdk.event.clearInstanceListeners(map);
        map.unbindAll();
        container.replaceChildren();
      }

      try { update(options.points, options.selectedId, options.lines); setMe(options.me ?? null); setStays(options.stays ?? []); }
      catch (error) { cleanMap(); throw error; }

      return {
        update,
        setHiddenPoints,
        relayout,
        setMe,
        setStays,
        fit() { needsFit = true; fit(); },
        zoomBy(delta) { const zoom = map.getZoom?.(); if (!destroyed && typeof zoom === "number") map.setZoom(zoom + delta); },
        centerOn(at, keepZoom) {
          if (destroyed) return;
          if (keepZoom) { map.panTo(at); return; }
          map.setCenter(at);
          map.setZoom(Math.max(map.getZoom?.() ?? 15, 15));
        },
        resize() {
          if (destroyed || !container.clientWidth || !container.clientHeight) return;
          const center = map.getCenter();
          sdk.event.trigger(map, "resize");
          if (center) map.setCenter(center);
          fit();
        },
        destroy() {
          if (destroyed) return;
          destroyed = true;
          clearTimeout(pulseTimer);
          cleanMap();
        },
      };
    },
  };
}
