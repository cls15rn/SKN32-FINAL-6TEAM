import type { Page } from "@playwright/test";

export type TestMapProvider = "naver" | "google";
type Position = { lat: number; lng: number };

export interface MapSdkSnapshot {
  provider: TestMapProvider;
  maps: number;
  fits: Position[][];
  pans: Position[];
  resizes: number;
  markers: { title: string; position: Position; attached: boolean }[];
  /** Route lines the page drew: the path in order, whether it is dashed, and whether it is still on the map. */
  lines: { path: Position[]; dashed: boolean; walk: boolean; attached: boolean }[];
  /** `[2026-10-05]` Circles the page drew (「내 위치」's accuracy): centre, radius in metres, and whether it is still on the map. */
  circles: { center: Position; radius: number; attached: boolean }[];
}

declare global {
  interface Window {
    __mapSdkDouble?: MapSdkSnapshot;
    __mapXss?: number;
  }
}

/** SDK contract doubles only: these tests do not claim real provider authentication or tiles. */
function installSdkDouble(provider: TestMapProvider) {
  const state: MapSdkSnapshot = { provider, maps: 0, fits: [], pans: [], resizes: 0, markers: [], lines: [], circles: [] };
  window.__mapSdkDouble = state;
  type LatLngLike = Position | LatLng;

  class LatLng {
    constructor(private latitude: number, private longitude: number) {}
    lat() { return this.latitude; }
    lng() { return this.longitude; }
  }

  function position(value: LatLngLike): Position {
    return value instanceof LatLng ? { lat: value.lat(), lng: value.lng() } : { lat: value.lat, lng: value.lng };
  }

  class LatLngBounds {
    points: Position[] = [];
    extend(value: LatLngLike) { this.points.push(position(value)); return this; }
    getCenter() {
      return new LatLng(
        this.points.reduce((sum, value) => sum + value.lat, 0) / this.points.length,
        this.points.reduce((sum, value) => sum + value.lng, 0) / this.points.length,
      );
    }
    isEmpty() { return this.points.length === 0; }
  }

  class SdkMap {
    center: LatLngLike;
    constructor(public container: HTMLElement, options: { center: LatLngLike }) {
      this.center = options.center;
      state.maps++;
      container.dataset.sdkMap = provider;
    }
    fitBounds(bounds: LatLngBounds | LatLngLike[]) {
      state.fits.push(Array.isArray(bounds) ? bounds.map(position) : [...bounds.points]);
    }
    panTo(value: LatLngLike) { this.center = value; state.pans.push(position(value)); }
    setCenter(value: LatLngLike) { this.center = value; state.pans.push(position(value)); }
    getCenter() { return this.center; }
    setZoom() {}
    setSize() { state.resizes++; }
    unbindAll() {}
    destroy() { this.container.replaceChildren(); }
  }

  type Listener = { target: object; name: string; callback: () => void };
  const listeners: Listener[] = [];
  const events = {
    addListener(target: object, name: string, callback: () => void) {
      const listener = { target, name, callback };
      listeners.push(listener);
      return { ...listener, remove: () => events.removeListener(listener) };
    },
    removeListener(listener: Listener) {
      const index = listeners.findIndex((item) => item.target === listener.target && item.name === listener.name && item.callback === listener.callback);
      if (index >= 0) listeners.splice(index, 1);
    },
    clearInstanceListeners(target: object) {
      for (let index = listeners.length - 1; index >= 0; index--) if (listeners[index].target === target) listeners.splice(index, 1);
    },
    trigger(target: object, name: string) {
      if (name === "resize") state.resizes++;
      listeners.filter((listener) => listener.target === target && listener.name === name).forEach((listener) => listener.callback());
    },
  };

  class Marker extends EventTarget {
    private currentMap: SdkMap | null = null;
    private node: HTMLElement;
    private record: MapSdkSnapshot["markers"][number];
    constructor(options: {
      map?: SdkMap;
      position: LatLngLike;
      title?: string;
      content?: HTMLElement;
      icon?: { content: HTMLElement | string };
    }) {
      super();
      const content = options.content ?? options.icon?.content;
      if (typeof content === "string") {
        this.node = document.createElement("div");
        // Naver interprets icon.content as HTML; preserve that behavior to catch unsafe labels.
        this.node.innerHTML = content;
      } else this.node = content ?? document.createElement("div");
      this.node.dataset.sdkMarker = provider;
      this.record = { title: options.title ?? "", position: position(options.position), attached: false };
      state.markers.push(this.record);
      this.node.addEventListener("click", () => {
        events.trigger(this, "click");
        this.dispatchEvent(new Event("gmp-click"));
      });
      this.map = options.map ?? null;
    }
    get map() { return this.currentMap; }
    set map(map: SdkMap | null) {
      this.currentMap = map;
      this.record.attached = Boolean(map);
      if (map) map.container.append(this.node);
      else this.node.remove();
    }
    setMap(map: SdkMap | null) { this.map = map; }
    setZIndex() {}
    /** `[2026-10-05]` naver `Marker.setPosition` — 「내 위치」 moves its marker instead of making a new one. */
    setPosition(value: LatLngLike) { this.record.position = position(value); }
    addListener(name: string, callback: () => void) { return events.addListener(this, name, callback); }
  }

  class AdvancedMarkerElement extends HTMLElement {
    private currentMap: SdkMap | null = null;
    private record: MapSdkSnapshot["markers"][number];
    zIndex = 0;
    constructor(options: { map: SdkMap; position: LatLngLike; title: string; gmpClickable: boolean }) {
      super();
      this.dataset.sdkMarker = "google";
      this.title = options.title;
      this.record = { title: options.title, position: position(options.position), attached: false };
      state.markers.push(this.record);
      if (options.gmpClickable) {
        this.setAttribute("role", "button");
        this.tabIndex = 0;
        this.addEventListener("click", () => this.dispatchEvent(new Event("gmp-click")));
        this.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            this.dispatchEvent(new Event("gmp-click"));
          }
        });
      }
      this.map = options.map;
    }
    get map() { return this.currentMap; }
    set map(map: SdkMap | null) {
      this.currentMap = map;
      this.record.attached = Boolean(map);
      if (map) map.container.append(this);
      else this.remove();
    }
    /** `[2026-10-05]` google `AdvancedMarkerElement.position` — 「내 위치」 moves its marker by setting it. */
    get position() { return this.record.position; }
    set position(value: LatLngLike) { this.record.position = position(value); }
  }

  /** `[2026-10-05]` Both providers draw 「내 위치」's accuracy the same way for this test: a centre, a radius in metres, a map to be on. */
  class Circle {
    private record: MapSdkSnapshot["circles"][number];
    constructor(options: { map?: SdkMap; center: LatLngLike; radius: number }) {
      this.record = { center: position(options.center), radius: options.radius, attached: Boolean(options.map) };
      state.circles.push(this.record);
    }
    setMap(map: SdkMap | null) { this.record.attached = Boolean(map); }
    setCenter(value: LatLngLike) { this.record.center = position(value); }
    setRadius(radius: number) { this.record.radius = radius; }
  }

  /** Both providers draw a line the same way for this test: a path, a map to be on, and a dash (naver `strokeStyle`, google an icon repeated along it). */
  class Polyline {
    private record: MapSdkSnapshot["lines"][number];
    constructor(options: { map?: SdkMap; path: LatLngLike[]; strokeStyle?: string; icons?: unknown[]; strokeOpacity?: number }) {
      // `[2026-10-15 ST4F-495]` a walked stretch is drawn as round dots (naver `dot`, google the circle symbol 0) — not the long dash of a guess (naver `shortdash`, google the bar icon)
      const icon = (options.icons?.[0] as { icon?: { path?: unknown } } | undefined)?.icon;
      this.record = { path: options.path.map(position), dashed: options.strokeStyle === "shortdash" || icon?.path === "M 0,-1 0,1", walk: options.strokeStyle === "dot" || icon?.path === 0, attached: Boolean(options.map) };
      state.lines.push(this.record);
    }
    setMap(map: SdkMap | null) { this.record.attached = Boolean(map); }
  }

  class Point { constructor(public x: number, public y: number) {} }
  class Size { constructor(public width: number, public height: number) {} }
  const common = { Map: SdkMap, LatLng, LatLngBounds, Polyline, Circle };
  const globals = window as unknown as Record<string, unknown>;
  if (provider === "naver") globals.naver = { maps: { ...common, Marker, Point, Size, Event: events } };
  else {
    customElements.define("test-google-advanced-marker", AdvancedMarkerElement);
    globals.google = {
      maps: { ...common, event: events, marker: { AdvancedMarkerElement } },
    };
  }

  // The real SDK resolves its documented callback after installing its globals.
  const script = document.currentScript as HTMLScriptElement | null;
  const callbackName = script ? new URL(script.src).searchParams.get("callback") : null;
  if (callbackName) {
    const callback = callbackName.split(".").reduce<unknown>((parent, key) => (parent as Record<string, unknown>)[key], window);
    if (typeof callback === "function") callback();
  }
}

/** Only maps SDK URLs are fulfilled; an unselected provider request is recorded and aborted. */
export async function stubMapSdk(page: Page, provider: TestMapProvider, options: { failFirst?: boolean; holdFirst?: boolean } = {}) {
  const requests: { provider: TestMapProvider; url: string }[] = [];
  let selectedRequests = 0;
  let releaseFirst!: () => void;
  const firstResponse = new Promise<void>((resolve) => { releaseFirst = resolve; });
  await page.route(/^https:\/\/(?:oapi\.map\.naver\.com|openapi\.map\.naver\.com|maps\.googleapis\.com)\//, async (route) => {
    const url = route.request().url();
    const requestedProvider = new URL(url).hostname === "maps.googleapis.com" ? "google" : "naver";
    requests.push({ provider: requestedProvider, url });
    if (requestedProvider !== provider) return route.abort("blockedbyclient");
    selectedRequests++;
    if (options.failFirst && selectedRequests === 1) return route.abort("failed");
    if (options.holdFirst && selectedRequests === 1) await firstResponse;
    await route.fulfill({ contentType: "application/javascript", body: `(${installSdkDouble.toString()})(${JSON.stringify(provider)});` });
  });
  return { requests, releaseFirst, selectedRequestCount: () => selectedRequests };
}

export function readMapSdk(page: Page) {
  return page.evaluate(() => window.__mapSdkDouble);
}
