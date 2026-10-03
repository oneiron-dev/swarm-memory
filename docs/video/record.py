#!/usr/bin/env python3
"""Record the two-minute demo of the live Swarm memory app.

Drives headless Chrome through the six views with Playwright, grabs frames with the DevTools
screencast, then builds a 1920x1080 H.264 MP4 with ffmpeg: the browser on top, a caption band
below. The captions (and captions.srt) are timed from the moments the take actually reached.
Nothing here holds dataset text: the script only names the day, agent and entity to open.

    python docs/video/record.py --out /path/to/video
    python docs/video/record.py --out /path/to/video --srt docs/video/captions.srt

Needs Python 3.12, `pip install -r docs/video/requirements.txt`, ffmpeg with libx264 on PATH,
and Google Chrome (or `python -m playwright install chromium`).
"""

import argparse
import asyncio
import base64
import json
import math
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.async_api import Page, async_playwright

BASE = 'https://arch.tail87c1d7.ts.net/'

# The browser renders at 1536x776 CSS px with a 1.25 device scale: 1920x970 frames, readable at
# 1080p. The caption band takes the last 110 px of the 1080 frame.
VIEW_W, VIEW_H, SCALE = 1536, 776, 1.25
FRAME_W, FRAME_H = 1920, 970
BAND_H = 1080 - FRAME_H
FPS = 30

# What the take opens. Each is a public id in the bundle, never dataset text.
DAY = 314                     # the first day of the park clean-up goal
AGENT = 'gemini-2-5-pro'      # the agent with the most kept memories
GOAL_HINT = 'Adopt a park'    # village goal picked in the network filter
ENTITY_QUERY = 'devoe'
ENTITY_TEXT = 'Devoe Park'
MEMORY_DAY = 314

INK, INK3, BG, RULE = '#DDD6C9', '#8F887A', '#131110', '#322D27'

CURSOR_JS = r"""
(() => {
  if (window.__cursor) return;
  const css = document.createElement('style');
  css.textContent = `
    #__cur[popover] { inset: auto; left: 0; top: 0; margin: 0; padding: 0; border: 0; background: transparent;
      overflow: visible; width: 26px; height: 26px; pointer-events: none; transform: translate(-100px, -100px); }
    #__cur svg { display: block; filter: drop-shadow(0 1px 2px rgba(0,0,0,.7)); }
    #__cur .ring { position: absolute; left: -16px; top: -16px; width: 32px; height: 32px; border-radius: 50%;
      border: 2px solid #E0452B; opacity: 0; }
    #__cur .ring.go { animation: __ring .5s ease-out; }
    @keyframes __ring { from { transform: scale(.3); opacity: .95; } to { transform: scale(1.5); opacity: 0; } }`;
  document.head.appendChild(css);
  const el = document.createElement('div');
  el.id = '__cur';
  el.setAttribute('popover', 'manual');
  el.innerHTML = '<div class="ring"></div><svg width="26" height="26" viewBox="0 0 26 26">' +
    '<path d="M2 2 L2 21 L7.2 16.4 L10.6 24 L14 22.5 L10.7 15.1 L17.6 15.1 Z" fill="#FAF9F5" stroke="#131110" stroke-width="1.4" stroke-linejoin="round"/></svg>';
  document.body.appendChild(el);
  el.showPopover();
  const ring = el.querySelector('.ring');
  addEventListener('pointermove', (e) => { el.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`; }, true);
  addEventListener('pointerdown', () => { ring.classList.remove('go'); void ring.offsetWidth; ring.classList.add('go'); }, true);
  // keep the cursor above a modal dialog: the top layer stacks in show order
  window.__raiseCursor = () => { el.hidePopover(); el.showPopover(); };
  window.__cursor = el;
})();
"""

SCROLL_JS = r"""
window.__ease = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
window.__scroll = (sel, y, ms) => new Promise((res) => {
  const box = sel ? document.querySelector(sel) : null;
  const get = () => (box ? box.scrollTop : scrollY);
  const set = (v) => (box ? (box.scrollTop = v) : scrollTo(0, v));
  const y0 = get(), t0 = performance.now();
  const f = (now) => {
    const p = Math.min(1, (now - t0) / ms);
    set(y0 + (y - y0) * window.__ease(p));
    p < 1 ? requestAnimationFrame(f) : res();
  };
  requestAnimationFrame(f);
});
"""


class Take:
    """A screencast running against one page, plus the caption cues hit along the way."""

    def __init__(self, page: Page, frames: Path):
        self.page = page
        self.frames = frames
        self.shots: list[tuple[float, Path]] = []
        self.cues: list[tuple[float, str, str]] = []
        self.mouse = (VIEW_W * 0.7, VIEW_H * 0.6)
        self.t_start = self.t_end = 0.0
        self.cdp = None

    async def start_screencast(self):
        self.cdp = await self.page.context.new_cdp_session(self.page)
        self.cdp.on('Page.screencastFrame', lambda ev: asyncio.ensure_future(self._frame(ev)))
        await self.cdp.send('Page.startScreencast', {
            'format': 'jpeg', 'quality': 92, 'maxWidth': FRAME_W, 'maxHeight': FRAME_H, 'everyNthFrame': 1,
        })

    async def _frame(self, ev):
        path = self.frames / f'{len(self.shots):06d}.jpg'
        path.write_bytes(base64.b64decode(ev['data']))
        self.shots.append((ev['metadata'].get('timestamp') or time.time(), path))
        try:
            await self.cdp.send('Page.screencastFrameAck', {'sessionId': ev['sessionId']})
        except Exception:
            pass

    async def stop_screencast(self):
        await self.cdp.send('Page.stopScreencast')

    def cue(self, scene: str, text: str):
        self.cues.append((time.time(), scene, text))
        print(f'  {time.time() - (self.t_start or time.time()):6.1f}s  [{scene}] {text}')

    async def hold(self, s: float):
        await asyncio.sleep(s)

    async def move(self, x: float, y: float, ms: float = 700):
        x0, y0 = self.mouse
        n = max(2, int(ms / 16))
        for i in range(1, n + 1):
            p = i / n
            e = 4 * p * p * p if p < 0.5 else 1 - math.pow(-2 * p + 2, 3) / 2
            await self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            await asyncio.sleep(ms / 1000 / n)
        self.mouse = (x, y)

    async def click_at(self, x: float, y: float, ms: float = 700):
        await self.move(x, y, ms)
        await asyncio.sleep(0.15)
        await self.page.mouse.down()
        await asyncio.sleep(0.06)
        await self.page.mouse.up()

    async def center(self, locator):
        await locator.wait_for(state='visible', timeout=15000)
        b = await locator.bounding_box()
        return b['x'] + b['width'] / 2, b['y'] + b['height'] / 2

    async def click(self, locator, ms: float = 700, dx: float = 0):
        await locator.wait_for(state='visible', timeout=15000)
        b = await locator.bounding_box()
        if b['y'] < 8 or b['y'] + b['height'] > VIEW_H - 8:
            # off screen (the mast scrolls away): bring it into view first, as a viewer would
            y = await self.page.evaluate('(dy) => Math.max(0, scrollY + dy)', b['y'] - VIEW_H * 0.35)
            await self.page.evaluate('([y, ms]) => window.__scroll(null, y, ms)', [y, 900])
            await asyncio.sleep(0.15)
        x, y = await self.center(locator)
        await self.click_at(x + dx, y, ms)

    async def scroll_to(self, selector: str, offset: float = 90, ms: float = 1300, box: str | None = None):
        """Smooth-scroll the window (or the scroll box `box`) so `selector` sits `offset` px from the top."""
        y = await self.page.evaluate(
            """([sel, off, box]) => {
                const el = document.querySelector(sel);
                if (!el) return null;
                if (box) { const b = document.querySelector(box); return el.getBoundingClientRect().top - b.getBoundingClientRect().top + b.scrollTop - off; }
                return Math.max(0, el.getBoundingClientRect().top + scrollY - off);
            }""",
            [selector, offset, box],
        )
        if y is None:
            raise RuntimeError(f'nothing matches {selector}')
        await self.page.evaluate('([box, y, ms]) => window.__scroll(box, y, ms)', [box, y, ms])

    async def sweep(self, selector: str, frm: float, to: float, y_frac: float, ms: float):
        """Glide the cursor across a chart from `frm` to `to` (fractions of its width)."""
        b = await self.page.locator(selector).first.bounding_box()
        await self.move(b['x'] + b['width'] * frm, b['y'] + b['height'] * y_frac, 600)
        await self.move(b['x'] + b['width'] * to, b['y'] + b['height'] * y_frac, ms)


def day_x(box: dict, day: float, lo: int, hi: int, pad_l: float = 44, pad_r: float = 8) -> float:
    """The x of a day in the app's day charts (same padding and band maths as the views)."""
    return box['x'] + pad_l + (day - (lo - 0.5)) / (hi - lo + 1) * (box['width'] - pad_l - pad_r)


async def script(take: Take, findings: bool):
    page = take.page
    nav = lambda name: page.locator('nav.nav a', has_text=name).first
    rng = await page.evaluate('fetch("data/bundle.json").then(r => r.json()).then(b => b.manifest.range)')
    lo, hi = rng['first_day'], rng['last_day']

    # 1. overview
    await page.locator('section.hero h1').wait_for(timeout=20000)
    take.t_start = time.time()
    take.cue('01 overview', '46 AI agents shared one village across 535 days.')
    await take.move(VIEW_W * 0.62, VIEW_H * 0.5, 900)
    await take.hold(2.4)
    take.cue('01 overview', 'Oneiron and our tagger oneiroNER read its text records.')
    await take.move(VIEW_W * 0.5, VIEW_H * 0.62, 900)
    await take.hold(3.4)
    take.cue('01 overview', 'Messages per day, by lab. The strip marks the 51 goals.')
    await take.sweep('section.well .chart svg', 0.08, 0.86, 0.6, 3400)
    await take.hold(1.0)
    await take.move(VIEW_W * 0.9, VIEW_H * 0.5, 600)
    if findings:
        take.cue('01 overview', 'What we found, each finding cited and linked to its view.')
        await take.scroll_to('.findings', offset=110, ms=1600)
        await take.hold(5.0)

    # 2. timeline
    await take.click(nav('timeline'))
    take.cue('02 timeline', f'Timeline: pick a day. Day {DAY} starts the park clean-up goal.')
    chart = page.locator('section.well .chart svg').first
    await chart.wait_for()
    await take.hold(1.0)
    box = await chart.bounding_box()
    await take.click_at(day_x(box, DAY, lo, hi), box['y'] + box['height'] * 0.45, 1100)
    await page.wait_for_function(f'location.hash === "#timeline/{DAY}"', timeout=5000)
    await take.hold(1.4)
    take.cue('02 timeline', "The day's goal, its counts, who worked, what was named.")
    await take.scroll_to('.cols', offset=24, ms=1400)
    await take.hold(2.6)
    take.cue('02 timeline', "What happened that day, from AI Digest's daily summary.")
    await take.move(VIEW_W * 0.62, VIEW_H * 0.42, 800)
    await take.hold(2.4)
    take.cue('02 timeline', 'Click a citation: the source opens with its tags lit up.')
    await take.click(page.locator('.cols button.cite').first)
    await page.locator('dialog.sheet blockquote').wait_for()
    await page.evaluate('window.__raiseCursor()')
    await take.hold(0.6)
    await take.move(VIEW_W * 0.8, VIEW_H * 0.8, 800)
    await take.hold(3.4)
    await take.click(page.locator('dialog.sheet header button'))
    await take.hold(0.6)

    # 3. agents
    take.cue('03 agents', 'Every agent has a page. Here: Gemini 2.5 Pro.')
    await take.click(page.locator(f'a.who[href="#agents/{AGENT}"]'))
    await page.locator('h1.place', has_text='Gemini 2.5 Pro').wait_for()
    await take.hold(1.8)
    take.cue('03 agents', 'Its own goal, and what it talked about most.')
    await take.move(VIEW_W * 0.6, VIEW_H * 0.52, 900)
    await take.hold(2.2)
    take.cue('03 agents', 'Its mood per day from oneiroNER, against the village mean.')
    mood = 'svg[aria-label="v over days"]'
    await take.scroll_to('.stack > section.well:nth-of-type(2)', offset=24, ms=1400)
    await take.sweep(mood, 0.1, 0.74, 0.5, 2600)
    await take.hold(1.0)
    take.cue('03 agents', 'A cited summary for its active days.')
    await take.move(VIEW_W * 0.55, VIEW_H * 0.2, 500)
    await take.scroll_to('.stack > section.well:nth-of-type(3)', offset=24, ms=1400)
    await take.hold(1.2)
    await take.click(page.locator('.stack > section.well:nth-of-type(3) article.summary button.cite').first)
    await page.locator('dialog.sheet blockquote').wait_for()
    await page.evaluate('window.__raiseCursor()')
    await take.move(VIEW_W * 0.8, VIEW_H * 0.8, 700)
    await take.hold(2.6)
    await take.click(page.locator('dialog.sheet header button'))
    await take.hold(0.4)

    # 4. who talks to whom
    await take.click(nav('who talks to whom'))
    take.cue('04 who talks to whom', 'Who talks to whom: mentions and replies between agents.')
    net = 'svg[aria-label="network of agent interactions"]'
    await page.locator(net).wait_for()
    await take.hold(0.8)
    await take.scroll_to('.range', offset=24, ms=1200)
    await take.hold(1.4)
    take.cue('04 who talks to whom', 'Filter to one goal: the week of the park clean-up.')
    sel = page.locator('.range select')
    await take.click(sel, 800)
    value = await sel.evaluate('(s, hint) => [...s.options].find((o) => o.text.includes(hint))?.value', GOAL_HINT)
    await sel.select_option(value)
    await take.hold(2.6)
    take.cue('04 who talks to whom', 'Pick an agent: who it speaks to, and who it hears from.')
    node = await page.evaluate(
        """([net, name]) => {
            const t = [...document.querySelectorAll(net + ' text.nlabel')].find((x) => x.textContent === name);
            const c = t && t.parentElement.querySelectorAll('circle')[1];
            if (!c) return null;
            const b = c.getBoundingClientRect();
            return [b.x + b.width / 2, b.y + b.height / 2];
        }""",
        [net, 'Gemini 2.5 Pro'],
    )
    await take.move(node[0], node[1], 1000)
    await take.hold(1.2)
    await take.click_at(node[0], node[1], 200)
    await take.hold(0.8)
    await take.move(VIEW_W * 0.85, VIEW_H * 0.3, 600)
    await take.scroll_to('.partners', offset=VIEW_H - 360, ms=1300)
    await take.hold(2.6)

    # 5. memory drift
    await take.click(nav('memory drift'))
    take.cue('05 memory drift', 'Agents rewrite their own memory notes as they work.')
    drift = 'svg[aria-label="memory drift per day"]'
    await page.locator(drift).wait_for(timeout=20000)
    await take.hold(3.4)
    take.cue('05 memory drift', 'Drift: how many tagged entities changed since its last memory.')
    await take.scroll_to('.kv', offset=150, ms=1300)
    await take.sweep(drift, 0.12, 0.5, 0.35, 2000)
    await take.hold(0.6)
    take.cue('05 memory drift', f'Day {MEMORY_DAY}: a new goal, and almost all tagged entities changed.')
    mem = await page.evaluate(f'fetch("data/memory/{AGENT}.json").then(r => r.json()).then(m => [m.days[0].day, m.days[m.days.length - 1].day])')
    box = await page.locator(drift).bounding_box()
    await take.click_at(day_x(box, MEMORY_DAY, mem[0], mem[1]), box['y'] + 70, 900)
    await page.wait_for_function(f'location.hash === "#memory/{AGENT}/{MEMORY_DAY}"')
    await take.hold(1.6)
    take.cue('05 memory drift', 'What it added, what it dropped, and how the memory begins.')
    await take.move(VIEW_W * 0.9, VIEW_H * 0.3, 500)
    await take.scroll_to('.cols', offset=24, ms=1500)
    await take.hold(3.8)

    # 6. entities
    await take.click(nav('entities'))
    take.cue('06 entities', '3 million mentions, grouped into the top 2,000 entities.')
    search = page.locator('input.search')
    await search.wait_for()
    await take.hold(3.0)
    take.cue('06 entities', 'Search one: its mentions per day, and who named it.')
    await take.click(search, 800)
    await page.keyboard.type(ENTITY_QUERY, delay=140)
    await take.hold(0.8)
    row = page.locator('table tbody tr', has=page.locator('td:first-child', has_text=ENTITY_TEXT)).first
    await take.click(row, 800)
    await page.locator('.stack.sticky h3', has_text='Who named it').wait_for(timeout=20000)
    await take.hold(0.8)
    await take.move(VIEW_W * 0.78, VIEW_H * 0.42, 900)
    await take.hold(2.4)
    take.cue('06 entities', 'Each example leads back to the sentence it came from.')
    await take.move(VIEW_W * 0.92, VIEW_H * 0.55, 600)
    await take.scroll_to('.stack.sticky .source', offset=VIEW_H - 230, ms=1400, box='.stack.sticky')
    await take.hold(3.6)

    # 7. outro
    take.cue('07', 'Built with Oneiron and oneiroNER. Explore it yourself.')
    await page.evaluate('window.__cursor.style.opacity = "0"')
    await page.evaluate(OUTRO_JS)
    await take.hold(7.0)
    take.t_end = time.time()


OUTRO_JS = r"""
(() => {
  const app = document.querySelector('.app');
  app.style.animation = 'none';  // the arrive animation pins opacity; drop it before fading
  app.style.transition = 'opacity .7s ease';
  requestAnimationFrame(() => { app.style.opacity = '0'; });
  const card = document.createElement('div');
  card.innerHTML = `
    <div style="background: rgba(27,24,21,.95); border-radius: 6px; padding: 52px 64px; max-width: 980px; box-shadow: 0 14px 40px rgba(0,0,0,.55)">
      <h1 style="font: 600 40px/1.15 var(--serif); color: var(--ink-1)">Swarm memory</h1>
      <p style="font: 400 22px/1.4 var(--serif); color: var(--ink-2); margin-top: 10px">What the AI Village agents did, said and remembered, read with Oneiron and oneiroNER.</p>
      <dl style="display: grid; grid-template-columns: max-content 1fr; gap: 14px 28px; margin-top: 34px; font: 400 21px/1.3 var(--mono); color: var(--ink-1)">
        <dt style="color: var(--ink-3)">Data</dt><dd>AI Digest / AI Village</dd>
        <dt style="color: var(--ink-3)">Code</dt><dd>github.com/oneiron-dev/swarm-memory</dd>
        <dt style="color: var(--ink-3)">Live</dt><dd>arch.tail87c1d7.ts.net</dd>
      </dl>
    </div>`;
  card.style.cssText = 'position: fixed; inset: 0; display: grid; place-items: center; z-index: 50; opacity: 0; transition: opacity .9s ease .4s';
  document.body.appendChild(card);
  requestAnimationFrame(() => requestAnimationFrame(() => { card.style.opacity = '1'; }));
})();
"""


def band_html(scene: str, text: str, fonts: dict[str, str]) -> str:
    label = scene.split(' ', 1)
    tag = f'<b>{label[0]}</b> {label[1]}' if len(label) == 2 else ''
    return f"""<style>
@font-face {{ font-family: S; src: url(data:font/woff2;base64,{fonts['serif']}) format('woff2'); font-weight: 400 600; }}
@font-face {{ font-family: M; src: url(data:font/woff2;base64,{fonts['mono']}) format('woff2'); }}
html, body {{ margin: 0; width: {FRAME_W}px; height: {BAND_H}px; background: {BG}; overflow: hidden; }}
.band {{ position: relative; height: {BAND_H}px; border-top: 1px solid {RULE}; box-sizing: border-box;
  display: grid; place-items: center; }}
.cap {{ font: 400 37px/1.2 S, Georgia, serif; color: {INK}; letter-spacing: .005em; }}
.tag {{ position: absolute; left: 56px; top: 50%; transform: translateY(-50%); font: 400 17px M, Menlo, monospace;
  color: {INK3}; letter-spacing: .04em; }}
.tag b {{ color: #E0452B; font-weight: 400; margin-right: 6px; }}
</style><div class="band"><div class="tag">{tag}</div><div class="cap">{text}</div></div>"""


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def write_srt(cues: list[tuple[float, float, str]], path: Path):
    lines = []
    for i, (a, b, text) in enumerate(cues, 1):
        lines += [str(i), f'{srt_time(a)} --> {srt_time(b - 0.05)}', text, '']
    path.write_text('\n'.join(lines))


def concat_list(items: list[tuple[Path, float]], path: Path):
    out = ['ffconcat version 1.0']
    for f, d in items:
        out += [f"file '{f}'", f'duration {d:.4f}']
    out.append(f"file '{items[-1][0]}'")  # the concat demuxer drops the last duration unless the file repeats
    path.write_text('\n'.join(out) + '\n')


async def record(args):
    out = Path(args.out).resolve()
    work = out / 'work'  # scratch folder: recreated on every run (any existing <out>/work is deleted)
    frames = work / 'frames'
    shutil.rmtree(work, ignore_errors=True)
    frames.mkdir(parents=True)
    base = args.base.rstrip('/') + '/'

    fonts = {
        k: base64.b64encode(urllib.request.urlopen(base + p, timeout=30).read()).decode()
        for k, p in (('serif', 'fonts/source-serif-4-normal-400-600-latin.woff2'), ('mono', 'fonts/ServerMono-Regular.woff2'))
    }
    try:
        findings = urllib.request.urlopen(base + 'data/findings.json', timeout=30).status == 200
    except Exception:
        findings = False
    print(f'findings.json live: {findings}')

    async with async_playwright() as p:
        # A real device scale (not emulation) so the screencast hands over full-size frames.
        # The window height adds the 87 px headless Chrome keeps for its own frame.
        flags = [f'--force-device-scale-factor={SCALE}', f'--window-size={VIEW_W},{VIEW_H + 87}']
        try:
            browser = await p.chromium.launch(channel='chrome', headless=True, args=flags)
        except Exception:
            browser = await p.chromium.launch(headless=True, args=flags)
        ctx = await browser.new_context(no_viewport=True, color_scheme='dark')
        await ctx.add_init_script(SCROLL_JS)
        await ctx.add_init_script(f'addEventListener("DOMContentLoaded", () => {{ {CURSOR_JS} }})')
        page = await ctx.new_page()
        url = base + '?theme=dark#overview'
        await page.goto(url, wait_until='networkidle')  # warm the cache: fonts, art, bundle
        await page.wait_for_timeout(1500)
        size = await page.evaluate('[innerWidth, innerHeight, devicePixelRatio]')
        if size != [VIEW_W, VIEW_H, SCALE]:
            print(f'warning: the page is {size}, expected {[VIEW_W, VIEW_H, SCALE]}')
        take = Take(page, frames)
        await take.start_screencast()
        await page.reload(wait_until='domcontentloaded')
        await page.mouse.move(*take.mouse)
        await script(take, findings)
        await asyncio.sleep(0.3)
        await take.stop_screencast()

        # caption band images, one per cue
        bands = []
        bpage = await browser.new_page(viewport={'width': FRAME_W, 'height': BAND_H})
        for i, (_, scene, text) in enumerate(take.cues):
            f = work / f'band-{i:02d}.png'
            await bpage.set_content(band_html(scene, text, fonts))
            await bpage.evaluate('document.fonts.ready')
            await bpage.screenshot(path=str(f))
            bands.append(f)
        await browser.close()

    t0, t1 = take.t_start, take.t_end
    total = t1 - t0
    shots = sorted(take.shots)
    # the frame on screen at t0, then every frame up to t1
    first = max((i for i, (t, _) in enumerate(shots) if t <= t0), default=0)
    seq = shots[first:]
    seq = [(max(t, t0), f) for t, f in seq if t < t1]
    items = [(f, (seq[i + 1][0] if i + 1 < len(seq) else t1) - t) for i, (t, f) in enumerate(seq)]
    items = [(f, d) for f, d in items if d > 0]
    concat_list(items, work / 'frames.txt')

    starts = [c[0] - t0 for c in take.cues]
    spans = [(max(0.0, a), (starts[i + 1] if i + 1 < len(starts) else total), take.cues[i][2]) for i, a in enumerate(starts)]
    concat_list([(bands[i], b - a) for i, (a, b, _) in enumerate(spans)], work / 'band.txt')
    write_srt(spans, out / 'captions.srt')
    if args.srt:
        shutil.copy(out / 'captions.srt', args.srt)

    mp4 = out / args.name
    fade_out = max(0.0, total - 0.8)
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error', '-stats',
        '-f', 'concat', '-safe', '0', '-i', str(work / 'frames.txt'),
        '-f', 'concat', '-safe', '0', '-i', str(work / 'band.txt'),
        '-filter_complex',
        f'[0:v]scale={FRAME_W}:{FRAME_H}:flags=lanczos:in_range=pc:out_range=tv:in_color_matrix=bt601:out_color_matrix=bt709,format=yuv420p,fps={FPS},setsar=1[top];'
        f'[1:v]scale={FRAME_W}:{BAND_H}:out_range=tv:out_color_matrix=bt709,format=yuv420p,fps={FPS},setsar=1[band];'
        f'[top][band]vstack=inputs=2,format=yuv420p,trim=duration={total:.3f},'
        f'fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out:.3f}:d=0.8[v]',
        '-map', '[v]', '-c:v', 'libx264', '-preset', 'slow', '-crf', str(args.crf), '-pix_fmt', 'yuv420p',
        '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
        '-r', str(FPS), '-movflags', '+faststart', str(mp4),
    ]
    subprocess.run(cmd, check=True)
    poster_t = min(2.8, total / 2)  # the overview under the first caption
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-ss', f'{poster_t:.2f}', '-i', str(mp4), '-frames:v', '1', str(out / 'poster.png')], check=True)

    meta = {
        'duration_s': round(total, 2), 'frames_captured': len(shots), 'findings': findings,
        'cues': [{'start': round(a, 2), 'end': round(b, 2), 'text': t} for a, b, t in spans],
    }
    (out / 'take.json').write_text(json.dumps(meta, indent=1))
    print(f'{mp4}  {total:.1f}s  {mp4.stat().st_size / 1e6:.1f} MB  {len(shots)} frames')
    if not args.keep_work:
        shutil.rmtree(work)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--base', default=BASE, help='the live app')
    ap.add_argument('--out', required=True, help='output folder (MP4, poster.png, captions.srt, take.json)')
    ap.add_argument('--name', default='swarm-memory-demo.mp4')
    ap.add_argument('--srt', help='also copy captions.srt here (docs/video/captions.srt)')
    ap.add_argument('--crf', type=int, default=20)
    ap.add_argument('--keep-work', action='store_true', help='keep the raw frames and caption bands')
    asyncio.run(record(ap.parse_args()))


if __name__ == '__main__':
    sys.exit(main())
