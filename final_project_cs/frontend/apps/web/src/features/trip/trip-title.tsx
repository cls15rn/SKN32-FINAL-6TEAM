"use client";

import { useContext, useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Download, ExternalLink, FileText, Link2, MessageCircle, MessageSquare, MoreHorizontal, Pencil, Send, Share2, X } from "lucide-react";
import { HeaderSlot } from "@/components/layout/device-frame";
import { Act } from "@/features/plan-check/parts";
import pc from "@/features/plan-check/plan-check.module.css";
import { planDownloadUrl } from "@/lib/live/plan-download";
import type { BusToast } from "@/lib/toast-bus";
import { useT } from "@/lib/settings";
import type { Trip } from "./model";
import { useModalSheet } from "./sheet-modal";
import styles from "./trip-screen.module.css";

const PLAN_NOTE: [string, string] = ["로그인 없이 열리는 내 여행 링크예요. 링크를 아는 사람은 누구나 볼 수 있어요.", "This link opens without logging in. Anyone who has it can view your plan."];

/**
 * `[2026-10-07 사용자 결정 — 목업 C안 B: 제목 펼침]` The trip's name in the header (the plan check's title chip): pressed, it grows to the whole line with ▴ and a pencil at its right end and the row
 * 「여행계획서 열기 · 공유하기」 opens under it; the name pressed again (or the pencil) turns it into a field (Enter or leaving it saves, Esc keeps the name). It does not fold by itself (there are buttons
 * under it): ▴, a press outside and Esc fold it.
 * ★The server has no way to rename a registered trip yet: saving says so and the name stays (`onSave` answers) — nothing is shown as renamed.
 */
export function TripTitle({ title, open, editing, onOpen, onClose, onEdit, onEditEnd, onSave }: {
  title: string; open: boolean; editing: boolean;
  onOpen: () => void; onClose: () => void; onEdit: () => void; onEditEnd: () => void; onSave: (name: string) => void;
}) {
  const t = useT();
  const slot = useContext(HeaderSlot);
  const [draft, setDraft] = useState(title);
  const input = useRef<HTMLInputElement>(null);
  const cancelled = useRef(false);
  useEffect(() => { if (editing) { cancelled.current = false; input.current?.select(); } }, [editing]);
  if (!slot) return null;
  const startEdit = () => { setDraft(title); onEdit(); };
  function endEdit(restoreFocus = false) {
    onEditEnd();
    if (restoreFocus) requestAnimationFrame(() => document.getElementById("trip-title-button")?.focus({ preventScroll: true }));
  }
  function commit(restoreFocus = false) {
    if (cancelled.current) return;
    cancelled.current = true;
    const next = draft.trim();
    endEdit(restoreFocus);
    if (next && next !== title) onSave(next);
  }
  const keys = (event: KeyboardEvent<HTMLElement>) => { if (event.key === "Escape" && open && !editing) { event.preventDefault(); onClose(); } };
  return createPortal(<div className={pc.headInfo} data-title data-trip-title onKeyDown={keys}>
    {editing
      ? <form className={pc.titleForm} onSubmit={(event) => { event.preventDefault(); commit(true); }}>
        <input ref={input} className={pc.titleInput} value={draft} maxLength={80} aria-label={t("계획 이름", "Plan name")} autoComplete="off" enterKeyHint="done"
          onChange={(event) => setDraft(event.target.value)} onBlur={() => commit()}
          onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); cancelled.current = true; endEdit(true); } }} />
      </form>
      : <h1 className={`${pc.headTitle} ${styles.tripTitle}`} data-armed={open || undefined}>
        <button type="button" id="trip-title-button" className={pc.titleButton} aria-expanded={open} aria-label={t(`계획 이름 · ${title}`, `Plan name · ${title}`)}
          onClick={() => open ? startEdit() : onOpen()}><span>{title}</span></button>
        <button type="button" className={styles.titleCaret} aria-expanded={open} aria-controls="trip-title-menu"
          aria-label={open ? t("여행계획서 열기 · 공유하기 접기", "Hide plan and share") : t("여행계획서 열기 · 공유하기 펼치기", "Show plan and share")}
          onClick={() => open ? onClose() : onOpen()}><ChevronDown size={16} strokeWidth={2} aria-hidden="true" /></button>
        {open && <button type="button" className={pc.titleEdit} onClick={startEdit} aria-label={t(`계획 이름 바꾸기 · 지금 이름은 ${title}`, `Rename the plan · now ${title}`)}>
          <Pencil size={15} strokeWidth={1.8} aria-hidden="true" /></button>}
      </h1>}
  </div>, slot);
}

/** The row under the opened title: 「여행계획서 열기」 (a new tab) and 「공유하기」. Without a plan link from the server both say why. */
export function TitleMenu({ planUrl, onShare, onDone, explain }: { planUrl: string | null; onShare: () => void; onDone: () => void; explain: (why: string) => void }) {
  const t = useT();
  const none = planUrl ? null : t("서버가 아직 여행계획서 링크를 주지 않았어요.", "The server has not given a plan link yet.");
  return <div id="trip-title-menu" className={styles.titleMenu} role="group" aria-label={t("여행계획서", "Trip plan")} data-trip-title>
    {planUrl
      ? <a className={styles.menuButton} href={planUrl} target="_blank" rel="noopener noreferrer" onClick={onDone}><ExternalLink size={15} strokeWidth={1.8} aria-hidden="true" />{t("여행계획서 열기", "Open your trip plan")}</a>
      : <Act className={styles.menuButton} why={none} explain={explain} onPress={onDone}><ExternalLink size={15} strokeWidth={1.8} aria-hidden="true" />{t("여행계획서 열기", "Open your trip plan")}</Act>}
    <Act id="trip-title-share" className={styles.menuButton} data-primary why={none} explain={explain} onPress={onShare}><Share2 size={15} strokeWidth={1.8} aria-hidden="true" />{t("공유하기", "Share")}</Act>
  </div>;
}

const monthDay = (date: string) => `${date.slice(5, 7)}.${date.slice(8, 10)}`;

/**
 * `[2026-10-07 목업 C안]` 「공유하기」: a sheet from below like a phone's share sheet — where to send the plan link, and 「파일로 내려받기」 (the server's own file, `?download=1`).
 * What each target does here (the mockup asked to check it when built):
 *   - 메시지: an `sms:` address with the link; 텔레그램: its share address (a new tab); 링크 복사: the clipboard; 더보기: the browser's own share sheet (`navigator.share`).
 *   - 디스코드: no address sends to Discord from the web — the browser's share sheet (where Discord is installed), else it says to copy the link.
 *   - 카카오톡: Kakao sharing needs an app key this app does not have yet — it says so (「준비 중」), nothing is sent.
 */
export function TripShare({ trip, title, planUrl, onClose, notify }: { trip: Trip; title: string; planUrl: string; /** `finished`: the plan was sent or saved (the title row folds too). */ onClose: (finished?: boolean) => void; notify: (toast: BusToast) => void }) {
  const t = useT();
  const { heading, shade, sheet, keys } = useModalSheet(onClose);
  const days = [...new Set(trip.stops.map((stop) => stop.date))].sort();
  const span = days.length ? `${monthDay(days[0])}${days.length > 1 ? ` – ${monthDay(days.at(-1)!)}` : ""} · ${t(`${days.length}일`, `${days.length} day${days.length > 1 ? "s" : ""}`)} · ${t(`${trip.stops.length}개 일정`, `${trip.stops.length} stops`)} · ` : "";
  const text = t(`${title} — 여행계획서`, `${title} — trip plan`);
  const download = planDownloadUrl(planUrl);
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  const explain = (why: string) => notify({ text: why });
  const done = (toast?: BusToast) => { onClose(true); if (toast) notify(toast); };
  const shareSheet = (noSheet: string) => {
    if (!canShare) { explain(noSheet); return; }
    navigator.share({ title, text, url: planUrl }).then(() => done(), (error: unknown) => { if (!(error instanceof DOMException && error.name === "AbortError")) explain(t("공유 창을 열지 못했어요 · 링크 복사로 보내 주세요.", "Could not open the share sheet · copy the link instead.")); });
  };
  const copy = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(planUrl);
      done({ text: t("링크를 복사했어요", "Link copied"), sub: t(...PLAN_NOTE) });
    } catch {
      explain(t("링크를 복사하지 못했어요 · 여행계획서를 열어 주소를 복사해 주세요.", "Could not copy the link · open the plan and copy its address."));
    }
  };
  const app = (key: string, label: string, icon: ReactNode, tone: string) => ({ key, label, icon, tone });
  const targets = [
    app("kakao", t("카카오톡", "KakaoTalk"), <MessageCircle size={24} strokeWidth={1.8} aria-hidden="true" />, "kakao"),
    app("discord", t("디스코드", "Discord"), <MessageSquare size={24} strokeWidth={1.8} aria-hidden="true" />, "discord"),
    app("sms", t("메시지", "Messages"), <MessageCircle size={24} strokeWidth={1.8} aria-hidden="true" />, "sms"),
    app("telegram", t("텔레그램", "Telegram"), <Send size={24} strokeWidth={1.8} aria-hidden="true" />, "telegram"),
    app("copy", t("링크 복사", "Copy link"), <Link2 size={24} strokeWidth={1.8} aria-hidden="true" />, "plain"),
    app("more", t("더보기", "More"), <MoreHorizontal size={24} strokeWidth={1.8} aria-hidden="true" />, "plain"),
  ];
  const press = (key: string) => {
    if (key === "kakao") explain(t("카카오톡 공유는 준비 중이에요 · 카카오 앱 키가 아직 없어요. 링크 복사로 보내 주세요.", "KakaoTalk sharing is coming · there is no Kakao app key yet. Copy the link instead."));
    else if (key === "discord") shareSheet(t("이 브라우저에서는 디스코드로 바로 보낼 수 없어요 · 링크를 복사해 보내 주세요.", "This browser cannot send to Discord directly · copy the link instead."));
    else if (key === "copy") void copy();
    else if (key === "more") shareSheet(t("이 브라우저에는 공유 창이 없어요 · 링크 복사로 보내 주세요.", "This browser has no share sheet · copy the link instead."));
  };
  const href = (key: string) => key === "sms" ? `sms:?&body=${encodeURIComponent(`${text} ${planUrl}`)}`
    : key === "telegram" ? `https://t.me/share/url?url=${encodeURIComponent(planUrl)}&text=${encodeURIComponent(text)}` : null;
  return <div ref={shade} className={styles.shareShade} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }} onKeyDown={keys}>
    <section ref={sheet} className={styles.shareSheet} role="dialog" aria-modal="true" aria-labelledby="trip-share-title" data-trip-title>
      <span className={styles.shareGrip} aria-hidden="true" />
      <header className={styles.shareHead}>
        <h2 id="trip-share-title" ref={heading} tabIndex={-1}>{t("여행계획서 공유", "Share the trip plan")}</h2>
        <button type="button" className={styles.shareClose} onClick={() => onClose()} aria-label={t("닫기", "Close")}><X size={20} strokeWidth={1.8} aria-hidden="true" /></button>
      </header>
      <div className={styles.shareLink}><FileText size={22} strokeWidth={1.8} aria-hidden="true" /><span><strong>{title}</strong><small>{span}{t("여행계획서 링크", "plan link")}</small></span></div>
      <p className={styles.shareNote}>{t(...PLAN_NOTE)}</p>
      <ul className={styles.shareApps} aria-label={t("보낼 곳", "Send to")}>{targets.map((target) => {
        const link = href(target.key);
        const face = <><span className={styles.shareIcon} data-app={target.tone} aria-hidden="true">{target.icon}</span>{target.label}</>;
        return <li key={target.key}>{link
          ? <a className={styles.shareApp} href={link} target={target.key === "telegram" ? "_blank" : undefined} rel={target.key === "telegram" ? "noopener noreferrer" : undefined} onClick={() => done()}>{face}</a>
          : <button type="button" className={styles.shareApp} onClick={() => press(target.key)}>{face}</button>}</li>;
      })}</ul>
      {download && <a className={styles.shareDownload} href={download} data-plan-download onClick={() => done()}>
        <Download size={20} strokeWidth={1.8} aria-hidden="true" />{t("파일로 내려받기", "Download as a file")}<small>{`triPilot-${title}.html`}</small></a>}
    </section>
  </div>;
}
