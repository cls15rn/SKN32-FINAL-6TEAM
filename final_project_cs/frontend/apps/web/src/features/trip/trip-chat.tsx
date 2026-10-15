"use client";

import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type RefObject } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, LocateFixed, MessageCircle, Send, X } from "lucide-react";
import { tripGateway } from "@/lib/gateway";
import { undoChange, warmup } from "@/lib/live/extras";
import { LiveError } from "@/lib/live/client";
import { progressText, type OpProgress } from "@/lib/live/stream";
import { readConsents, useConsent } from "@/features/consent/consent-store";
import { OptionalConsentPrompt } from "@/features/consent/optional-consent-prompt";
import { currentLocation, locationFailureText, type LocationFix } from "@/lib/location";
import { useSettings, useT } from "@/lib/settings";
import { LinkedText } from "./linked-text";
import { useChatFollow } from "./use-chat-follow";
import { tripKey } from "./use-trip";
import { noticesKey, proposalsKey } from "./use-trip-extras";
import type { Trip, TripMessage, TripStop } from "./model";
import styles from "./trip-screen.module.css";

/**
 * `[2026-10-07]` The trip's conversation with the server (moved from the old trip screen, `trip-home.tsx`, unchanged in what it sends and shows): a message goes to the server's trip
 * consultation (`POST …/messages`, live progress), the plan is read again after it, a change made from chat can be undone while it is the latest, and a question that needs where
 * the customer is asks the browser only on that press (and the personal-location agreement first).
 */
export function useTripChat(trip: Trip, selectedId: string | null, onAsked: () => void) {
  const t = useT();
  const { language } = useSettings();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [inputError, setInputError] = useState("");
  // The server answered but the plan did not re-read (Q-05): the reply is shown, and the plan is read again until it loads.
  const [rereading, setRereading] = useState(false);
  const [locating, setLocating] = useState(false);
  const locationAgreed = useConsent("location");
  const locationGeneration = useRef(0);
  const locationAttempt = useRef(0);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; locationGeneration.current += 1; };
  }, []);
  useEffect(() => {
    if (!locationAgreed) locationGeneration.current += 1;
  }, [locationAgreed]);
  const [locationError, setLocationError] = useState("");
  // A question waiting for the customer's agreement to use their location (asked in place, see `askWithLocation`).
  const [locationQuestion, setLocationQuestion] = useState<string | null>(null);
  // What the server says it is doing with the message now (live progress stream); null until it says.
  const [progress, setProgress] = useState<OpProgress | null>(null);
  const message = useMutation({
    // Keep the cache entry that belongs to the account which sent this message.
    // A logout removes it; a new entry with the same trip key belongs to a later session.
    onMutate: () => ({ query: queryClient.getQueryCache().find({ queryKey: tripKey(trip.id, language), exact: true }) }),
    // ★Only a stop the customer actually picked goes to the server (`item_id`).
    mutationFn: async ({ text, itemId, location, summaryDay }: { text: string; clearDraft: boolean; itemId?: string | null; location?: LocationFix | null; summaryDay?: string }) => {
      setProgress(null);
      try { return { updated: await tripGateway.sendMessage(trip.id, text, language, itemId, location, setProgress, undefined, summaryDay), sent: null }; }
      catch (error) {
        if (!(error instanceof LiveError) || error.code !== "reply_kept") throw error;
        setRereading(true);
        return { updated: null, sent: (error.detail as { sent?: TripMessage[] } | undefined)?.sent ?? [] };
      }
    },
    onSuccess: (result, variables, context) => {
      if (!context?.query || queryClient.getQueryCache().find({ queryKey: tripKey(trip.id, language), exact: true }) !== context.query) return;
      // SSE can refresh the plan before this response reaches the mutation.
      // Keep a newer itinerary and append only replies not already in its messages.
      let updated = result.updated;
      const current = queryClient.getQueryData<Trip>(tripKey(trip.id, language)) ?? trip;
      const older = updated && typeof updated.version === "number" && typeof current.version === "number" && updated.version < current.version;
      if (!updated || older) {
        const seen = new Set(current.messages.map(item => item.id));
        const appended = (updated?.messages ?? result.sent ?? []).filter(item => { if (seen.has(item.id)) return false; seen.add(item.id); return true; });
        updated = { ...current, messages: [...current.messages, ...appended] };
      }
      queryClient.setQueryData(tripKey(trip.id, language), updated);
      if (variables.clearDraft) setDraft("");
      // ★A message can open a choice (「알아봐 줘」 → 「선택이 필요해요」) or send a notice — read both again now, not at the next poll.
      void queryClient.invalidateQueries({ queryKey: proposalsKey(trip.id, language) });
      void queryClient.invalidateQueries({ queryKey: noticesKey(trip.id, language) });
    },
  });

  // ★Undo a change made from chat (user decision 2026-09-29). Same server call as the automatic-change undo; 409 = the plan moved on, nothing changed.
  const undo = useMutation({
    mutationFn: (version: number) => undoChange(trip.id, { baseVersion: version, toVersion: version - 1, requestId: `chat-undo:v${version}->v${version - 1}` }, language),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: tripKey(trip.id, language) });
      void queryClient.invalidateQueries({ queryKey: noticesKey(trip.id, language) });
    },
  });

  useEffect(() => {
    if (!rereading) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const again = async () => {
      try {
        await queryClient.refetchQueries({ queryKey: tripKey(trip.id, language) }, { throwOnError: true });
        if (!stopped) setRereading(false);
      } catch {
        if (!stopped) timer = setTimeout(again, 5_000);
      }
    };
    timer = setTimeout(again, 2_000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [rereading, trip.id, language, queryClient]);

  // ★Wake the chat model as the trip opens, so the first question does not wait for it to load (~35 s cold). Best effort — a failed chat reports itself.
  useEffect(() => {
    warmup(language).catch(() => { /* the chat reports its own failure */ });
  }, [trip.id, language]);

  const pickedId = trip.stops.some((stop) => stop.id === selectedId) ? selectedId : null;

  function ask(text: string, clearDraft = false, itemId: string | null = pickedId, location: LocationFix | null = null, summaryDay?: string) {
    if (message.isPending) return;
    const trimmed = text.trim();
    if (!trimmed) {
      setInputError(t("메시지를 입력해 주세요.", "Enter a message."));
      document.getElementById("trip-chat-message")?.focus();
      return;
    }
    setInputError("");
    setLocationError("");
    onAsked();
    message.mutate({ text: trimmed, clearDraft, itemId, location, summaryDay });
  }

  // ★The server said the answer needs where the customer is. The browser is asked only now, on this press (user decision 2026-09-30) — and only with the agreement to the location item.
  async function askWithLocation(question: string) {
    if (!live.current || message.isPending) return;
    setLocationError("");
    if (!readConsents().location) { setLocationQuestion(question); return; }
    setLocationQuestion(null);
    const attempt = ++locationGeneration.current;
    locationAttempt.current = attempt;
    setLocating(true);
    const result = await currentLocation();
    // A delayed GPS answer must not submit a question after withdrawal, a newer
    // attempt, or leaving this trip. A withdrawn then renewed consent is a new attempt too.
    if (!live.current || attempt !== locationAttempt.current) return;
    setLocating(false);
    if (attempt !== locationGeneration.current || !readConsents().location) return;
    if (!result.ok) { setLocationError(locationFailureText(result.reason, t)); return; }
    ask(question, false, undefined, result.fix);
  }

  const askAbout = (stop: TripStop) => ask(t(`${stop.date} ${stop.time} ${stop.title} 일정의 상세를 알려 주세요.`, `Tell me about the ${stop.title} stop on ${stop.date} at ${stop.time}.`), false, stop.id);

  return { draft, setDraft, inputError, setInputError, rereading, locating: locating && locationAgreed, locationError, locationQuestion, setLocationQuestion, progress, message, undo, ask, askWithLocation, askAbout };
}

export type TripChat = ReturnType<typeof useTripChat>;

/**
 * The chat pane of the sheet (mockup C안 「채팅」): the context of the question (the day and the stop picked) at the top of the conversation, the conversation, the quick questions and
 * what a message does. Typing is the chat bar's (`ChatBar`) — it stays at the bottom of the frame.
 */
export function ChatPane({ trip, chat, day, dayText, selected, logRef, active }: { trip: Trip; chat: TripChat; day: string; dayText: string; selected: TripStop | null; logRef: RefObject<HTMLDivElement | null>; active: boolean }) {
  const t = useT();
  const { message, undo } = chat;
  const latest = useChatFollow(logRef, active, trip.messages.at(-1)?.id, message.isPending);
  return <>
    <div ref={logRef} className={styles.chatLog} tabIndex={0} onScroll={latest.onScroll} role="log" aria-label={t("여행 대화 이력", "Travel conversation")} aria-live="polite" aria-relevant="additions text" aria-busy={message.isPending}>
      <div className={styles.chatContext}><strong>{dayText} · {day}</strong>
        <span>{selected ? t(`선택: ${selected.title}`, `Selected: ${selected.title}`) : t("선택한 일정이 없어요.", "No stop selected.")}</span></div>
      {trip.messages.length === 0 && <article className={styles.message} data-role="assistant"><p className={styles.messageMeta}>triPilot</p><p className={styles.bubble}>{t("등록한 일정에서 궁금한 내용을 골라 주세요. 하루 요약, 선택한 장소와 다음 일정을 함께 살펴볼 수 있어요.", "Explore your saved itinerary. Ask for a day summary, details of your selected stop, or what comes next.")}</p></article>}
      {trip.messages.map((item, index) => <article key={item.id} className={styles.message} data-role={item.role}><p className={styles.messageMeta}>{item.role === "user" ? t("나", "You") : "triPilot"}</p><p className={styles.bubble}>{item.role === "assistant" ? <LinkedText text={item.text} mapLabel={t("지도 앱으로 열기", "Open in maps app")} siteLabel={(site) => t(`${site}에서 열기`, `Open on ${site}`)} /> : item.text}</p>
        {item.awaitingReply && index === trip.messages.length - 1 && <p className={styles.undoHint}>{t("답변을 기다리고 있어요. 아래에 답하시면 같은 요청을 이어서 처리해요.", "Waiting for your reply. Answer below to continue this request.")}</p>}
        {item.more && <details className={styles.more}><summary>{t("더 보기", "More")}</summary><p><LinkedText text={item.more} mapLabel={t("지도 앱으로 열기", "Open in maps app")} siteLabel={(site) => t(`${site}에서 열기`, `Open on ${site}`)} /></p></details>}
        {item.basis && item.basis.length > 0 && <p className={styles.devBasis}>{t("개발 모드 · 근거 ", "Dev mode · basis ")}{item.basis.join(", ")}</p>}
        {/* ★A change made from chat can be undone while it is still the plan's latest version. */}
        {item.changedTo !== undefined && item.changedTo === trip.version && <div className={styles.choices}>
          <button type="button" className={styles.choice} disabled={undo.isPending} onClick={() => undo.mutate(item.changedTo as number)}>{undo.isPending ? t("되돌리는 중…", "Undoing…") : t("되돌리기", "Undo")}</button>
          <span className={styles.undoHint}>{t("바꾼 일정이 마음에 안 드시면 되돌릴 수 있어요.", "Not what you wanted? You can undo this change.")}</span></div>}
        {/* ★Only the latest answer's choices can be picked — an older question is no longer open. */}
        {item.choices && item.choices.length > 0 && index === trip.messages.length - 1 && <div className={styles.choices}>
          {item.choicesTitle && <span className={styles.choicesTitle}>{item.choicesTitle}</span>}{item.choices.map((choice) =>
          <button key={choice.message} type="button" className={styles.choice} disabled={message.isPending} onClick={() => chat.ask(choice.message, false, choice.itemId ?? null)}>{choice.label}</button>)}</div>}
        {item.needsLocation && index === trip.messages.length - 1 && trip.messages[index - 1]?.role === "user" && <div className={styles.choices}>
          <button type="button" className={styles.choice} disabled={message.isPending || chat.locating} onClick={() => void chat.askWithLocation(trip.messages[index - 1].text)}><LocateFixed size={15} strokeWidth={1.6} aria-hidden="true" />{chat.locating ? t("위치 찾는 중…", "Finding your location…") : t("내 위치 알려 주고 다시 묻기", "Share my location and ask again")}</button>
          <span className={styles.undoHint}>{t("누르면 브라우저가 위치 권한을 물어요. 위치는 이 질문에 답하는 데 써요.", "Your browser will ask first. The location is used to answer this question.")}</span></div>}</article>)}
      {chat.locationQuestion !== null && <OptionalConsentPrompt code="location" why={t("내 위치로 답하려면 개인위치정보 수집·이용에 동의해야 해요.", "To answer from where you are, we need your agreement to the personal location item.")}
        onAgreed={() => void chat.askWithLocation(chat.locationQuestion!)} onCancel={() => chat.setLocationQuestion(null)} />}
      {chat.locationError && <p className={styles.error} role="alert">{chat.locationError}</p>}
      {undo.error && <p className={styles.error} role="alert">{undo.error instanceof LiveError && ["stale_itinerary", "stale", "invalid_version"].includes(undo.error.code)
        ? t("그 사이 일정이 다시 바뀌어 되돌리지 않았어요.", "The plan changed again meanwhile, so nothing was undone.") : undo.error.message}</p>}
      {undo.data && !undo.error && <p className={styles.chatNote} role="status">{undo.data.answer ?? t("바꾸기 전 일정으로 되돌렸어요.", "Your plan is back to how it was.")}</p>}
      {message.isPending && <>
        <article className={styles.message} data-role="user"><p className={styles.messageMeta}>{t("나", "You")}</p><p className={styles.bubble}>{message.variables.text}</p></article>
        <p className={styles.pending} role="status" data-lost={chat.progress?.lost || undefined}>{progressText(chat.progress, t, ["답변을 준비하고 있어요…", "Preparing a reply…"])}</p>
      </>}
      {chat.rereading && !message.isPending && <p className={styles.chatNote} role="status">{t("답은 받았어요. 최신 일정을 다시 불러오지 못해 잠시 뒤 다시 읽고 있어요.", "The reply arrived. The latest plan did not load, so it is being read again shortly.")}</p>}
      {message.isError && <div className={styles.error} role="alert"><p>{t("메시지를 보내지 못했어요.", "The message could not be sent.")} {message.error.message}</p><button type="button" className={styles.choice} onClick={() => message.variables && message.mutate(message.variables)}>{t("다시 보내기", "Send again")}</button></div>}
    </div>
    <div className={styles.prompts}>
      {latest.away && <button type="button" className={styles.chatLatest} onClick={() => { latest.jump(); logRef.current?.focus({ preventScroll: true }); }}>{latest.unread ? t("새 메시지 · 최신 대화로 이동", "New messages · Jump to latest") : t("최신 대화로 이동", "Jump to latest")}</button>}
      <button type="button" className={styles.prompt} disabled={message.isPending} onClick={() => chat.ask(t(`${day} 하루 일정을 요약해 주세요.`, `Summarize the itinerary for ${day}.`), false, null, null, day)}>{t("하루 요약", "Day summary")}</button>
      <button type="button" className={styles.prompt} disabled={!selected || message.isPending} onClick={() => selected && chat.askAbout(selected)}>{t("선택 일정", "Selected stop")}</button>
      <button type="button" className={styles.prompt} disabled={!selected || message.isPending} onClick={() => selected && chat.ask(t(`${selected.date} ${selected.time} ${selected.title} 다음 일정을 알려 주세요.`, `What comes after ${selected.title} on ${selected.date} at ${selected.time}?`))}>{t("다음 일정", "Next stop")}</button>
      <button type="button" className={styles.prompt} disabled={message.isPending} onClick={() => chat.ask(t(`${day} 예약 표시를 알려 주세요.`, `Show booking notes for ${day}.`))}>{t("예약 표시", "Booking notes")}</button>
    </div>
    <p className={styles.chatNote}>{t("보낸 문장은 여행 상담으로 접수돼요. 일정을 바꾸면 여행계획서에 새 버전이 생기고, 예약이 걸린 일정은 바꾸기 전에 물어봐요.", "Messages are filed as trip requests. Changes create a new version of your plan, and booked stops are never changed without asking.")}</p>
  </>;
}

/**
 * `[2026-10-07 사용자 결정 — 목업 C안]` The chat bar at the bottom of the frame. Closed: a round chat button. Pressed: it grows into the bar WITHOUT focusing the field (no phone keyboard);
 * the keyboard focus goes to its close button. Pressing the field (or tabbing into it) takes the sheet to the chat pane and the message is written there. In the chat pane it stays open.
 * `[2026-10-14 사용자 결정 — ST4F-493]` In the chat pane only, a round grey ← at the bar's left (where ✕ is outside it) goes back to the schedule — the sheet head has no chat button any more.
 * `subject`: the chosen stop's place name — the field's hint asks about it (the chat answers about the chosen stop) instead of 「일정」.
 * `[2026-10-14 사용자 지시 — ST4F-493]` The field keeps the width between the buttons and grows downward line by line as the message wraps, up to three lines; past that its height stays
 * and it scrolls. Enter still sends (as the one-line field did); Shift+Enter is a new line, and the Enter the browser reports while a Korean syllable is still being composed is not a
 * send (Chrome reports that Enter once more when the syllable is done — sending on both sends the last syllable twice). On a phone (no mouse, a finger — there is no Shift+Enter)
 * Enter is always a new line and the send button sends. The hint is drawn over the empty field so a long one ends in 「…」(a textarea's own placeholder cannot).
 * `[2026-10-14 사용자 결정]` Opening, the round button becomes the send button in its place (💬 turns into ➤ — `chatSendFrom` · `chatSendTo`, CSS in trip-screen.module.css).
 */
const touchKeyboard = () => typeof matchMedia === "function" && matchMedia("(hover: none) and (pointer: coarse)").matches;

export function ChatBar({ chat, open, inChat, subject, onOpen, onClose, onEnterChat, onLeaveChat }: { chat: TripChat; open: boolean; inChat: boolean; subject: string | null; onOpen: () => void; onClose: () => void; onEnterChat: () => void; onLeaveChat: () => void }) {
  const t = useT();
  const closeButton = useRef<HTMLButtonElement>(null);
  const fab = useRef<HTMLButtonElement>(null);
  const shown = open || inChat;
  const wasShown = useRef(shown);
  const wasInChat = useRef(inChat);
  const backToFab = useRef(false);
  const field = useRef<HTMLTextAreaElement>(null);
  // The keyboard focus follows the bar: to its close button when it opens from the round button (which is gone), back to the round button when its close button shut it.
  useEffect(() => {
    if (shown && !wasShown.current && !inChat && document.activeElement === document.body) closeButton.current?.focus({ preventScroll: true });
    // `[2026-10-15 검토]` ← left the chat but the bar stays open (a draft is kept): ← is gone and ✕ stands in its place — the focus goes there, not to the page.
    if (shown && !inChat && wasInChat.current && backToFab.current) { backToFab.current = false; closeButton.current?.focus({ preventScroll: true }); }
    if (!shown && backToFab.current) { backToFab.current = false; fab.current?.focus({ preventScroll: true }); }
    wasShown.current = shown;
    wasInChat.current = inChat;
  }, [shown, inChat]);
  // The field's height follows its text (the CSS `max-height` stops it at three lines, then it scrolls) — and the width (a turned phone wraps the same text differently).
  useLayoutEffect(() => {
    const box = field.current;
    if (!box) return;
    const fit = () => { box.style.height = "auto"; box.style.height = `${box.scrollHeight}px`; };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [chat.draft, shown, inChat]);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    chat.ask(chat.draft, true);
  }
  const hint = subject ? t(`${subject}에 대해 궁금한 점을 입력하세요`, `Ask about ${subject}`) : t("일정에 대해 궁금한 점을 입력하세요", "Ask about your itinerary");
  if (!shown) {
    return <div className={styles.chatBar}><button ref={fab} type="button" className={styles.chatFab} onClick={onOpen} aria-label={t("채팅 입력 열기", "Open the chat")}><MessageCircle size={26} strokeWidth={1.7} aria-hidden="true" /></button></div>;
  }
  return <div className={styles.chatBar} data-open>
    {!inChat && <button ref={closeButton} type="button" className={styles.chatClose} onClick={() => { backToFab.current = true; onClose(); }} aria-label={t("채팅 막대 접기", "Close the chat bar")}><X size={18} strokeWidth={1.8} aria-hidden="true" /></button>}
    {inChat && <button type="button" className={styles.chatClose} onClick={() => { backToFab.current = true; onLeaveChat(); }} aria-label={t("일정으로 돌아가기", "Back to the itinerary")} title={t("일정으로 돌아가기", "Back to the itinerary")}><ArrowLeft size={18} strokeWidth={1.8} aria-hidden="true" /></button>}
    <form className={styles.chatForm} onSubmit={submit} noValidate>
      <label className="sr-only" htmlFor="trip-chat-message">{t("여행 메시지", "Travel message")}</label>
      <span className={styles.chatField}>
      <textarea ref={field} id="trip-chat-message" className={styles.chatInput} rows={1} value={chat.draft} maxLength={600} autoComplete="off" placeholder={hint} readOnly={chat.message.isPending} aria-disabled={chat.message.isPending || undefined}
        aria-invalid={Boolean(chat.inputError)} aria-describedby={chat.inputError ? "trip-chat-error" : undefined}
        onFocus={() => { if (!inChat) onEnterChat(); }}
        onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && !touchKeyboard()) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }}
        onChange={(event) => { chat.setDraft(event.target.value); if (chat.inputError) chat.setInputError(""); }} />
      {!chat.draft && <span className={styles.chatHint} aria-hidden="true">{hint}</span>}
      </span>
      <button type="submit" className={styles.chatSend} aria-disabled={chat.message.isPending || undefined} style={chat.message.isPending ? { opacity: .5, cursor: "not-allowed" } : undefined} aria-label={t("메시지 전송", "Send message")}><MessageCircle className={styles.chatSendFrom} size={18} strokeWidth={1.8} aria-hidden="true" /><Send className={styles.chatSendTo} size={18} strokeWidth={1.8} aria-hidden="true" /></button>
    </form>
    {chat.inputError && <p id="trip-chat-error" className={styles.chatError} role="alert">{chat.inputError}</p>}
  </div>;
}
