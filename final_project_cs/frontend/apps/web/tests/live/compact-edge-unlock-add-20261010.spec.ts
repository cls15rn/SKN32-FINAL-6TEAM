import {expect,test,type Page} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
import {mockServer,noHorizontalScroll} from './helpers';
import {head,openFinished,INTAKE} from './plan-check-kit';
import {useMapTiles} from './capture-map-tiles';

const shots=process.env.COMPACT_EDGE_SHOTS;
test.beforeEach(async({request})=>{await mockServer(request).reset();});
async function prepare(page:Page){const close=page.getByRole('button',{name:'닫기',exact:true});if(await close.isVisible())await close.click();await page.mouse.move(0,0);}
async function capture(page:Page,name:string,movePointer=true){if(!shots)return;await mkdir(shots,{recursive:true});if(movePointer)await page.mouse.move(0,0);await page.screenshot({path:`${shots}/${name}.png`,fullPage:true});}
for(const width of [375,1280]){
  test(`먼 일정 거리 숨김·좁은 마커·조작 후 복귀 ${width}px`,async({page,request})=>{
    await page.setViewportSize({width,height:812});await page.emulateMedia({reducedMotion:'reduce'});
    const tiles=shots?await useMapTiles(page):async()=>{};
    await openFinished(page,request,undefined,{intakeEvents:'off'});await tiles();await prepare(page);
    await head(page,'경복궁 관람').click();await prepare(page);
    const map=page.getByRole('region',{name:'여행 지도'});
    await expect.poll(()=>map.getAttribute('data-moving')).toBe(null);
    await page.getByRole('button',{name:'지도 단추 펼치기',exact:true}).click();
    await page.getByRole('button',{name:'확대',exact:true}).click();
    await expect.poll(()=>map.getAttribute('data-moving')).toBe(null);
    await page.getByRole('button',{name:'지도 단추 접기',exact:true}).click();
    const chip=page.locator('[data-edge-chip]').first();await expect(chip).toBeVisible();await expect(chip.locator('small')).toBeVisible();
    const before=(await chip.boundingBox())!.width;await capture(page,`01-distance-${width}`);
    const box=(await map.boundingBox())!;
    await page.mouse.move(box.x+box.width/2,box.y+box.height-60);await page.mouse.down();
    await page.mouse.move(box.x+box.width/2+30,box.y+box.height-75,{steps:8});
    await expect(map).toHaveAttribute('data-moving','true');await expect(chip.locator('small')).toHaveCount(0);
    expect((await chip.boundingBox())!.width).toBeLessThan(before-20);
    await expect(chip).toHaveAccessibleName(/화면 밖.*누르면 그곳으로/);
    await capture(page,`02-moving-${width}`,false);await page.mouse.up();
    await expect.poll(()=>map.getAttribute('data-moving')).toBe(null);await expect(chip.locator('small')).toBeVisible();
    await noHorizontalScroll(page);
  });
  test(`전체 잠금 해제는 목록 맨 위·한 요청·스크롤에 사라짐 ${width}px`,async({page,request})=>{
    await page.setViewportSize({width,height:812});await page.emulateMedia({reducedMotion:'reduce'});
    const tiles=shots?await useMapTiles(page):async()=>{};
    const server=await openFinished(page,request,view=>{view.review.items[0].locked=true;view.review.items[2].locked=true;},{intakeEvents:'off'});await tiles();await prepare(page);
    const body=page.locator('[class*=sheetBody]'),all=page.getByRole('button',{name:'전체 잠금 해제',exact:true}),recommend=body.getByRole('button',{name:/^전체 자동 추천/});
    await expect(all).toBeVisible();const a=(await all.boundingBox())!,b=(await recommend.boundingBox())!;
    // `[2026-10-15 ST4F-494]` 두 단추는 첫 날짜 제목 줄 오른쪽 묶음(dayTools)에 있다 — 자동 추천 뒤(같은 줄 오른쪽, 폭이 모자라면 아래 줄 오른쪽 정렬)
    expect(await all.evaluate(el=>Boolean(el.closest('[class*=dayTools]')))).toBe(true);expect(await recommend.evaluate(el=>Boolean(el.closest('[class*=dayTools]')))).toBe(true);
    if(Math.abs(a.y-b.y)<1)expect(a.x).toBeGreaterThan(b.x+b.width);else expect(a.y).toBeGreaterThan(b.y);await capture(page,`03-top-actions-${width}`);
    await body.evaluate(el=>{el.scrollTop=250;});await expect(all).not.toBeInViewport();await capture(page,`04-scrolled-${width}`);
    await body.evaluate(el=>{el.scrollTop=0;});await all.click();await expect(all).toHaveCount(0);
    const writes=await server.received('POST',`/${INTAKE}/edits`);expect(writes).toHaveLength(1);
    expect(writes[0].body!.edits).toEqual(expect.arrayContaining([expect.objectContaining({field:'items[0].locked',value:false}),expect.objectContaining({field:'items[2].locked',value:false})]));
    expect((writes[0].body!.edits as unknown[])).toHaveLength(2);await expect(recommend).toBeFocused();
    await noHorizontalScroll(page);
  });
  test(`원형 추가 단추는 최상단 첫 일정 앞·취소 복귀 ${width}px`,async({page,request})=>{
    await page.setViewportSize({width,height:812});await page.emulateMedia({reducedMotion:'reduce'});
    const tiles=shots?await useMapTiles(page):async()=>{};
    await openFinished(page,request,undefined,{intakeEvents:'off'});await tiles();await prepare(page);
    for(let i=0;i<4;i++)await page.getByRole('button',{name:'목록 높이 바꾸기'}).press('ArrowUp');
    const seam=page.locator('[data-insert-near]'),add=seam.getByRole('button');await expect(add).toBeVisible();
    await expect(add.locator("svg")).toBeVisible();
    await expect(seam).toHaveAttribute('data-insert-start');
    // 목록 높이 변경과 scroll/ResizeObserver가 끝난 뒤 같은 프레임의 좌표끼리 비교한다.
    await expect.poll(()=>seam.evaluate((element)=>{
      const seamBox=element.getBoundingClientRect(),button=element.querySelector('button')!.getBoundingClientRect();
      const first=element.closest('[class*=sheetBody]')!.querySelector('article')!.getBoundingClientRect();
      return {width:button.width,height:button.height,seamHeight:seamBox.height,
        centredX:Math.abs(button.x+button.width/2-first.x-first.width/2)<1,
        centredY:Math.abs(button.y+button.height/2-seamBox.y-24)<.05};
    })).toEqual({width:44,height:44,seamHeight:48,centredX:true,centredY:true});
    const label=await add.getAttribute('aria-label');await capture(page,`05-add-placement-${width}`);
    await add.click();await expect(page.getByRole('region',{name:'일정 추가',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'일정 추가 취소',exact:true}).click();await expect(page.getByRole('button',{name:label!,exact:true})).toBeFocused();
    await capture(page,`06-add-return-${width}`);await noHorizontalScroll(page);
  });
}
