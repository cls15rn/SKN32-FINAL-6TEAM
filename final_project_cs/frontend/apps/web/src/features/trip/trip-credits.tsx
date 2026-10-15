"use client";

import { useEffect, useLayoutEffect, useRef, useState, type Ref } from "react";
import { Clapperboard } from "lucide-react";
import { useT } from "@/lib/settings";
import { creditNumbers, drawEndPicture, type CreditFacts } from "./ending-credits";
import { useModalSheet } from "./sheet-modal";
import styles from "./trip-credits.module.css";

/** How fast the credits roll (px a second — the mockup's speed). */
const ROLL_PX_PER_S = 46;
/** `[2026-10-15 사용자 결정]` The end screen's one line: the mockup's sentence for now (later a sentence written for the trip). */
const COOKIE_LINE: [string, string] = ["하루만 더 휴가 쓸걸.", "Should have taken one more day off."];

/** `[2026-10-15 사용자 결정 — ST4F-496]` At the top of 「전체」 once the trip is over: its name, days and numbers, and 「엔딩 크레딧 보기」. */
export function EndCard({ facts, onOpen, buttonRef }: { facts: CreditFacts; onOpen: () => void; buttonRef: Ref<HTMLButtonElement> }) {
  const t = useT();
  return <article className={styles.endCard} aria-labelledby="trip-end-title">
    <p className={styles.endKick}><Clapperboard size={14} strokeWidth={2} aria-hidden="true" />{t("여행이 끝났어요", "The trip is over")}</p>
    <h4 id="trip-end-title">{facts.title}{facts.dates && <small> · {facts.dates}</small>}</h4>
    <p className={styles.endMeta}>{creditNumbers(facts, t).filter(([label]) => label !== t("이동 거리", "Distance")).map(([label, value]) => `${label} ${value}`).join(" · ")}</p>
    <button ref={buttonRef} type="button" className={styles.endButton} onClick={onOpen}>{t("엔딩 크레딧 보기", "Watch the end credits")}</button>
  </article>;
}

type Shared = "idle" | "saved" | "failed";

/**
 * `[2026-10-15 사용자 결정 — ST4F-496]` The ending credits over the trip screen: they roll up (「건너뛰기」 skips; with motion reduced the end screen comes at once), then the end screen —
 * the trip's name, the cookie line, its numbers and 「다시 보기 · 공유하기 · 닫기」. 「공유하기」 shares a picture of the end screen without its buttons (made when the end screen comes:
 * a phone opens its share sheet only right after the press) — the phone's share sheet when it takes pictures, else the picture is saved.
 */
export function TripCredits({ facts, skipAnimation, onClose }: { facts: CreditFacts; skipAnimation: boolean; onClose: () => void }) {
  const t = useT();
  const { heading, shade, sheet, keys } = useModalSheet(onClose);
  const still = skipAnimation || (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  const [phase, setPhase] = useState<"roll" | "end">(still ? "end" : "roll");
  const view = useRef<HTMLDivElement>(null);
  const roll = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const [picture, setPicture] = useState<File | null | undefined>(undefined);
  const drawing = useRef(false);
  const [shared, setShared] = useState<Shared>("idle");
  const numbers = creditNumbers(facts, t);
  const line = t(...COOKIE_LINE);

  useLayoutEffect(() => {
    if (phase !== "roll" || !view.current || !roll.current) return;
    if (typeof roll.current.animate !== "function") { setPhase("end"); return; }
    const height = view.current.clientHeight, length = roll.current.scrollHeight;
    const animation = roll.current.animate([{ transform: `translateY(${height}px)` }, { transform: `translateY(${-length}px)` }],
      { duration: ((height + length) / ROLL_PX_PER_S) * 1000, easing: "linear", fill: "forwards" });
    let timer: ReturnType<typeof setTimeout> | undefined;
    animation.onfinish = () => { timer = setTimeout(() => setPhase("end"), 300); };
    return () => { animation.cancel(); clearTimeout(timer); };
  }, [phase]);

  useEffect(() => { if (phase === "end") close.current?.focus({ preventScroll: true }); }, [phase]);
  // The picture is made once, as soon as the end screen is there: the share sheet must open in the press itself.
  useEffect(() => {
    if (phase !== "end" || drawing.current || !shade.current) return;
    drawing.current = true;
    const css = getComputedStyle(shade.current);
    drawEndPicture({ heading: facts.title, dates: facts.dates, kicker: "COOKIE", line, cells: numbers, brand: "triPilot" }, {
      background: css.getPropertyValue("--color-inverse").trim(), ink: css.getPropertyValue("--color-on-inverse").trim(),
      accent: css.getPropertyValue("--color-credits-accent").trim(), font: css.fontFamily,
    }).then((blob) => setPicture(blob ? new File([blob], "tripilot-credits.png", { type: "image/png" }) : null), () => setPicture(null));
  }, [phase, facts, line, numbers, shade]);

  async function share() {
    if (!picture) return;
    const data = { files: [picture], title: facts.title };
    if (typeof navigator !== "undefined" && navigator.canShare?.(data)) {
      try { await navigator.share(data); setShared("idle"); }
      catch (error) { setShared(error instanceof DOMException && error.name === "AbortError" ? "idle" : "failed"); }
      return;
    }
    // No share sheet that takes pictures here (a computer): the picture is saved instead.
    const url = URL.createObjectURL(picture);
    const link = document.createElement("a");
    link.href = url;
    link.download = picture.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1_000);
    setShared("saved");
  }

  return <div ref={shade} className={styles.shade} onKeyDown={keys}>
    <section ref={sheet} className={styles.credits} role="dialog" aria-modal="true" aria-labelledby="trip-credits-title" data-phase={phase}>
      <h2 id="trip-credits-title" ref={heading} tabIndex={-1} className="sr-only">{t("엔딩 크레딧", "End credits")} · {facts.title}</h2>
      {phase === "roll" ? <>
        <button type="button" className={styles.skip} onClick={() => setPhase("end")}>{t("건너뛰기", "Skip")}</button>
        <div ref={view} className={styles.view}>
          <div ref={roll} className={styles.roll}>
            <p className={styles.small}>triPilot presents</p>
            <p className={styles.title}>{facts.title}</p>
            <p className={styles.small}>{facts.dates}</p>
            {facts.days.map((day) => <section key={day.label}><h3>{day.label}</h3>{day.titles.map((title, at) => <p key={at}>{title}</p>)}</section>)}
            <section><h3>{t("바뀐 장면", "What changed")}</h3>
              {facts.changes.length === 0 ? <p>{t("바뀐 일정 없이 끝났어요", "It ended without a change")}</p>
                : facts.changes.map((change) => <p key={change.version}>{change.reason}<br /><small>{[change.at, ...change.causes].join(" · ")}</small></p>)}</section>
            <section><h3>{t("숫자로 본 여행", "The trip in numbers")}</h3>
              {numbers.map(([label, value]) => <p key={label} className={styles.pair}><span>{label}</span><span>{value}</span></p>)}</section>
            {facts.sources.length > 0 && <section><h3>{t("자료 제공", "Data")}</h3>{facts.sources.map((source) => <p key={source}>{source}</p>)}</section>}
            <section><h3>{t("검증 · 감시", "Checked and watched by")}</h3><p>triPilot</p></section>
          </div>
        </div>
      </> : <div className={styles.end}>
        <p className={styles.endTitle}>{facts.title}<small>{facts.dates}</small></p>
        <p className={styles.cookie}>COOKIE</p>
        <p className={styles.line}>{line}</p>
        <div className={styles.sum}>{numbers.map(([label, value]) => <div key={label}><small>{label}</small><b>{value}</b></div>)}</div>
        <div className={styles.buttons}>
          {/* `[2026-10-15 검토]` With motion reduced (or animation skipped) the credits never roll, so there is nothing to replay. */}
          {!still && <button type="button" onClick={() => { setShared("idle"); setPhase("roll"); }}>{t("다시 보기", "Replay")}</button>}
          <button type="button" data-primary disabled={!picture} onClick={() => void share()} aria-describedby="trip-credits-share">{t("공유하기", "Share")}</button>
          <button ref={close} type="button" onClick={onClose}>{t("닫기", "Close")}</button>
        </div>
        <p id="trip-credits-share" className={styles.shareNote} role="status">{
          picture === undefined ? t("공유할 사진을 만들고 있어요…", "Making the picture to share…")
            : picture === null ? t("이 브라우저에서는 공유할 사진을 만들지 못했어요.", "This browser could not make the picture to share.")
              : shared === "saved" ? t("사진을 내려받았어요.", "The picture was saved.")
                : shared === "failed" ? t("공유 창을 열지 못했어요 · 다시 눌러 주세요.", "Could not open the share sheet · press again.") : ""
        }</p>
      </div>}
    </section>
  </div>;
}
