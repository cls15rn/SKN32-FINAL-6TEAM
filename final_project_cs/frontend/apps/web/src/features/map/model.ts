/** Provider-neutral WGS84 coordinates. Never infer these from a place label. */
export interface Coordinates {
  lat: number;
  lng: number;
}

/**
 * How a pin looks besides selected: `muted` — shown for context, not pressable (the other stops while one is being
 * changed); `current` — the stop being changed; `candidate` — a place it could change to.
 */
export type PinTone = "muted" | "current" | "candidate" | "warn";   // `warn` [2026-10-07]: a stop that needs a look, while the list shows only those

/** A pin's own label and look, by stop id (plan check's change screen: 「A · B · C」 candidates, the rest greyed). */
export interface PinLook { label?: string; tone?: PinTone; revealFocus?: boolean }

export interface MapPoint {
  id: string;
  title: string;
  date: string;
  time: string;
  endTime?: string;
  /** One-based itinerary order for the selected date, including unlocated stops. */
  order: number;
  coordinates: Coordinates;
  /** Shown on the pin instead of `order`. */
  label?: string;
  tone?: PinTone;
  /** First-check replay camera target; earlier points remain available as pins or edge chips. */
  revealFocus?: boolean;
}

/**
 * A route line between two stops (`GET /v1/web/trips/{id}/route-shapes`). `dashed` = the road was not known and the places were joined
 * by a straight line — drawn dashed and pale so it never reads as a real road.
 */
/** How a route is travelled: a line is drawn in the colour of its kind, and the legend over the map names the same kinds. */
export type MapLineMode = "walk" | "bike" | "taxi" | "subway" | "bus" | "mixed" | "unknown";

export interface MapLine {
  id: string;
  points: Coordinates[];
  dashed: boolean;
  /** `[2026-10-06 사용자 지시 — 선 색으로 수단을 알아보게]` Absent = the theme's primary colour. */
  mode?: MapLineMode;
  /** `[2026-10-15 사용자 결정 — ST4F-495]` Stretches of `points` (`[first, last]` indexes) that are walked: drawn as round grey dots, the rest as `mode`. Absent = none. */
  walks?: readonly (readonly [number, number])[];
  /** Said to a screen reader and as the line's tooltip: 「A → B · 지하철」. */
  title: string;
}

/**
 * `[2026-10-05 사용자 지시]` Where the customer is (the browser's fix — only with their location consent): a blue dot, and a pale circle of
 * `accuracyM` metres when the browser says how sure it is. Never a pin: it takes no press and does not move the camera, except on a map with no pins.
 */
export interface MyLocation {
  coordinates: Coordinates;
  accuracyM: number | null;
}

/**
 * `[2026-10-05]` A place where the server found the customer stayed (`GET /v1/web/trips/{id}/location/stops`): a small grey dot. ★Called a "stay"
 * here so it is never mixed up with a stop of the plan (a pin). Only what the server gave is drawn — nothing is worked out on the page.
 */
export interface StayPoint {
  id: string;
  coordinates: Coordinates;
  /** Said to a screen reader and as the dot's tooltip: 「머문 곳 · 14:05–14:20」. */
  label: string;
}

/**
 * `[2026-10-05]` What the map shows right now: the corners of the view (degrees) and its size in px. Every provider says it the same way, so the buttons the page draws over the map (the scale ruler,
 * the chips for stops out of view) are worked out from it alone (`map-geometry.ts`). A provider that cannot say it says nothing - those parts are then left out, never guessed.
 */
export interface MapView { south: number; west: number; north: number; east: number; width: number; height: number; zoom: number }

export interface MapViewProps {
  /** 클릭한 좌표의 미리보기. 저장은 일정 입력 화면에서 별도로 확인한다. */
  coordinatePick?: { value: Coordinates | null; onPick: (at: Coordinates) => void };
  points: MapPoint[];
  selectedId?: string;
  onSelect: (stopId: string) => void;
  /** Route lines under the pins; they never move the camera (the camera fits the pins). */
  lines?: MapLine[];
  /** `[2026-10-06 사용자 지시 — 경로를 누르면 그 경로가 나온다]` A route line was pressed (a wider, invisible line makes it easy to hit). Absent: the lines take no press. */
  onSelectLine?: (lineId: string) => void;
  /** The route line the screen shows as picked: drawn thicker and in the selection colour. */
  selectedLineId?: string;
  /** `[2026-10-04]` Px at the top of the map that a bar floats over: the camera fits the pins below it and no pin is put under it. */
  topInset?: number;
  /** `[2026-10-05]` Px at the bottom of the map that the rounded top of a sheet floats over: no chip for a stop out of view stands there. */
  bottomInset?: number;
  /** `[2026-10-05]` 「내 위치」 — null/absent: nothing is drawn. */
  me?: MyLocation | null;
  /** `[2026-10-05]` Where the server says the customer stayed — absent/empty: nothing is drawn. */
  stays?: StayPoint[];
  /** `[2026-10-05 사용자 지시]` The zoom level, said once the map is up and each time it changes - a screen asks for the detailed route lines when it is zoomed in (`use-route-detail.ts`). Keep it the same function between renders. */
  onZoom?: (zoom: number) => void;
  /** `[2026-10-05]` What the map shows, said once the map is up and each time it comes to rest after being moved, zoomed or resized. */
  onView?: (view: MapView) => void;
  onInteractionChange?: (active: boolean) => void;
  /** Whether location consent was given. The button is also available before consent. */
  meAvailable?: boolean;
  /** Read a position after consent, then move this map's camera. */
  onRequestLocation?: (onLocated: (location: MyLocation) => void) => void;
}

export interface MapController {
  update(points: MapPoint[], selectedId?: string, lines?: MapLine[], selectedLineId?: string): void;
  /** 거리 칩이 대신 표시하는 일정의 마커만 숨긴다. 좌표·경로·선택·카메라는 유지한다. */
  setHiddenPoints(ids: readonly string[]): void;
  relayout?(): void;
  /**
   * `[2026-10-04 사용자 지시]` Back to the whole picture: the place and the zoom where every pin shows (what the map did when the pins first came).
   * ★`[2026-10-05]` Pins only — 「내 위치」 is left out (far away, it would shrink the pins to nothing). A map with no pins centres on 「내 위치」 instead.
   */
  fit(): void;
  /** `[2026-10-05 사용자 지시]` Draw, move or take away 「내 위치」. A map with no pins centres on it once (it never chases the customer). */
  setMe(me: MyLocation | null): void;
  /** `[2026-10-05]` Draw the places the server says the customer stayed (replaces what was there). */
  setStays(stays: StayPoint[]): void;
  /** `[2026-10-05]` One zoom level in (`1`) or out (`-1`) - the map's own buttons are drawn by the page. The camera is then the customer's: a new size no longer fits the pins again. */
  zoomBy(delta: 1 | -1): void;
  /** `[2026-10-05]` The camera to a place: for 「내 위치」 at least a street-level zoom; with `keepZoom` (a chip for a stop out of view) only the place changes. The customer's own camera from then on. */
  centerOn(at: Coordinates, keepZoom?: boolean): void;
  resize(): void;
  destroy(): void;
}

export interface MapAdapter {
  create(container: HTMLElement, options: MapViewProps & {
    onError: (error: Error) => void;
  }): Promise<MapController>;
}
