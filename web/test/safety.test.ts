// Review fixes: hostile or malformed hashes and data never break the app.
import { describe, expect, test } from 'bun:test'
import { dayArg, internalHref, parse } from '../src/route'
import { nearestIndex } from '../src/chart'
import { citeDay } from '../src/text'
import { dateOf } from '../src/views/timeline'

const HOME = { view: 'overview', arg: null, sub: null }

describe('parse', () => {
  test('a malformed % escape falls back to the default route', () => {
    expect(parse('#timeline/%')).toEqual(HOME)
    expect(parse('#%E0%A4%A')).toEqual(HOME)
  })
  test('a good hash still parses', () => {
    expect(parse('#timeline/12')).toEqual({ view: 'timeline', arg: '12', sub: null })
    expect(parse('#memory/agent-alpha/3')).toEqual({ view: 'memory', arg: 'agent-alpha', sub: '3' })
  })
})

describe('internalHref (findings view)', () => {
  test('script and outside URLs get no link', () => {
    for (const bad of ['javascript:alert(1)', 'JAVASCRIPT:alert(1)', 'https://example.com/#agents', '//example.com', ' #agents', '#agents/<b>', '#agents/a"b', '#timeline/%', '#nowhere', '#', '', null, 42, undefined]) {
      expect(internalHref(bad)).toBeNull()
    }
  })
  test('our own routes are rebuilt by href()', () => {
    expect(internalHref('#agents/agent-alpha')).toBe('#agents/agent-alpha')
    expect(internalHref('#timeline/2')).toBe('#timeline/2')
    expect(internalHref('#/entities/moon-bakery')).toBe('#entities/moon-bakery')
    expect(internalHref('#overview')).toBe('#overview')
  })
})

describe('dayArg (timeline and memory day)', () => {
  test('non-numbers and out-of-range days fall back', () => {
    for (const bad of ['foo', '1e100', 'Infinity', 'NaN', '3.5', '-2', '0', '9', ' 2', '', null]) {
      expect(dayArg(bad, 1, 8)).toBeNull()
    }
  })
  test('a day inside the range is kept', () => {
    expect(dayArg('1', 1, 8)).toBe(1)
    expect(dayArg('8', 1, 8)).toBe(8)
  })
  test('dateOf never throws on a bad day', () => {
    const idx = { day: new Map() } as never
    expect(() => dateOf(idx, Number('foo'))).not.toThrow()
    expect(() => dateOf(idx, 1e100)).not.toThrow()
  })
})

describe('nearestIndex (chart pointer)', () => {
  test('no points: -1, so the pointer handler does nothing', () => {
    expect(nearestIndex([], 3)).toBe(-1)
  })
  test('picks the nearest day', () => {
    const pts = [{ day: 1 }, { day: 4 }, { day: 10 }]
    expect(nearestIndex(pts, 0)).toBe(0)
    expect(nearestIndex(pts, 2.4)).toBe(0)
    expect(nearestIndex(pts, 3)).toBe(1)
    expect(nearestIndex(pts, 99)).toBe(2)
  })
})

describe('citeDay (findings cites, schema v1.2.4)', () => {
  test('a cite uses its own day', () => {
    expect(citeDay({ n: 1, ref: 'chat:x', day: 5 })).toBe(5)
    expect(citeDay({ n: 1, ref: 'chat:x', day: 5 }, 9)).toBe(5)
  })
  test('a summary cite falls back to the summary day', () => {
    expect(citeDay({ n: 1, ref: 'chat:x' }, 9)).toBe(9)
  })
  test('a finding cite without a day opens nothing', () => {
    expect(citeDay({ n: 1, ref: 'chat:x' })).toBeNull()
    expect(citeDay({ n: 1, ref: 'chat:x', day: NaN })).toBeNull()
  })
})
