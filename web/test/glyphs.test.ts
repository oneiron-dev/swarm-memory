// The glyphs' pure helpers: same input, same drawing; the mood strip survives any series it is handed.
import { describe, expect, test } from 'bun:test'
import {
  GLYPH_KINDS, catmull, figureRulePath, glyphDef, glyphKindOf, lensWord, linePath, penCircle, rand, seedOf,
  smoothPath, stripGeometry, stripScale, wobbleCurve, wobbleLine, type Pt,
} from '../src/book/glyphs'

const NUM = /-?\d+(?:\.\d+)?/g
const numbers = (d: string): number[] => (d.match(NUM) ?? []).map(Number)

describe('randomness', () => {
  test('rand replays a seed and stays in [0, 1)', () => {
    const a = rand(42), b = rand(42)
    for (let i = 0; i < 500; i++) {
      const x = a()
      expect(x).toBe(b())
      expect(x >= 0 && x < 1).toBe(true)
    }
  })
  test('seedOf is stable and tells labels apart', () => {
    expect(seedOf('$1,481')).toBe(seedOf('$1,481'))
    expect(seedOf('32')).not.toBe(seedOf('33'))
  })
})

describe('wobble and path builders', () => {
  const poly: Pt[] = [[1, 18], [1, 12], [5, 8], [9, 12], [9, 18]]

  test('wobbleLine is deterministic and keeps every vertex exactly', () => {
    const a = wobbleLine(poly, { seed: 7, amp: 0.4, step: 2 })
    const b = wobbleLine(poly, { seed: 7, amp: 0.4, step: 2 })
    expect(a).toEqual(b)
    expect(a.length).toBe(poly.length - 1)
    a.forEach((seg, i) => {
      expect(seg[0]).toEqual(poly[i])
      expect(seg[seg.length - 1]).toEqual(poly[i + 1])
      expect(seg.length).toBeGreaterThan(2) // a few extra points, not a perfect line
    })
  })

  test('the wobble stays within its amplitude of the straight line', () => {
    const segs = wobbleLine([[0, 0], [40, 0]], { seed: 3, amp: 0.5, step: 2 })
    for (const [, y] of segs[0]) expect(Math.abs(y)).toBeLessThanOrEqual(0.5 + 1e-9)
    expect(segs[0].some(([, y]) => Math.abs(y) > 0.01)).toBe(true)
  })

  test('another seed draws another line', () => {
    expect(wobbleLine(poly, { seed: 1, amp: 0.4, step: 2 })).not.toEqual(wobbleLine(poly, { seed: 2, amp: 0.4, step: 2 }))
  })

  test('smoothPath starts and ends on the given points, closed paths close', () => {
    expect(smoothPath([])).toBe('')
    expect(smoothPath([[1, 2]])).toBe('M1 2')
    expect(smoothPath([[1, 2], [3, 4]])).toBe('M1 2L3 4')
    const d = smoothPath([[0, 0], [5, 5], [10, 0], [15, 5]])
    expect(d.startsWith('M0 0')).toBe(true)
    expect(d.endsWith('15 5')).toBe(true)
    expect(smoothPath([[0, 0], [5, 5], [10, 0]], true).endsWith('Z')).toBe(true)
  })

  test('linePath joins segments into one path through every vertex', () => {
    const d = linePath(wobbleLine(poly, { seed: 9, amp: 0.3, step: 2 }))
    expect(d.match(/M/g)?.length).toBe(1)
    for (const [x, y] of poly) expect(d.includes(`${x} ${y}`)).toBe(true)
    expect(linePath([])).toBe('')
  })

  test('catmull passes through its control points', () => {
    const ctrl: Pt[] = [[0, 0], [4, 6], [8, 0], [12, 6]]
    const pts = catmull(ctrl, 5)
    for (const c of ctrl) expect(pts.some((p) => Math.abs(p[0] - c[0]) < 1e-9 && Math.abs(p[1] - c[1]) < 1e-9)).toBe(true)
  })

  test('wobbleCurve keeps both ends and is deterministic', () => {
    const pts = catmull([[0, 0], [5, 5], [10, 0], [15, 5]], 6)
    const a = wobbleCurve(pts, { seed: 4, amp: 0.3 })
    expect(a).toEqual(wobbleCurve(pts, { seed: 4, amp: 0.3 }))
    expect(a[0]).toEqual(pts[0])
    expect(a[a.length - 1]).toEqual(pts[pts.length - 1])
  })

  test('penCircle is deterministic and stays near its radius', () => {
    const d = penCircle(11, 11, 6, { seed: 5, amp: 0.3 })
    expect(d).toBe(penCircle(11, 11, 6, { seed: 5, amp: 0.3 }))
    const n = numbers(d)
    for (let i = 0; i + 1 < n.length; i += 2) {
      const r = Math.hypot(n[i] - 11, n[i + 1] - 11)
      expect(r).toBeGreaterThan(4.5)
      expect(r).toBeLessThan(7.5)
    }
  })
})

describe('glyphs', () => {
  test('all five kinds draw, inside the 22 x 22 box, the same every time', () => {
    for (const k of GLYPH_KINDS) {
      const g = glyphDef(k)
      expect(g.d.length).toBeGreaterThan(0)
      for (const d of g.d) {
        expect(d.startsWith('M')).toBe(true)
        expect(d.includes('NaN')).toBe(false)
        for (const v of numbers(d)) expect(v >= -0.5 && v <= 22.5).toBe(true)
      }
      expect(g.dots.length).toBeLessThanOrEqual(2)
      expect(glyphDef(k)).toBe(g)
    }
  })
  test('lens to glyph and word', () => {
    const agents = [{ id: 'alpha', name: 'agent alpha' }]
    expect(glyphKindOf('village')).toBe('village')
    expect(glyphKindOf('agent:alpha')).toBe('agent')
    expect(lensWord('village', agents)).toBe('the village')
    expect(lensWord('goal', agents)).toBe('the goal')
    expect(lensWord('memory', agents)).toBe('memory')
    expect(lensWord('humans', agents)).toBe('the humans')
    expect(lensWord('agent:alpha', agents)).toBe('agent alpha')
    expect(lensWord('agent:unknown', agents)).toBe('unknown')
  })
})

describe('figure rule', () => {
  test('seeded by the value: stable, and different values differ', () => {
    expect(figureRulePath('32')).toBe(figureRulePath('32'))
    expect(figureRulePath('32')).not.toBe(figureRulePath('11 days'))
    for (const v of numbers(figureRulePath('$1,481'))) expect(v >= -1 && v <= 101).toBe(true)
  })
})

describe('mood strip', () => {
  const W = 220, H = 36

  test('scaling: 0 at the bottom, 1 at the top, clamped, gaps stay gaps', () => {
    const pts = stripScale([0, 1, null, 2, -1, Number.NaN, 0.5], W, H)
    expect(pts[0]![0]).toBeLessThan(pts[1]![0])
    expect(pts[0]![1]).toBeGreaterThan(pts[1]![1])
    expect(pts[2]).toBeNull()
    expect(pts[3]![1]).toBe(pts[1]![1])
    expect(pts[4]![1]).toBe(pts[0]![1])
    expect(pts[5]).toBeNull()
    for (const p of pts) if (p) expect(p[0] >= 0 && p[0] <= W && p[1] >= 0 && p[1] <= H).toBe(true)
  })

  test('all gaps: nothing drawn, the mark still has its place', () => {
    const g = stripGeometry([null, null, null], 1, W, H)
    expect(g.runs).toEqual([])
    expect(g.dots).toEqual([])
    expect(g.mark?.y).toBeNull()
    expect(g.mark!.x).toBeGreaterThan(0)
  })

  test('empty and one value', () => {
    expect(stripGeometry([], null, W, H)).toEqual({ runs: [], dots: [], mark: null })
    const one = stripGeometry([0.4], 0, W, H)
    expect(one.runs).toEqual([])
    expect(one.dots.length).toBe(1)
    expect(one.mark).toEqual({ x: W / 2, y: one.dots[0][1] })
  })

  test('gaps split runs; a lone value between gaps is a dot', () => {
    const g = stripGeometry([0.1, 0.2, null, 0.5, null, 0.3, 0.4, 0.6], 6, W, H)
    expect(g.runs.length).toBe(2)
    expect(g.dots.length).toBe(1)
    expect(g.mark!.y).not.toBeNull()
  })

  test('a mark out of range or not a whole day is ignored', () => {
    for (const m of [-1, 3, 1.5, Number.NaN]) expect(stripGeometry([0.1, 0.2, 0.3], m, W, H).mark).toBeNull()
  })

  test('1000 values: no throw, deterministic, every point finite', () => {
    const r = rand(11)
    const values = Array.from({ length: 1000 }, (_, i) => (i % 97 === 0 ? null : r()))
    const a = stripGeometry(values, 500, W, H)
    expect(a).toEqual(stripGeometry(values, 500, W, H))
    expect(a.runs.length).toBeGreaterThan(5)
    for (const d of a.runs) for (const v of numbers(d)) expect(Number.isFinite(v)).toBe(true)
  })

  test('the data points themselves are on the line', () => {
    const values = [0.2, 0.8, 0.5]
    const pts = stripScale(values, W, H)
    const d = stripGeometry(values, null, W, H).runs[0]
    for (const p of pts) {
      const s = `${Math.round(p![0] * 100) / 100} ${Math.round(p![1] * 100) / 100}`
      expect(d.includes(s)).toBe(true)
    }
  })
})
