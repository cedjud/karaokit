import { cn } from '@/lib/utils'
import { activeSegmentIndex, type Segment } from '@/transcript'

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
          <span
            key={i}
            className={cn(
              'transition-colors duration-150',
              time < w.start && 'text-muted-foreground/40',
              time >= w.start && time < w.end && 'text-primary',
              time >= w.end && 'text-foreground',
            )}
          >
            {w.word}{' '}
          </span>
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
