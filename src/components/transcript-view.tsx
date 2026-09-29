import { Fragment } from 'react'
import { activeSegmentIndex, type Segment, type Word } from '@/transcript'

// Share of the word sung so far, 0-1. Zero-length words flip at their start.
const progress = ({ start, end }: Word, time: number) =>
  end > start ? Math.min(Math.max((time - start) / (end - start), 0), 1) : Number(time >= start)

type Props = { segments: Segment[]; time: number }

export function TranscriptView({ segments, time }: Props) {
  if (segments.length === 0) return null

  const index = activeSegmentIndex(segments, time)
  const segment = segments[index]
  const next = segments[index + 1]

  return (
    <div className="flex min-h-64 flex-col justify-center gap-6">
      <p key={index} className="text-4xl leading-tight font-semibold tracking-tight md:text-5xl">
        {segment.words.map((w, i) => (
          <Fragment key={i}>
            <span
              className="bg-clip-text text-transparent"
              style={{
                // Hard stop at the sung share fills the word left to right
                backgroundImage: `linear-gradient(to right, var(--color-foreground) ${progress(w, time) * 100}%, color-mix(in oklab, var(--color-muted-foreground) 40%, transparent) 0)`,
              }}
            >
              {w.word}
            </span>{' '}
          </Fragment>
        ))}
      </p>
      {next && (
        <p className="text-muted-foreground/40 text-xl font-medium md:text-2xl">
          {next.words.map((w) => w.word).join(' ')}
        </p>
      )}
    </div>
  )
}
