"use client";

import { useContext, useEffect, useRef, type KeyboardEvent } from "react";
import { OverlayRoot } from "@/components/layout/overlay-root";

/**
 * A sheet from below over the trip screen (「공유하기」 · `[2026-10-14]` 「지도 앱으로 열기」): everything behind it goes inert, the focus starts on its heading and stays inside it,
 * and Esc closes it. The caller puts `shade` · `sheet` · `heading` on its elements and `keys` on the shade; it gives the focus back itself when the sheet closes.
 */
export function useModalSheet(onClose: () => void) {
  const heading = useRef<HTMLHeadingElement>(null);
  const shade = useRef<HTMLDivElement>(null);
  const sheet = useRef<HTMLElement>(null);
  const overlayRoot = useContext(OverlayRoot);
  useEffect(() => {
    const behind: { element: HTMLElement; inert: boolean }[] = [];
    let branch: HTMLElement | null = shade.current;
    // The sheet is nested inside the trip main. Disable sibling branches all the way
    // to the page root, including the floating header and chat bar.
    while (branch && branch !== document.body) {
      const parent: HTMLElement | null = branch.parentElement;
      for (const element of Array.from(parent?.children ?? [])) {
        if (element === branch || element === overlayRoot || !(element instanceof HTMLElement) || element.matches("script, style")) continue;
        behind.push({ element, inert: element.inert });
        element.inert = true;
      }
      branch = parent;
    }
    heading.current?.focus({ preventScroll: true });
    return () => { behind.forEach(({ element, inert }) => { element.inert = inert; }); };
  }, [overlayRoot]);
  function keys(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); return; }
    if (event.key !== "Tab" || !sheet.current) return;
    const stops = Array.from(sheet.current.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), [tabindex="0"]')).filter(element => !element.closest("[inert]"));
    const first = stops[0], last = stops.at(-1);
    if (!first || !last) return;
    const at = document.activeElement;
    if (event.shiftKey && (at === first || at === heading.current || !sheet.current.contains(at))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (at === last || !sheet.current.contains(at))) { event.preventDefault(); first.focus(); }
  }
  return { heading, shade, sheet, keys };
}
