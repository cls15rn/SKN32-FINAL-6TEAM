"use client";

import { useCallback, useEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type PointerEvent, type RefObject, type UIEvent, type WheelEvent } from "react";

/** Wait until scrolling, including its inertia, has stopped before restoring the floating day controls. */
export const DAY_STRIP_IDLE_MS = 800;

/**
 * `tools` (optional, `[2026-10-14]` the trip screen's ✎ beside the day chips): hidden with the strip, and pressing it is pressing the strip — it does not hide them.
 */
export function useQuietDayStrip(strip: RefObject<HTMLElement | null>, enabled: boolean, suppressed = false, listBox?: RefObject<HTMLElement | null>, tools?: RefObject<HTMLElement | null>) {
  const [busy, setBusy] = useState(false);
  const [atTop, setAtTop] = useState(Boolean(listBox));
  const [focused, setFocused] = useState(false);
  const pointers = useRef(new Set<number>());
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const reveal = useCallback(() => { clearTimeout(timer.current); setBusy(false); }, []);
  const settle = useCallback(() => {
    clearTimeout(timer.current);
    if (!pointers.current.size) timer.current = setTimeout(() => setBusy(false), DAY_STRIP_IDLE_MS);
  }, []);
  const activity = useCallback((target: EventTarget | null) => {
    if (!enabled) return;
    const list = listBox ? listBox.current : null;
    const keepAtTop = Boolean(list && target instanceof Node && list.contains(target) && list.scrollTop <= 1);
    setAtTop(keepAtTop);
    if (keepAtTop) { reveal(); return; }
    if (listBox) setFocused(false);
    setBusy(true);
    settle();
  }, [enabled, listBox, reveal, settle]);
  const inStrip = useCallback((target: EventTarget | null) => target instanceof Node && Boolean(strip.current?.contains(target) || (tools && tools.current?.contains(target))), [strip, tools]);

  useEffect(() => {
    // Suppression changes cancel the old idle timer in cleanup; resume it when the list is reopened.
    if (!suppressed) settle();
    const release = (event: globalThis.PointerEvent) => { if (pointers.current.delete(event.pointerId)) settle(); };
    const blur = () => { pointers.current.clear(); settle(); };
    const tab = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Tab" || suppressed) return;
      // The header is outside this sheet. Restore the tabs before the browser calculates its next focus target.
      if (strip.current) strip.current.inert = false;
      if (tools?.current) tools.current.inert = false;
      reveal();
    };
    // A drag may end outside the sheet or map; never leave the day controls permanently hidden.
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    window.addEventListener("blur", blur);
    window.addEventListener("keydown", tab, true);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
      window.removeEventListener("blur", blur);
      window.removeEventListener("keydown", tab, true);
      clearTimeout(timer.current);
    };
  }, [settle, strip, tools, reveal, suppressed]);

  const onPointerDownCapture = useCallback((event: PointerEvent<HTMLElement>) => {
    if (!enabled || inStrip(event.target)) return;
    pointers.current.add(event.pointerId);
    activity(event.target);
  }, [enabled, inStrip, activity]);
  const onPointerMoveCapture = useCallback((event: PointerEvent<HTMLElement>) => {
    if (pointers.current.has(event.pointerId)) activity(event.target);
  }, [activity]);
  const onWheelCapture = useCallback((event: WheelEvent<HTMLElement>) => { if (!inStrip(event.target)) activity(event.target); }, [inStrip, activity]);
  const onScrollCapture = useCallback((event: UIEvent<HTMLElement>) => { if (!inStrip(event.target)) activity(event.target); }, [inStrip, activity]);
  const onKeyDownCapture = useCallback((event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Tab" || inStrip(event.target)) { reveal(); return; }
    if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) activity(event.target);
  }, [inStrip, reveal, activity]);
  const onFocusCapture = useCallback(() => { setFocused(true); reveal(); }, [reveal]);
  const onBlurCapture = useCallback((event: FocusEvent<HTMLElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
  }, []);

  return {
    hidden: enabled && busy && !focused,
    atTop,
    handlers: { onPointerDownCapture, onPointerMoveCapture, onWheelCapture, onScrollCapture, onKeyDownCapture },
    focusHandlers: { onFocusCapture, onBlurCapture },
  };
}
