import { describe, expect, it } from 'vitest'

import { offsetFromPosition } from '../components/textInput.js'
import { cursorLayout, inputVisualHeight, stableComposerColumns } from '../lib/inputMetrics.js'

describe('cursorLayout — word-wrap parity with Ink wrap mode', () => {
  it('places cursor mid-line at its column', () => {
    expect(cursorLayout('hello world', 6, 40)).toEqual({ column: 6, line: 0 })
  })

  it('places cursor at end of a non-full line', () => {
    expect(cursorLayout('hi', 2, 10)).toEqual({ column: 2, line: 0 })
  })

  it('wraps to next line when cursor lands exactly at the right edge', () => {
    // 8 chars on an 8-col line: text fills the row exactly; the cursor's
    // inverted-space cell overflows to col 0 of the next row.
    expect(cursorLayout('abcdefgh', 8, 8)).toEqual({ column: 0, line: 1 })
  })

  it('moves an overflowing word to the next line instead of splitting it', () => {
    // Normal prose should wrap as "hello \nworld", not "hello wo\nrld".
    expect(cursorLayout('hello wo', 8, 8)).toEqual({ column: 0, line: 1 })
    expect(cursorLayout('hello wor', 9, 8)).toEqual({ column: 3, line: 1 })
    expect(cursorLayout('hello worl', 10, 8)).toEqual({ column: 4, line: 1 })
  })

  it('moves the cursor to the next row at a hard-wrap boundary before more text', () => {
    expect(cursorLayout('abcdefghi', 8, 8)).toEqual({ column: 0, line: 1 })
  })

  it('keeps the cursor stop for whitespace before a moved word', () => {
    expect(cursorLayout('hello wor', 6, 8)).toEqual({ column: 6, line: 0 })
  })

  it('honours explicit newlines', () => {
    expect(cursorLayout('one\ntwo', 5, 40)).toEqual({ column: 1, line: 1 })
    expect(cursorLayout('one\ntwo', 4, 40)).toEqual({ column: 0, line: 1 })
  })

  it('does not wrap when cursor is before the right edge', () => {
    expect(cursorLayout('abcdefg', 7, 8)).toEqual({ column: 7, line: 0 })
  })
})

describe('input metrics helpers', () => {
  it('computes visual height from the wrapped cursor line', () => {
    expect(inputVisualHeight('abcdefgh', 8)).toBe(2)
    expect(inputVisualHeight('one\ntwo', 40)).toBe(2)
  })

  it('reserves gutters on wide panes without starving narrow composer width', () => {
    expect(stableComposerColumns(100, 3)).toBe(93)
    expect(stableComposerColumns(100, 5)).toBe(91)
    expect(stableComposerColumns(10, 3)).toBe(5)
    expect(stableComposerColumns(6, 3)).toBe(1)
  })
})

describe('offsetFromPosition — char-wrap inverse of cursorLayout', () => {
  it('returns 0 for empty input', () => {
    expect(offsetFromPosition('', 0, 0, 10)).toBe(0)
  })

  it('maps clicks within a single line', () => {
    expect(offsetFromPosition('hello', 0, 3, 40)).toBe(3)
  })

  it('maps clicks past end to value length', () => {
    expect(offsetFromPosition('hi', 0, 10, 40)).toBe(2)
  })

  it('maps clicks on a wrapped second row at cols boundary', () => {
    // "abcdefghij" at cols=8 wraps to "abcdefgh\nij" — click at row 1 col 0
    // should land on 'i' (offset 8).
    expect(offsetFromPosition('abcdefghij', 1, 0, 8)).toBe(8)
  })

  it('maps clicks on whitespace before a moved word', () => {
    expect(offsetFromPosition('hello wor', 0, 6, 8)).toBe(6)
  })

  it('maps clicks past a \\n into the target line', () => {
    expect(offsetFromPosition('one\ntwo', 1, 2, 40)).toBe(6)
  })
})
