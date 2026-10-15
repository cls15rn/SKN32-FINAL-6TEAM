import { expect, test, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { mockServer, noHorizontalScroll } from './helpers';
import { card, head, openFinished } from './plan-check-kit';
import { useMapTiles } from './capture-map-tiles';

const shots = process.env.STACK_DATE_SHOTS;
test.beforeEach(async ({ request }) => { await mockServer(request).reset(); });
async function idle(page: Page) {
  await expect(page.getByRole('status').filter({ hasText: '계획 확인 화면이에요' })).toHaveCount(0);
  await page.mouse.move(0, 0);
}
async function snap(page: Page, name: string) {
  if (!shots) return;
  await mkdir(shots, { recursive: true });
  await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true });
}
for (const width of [375, 1280]) {
  test(`스택 아이콘 안 실제 개수·좌우 순서·지도 이동 중 개수 유지 ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 812 }); await page.emulateMedia({ reducedMotion: 'reduce' });
    const tiles = shots ? await useMapTiles(page) : async () => {};
    await openFinished(page, request, v => {
      v.review.items[1].place.latitude = 37.5796; v.review.items[1].place.longitude = 126.94;
      v.review.items[2].place.latitude = 37.5796; v.review.items[2].place.longitude = 127.02;
      for (let i = 3; i <= 4; i++) v.review.items.push({ ...v.review.items[2], id: `0-${i}`, index: i, title: `종로 일정 ${i}`, place: { ...v.review.items[2].place, longitude: 127.02 + i / 100000 } });
    }, { intakeEvents: 'off' }); await tiles(); await idle(page);
    await head(page, '경복궁 관람').click();
    const map = page.getByRole('region', { name: '여행 지도' });
    await page.getByRole('button', { name: '지도 단추 펼치기', exact: true }).click();
    for (let i = 0; i < 3; i++) {
      await page.getByRole('button', { name: '확대', exact: true }).click();
      await expect(map).not.toHaveAttribute('data-moving');
    }
    await page.getByRole('button', { name: '지도 단추 접기', exact: true }).click();
    const stack = page.locator('[data-edge-chip][data-multiple]');
    await expect(stack).toHaveCount(1); await expect(stack).toContainText('×3');
    const badge = stack.locator(':scope > span');
    await expect(badge).toHaveCSS('background-color', 'rgb(32, 32, 32)');
    const geometry = await badge.evaluate(e => {
      const r = e.getBoundingClientRect();
      return Array.from(e.children).every(c => { const b = c.getBoundingClientRect(); return b.left >= r.left && b.right <= r.right; });
    }); expect(geometry).toBe(true);
    const bounds = await stack.boundingBox(), frame = await map.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(frame!.x); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(frame!.x + frame!.width);
    await idle(page); await snap(page, `stack-${width}`);
    await page.mouse.move(frame!.x + 110, frame!.y + 140); await page.mouse.down();
    await page.mouse.move(frame!.x + 130, frame!.y + 145, { steps: 5 });
    await expect(map).toHaveAttribute('data-moving', 'true');
    await expect(stack).toContainText('×3'); await expect(stack.locator('small')).toHaveCount(0);
    await page.mouse.up(); await noHorizontalScroll(page);
  });

  test(`일반 카드 테두리(10-13 ④)와 선택·검토·수정 상태 테두리 ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 812 }); await page.emulateMedia({ reducedMotion: 'reduce' });
    const tiles = shots ? await useMapTiles(page) : async () => {};
    await openFinished(page, request, undefined, { intakeEvents: 'off' }); await tiles(); await idle(page);
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
    await expect(card(page, '경복궁 관람')).toHaveCSS('border-top-color', 'rgb(195, 205, 198)');
    await expect(card(page, '올리브영')).not.toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await expect(card(page, '광장시장')).not.toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)');
    await snap(page, `cards-${width}`);
    await head(page, '경복궁 관람').focus();
    await expect(card(page, '경복궁 관람')).not.toHaveCSS('border-top-color', 'rgb(195, 205, 198)');
    await head(page, '경복궁 관람').click(); await head(page, '경복궁 관람').click();
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur(); });
    await expect(card(page, '경복궁 관람')).toHaveCSS('border-top-color', 'rgb(195, 205, 198)');
    await noHorizontalScroll(page);
  });

  test(`날짜 여백·최상단 유지·스크롤 숨김·최상단 즉시 복귀 ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 812 }); await page.emulateMedia({ reducedMotion: 'no-preference' });
    const tiles = shots ? await useMapTiles(page) : async () => {};
    await openFinished(page, request, undefined, { intakeEvents: 'off' }); await tiles(); await idle(page);
    const body = page.locator('div[class*=sheetBody]'), strip = page.getByRole('tablist', { name: '일차 고르기', includeHidden: true });
    const gaps = await strip.evaluate(e => {
      const tabs = Array.from(e.querySelectorAll('[role=tab]')).map(t => t.getBoundingClientRect());
      const actions = e.closest('section')!.querySelector('[class*=dayTools]')!.getBoundingClientRect();   // `[ST4F-494]` 단추는 첫 날짜 제목 줄 오른쪽
      return { between: tabs[1].left - tabs[0].right, below: actions.top - Math.max(...tabs.map(t => t.bottom)) };
    }); expect(gaps.between).toBeGreaterThanOrEqual(8); expect(gaps.below).toBeGreaterThanOrEqual(20);
    await body.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
    await body.hover(); await page.mouse.wheel(0, -100);
    await expect(strip).not.toHaveAttribute('data-quiet'); await expect(strip).toHaveCSS('opacity', '1');
    await snap(page, `date-top-${width}`);
    await strip.getByRole('tab').last().click();
    // Inspect before the 800ms idle timer, while a touch remains held.
    const observed = await body.evaluate(async el => {
      const tabs = el.closest('section')!.querySelector<HTMLElement>('[role=tablist]')!;
      el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 91 }));
      el.scrollTop = 140; el.dispatchEvent(new Event('scroll'));
      await new Promise<void>(r => requestAnimationFrame(() => requestAnimationFrame(() => r())));
      const hidden = tabs.hasAttribute('data-quiet');
      el.scrollTop = 0; el.dispatchEvent(new Event('scroll'));
      await new Promise<void>(r => requestAnimationFrame(() => requestAnimationFrame(() => r())));
      const shown = !tabs.hasAttribute('data-quiet') && getComputedStyle(tabs).opacity === '1';
      window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 91 }));
      return { hidden, shown };
    }); expect(observed).toEqual({ hidden: true, shown: true });
    await body.hover(); await page.mouse.wheel(0, 140); await expect(strip).toHaveAttribute('data-quiet');
    await expect(strip).not.toHaveAttribute('data-quiet'); await noHorizontalScroll(page);
  });

  test(`목록 끝은 마지막 일정 뒤 추가·종료 시각 초안·저장 ${width}px`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 812 }); await page.emulateMedia({ reducedMotion: 'reduce' });
    const tiles = shots ? await useMapTiles(page) : async () => {};
    const api = await openFinished(page, request, undefined, { intakeEvents: 'off' }); await tiles(); await idle(page);
    const body = page.locator('div[class*=sheetBody]');
    await body.evaluate(el => { el.scrollTop = el.scrollHeight; });
    const seam = page.locator('[data-insert-near]'); await expect(seam).toHaveAttribute('data-insert-point', '1:end');
    const add = seam.getByRole('button', { name: '광장시장 다음에 일정 추가', exact: true }); await expect(add).toBeVisible();
    const last = await card(page, '광장시장').boundingBox(), button = await add.boundingBox();
    expect(button!.y).toBeGreaterThan(last!.y + last!.height - 1);
    await snap(page, `last-add-${width}`); await add.click();
    const form = page.getByRole('form', { name: '일정 추가' });
    await expect(form.getByLabel('시작', { exact: true })).toHaveValue('13:30');
    await form.getByLabel('일정 이름', { exact: true }).fill('시장 다음 쉬는 시간');
    await page.getByRole('searchbox', { name: '장소 검색', exact: true }).fill('올리브영');
    await page.getByRole('list', { name: '장소 검색 결과' }).getByRole('button', { name: /올리브영 명동 플래그십/ }).click();
    await form.getByRole('button', { name: '일정에 추가', exact: true }).click();
    await expect.poll(async () => (await api.received('POST', '/edits')).length).toBe(1);
    const payload = JSON.stringify((await api.received('POST', '/edits'))[0].body);
    expect(payload).toContain('시장 다음 쉬는 시간'); expect(payload).toContain('13:30');
    await noHorizontalScroll(page);
  });
}
