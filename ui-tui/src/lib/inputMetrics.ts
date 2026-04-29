import { stringWidth } from '@hermes/ink'

import type { Role } from '../types.js'

export const COMPOSER_PROMPT_GAP_WIDTH = 1

let _seg: Intl.Segmenter | null = null
const seg = () => (_seg ??= new Intl.Segmenter(undefined, { granularity: 'grapheme' }))

interface VisualLine {
  end: number
  start: number
}

type LayoutStop = { column: number; line: number; offset: number }

const isWhitespace = (value: string) => /\s/.test(value)
const isSoftWrapWhitespace = (segment: string) => /\s/u.test(segment) && segment !== '\n'

const graphemes = (value: string) =>
  [...seg().segment(value)].map(({ segment, index }) => ({
    end: index + segment.length,
    index,
    segment,
    width: Math.max(1, stringWidth(segment))
  }))

function visualLines(value: string, cols: number): VisualLine[] {
  const width = Math.max(1, cols)
  const lines: VisualLine[] = []
  let sourceLineStart = 0

  for (const sourceLine of value.split('\n')) {
    const parts = graphemes(sourceLine)

    if (!parts.length) {
      lines.push({ start: sourceLineStart, end: sourceLineStart })
      sourceLineStart += 1
      continue
    }

    let lineStartPart = 0
    let lineStartOffset = sourceLineStart
    let column = 0
    let breakPart: null | number = null
    let i = 0

    while (i < parts.length) {
      const part = parts[i]!
      const partStart = sourceLineStart + part.index

      if (column + part.width > width && i > lineStartPart) {
        if (breakPart !== null && breakPart > lineStartPart) {
          const breakOffset = sourceLineStart + parts[breakPart - 1]!.end
          lines.push({ start: lineStartOffset, end: breakOffset })
          lineStartPart = breakPart
          lineStartOffset = breakOffset
        } else {
          lines.push({ start: lineStartOffset, end: partStart })
          lineStartPart = i
          lineStartOffset = partStart
        }

        column = 0
        breakPart = null
        i = lineStartPart
        continue
      }

      column += part.width

      if (isWhitespace(part.segment)) {
        breakPart = i + 1
      }

      i += 1

      if (column >= width && i < parts.length) {
        const next = parts[i]!
        const nextStartsWord = !isWhitespace(next.segment)

        if (breakPart !== null && breakPart > lineStartPart && nextStartsWord) {
          const breakOffset = sourceLineStart + parts[breakPart - 1]!.end
          lines.push({ start: lineStartOffset, end: breakOffset })
          lineStartPart = breakPart
          lineStartOffset = breakOffset
          column = 0
          breakPart = null
          i = lineStartPart
        }
      }
    }

    lines.push({ start: lineStartOffset, end: sourceLineStart + sourceLine.length })
    sourceLineStart += sourceLine.length + 1
  }

  return lines.length ? lines : [{ start: 0, end: 0 }]
}

function layoutStops(value: string, cols: number) {
  const w = Math.max(1, cols)
  const segments = Array.from(seg().segment(value), part => ({
    end: part.index + part.segment.length,
    index: part.index,
    segment: part.segment,
    width: Math.max(0, stringWidth(part.segment))
  }))
  const stops: LayoutStop[] = [{ column: 0, line: 0, offset: 0 }]
  let column = 0
  let line = 0
  let i = 0

  const addStop = (offset: number) => {
    stops.push({ column, line, offset })
  }

  const renderSegment = (part: (typeof segments)[number]) => {
    if (part.segment === '\n') {
      line++
      column = 0
      addStop(part.end)

      return
    }

    const width = part.width
    if (!width) {
      addStop(part.end)

      return
    }

    if (column + width > w) {
      line++
      column = 0
      addStop(part.index)
    }

    column += width
    addStop(part.end)
  }

  while (i < segments.length) {
    const part = segments[i]!

    if (part.segment === '\n' || isSoftWrapWhitespace(part.segment)) {
      renderSegment(part)
      i++

      continue
    }

    let j = i
    let wordWidth = 0

    while (
      j < segments.length &&
      segments[j]!.segment !== '\n' &&
      !isSoftWrapWhitespace(segments[j]!.segment)
    ) {
      wordWidth += segments[j]!.width
      j++
    }

    // Match Ink's normal wrap mode: prefer moving an overflowing word to the
    // next row, but still hard-wrap words longer than the available row.
    if (column > 0 && wordWidth > 0 && column + wordWidth > w) {
      line++
      column = 0
      addStop(part.index)
    }

    while (i < j) {
      renderSegment(segments[i]!)
      i++
    }
  }

  return stops
}

/**
 * Mirrors Ink's normal <Text wrap="wrap"> behavior: word wrap first, then
 * hard-wrap single words that are longer than the available input width.
 * Returns the zero-based visual line and column of the cursor cell.
 */
export function cursorLayout(value: string, cursor: number, cols: number) {
  const pos = Math.max(0, Math.min(cursor, value.length))
  const stops = layoutStops(value, cols)
  const firstAtOrAfter = stops.findIndex(item => item.offset >= pos)
  let stop = firstAtOrAfter >= 0 ? stops[firstAtOrAfter]! : { column: 0, line: 0, offset: pos }

  // Duplicate offsets can represent both sides of a wrap boundary. If the
  // pre-wrap cursor cell would overflow while more text follows, prefer the
  // post-wrap visual stop. For soft word-wrap after whitespace, keep the
  // pre-wrap whitespace stop so row/column maps back naturally.
  if (stop.offset === pos && pos < value.length && stop.column >= Math.max(1, cols)) {
    for (let i = firstAtOrAfter + 1; i < stops.length && stops[i]!.offset === pos; i++) {
      stop = stops[i]!
    }
  }

  // A trailing cursor-cell overflows to the next row at the wrap column.
  if (pos === value.length && stop.column >= Math.max(1, cols)) {
    return { column: 0, line: stop.line + 1 }
  }

  return { column: stop.column, line: stop.line }
}

export function offsetFromPosition(value: string, row: number, col: number, cols: number) {
  if (!value.length) {
    return 0
  }

  const lines = visualLines(value, cols)
  const target = lines[Math.max(0, Math.min(lines.length - 1, Math.floor(row)))]!
  const targetCol = Math.max(0, Math.floor(col))
  let column = 0

  for (const part of graphemes(value.slice(target.start, target.end))) {
    if (targetCol <= column + Math.max(0, part.width - 1)) {
      return target.start + part.index
    }

    column += part.width
  }

  return target.end
}

export function inputVisualHeight(value: string, columns: number) {
  return cursorLayout(value, value.length, columns).line + 1
}

export function composerPromptWidth(promptText: string) {
  return Math.max(1, stringWidth(promptText)) + COMPOSER_PROMPT_GAP_WIDTH
}

export function transcriptGutterWidth(role: Role, userPrompt: string) {
  return role === 'user' ? composerPromptWidth(userPrompt) : 3
}

export function transcriptBodyWidth(totalCols: number, role: Role, userPrompt: string) {
  return Math.max(20, totalCols - transcriptGutterWidth(role, userPrompt) - 2)
}

export function stableComposerColumns(totalCols: number, promptWidth: number) {
  // Physical render/wrap width. Always reserve outer composer padding and
  // prompt prefix. Only reserve the transcript scrollbar gutter when the
  // terminal is wide enough; on narrow panes, preserving input columns beats
  // keeping gutters visually aligned.
  return Math.max(1, totalCols - promptWidth - 2 - (totalCols - promptWidth >= 24 ? 2 : 0))
}
