import type { Coordinates, MapAdapter, MapLine, MapPoint, MyLocation, StayPoint } from "../model";
import { linePieces, lineStyle, linesKey, walkStyle } from "./lines";
import { ACCURACY_STYLE, accuracyRadius, createMeDot, createStayDot, ME_BOX, ME_LABEL, meKey, STAY_BOX, staysKey } from "./me";
import { createPin, geometryKey, layoutPins, paintPinGroups, pinArea, PIN_BOX, pinPriority, placePin, pointLabel, pulsePin, setPinSelected, type PinSlot } from "./pin";
import { createSdkLoader } from "./sdk-loader";
import { setMarkerHidden } from "./visibility";

interface NaverLatLng { lat(): number; lng(): number }
interface NaverSize { width: number; height: number }
/** Optional: a build of the SDK without it still shows the pins (they then keep their first side). */
interface NaverProjection { fromCoordToOffset(position: NaverLatLng): { x: number; y: number } }
/** Optional, every method: whatever this build of the SDK has is used, and nothing is guessed. */
interface NaverBounds { south?(): number; west?(): number; north?(): number; east?(): number; getNE?(): NaverLatLng; getSW?(): NaverLatLng }
interface NaverMap {
  getProjection?(): NaverProjection | undefined;
  getBounds?(): NaverBounds | undefined;
  panTo(position: NaverLatLng): void;
  setCenter(position: NaverLatLng): void;
  getCenter(): NaverLatLng;
  setZoom(zoom: number, effect?: boolean): void;
  getZoom?(): number;
  fitBounds(points: NaverLatLng[], margins: { top: number; right: number; bottom: number; left: number; maxZoom: number }): void;
  setSize(size: NaverSize): void;
  destroy(): void;
}
/** `setPosition` is optional: without it 「내 위치」 is moved by making its marker again. */
interface NaverMarker { setMap(map: NaverMap | null): void; setZIndex(zIndex: number): void; setPosition?(position: NaverLatLng): void }
interface NaverPolyline { setMap(map: NaverMap | null): void }
/** `[2026-10-05]` The accuracy circle of 「내 위치」 (`naver.maps.Circle`, radius in metres). */
interface NaverCircle { setMap(map: NaverMap | null): void; setCenter(center: NaverLatLng): void; setRadius(radius: number): void }
interface NaverCircleOptions {
  map: NaverMap; center: NaverLatLng; radius: number; strokeColor: string; strokeOpacity: number; strokeWeight: number;
  fillColor: string; fillOpacity: number; clickable: boolean; zIndex: number;
}
interface NaverPolylineOptions {
  map: NaverMap; path: NaverLatLng[]; strokeColor: string; strokeOpacity: number; strokeWeight: number;
  strokeStyle: "solid" | "shortdash" | "dot"; strokeLineCap: "round"; strokeLineJoin: "round"; clickable: boolean;
}
interface NaverListener { eventName: string }
interface NaverSdk {
  Map: new (container: HTMLElement, options: { center: NaverLatLng; zoom: number; minZoom: number; maxZoom: number; zoomControl: boolean }) => NaverMap;
  LatLng: new (lat: number, lng: number) => NaverLatLng;
  /** Optional: a build of the SDK without lines still shows the pins. */
  Polyline?: new (options: NaverPolylineOptions) => NaverPolyline;
  /** Optional: without it 「내 위치」 is the dot alone. */
  Circle?: new (options: NaverCircleOptions) => NaverCircle;
  Size: new (width: number, height: number) => NaverSize;
  Marker: new (options: {
    map: NaverMap; position: NaverLatLng; title: string;
    icon: { content: HTMLElement; size: NaverSize; anchor: { x: number; y: number } };
    /** `[2026-10-05]` 「내 위치」 and the stays take no press. */
    clickable?: boolean; zIndex?: number;
  }) => NaverMarker;
  Event: {
    addListener(target: NaverMarker | NaverMap, event: "click" | "idle" | "bounds_changed", callback: () => void): NaverListener;
    removeListener(listener: NaverListener): void;
  };
}

const loader = createSdkLoader<NaverSdk>({
  provider: "naver", label: "네이버지도", authCallback: "navermap_authFailure",
  read: () => {
    const sdk = (window as Window & { naver?: { maps?: NaverSdk } }).naver?.maps;
    return sdk?.Map && sdk.Marker && sdk.LatLng && sdk.Size && sdk.Event ? sdk : undefined;
  },
  url: (key, callback) => `https://oapi.map.naver.com/openapi/v3/maps.js?${new URLSearchParams({ ncpKeyId: key, callback })}`,
});

export function createNaverAdapter(clientId: string): MapAdapter {
  return {
    async create(container, options) {
      const sdk = await loader.load(clientId);
      const initial = options.points[0]?.coordinates ?? { lat: 37.5665, lng: 126.978 };
      const map = new sdk.Map(container, {
        center: new sdk.LatLng(initial.lat, initial.lng), zoom: 14, minZoom: 3, maxZoom: 18, zoomControl: false,        // [2026-10-05] the page draws the map's buttons itself (`map-controls.tsx`)
      });
      let destroyed = false;
      let points: MapPoint[] = [];
      let previousData = "";
      let previousGeometry = "";
      let selectedId: string | undefined;
      let needsFit = false;
      let centredOnMe = false;           // [2026-10-05] an empty map is centred on the customer once; pins (a different set of them) reset it
      let markers: { id: string; marker: NaverMarker; pin: HTMLElement; listener: NaverListener; onKey: (event: KeyboardEvent) => void }[] = [];
      let routes: NaverPolyline[] = [];
      let previousLines = "";
      const unwatch = loader.watchAuthFailure(options.onError);

      // ★`[2026-10-04]` Each pin stands on the side of its coordinate where it overlaps the fewest others (`layoutPins`); worked out again when the map comes to rest.
      let slots: Record<string, PinSlot> = {};
      let hiddenPoints = new Set<string>();
      function setHiddenPoints(ids: readonly string[]) {
        hiddenPoints = new Set(ids);
        markers.forEach(({ id, pin, marker }) => {
          const hidden = hiddenPoints.has(id);
          // SDK의 투명 클릭 영역도 함께 숨긴다. 같은 marker를 다시 붙이며
          // 좌표나 지도 시점은 바꾸지 않는다.
          if (hidden !== (pin.dataset.edgeHidden === "true")) marker.setMap(hidden ? null : map);
          setMarkerHidden(pin, hidden);
        });
      }
      function relayout() {
        const projection = map.getProjection?.();
        if (destroyed || !projection || !markers.length || !container.clientWidth || !container.clientHeight) return;
        const placed = points.filter((point) => !hiddenPoints.has(point.id)).sort((a, b) => pinPriority(a, b, selectedId)).map((point) => {
          const at = projection.fromCoordToOffset(new sdk.LatLng(point.coordinates.lat, point.coordinates.lng));
          return { id: point.id, x: at.x, y: at.y };
        });
        slots = layoutPins(placed, pinArea(container, options), slots);
        markers.forEach(({ id, pin }) => { if (slots[id]) placePin(pin, slots[id]); });
        paintPinGroups(markers, slots);
      }
      const motionListener = sdk.Event.addListener(map, "bounds_changed", () => options.onInteractionChange?.(true));
      const idleListener = sdk.Event.addListener(map, "idle", () => { options.onInteractionChange?.(false); relayout(); });
      // [2026-10-05] The zoom level goes to the screen (it asks for the detailed route lines when zoomed in).
      const zoomListener = sdk.Event.addListener(map, "idle", () => { const zoom = map.getZoom?.(); if (typeof zoom === "number") options.onZoom?.(zoom); });
      // [2026-10-05] What the map shows goes to the screen (the scale ruler, the chips for stops out of view) - nothing when this build of the SDK cannot say it.
      const sayView = () => {
        const bounds = map.getBounds?.(), zoom = map.getZoom?.();
        const ne = bounds?.getNE?.(), sw = bounds?.getSW?.();
        const south = bounds?.south?.() ?? sw?.lat(), west = bounds?.west?.() ?? sw?.lng(), north = bounds?.north?.() ?? ne?.lat(), east = bounds?.east?.() ?? ne?.lng();
        if (destroyed || [south, west, north, east, zoom].some((value) => typeof value !== "number") || !container.clientWidth || !container.clientHeight) return;
        options.onView?.({ south: south!, west: west!, north: north!, east: east!, width: container.clientWidth, height: container.clientHeight, zoom: zoom! });
      };
      const viewListener = sdk.Event.addListener(map, "idle", sayView);
      const movingViewListener = sdk.Event.addListener(map, "bounds_changed", sayView);

      function clearMarkers() {
        markers.forEach(({ marker, pin, listener, onKey }) => {
          sdk.Event.removeListener(listener);
          pin.removeEventListener("keydown", onKey);
          marker.setMap(null);
        });
        markers = [];
      }

      // ★`[2026-10-05 사용자 지시]` 「내 위치」: a marker that takes no press, under the pins (`zIndex` 0; pins are 1 and 100; stays −1), and its accuracy circle.
      let me: MyLocation | null = null;
      let meMarker: NaverMarker | null = null;
      let meCircle: NaverCircle | null = null;
      let previousMe = "";
      let stayMarkers: NaverMarker[] = [];
      let previousStays = "";
      const latLng = ({ lat, lng }: Coordinates) => new sdk.LatLng(lat, lng);

      function quietMarker(position: Coordinates, title: string, content: HTMLElement, size: number, zIndex: number): NaverMarker {
        return new sdk.Marker({
          map, position: latLng(position), title, clickable: false, zIndex,
          icon: { content, size: new sdk.Size(size, size), anchor: { x: size / 2, y: size / 2 } },
        });
      }

      function removeMe() {
        meMarker?.setMap(null);
        meCircle?.setMap(null);
        meMarker = null;
        meCircle = null;
      }

      function clearStays() {
        stayMarkers.forEach((marker) => marker.setMap(null));
        stayMarkers = [];
      }

      function fit() {
        if (!needsFit || !container.clientWidth || !container.clientHeight) return;
        // `[2026-10-05]` No pins: the customer's own position is the picture (once — the map does not chase them); nothing at all: wait.
        if (!points.length) {
          if (!me) return;
          needsFit = false;
          centredOnMe = true;
          map.setCenter(latLng(me.coordinates));
          map.setZoom(15);
          return;
        }
        needsFit = false;
        const focus = points.find(point => point.revealFocus);
        const positions = (focus ? [focus] : points).map(({ coordinates }) => new sdk.LatLng(coordinates.lat, coordinates.lng));
        if (positions.length === 1) {
          if (focus && previousGeometry && !matchMedia("(prefers-reduced-motion: reduce)").matches) map.panTo(positions[0]);
          else map.setCenter(positions[0]);
          map.setZoom(15);
        } else {
          map.fitBounds(positions, { top: 80 + (options.topInset ?? 0), right: 80, bottom: 80 + (options.bottomInset ?? 0), left: 80, maxZoom: 16 });
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
          // `[2026-10-15 ST4F-495]` A walked stretch is the SDK's own round-capped dots (`strokeStyle: "dot"`) in the walking colour.
          const style = walk ? { ...walkStyle(container), dash: null } : lineStyle(container, line);
          const route = new Polyline({
            map, path: points.map(({ lat, lng }) => new sdk.LatLng(lat, lng)), strokeColor: style.color, strokeOpacity: style.opacity, strokeWeight: style.weight,
            strokeStyle: walk ? "dot" : style.dash ? "shortdash" : "solid", strokeLineCap: "round", strokeLineJoin: "round", clickable: Boolean(options.onSelectLine),
          });
          // `[2026-10-06 사용자 지시]` A press on a route line picks it.
          (sdk as unknown as { Event?: { addListener?: (target: unknown, name: string, run: () => void) => void } }).Event?.addListener?.(route, "click", () => options.onSelectLine?.(line.id));
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
            pin.setAttribute("role", "button");
            pin.setAttribute("aria-label", pointLabel(point));
            pin.tabIndex = 0;
            const marker = new sdk.Marker({
              map, position: new sdk.LatLng(point.coordinates.lat, point.coordinates.lng), title: pointLabel(point),
              icon: { content: pin, size: new sdk.Size(PIN_BOX, PIN_BOX), anchor: { x: PIN_BOX / 2, y: PIN_BOX / 2 } },
            });
            const listener = sdk.Event.addListener(marker, "click", () => options.onSelect(point.id));
            const onKey = (event: KeyboardEvent) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                event.stopPropagation();
                options.onSelect(point.id);
              }
            };
            pin.addEventListener("keydown", onKey);
            markers.push({ id: point.id, marker, pin, listener, onKey });
          });
          previousData = data;
          setHiddenPoints([...hiddenPoints]);
          relayout();
        }
        const revealId = points.find(point => point.revealFocus)?.id;   // `[2026-10-13]` 검증 로딩 중 지금 드러나는 마커
        markers.forEach(({ id, pin, marker }) => {
          const selected = id === nextSelectedId;
          setPinSelected(pin, selected, id === revealId);
          pin.setAttribute("aria-pressed", String(selected));
          marker.setZIndex(selected || id === revealId ? 100 : 1);
        });
        if (changed) {
          previousGeometry = geometry;
          needsFit = true;
          centredOnMe = false;          // [2026-10-05] a different set of pins: an empty day after it centres on the customer again
          fit();
          pulseFirst();
        } else if (nextSelectedId !== selectedId) {
          const selected = points.find(({ id }) => id === nextSelectedId);
          if (selected) map.panTo(new sdk.LatLng(selected.coordinates.lat, selected.coordinates.lng));
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
        if (meMarker?.setPosition) meMarker.setPosition(latLng(next.coordinates));
        else {
          meMarker?.setMap(null);
          meMarker = quietMarker(next.coordinates, ME_LABEL, createMeDot(), ME_BOX, 0);
        }
        const radius = accuracyRadius(next);
        if (radius === null) { meCircle?.setMap(null); meCircle = null; }
        else if (meCircle) { meCircle.setCenter(latLng(next.coordinates)); meCircle.setRadius(radius); }
        else if (sdk.Circle) {
          meCircle = new sdk.Circle({
            map, center: latLng(next.coordinates), radius, strokeColor: ACCURACY_STYLE.color, strokeOpacity: ACCURACY_STYLE.opacity, strokeWeight: ACCURACY_STYLE.weight,
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
        stayMarkers = stays.map((stay) => quietMarker(stay.coordinates, stay.label, createStayDot(stay), STAY_BOX, -1));
      }

      function cleanUp() { clearTimeout(pulseTimer); clearMarkers(); clearLines(); removeMe(); clearStays(); sdk.Event.removeListener(motionListener); sdk.Event.removeListener(idleListener); sdk.Event.removeListener(zoomListener); sdk.Event.removeListener(viewListener); sdk.Event.removeListener(movingViewListener); unwatch(); map.destroy(); }

      try { update(options.points, options.selectedId, options.lines); setMe(options.me ?? null); setStays(options.stays ?? []); }
      catch (error) { cleanUp(); throw error; }

      return {
        update,
        setHiddenPoints,
        relayout,
        setMe,
        setStays,
        fit() { needsFit = true; fit(); },
        zoomBy(delta) { const zoom = map.getZoom?.(); if (!destroyed && typeof zoom === "number") map.setZoom(zoom + delta, true); },
        centerOn(at, keepZoom) {
          if (destroyed) return;
          if (keepZoom) { map.panTo(latLng(at)); return; }
          map.setCenter(latLng(at));
          map.setZoom(Math.max(map.getZoom?.() ?? 15, 15));
        },
        resize() {
          if (destroyed || !container.clientWidth || !container.clientHeight) return;
          const center = map.getCenter();
          map.setSize(new sdk.Size(container.clientWidth, container.clientHeight));
          map.setCenter(center);
          fit();
        },
        destroy() {
          if (destroyed) return;
          destroyed = true;
          cleanUp();
        },
      };
    },
  };
}
