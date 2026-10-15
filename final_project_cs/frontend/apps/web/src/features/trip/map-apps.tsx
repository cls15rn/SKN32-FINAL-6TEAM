"use client";

import { MapPinned, Route, X } from "lucide-react";
import type { Translate } from "@/lib/i18n";
import { useT } from "@/lib/settings";
import type { TripStop } from "./model";
import { useModalSheet } from "./sheet-modal";
import type { TripLeg } from "./trip-timeline";
import styles from "./trip-screen.module.css";

/**
 * `[2026-10-14 사용자 결정 — ST4F-492]` Opening a stop or a leg in the customer's own map app: Naver Map, KakaoMap or Google Maps, always by the web address (on a phone the map's own page
 * hands over to its app). The way between two stops uses the addresses of the app comparison sheet (artifact 「앱 대조 기록표」, 2026-10-06 — 39 of its 40 searches were opened by these
 * links and all 40 found a route): KakaoMap's `/link/by/traffic/name,lat,lng/name,lat,lng` (its web guide) and Naver Map's `/p/directions/lng,lat,name,,/…/-/transit` (no public guide).
 * A place is searched by its name (KakaoMap `/link/search/` — its web guide; Naver Map `/p/search/` — no public guide). Google Maps is the link the server already sends (`map_url` ·
 * the leg's directions). The map companies' own place ids are not in our data, so a search can list several places of the same name — asked of the backend.
 */
export type MapApp = "naver" | "kakao" | "google";
export interface MapLink { app: MapApp; url: string }
/** What the sheet opens: one place, or the way from one stop to the next. */
export type MapTarget = { route: false; name: string; links: MapLink[] } | { route: true; from: string; to: string; links: MapLink[] };

const APP_NAMES: Record<MapApp, [string, string]> = { naver: ["네이버 지도", "Naver Map"], kakao: ["카카오맵", "KakaoMap"], google: ["구글 지도", "Google Maps"] };

export const mapAppName = (app: MapApp, t: Translate) => t(...APP_NAMES[app]);

/** The name a map app shows for the stop: the server's place name, else the stop's own name (what its card shows). */
const placeName = (stop: TripStop) => stop.place ?? stop.title;
/** `/` and `,` split the apps' route paths — spaces instead (the comparison sheet did the same). */
const pathName = (stop: TripStop) => encodeURIComponent(placeName(stop).replace(/[/,]/g, " ").trim());

/** A place in the three apps; null when the server sent no map link for it (no coordinates — as before, no button). */
export function placeLinks(stop: TripStop): MapLink[] | null {
  if (!stop.mapUrl) return null;
  const query = encodeURIComponent(placeName(stop));
  return [
    { app: "naver", url: `https://map.naver.com/p/search/${query}` },
    { app: "kakao", url: `https://map.kakao.com/link/search/${query}` },
    { app: "google", url: stop.mapUrl },
  ];
}

/** By public transport from the stop before to the stop after; null when the server sent no directions for the leg or a stop has no coordinates. */
export function routeLinks(leg: TripLeg): MapLink[] | null {
  const from = leg.from.coordinates, to = leg.to.coordinates;
  if (!leg.directions || !from || !to) return null;
  const naver = (stop: TripStop, at: typeof from) => `${at.lng},${at.lat},${pathName(stop)},,`;
  const kakao = (stop: TripStop, at: typeof from) => `${pathName(stop)},${at.lat},${at.lng}`;
  return [
    { app: "naver", url: `https://map.naver.com/p/directions/${naver(leg.from, from)}/${naver(leg.to, to)}/-/transit` },
    { app: "kakao", url: `https://map.kakao.com/link/by/traffic/${kakao(leg.from, from)}/${kakao(leg.to, to)}` },
    { app: "google", url: leg.directions },
  ];
}

export const placeTarget = (stop: TripStop, links: MapLink[]): MapTarget => ({ route: false, name: placeName(stop), links });
export const routeTarget = (leg: TripLeg, links: MapLink[]): MapTarget => ({ route: true, from: placeName(leg.from), to: placeName(leg.to), links });

/**
 * The sheet from below, like 「공유하기」: what is opened, then the three apps by name. A press opens the app's page in a new tab and closes the sheet.
 * `[2026-10-14 사용자 결정]` No logos and no letter marks: Google allows a plain text or button link without asking but its product icons only with permission, Naver offers no map icon
 * for other services, and only KakaoMap gives its icon for shortcuts — so all three are names only.
 */
export function MapAppsSheet({ target, onClose }: { target: MapTarget; onClose: () => void }) {
  const t = useT();
  const { heading, shade, sheet, keys } = useModalSheet(onClose);
  const title = target.route ? t("지도 앱에서 길찾기", "Directions in a maps app") : t("지도 앱으로 열기", "Open in a maps app");
  return <div ref={shade} className={styles.shareShade} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }} onKeyDown={keys}>
    <section ref={sheet} className={styles.shareSheet} role="dialog" aria-modal="true" aria-labelledby="trip-map-apps-title" data-map-apps>
      <span className={styles.shareGrip} aria-hidden="true" />
      <header className={styles.shareHead}>
        <h2 id="trip-map-apps-title" ref={heading} tabIndex={-1}>{title}</h2>
        <button type="button" className={styles.shareClose} onClick={onClose} aria-label={t("닫기", "Close")}><X size={20} strokeWidth={1.8} aria-hidden="true" /></button>
      </header>
      <div className={styles.shareLink}>
        {target.route ? <Route size={22} strokeWidth={1.8} aria-hidden="true" /> : <MapPinned size={22} strokeWidth={1.8} aria-hidden="true" />}
        <span><strong>{target.route ? `${target.from} → ${target.to}` : target.name}</strong><small>{target.route ? t("대중교통 길찾기", "By public transport") : t("장소", "Place")}</small></span>
      </div>
      <ul className={styles.mapApps} aria-label={t("열 지도 앱", "Maps app to open")}>{target.links.map((link) =>
        <li key={link.app}><a className={styles.action} href={link.url} target="_blank" rel="noopener noreferrer" data-map-app={link.app} onClick={onClose}>{mapAppName(link.app, t)}</a></li>)}</ul>
    </section>
  </div>;
}
