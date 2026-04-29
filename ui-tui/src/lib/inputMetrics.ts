import { stringWidth } from '@hermes/ink'

let _seg: Intl.Segmenter | null = null
const seg = () => (_seg ??= new Intl.Segmenter(undefined, { granularity: 'grapheme' }))

type LayoutStop = { column: number; line: number; offset: number }

const isSoftWrapWhitespace = (segment: string) => /\s/u.test(segment) && segment !== '\n'

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

export function inputVisualHeight(value: string, columns: number) {
  return cursorLayout(value, value.length, columns).line + 1
}

export function stableComposerColumns(totalCols: number, promptWidth: number) {
  // Physical render/wrap width. Always reserve outer composer padding and
  // prompt prefix. Only reserve the transcript scrollbar gutter when the
  // terminal is wide enough; on narrow panes, preserving input columns beats
  // keeping gutters visually aligned.
  return Math.max(1, totalCols - promptWidth - 2 - (totalCols - promptWidth >= 24 ? 2 : 0))
}
