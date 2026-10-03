// Develop-on-load: the printed field exposes through an 8x8 Bayer screen over about a second.
// A film-black (or paper) cover sits above the field art and dissolves dot by dot, so the
// field is shown by the material itself; reduced motion skips straight to the print.

const BAYER = [
  0, 32, 8, 40, 2, 34, 10, 42, 48, 16, 56, 24, 50, 18, 58, 26, 12, 44, 4, 36, 14, 46, 6, 38, 60, 28, 52, 20, 62, 30, 54, 22,
  3, 35, 11, 43, 1, 33, 9, 41, 51, 19, 59, 27, 49, 17, 57, 25, 15, 47, 7, 39, 13, 45, 5, 37, 63, 31, 55, 23, 61, 29, 53, 21,
]
const CELL = 3

export function develop(canvas: HTMLCanvasElement, done: () => void) {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduce) return done()
  const w = Math.ceil(innerWidth / CELL)
  const h = Math.ceil(innerHeight / CELL)
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) return done()
  const ground = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#131110'
  ctx.fillStyle = ground
  ctx.fillRect(0, 0, w, h)
  const img = ctx.getImageData(0, 0, w, h)
  const px = img.data
  // per-cell threshold: the Bayer rank, warped by a low-frequency field so the edge is statistical
  const thr = new Float32Array(w * h)
  const mx = w * 0.64
  const my = h * 0.16
  const diag = Math.hypot(w, h)
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const b = BAYER[(y & 7) * 8 + (x & 7)] / 64
      const r = Math.hypot(x - mx, y - my) / diag
      const n = Math.sin(x * 0.043 + y * 0.021) * Math.cos(y * 0.037 - x * 0.012) * 0.08
      thr[y * w + x] = b * 0.62 + r * 0.5 + n
    }
  }
  const t0 = performance.now()
  const dur = 1100
  const frame = (now: number) => {
    const p = Math.min(1, (now - t0) / dur)
    const level = (1 - Math.pow(1 - p, 3)) * 1.25
    for (let i = 0; i < thr.length; i++) if (thr[i] < level) px[i * 4 + 3] = 0
    ctx.putImageData(img, 0, 0)
    if (p < 1) requestAnimationFrame(frame)
    else done()
  }
  requestAnimationFrame(frame)
}
