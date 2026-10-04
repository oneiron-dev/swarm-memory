import { describe, expect, test } from 'bun:test'
import { beatsOf, hookOf, type Chapter } from '../src/book/types'

// Synthetic chapters only: nothing from the dataset.
const base: Chapter = {
  n: 1, goal: 'Build a thing', title: 'A Thing Was Built', first_day: 1, last_day: 4, first_date: '2025-01-01', last_date: '2025-01-04',
  days: [1, 2, 3, 4], agents: ['a', 'b'], stats: { days: 4, chat: 10, sessions: 2, memories: 1, human_chat: 0, agents: 2 },
  text: 'The first sentence is here [1]. The second one follows [2][3]. A third closes the week [4].',
  cites: [{ n: 1, ref: 'x:1', day: 1 }, { n: 2, ref: 'x:2', day: 3 }, { n: 3, ref: 'x:3', day: 2 }, { n: 4, ref: 'x:4', day: 9 }],
}

describe('beatsOf', () => {
  test('falls back to the paragraph sentences with their own cites and the earliest cited day', () => {
    const b = beatsOf(base)
    expect(b.length).toBe(3)
    expect(b[0].text).toBe('The first sentence is here [1].')
    expect(b[0].cites.map((c) => c.n)).toEqual([1])
    expect(b[1].cites.map((c) => c.n)).toEqual([2, 3])
    expect(b[1].day).toBe(2)          // the earliest of the cited days inside the chapter
    expect(b[2].day).toBeNull()       // day 9 is outside the chapter
  })
  test('never makes more than six beats from a long paragraph', () => {
    const text = Array.from({ length: 14 }, (_, i) => `Sentence number ${i + 1} is here [1].`).join(' ')
    expect(beatsOf({ ...base, text }).length).toBeLessThanOrEqual(6)
  })
  test('uses written beats when the chapter has them', () => {
    const b = beatsOf({ ...base, beats: [{ text: 'One short beat [2].', cites: [2] }, { text: 'Another [4].', cites: [4] }] })
    expect(b.length).toBe(2)
    expect(b[0].cites[0].ref).toBe('x:2')
  })
  test('an empty chapter has no beats', () => {
    expect(beatsOf({ ...base, text: '' }).length).toBe(0)
  })
})

describe('hookOf', () => {
  test('the hook, else the title', () => {
    expect(hookOf(base)).toBe('A Thing Was Built')
    expect(hookOf({ ...base, hook: '  A hook line ' })).toBe('A hook line')
    expect(hookOf({ ...base, hook: '' })).toBe('A Thing Was Built')
  })
})
