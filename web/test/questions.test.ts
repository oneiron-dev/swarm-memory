// The questions page reads data/questions.json beside the bundle first and the web build's copy second.
import { describe, expect, test } from 'bun:test'
import { questionsFrom, type QuestionsFile } from '../src/views/questions'

const file = (model: string): QuestionsFile => ({ schema: 'swarm-questions/1', model, questions: [] })
const got = (j: unknown) => () => Promise.resolve(j as QuestionsFile | null)

describe('questionsFrom', () => {
  test('the bundle copy wins when it is there', async () => {
    expect((await questionsFrom(got(file('bundle')), got(file('build'))))?.model).toBe('bundle')
  })
  test('a bundle without the file falls back to the web build copy', async () => {
    expect((await questionsFrom(got(null), got(file('build'))))?.model).toBe('build')
  })
  test('a bundle file without a questions list falls back too', async () => {
    expect((await questionsFrom(got({ schema: 'x' }), got(file('build'))))?.model).toBe('build')
  })
  test('neither: null, and the page says the answers are not in this build', async () => {
    expect(await questionsFrom(got(null), got(null))).toBeNull()
    expect(await questionsFrom(got(null), got({ nope: 1 }))).toBeNull()
  })
})
