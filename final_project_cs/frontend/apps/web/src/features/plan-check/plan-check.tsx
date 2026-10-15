"use client";

import { useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, ArrowLeftRight, RotateCcw, ChevronDown, ChevronsDown, ChevronsRight, Sparkles, Pencil, Plus, Undo2, LockOpen, X } from "lucide-react";
import { DeviceFrame, HeaderSlot } from "@/components/layout/device-frame";
import { ToastView, useToastState } from "@/components/toast-view";
import { TripMap } from "@/features/map";
import { routeNotes, visibleShapes } from "@/features/map/route-lines";
import type { RouteShapes } from "@/lib/live/route-shapes";
import type { Coordinates, PinLook } from "@/features/map/model";
import type { TripStop } from "@/features/trip/model";
import type { Language, Translate } from "@/lib/i18n";
import { eul, ro } from "@/lib/josa";
import { useSettings, useT } from "@/lib/settings";
import { onToast, pushToast, type BusToast } from "@/lib/toast-bus";
import { foundCount, isInstantRow, needs, progress, STAGES, tally, timeline, type CheckRow, type ItemDraft, type PlanCandidate, type PlanCheckView, type PlanDay, type PlanItem, type Reread, type ServerProgress, type TripIssue } from "./model";
import { Act, HeadBadges, letter, pinKindLabel, reason, type ListFilter } from "./parts";
import { PlaceChange, type ChangeSession, type SearchState } from "./place-change";
import { dayTimes, withTimes } from "./day-times";
import { DayList, type RowContext } from "./plan-rows";
import { checkingList } from "./checking-list";
import { PreviewHint, ResultFooter, StopEditor, TripIssues, type Registration } from "./result-parts";
import { TaxiPricing } from "./taxi-pricing";
import type { WebTaxiPricing } from "@/lib/live/taxi-pricing";
import { dampedScrollTo } from "@/lib/damped-scroll";
import type { MoveOptions, MoveOptionMode } from "@/lib/live/move-options";
import { moveToast } from "./map-description";
import { usePlanIntroToast, useReadingToastHandoff } from "./use-plan-intro-toast";
import { useFollowScroll } from "./use-follow-scroll";
import { useListZoom } from "./list-zoom";
import { useTimeInset } from "./use-time-inset";
import { useDayMarker } from "./use-day-marker";
import { usePageScrollReset } from "./use-page-scroll-reset";
import { useQuietDayStrip } from "./use-quiet-day-strip";
import { useListEnd } from "./use-list-end";
import { usePullPastEnd } from "./use-pull-past-end";
import { useDayGestures } from "./use-day-gestures";
import { formatHm, moveStop, parseHm, type Retime } from "./time-plan";
import { TimeDragOverlay } from "./time-drag-overlay";
import { heldTexts, useTimeEdit } from "./use-time-edit";
import { useCenterTools } from "./use-center-tools";
import { REVEAL_MS, useReveal } from "./use-reveal";
import { AddStop, type AddStopPlace, type NewStop } from "./add-stop";
import type { InsertionPoint } from "./insertion-points";
import { useInsertionPoint } from "./use-insertion-point";
import { PlaceSearchHeader } from "./place-search-header";
import { useBackgroundBack } from "./use-background-back";
import type { RailNow } from "./rail-list";
import styles from "./plan-check.module.css";
import tripStyles from "@/features/trip/trip-screen.module.css";

export interface PlanCheckProps {
  /** The latest snapshot. The screen paces the changes between snapshots itself (`useReveal`). */
  view: PlanCheckView;
  /** The back arrow — e.g. back to the plan entry. */
  onBack: () => void;
  /** Called once the screen has drawn everything in `view` and held it a moment — e.g. to move on after the last line. */
  onCaughtUp?: () => void;
  /** Uses the shared toast; arbitrary content cannot be inserted above the map. */
  notice?: BusToast | null;
  /**
   * `[2026-10-06]` The questions asked while the server reads (`features/survey-questions/`). `top` stands under the reading screen's heading, `footer` at its bottom (it does not scroll),
   * `onActivity` hears every touch on the screen BY THE CUSTOMER (a press, a key, a wheel, a finger) - not scrolls: the screen scrolls itself too, and that is not a touch. Left out: the reading screen is as it was.
   */
  readingExtras?: { top: ReactNode; footer: ReactNode; onActivity: () => void };
  /** The plan is still on its way to the server (`sendingOf`): the stage `received` is then not the server's word, so it is not read out as one. */
  sending?: boolean;
  /** What the result can change, each wired by the page. One left out stays on the screen and says it is coming. */
  actions?: PlanCheckActions;
  /** Registering the result, at the bottom of the list once the check is done. */
  registration?: Registration;
  /** Trip-wide details the server asks for before it can register. */
  tripIssues?: TripIssue[];
  /**
   * `[2026-10-04]` The plan as 「전체 자동 추천」 WOULD make it (the server's dry run: nothing saved), once it has been asked for. The screen stands it under `view` and lets the list
   * scroll on into it; `view` stays the plan as it is, so both pictures are held and the customer can go back up to the one before.
   */
  previewView?: PlanCheckView | null;
  /** `[2026-10-04]` The lines between the stops for the plan as it is (the server's `route-shapes` for the intake); null = none (an older server, or nothing to draw). */
  routes?: RouteShapes | null;
  /** `[2026-10-05]` The zoom level of the map: the screen above asks for the detailed route lines once it is zoomed in (`features/map/use-route-detail.ts`). */
  onMapZoom?: (zoom: number) => void;
  /** `[2026-10-13 사용자 결정 — 수정 모드]` The screen edits a registered trip (`features/trip/trip-edit.tsx`) instead of checking a new plan. */
  edit?: TripEditMode;
}

/**
 * `[2026-10-13 사용자 결정 — 수정 모드]` What changes when the plan check edits a registered trip. The steps are the plan check's own — change, 「다시 제출」, checked, ✓ — and the ✓ is
 * `registration.onRegister`, which writes the trip's next version instead of a new trip. `[2026-10-13 사용자 지시]` The sheet's head has 「취소」 (the edit is let go — the page asks first).
 * The trip's 「지금」 runs on the list, and the first notice says what the screen is. Every change still goes through `actions`, as on the plan check of a new plan.
 */
export interface TripEditMode {
  /** 「취소」 in the sheet's head: the page asks, then drops the edit and goes back to the trip. */
  onCancel: () => void;
  /** 「지금」 on a day's line, as on the trip screen. */
  now: (date: string) => RailNow | null;
  /** The first notice (in place of 「계획 확인 화면이에요」). */
  intro: BusToast;
}

/** A place for one stop: an alternative or a search result, or just a name the server looks up. */
export type PlaceChoice = { candidate: PlanCandidate } | { name: string };

/** What 「전체 자동 추천」 did: one line per stop changed (「올리브영 → 올리브영 광화문점 11:00–12:00」), how many stayed. */
export interface AutoResult {
  changes: string[];
  kept: number;
  /** The stops it changed and what each had been (the place's name, or 「장소 미정」) — the screen marks them 「바뀜」. */
  changed?: { id: string; from: string }[];
}

/**
 * The result's changes — the seams the backend connects (PLAN_CHECK_SCREEN.md §4). Each resolves once the plan is the
 * new one (the page passes the new `view`), or rejects with the server's sentence. One left out is shown but says it is
 * coming, so the screen is whole either way.
 */
export interface PlanCheckActions {
  add?: (draft: NewStop) => Promise<void>;
  searchToAdd?: (anchorId: string, query: string) => Promise<AddStopPlace[]>;
  /** Save one stop's details as drafted (the server looks a typed place up again and checks the plan again). */
  edit?: (id: string, draft: ItemDraft) => Promise<void>;
  /**
   * `[2026-10-04]` Move several stops in time at once: each change is one stop's new start and end ("HH:MM"; `end` "" = no end time).
   * All changes go to the server in ONE request, so the plan is saved and checked again once. A change that changes nothing is skipped.
   */
  retime?: (changes: { id: string; start: string; end: string }[]) => Promise<void>;
  /** Take one stop out. */
  remove?: (id: string) => Promise<void>;
  /** Answer a trip-wide detail. */
  editTrip?: (field: "first_day" | "party_size" | "title" | "taxi_pricing", value: string | number | WebTaxiPricing) => Promise<void>;
  /** The alternatives for one stop, best first (mockup 「대체 후보 A · B · C」). */
  candidates?: (id: string) => Promise<PlanCandidate[]>;
  /** Why the list for a stop is short or empty (the server's `notes`: `no_same_kind` · `booked_needs_name` · …) — read after `candidates` has answered. */
  candidateNotes?: (id: string) => string[];
  /** Places matching words, nearest first (the change screen's search bar). */
  search?: (id: string, query: string) => Promise<PlanCandidate[]>;
  /** Put a place in one stop: a candidate or search result, or a name the server looks up. */
  replace?: (id: string, choice: PlaceChoice) => Promise<void>;
  /** The first alternative, in one press (the card's 「자동 추천」). */
  autoRecommend?: (id: string) => Promise<void>;
  /**
   * 「전체 자동 추천」: first only SHOWN (`previewRecommendAll` — nothing is saved; the screen then draws the plan as it would be), then saved
   * by `applyRecommended` or let go by `discardPreview`. `[2026-10-03]` Pushing past the end of the list or pressing the button never changes the plan.
   */
  previewRecommendAll?: () => Promise<AutoResult>;
  /**
   * `[2026-10-05]` Whether 「전체 자동 추천」 has anything to change - read from the dry run that is already asked for in the background, and shown nowhere.
   * `null` = not known yet (never read as 「nothing」): the hint at the end of the list stays as it is until it is.
   */
  hasRecommendation?: () => Promise<boolean | null>;
  applyRecommended?: () => Promise<AutoResult>;
  discardPreview?: () => void;
  /** Fix a stop so re-planning and recommendations keep it (「잠금」 — 「반드시 포함」). */
  lock?: (id: string, locked: boolean) => Promise<void>;
  unlockAll?: () => Promise<number>;
  /** `[2026-10-07]` Keep one of two readings of a photo's line (`value` = the kept one or the other) for the stop's name or booking number; the plan is checked again. */
  pickReading?: (id: string, field: "title" | "booking_no", value: string) => Promise<void>;
  /** Put back what the last change, delete or recommendation changed (「되돌리기」). */
  undo?: () => Promise<void>;
  /** Whether the last change can be put back (a change whose earlier place is not known cannot) — the screen offers 「되돌리기」 only when it can. */
  canUndo?: () => boolean;
  undoItem?: (id: string) => Promise<void>;
  canUndoItem?: (id: string) => boolean;
  /** Restore all saved customer edits for this intake together. */
  undoAll?: () => Promise<void>;
  canUndoAll?: () => boolean;
  /** Check the whole plan again without changing it (「다시 제출」); the view says which stop it is at (`rechecking`). `ids` = the stops that were changed (the ones shown being checked). */
  recheck?: (ids?: string[]) => Promise<void>;
  /**
   * `[2026-10-07 사용자 지시 — 이동수단 고르기]` The ways to go for one leg (subway · bus · taxi · walking: how long, what it costs, how much time is left), read when the customer opens the box - one leg at a
   * time, and remembered until the plan changes. `null` = the server has nothing to offer for this leg (an older server, a leg that is not calculated): the box is then not there.
   */
  moveOptions?: (moveId: string) => Promise<MoveOptions | null>;
  /** Take one way for a leg (`"recommended"` = what the calculator chose). The server counts it again and takes it only if it reaches in time; the plan then shown is the server's. No 「다시 제출」 is needed. */
  setMoveMode?: (moveId: string, mode: MoveOptionMode | "recommended") => Promise<void>;
}

/** How long the screen stays on what it has drawn before `onCaughtUp` — so the last line read is seen, not skipped. */
const HOLD_MS = 800;

/**
 * The plan check after 「계획 확인하기」, as the mockup `mockups/tripilot-plan-check-streaming.html` draws it: the uploaded
 * lines being read, then the places, hours and moves being checked on a map with a sheet over it, then the result —
 * cards and pins worked together, each stop locked, recommended, changed or deleted, 「전체 자동 추천 → 재검증 → 여행 등록」.
 * A phone-sized page of its own, like the start screen.
 */
export function PlanCheck({ view: latest, onBack, onCaughtUp, notice, readingExtras, sending = false, ...result }: PlanCheckProps) {
  const { view, settled } = useReveal(latest, Boolean(readingExtras));
  const { shown: shownNotice, show: showNotice, hide: hideNotice } = useToastState();
  const onReading = view.stage === "received" || view.stage === "reading" || Boolean(readingExtras);
  // The reading screen owns one notice; the header hands notices to this owner.
  useEffect(() => onReading ? onToast(showNotice) : undefined, [onReading, showNotice]);
  // 읽기 판이 결과 판으로 바뀌어도 바로 누른 머리줄 안내와 되돌리기 동작을 새 소유자에게 넘긴다.
  useReadingToastHandoff(onReading, shownNotice, hideNotice);
  const notified = useRef(new Set<string>());
  const noticeKey = notice ? JSON.stringify([notice.text, notice.sub, notice.note]) : null;
  useEffect(() => {
    if (!notice || !noticeKey) { notified.current.clear(); return; }
    if (notified.current.has(noticeKey)) return;
    notified.current.add(noticeKey);
    if (onReading) showNotice(notice);
    else pushToast(notice);
  }, [notice, noticeKey, onReading, showNotice]);
  const t = useT();
  useEffect(() => {
    if (!settled || !onCaughtUp) return;
    const timer = setTimeout(onCaughtUp, HOLD_MS);
    return () => clearTimeout(timer);
  }, [settled, onCaughtUp]);
  // ★`[2026-10-04 사용자 지시]` Once the map is there the header is transparent: the map reaches the top and only the home mark, the plan's name and the menu float over it.
  // ★`[2026-10-07 사용자 지적 — 화면이 읽는 중 ↔ 확인으로 오간다]` While questions are on the screen (`readingExtras`) the reading screen STAYS, whatever stage the server has got to: the bar at its top
  //   carries the stage. It used to give way to the check screen as soon as the server's check began and was then pulled back to the reading screen when the server finished (the questions hold the page),
  //   and the check was drawn again from the first step once the customer was let through.
  const floating = !onReading && (view.stage === "checking" || view.stage === "done");
  return <DeviceFrame floating={floating} guardianIcon>
    <main id="main-content" tabIndex={-1} className={styles.screen} data-stage={view.stage} data-replay={readingExtras ? "paused" : "playing"} data-floating={floating || undefined}
      {...(readingExtras && { onPointerDownCapture: readingExtras.onActivity, onKeyDownCapture: readingExtras.onActivity, onWheelCapture: readingExtras.onActivity, onTouchStartCapture: readingExtras.onActivity })}>
      {onReading ? <Reading view={readingExtras ? latest : view} onBack={onBack} extras={readingExtras} /> : <Checking view={view} sourceView={latest} {...result} />}
      {onReading && shownNotice && <ToastView key={shownNotice.stamp} toast={shownNotice.toast} onDone={hideNotice} />}
      <p className="sr-only" role="status">{sending ? t("계획을 서버로 보내고 있어요.", "Sending your plan to the server.") : announce(readingExtras ? latest : view, t)}</p>
    </main>
  </DeviceFrame>;
}

function BackButton({ onBack, label }: { onBack: () => void; label?: string }) {
  const t = useT();
  return <button type="button" className={styles.back} onClick={onBack} aria-label={label ?? t("뒤로", "Back")}><ArrowLeft size={20} strokeWidth={1.6} aria-hidden="true" /></button>;
}

const stepLabels = (t: Translate) => [t("받았어요", "Received"), t("일정 읽기", "Reading"), t("장소·운영시간", "Places & hours"), t("정리 완료", "Done")];

/**
 * One straight line with four points; the filled part follows the lines read, then the places and moves checked.
 * ★`[2026-10-04 사용자 지시]` A point is checked and filled when the line has filled up to it (not when the stage after it begins), and no line shows
 * through a circle: the points stand over the line, each in the middle of a quarter of the width and the line runs from the first to the last.
 */
function ProgressBar({ view }: { view: PlanCheckView }) {
  const t = useT();
  const labels = stepLabels(t);
  const current = STAGES.indexOf(view.stage);
  const exact = progress(view);
  const value = Math.round(exact);
  // The line is as far as the points it has reached: point `i` of four sits at `i / 3` of the line (`stops`), and is reached when the line is that far.
  //   The last point is the screen having drawn everything (the stage 「정리 완료」), however far the server's own count says it is.
  const last = labels.length - 1;
  const reached = (index: number) => view.stage === "done" || (index === 0 ? view.stage !== "received" : index === last ? false : exact >= (index / last) * 100 - 0.05);
  const waitingAt = labels.findIndex((_, index) => !reached(index));
  // ★`[2026-10-03]` The server's "장소 3/14 · 광장시장" count comes while it reads, so the big bar says it too.
  // A terminal result supersedes the last partial SSE count; never announce it as still running.
  const at = view.stage !== "done" && view.serverProgress ? serverProgressText(view.serverProgress, t) : null;
  return <div className={styles.progress} data-size="large" role="progressbar" aria-label={t("계획 확인 진행", "Plan check progress")}
    aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-valuetext={`${labels[current]} · ${value}%${at ? ` · ${at}` : ""}`}>
    <div className={styles.track}><span className={styles.fill} style={{ width: `${Math.min(100, exact)}%` }} /></div>
    <ol className={styles.steps}>{labels.map((label, index) =>
      <li key={label} data-state={reached(index) ? "done" : index === waitingAt ? "current" : "waiting"}>
        <span className={styles.node} aria-hidden="true" /><span className={styles.stepLabel}>{label}</span>
      </li>)}</ol>
    {/* ★Under the steps, not between them and the track: `.steps` is pulled up over the track (negative margin), so a line there was drawn over the nodes (user's screenshot 2026-10-03). */}
    {at && <p className={styles.progressAt}>{at}</p>}
  </div>;
}

/**
 * `[2026-10-03 사용자]` The check's progress beside the brand in the header (it was a white bar of its own under the header):
 * the step the server is at and how far, with a thin line under it. Same `progressbar` as the big one on the reading screen.
 */
function HeaderProgress({ view }: { view: PlanCheckView }) {
  const t = useT();
  const labels = stepLabels(t);
  const current = STAGES.indexOf(view.stage);
  const value = Math.round(progress(view));
  // ★`[2026-10-03 사용자 지시]` What the server says it is at ("3/14 · 광장시장 운영시간 확인 중"): only when it sent a `progress` packet.
  // ★A "places" count belongs to the reading: once the check has begun, a left-over "13/14 · … 장소 확인 중" would keep saying that while the hours and legs are drawn.
  const at = view.stage !== "done" && view.serverProgress && view.serverProgress.phase !== "places" ? serverProgressText(view.serverProgress, t) : null;
  return <div className={styles.headProgress} role="progressbar" aria-label={t("계획 확인 진행", "Plan check progress")}
    aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-valuetext={`${labels[current]} · ${value}%${at ? ` · ${at}` : ""}`}>
    <span className={styles.headStep}>{labels[current]}<b>{value}%</b></span>
    <span className={styles.headTrack}><span style={{ width: `${value}%` }} /></span>
    {at && <span className={styles.headAt}>{at}</span>}
  </div>;
}

/** "3/14 · 광장시장 운영시간 확인 중" — the count and the step the server is at, in its words (no title when the packet names none). */
export function serverProgressText(at: ServerProgress, t: Translate): string {
  const what = at.phase === "places" ? t("장소 확인", "place check") : at.phase === "hours" ? t("운영시간 확인", "hours check") : t("이동 확인", "route check");
  return `${at.done}/${at.total} · ${at.title ? `${at.title} ` : ""}${what}${t(" 중", " in progress")}`;
}

/**
 * The plan's name in the header. ★`[2026-10-04 사용자 지시]` No pencil stands by it all the time: pressing the name shows the whole name and a pencil for a few seconds; the pencil turns it into
 * a field (Enter or leaving it saves, Esc keeps the name). Without a way to save it is plain text.
 * ★`[2026-10-05 사용자 지시 · 내 여행 칩 안 A]` The chip is as long as its text and stands at the left (it no longer takes the whole line); pressing it grows it to the right to show the whole
 *   name, and pressing the name again opens the field (the keyboard comes up) - the pencil is the same step for those who look for it.
 */
function TripTitle({ title, onSave }: { title: string; onSave?: (value: string) => Promise<void> }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const timer = setTimeout(() => setArmed(false), 4500);
    return () => clearTimeout(timer);
  }, [armed]);
  const [draft, setDraft] = useState(title);
  const input = useRef<HTMLInputElement>(null);
  const titleButton = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const cancelled = useRef(false);
  useEffect(() => { if (editing) { cancelled.current = false; input.current?.select(); } }, [editing]);
  useEffect(() => {
    if (editing || !onSave || !restoreFocus.current) return;
    restoreFocus.current = false;
    titleButton.current?.focus({ preventScroll: true });
  }, [editing, onSave]);
  if (!onSave && !editing) return <h1 className={styles.headTitle}>{title}</h1>;
  function commit(returnFocus = false) {
    if (cancelled.current || !onSave) return;
    cancelled.current = true;
    restoreFocus.current = returnFocus;
    const next = draft.trim();
    setEditing(false);
    if (next && next !== title) void onSave?.(next);
  }
  if (editing) {
    return <form className={styles.titleForm} onSubmit={(event) => { event.preventDefault(); commit(true); }}>
      <input ref={input} className={styles.titleInput} value={draft} maxLength={80} aria-label={t("계획 이름", "Plan name")} autoComplete="off" enterKeyHint="done"
        readOnly={!onSave} onChange={(event) => setDraft(event.target.value)} onBlur={() => commit()}
        onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); cancelled.current = true; restoreFocus.current = true; setEditing(false); } }} />
    </form>;
  }
  return <h1 className={styles.headTitle} data-armed={armed || undefined}>
    <button ref={titleButton} type="button" className={styles.titleButton} onClick={() => { if (armed) { setDraft(title); setEditing(true); setArmed(false); } else setArmed(true); }} aria-expanded={armed} aria-label={t(`계획 이름 · ${title}`, `Plan name · ${title}`)}>
      <span>{title}</span>
    </button>
    {armed && <button type="button" className={styles.titleEdit} onClick={() => { setDraft(title); setEditing(true); setArmed(false); }} aria-label={t(`계획 이름 바꾸기 · 지금 이름은 ${title}`, `Rename the plan · now ${title}`)}>
      <Pencil size={15} strokeWidth={1.8} aria-hidden="true" />
    </button>}
  </h1>;
}

/** 한국관광공사 이용조건 — 관광정보를 화면에 올리면 출처와 저작권 정책 링크를 같이 준다(루트 사실표 「출처 표시 유지」). 옛 목록 화면에 있던 줄을 새 화면으로 옮겼다. */
const TOUR_API_POLICY_URL = "https://api.visitkorea.or.kr/#/useServiceGuide/2";

/** What the list follows while the server's check is drawn: the newest row of the check. */
const newestRow = (box: HTMLElement) => {
  return box.querySelector<HTMLElement>('li[data-reveal-current="true"]');
};

/**
 * ①② The plan being read. ★`[2026-10-07 사용자 지시]` The 「올린 계획」 list (one row per line of the plan with a ✓ and the stop found) and its 「읽는 곳으로」 button are gone: the bar at the top says
 * how far the reading is, the number of stops found stays under it, and every line comes in detail on the next page (the check). The server still sends the lines (the bar counts them).
 */
function Reading({ view, onBack, extras }: { view: PlanCheckView; onBack: () => void; extras?: PlanCheckProps["readingExtras"] }) {
  const t = useT();
  const head = <header className={styles.head}>
    <div className={styles.headRow}><BackButton onBack={onBack} /><h1 className={styles.title}>{t("계획을 확인하고 있어요", "Checking your plan")}</h1></div>
    <p className={styles.desc}>{t("사진 한 장은 1분쯤 걸려요. 이 화면을 열어 두면 끝나는 대로 보여 드려요.", "A photo takes about a minute. Keep this page open and the result will appear.")}</p>
    <ProgressBar view={view} />
  </header>;
  const found = <p className={styles.found}>{t("찾은 일정", "Stops found")} <b>{foundCount(view)}</b>{t("개", "")}</p>;
  // `[2026-10-06]` With questions on the screen: heading, questions and the count are one scrolling page, and the footer stays at the bottom.
  if (extras) return <>
    <div className={styles.reading}>{head}{extras.top}{found}</div>
    {extras.footer}
  </>;
  return <div className={styles.reading}>{head}{found}</div>;
}

/** How high the sheet stands over the map: a strip, half the screen, or (nearly) all of it — the handle cycles them. */
const SHEETS = ["half", "full", "peek"] as const;
type Sheet = (typeof SHEETS)[number];

/** Under this height (px) there is no room left for a list worth reading. */
const COMPACT_BELOW = 190;
/** The fully folded sheet keeps only a handle with a 44px press target. */
const SHEET_MIN = 44;

interface Toast { text: string; sub?: string; undo?: boolean; /** An error: it stays until closed or swiped away. */ stay?: boolean; /** Puts back what this toast says was done (a batch of new times), instead of the plan-wide 「되돌리기」. */ revert?: () => void; /** The button's words when it is not 「되돌리기」 (a notice from outside this screen, `lib/toast-bus.ts`). */ undoLabel?: string; /** How long it stays when the default is too short (`lib/toast-bus.ts`). */ ms?: number }

type ResultProps = Pick<PlanCheckProps, "actions" | "registration" | "tripIssues" | "previewView" | "routes" | "onMapZoom" | "edit">;

/** What still needs a look in a plan, leaving out the stops marked for deletion (they are leaving) and the legs that end at one. */
function needsLeft(view: Pick<PlanCheckView, "items" | "moves">, removed: ReadonlySet<string>): { places: number; moves: number; total: number } {
  const places = view.items.filter((item) => item.verdict === "review" && !removed.has(item.id)).length;
  const moves = view.moves.filter((move) => move.verdict === "review" && !removed.has(move.fromId) && !removed.has(move.toId)).length;
  return { places, moves, total: places + moves };
}

/** ③④⑤ The map under a floating bar, the sheet over it. Once done, the list and the map are worked together. */
function Checking({ view, sourceView, actions = {}, registration, tripIssues = [], previewView = null, routes = null, onMapZoom, edit }: { view: PlanCheckView; sourceView: PlanCheckView } & ResultProps) {
  const t = useT();
  const { language, skipAnimation } = useSettings();
  const done = view.stage === "done";
  // What the customer picked once the check is done: one card or move open, one place selected, one day on the map.
  const [openAt, setOpenAt] = useState<{ list: "before" | "after"; id: string } | null>(null);
  const [mapMoving, setMapMoving] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  // `[2026-10-06 사용자 지시]` The route line the customer pressed on the map (the id of its shape): drawn picked, its leg opened in the list and described in the card over the map.
  const [selectedLine, setSelectedLine] = useState<string | null>(null);
  const [chosenDay, setChosenDay] = useState<number | null>(null);
  const [sheet, setSheet] = useState<Sheet>("half");
  // ⑤ The stop being changed (the change screen takes the sheet), the search in the top bar.
  const [change, setChange] = useState<ChangeSession | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<SearchState | null>(null);
  const [details, setDetails] = useState(false);
  // ★`[2026-10-06 사용자 지시 — 알림은 하나의 포맷으로]` This screen's notices are shown by the one bar every screen uses (`components/toast-view.tsx`): `setToast` turns what the screen says into it. The words of 「되돌리기」 and the
  //   plan-wide undo are read when the button is pressed (`latest`), not when the notice was made.
  const { shown: shownToast, show: showToast, hide: hideToast } = useToastState();
  const latest = useRef<{ t: Translate; undo: () => void } | null>(null);
  const setToast = useCallback((value: Toast | null) => {
    if (!value) { hideToast(); return; }
    showToast({
      text: value.text, sub: value.sub, ms: value.ms, stay: value.stay,
      action: value.undo || value.revert ? { label: value.undoLabel ?? latest.current?.t("되돌리기", "Undo") ?? "", run: value.revert ?? (() => latest.current?.undo()) } : undefined,
    });
  }, [hideToast, showToast]);
  usePlanIntroToast(done, showToast, t, edit?.intro);
  const [working, setWorking] = useState(false);
  const changingNow = useRef(false);
  const [changeToolsOpen, setChangeToolsOpen] = useState(false);
  // `[2026-10-03 사용자]` The place search lives in the header, where the brand stands: small there, and wide while it has focus.
  const headerSlot = useContext(HeaderSlot);
  // The list's height: one of three presets (the handle cycles them) or a height the customer dragged it to.
  const [custom, setCustom] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const [compact, setCompact] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [mapTight, setMapTight] = useState(false);
  const checkingBox = useRef<HTMLDivElement>(null);
  const resetFocusPending = useRef(false);
  const sheetBox = useRef<HTMLElement>(null);
  const bodyBox = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!selected) bodyBox.current?.style.removeProperty("--focus-room"); }, [selected]);
  // `[2026-10-05 사용자 지시]` The plan as it was and the proposed plan are TWO PAGES, like the first screens of the app: one is shown at a time, and the turn from one to the other
  //   happens past a threshold (the pull at the end / the start of the list fills a bar, let go early and it springs back) with the dots to say where we are - not one list glued
  //   together. Where the customer had got to on the plan as it was is kept (`pageAt`) for the way back.
  const pageAt = useRef(0);
  const [turn, setTurn] = useState<"forward" | "back" | null>(null);
  const grab = useRef<{ y: number; height: number; moved: boolean } | null>(null);
  const justDragged = useRef(false);
  /** The height the list was last laid out at, and the height a drag has reached (written straight to the screen, not through state: a drag re-rendering this whole screen on every move was what made it stutter). */
  const lastSheetHeight = useRef(0);
  const dragHeight = useRef<number | null>(null);
  const dragFrame = useRef<number | null>(null);
  /**
   * ★`[2026-10-04 사용자 지시]` 「전체 자동 추천」 is first only SHOWN: the plan as it WOULD be (nothing saved) stands under the plan as it is — one list, the same line running through — and
   * the sheet, the map and the buttons follow whichever of the two is in view (`side`). The screen holds both pictures; scrolling back up shows the plan as it was.
   * It is saved when the customer registers from it or changes something while looking at it (`settle`), and let go when the plan changes some other way.
   */
  const [recommended, setRecommended] = useState<{ count: number; was: Record<string, string> } | null>(null);
  const previewing = Boolean(recommended && previewView);
  // ★`[2026-10-05 사용자 선택 — 스크롤 끝 안 A]` The server said there is nothing to recommend for the plan AS IT IS (`noFix` holds that plan's mark; a change of the plan makes it stale):
  //   the hint at the end of the list is switched off and says why, and pushing on does not take the customer anywhere - the list stays where it is, shakes a little and says it once.
  const [noFix, setNoFix] = useState<string | null>(null);
  const [shake, setShake] = useState(false);
  const nudging = useRef(false);
  const planKey = useMemo(() => view.items.map((item) => [item.id, item.verdict ?? "", item.startsAt, item.endsAt, item.place, item.locked ? 1 : 0].join("|")).join(";"), [view.items]);
  const noFixNow = noFix === planKey;
  const [side, setSide] = useState<"before" | "after">("before");
  // The counts in the head, pressed, narrow the list to what needs a look / what was changed.
  const [filter, setFilter] = useState<ListFilter | null>(null);
  // `[2026-10-04]` Stops changed since the plan was last sent (and what each had been); stops marked for deletion (really taken out when the plan is sent again).
  const [changed, setChanged] = useState<Record<string, string>>({});
  const [removed, setRemoved] = useState<string[]>([]);
  const [inserting, setInserting] = useState<InsertionPoint | null>(null);
  const [mapPicking, setMapPicking] = useState(false);
  const [mapCoordinate, setMapCoordinate] = useState<Coordinates | null>(null);
  const [addingBusy, setAddingBusy] = useState(false);
  const removedSet = useMemo(() => new Set(removed), [removed]);
  // `[2026-10-04 사용자]` 한 번 더 확인할 때 바뀐 곳만 확인 표시가 다시 하나씩 켜진다.
  const [replay, setReplay] = useState<{ ids: string[]; at: number } | null>(null);
  // `[2026-10-04 사용자 결정]` 날짜 칩 줄: 하루씩 보이고(칩·좌우로 넘기기), 「전체」는 이어진 목록. `"all"` = 사용자가 「전체」를 골랐다, null = 목록이 지도의 날짜를 따라간다.
  const [dayChoice, setDayChoice] = useState<"all" | null>(null);
  // Which way the last move between days went (for the slide in); null when the list was not moved from one day to another.
  const [slide, setSlide] = useState<"next" | "prev" | null>(null);
  // 날짜는 한 손가락으로 넘긴다. 목록 크기는 메뉴에서만 바꿔 브라우저의 두 손가락 확대를 보존한다.
  const [peek, setPeek] = useState<{ side: -1 | 0 | 1; top: number; edge: boolean }>({ side: 0, top: 0, edge: false });
  const listZoom = useListZoom();
  const [swap, setSwap] = useState<"overview" | "day" | null>(null);
  const trackBox = useRef<HTMLDivElement>(null);
  const zoomBox = useRef<HTMLDivElement>(null);
  const stripBox = useRef<HTMLDivElement>(null);
  const markerBox = useRef<HTMLSpanElement>(null);
  // `[2026-10-06 사용자 지시]` What the customer did elsewhere on this screen (the Course Keeper turned on or off in the header) is told in THIS bar, like every other notice here.
  // The preview is gone from the page (the plan changed some other way): the list is the plan as it is.
  const hadPreview = useRef(false);
  useEffect(() => {
    if (previewView) { hadPreview.current = true; return; }
    if (!hadPreview.current) return;
    hadPreview.current = false;
    setRecommended(null);
    setSide("before");
  }, [previewView]);
  useEffect(() => {
    const element = sheetBox.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const watcher = new ResizeObserver(() => {
      const height = element.offsetHeight;
      lastSheetHeight.current = height;
      setCompact(height < COMPACT_BELOW);
      setCollapsed(height <= SHEET_MIN);
      setMapTight((checkingBox.current?.clientHeight ?? 0) - height < 136);
    });
    watcher.observe(element);
    if (checkingBox.current) watcher.observe(checkingBox.current);
    return () => watcher.disconnect();
  }, []);
  // ★`[2026-10-06 사용자 지적 — 여행 일정 창 확대·축소가 부드럽지 않다]` The list takes its new height at once (nothing inside re-flows while it moves; the map's edge follows with the same curve) and SLIDES there as a transform
  // on the compositor, from where its top edge was. A drag does not come through here: it is written to the screen as it goes (`dragSheet`) and ends with the same height it reached.
  useLayoutEffect(() => {
    const element = sheetBox.current;
    if (!element || typeof element.animate !== "function") return;
    const height = element.offsetHeight;
    const before = lastSheetHeight.current;
    lastSheetHeight.current = height;
    if (!before || Math.abs(height - before) < 2 || skipAnimation || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const slide = element.animate([{ transform: `translateY(${height - before}px)` }, { transform: "translateY(0)" }], { duration: 380, easing: "cubic-bezier(.22, .8, .3, 1)" });
    return () => slide.cancel();
  }, [sheet, custom, skipAnimation]);

  // ★A stop stands in both lists while a proposal is under the plan: what is open, and what is being edited, belongs to ONE of them — never both (opening the one out of sight would push the one in view).
  const setOpen = (id: string | null, list: "before" | "after" = "before") => setOpenAt(id === null ? null : { list, id });
  const closeOpen = (id: string) => setOpenAt((current) => current?.id === id ? null : current);
  useEffect(() => { if (!previewing) { setOpenAt((current) => current?.list === "after" ? { ...current, list: "before" } : current); } }, [previewing]);
  /** The plan the sheet, the map and the buttons are about: the proposed one while it is in view. */
  const shown: PlanCheckView = previewing && side === "after" ? previewView! : view;
  const readingList = checkingList(view, sourceView);
  const registered = Boolean(registration?.registeredHref);
  // Nothing can be changed while a stop or the whole plan is checked again, or once registered — buttons say why.
  const frozen = registered ? t("이미 등록한 여행이에요", "This trip is already registered")
    : view.rechecking ? t("다시 확인하는 중이에요 · 잠시만요", "Checking again · one moment")
      : working || view.items.some((item) => item.verdict === null) ? t("바뀐 일정을 다시 확인하는 중이에요 · 잠시만요", "Checking the change · one moment") : null;
  const explain = (why: string) => setToast({ text: why });
  const changeBlocked = useRef(frozen);
  changeBlocked.current = frozen;
  const busyReason = () => changeBlocked.current ?? (changingNow.current ? t("바뀐 일정을 다시 확인하는 중이에요 · 잠시만요", "Checking the change · one moment") : null);
  const changing = change ? view.items.find((item) => item.id === change.id) ?? null : null;
  /** Back to the card's own button after the change screen closes. */
  const refocus = (id: string, which: "edit" | "delete") => requestAnimationFrame(() => document.getElementById(`plan-${which}-${id}`)?.focus());

  const firstDay = shown.days.find((day) => shown.items.some((item) => item.day === day.day))?.day ?? 1;
  // While checking, the map follows the day being checked; once done it shows the day picked (the first by default).
  const mapDay = changing ? changing.day : done ? (chosenDay !== null && shown.items.some((item) => item.day === chosenDay) ? chosenDay : firstDay) : shown.items.at(-1)?.day ?? firstDay;
  const days = done ? shown.days.filter((day) => shown.items.some((item) => item.day === day.day)) : readingList.view.days;
  // Places the server could not settle have no pin; say so on the map, as the mockup's 「위치 미정」 tag does.
  const unlocated = changing ? [] : shown.items.filter((item) => item.day === mapDay && item.verdict !== null && !item.coordinates);
  // ★`[2026-10-07 사용자 지시 — 마커를 누르면 「위치 미정 · 호텔」 자리에 그 마커의 이름이 「경복궁 · 액티비티」로]` While a stop with a pin is picked, the tag under the map says which one it is.
  const pinned = !changing && selected ? shown.items.find((item) => item.id === selected && item.day === mapDay && item.coordinates) ?? null : null;

  // Search as the words change (a moment after typing stops). No search on this server: say so, keep 「change by name」.
  useEffect(() => {
    const words = query.trim();
    if (!change || !words) { setSearch(null); return; }
    if (!actions.search) { setSearch({ query: words, list: [], state: "unsupported" }); return; }
    setSearch({ query: words, list: [], state: "loading" });
    let live = true;
    const timer = setTimeout(() => {
      actions.search!(change.id, words).then((list) => { if (live) setSearch({ query: words, list, state: "ready" }); })
        .catch((error: unknown) => { if (live) setSearch({ query: words, list: [], state: "failed", message: reason(error) }); });
    }, 250);
    return () => { live = false; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a new search only when the words or the stop change
  }, [query, change?.id]);

  /**
   * A place picked on the map or in the list. ★`[2026-10-04 사용자 지시]` Pressed again it is let go: the card closes and the pin is no longer marked.
   * From the list a card opens (and is marked) or closes; from the map a pin marks its stop, opens it and brings it into view.
   */
  function pick(id: string, from: "map" | "list", list: "before" | "after" = previewing && side === "after" ? "after" : "before") {
    const item = shown.items.find((entry) => entry.id === id);
    if (!item) return;
    if (selected === id) { setSelected(null); closeOpen(id); return; }
    // A marker replaces the route explanation with the list's own place details.
    if (selectedLine && from === "map") setToast(null);
    setSelectedLine(null);
    setSelected(id);
    setChosenDay(item.day);
    setOpen(id, list);
    if (from === "map") {
      if (sheet === "peek") setSheet("half");
      const bringUp = () => requestAnimationFrame(() => requestAnimationFrame(() => {
        const box = bodyBox.current;
        const card = document.getElementById(`${list === "after" ? "after-" : ""}plan-card-${id}`);
        const row = card?.closest<HTMLElement>('li[data-type="item"]') ?? card;
        if (!box || !row) return;
        const head = sheetBox.current?.querySelector<HTMLElement>(`header.${styles.sheetHead}`);
        const visibleTop = Math.max(box.getBoundingClientRect().top, head?.getBoundingClientRect().bottom ?? 0) + 12;
        // Reserve only the missing room; the source and preview hint already provide space below the final stop.
        box.style.removeProperty("--focus-room");
        const target = box.scrollTop + row.getBoundingClientRect().top - visibleTop;
        box.style.setProperty("--focus-room", `${Math.max(0, target - (box.scrollHeight - box.clientHeight))}px`);
        dampedScrollTo(box, target);
      }));
      bringUp();
    }
  }
  /**
   * `[2026-10-06 사용자 지시 — 경로를 누르면 해당 경로가 나온다]` A route line pressed on the map: it is drawn picked, its leg is opened in the list (and brought into view), and a card over the map describes it. Pressed again it is let go.
   */
  function pickLine(lineId: string) {
    const shape = routes?.shapes.find((entry) => entry.itemId === lineId);
    const move = shape && (shown.moves.find((entry) => entry.id === shape.itemId) ?? shown.moves.find((entry) => entry.fromId === shape.fromItemId && entry.toId === shape.toItemId));
    if (!move) return;
    if (selectedLine === lineId) { setSelectedLine(null); closeOpen(move.id); setToast(null); return; }
    setSelectedLine(lineId);
    setSelected(null);
    const from = shown.items.find((entry) => entry.id === move.fromId);
    if (from) setChosenDay(from.day);
    const list = previewing && side === "after" ? "after" : "before";
    setOpenAt({ list, id: move.id });
    if (sheet === "peek") setSheet("half");
    const bringUp = () => requestAnimationFrame(() => bodyBox.current?.querySelector<HTMLElement>(`[data-entry-id="${move.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    bringUp();
    const name = (id: string, fallback: string | null) => shown.items.find((entry) => entry.id === id)?.title ?? fallback ?? "";
    showToast(moveToast({ move, from: name(move.fromId, shape.from), to: name(move.toId, shape.to), shape }, t, bringUp));
  }
  function showDay(day: number) {
    setChosenDay(day);
    if (shown.items.find((item) => item.id === selected)?.day !== day) setSelected(null);
  }

  /** ⑤ Open the change screen for one stop: the map shows it and its alternatives, the top bar becomes a search. */
  async function startChange(item: PlanItem) {
    timeEdit.close();
    setOpen(item.id); setSelected(item.id); setChosenDay(item.day);
    if (sheet === "peek") setSheet("half");
    setQuery(""); setDetails(false);
    setChange({ id: item.id, list: [], index: 0, state: actions.candidates ? "loading" : "ready" });
    requestAnimationFrame(() => document.getElementById("plan-change-title")?.focus({ preventScroll: true }));
    if (!actions.candidates) return;
    try {
      const list = await actions.candidates(item.id);
      const notes = actions.candidateNotes?.(item.id) ?? [];
      setChange((current) => current?.id === item.id ? { ...current, list, notes, state: "ready" } : current);
    } catch (error) {
      setChange((current) => current?.id === item.id ? { ...current, state: "failed", message: reason(error) } : current);
    }
  }
  function endChange() {
    const id = change?.id;
    setChange(null); setQuery(""); setSearch(null); setDetails(false);
    if (id) refocus(id, "edit");
  }

  /** The proposed plan stops being one the moment the plan changes in any other way. */
  function leaveRecommended() {
    if (recommended) { setRecommended(null); setSide("before"); }
  }
  /**
   * ★`[2026-10-04 사용자 지시]` Looking at the proposed plan and then changing something means the proposed plan is the plan: it is saved first (the server's
   * own check of it is already in), and the change is made on it.
   */
  async function settle(): Promise<void> {
    if (!(recommended && previewView)) return;
    const outcome = await actions.applyRecommended!();
    setChanged((current) => ({ ...current, ...wasOf(outcome) }));
    setRecommended(null);
    setSide("before");
  }
  /** Run one change; a refusal is said in the server's words, and nothing else moves. */
  async function run(step: () => Promise<Toast | null>, keepRecommended = false) {
    const why = busyReason();
    if (why) { explain(why); return; }
    changingNow.current = true;
    setWorking(true);
    try {
      if (!keepRecommended && previewing && side === "after") await settle();
      const said = await step();
      if (!keepRecommended) leaveRecommended();
      if (said) setToast(said);
    }
    catch (error) { setToast({ text: reason(error), stay: true }); }
    finally { changingNow.current = false; setWorking(false); }
  }
  /** ★`[2026-10-03 사용자 지적]` 「되돌리기」는 되돌릴 수 있을 때만 단다 (이전 장소를 모르면 누른 뒤에야 「되돌릴 수 없어요」라고 하던 것). */
  const undoNow = () => Boolean(actions.undo) && (actions.canUndo?.() ?? true);
  const markChanged = (id: string, was: string) => setChanged((current) => current[id] === undefined ? { ...current, [id]: was } : current);
  const wasText = (item: PlanItem) => item.place || item.title;

  function replace(item: PlanItem, choice: PlaceChoice) {
    const name = "candidate" in choice ? choice.candidate.name : choice.name;
    const was = wasText(item);
    void run(async () => {
      await actions.replace!(item.id, choice);
      markChanged(item.id, was);
      setChange(null); setQuery(""); setSearch(null); setDetails(false);
      setOpen(item.id); refocus(item.id, "edit");
      return { text: t(`${eul(item.title)} ${ro(name)} 바꿨어요`, `Changed ${item.title} to ${name}`),
        sub: t("바뀐 장소와 앞뒤 이동을 다시 확인해요", "Checking the new place and the moves either side"), undo: undoNow() };
    });
  }
  function recommend(item: PlanItem) {
    const was = wasText(item);
    void run(async () => {
      await actions.autoRecommend!(item.id);
      markChanged(item.id, was);
      setOpen(item.id);
      return { text: item.suggestion ? t(`${eul(item.title)} ${ro(item.suggestion)} 바꿨어요`, `Changed ${item.title} to ${item.suggestion}`) : t("대체 후보 1순위로 바꿨어요", "Changed to the first alternative"),
        sub: t("대체 후보 1순위 · 바뀐 장소와 앞뒤 이동을 다시 확인해요", "First alternative · checking the new place and the moves either side"), undo: undoNow() };
    });
  }
  /** `[2026-10-07]` The customer said which reading of the photo is right. Keeping the current one only confirms it; the other one changes the name (or booking number). */
  function pickReading(item: PlanItem, reread: Reread, value: string) {
    if (item.locked) { setToast({ text: t("고정한 일정이라 바꿀 수 없어요 · 잠금을 풀면 수정할 수 있어요", "Locked · unlock it to change it") }); return; }
    const was = wasText(item);
    const what = reread.field === "title" ? t("이름", "name") : t("예약번호", "booking number");
    void run(async () => {
      await actions.pickReading!(item.id, reread.field, value);
      if (value !== reread.current) markChanged(item.id, was);
      setOpen(item.id);
      return value === reread.current
        ? { text: t(`「${value}」 그대로 두었어요`, `Kept “${value}”`), sub: t(`사진에서 읽은 ${eul(what)} 확인했어요 · 다시 확인해요`, `The ${what} read off the photo is confirmed · checking again`) }
        : { text: t(`${eul(what)} 다른 읽기로 바꿨어요 · 「${value}」`, `Changed the ${what} to “${value}”`), sub: t("사진을 다르게 읽은 쪽으로 바꾸고 다시 확인해요", "Took the other reading of the photo · checking again with it"), undo: undoNow() };
    });
  }
  function lock(item: PlanItem) {
    const on = !item.locked;
    void run(async () => {
      await actions.lock!(item.id, on);
      if (on && timeEdit.edit?.id === item.id) timeEdit.close();
      return on ? { text: t(`${eul(item.title)} 꼭 넣을 일정으로 고정했어요`, `Locked ${item.title} in`), sub: t("다시 짜거나 바꿔도 빠지지 않게 서버에 알렸어요", "Re-planning and recommendations keep it") }
        : { text: t(`${item.title} 고정을 풀었어요`, `Unlocked ${item.title}`), sub: t("이제 바꾸거나 삭제할 수 있어요", "It can be changed or deleted now") };
    }, true);
  }
  function unlockAll() {
    void run(async () => {
      const count = await actions.unlockAll!();
      requestAnimationFrame(() => document.getElementById("plan-recommend-all")?.focus({ preventScroll: true }));
      return { text: t(`일정 ${count}개의 잠금을 모두 풀었어요`, `Unlocked all ${count} stops`) };
    }, true);
  }
  /**
   * ★`[2026-10-04 사용자 지시]` Deleting does not delete: the card turns grey and 「되돌리기」 stands where the bin was. The stop is really taken out when the plan is sent again (`recheck`).
   */
  function markRemoved(item: PlanItem) {
    const mark = () => {
      setRemoved((current) => current.includes(item.id) ? current : [...current, item.id]);
      closeOpen(item.id);
      return { text: t(`${item.title} 일정을 뺄 거예요`, `${item.title} will be taken out`), sub: t("「다시 제출」을 누르면 지워져요 · 카드의 되돌리기로 살릴 수 있어요", "It goes when you send the plan again · Undo on the card keeps it") };
    };
    if (previewing && side === "after") void run(async () => mark(), false);   // changing the proposed plan saves it first
    else setToast(mark());
  }
  function restore(item: PlanItem) {
    setRemoved((current) => current.filter((id) => id !== item.id));
    refocus(item.id, "delete");
  }
  // ★`[2026-10-04 사용자 지시]` The time at the left of a stop is changed in place (`useTimeEdit`): typed, dragged by the grip or stepped, the stops around it pushed along if the switch is on.
  //   What it looked like when the screen opened is kept (`baseline`): a stop whose time differs has 「되돌리기」, and the change menu has 「바꾼 시간 모두 되돌리기」.
  const baseline = useRef<Record<string, { start: string; end: string }>>({});
  useEffect(() => {
    if (sourceView.stage !== "done") return;
    // Capture the complete snapshot, before the paced drawing can fall behind a later time edit.
    for (const item of sourceView.items) {
      if (!baseline.current[item.id]) baseline.current[item.id] = { start: item.startsAt, end: item.endsAt };
    }
  }, [sourceView.stage, sourceView.items]);
  const adjustedIn = (plan: PlanCheckView | null) => new Set((plan?.items ?? []).filter((item) => {
    const first = baseline.current[item.id];
    return first !== undefined && (first.start !== item.startsAt || first.end !== item.endsAt);
  }).map((item) => item.id));
  const timesOf = (plan: PlanCheckView, id: string): Retime | null => {
    const item = plan.items.find((entry) => entry.id === id);
    const start = parseHm(item?.startsAt ?? "");
    return item && start !== null ? { id, start, end: parseHm(item.endsAt) } : null;
  };
  /** Send a batch of new times - ONE request, however many stops move - and say what was done; the toast's 「되돌리기」 sends the old times back. */
  async function sendTimes(list: "before" | "after", changes: Retime[], text: string, quiet = false): Promise<void> {
    const why = busyReason();
    if (why) throw new Error(why);
    const plan = list === "after" && previewView ? previewView : view;
    const fixed = changes.find((change) => {
      const item = plan.items.find((entry) => entry.id === change.id);
      return item && (item.locked || item.booked === true) && (item.startsAt !== formatHm(change.start) || item.endsAt !== (change.end === null ? "" : formatHm(change.end)));
    });
    if (fixed) throw new Error(t("고정·예약 일정의 시간은 바꿀 수 없어요 · 잠금을 풀거나 다른 일정을 조정해 주세요", "Locked or booked times cannot change · unlock it or adjust another stop"));
    const was = changes.flatMap((change) => { const old = timesOf(plan, change.id); return old ? [old] : []; });
    const title = (id: string) => plan.items.find((entry) => entry.id === id)?.title ?? id;
    const clock = (change: Retime) => `${formatHm(change.start)}${change.end === null ? "" : `–${formatHm(change.end)}`}`;
    changingNow.current = true;
    setWorking(true);
    try {
      if (list === "after" && previewing) await settle();
      await actions.retime!(changes.map((change) => ({ id: change.id, start: formatHm(change.start), end: change.end === null ? "" : formatHm(change.end) })));
      for (const change of changes) {
        const old = was.find((entry) => entry.id === change.id);
        // ★`[2026-10-07 사용자 지적 — 시간 초기화를 눌러도 수정된 것으로 나온다]` A time put back to the one the screen opened with is not a change any more: the mark 「바뀜 · 이전 …」 of an earlier time change is taken off (a
        //   mark that came from something else - a new place - stays), and nothing is marked for a stop that stands where it began.
        const first = baseline.current[change.id];
        const atFirst = first !== undefined && first.start === formatHm(change.start) && first.end === (change.end === null ? "" : formatHm(change.end));
        if (atFirst) setChanged((current) => current[change.id] === clock(change) ? Object.fromEntries(Object.entries(current).filter(([id]) => id !== change.id)) : current);
        else markChanged(change.id, old ? clock(old) : "");
      }
      leaveRecommended();
      setToast({
        text, sub: changes.slice(0, 2).map((change) => `${title(change.id)} → ${clock(change)}`).join(" · ") + (changes.length > 2 ? t(` 외 ${changes.length - 2}곳`, ` and ${changes.length - 2} more`) : ""),
        revert: quiet || !was.length ? undefined : () => { setToast(null); void sendTimes("before", was, t("시간을 되돌렸어요", "Put the times back"), true).catch((error: unknown) => setToast({ text: reason(error), stay: true })); },
      });
    } finally { changingNow.current = false; setWorking(false); }
  }
  const timeEdit = useTimeEdit({
    views: { before: view, after: previewing ? previewView : null },
    commit: (list, changes) => sendTimes(list, changes, t(`${changes.length}개 일정의 시간을 바꿨어요`, `Changed the time of ${changes.length} stop${changes.length > 1 ? "s" : ""}`)),
    // `[2026-10-05]` The free time is used up while dragging: said once, with the notice the screen already has.
    onNotice: (kind, held) => setToast({ text: t(...heldTexts(kind, held)) }),
  });
  /** One stop back to the time it had when the screen opened (the stops around it are pushed only if that is needed to make room). */
  function revertTime(item: PlanItem) {
    if (item.locked || item.booked === true) { setToast({ text: t("고정·예약 일정의 시간은 바꿀 수 없어요", "Locked or booked times cannot change") }); return; }
    const first = baseline.current[item.id];
    const list = previewing && side === "after" ? "after" : "before";
    const plan = list === "after" && previewView ? previewView : view;
    const times = dayTimes(plan, item.day);
    const index = times.indexOf.get(item.id);
    const start = parseHm(first?.start ?? "");
    if (!first || index === undefined || start === null) return;
    const end = parseHm(first.end);
    const moved = moveStop(times.stops, times.legs, index, start, { push: true, step: 1 }).changes.filter((change) => change.id !== item.id);
    void sendTimes(list, [...moved, { id: item.id, start, end }], t(`${item.title} 시간을 처음으로 되돌렸어요`, `Put the time of ${item.title} back`), true).catch((error: unknown) => setToast({ text: reason(error), stay: true }));
  }
  /** Every stop whose time was changed back to the time it had when the screen opened, in one request. */
  function resetTimes() {
    const list = previewing && side === "after" ? "after" : "before";
    // A completed edit can already change three stops while useReveal has drawn only one. Undo the actual snapshot.
    const plan = list === "after" && previewView ? previewView : sourceView;
    const changes = [...adjustedIn(plan)].flatMap((id): Retime[] => {
      const item = plan.items.find((entry) => entry.id === id);
      if (item?.locked || item?.booked === true) return [];
      const first = baseline.current[id];
      const start = parseHm(first?.start ?? "");
      return first && start !== null ? [{ id, start, end: parseHm(first.end) }] : [];
    });
    if (!changes.length) { setToast({ text: t("고정·예약 일정은 그대로 유지했어요", "Locked or booked stops were kept") }); return; }
    resetFocusPending.current = true;
    void sendTimes(list, changes, t("시간을 모두 처음으로 되돌렸어요", "Put every time back"), true)
      .catch((error: unknown) => { resetFocusPending.current = false; setToast({ text: reason(error), stay: true }); })
      .finally(() => requestAnimationFrame(() => {
        if (document.activeElement !== document.body && !document.activeElement?.classList.contains(styles.changeCount)) return;
        const box = checkingBox.current;
        // The trigger disappears after all changes are undone; leave keyboard focus on the adjacent check button instead.
        (box?.querySelector<HTMLButtonElement>(`button.${styles.changeCount}`)
          ?? box?.querySelector<HTMLButtonElement>(`button.${styles.mapNeeds}`)
          ?? box?.querySelector<HTMLButtonElement>(`button.${styles.handle}`))?.focus();
      }));
  }
  /** What the recommendation did or would do, as the line under the toast title. */
  const changesLine = (outcome: AutoResult) => outcome.changes.slice(0, 2).join(" · ") + (outcome.changes.length > 2 ? t(` 외 ${outcome.changes.length - 2}곳`, ` and ${outcome.changes.length - 2} more`) : "")
    + (outcome.kept ? t(` · ${outcome.kept}곳은 그대로`, ` · ${outcome.kept} kept`) : "");
  const wasOf = (outcome: AutoResult) => Object.fromEntries((outcome.changed ?? []).map((entry) => [entry.id, entry.from]));
  /**
   * ★`[2026-10-03 사용자 지시]` 자동으로 바꿀 곳이 없으면 이유만 말하고 끝내지 않는다 — 사용자가 직접 고쳐야 하는 첫 곳(확인 필요)을 열고 그 자리로 옮겨 가
   * 반짝여 보인다. 고칠 곳이 없는데 못 바꾼 것이면 이유만 말한다.
   */
  function showFirstNeed(): boolean {
    // What the customer edits is a stop, so a stop that needs a look comes first; a leg only when no stop does.
    const entries = view.days.flatMap((day) => timeline(view, day.day));
    const first = entries.find((entry) => entry.type === "item" && entry.item.verdict === "review") ?? entries.find((entry) => entry.type === "move" && entry.move.verdict === "review");
    if (!first) return false;
    const id = first.type === "item" ? first.item.id : first.move.id;
    if (first.type === "item") { setSelected(first.item.id); setChosenDay(first.item.day); }
    else setChosenDay(first.move.day);
    setDayChoice((current) => current === "all" ? current : null);                       // 하루씩 볼 때는 그 곳이 있는 날로 넘어간다
    setFilter(null);
    setOpen(id);
    if (sheet === "peek") setSheet("half");
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const row = bodyBox.current?.querySelector<HTMLElement>(`[data-entry-id="${id}"]`);
      if (!row) return;
      row.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      row.dataset.attention = "true";
      setTimeout(() => { delete row.dataset.attention; }, 2600);
    }));
    return true;
  }
  const noAlternative = (outcome: AutoResult) => {
    const moved = showFirstNeed();
    return {
      text: t("바꿀 수 있는 대체 일정이 없어요", "No alternative fits"),
      sub: moved ? t("직접 고쳐야 하는 곳으로 옮겼어요 · 이유를 읽고 수정해 주세요", "Moved to what needs your fix · read why and edit it")
        : outcome.kept ? t("고정한 일정이거나 시간이 맞는 후보가 없어요", "Locked, or no alternative fits the time") : undefined,
    };
  };
  /**
   * ★`[2026-10-03]` 「전체 자동 추천」 (the button, and one more push past the end of the list) only SHOWS the plan as it would be: the dry run of the server saves nothing.
   * ★`[2026-10-04]` It stands under the plan as it is, and the list scrolls on into it.
   */
  function recommendAll(source: "button" | "pull" = "button") {
    void run(async () => {
      const outcome = await actions.previewRecommendAll!();
      if (!outcome.changes.length) {
        setNoFix(planKey);
        // ★The button leads to what needs fixing by hand (2026-10-03). Pushing past the end does not: the list stays where it is, and says so.
        if (source === "pull") { nudge(); return null; }
        return noAlternative(outcome);
      }
      setRecommended({ count: outcome.changes.length, was: wasOf(outcome) });
      pageAt.current = bodyBox.current?.scrollTop ?? 0;
      setTurn(skipAnimation ? null : "forward");
      setSide("after");
      return { text: t(`확인이 필요한 일정 ${outcome.changes.length}곳을 바꾼 수정안이에요`, `The proposed plan changes ${outcome.changes.length} stop${outcome.changes.length > 1 ? "s" : ""}`),
        sub: `${changesLine(outcome)} · ${t("아직 저장하지 않았어요 · 위로 올리면 변경 전이에요", "not saved yet · scroll up for the plan as it was")}` };
    }, true);
  }
  const noFixWhy = t("권장 수정안이 없어요", "No recommended fix");
  /**
   * The list shakes a little where it stands and the notice says why - ONCE: another push or press while the notice is up (and a moment after) is ignored,
   * so notices do not pile up and the list does not keep trembling.
   */
  function nudge() {
    if (nudging.current) return;
    nudging.current = true;
    setShake(true);
    setToast({ text: noFixWhy, sub: t("확인이 필요한 곳은 직접 고쳐 주세요", "Fix what needs a look by hand") });
    setTimeout(() => setShake(false), 480);
    setTimeout(() => { nudging.current = false; }, 3100);
  }
  /** Turn to the other page (the dots, the pull past the end of the list, 「계속 내리면」). The way back finds the plan as it was where the customer left it. */
  function flip(next: "before" | "after") {
    if (next === side) return;
    if (side === "before") pageAt.current = bodyBox.current?.scrollTop ?? 0;
    setTurn(skipAnimation ? null : next === "after" ? "forward" : "back");
    setSide(next);
  }
  const turnTo = (next: "before" | "after") => { if (previewing) flip(next); };
  // The page that comes in starts at its top (the proposed plan) or where the customer was (the plan as it was).
  usePageScrollReset(bodyBox, `${side}|${inserting?.key ?? ""}`, () => inserting || side === "after" ? 0 : pageAt.current);
  /** `[2026-10-03 사용자 지시]` The plan's own name (the header says 「내 여행」 until the customer names it): saved as the server's `trip.title`. */
  function renameTrip(value: string) {
    return run(async () => {
      await actions.editTrip!("title", value);
      return { text: t("계획 이름을 바꿨어요", "Renamed the plan"), sub: value };
    }, true);
  }
  /**
   * 「다시 제출」: the stops marked for deletion are taken out first, then the whole plan is checked again; the stops that were changed have their checks ticked in once more.
   */
  function recheck() {
    void run(async () => {
      for (const id of removed) {
        await actions.remove!(id);
        setRemoved((current) => current.filter((entry) => entry !== id));
        setChanged((current) => Object.fromEntries(Object.entries(current).filter(([key]) => key !== id)));
        setSelected((current) => current === id ? null : current);
      }
      const ids = Object.keys(changed).filter((id) => !removedSet.has(id));
      // A server that does not check the whole plan again (no `recheck`) has still taken the stops out above; what the customer changed was checked when it was saved.
      if (actions.recheck) await actions.recheck(ids);
      if (actions.recheck && ids.length && !skipAnimation) setReplay({ ids, at: 0 });
      else setChanged({});
      // `[2026-10-13 사용자 지시 — 수정 모드]` The edit's last button is 「완료」 (the trip's next version), not 「여행 등록」.
      return actions.recheck
        ? { text: t("고친 내용을 다시 확인했어요", "Checked the changes again"), sub: edit ? t("이대로 진행할 수 있어요 · 오른쪽 버튼이 「완료」로 바뀌었어요", "You can go ahead · the right button is now 「Done」") : t("이대로 진행할 수 있어요 · 오른쪽 버튼이 「여행 등록」으로 바뀌었어요", "You can go ahead · the right button is now 「Register trip」") }
        : { text: t("뺄 일정을 지웠어요", "Took the stops out"), sub: t("이대로 진행할 수 있어요", "You can go ahead") };
    });
  }
  /** 「여행 등록」 pressed while looking at the proposed plan: that plan is saved, and then registered. */
  function registerPreview() {
    void run(async () => {
      await settle();
      registration?.onRegister();
      return null;
    }, true);
  }
  function undo() {
    setToast(null);
    void run(async () => { await actions.undo!(); return { text: t("되돌렸어요", "Undone"), sub: t("바꾸기 전으로 돌렸어요", "Back to how it was") }; });
  }
  function undoItem(item: PlanItem) {
    void run(async () => {
      await actions.undoItem!(item.id);
      setChanged(current => Object.fromEntries(Object.entries(current).filter(([id]) => id !== item.id)));
      setOpen(item.id);
      refocus(item.id, "edit");
      return { text: t("이 일정의 변경을 되돌렸어요", "Undid this stop's changes"), sub: t("다른 일정의 변경은 유지했어요", "Changes to other stops were kept") };
    });
  }
  function undoAll() {
    void run(async () => {
      if (!actions.canUndoAll?.()) {
        if (!removed.length) return null;
        setRemoved([]);
        return { text: t("삭제 대기를 취소했어요", "Cancelled pending deletions") };
      }
      await actions.undoAll!();
      setChanged({}); setRemoved([]); setFilter(null); setChangeToolsOpen(false); setOpen(null);
      setRecommended(null); setSide("before"); actions.discardPreview?.();
      requestAnimationFrame(() => checkingBox.current?.querySelector<HTMLButtonElement>(`button.${styles.mapNeeds}`)?.focus());
      return { text: t("변경사항을 모두 되돌렸어요", "All changes undone") };
    }, true);
  }
  useEffect(() => { latest.current = { t, undo }; });

  // The checks of the stops that were changed come in once more, one at a time, after the plan has been checked again.
  const replayQueue = useMemo(() => !replay ? [] : replay.ids.flatMap((id) => {
    const item = view.items.find((entry) => entry.id === id);
    return item ? item.checks.flatMap((row, index) => isInstantRow(row) ? [] : [{ id, index }]) : [];
  }), [replay, view.items]);
  useEffect(() => {
    if (!replay) return;
    const current = replayQueue[replay.at];
    if (current) {
      setOpen(current.id);
      requestAnimationFrame(() => document.getElementById(`plan-card-${current.id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    }
    const timer = setTimeout(() => {
      if (replay.at < replayQueue.length) setReplay({ ...replay, at: replay.at + 1 });
      else { setReplay(null); setChanged({}); }
    }, replay.at < replayQueue.length ? REVEAL_MS : 600);
    return () => clearTimeout(timer);
  }, [replay, replayQueue]);
  const rowsFor = (item: PlanItem): CheckRow[] | null => {
    if (!replay || !replay.ids.includes(item.id)) return null;
    const ticked = new Set(replayQueue.slice(0, replay.at).filter((entry) => entry.id === item.id).map((entry) => entry.index));
    return item.checks.map((row, index) => isInstantRow(row) || ticked.has(index) ? row : { ...row, result: "pending" as const, text: "" });
  };

  /** `[2026-10-03 사용자]` The list can be dragged to any height by its handle (or its head), not only the three presets. */
  const sheetLimits = () => ({ min: SHEET_MIN, max: Math.max(180, (checkingBox.current?.clientHeight ?? 600) - 64) });
  const sheetHeight = () => sheetBox.current?.getBoundingClientRect().height ?? 0;
  function grabSheet(event: PointerEvent<HTMLElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    // ★A press on a button or field inside the head (the counts, …) is that control's: capturing the pointer here sent its click to the head.
    const control = (event.target as HTMLElement).closest("button, a, input, select, textarea");
    if (control && control !== event.currentTarget) return;
    justDragged.current = false;
    grab.current = { y: event.clientY, height: sheetHeight(), moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function dragSheet(event: PointerEvent<HTMLElement>) {
    const held = grab.current;
    if (!held) return;
    const up = held.y - event.clientY;
    if (!held.moved && Math.abs(up) < 6) return;
    held.moved = true;
    if (!dragging) setDragging(true);
    const { min, max } = sheetLimits();
    const height = Math.round(Math.min(max, Math.max(min, held.height + up)));
    dragHeight.current = height;
    lastSheetHeight.current = height;                              // the slide below is for a press of the handle, not for a drag that is already there
    if (dragFrame.current === null) dragFrame.current = requestAnimationFrame(() => {
      dragFrame.current = null;
      if (dragHeight.current !== null) checkingBox.current?.style.setProperty("--sheet-h", `${dragHeight.current}px`);
    });
  }
  function dropSheet() {
    if (grab.current?.moved) {
      justDragged.current = true;                                  // the click that ends a drag is not a press of the handle
      if (dragFrame.current !== null) { cancelAnimationFrame(dragFrame.current); dragFrame.current = null; }
      if (dragHeight.current !== null) setCustom(dragHeight.current);
    }
    grab.current = null;
    dragHeight.current = null;
    setDragging(false);
  }
  function cycleSheet() {
    if (justDragged.current) { justDragged.current = false; return; }
    if (custom !== null) { setCustom(null); setSheet("half"); return; }
    setSheet((current) => SHEETS[(SHEETS.indexOf(current) + 1) % SHEETS.length]);
  }
  function nudgeSheet(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Enter" || event.key === " ") justDragged.current = false;
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const { min, max } = sheetLimits();
    setCustom(Math.round(Math.min(max, Math.max(min, (custom ?? sheetHeight()) + (event.key === "ArrowUp" ? 40 : -40)))));
  }

  // The counts in the head, and the filters they switch.
  const needCount = needsLeft(shown, removedSet).total;
  const changedMap: Record<string, string> = previewing && side === "after" && recommended ? { ...changed, ...recommended.was } : changed;
  const changedCount = Object.keys(changedMap).filter((id) => !removedSet.has(id)).length;
  const firstNeed = shown.items.find((item) => item.verdict === "review" && !removedSet.has(item.id));
  const firstMoveNeed = shown.moves.find((move) => move.verdict === "review" && !removedSet.has(move.fromId) && !removedSet.has(move.toId));
  const changedItem = shown.items.find((item) => changedMap[item.id] && !removedSet.has(item.id));
  const mapSummary = !done || changing || registered ? null
    : firstNeed ? t(`확인 필요 · ${firstNeed.title}`, `Needs a look · ${firstNeed.title}`)
      : firstMoveNeed ? t(`이동 확인 · ${shown.items.find((item) => item.id === firstMoveNeed.fromId)?.title ?? ""} → ${shown.items.find((item) => item.id === firstMoveNeed.toId)?.title ?? ""}`, `Check travel · ${shown.items.find((item) => item.id === firstMoveNeed.fromId)?.title ?? ""} → ${shown.items.find((item) => item.id === firstMoveNeed.toId)?.title ?? ""}`)
        : actions.canUndoAll?.() ? changedItem ? t(`변경됨 · ${changedItem.title}`, `Changed · ${changedItem.title}`) : removed.length > 0 ? t(`삭제한 일정 ${removed.length}곳 · 되돌리기 가능`, `${removed.length} removed stops · can undo`) : t("변경한 내용 · 되돌리기 가능", "Changes · can undo") : null;

  // The filter ends by itself when nothing is left to show.
  const filtering = done && filter !== null && (filter === "needs" ? needCount > 0 : changedCount > 0);
  useEffect(() => {
    if (done && filter && (filter === "needs" ? needCount === 0 : changedCount === 0)) setFilter(null);
    if (changedCount === 0) setChangeToolsOpen(false);
  }, [done, filter, needCount, changedCount]);
  // ★`[2026-10-04 사용자 결정]` With more than one day the list shows one day at a time, with a strip of day chips above it (and a swipe sideways to the next or
  //   the previous day); 「전체」 and the filters show every day as before. The map follows the day.
  const dayStrip = done && !changing && days.length > 0;
  const quietDays = useQuietDayStrip(stripBox, dayStrip, false, bodyBox);
  const submitBox = useRef<HTMLElement>(null);
  const quietSubmit = useQuietDayStrip(submitBox, done && !changing && !inserting, collapsed);
  const atListEnd = useListEnd(bodyBox, done && !changing && !inserting);
  const listDay: number | "all" = !dayStrip || dayChoice === "all" || filtering ? "all" : mapDay;
  const dayAt = days.findIndex((day) => day.day === listDay);
  const needsOfDay = (day: number) => timeline(shown, day).filter((entry) => (entry.type === "item" ? entry.item.verdict : entry.move.verdict) === "review"
    && !(entry.type === "item" ? removedSet.has(entry.item.id) : removedSet.has(entry.move.fromId) || removedSet.has(entry.move.toId))).length;
  function goDay(day: number, animate = true) {
    const to = days.findIndex((entry) => entry.day === day);
    setSlide(animate && dayAt >= 0 && to >= 0 && to !== dayAt ? (to > dayAt ? "next" : "prev") : null);
    setFilter(null);
    setDayChoice(null);
    showDay(day);
    bodyBox.current?.scrollTo({ top: 0 });
  }
  function stepDay(delta: 1 | -1, animate = true) {
    if (dayAt < 0) return;
    const next = days[dayAt + delta];
    if (next) goDay(next.day, animate);
  }
  function chooseAllDays() { setSlide(null); setFilter(null); setDayChoice("all"); bodyBox.current?.scrollTo({ top: 0 }); }
  const neighbourOf = (side: -1 | 1) => (dayAt < 0 ? undefined : days[dayAt + side]);
  const placeMarker = useDayMarker(stripBox, markerBox, listDay === "all" ? 0 : dayAt + 1, `${dayStrip}|${days.length}|${language}|${side}|${compact}|${Boolean(inserting)}`);
  useCenterTools(bodyBox, done && !changing);
  useDayGestures(bodyBox, {
    swipe: dayStrip && !filtering && !inserting,
    hasPrev: dayAt > 0,
    hasNext: dayAt >= 0 && dayAt < days.length - 1,
    track: () => trackBox.current,
    onPeek: (side, top, edge) => setPeek({ side, top, edge }),
    onProgress: (shift, width) => placeMarker(shift, width),
    onTurn: (delta) => stepDay(delta, false),
    overview: listDay === "all",
    onOverviewSwipe: () => {
      // The day whose part of the list is at the top of what shows (the one being looked at).
      const top = bodyBox.current?.getBoundingClientRect().top ?? 0;
      const sections = Array.from(bodyBox.current?.querySelectorAll<HTMLElement>('section[aria-labelledby*="plan-day-"]') ?? []);
      const seen = sections.find((section) => section.getBoundingClientRect().bottom > top + 24) ?? sections[0];
      const day = Number(/plan-day-(\d+)$/.exec(seen?.getAttribute("aria-labelledby") ?? "")?.[1]);
      goDay(Number.isFinite(day) && day > 0 ? day : (days[0]?.day ?? 1), false);
    },
  });

  // Pushing on past the end of the list shows the plan with the recommended fixes under it. ★`[2026-10-04 사용자 지시]` At the end of any day (or of the whole list), not only the last.
  const canRecommend = Boolean(actions.previewRecommendAll) && done && !changing && !registered && !recommended && needsLeft(view, removedSet).total > 0;
  // `[2026-10-05]` The hint is switched off from the start when the dry run (asked for in the background) found nothing to change - not only after the first push.
  useEffect(() => {
    if (!canRecommend || noFixNow || !actions.hasRecommendation) return;
    let live = true;
    let tries = 0;
    const ask = () => void actions.hasRecommendation!().then((has) => {
      if (!live) return;
      if (has === false) setNoFix(planKey);
      else if (has === null && tries++ < 6) setTimeout(ask, 500);           // not read yet: ask again shortly
    }, () => undefined);
    ask();
    return () => { live = false; };
  }, [actions, canRecommend, noFixNow, planKey]);
  // `[2026-10-05]` Past the end of the plan as it was, with a proposal ready: the next page. At the top of the proposed plan: back. (No proposal yet: it is asked for, as before.)
  const pull = usePullPastEnd(bodyBox, {
    onEnd: previewing && side === "before" ? () => flip("after") : canRecommend && !frozen ? (noFixNow ? nudge : () => recommendAll("pull")) : undefined,
    onStart: previewing && side === "after" ? () => flip("before") : undefined,
  });
  const creditMuted = Boolean(slide || turn || swap || peek.side !== 0 || pull.progress > 0);
  // 애니메이션이 없는 설정이나 DOM 교체로 종료 이벤트가 빠져도 출처가 숨은 채 남지 않는다.
  useEffect(() => {
    if (!slide && !turn && !swap) return;
    const timer = setTimeout(() => { setSlide(null); setTurn(null); setSwap(null); }, matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 650);
    return () => clearTimeout(timer);
  }, [slide, turn, swap]);
  // `[2026-10-03 사용자]` While the check is drawn row by row the list follows the newest row (it stayed at the top while rows were added below).
  const follow = useFollowScroll(bodyBox, newestRow, !done && !changing, view, "center");
  const canInsert = Boolean(actions.add) && done && !registered && !changing && !filtering && !frozen && !timeEdit.edit && !removed.length;
  useInsertionPoint(bodyBox, canInsert && !inserting, `${planKey}|${listDay}|${listZoom}|${side}|${previewing}`);
  const closeInsertion = (key: string) => {
    if (addingBusy) return;
    setMapPicking(false); setMapCoordinate(null); setInserting(null); setSlide("prev");
    requestAnimationFrame(() => {
      const button = document.getElementById(`plan-insert-${key}`);
      const box = bodyBox.current;
      if (box && button) dampedScrollTo(box, box.scrollTop + button.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 2 + button.offsetHeight / 2);
      button?.focus({ preventScroll: true });
    });
  };
  const backGesture = useBackgroundBack(Boolean(inserting) && !addingBusy, () => { if (mapPicking) setMapPicking(false); else if (inserting) closeInsertion(inserting.key); });
  const renderInsertion = (point: InsertionPoint) => {
    // `[2026-10-13 사용자 결정 — 수정 모드]` No ＋ in a gap that is over or going on now: after a stop that is over (or before one, at the start of a day).
    const stops = shown.items.filter((item) => item.date === point.date);
    const at = stops.findIndex((item) => item.id === point.anchorId);
    const before = point.side === "before-stop" ? stops[at - 1] : stops[at];
    if (before?.past || (!before && stops[at]?.past)) return null;
    const label = point.side === "before-move"
      ? t(`${point.previous} 다음 이동 전에 일정 추가`, `Add a stop before leaving ${point.previous}`)
      : point.next ? t(`${point.next} 전에 일정 추가`, `Add a stop before ${point.next}`)
        : t(`${point.previous} 다음에 일정 추가`, `Add a stop after ${point.previous}`);
    return <li key={point.key} className={styles.insertPoint} data-insert-point={point.key} data-insert-start={!point.previous || undefined}>
      <button type="button" id={`plan-insert-${point.key}`} className={styles.insertButton} aria-label={label} aria-describedby={`plan-insert-tip-${point.key}`}
        onClick={() => { void run(async () => { setAddingBusy(false); setInserting(point); if (sheet === "peek") setSheet("half"); return null; }); }}>
        <Plus size={18} strokeWidth={1.8} aria-hidden="true" /><span id={`plan-insert-tip-${point.key}`} className={styles.iconTip} role="tooltip">{label}</span>
      </button>
    </li>;
  };
  // ★`[2026-10-06 사용자 지적 — 첫 화면이 왜 애매하게 1일차 마지막 일정에 가 있나]` While the check is drawn the list follows the newest row, so it ends where the last row was drawn - in the middle of day 1.
  //   When the check is done the list goes back to the very top (the first stop of the first day), once.
  const wentTop = useRef(false);
  useEffect(() => {
    if (!done || wentTop.current) return;
    wentTop.current = true;
    if (bodyBox.current) dampedScrollTo(bodyBox.current, 0, 5);
  }, [done]);

  // The change screen's map: the day's other stops greyed, the stop being changed, and its alternatives A, B, C.
  const cards = change ? change.list : [];
  const mapStops: TripStop[] = changing ? changeStops(view, changing, cards) : done ? stopsOf(shown, mapDay)
    : shown.items.map(item => asStop(item.id, item.date, item.startsAt, item.title, item.coordinates));
  const revealingPoint = !done ? mapStops.findLast(stop => stop.coordinates)?.id : undefined;
  const emptyMapInList = done && !changing && !inserting && mapTight && mapStops.length > 0 && mapStops.every(stop => !stop.coordinates);
  useTimeInset(bodyBox, sheetBox, done && !changing && !inserting, `${dayStrip}|${emptyMapInList}|${side}`);
  // ★`[2026-10-07 사용자 지시]` While the list shows only what needs a look, the map does too: those pins in the warning colours, every other pin grey and not pressable.
  const needsOnMap = done && !changing && filtering && filter === "needs";
  const looks: Record<string, PinLook> | undefined = revealingPoint ? { [revealingPoint]: { revealFocus: true } } : changing ? changeLooks(view, changing, cards)
    : needsOnMap ? Object.fromEntries(shown.items.filter((item) => item.day === mapDay).map((item) => [item.id, { tone: item.verdict === "review" && !removed.includes(item.id) ? "warn" as const : "muted" as const }]))
      : removed.length ? Object.fromEntries(removed.map((id) => [id, { tone: "muted" as const }])) : undefined;
  const mapSelected = changing && change ? (change.index === 0 ? changing.id : `cand-${change.index}`) : selected ?? undefined;
  function onPin(id: string) {
    if (changing && change) {
      const index = id === changing.id ? 0 : id.startsWith("cand-") ? Number(id.slice(5)) : -1;
      if (index >= 0) setChange({ ...change, index });
      return;
    }
    if (done) pick(id, "map");
  }
  function onLine(lineId: string) { if (done && !changing) pickLine(lineId); }

  // The lines belong to the plan as it is: for the proposed one only those between stops it did not change are still true (a changed place moves its ends).
  const lineShapes = !routes || changing ? null : previewing && side === "after" && recommended
    ? { ...routes, shapes: routes.shapes.filter((shape) => !(shape.fromItemId in recommended.was) && !(shape.toItemId in recommended.was)) } : routes;
  // The lines of the day on the map, for attribution and source notes.
  const drawn = visibleShapes(lineShapes?.shapes, mapStops);

  const applyWhy = !changing ? null : !actions.replace ? t("장소 바꾸기는 준비 중이에요", "Changing the place is coming")
    : changing.locked ? t("고정한 일정이라 바꿀 수 없어요 · 잠금을 풀면 수정할 수 있어요", "Locked · unlock it to change it") : frozen;

  /** The rows of a list, for the plan as it is and for the proposed one. */
  const visibleIn = (changedIds: ReadonlySet<string>) => (entry: ReturnType<typeof timeline>[number]) =>
    !filtering || (filter === "needs" ? (entry.type === "item" ? entry.item.verdict : entry.move.verdict) === "review" : entry.type === "item" && changedIds.has(entry.item.id));
  /** What the time editor shows, for the list it is open on. */
  const timeUi = (list: "before" | "after"): RowContext["time"] => {
    const { edit, preview } = timeEdit;
    if (!edit || edit.list !== list || !preview) return null;
    return {
      kind: edit.kind, id: edit.id, start: edit.start, end: edit.end, push: edit.push, busy: timeEdit.busy, problem: preview.problem,
      range: { min: formatHm(preview.range.min), max: formatHm(preview.range.max) }, along: preview.along, count: preview.changes.length,
      setStart: timeEdit.setStart, setEnd: timeEdit.setEnd, setPush: timeEdit.setPush, step: timeEdit.step, apply: () => void timeEdit.apply(), cancel: timeEdit.close, grip: timeEdit.grip,
      direct: Boolean(edit.direct),
      undo: (() => {
        const plan = list === "after" && previewView ? previewView : view;
        const item = plan.items.find((entry) => entry.id === edit.id);
        return item && adjustedIn(plan).has(item.id) ? () => { timeEdit.close(); revertTime(item); } : null;
      })(),
    };
  };
  const contextFor = (was: Readonly<Record<string, string>>, list: "before" | "after"): RowContext => ({
    done, open: openAt?.list === list ? openAt.id : null, selected, frozen, actions, explain, rechecking: null, changed: new Set(Object.keys(was)), was, removed: removedSet, rowsFor,
    reveal: done ? undefined : { activeId: readingList.activeId, waiting: readingList.waiting },
    prefix: list === "after" ? "after-" : "",
    onPick: (id) => pick(id, "list", list),
    // ★`[2026-10-07 사용자 지적 — 목록에서 이동을 다시 눌러 닫아도 지도의 선은 골라진 채였다]` The list and the map say the same: opening a leg picks its line on the map, closing it lets the line go.
    onToggleMove: (id) => {
      const closing = openAt?.id === id && openAt.list === list;
      setOpenAt(closing ? null : { list, id });
      const move = shown.moves.find((entry) => entry.id === id);
      const line = move && routes?.shapes.find((entry) => entry.itemId === move.id || (entry.fromItemId === move.fromId && entry.toItemId === move.toId));
      setSelectedLine(closing || !line ? null : line.itemId);
      if (!closing) setSelected(null);
    },
    // ★`[2026-10-07 사용자 지시 — 이동수단 고르기]` The server counts the leg again and answers with the plan; the notice says what was done and 「되돌리기」 takes the way it was (or the recommended one when it was not a single way).
    onSetMode: async (move, choice) => {
      const before = move.modeKey ?? null;
      await actions.setMoveMode!(move.id, choice.mode);
      const back: MoveOptionMode | "recommended" = before === "subway" || before === "bus" || before === "taxi" || before === "walk" ? before : "recommended";
      setToast({
        text: choice.mode === "recommended" ? t("추천 수단으로 돌아갔어요", "Back to the recommended way") : t(`${ro(choice.label)} 바꿨어요`, `Changed to ${choice.label}`),
        sub: t("이 구간만 다시 계산했어요", "Only this leg was counted again"), undo: true,
        revert: () => { void actions.setMoveMode!(move.id, back).catch((error: unknown) => setToast({ text: reason(error), stay: true })); },
      });
    },
    onChange: (item) => void startChange(item),
    onDelete: markRemoved,
    onRestore: restore,
    onLock: lock,
    onRecommend: recommend,
    onUndoItem: list === "before" && actions.undoItem ? undoItem : undefined,
    canUndoItem: (item) => list === "before" && Boolean(actions.undoItem && actions.canUndoItem?.(item.id)),
    onPickReading: actions.pickReading ? pickReading : null,
    time: timeUi(list),
    onOpenTime: (item) => timeEdit.open(item, list),
    timeHandle: (item, kind) => timeEdit.handle(item, list, kind),
    dragEnded: timeEdit.dragEnded,
    onOpenDepart: (fromId) => { const item = (list === "after" && previewView ? previewView : view).items.find((entry) => entry.id === fromId); if (item) timeEdit.open(item, list, "depart"); },
    timeAdjusted: adjustedIn(list === "after" ? previewView : view),
    onRevertTime: revertTime,
    visible: visibleIn(new Set(Object.keys(was))),
  });
  // The plan as it would be with the time being edited (nothing saved): the editor's list is drawn from it, so the gaps move with the fingers.
  const editing = timeEdit.preview && !timeEdit.preview.problem ? timeEdit.preview.changes : [];
  const listBefore = timeEdit.edit?.list === "before" ? withTimes(view, editing) : readingList.view;
  const listAfter = previewView && timeEdit.edit?.list === "after" ? withTimes(previewView, editing) : previewView;
  const contextBefore = contextFor(changed, "before");
  const contextAfter = contextFor({ ...changed, ...(recommended?.was ?? {}) }, "after");
  const heading = (day: PlanDay) => dayHeading(day, language, t);
  const nextDay = typeof listDay === "number" && dayAt >= 0 && dayAt < days.length - 1 && !changing ? days[dayAt + 1] : null;
  const adjustedNow = adjustedIn(previewing && side === "after" ? previewView : sourceView).size;
  // 요청 완료 직후의 rAF는 아직 사라지기 전 변경 버튼을 볼 수 있다. 새 DOM 반영 뒤 초점을 다시 확인한다.
  useLayoutEffect(() => {
    if (!resetFocusPending.current || adjustedNow !== 0) return;
    resetFocusPending.current = false;
    if (document.activeElement !== document.body && !document.activeElement?.classList.contains(styles.changeCount)) return;
    (checkingBox.current?.querySelector<HTMLButtonElement>(`button.${styles.mapNeeds}`)
      ?? checkingBox.current?.querySelector<HTMLButtonElement>(`button.${styles.handle}`))?.focus();
  }, [adjustedNow]);
  const sheetTitle = registered ? t("등록 완료", "Registered") : !done ? t("장소·운영시간 확인", "Places & hours") : previewing && side === "after" ? t("수정안", "Proposed plan") : t("계획 확인", "Plan check");
  const rechecking = view.rechecking ? (() => {
    const order = view.days.flatMap((day) => view.items.filter((item) => item.day === day.day).map((item) => item.id));
    return { at: order.indexOf(view.rechecking) + 1, of: order.length };
  })() : null;

  return <div ref={checkingBox} className={styles.checking} data-sheet={custom !== null ? "custom" : sheet} data-changing={changing ? true : undefined} data-dragging={dragging || undefined} data-compact={compact && done && !changing && !inserting ? true : undefined} data-collapsed={collapsed && done && !changing && !inserting ? true : undefined} data-empty-map-in-list={emptyMapInList || undefined}
    {...quietDays.handlers} {...backGesture}
    style={mapPicking ? { "--sheet-h": "min(320px, 55%)" } as CSSProperties : custom !== null ? { "--sheet-h": `${custom}px` } as CSSProperties : undefined}>
    <div className={styles.map}>
      <TripMap stops={mapStops} dayNumber={mapDay} selectedId={mapSelected} looks={looks} variant="fill" onInteractionChange={setMapMoving} topInset={64} bottomInset={76} routes={lineShapes} onZoom={onMapZoom} onSelect={onPin}
        coordinatePick={mapPicking ? { value: mapCoordinate, onPick: setMapCoordinate } : undefined}
        selectedLineId={selectedLine ?? undefined} onSelectLine={done && !changing ? onLine : undefined} />
    </div>
    {/* `[2026-10-08 사용자 지시]` 확인·변경·장소 표시는 같은 막대에, 이동 수단 이름은 확대된 경로선에 표시한다. */}
    {!mapPicking && (unlocated.length > 0 || pinned || mapSummary || (done && !changing && !registered && (needCount > 0 || changedCount > 0 || removed.length > 0 || actions.canUndoAll?.()))) && <div className={styles.mapChips} data-muted={mapMoving || undefined} inert={mapMoving} aria-hidden={mapMoving || undefined}>
      <div className={styles.statusTools} role="group" aria-label={t("일정 확인과 변경", "Plan checks and changes")}>
      {pinned
        ? <p className={styles.picked} aria-live="polite"><b>{pinned.title}</b>{(pinKindLabel(pinned.kind ?? pinned.info?.kind, t) ?? pinned.info?.category) && <> · {pinKindLabel(pinned.kind ?? pinned.info?.kind, t) ?? pinned.info?.category}</>}</p>
        : mapSummary ? <p className={styles.picked} data-map-summary aria-live="polite">{mapSummary}</p> : unlocated.length > 0 && <p className={styles.unlocated}>{t("위치 미정", "No location")} · {unlocated.map((item) => item.title).join(", ")}</p>}
      {/* ★`[2026-10-07 사용자 지시 — 「! 2」는 지도 쪽에]` The count of what needs a look stands on the map; pressed, the list and the map show only those. */}
      {done && !changing && !registered && needCount > 0 && <button type="button" className={styles.mapNeeds} aria-pressed={filter === "needs" && filtering}
        onClick={() => setFilter(filter === "needs" && filtering ? null : "needs")} aria-label={t(`확인 필요 ${needCount}곳`, `${needCount} to check`)}
        title={filter === "needs" && filtering ? t("눌러서 전체 일정 보기", "Press to show every stop") : t("눌러서 확인이 필요한 곳만 모아 보기", "Press to show only the stops that need a look")}>
        <span className={styles.mark} data-result="warn" aria-hidden="true">!</span><b>{needCount}</b></button>}
      {done && !changing && !registered && changedCount > 0 && <div className={styles.changeTools}>
        <button type="button" className={styles.changeCount} aria-expanded={changeToolsOpen} aria-controls="plan-change-tools"
          aria-describedby="plan-change-count-tip"
          aria-label={t(`바뀐 일정 ${changedCount}곳`, `${changedCount} changed`)}
          onClick={() => { setChangeToolsOpen(!changeToolsOpen); setFilter(changeToolsOpen ? null : "changed"); }}>
          <span className={styles.mark} data-result="filled"><ArrowLeftRight size={13} strokeWidth={1.8} aria-hidden="true" /></span><b>{changedCount}</b>
        </button>
        <span id="plan-change-count-tip" role="tooltip" className={styles.iconTip}>{t(`바뀐 일정 ${changedCount}곳`, `${changedCount} changed`)}</span>
        {changeToolsOpen && <section id="plan-change-tools" className={styles.changePanel} aria-label={t("변경 관리", "Manage changes")}
          onKeyDown={(event) => { if (event.key === "Escape") { setChangeToolsOpen(false); event.currentTarget.parentElement?.querySelector<HTMLButtonElement>("button")?.focus(); } }}>
          <p>{t(`바뀐 일정 ${changedCount}곳을 보고 있어요.`, `Showing ${changedCount} changed stops.`)}</p>
          {adjustedNow > 0 && <button type="button" className={styles.resetTimes} onClick={() => { resetTimes(); setChangeToolsOpen(false); }}
            aria-label={t(`시간 조정 ${adjustedNow}곳 모두 처음으로`, `Put the ${adjustedNow} changed times back`)}>
            <Undo2 size={14} aria-hidden="true" />{t("바꾼 시간 모두 되돌리기", "Undo all time changes")}
          </button>}
          <button type="button" onClick={() => { setFilter(null); setChangeToolsOpen(false); }}>{t("전체 일정 보기", "Show all stops")}</button>
        </section>}
      </div>}
      {done && !changing && !registered && (removed.length > 0 || (actions.undoAll && actions.canUndoAll?.())) && <Act className={styles.undoAll} why={frozen} explain={explain} onPress={undoAll}
        aria-label={t("전체 되돌리기", "Undo all changes")} tooltip={t("전체 되돌리기", "Undo all changes")}><RotateCcw size={17} strokeWidth={1.8} aria-hidden="true" /></Act>}
      </div>
    </div>}
    {changing && <PlaceSearchHeader query={query} onQuery={setQuery} onBack={endChange} backLabel={t("바꾸기 그만두기", "Stop changing")} escapeClears placeholder={t(`${changing.title} 대신 찾을 장소`, `A place instead of ${changing.title}`)} />}
    {!changing && !inserting && headerSlot && createPortal(
      <div className={styles.headInfo} data-title={(done && view.title) ? true : undefined}>
        {done && view.title
          ? <TripTitle title={view.title} onSave={actions.editTrip && !frozen && !registered ? renameTrip : undefined} />
          : <><h1 className="sr-only">{t("계획을 확인하고 있어요", "Checking your plan")}</h1><HeaderProgress view={sourceView.stage === "done" ? sourceView : view} /></>}
      </div>, headerSlot)}
    <section ref={sheetBox} className={styles.sheet} aria-labelledby={inserting ? undefined : changing ? "plan-change-title" : "plan-check-sheet-title"} aria-label={inserting ? t("일정 추가", "Add a stop") : undefined}>
      {done && <button type="button" className={styles.handle} aria-label={t("목록 높이 바꾸기", "Change the list height")} title={t("눌러서 높이를 바꾸고, 잡고 끌어 원하는 높이로 맞춰요", "Press to change the height, or drag it to any height")}
        onClick={cycleSheet} onPointerDown={grabSheet} onPointerMove={dragSheet} onPointerUp={dropSheet} onPointerCancel={dropSheet} onKeyDown={nudgeSheet}><span aria-hidden="true" /></button>}
      {inserting ? <div className={`${styles.change} ${styles.page}`} data-slide="next">
        <AddStop key={inserting.key} date={inserting.date} initial={{ start: inserting.start, end: inserting.end }} blocked={frozen} onBusy={setAddingBusy} onCancel={() => closeInsertion(inserting.key)}
          mapPicker={{ active: mapPicking, value: mapCoordinate, onStart: () => { setMapCoordinate(null); setMapPicking(true); }, onClose: () => setMapPicking(false) }}
          search={actions.searchToAdd ? (query) => actions.searchToAdd!(inserting.anchorId, query) : undefined}
          onSave={async (draft) => { await actions.add!(draft); setAddingBusy(false); setInserting(null); setSlide("prev"); setToast({ text: t("일정을 추가하고 앞뒤 이동시간을 다시 확인했어요.", "Added the stop and checked travel times around it.") }); }} />
      </div> : changing && change
        ? <PlaceChange item={changing} session={change} search={search} explain={explain} applyWhy={applyWhy} candidatesSupported={Boolean(actions.candidates)}
            onIndex={(index) => setChange({ ...change, index })} onApply={(candidate) => replace(changing, { candidate })} onApplyName={(name) => replace(changing, { name })}
            onPick={(candidate) => {
              const at = change.list.findIndex((entry) => entry.id === candidate.id);
              const list = at >= 0 ? change.list : [{ ...candidate, source: "search" as const }, ...change.list];
              setChange({ ...change, list, index: at >= 0 ? at + 1 : 1 }); setQuery("");
            }}
            onSheetFull={() => { setCustom(null); setSheet("full"); }}
            editor={actions.edit && <details className={styles.details} open={details} onToggle={(event) => setDetails(event.currentTarget.open)}>
              <summary>{t("직접 고치기 · 이름·날짜·시각·장소 없음", "Edit directly · name, date, time, no place")}</summary>
              {details && <StopEditor key={changing.id} item={changing} autoFocus={false} onCancel={() => setDetails(false)}
                onSave={async (draft) => {
                  const was = wasText(changing);
                  if (previewing && side === "after") await settle();
                  await actions.edit!(changing.id, draft);
                  markChanged(changing.id, was);
                  leaveRecommended();
                  setChange(null); setDetails(false); setOpen(changing.id); refocus(changing.id, "edit");
                  setToast({ text: t("저장했어요. 서버가 일정을 다시 확인했어요.", "Saved. The server checked the plan again.") });
                }} />}
            </details>} />
        : <>
          <header className={styles.sheetHead} data-done={done || undefined} data-float={done && dayStrip ? true : undefined} onPointerDown={grabSheet} onPointerMove={dragSheet} onPointerUp={dropSheet} onPointerCancel={dropSheet}>
            <h2 id="plan-check-sheet-title" className={done || dayStrip ? "sr-only" : styles.sheetTitle}>{sheetTitle}</h2>
            {dayStrip && <div ref={stripBox} className={styles.dayStrip} role="tablist" aria-label={t("일차 고르기", "Choose a day")}
              data-at-top={quietDays.atTop || undefined} data-quiet={quietDays.hidden || undefined} aria-hidden={quietDays.hidden || undefined} inert={quietDays.hidden} {...quietDays.focusHandlers}
              onKeyDown={(event) => {
                if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
                const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'));
                const at = tabs.indexOf(document.activeElement as HTMLElement);
                const next = tabs[at + (event.key === "ArrowRight" ? 1 : -1)];
                if (next) { event.preventDefault(); next.focus(); next.click(); }
              }}>
              <span ref={markerBox} className={styles.dayMarker} aria-hidden="true" />
              <button type="button" role="tab" className={styles.dayChip} aria-selected={listDay === "all"} tabIndex={listDay === "all" ? 0 : -1} onClick={chooseAllDays}>{t("전체", "All")}</button>
              {days.map((day) => {
                const wait = needsOfDay(day.day);
                return <button key={day.day} type="button" role="tab" className={styles.dayChip} aria-selected={listDay === day.day} tabIndex={listDay === day.day ? 0 : -1} onClick={() => goDay(day.day)}>
                  {t(`${day.day}일차`, `Day ${day.day}`)}<small>{day.date ? dayLabel(day.date, language) : ""}</small>
                  {wait > 0 && <span className={styles.dayBadge} role="img" aria-label={t(`확인 필요 ${wait}곳`, `${wait} to check`)}>{wait}</span>}
                </button>;
              })}
            </div>}

            {edit && done
              // `[2026-10-13 사용자 지시 — 수정 모드]` 「취소」 in the trip screen's chat-button place (the round ✓ saves, as on the plan check): it lets the edit go, after asking.
              ? <div className={tripStyles.headTools}><button type="button" className={tripStyles.chatButton} aria-disabled={registration?.busy || undefined}
                  onClick={() => { if (!registration?.busy) edit.onCancel(); }}><X size={15} strokeWidth={2.2} aria-hidden="true" />{t("취소", "Cancel")}</button></div>
              : <p className={styles.count}>{done
                ? (registered || rechecking ? <HeadBadges needs={needCount} changed={0} filter={null} onFilter={setFilter} registered={registered} rechecking={rechecking} needsOnMap /> : null)
                : countText(view, t)}</p>}
          </header>
          <div ref={bodyBox} className={styles.sheetBody} data-next-preview={(canRecommend || (previewing && side === "before")) && !noFixNow || undefined} data-under-float={done && dayStrip ? true : undefined} {...quietSubmit.handlers} {...follow.handlers} onScroll={follow.handlers.onScroll}>
            {emptyMapInList && <p className={styles.emptyMapNotice} role="status" data-empty-map-notice>{t("표시할 장소 좌표가 없어요.", "There are no place coordinates to display.")}<span>{t("일정에서 장소를 수정해 주세요.", "Edit the places in your stops.")}</span></p>}
            {done && !registered && !previewing && !view.items.length && actions.add && <AddStop date={days[0]?.date ?? ""} blocked={frozen} onSave={actions.add} />}
            {done && tripIssues.length > 0 && <TripIssues issues={tripIssues} onSave={actions.editTrip} />}
            {done && !registered && !previewing && view.taxiPricing && actions.editTrip &&
              <TaxiPricing value={view.taxiPricing} locked={frozen} onSave={(value) => actions.editTrip!("taxi_pricing", value)} />}
            {filtering && <p className={styles.filterNote} role="status">{filter === "changed" ? t("바뀐 곳만 보는 중이에요", "Showing only what changed") : t("확인이 필요한 곳만 보는 중이에요", "Showing only what needs a look")}
              <button type="button" onClick={() => setFilter(null)}>{t("전체 보기", "Show all")}</button></p>}
            {(() => {
              const proposed = previewing && side === "after" && previewView;
              // `[2026-10-15 사용자 결정 — ST4F-494]` 「전체 자동 추천」(잠긴 일정이 있으면 「전체 잠금 해제」도)은 목록 위 따로 한 줄이 아니라 첫 날짜 제목 줄의 오른쪽 끝에 선다.
              const tools = done && !registered && !previewing ? <>
                <Act id="plan-recommend-all" className={styles.autoAllTop} why={frozen ?? (!actions.previewRecommendAll ? t("전체 자동 추천은 준비 중이에요", "Recommending all is coming") : null)} explain={explain} onPress={() => recommendAll("button")}>
                  <Sparkles size={15} aria-hidden="true" />{t("전체 자동 추천", "Recommend all")}{needCount > 0 && <span className={styles.badge}>{needCount}</span>}
                </Act>
                {view.items.some((item) => item.locked) && <Act className={styles.unlockAllTop} why={frozen ?? (!actions.unlockAll ? t("전체 잠금 해제는 준비 중이에요", "Unlocking all is coming") : null)} explain={explain} onPress={unlockAll}>
                  <LockOpen size={15} aria-hidden="true" />{t("전체 잠금 해제", "Unlock all")}
                </Act>}
              </> : null;
              /** A day of the list as the page shows it (also drawn beside the page while it is dragged). */
              const dayList = (which: number | "all", insert = true) => proposed
                ? <DayList view={listAfter ?? previewView} days={previewView.days.filter((day) => previewView.items.some((item) => item.day === day.day))} listDay={which} ctx={{ ...contextAfter, insertion: insert && canInsert ? renderInsertion : undefined }} mapDay={mapDay} onShowDay={showDay} heading={heading} now={edit?.now} />
                : <DayList view={listBefore} days={days} listDay={which} ctx={{ ...contextBefore, insertion: insert && canInsert ? renderInsertion : undefined }} mapDay={mapDay} onShowDay={showDay} heading={heading} now={edit?.now} tools={insert ? tools : undefined} />;
              const near = peek.side === 0 ? undefined : neighbourOf(peek.side);
              const zoomStyle = listZoom === 1 ? undefined : { zoom: listZoom };
              return <div ref={trackBox} className={styles.dayTrack}>
                {near && <div className={styles.peek} data-side={peek.side} style={{ top: peek.top, ...zoomStyle }} aria-hidden="true" inert>
                  <span className={styles.peekBadge}>{t(`놓으면 ${near.day}일차`, `Let go for day ${near.day}`)}</span>
                  {dayList(near.day, false)}
                </div>}
                {peek.side !== 0 && peek.edge && <p className={styles.edgeNote} data-side={peek.side} style={{ top: peek.top + 28 }} aria-hidden="true">{peek.side === 1 ? t("마지막 날이에요", "Last day") : t("첫날이에요", "First day")}</p>}
                <div ref={zoomBox} className={styles.dayZoom} data-swap={swap ?? undefined} style={zoomStyle} onAnimationEnd={(event) => { if (event.target === event.currentTarget) setSwap(null); }}>
              <div key={`${proposed ? "after" : "before"}:${String(listDay)}`} className={styles.page} data-slide={slide ?? undefined} data-proposed={proposed ? true : undefined} data-turn={turn ?? undefined}
                data-pull={pull.edge ?? undefined} data-shake={shake || undefined} style={{ "--pull": pull.progress } as CSSProperties} onAnimationEnd={(event) => { if (event.target === event.currentTarget) { setTurn(null); setSlide(null); } }}>
                {proposed && <PreviewHint direction="up" text={t("계속 올리면 변경 전 일정이에요", "Keep going up for the plan as it was")} why={frozen} explain={explain} onPress={() => flip("before")} edge={pull.edge ?? undefined} progress={pull.edge === "start" ? pull.progress : 0} />}
                {dayList(listDay)}
              </div>
                </div>
              </div>;
            })()}
            {/* ★`[2026-10-03 사용자]` 출처 줄이 「계속 내리면 …」 안내 위에 있어야, 목록 맨 끝에 그 안내가 보여 「내리면 다음이 나온다」가 읽힌다. */}
            {done && view.items.length > 0 && <details className={styles.credit} data-muted={creditMuted || undefined} inert={creditMuted} aria-hidden={creditMuted || undefined}>
              <summary aria-label={t("출처와 경로 안내 펼치기", "Show sources and route notes")}><span>{t("ⓒ한국관광공사", "ⓒKorea Tourism Organization")}{drawn.length > 0 && lineShapes?.attribution && ` · ${lineShapes.attribution.includes("OpenStreetMap") ? "© OpenStreetMap" : lineShapes.attribution}`}</span><ChevronDown size={14} strokeWidth={1.8} aria-hidden="true" /></summary>
              <div className={styles.creditDetails}><p>{t("장소 정보 출처 : ⓒ한국관광공사 · ", "Place data: ⓒKorea Tourism Organization · ")}<a href={TOUR_API_POLICY_URL} target="_blank" rel="noreferrer">{t("저작권 정책", "Copyright policy")}</a></p>
                {drawn.length > 0 && <p>{lineShapes?.attribution}{routeNotes(drawn).map((note) => <span key={note} data-route-note>{note}</span>)}</p>}
              </div></details>}
            {nextDay && <p className={styles.dayHint}>{t(`옆으로 밀면 ${nextDay.day}일차${nextDay.date ? ` ${dayLabel(nextDay.date, language)}` : ""} 일정이 나와요`, `Swipe sideways for day ${nextDay.day}`)}<ChevronsRight size={15} strokeWidth={1.8} aria-hidden="true" /></p>}
            {(canRecommend || (previewing && side === "before")) && (canRecommend && !previewing && noFixNow
              ? <Act className={styles.pullEmpty} why={frozen ?? noFixWhy} explain={frozen ? explain : nudge} onPress={nudge}>
                <ChevronsDown size={16} strokeWidth={1.8} aria-hidden="true" />
                <span>{t("권장 수정안이 없어서 더 보여 드릴 게 없어요", "No recommended fix, so there is nothing more to show")}</span>
              </Act>
              : <PreviewHint direction="down" text={previewing ? t("아래로 스크롤하면 수정안이 나와요", "Scroll down for the proposed plan") : t("아래로 스크롤하면 권장 수정안이 나와요", "Scroll down for the recommended plan")} why={frozen} explain={explain} onPress={previewing ? () => flip("after") : () => recommendAll("button")} edge={pull.edge ?? undefined} progress={pull.progress} />)}
            {done && !view.items.length && <p className={styles.empty}>{t("남은 일정이 없어요", "No stops left")}</p>}
            {!done && !follow.following && <button type="button" className={styles.followPill} onClick={follow.resume}><ChevronsDown size={14} strokeWidth={1.8} aria-hidden="true" />{t("확인 중인 곳으로", "Follow the check")}</button>}
          </div>
          {done && previewing && <div className={styles.pager} role="group" aria-label={t("보는 일정", "Plan shown")}>
            <button type="button" className={styles.pagerDot} aria-current={side === "before" ? "step" : undefined} aria-label={t("1. 변경 전 일정", "1. The plan as it was")} onClick={() => turnTo("before")}><span aria-hidden="true" /></button>
            <button type="button" className={styles.pagerDot} aria-current={side === "after" ? "step" : undefined} aria-label={t("2. 수정안", "2. The proposed plan")} onClick={() => turnTo("after")}><span aria-hidden="true" /></button>
          </div>}

        </>}
    </section>
          {/* `[2026-10-14 실서버 확인]` 시간 고치기 창이 열려 있으면 숨긴다 — 창의 ✓ 「적용」이 아래쪽에 오면 같은 초록 ✓ 인 이 단추가 그 위를 덮어,
              「적용」을 누른 손님이 「완료」(수정 모드면 아무것도 쓰지 않고 닫힘)를 눌렀다. 창을 닫으면(적용 · 취소) 다시 나온다. */}
          {done && !changing && !inserting && !timeEdit.edit && <ResultFooter footerRef={submitBox} atListEnd={atListEnd} muted={collapsed || (!atListEnd && quietSubmit.hidden) || mapMoving} focusHandlers={quietSubmit.focusHandlers} view={shown} registration={registration} frozen={frozen} explain={explain} needsTotal={needCount} pendingRemovals={removed.length}
            previewing={previewing && side === "after"} onRecheck={actions.recheck || actions.remove ? recheck : undefined} onRegisterPreview={registerPreview} editing={Boolean(edit)} />}
    {timeEdit.drag && <TimeDragOverlay drag={timeEdit.drag} />}
    {shownToast && <ToastView key={shownToast.stamp} toast={shownToast.toast} onDone={hideToast} />}
  </div>;
}

function dayHeading(day: { day: number; date: string }, language: Language, t: Translate) {
  if (day.day === 0) return <>{t("날짜 미정", "Date to be set")}</>;
  return <>{t(`${day.day}일차`, `Day ${day.day}`)}<small>{day.date ? dayLabel(day.date, language) : t("날짜 미정", "date to be set")}</small></>;
}

function countText(view: PlanCheckView, t: Translate): string {
  const count = tally(view);
  if (view.stage !== "done") return t(`장소 ${count.placesDone}/${count.places} · 이동 ${count.movesDone}/${count.moves}`, `Places ${count.placesDone}/${count.places} · Moves ${count.movesDone}/${count.moves}`);
  const left = needs(view);
  if (!left.total) return t("모두 확인했어요", "All checked");
  const parts = [left.places && t(`장소 ${left.places}곳`, `${left.places} place${left.places > 1 ? "s" : ""}`),
    left.moves && t(`이동 ${left.moves}구간`, `${left.moves} move${left.moves > 1 ? "s" : ""}`)].filter(Boolean);
  return t(`${parts.join(" · ")} 확인 필요`, `To check: ${parts.join(" · ")}`);
}

function announce(view: PlanCheckView, t: Translate): string {
  if (view.stage === "received") return t("계획을 받았어요.", "Plan received.");
  if (view.stage === "reading") return t("일정을 읽고 있어요.", "Reading your plan.");
  if (view.stage === "checking") return t("장소와 운영시간을 확인하고 있어요.", "Checking places and opening hours.");
  return t(`확인을 마쳤어요. ${countText(view, t)}.`, `Check finished. ${countText(view, t)}.`);
}

const monthDay = (date: string) => `${date.slice(5, 7)}.${date.slice(8, 10)}`;
const weekday = (date: string, language: Language) =>
  new Intl.DateTimeFormat(language === "ko" ? "ko-KR" : "en-US", { weekday: "short", timeZone: "Asia/Seoul" }).format(new Date(`${date}T12:00:00+09:00`));

function dayLabel(date: string, language: Language): string {
  return `${monthDay(date)} ${weekday(date, language)}`;
}

const asStop = (id: string, date: string, time: string, title: string, coordinates: TripStop["coordinates"]): TripStop =>
  ({ id, date, time, title, booking: "unknown", notes: "", coordinates });

/** The map's stops for one day. Places without coordinates are passed too: the map says they have no pin. */
function stopsOf(view: PlanCheckView, day: number): TripStop[] {
  const date = view.days.find((entry) => entry.day === day)?.date ?? "";
  return view.items.filter((item) => item.day === day).map((item) => asStop(item.id, date, item.startsAt, item.title, item.coordinates));
}

/** The change screen's map (mockup renderEditMap): the day's stops, then the alternatives as `cand-1`, `cand-2` … */
function changeStops(view: PlanCheckView, item: PlanItem, list: PlanCandidate[]): TripStop[] {
  return [...stopsOf(view, item.day), ...list.map((candidate, at) => asStop(`cand-${at + 1}`, item.date, "", candidate.name, candidate.coordinates))];
}

function changeLooks(view: PlanCheckView, item: PlanItem, list: PlanCandidate[]): Record<string, PinLook> {
  const looks: Record<string, PinLook> = {};
  view.items.filter((entry) => entry.day === item.day).forEach((entry, at) => { looks[entry.id] = { label: String(at + 1), tone: entry.id === item.id ? "current" : "muted" }; });
  list.forEach((_, at) => { looks[`cand-${at + 1}`] = { label: letter(at + 1), tone: "candidate" }; });
  return looks;
}
