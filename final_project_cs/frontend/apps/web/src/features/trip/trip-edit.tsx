"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { JourneyShell } from "@/components/layout/journey-shell";
import { Button, QueryState } from "@/components/ui";
import { useRouteDetail } from "@/features/map/use-route-detail";
import { readingOf } from "@/features/plan-check/from-intake";
import { reviewResultOf } from "@/features/plan-check/from-review";
import type { PlanCheckView, PlanItem } from "@/features/plan-check/model";
import { PlanCheck, type TripEditMode } from "@/features/plan-check/plan-check";
import type { RailNow } from "@/features/plan-check/rail-list";
import { formatHm } from "@/features/plan-check/time-plan";
import { useServerReview } from "@/features/plan-check/use-server-review";
import { LiveError } from "@/lib/live/client";
import { cancelTripEdit, confirmIntake, getIntake, reopenTrip } from "@/lib/live/intake";
import type { ReviewedIntakeView } from "@/lib/live/intake-review";
import { getIntakeRouteShapes } from "@/lib/live/route-shapes";
import { useSettings, useT } from "@/lib/settings";
import { pushToast } from "@/lib/toast-bus";
import type { Trip, TripStop } from "./model";
import { tripKey, tripsKey } from "./use-trip";
import { useSeoulNow } from "./use-seoul-now";
import dialogStyles from "./trip-list.module.css";

/**
 * `[2026-10-13 사용자 결정 — D 수정 모드 · 「일정 수정」 후보 1]` A registered trip changed in place, with everything the plan check can do: 「일정 수정」 reopens the trip as a plan-check intake
 * (`POST /v1/web/trips/{id}/reopen`), and every change goes through the plan check's own calls (`useServerReview` — times, places, adding, deleting, recommending, locking, ways to go, undo).
 * 「완료」 writes it as the trip's next version (the same intake's `/confirm`: a version, the history and one notice, like a change asked in the chat — `wiki/external/rest-endpoints.md`
 * 「등록한 여행 고치기」) and goes back to the trip.
 * ★Stops that are over are not in the intake (the server keeps them as they are): they stand in the list as the trip screen's grey lines and cannot change.
 * ★The trip's name, party and first day are not changed here (the server refuses them), and the plan check's 「일정 짜 주기」 is not offered.
 */
export function TripEdit({ trip, onExit }: { trip: Trip; onExit: () => void }) {
  const t = useT();
  const { language } = useSettings();
  const queryClient = useQueryClient();
  const now = useSeoulNow();
  const [intakeId, setIntakeId] = useState<string | null>(null);
  /** Back to the trip, read again — saying in the server's words what went wrong, when something did (the trip screen's bar shows it). */
  const leave = useCallback((error?: Error) => {
    void queryClient.invalidateQueries({ queryKey: tripKey(trip.id, language) });
    if (error) pushToast({ text: error.message, stay: true });
    onExit();
  }, [queryClient, trip.id, language, onExit]);
  const reopen = useMutation({
    mutationFn: () => reopenTrip(trip.id, trip.version ?? 0, language),
    onSuccess: (view) => { queryClient.setQueryData(["intake", view.intake_id, language], view); setIntakeId(view.intake_id); },
    // A trip that changed in the meantime (`stale_itinerary`), or any other refusal: the trip is shown as it is now.
    onError: (error) => leave(error),
  });
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current) return;
    opened.current = true;
    reopen.mutate();
  }, [reopen]);

  const key = useMemo(() => ["intake", intakeId ?? "", language] as const, [intakeId, language]);
  const query = useQuery({
    queryKey: key,
    queryFn: () => getIntake(intakeId!, language),
    enabled: Boolean(intakeId),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 2_000,
  });
  const reread = useCallback(() => { void queryClient.refetchQueries({ queryKey: key }, { cancelRefetch: false }); }, [queryClient, key]);
  const apply = useCallback((next: ReviewedIntakeView) => { queryClient.setQueryData(key, next); }, [queryClient, key]);
  const server = useServerReview({ intakeId: intakeId ?? "", view: query.data, language, apply, reread });
  // The trip's name is not changed here — the server refuses it on this intake (`trip_field_locked`), so the name is not offered for editing.
  const actions = useMemo(() => ({ ...server.actions, editTrip: undefined }), [server.actions]);

  const frozen = query.data?.reopen?.frozen_item_ids;
  const reviewed = useMemo(() => query.data?.review ? reviewResultOf(query.data, readingOf(query.data), server.infos) : null, [query.data, server.infos]);
  const previewed = useMemo(() => server.preview?.review ? reviewResultOf(server.preview, readingOf(server.preview), server.infos) : null, [server.preview, server.infos]);
  const result = useMemo(() => reviewed && withTrip({ ...reviewed, dirty: server.dirty, rechecking: server.rechecking }, trip, frozen ?? []), [reviewed, server.dirty, server.rechecking, trip, frozen]);
  const preview = useMemo(() => previewed && withTrip({ ...previewed, dirty: false, rechecking: null }, trip, frozen ?? []), [previewed, trip, frozen]);
  // The lines between the stops for the plan being edited (the intake's `route-shapes`), as on the plan check; none on a failure.
  const routeShapes = useQuery({
    queryKey: ["intake-route-shapes", intakeId, query.data?.revision ?? 0, language],
    queryFn: () => getIntakeRouteShapes(intakeId!, language).catch(() => null),
    enabled: Boolean(reviewed),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 10 * 60_000,
  });
  const routeDetail = useRouteDetail(routeShapes.data, ["intake", intakeId ?? "", query.data?.revision ?? 0, language], () => getIntakeRouteShapes(intakeId!, language, { detail: true }));

  // `[2026-10-13 사용자 지시]` The plan check's last step, as it is: changes → 「다시 제출」 → checked → ✓. Here ✓ (and 「완료」 in the head) writes the trip's next version, then the trip is shown.
  const done = useMutation({
    mutationFn: (revision: number) => confirmIntake(intakeId!, revision, language),
    onSuccess: () => { queryClient.removeQueries({ queryKey: tripsKey }); leave(); },
    onError: (error) => {
      if (error instanceof LiveError && error.code === "stale_revision") reread();
      // The trip changed since the edit was opened: nothing was written - back to the trip as it is now.
      if (error instanceof LiveError && error.code === "stale_itinerary") leave(error);
    },
  });
  const refusal = done.error instanceof LiveError ? (done.error.detail as { problems?: { field: string; message?: string; reason?: string }[] } | undefined) : undefined;
  // The server calls the plan ready (`review.ready`) — or, with every stop still ahead taken out, there is nothing left to check and the stops that are over stay.
  const review = query.data?.review;
  const emptied = (frozen?.length ?? 0) > 0 && review?.items.length === 0 && (query.data?.check?.problems ?? []).every((problem) => problem.code === "no_items");
  const registration = {
    ready: Boolean(review?.ready) || emptied, previewReady: server.preview?.review ? server.preview.review.ready : undefined, busy: done.isPending,
    // The revision the server holds when it is pressed (a change saved a moment before is newer than this render's).
    onRegister: () => done.mutate(queryClient.getQueryData<ReviewedIntakeView>(key)?.revision ?? query.data?.revision ?? 1),
    error: done.error?.message ?? null, problems: refusal?.problems?.map((problem) => problem.message ?? `${problem.field}: ${problem.reason}`) ?? [],
    registeredHref: null,
  };
  // `[2026-10-13 사용자 지시]` 「취소」: asked first, then the edit is closed on the server (what was changed is not written, and the next edit starts afresh) and the trip is shown.
  const [asking, setAsking] = useState(false);
  const drop = useMutation({
    mutationFn: () => cancelTripEdit(intakeId!, language),
    onSuccess: () => leave(),
    onError: (error) => { setAsking(false); pushToast({ text: error.message, stay: true }); },
  });
  // `[2026-10-15 사용자 지시 — ST4F-494]` The edit shows 「지금 HH:MM」 beside the dot too, under the same operator switch as the trip screen (`trip.nowLabel`).
  const nowLabel = trip.nowLabel;
  const railNow = useCallback((date: string): RailNow | null => date < now.date ? { kind: "over" }
    : date === now.date ? { kind: "today", minute: now.minute, ...(nowLabel ? { label: t(`지금 ${formatHm(now.minute)}`, `Now ${formatHm(now.minute)}`) } : {}) } : null, [now, nowLabel, t]);
  const edit: TripEditMode = useMemo(() => ({
    onCancel: () => setAsking(true),
    now: railNow,
    intro: { text: t("일정을 고칠 수 있어요", "You can change the itinerary"), sub: t("지난 일정은 바꿀 수 없어요 · 다 고쳤으면 「완료」를 눌러 주세요", "Past stops cannot change · press Done when you are finished"), ms: 3_500 },
  }), [railNow, t]);

  if (query.data && !query.data.review) {
    // The server could not check the plan (`review_error`): nothing is offered as fine that it did not check.
    return <JourneyShell view="other" title={["나의 여행", "Your trip"]}>
      <QueryState loading={false} error={new Error(t("서버가 장소·운영시간·이동 확인 결과를 만들지 못했어요.", "The server could not build the place, hours and route check."))} retry={reread} />
    </JourneyShell>;
  }
  if (!result) {
    return <JourneyShell view="other" title={["나의 여행", "Your trip"]}><QueryState loading={!query.error} error={query.error} retry={reread} /></JourneyShell>;
  }
  return <>
    <PlanCheck key="edit" view={result} previewView={preview} routes={routeDetail.routes} onMapZoom={routeDetail.onZoom} onBack={onExit} actions={actions} registration={registration} edit={edit} />
    {asking && <CancelDialog pending={drop.isPending} onKeep={() => setAsking(false)} onDrop={() => drop.mutate()} />}
  </>;
}

/** 「취소」 asked first (the trip list's delete question, in its look): going on editing is the button in focus. */
function CancelDialog({ pending, onKeep, onDrop }: { pending: boolean; onKeep: () => void; onDrop: () => void }) {
  const t = useT();
  const keep = useRef<HTMLButtonElement>(null);
  const drop = useRef<HTMLButtonElement>(null);
  useEffect(() => { keep.current?.focus(); }, []);

  function keys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") { event.preventDefault(); if (!pending) onKeep(); return; }
    if (event.key !== "Tab") return;
    if (event.shiftKey && document.activeElement === keep.current) { event.preventDefault(); drop.current?.focus(); }
    else if (!event.shiftKey && document.activeElement === drop.current) { event.preventDefault(); keep.current?.focus(); }
  }

  // While cancelling, both buttons stay focusable (aria-disabled) so focus does not drop out of the dialog.
  return createPortal(<div className={dialogStyles.overlay}>
    <div className={dialogStyles.dialog} role="alertdialog" aria-modal="true" aria-labelledby="edit-cancel-title" aria-describedby="edit-cancel-body" onKeyDown={keys}>
      <h2 id="edit-cancel-title">{t("일정 변경을 취소할까요?", "Cancel the changes to the itinerary?")}</h2>
      <p id="edit-cancel-body" className={dialogStyles.dialogBody}>{t("취소하면 변경 내용이 사라져요.", "If you cancel, the changes are lost.")}</p>
      <div className={dialogStyles.dialogActions}>
        <Button ref={keep} aria-disabled={pending || undefined} onClick={() => { if (!pending) onKeep(); }}>{t("계속 수정", "Keep editing")}</Button>
        <Button ref={drop} variant="danger" aria-disabled={pending || undefined} onClick={() => { if (!pending) onDrop(); }}>{pending ? t("취소하는 중…", "Cancelling…") : t("변경 취소", "Cancel changes")}</Button>
      </div>
    </div>
  </div>, document.body);
}

/**
 * The edit's plan with the trip around it: the stops that are not in the edit (`frozen` — over) as grey lines, and every stop numbered by the trip's days — the server counts only the days
 * that have a stop in the edit, so a trip seen on its second day would start again at 「1일차」.
 */
export function withTrip(view: PlanCheckView, trip: Pick<Trip, "stops" | "title">, frozen: readonly string[]): PlanCheckView {
  const kept = new Set(frozen);
  const past = trip.stops.filter((stop) => kept.has(stop.id)).map(pastItem);
  const dates = [...new Set([...trip.stops.map((stop) => stop.date), ...view.items.map((item) => item.date)].filter(Boolean))].sort();
  const items = [...past, ...view.items].map((item) => item.date ? { ...item, day: dates.indexOf(item.date) + 1 } : item)
    .sort((a, b) => (a.date || "9999").localeCompare(b.date || "9999") || (a.startsAt || "99:99").localeCompare(b.startsAt || "99:99"));
  const dayOf = new Map(items.map((item) => [item.id, item.day]));
  const days = [...new Map(items.map((item) => [item.day, item.date])).entries()].map(([day, date]) => ({ day, date })).sort((a, b) => a.day - b.day);
  return { ...view, title: trip.title ?? view.title, items, days, moves: view.moves.map((move) => ({ ...move, day: dayOf.get(move.fromId) ?? move.day })) };
}

/** A stop of the trip that is not in the edit, as one of the list's (`past` — drawn grey, nothing on it can change). */
function pastItem(stop: TripStop): PlanItem {
  return {
    id: stop.id, day: 0, date: stop.date, startsAt: stop.time, endsAt: stop.endTime ?? "", title: stop.title, place: "", noPlace: false, kind: stop.kind ?? null,
    coordinates: stop.coordinates ?? null, checks: [], verdict: "keep", locked: false, booked: stop.booking === "booked" ? true : null, info: null, suggestion: null, past: true,
  };
}
