"use client";

import type { ReactNode } from "react";
import { ArrowRight, BedDouble, Bike, Bus, Car, ChevronRight, Clock, Footprints, Map as MapIcon, MapPinned, Route, Tag, TrainFront } from "lucide-react";
import type { RouteShape } from "@/lib/live/route-shapes";
import type { Translate } from "@/lib/i18n";
import { useT } from "@/lib/settings";
import { isGuess, MODE_NAMES, rideLines } from "@/features/map/route-lines";
import { freeText } from "@/features/plan-check/day-times";
import pc from "@/features/plan-check/plan-check.module.css";
import { kindLook } from "@/features/plan-check/stop-kind";
import { placeLinks, placeTarget, routeLinks, routeTarget, type MapTarget } from "./map-apps";
import { distanceText, isStay, stayNights, type StayMark, type TripLeg } from "./trip-timeline";
import type { TripStop } from "./model";
import styles from "./trip-screen.module.css";

/** 요식 원장 속성 이름 → 화면 말. 모르는 이름은 서버가 보낸 그대로 보인다(숨기지 않는다). */
const TAG_LABELS: Record<string, [string, string]> = {
  card_payment: ["카드 결제", "Cards accepted"], parking: ["주차", "Parking"], takeout: ["포장", "Takeout"],
  vegetarian_menu: ["채식 메뉴", "Vegetarian menu"], kids_allowed: ["아이 동반", "Kids welcome"], halal: ["할랄", "Halal"],
};

/** The place facts of a stop in the server's words (old trip screen's rows): only what the server sent. */
export function placeRows(stop: TripStop, t: Translate, where = true) {
  const info = stop.placeInfo;
  if (!info) return null;
  const tags = info.tags.filter((tag) => !tag.startsWith("michelin")).map((tag) => (TAG_LABELS[tag] ? t(...TAG_LABELS[tag]) : tag));
  return <>
    {where && info.category && <><dt>{t("분류", "Type")}</dt><dd>{info.category}</dd></>}
    {where && info.address && <><dt>{t("주소", "Address")}</dt><dd>{info.address}</dd></>}
    {info.phone && <><dt>{t("전화", "Phone")}</dt><dd><a href={`tel:${info.phone.replace(/[^\d+]/g, "")}`}>{info.phone}</a></dd></>}
    {info.hours.length > 0 && <><dt>{t("영업시간", "Hours")}</dt><dd>{info.hours.join(" · ")}</dd></>}
    {info.hoursNotes.length > 0 && <><dt>{info.hours.length ? t("운영 안내", "Hours notes") : t("영업시간", "Hours")}</dt><dd>{info.hoursNotes.join(" · ")}</dd></>}
    {info.michelin && <><dt>{t("미쉐린", "Michelin")}</dt><dd>{info.michelin.level}{info.michelin.year ? ` (${info.michelin.year})` : ""}</dd></>}
    {tags.length > 0 && <><dt>{t("편의", "Amenities")}</dt><dd>{tags.join(" · ")}</dd></>}
    {/* The detail (`where` false) shows the credit as a tag beside the name — said once. */}
    {where && info.sourceNote && <><dt>{t("출처", "Source")}</dt><dd>{info.sourceNote}</dd></>}
  </>;
}

/** "2026-11-06" → "11/6". */
const monthDay = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

/** `[2026-10-13 사용자 결정 — 숙소 항목 B안]` The stay's nights and days as a pill: 「3박 · 11/6~11/9」. */
export function stayPill(stop: TripStop, t: Translate): string {
  const nights = stayNights(stop);
  return t(`${nights}박 · ${monthDay(stop.date)}~${monthDay(stop.endDate!)}`, `${nights} night${nights === 1 ? "" : "s"} · ${monthDay(stop.date)}–${monthDay(stop.endDate!)}`);
}

export function bookingLabel(stop: TripStop, t: Translate) {
  if (stop.booking === "booked") return t("예약 있음", "Booking noted");
  return null;
}

/**
 * `[2026-10-13 사용자 결정 — 목업 A]` The place's hours on the day of the visit (one rule, or two for a split day), in the server's words; with no weekly table at all, the source's own
 * text. Null when the server said nothing for that day.
 */
function visitHours(stop: TripStop): string | null {
  const info = stop.placeInfo;
  if (!info) return null;
  const sunday0 = new Date(`${stop.date}T12:00:00+09:00`).getUTCDay();
  const weekday = sunday0 === 0 ? 7 : sunday0;
  const lines = (info.dayHours ?? []).filter((rule) => rule.weekday === weekday).map((rule) => rule.line);
  if (lines.length) {
    // A split day (lunch, dinner) says its day once: 「화 11:30–15:00 · 17:00–21:00」.
    const day = `${lines[0].split(" ")[0]} `;
    return lines.map((line, at) => (at > 0 && line.startsWith(day) ? line.slice(day.length) : line)).join(" · ");
  }
  return info.hours.length === 0 && info.hoursNotes.length ? info.hoursNotes.join(" · ") : null;
}

/**
 * `[2026-10-07 사용자 결정 — 목업 C안]` One stop of a registered trip, on the plan check's timeline: its time at the left (start, and `[2026-10-12]` always its end small under it; it stays at
 * the top of the list while the opened stop is in view) and its card.
 * `[2026-10-13 사용자 결정 — 목업 Tripilot_personal/travel-careops/mockups/2026-10-13_타임라인_일정간격_접힌카드_목업_v2.html (A)]` The card shows its kind as an icon before the name (the
 * server's `kind`, its `kind_label` said for the icon — a word chip read like the plan check's 「확인 필요」), and from the card's left, under that icon, the place's type (when the server
 * gives one), its hours on the visit day and its address. Opened, only the three buttons are added: the facts list is no longer in the card (the detail still shows it — backup of
 * what the card showed: Tripilot_personal/travel-careops/frontend/2026-10-13_등록여행_펼친카드내용_백업.md). It opens and closes only when pressed.
 * `[2026-10-12 사용자 결정 — E]` 「상세 보기」 is no longer in the card's head: it takes the place of 「지도에서 보기」 among the buttons (that button only lowered the sheet — the customer sets its height).
 * `past` (its end has passed): the same card in grey with 「지난 일정」 at the right of its head; `started` fills its dot (now has passed its start).
 * `[2026-10-14 사용자 결정 — ST4F-492]` 「이 일정 질문하기」 is no longer in the card (the detail keeps it), and 「지도 앱으로 열기」 opens the sheet of three map apps (`map-apps.tsx`).
 */
export function StopRow({ stop, open, selected, past = false, started = false, onToggle, onDetail, onMapApps }: {
  stop: TripStop; open: boolean; selected: boolean; past?: boolean; started?: boolean;
  onToggle: () => void; onDetail: () => void; onMapApps: (target: MapTarget) => void;
}) {
  const t = useT();
  const id = `trip-stop-${stop.id}`;
  const timeFocused = open || selected;
  const stay = isStay(stop);
  const hasTags = stay || stop.booking === "booked" || stop.pinned || stop.paused || !!stop.badges?.length;
  const kind = kindLook(stop.kind);
  const KindIcon = kind.icon;
  const kindName = stop.kindLabel ?? t(kind.label[0], kind.label[1]);
  const category = stop.placeInfo?.category;
  const address = stop.placeInfo?.address;
  const hours = visitHours(stop);
  const maps = placeLinks(stop);
  // `[2026-10-13]` A stay's end is on its check-out day — it is not this day's end, so the line does not read it (`data-end`).
  return <li className={`${pc.entry} ${styles.stopEntry}`} data-type="item" data-entry-id={stop.id} data-time-focused={timeFocused || undefined} data-time-follow={open || undefined} data-selected={selected || undefined} data-paused={stop.paused || undefined}
    data-rail="stay" data-start={stop.time} data-end={(!stay && stop.endTime) || undefined} data-rail-open={open || undefined} data-past-open={past || undefined} data-passed={started || undefined}>
    <span className={pc.time}><span className={pc.timeStack}><time data-stop-start>{stop.time}</time><time className={pc.rangeEnd} data-stop-end>~{stay ? `${monthDay(stop.endDate!)} ` : ""}{stop.endTime || "—"}</time></span></span>
    <span className={pc.rail} aria-hidden="true"><span className={`${pc.dot} ${styles.stopDot}`} data-stay={stay || undefined} /></span>
    <article id={`trip-card-${stop.id}`} className={`${pc.card} ${styles.stopCard}`} data-stay={stay || undefined} data-open={open || undefined} aria-labelledby={`${id}-title`}>
      <div className={`${pc.cardTop} ${styles.stopHead}`}>
        <button type="button" className={`${pc.cardHead} ${styles.stopToggle}`} id={`stop-button-${stop.id}`} aria-expanded={open} aria-controls={`stop-detail-${stop.id}`} onClick={onToggle}>
          <span className={styles.stopName}>
            <span className={styles.stopKind} role="img" aria-label={kindName} title={kindName}><KindIcon size={18} strokeWidth={1.8} aria-hidden="true" /></span>
            <strong id={`${id}-title`} className={pc.cardTitle}>{stay ? t(`${stop.title} 체크인`, `Check in · ${stop.title}`) : stop.title}</strong>
            {hasTags && <span className={styles.stopTags}>
              {stay && <span className={pc.pill} data-stay-nights>{stayPill(stop, t)}</span>}
              {stop.badges?.map((badge) => <span className={pc.pill} key={badge.code}>{badge.label}</span>)}
              {stop.booking === "booked" && <span className={pc.pill} data-booked>{bookingLabel(stop, t)}</span>}
              {stop.pinned && <span className={pc.pill}>{t("고정한 일정", "Pinned")}</span>}
              {stop.paused && <span className={pc.pill} data-state="review">{t("일정 정지", "Paused")}</span>}
            </span>}
          </span>
          {category && <span className={styles.stopFact}><Tag size={14} strokeWidth={1.8} aria-hidden="true" /><span>{category}</span></span>}
          <span className={styles.stopFact} data-missing={!hours || undefined}><Clock size={14} strokeWidth={1.8} aria-hidden="true" /><span>{hours ?? t("영업시간 정보 없음", "Hours not known")}</span></span>
          <span className={styles.stopFact} data-missing={!address || undefined}><MapIcon size={14} strokeWidth={1.8} aria-hidden="true" /><span>{address ?? t("주소 정보 없음", "Address not known")}</span></span>
        </button>
        {/* `[2026-10-08 사용자 지시]` ＋/－ 는 늘 맨 오른쪽이다. 오른쪽 끝의 ＋/－ 는 같은 접기 단추의 누르는 자리일 뿐이라 화면 읽기와 키보드에는 앞의 이름 단추 하나만 있다.
             `[2026-10-12 사용자 지시]` 지난 일정은 그 앞(머리 오른쪽)에 「지난 일정」 표시. */}
        {past && <span className={`${pc.pill} ${styles.stopPast}`} data-state="past">{t("지난 일정", "Past")}</span>}
        <button type="button" className={styles.toggleEnd} onClick={onToggle} tabIndex={-1} aria-hidden="true" data-toggle-mark><span className={styles.toggleMark}>{open ? "−" : "+"}</span></button>
      </div>
      {open && <div className={styles.stopBody} id={`stop-detail-${stop.id}`}>
        <div className={styles.detailActions}>
          <button type="button" className={styles.action} onClick={onDetail} aria-label={t(`${stop.title} 상세 보기`, `Details of ${stop.title}`)}>{t("상세 보기", "Details")}<ChevronRight size={16} strokeWidth={1.6} aria-hidden="true" /></button>
          {maps && <button type="button" className={styles.action} aria-haspopup="dialog" onClick={() => onMapApps(placeTarget(stop, maps))}><MapPinned size={16} strokeWidth={1.6} aria-hidden="true" />{t("지도 앱으로 열기", "Open in maps app")}</button>}
        </div>
      </div>}
    </article>
  </li>;
}

/**
 * `[2026-10-12 사용자 결정 — B 지난 일정]` A stop whose end has passed, folded: one thin grey line (its name, 「~end」, ＋) — not to scale. Pressed, it opens as the same card as before
 * (`StopRow` with `past`), the same facts and buttons, in grey.
 */
export function PastStopRow({ stop, onToggle }: { stop: TripStop; onToggle: () => void }) {
  const t = useT();
  // `[2026-10-13 숙소 B안 × 지난 일정]` A past check-in still says it is the check-in, and its nights stand where the end would (the end is on the check-out day, not this one).
  const stay = isStay(stop);
  const title = stay ? t(`${stop.title} 체크인`, `Check in · ${stop.title}`) : stop.title;
  return <li className={pc.entry} data-type="item" data-entry-id={stop.id} data-rail="stay" data-start={stop.time} data-end={(!stay && stop.endTime) || undefined} data-past data-passed>
    <span className={pc.time}>{stop.time}</span>
    <span className={pc.rail} aria-hidden="true"><span className={pc.dot} /></span>
    <div><button type="button" className={pc.pastLine} id={`stop-button-${stop.id}`} aria-expanded={false} onClick={onToggle} aria-label={t(`${title} · 지난 일정 · 펼치기`, `${title} · past · open`)}>
      <span>{title}</span><small>{stay ? stayPill(stop, t) : `~${stop.endTime || "—"}`}</small><b aria-hidden="true">+</b></button></div>
  </li>;
}

/** The way between two past stops: a small gap (not to scale). */
export function PastLegRow({ id }: { id: string }) {
  return <li className={pc.entry} data-type="move" data-entry-id={id} data-rail="move" data-past aria-hidden="true">
    <span className={pc.time} /><span className={pc.rail} /><div className={pc.pastGap} />
  </li>;
}

/**
 * The facts of a stop (the old trip screen's card), in the server's words, for the detail (`brief` leaves out what it shows above them: date, time, kind, address, credit).
 * `[2026-10-13 사용자 결정]` The opened card no longer shows them (backup: Tripilot_personal/travel-careops/frontend/2026-10-13_등록여행_펼친카드내용_백업.md).
 */
export function StopFacts({ stop, next, brief = false }: { stop: TripStop; next: TripStop | undefined; brief?: boolean }) {
  const t = useT();
  // Michelin already has its own row from the place facts (`placeRows`) — the mark row does not repeat it.
  const marks = (stop.badges ?? []).filter((badge) => !(badge.code === "michelin" && stop.placeInfo?.michelin));
  return <dl className={styles.details}>
    {!brief && <><dt>{t("날짜", "Date")}</dt><dd>{stop.date}</dd>
      <dt>{t("예정 시간", "Planned time")}</dt><dd>{stop.time}</dd></>}
    {placeRows(stop, t, !brief)}
    {stop.booking === "booked" && <><dt>{t("예약 표시", "Booking note")}</dt><dd>{bookingLabel(stop, t)}</dd></>}
    <dt>{t("다음 일정", "Next stop")}</dt><dd>{next ? `${next.time} · ${next.title}` : t("이날 마지막 일정", "Last stop of the day")}</dd>
    {stop.otherOptions && stop.otherOptions.length > 0 && <><dt>{t("다른 안", "Other options")}</dt><dd>{stop.otherOptions.map((option) => option.name).join(" · ")}</dd></>}
    {marks.length > 0 && <><dt>{t("식당 표시", "Restaurant marks")}</dt><dd>{marks.map((badge) => badge.label).join(" · ")}</dd></>}
    <dt>{t("입력한 메모", "Your notes")}</dt><dd>{stop.notes || t("등록된 메모가 없어요.", "No notes added.")}</dd>
  </dl>;
}

/**
 * `[2026-10-13 사용자 결정 — 숙소 항목 B안]` A stay on a day after its check-in. 「from」: a band at the top — the day starts from the hotel (the move from it is drawn only when the server keeps one).
 * 「checkout」: the check-out at its time, as a stop-like row. Pressing either shows the stay itself (`onShow`) — the stay is one item.
 */
export function StayRow({ stay, onShow }: { stay: StayMark; onShow: () => void }) {
  const t = useT();
  const { stop, phase, night, nights } = stay;
  if (phase === "from") {
    const which = night > nights ? t("마지막 날", "Last day") : t(`${night}박째`, `Night ${night}`);
    return <li className={styles.stayBand} data-type="stay" data-entry-id={`${stop.id}:from`}>
      <button type="button" className={styles.stayBandButton} onClick={onShow} aria-label={t(`${stop.title}에서 출발 — 숙소 보기`, `Leaving from ${stop.title} — show the stay`)}>
        <BedDouble size={16} strokeWidth={1.8} aria-hidden="true" />
        <span>{t(`${stop.title}에서 출발`, `Leaving from ${stop.title}`)}</span>
        <small>{which}</small>
      </button>
    </li>;
  }
  return <li className={`${pc.entry} ${styles.stopEntry}`} data-type="stay" data-entry-id={`${stop.id}:checkout`}>
    <span className={pc.time}><span className={pc.timeStack}><time data-stay-checkout>{stop.endTime || "—"}</time></span></span>
    <span className={pc.rail} aria-hidden="true"><span className={`${pc.dot} ${styles.stopDot}`} data-stay /></span>
    <article className={`${pc.card} ${styles.stopCard}`} data-stay>
      <div className={`${pc.cardTop} ${styles.stopHead}`}>
        <button type="button" className={`${pc.cardHead} ${styles.stopToggle}`} onClick={onShow}>
          <BedDouble size={18} strokeWidth={1.8} aria-hidden="true" />
          <span className={styles.stopName}>
            <strong className={pc.cardTitle}>{t(`${stop.title} 체크아웃`, `Check out · ${stop.title}`)}</strong>
            <span className={styles.stayNote}>{t("퇴실 마감", "Check-out time")}</span>
          </span>
        </button>
      </div>
    </article>
  </li>;
}

/** The icon of how a leg is travelled (the route line's mode); a plain arrow when the server did not say. */
export function ModeIcon({ mode }: { mode: RouteShape["mode"] | null }) {
  const props = { size: 14, strokeWidth: 1.8, "aria-hidden": true as const };
  if (mode === "walk") return <Footprints {...props} />;
  if (mode === "subway" || mode === "mixed") return <TrainFront {...props} />;
  if (mode === "bus") return <Bus {...props} />;
  if (mode === "taxi") return <Car {...props} />;
  if (mode === "bike") return <Bike {...props} />;
  return <ArrowRight {...props} />;
}

const MODE_EN: Record<RouteShape["mode"], string> = { walk: "Walk", bike: "Bike", taxi: "Taxi", subway: "Subway", bus: "Bus", mixed: "Transit", unknown: "Move" };

/** How the leg is travelled, in one line from what the server gave (「지하철 3호선→1호선 15분 · 2.1km」 · 「도보 12분」 · 「이동 20분」); null when it gave nothing to say. */
export function legSummary(leg: TripLeg, t: Translate): string | null {
  const how = leg.shape?.rides.length ? leg.shape.rides.map((ride) => ride.line).join("→") : leg.shape ? t(MODE_NAMES[leg.shape.mode], MODE_EN[leg.shape.mode]) : leg.move ? t("이동", "Move") : null;
  if (!how) return null;
  const time = leg.minutes !== null ? t(`${leg.minutes}분`, `${leg.minutes} min`) : null;
  const far = distanceText(leg.shape?.distanceM);
  return [[how, time].filter(Boolean).join(" "), far].filter(Boolean).join(" · ");
}

/**
 * `[2026-10-07 사용자 결정 — 목업 C안 · 계획 확인 화면과 같게]` The way from one stop to the next: its time with 「출발」 under it at the left (it stays at the top while the leg is in view),
 * the mode, how long and how far, and the free time after it (15 minutes or more; late says so). `[2026-10-12]` The space it takes is its time to scale (`RailList` — dashes only while
 * travelling). Opened: the server's own line for the move, when to leave and arrive, what it rides, whether the line is a guess, and the customer's map app. A plan that keeps no moves
 * (typed in) shows only the way to the next stop, as the old screen did.
 * `[2026-10-14 사용자 결정 — ST4F-492]` 「지도 앱에서 길찾기」 is a button like the stop card's (an underlined link before) and opens the sheet of three map apps (Google only before).
 */
export function LegRow({ leg, open, onToggle, onMapApps }: { leg: TripLeg; open: boolean; onToggle: () => void; onMapApps: (target: MapTarget) => void }) {
  const t = useT();
  const summary = legSummary(leg, t);
  const late = leg.slack !== null && leg.slack < 0;
  const free = leg.slack !== null && !late ? freeText(leg.slack) : null;
  const freeSays = late ? t(`${Math.abs(leg.slack!)}분 늦어요`, `${Math.abs(leg.slack!)} min late`) : free ? t(`여유 ${free}`, `${free} free`) : null;
  const maps = routeLinks(leg);
  const directions = maps && <div className={styles.detailActions}><button type="button" className={styles.action} aria-haspopup="dialog" onClick={() => onMapApps(routeTarget(leg, maps))}><Route size={16} strokeWidth={1.6} aria-hidden="true" />{t("지도 앱에서 길찾기", "Directions in maps app")}</button></div>;
  const time: ReactNode = leg.move ? <span className={pc.timeStack}><time data-move-departure>{leg.move.departAt || t("미정", "TBD")}</time>{leg.move.departAt && <small className={pc.departWord}>{t("출발", "Leave")}</small>}<time className={pc.rangeEnd} data-move-arrival aria-hidden={!open}>{leg.move.arriveAt ? `~${leg.move.arriveAt}` : t("미정", "TBD")}</time></span> : null;
  const rail = { "data-rail": "move", "data-dep": leg.move?.departAt || undefined, "data-arr": leg.move?.arriveAt || undefined, "data-rail-open": open || undefined };
  if (!summary) {
    return <li className={`${pc.entry} ${styles.legEntry}`} data-type="move" data-entry-id={leg.id} {...rail}>
      <span className={pc.time} />
      <span className={pc.rail} aria-hidden="true"><span className={`${pc.dot} ${pc.departureMark}`}>−</span></span>
      <div className={pc.moveCol}><p className={`${pc.move} ${styles.legPlain}`}><span className={pc.modeIcon}><ModeIcon mode={null} /></span>{t("이동 정보가 아직 없어요", "Travel details are not available yet")}</p>{directions}</div>
    </li>;
  }
  const guess = leg.shape && isGuess(leg.shape);
  const lines = [
    leg.move?.title,
    leg.move ? [t(`${leg.move.departAt} 출발`, `Leave ${leg.move.departAt}`), leg.move.arriveAt && t(`${leg.move.arriveAt} 도착`, `arrive ${leg.move.arriveAt}`)].filter(Boolean).join(" → ") : null,
    ...(leg.shape ? rideLines(leg.shape, t) : []),
  ].filter(Boolean) as string[];
  return <li className={`${pc.entry} ${styles.legEntry}`} data-type="move" data-entry-id={leg.id} data-time-focused={open || undefined} {...rail}>
    <div className={pc.moveClock}><span className={pc.time}>{time}</span></div>
    <span className={pc.rail} aria-hidden="true"><span className={`${pc.dot} ${pc.departureMark}`}>−</span></span>
    <div className={pc.moveCol}>
      <div className={pc.move} data-open={open || undefined} data-rail-box>
        <button type="button" className={pc.moveHead} aria-expanded={open} aria-controls={`trip-leg-${leg.id}`} onClick={onToggle}>
          <span className={pc.modeIcon}><ModeIcon mode={leg.shape?.mode ?? null} /></span>
          <span className={pc.moveText}>{summary}</span>
          {freeSays && <span className={pc.moveFree} data-late={late || undefined}>{freeSays}</span>}
        </button>
        {open && <div id={`trip-leg-${leg.id}`} className={styles.legBody}>
          {lines.map((line) => <p key={line}>{line}</p>)}
          {guess && <p className={styles.legGuess}>{t("길을 몰라 두 곳을 직선으로 이은 구간이에요.", "The way is not known: the two places are joined by a straight line.")}{leg.shape?.note ? ` ${leg.shape.note}` : ""}</p>}
          {directions}
        </div>}
      </div>
    </div>
  </li>;
}
