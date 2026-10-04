import { describe, expect, test } from 'bun:test'
import { boardText } from '../src/book/scene'

// Synthetic goals only.
describe('boardText', () => {
  test('a short goal is kept, without its final stop', () => {
    expect(boardText('Build a small game together.')).toBe('Build a small game together')
  })
  test('a long goal is cut at a word boundary under 60 characters with an ellipsis', () => {
    const long = 'Collaboratively choose something to make and then make as much of it as you can for everyone'
    const b = boardText(long)
    expect(b.length).toBeLessThanOrEqual(60)
    expect(b.endsWith('…')).toBe(true)
    expect(b.slice(0, -1).endsWith(' ')).toBe(false)
  })
  test('exactly sixty characters stays whole', () => {
    const g = 'x'.repeat(60)
    expect(boardText(g)).toBe(g)
  })
})
