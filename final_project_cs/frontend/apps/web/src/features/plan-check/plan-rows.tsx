"use client";

import { BedDouble, Bike, Bus, Car, Check, ChevronsUpDown, Footprints, Lock, LockOpen, Minus, Pencil, Plus, Sparkles, Trash2, TrainFront, Undo2, X } from "lucide-react";
import { Fragment, type MouseEvent, type ReactNode } from "react";
import type { MoveOptionMode } from "@/lib/live/move-options";
import { ro } from "@/lib/josa";
import { useT } from "@/lib/settings";
import { dayTimes, freeText, moveSlack } from "./day-times";
import { timeline, type CheckRow, type PlanCheckView, type PlanDay, type PlanItem, type PlanMove, type Reread } from "./model";
import { MoveModeBox } from "./move-mode";
import { Act, Checks, NeedsLine, needsOf, VerdictMark } from "./parts";
import type { PlanCheckActions } from "./plan-check";
import { insertionPoints, type InsertionPoint } from "./insertion-points";
import { RailList, type RailNow } from "./rail-list";
import { StopKind } from "./stop-kind";
import type { Grab, TimeEditHandles, TimeKind } from "./use-time-edit";
import styles from "./plan-check.module.css";

/** Everything a row of the list needs from the screen, in one place (the list is drawn twice when a preview stands under the plan). */
export interface RowContext {
  revision?: number;
  done: boolean;
  /** Only the currently revealed check opens; future read stops remain quiet and folded. */
  reveal?: { activeId: string | null; waiting: ReadonlySet<string> };
  /** The card or move that is open, and the stop picked on the map or in the list. */
  open: string | null;
  selected: string | null;
  frozen: string | null;
  actions: PlanCheckActions;
  explain: (why: string) => void;
  /** The stop being checked again just now (the recheck ticker). */
  rechecking: string | null;
  /** Stops changed since the last check, and what each had been (「바뀜 · 이전 …」). */
  changed: ReadonlySet<string>;
  was: Readonly<Record<string, string>>;
  /** Stops marked for deletion: grey, with 「되돌리기」 where the bin is; they are really taken out when the plan is checked again. */
  removed: ReadonlySet<string>;
  /** The checks to show for a stop while they are being ticked in again (null = the stop's own). */
  rowsFor: (item: PlanItem) => CheckRow[] | null;
  onPick: (id: string) => void;
  onToggleMove: (id: string) => void;
  /** `[2026-10-07]` The customer picked a way for a leg (`mode`, and its name for the notice): the screen sends it and says what was done, with 「되돌리기」. Rejects with the server's refusal. */
  onSetMode: (move: PlanMove, choice: { mode: MoveOptionMode | "recommended"; label: string }) => Promise<void>;
  onChange: (item: PlanItem) => void;
  onDelete: (item: PlanItem) => void;
  onRestore: (item: PlanItem) => void;
  onLock: (item: PlanItem) => void;
  onRecommend: (item: PlanItem) => void;
  onUndoItem?: (item: PlanItem) => void;
  canUndoItem?: (item: PlanItem) => boolean;
  /** `[2026-10-07]` Keep one of two readings of a photo's line (null = the page cannot send it: the buttons are not drawn, the row's note still says it). */
  onPickReading: ((item: PlanItem, reread: Reread, value: string) => void) | null;
  /**
   * ★`[2026-10-04 사용자 지시]` The time at the left of a stop is pressed to change it. `time` is the editor when it is open on a stop of THIS list (null otherwise); the stops whose
   * time is not the one they had when the screen opened have a 「되돌리기」 beside their state.
   */
  time: TimeEditUi | null;
  onOpenTime: (item: PlanItem) => void;
  /**
   * `[2026-10-05 사용자 선택 — 시간 조정 합친 안]` The time of a stop (or of leaving it) and its dot are taken and dragged at once - no tap first (`kind` says which). `dragEnded` is true once, for the click
   * that ends such a drag: it is not a tap, so it does not open the form.
   */
  timeHandle: (item: PlanItem, kind: TimeKind) => Grab;
  dragEnded: () => boolean;
  /** `[2026-10-05]` The time of LEAVING a stop (the time at the left of the leg after it) is pressed: its end changes (free time first, then the next stops are pushed). Takes the id of that stop. */
  onOpenDepart: (fromId: string) => void;
  timeAdjusted: ReadonlySet<string>;
  onRevertTime: (item: PlanItem) => void;
  /** `""` for the plan as it is, `"after-"` for the proposed one: the same stop stands in both lists, and an id is one element's. */
  prefix: string;
  /** Which entries to draw (the 「확인 필요」 and 「바뀜」 filters). */
  visible: (entry: ReturnType<typeof timeline>[number]) => boolean;
  insertion?: (point: InsertionPoint) => ReactNode;
}

/** What the time editor of one stop shows and does (built from `useTimeEdit` by the screen). */
export interface TimeEditUi {
  /** What is changed: the start of the stop, or the time of leaving it (its end). */
  kind: TimeKind;
  id: string; start: string; end: string; push: boolean; busy: boolean; problem: string;
  /** The earliest and latest start the switch allows, as 「HH:MM」. */
  range: { min: string; max: string };
  /** The other stops that move along, and by how many minutes (negative = earlier). */
  along: { title: string; minutes: number }[];
  /** How many stops change in all. */
  count: number;
  setStart: (text: string) => void; setEnd: (text: string) => void; setPush: (push: boolean) => void;
  step: (minutes: number) => void; apply: () => void; cancel: () => void;
  grip: TimeEditHandles["grip"];
  /** Dragged straight from the time: the form stays shut while it is held. */
  direct: boolean;
  /** Put the time back to what it was when the screen opened - null when it was never changed. */
  undo: (() => void) | null;
}

/** The days of a view, each with its timeline: stops (cards) and the moves between them. */
export function DayList({ view, days, listDay, ctx, mapDay, onShowDay, heading, now, tools }: {
  view: PlanCheckView;
  days: PlanDay[];
  listDay: number | "all";
  ctx: RowContext;
  mapDay: number;
  onShowDay: (day: number) => void;
  heading: (day: PlanDay) => ReactNode;
  /** `[2026-10-13 사용자 결정 — 수정 모드]` The trip screen's 「지금」 on a day's line (the edit of a registered trip); none on the plan check of a new plan. */
  now?: (date: string) => RailNow | null;
  /** `[2026-10-15 사용자 결정 — ST4F-494]` Buttons at the right end of the first day's heading (the plan check's 「전체 자동 추천」 · 「전체 잠금 해제」 — a row of their own above the list before). */
  tools?: ReactNode;
}) {
  const t = useT();
  return <>{days.filter((day) => (listDay === "all" || day.day === listDay) && timeline(view, day.day).some(ctx.visible)).map((day, at) => {
    const title = <h3 id={`${ctx.prefix}plan-day-${day.day}`} className={styles.day}>{ctx.done
        ? <button type="button" className={styles.dayButton} aria-pressed={mapDay === day.day} onClick={() => onShowDay(day.day)}>{heading(day)}</button>
        : heading(day)}</h3>;
    return <section key={day.day} aria-labelledby={`${ctx.prefix}plan-day-${day.day}`}>
      {at === 0 && tools ? <div className={styles.dayRow}>{title}<div className={styles.dayTools}>{tools}</div></div> : title}
      <RailList className={styles.timeline} now={now?.(day.date)} toScale>{(() => {
        const times = dayTimes(view, day.day);
        const points = ctx.insertion ? insertionPoints(view, day) : [];
        // `[2026-10-13 사용자 지시]` A day the customer wakes up in a stay: 「○○에서 출발」 at the top (the move from it follows, when the check found one).
        const stay = view.items.find((item) => isStayItem(item) && item.date < day.date && day.date <= item.endDate!);
        return <>{stay && <li className={styles.stayBand} data-entry-id={`${stay.id}:from`}>
          <BedDouble size={16} strokeWidth={1.8} aria-hidden="true" />
          <span>{t(`${stay.title}에서 출발`, `Leaving from ${stay.title}`)}</span>
          <small>{day.date === stay.endDate ? t("마지막 날", "Last day") : t(`${nightOf(stay, day.date)}박째`, `Night ${nightOf(stay, day.date)}`)}</small>
        </li>}{timeline(view, day.day).filter(ctx.visible).map((entry, index) => <Fragment key={entry.type === "item" ? entry.item.id : entry.move.id}>
          {points[index] && ctx.insertion?.(points[index])}
          {entry.type === "item"
            ? entry.item.past ? <PastItemRow item={entry.item} ctx={ctx} /> : <ItemRow item={entry.item} ctx={ctx} rechecking={view.rechecking === entry.item.id || ctx.rechecking === entry.item.id} />
            : <MoveRow move={entry.move} from={view.items.find((item) => item.id === entry.move.fromId)} to={view.items.find((item) => item.id === entry.move.toId)} ctx={{ ...ctx, revision: view.revision }} slack={moveSlack(times, entry.move)} dim={ctx.removed.has(entry.move.fromId) || ctx.removed.has(entry.move.toId)} />}
        </Fragment>)}{points.at(-1) && ctx.insertion?.(points.at(-1)!)}</>;
      })()}</RailList>
    </section>;
  })}</>;
}

/**
 * One stop's card: the lock, the name (opens and closes the card), its state and recommend/edit/delete icons; inside, its checks. A tool that cannot act now stays and says why (locked, being checked, not connected yet).
 *
 * 2026-10-08: 제목 옆에는 바뀐 일정의 체크만 둔다. 확인 필요와 시간 되돌리기는 카드 하단 오른쪽에 놓는다.
 * A stop marked for deletion is grey and has Undo where the bin is.
 */
/** `[2026-10-13 사용자 결정 — 숙소 항목 B안]` A stay (kind lodging with a later end day): 「○○ 체크인」 and its nights 「3박 · 11/6~11/9」. Other stops keep their title. */
const isStayItem = (item: PlanItem) => item.kind === "lodging" && !!item.endDate && item.endDate > item.date;
/** Which night of the stay the morning of `date` follows (the day after check-in is night 2 …, as the trip screen says it). */
const nightOf = (stay: PlanItem, date: string) => Math.round((Date.parse(`${date}T00:00:00Z`) - Date.parse(`${stay.date}T00:00:00Z`)) / 86_400_000) + 1;
const monthDay = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
function stayTitle(item: PlanItem, t: ReturnType<typeof useT>): string {
  return isStayItem(item) ? t(`${item.title} 체크인`, `Check in · ${item.title}`) : item.title;
}
function stayPillOf(item: PlanItem, t: ReturnType<typeof useT>): string | null {
  if (!isStayItem(item)) return null;
  const nights = Math.round((Date.parse(`${item.endDate}T00:00:00Z`) - Date.parse(`${item.date}T00:00:00Z`)) / 86_400_000);
  return t(`${nights}박 · ${monthDay(item.date)}~${monthDay(item.endDate!)}`, `${nights} night${nights === 1 ? "" : "s"} · ${monthDay(item.date)}–${monthDay(item.endDate!)}`);
}

function ItemRow({ item, ctx, rechecking }: { item: PlanItem; ctx: RowContext; rechecking: boolean }) {
  const t = useT();
  const { done, actions, explain, frozen } = ctx;
  const current = !done && ctx.reveal?.activeId === item.id;
  const waiting = !done && Boolean(ctx.reveal?.waiting.has(item.id));
  const open = done ? ctx.open === item.id || item.verdict === null : current;
  const selected = done && ctx.selected === item.id;
  const timeFocused = done && (ctx.time ? ctx.time.id === item.id && ctx.time.kind === "start" : selected || ctx.open === item.id);
  const checking = item.verdict === null;
  const review = item.verdict === "review";
  const removed = ctx.removed.has(item.id);
  const changed = ctx.changed.has(item.id);
  const changedFrom = ctx.was[item.id];
  const lockedWhy = t("고정한 일정이라 바꿀 수 없어요 · 잠금을 풀면 수정할 수 있어요", "Locked · unlock it to change it");
  const removedWhy = t("삭제할 일정이에요 · 「되돌리기」를 누르면 다시 쓸 수 있어요", "Marked for deletion · press Undo to keep it");
  const lockWhy = removed ? removedWhy : !actions.lock ? t("잠금은 준비 중이에요", "Locking is coming") : review && !item.locked ? t("확인이 필요한 일정은 먼저 고쳐야 고정할 수 있어요", "Fix this stop before locking it") : frozen;
  const changeWhy = removed ? removedWhy : !actions.replace && !actions.edit ? t("수정은 준비 중이에요", "Editing is coming") : item.locked ? lockedWhy : frozen;
  const autoWhy = removed ? removedWhy : !actions.autoRecommend ? t("자동 추천은 준비 중이에요", "Recommending is coming") : item.locked ? lockedWhy : frozen;
  const deleteWhy = !actions.remove ? t("삭제는 준비 중이에요", "Deleting is coming") : item.locked ? t("고정한 일정은 삭제할 수 없어요 · 잠금을 먼저 풀어 주세요", "Locked stops cannot be deleted · unlock it first") : frozen;
  const lockLabel = item.locked ? t(`${item.title} 고정 풀기`, `Unlock ${item.title}`) : review ? t("확인이 필요한 일정은 고정할 수 없어요", "Stops that need a look cannot be locked") : t(`${item.title} 꼭 넣을 일정으로 고정`, `Lock ${item.title} in`);
  // Being checked again (after a change, or in a full re-check): a waiting label in place of the state.
  const status: ReactNode = done && (checking || rechecking)
    ? <span className={styles.pillWait}><span className={styles.spinner} aria-hidden="true" />{rechecking ? t("재검증", "Checking") : t("확인 중", "Checking")}</span>
    : checking && !waiting && current ? <span className={styles.spinner} role="img" aria-label={t("확인하는 중", "Checking")} />
      : removed ? <span className={styles.pill} data-state="removed">{t("삭제 예정", "To delete")}</span>
        : null;
  const changedMark = changed && !removed && !checking && !rechecking
    ? <span className={styles.changedMark} role="img" aria-label={t("변경 완료", "Changed")} title={t("변경 완료", "Changed")}><Check size={16} strokeWidth={2} aria-hidden="true" /></span> : null;
  const canUndoItem = Boolean(ctx.onUndoItem && ctx.canUndoItem?.(item));
  const showRecommend = !removed && !changed && !canUndoItem;
  const note = !showRecommend ? null : item.locked ? lockedWhy
    : !actions.autoRecommend ? t("자동 추천은 준비 중이에요 · 수정에서 장소를 바꿀 수 있어요", "Recommending is coming · change the place in Edit")
      : item.suggestion ? t(`자동 추천은 1순위 ${ro(item.suggestion)} 바로 바꿔요`, `Recommend changes it to the first alternative, ${item.suggestion}`) : null;
  const rows = ctx.rowsFor(item) ?? item.checks;
  const timeWhy = removed ? removedWhy : !actions.retime && !actions.edit ? t("시간 고치기는 준비 중이에요", "Changing the time is coming") : item.locked ? lockedWhy : item.booked === true ? t("예약한 일정이라 시간을 바꿀 수 없어요", "Booked: its time cannot be changed") : frozen;
  const adjusted = ctx.timeAdjusted.has(item.id) && !removed;
  const showUndo = done && (adjusted || canUndoItem);
  const showReview = review && !checking && !rechecking && !showUndo && !waiting;
  // The time and its dot are taken and dragged at once - unless the time cannot be changed (then the time says why when pressed, and the dot is just a dot).
  const grab = done && !timeWhy ? ctx.timeHandle(item, "start") : undefined;
  // ★`[2026-10-07 사용자 지시 — 상자 전체가 상세정보를 여닫는 자리]` Once done, the whole card opens and closes the checks - not only the name. A press on anything that is its own button (lock, edit, delete, the time, the
  //   tools), on the time editor and inside the opened checks does not: it does what it says. The name stays the one real button of it (keyboard and screen reader).
  const pickCard = (event: MouseEvent<HTMLElement>) => {
    if (!done || checking || removed) return;
    if ((event.target as HTMLElement).closest("button, a, input, select, textarea, label, form, [data-details]")) return;
    ctx.onPick(item.id);
  };
  // `[2026-10-14 실서버 확인]` The time editor opens inside the card: the card is then «open» for the rail too, so the day's other folded cards are not stretched to the editor's
  //   height (they were — empty tall boxes above and below it — because only an opened card was left out of 「every folded card as tall as the tallest」).
  const editingTime = done && ctx.time?.id === item.id && ctx.time.kind === "start" && !ctx.time.direct && !removed;
  // `[2026-10-12 사용자 결정]` The rail (`rail-list.tsx`) reads the stop's start and end to draw it to scale; an opened card carries its line to the card's end.
  return <li className={styles.entry} data-type="item" data-entry-id={item.id} data-reveal-current={current || undefined} data-reveal-waiting={waiting || undefined} data-verdict={item.verdict ?? "checking"} data-selected={selected || undefined} data-time-follow={done && (open || review || showUndo || removed || Boolean(changedFrom && actions.canUndoAll?.())) || undefined} data-time-focused={timeFocused || undefined} data-changed={changed || undefined} data-removed={removed || undefined}
    data-rail="stay" data-start={item.startsAt || undefined} data-end={item.endsAt || undefined} data-rail-open={((open || editingTime) && !checking) || undefined}>
    {done
      // `[2026-10-12 사용자 결정 — A]` Every stop shows its end under its start (it was only the picked or opened one, 2026-10-10).
      ? <Act className={styles.time} data-editing={(ctx.time?.id === item.id && ctx.time.kind === "start") || undefined} data-adjusted={adjusted || undefined} data-grab={grab ? true : undefined} {...grab} why={timeWhy} explain={explain}
          onPress={() => { if (!ctx.dragEnded()) ctx.onOpenTime(item); }}
          title={t("눌러서 시간 고치기 · 잡고 위아래로 끌어도 돼요", "Press to change the time · or take it and drag up or down")} aria-label={t(`${item.title} 시간 고치기 · 지금 ${item.startsAt || "시간 없음"} · 끝 ${item.endsAt || "시간 없음"}`, `Change the time of ${item.title} · now ${item.startsAt || "no time"} · ends ${item.endsAt || "no time"}`)}>
          <span className={styles.timeStack}><time data-stop-start>{item.startsAt || "–"}</time><time className={styles.rangeEnd} data-stop-end>~{item.endsAt || "—"}</time></span></Act>
      : <span className={styles.time}>{item.startsAt || "–"}</span>}
    <span className={styles.rail} aria-hidden="true"><span className={styles.dot} data-grab={grab ? true : undefined} {...grab} /></span>
    <article id={`${ctx.prefix}plan-card-${item.id}`} className={styles.card} data-locked={item.locked || undefined} data-pickable={done && !checking && !removed ? true : undefined} data-open={open && !checking ? true : undefined} aria-labelledby={`${ctx.prefix}plan-item-${item.id}`} aria-busy={checking && !waiting} onClick={pickCard}>
      {done
        // Once done a card opens and closes (accordion: the heading holds the button).
        ? <div className={styles.cardTop}>
            {open ? <Act className={`${styles.icon} ${styles.lockIcon}`} why={lockWhy} explain={explain} onPress={() => ctx.onLock(item)} aria-pressed={item.locked} aria-label={lockLabel}>
              {item.locked ? <Lock size={18} strokeWidth={1.8} aria-hidden="true" /> : <LockOpen size={18} strokeWidth={1.8} aria-hidden="true" />}</Act> : <StopKind item={item} />}
            <h4 className={styles.cardHeading}><button type="button" className={styles.cardHead} title={item.title} aria-labelledby={`${ctx.prefix}plan-item-${item.id}`} aria-expanded={open} aria-controls={`${ctx.prefix}plan-item-${item.id}-checks`} onClick={() => ctx.onPick(item.id)}>
              <span className={styles.cardName}>
                <span className={styles.cardTitleLine}><span id={`${ctx.prefix}plan-item-${item.id}`} className={styles.cardTitle}>{stayTitle(item, t)}</span>{changedMark}</span>
                {stayPillOf(item, t) && <small className={styles.pill} data-stay-nights>{stayPillOf(item, t)}</small>}
                {item.written && <small className={styles.cardWritten}>{t(`원문 「${item.written}」`, `As written: “${item.written}”`)}</small>}
              </span>
            </button></h4>
            {/* 수정·삭제와 확인 중 표시는 제목 옆에, 확인 필요·시간 되돌리기는 하단 한 자리에 둔다. */}
            <span className={styles.cardTools}>
            {status}
            {showRecommend && <Act id={`${ctx.prefix}plan-recommend-${item.id}`} className={styles.icon} why={autoWhy} explain={explain} onPress={() => ctx.onRecommend(item)}
              aria-label={t(`${item.title} 자동 추천`, `Recommend for ${item.title}`)} tooltip={t(`${item.title} 자동 추천`, `Recommend for ${item.title}`)}><Sparkles size={16} strokeWidth={1.8} aria-hidden="true" /></Act>}
            <Act id={`${ctx.prefix}plan-edit-${item.id}`} className={styles.icon} why={changeWhy} explain={explain} onPress={() => ctx.onChange(item)} aria-label={t(`${item.title} 수정`, `Edit ${item.title}`)} tooltip={t(`${item.title} 수정`, `Edit ${item.title}`)}><Pencil size={16} strokeWidth={1.8} aria-hidden="true" /></Act>
            {removed
              ? <button type="button" id={`${ctx.prefix}plan-restore-${item.id}`} className={styles.icon} data-restore onClick={() => ctx.onRestore(item)} aria-label={t(`${item.title} 삭제 되돌리기`, `Undo deleting ${item.title}`)} title={t("되돌리기", "Undo")}><Undo2 size={16} strokeWidth={1.8} aria-hidden="true" /></button>
              : <Act id={`${ctx.prefix}plan-delete-${item.id}`} className={styles.icon} why={deleteWhy} explain={explain} onPress={() => ctx.onDelete(item)} aria-label={t(`${item.title} 삭제`, `Delete ${item.title}`)} tooltip={t(`${item.title} 삭제`, `Delete ${item.title}`)}><Trash2 size={16} strokeWidth={1.8} aria-hidden="true" /></Act>}
            </span>
          </div>
        : <header className={styles.cardHead}><span className={styles.cardName}><h4 id={`${ctx.prefix}plan-item-${item.id}`} className={styles.cardTitle}>{stayTitle(item, t)}</h4>{stayPillOf(item, t) && <small className={styles.pill} data-stay-nights>{stayPillOf(item, t)}</small>}{item.written && <small className={styles.cardWritten}>{t(`원문 「${item.written}」`, `As written: “${item.written}”`)}</small>}</span>{status}</header>}
      {editingTime && <TimeEditor item={item} ui={ctx.time!} />}
      {open && !removed && <div id={`${ctx.prefix}plan-item-${item.id}-checks`} data-details>
        {rows.length
          ? <Checks rows={rows} />
          : !checking && <p className={styles.noChecks}>{t("서버가 이 일정에 따로 알린 것이 없어요.", "The server has nothing more on this stop.")}</p>}
        {done && !checking && ctx.onPickReading && (item.rereads ?? []).map((reread) => <Rereading key={reread.field} item={item} reread={reread} onPick={ctx.onPickReading!} />)}
        {done && !checking && <>
          {note && <p className={styles.cardNote}>{note}</p>}
        </>}
      </div>}
      {/* 2026-10-08: 변경은 제목 옆 체크, 확인 필요와 시간 되돌리기는 펼친 카드에서도 맨 아래 오른쪽. */}
      {!removed && (showReview || (done && (changedFrom || adjusted || canUndoItem || (!open && !checking && needsOf(rows).length > 0)))) && <div className={styles.cardFoot}>
        <div className={styles.cardFootNote}>
          {done && changedFrom && <small className={styles.cardWritten} data-changed>{t(`바뀜 · 이전 ${changedFrom}`, `Changed · was ${changedFrom}`)}</small>}
          {done && !open && !checking && !changedFrom && !adjusted && <NeedsLine rows={rows} inline />}
        </div>
        <div className={styles.cardFootTools} data-card-footer-status>
          {showUndo
            ? canUndoItem ? <Act className={styles.undoTime} why={item.locked ? lockedWhy : item.booked ? t("예약한 일정이라 되돌릴 수 없어요", "Booked stops cannot be restored") : frozen} explain={explain} onPress={() => ctx.onUndoItem!(item)} aria-label={t(`${item.title} 변경 되돌리기`, `Undo changes to ${item.title}`)}><Undo2 size={13} strokeWidth={1.8} aria-hidden="true" />{t("되돌리기", "Undo")}</Act>
              : <Act className={styles.undoTime} why={timeWhy} explain={explain} onPress={() => ctx.onRevertTime(item)} aria-label={t(`${item.title} 시간 되돌리기`, `Put back the time of ${item.title}`)} title={t("시간을 처음으로 되돌리기", "Put the time back")}><Undo2 size={13} strokeWidth={1.8} aria-hidden="true" />{t("되돌리기", "Undo")}</Act>
            : showReview ? <span className={styles.pill} data-state="review">{t("확인 필요", "Check")}</span> : null}
        </div>
      </div>}
    </article>
  </li>;
}

/**
 * `[2026-10-13 사용자 결정 — 수정 모드]` A stop of the registered trip that is over: the trip screen's thin grey line (its name, 「~end」). It is not in the edit — the server keeps it as it is —
 * so it has no tools, and pressed it says why.
 */
function PastItemRow({ item, ctx }: { item: PlanItem; ctx: RowContext }) {
  const t = useT();
  return <li className={styles.entry} data-type="item" data-entry-id={item.id} data-rail="stay" data-start={item.startsAt || undefined} data-end={item.endsAt || undefined} data-past data-passed>
    <span className={styles.time}>{item.startsAt}</span>
    <span className={styles.rail} aria-hidden="true"><span className={styles.dot} /></span>
    <div><Act className={styles.pastLine} why={t("지난 일정이라 바꿀 수 없어요", "This stop is over, so it cannot change")} explain={ctx.explain} onPress={() => undefined}
      aria-label={t(`${item.title} · 지난 일정`, `${item.title} · past`)}><span>{item.title}</span><small>~{item.endsAt || "—"}</small></Act></div>
  </li>;
}

/**
 * `[2026-10-07 cs 개발 세션 · 받아쓰기 두 읽기]` A photo or scan whose line the server read two ways: the two values side by side, the customer presses the one that matches the original.
 * The server cannot tell which is right (it often is the other one — 「진옥화매원조닭한마리」 / 「진옥화할매원조닭한마리」), so neither is marked as better.
 */
function Rereading({ item, reread, onPick }: { item: PlanItem; reread: Reread; onPick: (item: PlanItem, reread: Reread, value: string) => void }) {
  const t = useT();
  const what = reread.field === "title" ? t("이름", "Name") : t("예약번호", "Booking number");
  return <div className={styles.reread} role="group" aria-label={t(`${what} — 사진에서 두 가지로 읽었어요`, `${what} — read two ways off the photo`)}>
    <p className={styles.rereadHint}>{t(`${what} · 원본 사진과 맞는 쪽을 눌러 주세요`, `${what} · press the one that matches the photo`)}</p>
    <div className={styles.rereadOptions}>
      {[reread.current, reread.other].map((value, index) =>
        <button key={index} type="button" className={styles.rereadOption} data-reading={index === 0 ? "current" : "other"} onClick={() => onPick(item, reread, value)}
          aria-label={index === 0 ? t(`「${value}」 그대로 두기`, `Keep “${value}”`) : t(`「${value}」 쪽으로 바꾸기`, `Change to “${value}”`)}>
          <small>{index === 0 ? t("지금 값", "Now") : t("다른 읽기", "Other reading")}</small><span>{value}</span>
        </button>)}
    </div>
  </div>;
}

/**
 * ★`[2026-10-04 사용자 지시]` The stop's time, changed in place (the time at the left of the card was pressed): the start and the end can be typed, the start can be dragged by the grip
 * (up = earlier, down = later; 10 px is 5 minutes) or stepped by 5 minutes, and the stops around it can be pushed along or left alone. What the new time would do is shown before it is
 * sent - which other stops move, and by how much - and the range it may take. Whether it fits the place's hours or the way there is the server's to say when it checks the plan again.
 */
function TimeEditor({ item, ui }: { item: PlanItem; ui: TimeEditUi }) {
  const t = useT();
  const leaving = ui.kind === "depart";
  const along = ui.along.slice(0, 3).map((stop) => `${stop.title} ${stop.minutes > 0 ? "+" : "−"}${Math.abs(stop.minutes)}분`).join(" · ");
  return <form className={styles.timeEditor} data-kind={ui.kind} aria-label={leaving ? t(`${item.title}에서 나서는 시각 고치기`, `Change when to leave ${item.title}`) : t(`${item.title} 시간 고치기`, `Change the time of ${item.title}`)} noValidate
    onSubmit={(event) => { event.preventDefault(); ui.apply(); }}
    onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); ui.cancel(); } }}>
    {leaving
      ? <label className={styles.field}>{t("출발", "Leave")}<input type="time" value={ui.end} aria-invalid={Boolean(ui.problem)} autoFocus onChange={(event) => ui.setEnd(event.target.value)} /></label>
      : <>
        <label className={styles.field}>{t("시작", "Start")}<input type="time" value={ui.start} aria-invalid={Boolean(ui.problem)} autoFocus onChange={(event) => ui.setStart(event.target.value)} /></label>
        <label className={styles.field}>{t("끝", "End")}<input type="time" value={ui.end} aria-invalid={Boolean(ui.problem)} onChange={(event) => ui.setEnd(event.target.value)} /></label></>}
    <div className={styles.timeSteps}>
      <button type="button" className={styles.timeGrip} {...ui.grip} disabled={ui.busy}
        aria-label={leaving ? t("끌어서 출발 시각 바꾸기 · 위아래 화살표로도 바꿔요", "Drag to change when to leave · the arrow keys work too") : t("끌어서 시작 시각 바꾸기 · 위아래 화살표로도 바꿔요", "Drag to change the start · the arrow keys work too")}
        title={t("위로 끌면 일찍, 아래로 끌면 늦게 (10px = 5분) · 여유를 다 쓰면 한 번 멈춰요", "Drag up for earlier, down for later (10 px = 5 minutes) · it stops once when the free time is used up")}>
        <ChevronsUpDown size={18} strokeWidth={1.8} aria-hidden="true" /></button>
      <button type="button" className={styles.timeStep} onClick={() => ui.step(-5)} disabled={ui.busy} aria-label={t("−5분", "−5 min")} title={t("5분 일찍", "5 minutes earlier")}><Minus size={14} strokeWidth={2} aria-hidden="true" />5</button>
      <button type="button" className={styles.timeStep} onClick={() => ui.step(5)} disabled={ui.busy} aria-label={t("+5분", "+5 min")} title={t("5분 늦게", "5 minutes later")}><Plus size={14} strokeWidth={2} aria-hidden="true" />5</button>
      <span className={styles.timeRange}>{t(`가능한 시각 ${ui.range.min} ~ ${ui.range.max}`, `Possible: ${ui.range.min} – ${ui.range.max}`)}</span>
    </div>
    <label className={styles.timePush}><input type="checkbox" checked={ui.push} onChange={(event) => ui.setPush(event.target.checked)} />{leaving ? t("다음 일정도 함께 밀기", "Push the next stops along") : t("앞뒤 일정도 함께 밀기", "Push the stops around it along")}</label>
    {ui.count > 1 && !ui.problem && <p className={styles.timeNote} role="status">{t(`함께 바뀌는 일정 ${ui.count - 1}개 · ${along}${ui.along.length > 3 ? ` 외 ${ui.along.length - 3}개` : ""}`, `${ui.count - 1} more stop${ui.count > 2 ? "s" : ""} move: ${along}`)}</p>}
    <div className={styles.timeIcons}>
      {ui.undo && <button type="button" className={styles.timeIcon} data-undo onClick={ui.undo} disabled={ui.busy} aria-label={t("처음 시간으로 되돌리기", "Put the time back")} title={t("처음 시간으로 되돌리기", "Put the time back")}><Undo2 size={18} strokeWidth={1.9} aria-hidden="true" /></button>}
      <button type="button" className={styles.timeIcon} onClick={ui.cancel} disabled={ui.busy} aria-label={t("취소", "Cancel")} title={t("취소", "Cancel")}><X size={19} strokeWidth={2} aria-hidden="true" /></button>
      <button type="submit" className={styles.timeIcon} data-primary disabled={ui.busy || Boolean(ui.problem) || ui.count === 0} aria-label={ui.busy ? t("저장하는 중…", "Saving…") : t("적용", "Apply")} title={t("적용", "Apply")}>{ui.busy ? <span className={styles.spinner} aria-hidden="true" /> : <Check size={19} strokeWidth={2.2} aria-hidden="true" />}</button>
    </div>
    {ui.problem && <p className={styles.editorError} role="alert">{ui.problem}</p>}
  </form>;
}

/** The icon for how the leg is travelled — chosen from the server's own words for the mode; none when they say something else. */
function ModeIcon({ mode }: { mode: string }) {
  const props = { size: 14, strokeWidth: 1.8, "aria-hidden": true as const };
  if (/도보|걷/.test(mode)) return <Footprints {...props} />;
  if (/지하철|전철|철도|기차/.test(mode)) return <TrainFront {...props} />;
  if (/버스/.test(mode)) return <Bus {...props} />;
  if (/택시|자동차|차량/.test(mode)) return <Car {...props} />;
  if (/자전거|따릉이/.test(mode)) return <Bike {...props} />;
  return null;
}

/**
 * ★`[2026-10-04 사용자 지시]` The way to the next place says only how long and how far (the mode is an icon and for a screen reader): the server's sentence
 * 「지하철 3호선 10분 · 1.1km」 repeated the line the mode chip had just said. The sentence is cut at the first 「n분」/「n시간」 and left whole when it has none.
 */
export function legText(summary: string): string {
  const found = /(\d+\s*시간\s*)?\d+\s*분.*$/.exec(summary);
  return found ? found[0].trim() : summary;
}

/**
 * `slack` = the free minutes after this leg (next stop's start minus the time the traveller gets there). ★`[2026-10-04 사용자 지시]` 15 minutes or more is said once, quietly
 * (「여유 1시간 42분」); a leg that arrives late says so instead (「10분 늦어요」). `[2026-10-12 사용자 결정]` The space the leg takes is its time to scale (`rail-list.tsx` — dashes only while
 * travelling, a thin line for waiting and the free time), and its time, 「−」 and line of text stand where it leaves (`--dep`).
 */
function MoveRow({ move, from, to, ctx, slack, dim }: { move: PlanMove; from: PlanItem | undefined; to: PlanItem | undefined; ctx: RowContext; slack: number | null; dim: boolean }) {
  const t = useT();
  const { done } = ctx;
  const open = done && ctx.open === move.id;
  const checking = move.verdict === null;
  // Only a leg that needs a look is marked: a fine one stays quiet, so the eye goes to the stops first.
  const mark = checking || move.verdict !== "review" ? null : <VerdictMark verdict={move.verdict} />;
  const free = freeText(slack);
  const late = slack !== null && slack < 0;
  // ★`[2026-10-05 사용자 지시]` One line says it all: the way, how long, how far - and how much time is left after it (「지하철 4호선 50분 · 3.5km  여유 1시간 40분」). The free time was a second line
  //   under the leg that took room and said nothing the first line could not.
  const freeSays = done && !checking ? (late ? t(`${Math.abs(slack!)}분 늦어요`, `${Math.abs(slack!)} min late`) : free ? t(`여유 ${free}`, `${free} free`) : null) : null;
  const line = checking ? null : <>
    <span className={styles.modeIcon}><ModeIcon mode={move.mode} /></span>
    {!move.summary.includes(move.mode) && <span className="sr-only">{move.mode}</span>}
    <span className={styles.moveText}>{move.summary}</span>
    {freeSays && <span className={styles.moveFree} data-late={late || undefined}>{freeSays}</span>}{mark}</>;
  // ★`[2026-10-05 사용자 지시]` The time of leaving can be changed like the time of a stop: leaving later uses the free time first, then the next stops are pushed.
  const leaveEditing = ctx.time?.id === move.fromId && ctx.time.kind === "depart";
  const timeFocused = done && (ctx.time ? leaveEditing : open);
  // `[2026-10-12 사용자 결정 — A]` 「HH:MM」 and 「출발」 under it (the 44 px column holds no 「HH:MM 출발」 on one line); opened, 「~도착」 under that.
  const leaving = <span className={styles.timeStack}>
    {move.departAt ? <><time data-move-departure>{move.departAt}</time><small className={styles.departWord}>{t("출발", "Leave")}</small></> : <span data-move-departure>{t("미정", "TBD")}</span>}
    {(move.departAt || move.arriveAt) && (move.arriveAt
      ? <time className={styles.rangeEnd} data-move-arrival aria-hidden={!timeFocused}>~{move.arriveAt}</time>
      : <span className={styles.rangeEnd} data-move-arrival aria-hidden={!timeFocused}>{t("미정", "TBD")}</span>)}
  </span>;
  const leaveWhy = !from ? null : dim ? t("삭제할 일정 앞뒤의 이동이라 바꿀 수 없어요", "This leg belongs to a stop marked for deletion")
    : !ctx.actions.retime && !ctx.actions.edit ? t("시간 고치기는 준비 중이에요", "Changing the time is coming")
      : from.locked ? t("고정한 일정에서 나서는 시각이라 바꿀 수 없어요 · 잠금을 풀면 수정할 수 있어요", "Locked · unlock the stop to change when to leave")
        : from.booked === true ? t("예약한 일정에서 나서는 시각이라 바꿀 수 없어요", "Booked: when to leave cannot be changed") : ctx.frozen;
  const grab = done && !checking && from && !leaveWhy ? ctx.timeHandle(from, "depart") : undefined;
  return <li className={styles.entry} data-type="move" data-entry-id={move.id} data-reveal-current={!done && ctx.reveal?.activeId === move.id || undefined} data-verdict={move.verdict ?? "checking"} data-time-focused={timeFocused || undefined} data-dim={dim || undefined}
    data-rail="move" data-dep={move.departAt || undefined} data-arr={move.arriveAt || undefined} data-rail-open={open || undefined}>
    <div className={styles.moveClock}>
    {done && !checking && from
      ? <Act className={styles.time} data-editing={leaveEditing || undefined} data-grab={grab ? true : undefined} {...grab} why={leaveWhy} explain={ctx.explain} onPress={() => { if (!ctx.dragEnded()) ctx.onOpenDepart(move.fromId); }}
          title={t("눌러서 출발 시각 고치기", "Press to change when to leave")} aria-label={t(`${from.title}에서 나서는 시각 고치기 · 지금 ${move.departAt || "미정"} · 도착 ${move.arriveAt || "미정"}`, `Change when to leave ${from.title} · now ${move.departAt || "TBD"} · arrival ${move.arriveAt || "TBD"}`)}>{leaving}</Act>
      : <span className={styles.time}>{!checking && leaving}</span>}
    </div>
    <span className={styles.rail} aria-hidden="true"><span className={`${styles.dot} ${styles.departureMark}`} data-departure-mark title={t("출발", "Depart")} data-grab={grab ? true : undefined} {...grab}><Minus size={16} strokeWidth={2} /></span></span>
    <div className={styles.moveCol}>
    <div className={styles.move} data-open={open || undefined} data-rail-box>{checking
      ? <span className={styles.waiting}>{done ? t("이동 경로 다시 찾는 중…", "Finding the way again…") : t("이동 경로를 찾는 중…", "Finding the way…")}</span>
      : done
        ? <><button type="button" className={styles.moveHead} aria-expanded={open} aria-controls={`${ctx.prefix}plan-move-${move.id}-checks`} onClick={() => ctx.onToggleMove(move.id)}>{line}</button>
          {/* `[2026-10-07 사용자 지시 — 이동수단 고르기]` 펼친 칸 맨 위: 「이동 수단」 박스(서버가 이 기능을 주지 않으면 없다). 확인 중 · 삭제 예정 일정 옆 구간은 잠기고 이유를 말한다. */}
          {open && ctx.actions.moveOptions && <MoveModeBox key={`${move.id}:${ctx.revision ?? "preview"}`} move={move} to={to} ctx={ctx}
            locked={dim ? t("삭제할 일정 앞뒤의 이동이라 바꿀 수 없어요", "This leg belongs to a stop marked for deletion") : ctx.frozen} />}
          {open && <Checks id={`${ctx.prefix}plan-move-${move.id}-checks`} rows={move.checks} />}</>
        : <div className={styles.moveHead}>{line}</div>}</div>
    {done && !checking && leaveEditing && !ctx.time!.direct && from && <TimeEditor item={from} ui={ctx.time!} />}
    </div>
  </li>;
}
