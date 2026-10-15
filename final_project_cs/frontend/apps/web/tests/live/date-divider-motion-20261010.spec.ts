import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { mockServer, noHorizontalScroll } from './helpers';
import { head, openFinished } from './plan-check-kit';
import { useMapTiles } from './capture-map-tiles';

const shots = process.env.DATE_DIVIDER_SHOTS;
test.beforeEach(async ({ request }) => { await mockServer(request).reset(); });
async function snap(page: Page, name: string) {
  if (!shots) return;
  await mkdir(shots, { recursive: true });
  await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true });
}
async function phase(target: Locator, time: number) {
  await target.evaluate((e, t) => {
    const animations = e.getAnimations({ subtree: true }).filter(a => a instanceof CSSAnimation && /dayShadow|insertLineBreathe/.test(a.animationName));
    if (!animations.length) throw new Error(`반복 애니메이션을 찾지 못했습니다: ${getComputedStyle(e, '::after').animationName}`);
    animations.forEach(a => { a.pause(); a.currentTime = t; });
  }, time);
}
async function restart(target: Locator) {
  await target.evaluate(e => {
    // Discard test-only Web Animations overrides and restore the real CSS playback controls.
    const node = e as HTMLElement;
    if (node.hasAttribute('data-insert-near')) {
      node.removeAttribute('data-insert-near'); void node.offsetWidth; node.setAttribute('data-insert-near', '');
    } else {
      node.style.display = 'none'; void node.offsetWidth; node.style.removeProperty('display');
    }
  });
}
for (const width of [375, 1280]) {
  test(`날짜 그림자는 제자리에서 잘림 없이 변하고 추가 가로선·조작 정지 유지 ${width}px`, async ({ page, request }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 812 });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    const tiles = shots ? await useMapTiles(page) : async () => {};
    const api = await openFinished(page, request, undefined, { intakeEvents: 'off' });
    await tiles();
    await expect(page.getByRole('status').filter({ hasText: '계획 확인 화면이에요' })).toHaveCount(0);
    await page.mouse.move(0, 0);
    const strip = page.getByRole('tablist', { name: '일차 고르기', includeHidden: true });
    await expect(strip).not.toHaveAttribute('data-quiet');
    const firstTab = strip.getByRole('tab').first();
    const shadow = () => firstTab.evaluate(e => {
      const c = getComputedStyle(e, '::after');
      return { opacity: Number(c.opacity), duration: c.animationDuration, animation: c.animationName, state: c.animationPlayState };
    });
    expect((await shadow()).duration).toBe('4s');
    await phase(strip, 0);
    const body = page.locator('div[class*=sheetBody]');
    const origin = await strip.boundingBox(), bodyOrigin = await body.boundingBox();
    const marker = strip.locator('[class*=dayMarker]');
    const markerOrigin = await marker.boundingBox();
    const dimShadow = await shadow();
    const tabOrigin = await firstTab.boundingBox();
    const clearances = [];
    // Check the entire cycle, including both extremes, against the actual opaque handle and scroll clip.
    for (let time = 0; time <= 4000; time += 250) {
      await phase(strip, time);
      expect(await strip.boundingBox()).toEqual(origin);
      expect(await firstTab.boundingBox()).toEqual(tabOrigin);
      expect(await marker.boundingBox()).toEqual(markerOrigin);
      expect(await body.boundingBox()).toEqual(bodyOrigin);
      const clearance = await strip.evaluate(e => {
        const r = e.getBoundingClientRect(), handle = e.closest('section')!.querySelector('button')!.getBoundingClientRect();
        const tabs = Array.from(e.querySelectorAll<HTMLElement>('[role=tab]')).map(tab => {
          const t = tab.getBoundingClientRect();
          const visible = t.left >= r.left && t.right <= r.right;
          return { top: t.top-r.top, bottom: r.bottom-t.bottom, handleGap: t.top-handle.bottom,
            topHit: !visible || tab.contains(document.elementFromPoint(t.x+t.width/2,t.top+.5)),
            bottomHit: !visible || tab.contains(document.elementFromPoint(t.x+t.width/2,t.bottom-.5)) };
        });
        const actions = e.closest('section')!.querySelector<HTMLElement>('[class*=dayTools]')!.getBoundingClientRect();   // `[ST4F-494]` 단추는 첫 날짜 제목 줄 오른쪽
        const tabBottom = Math.max(...Array.from(e.querySelectorAll('[role=tab]')).map(tab => tab.getBoundingClientRect().bottom));
        return {time: performance.now(),tabs,actionGap:actions.top-tabBottom};
      });
      for (const t of clearance.tabs) {
        expect(t.top).toBeGreaterThanOrEqual(12); expect(t.bottom).toBeGreaterThanOrEqual(12);
        expect(t.handleGap).toBeGreaterThanOrEqual(10); expect(t.topHit).toBe(true); expect(t.bottomHit).toBe(true);
      }
      expect(clearance.actionGap).toBeGreaterThanOrEqual(12);
      clearances.push(clearance);
    }
    await phase(strip, 2000);
    expect((await shadow()).opacity - dimShadow.opacity).toBeGreaterThan(.4);
    await test.info().attach(`date-clearance-${width}`, {body:JSON.stringify(clearances),contentType:'application/json'});
    await snap(page, `date-shadow-${width}`);
    await restart(strip);
    const stripBox = (await strip.boundingBox())!;
    await page.mouse.move(stripBox.x + stripBox.width / 2, stripBox.y + stripBox.height / 2);
    await expect.poll(async () => (await shadow()).state).toBe('paused');
    await expect.poll(() => strip.evaluate(e => e.getAnimations({subtree:true}).filter(a => a instanceof CSSAnimation && /dayShadow/.test(a.animationName)).every(a => a.playState === 'paused'))).toBe(true);
    await page.mouse.move(0, 0);
    await firstTab.focus(); expect((await shadow()).state).toBe('paused');
    const handle = page.getByRole('button', { name: '목록 높이 바꾸기' });
    await handle.focus();
    for (let i = 0; i < 5; i++) await handle.press('ArrowUp');
    await head(page, '올리브영').click(); await page.mouse.move(0, 0);
    await expect(strip).not.toHaveAttribute('data-quiet');
    const seam = page.locator('[data-insert-near]'), add = seam.getByRole('button');
    await expect(add).toBeVisible();
    const line = () => seam.evaluate(e => {
      const c = getComputedStyle(e, '::after'), r = e.getBoundingClientRect();
      const button = e.querySelector('button')!.getBoundingClientRect();
      return { opacity: Number(c.opacity), height: c.height, left: c.left, right: c.right, width: r.width, y: r.y + Number.parseFloat(c.top), lineTop: Number.parseFloat(c.top), start: e.hasAttribute('data-insert-start'), centerX: r.x + r.width / 2, buttonX: button.x + button.width / 2, buttonY: button.y + button.height / 2, buttonWidth: button.width, buttonHeight: button.height, animation: c.animationName, state: c.animationPlayState };
    });
    await phase(seam, 0); const dim = await line();
    await phase(seam, 1500); const bright = await line();
    expect(bright.opacity - dim.opacity).toBeGreaterThan(.45);
    expect(bright.height).toBe('1px'); expect(bright.left).toBe('0px'); expect(bright.right).toBe('0px');
    expect(bright.lineTop).toBe(bright.start ? 24 : 0);
    expect(bright.buttonX).toBeCloseTo(bright.centerX, 1); expect(bright.buttonY).toBeCloseTo(bright.y, 1);
    expect(bright.buttonWidth).toBe(44); expect(bright.buttonHeight).toBe(44);
    expect(bright.buttonX).toBe(dim.buttonX); expect(bright.buttonY).toBe(dim.buttonY);
    await phase(strip, 2000); await snap(page, `divider-${width}`);
    if (shots && width === 375 && process.env.DATE_DIVIDER_PREVIEW === '1') {
      const frames = '.tmp/date-shadow-preview-frames'; await mkdir(frames, { recursive: true });
      // Capture the stationary date shadows and approved line over their common 12s cycle.
      for (let i = 0; i < 60; i++) {
        await phase(strip, i * 200 % 4000); await phase(seam, i * 200 % 3000);
        await page.screenshot({ path: `${frames}/${String(i).padStart(3, '0')}.png` });
      }
    }
    await restart(strip); await restart(seam);
    await add.hover(); expect((await line()).state).toBe('paused');
    await add.focus(); expect((await line()).state).toBe('paused');
    const label = (await add.getAttribute('aria-label'))!;
    await add.click(); await expect(page.getByRole('searchbox', { name: '장소 검색', exact: true })).toBeFocused();
    await page.getByRole('button', { name: '일정 추가 취소', exact: true }).click();
    await expect(page.getByRole('button', { name: label, exact: true })).toBeFocused();
    await page.mouse.move(0, 0);
    await handle.focus();
    await restart(strip);
    await body.hover(); await page.mouse.wheel(0, 80);
    await expect(strip).toHaveAttribute('data-quiet'); await expect(strip).toHaveAttribute('inert', '');
    await expect(strip).not.toHaveAttribute('data-quiet'); expect((await shadow()).state).toBe('running');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect(strip).toHaveCSS('animation-name', 'none'); await expect(strip).toHaveCSS('transform', 'none');
    expect((await shadow()).animation).toBe('none'); expect((await shadow()).opacity).toBe(.35);
    const staticSeam = page.locator('[data-insert-near]');
    expect(await staticSeam.evaluate(e => getComputedStyle(e, '::after').animationName)).toBe('none');
    expect(Number(await staticSeam.evaluate(e => getComputedStyle(e, '::after').opacity))).toBe(.6);
    await snap(page, `reduced-motion-${width}`);
    expect(await api.received('POST', '/edits')).toHaveLength(0); await noHorizontalScroll(page);
  });
}
