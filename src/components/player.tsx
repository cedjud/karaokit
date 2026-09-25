import { useEffect, useMemo, useState } from 'react'
import { LoaderCircle, Mic, MicOff, Pause, Play } from 'lucide-react'
import { TranscriptView } from '@/components/transcript-view'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { stemUrl, transcriptUrl, type Song } from '@/songs'
import { sungDetector, toSegments, type RawTranscript } from '@/transcript'
import { useStemPlayer } from '@/lib/use-stem-player'

const formatTime = (s: number) => {
  if (!Number.isFinite(s)) return '0:00'
  const m = Math.floor(s / 60)
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

type Props = { song: Song }

export function Player({ song }: Props) {
  const [time, setTime] = useState(0)
  const [raw, setRaw] = useState<RawTranscript | null>(null)
  const [vocalVolume, setVocalVolume] = useState(1)
  const [hideUnsung, setHideUnsung] = useState(true)

  const player = useStemPlayer({
    vocals: stemUrl(song, 'vocals'),
    instrumental: stemUrl(song, 'no_vocals'),
  })
  const { playing, getTime, vocalsBuffer } = player
  const transcript = transcriptUrl(song)

  const isSung = useMemo(() => vocalsBuffer && sungDetector(vocalsBuffer), [vocalsBuffer])
  const segments = useMemo(
    () => (raw ? toSegments(raw, (hideUnsung && isSung) || undefined) : []),
    [raw, hideUnsung, isSung],
  )

  useEffect(() => {
    setTime(0)
    setRaw(null)
    const controller = new AbortController()
    fetch(transcript, { signal: controller.signal })
      .then((r) => r.json())
      .then(setRaw)
      .catch(() => {})
    return () => controller.abort()
  }, [transcript])

  useEffect(() => {
    if (!playing) return
    let frame: number
    const tick = () => {
      setTime(getTime())
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [playing, getTime])

  const changeVocalVolume = (volume: number) => {
    setVocalVolume(volume)
    player.setVocalVolume(volume)
  }

  const toggle = () => (playing ? player.pause() : void player.play())

  const seek = ([value]: number[]) => {
    player.seek(value)
    setTime(value)
  }

  return (
    <div className="flex flex-col gap-10">
      <TranscriptView segments={segments} time={time} />


      {player.failed && (
        <p className="text-destructive text-sm">Couldn't load audio for this song.</p>
      )}

      <div className="flex items-center gap-3">
        <Button
          size="icon"
          onClick={toggle}
          disabled={!player.ready}
          aria-label={player.ready ? (playing ? 'Pause' : 'Play') : 'Loading'}>
          {!player.ready && !player.failed ? (
            <LoaderCircle className="animate-spin" />
          ) : playing ? (
            <Pause />
          ) : (
            <Play />
          )}
        </Button>
        <span className="text-muted-foreground w-10 text-xs tabular-nums">{formatTime(time)}</span>
        <Slider
          value={[time]}
          max={player.duration || 1}
          step={0.1}
          onValueChange={seek}
          aria-label="Seek"
        />
        <span className="text-muted-foreground w-10 text-right text-xs tabular-nums">
          {formatTime(player.duration)}
        </span>
      </div>

      <div className="flex items-center gap-3">
        <Button
          size="icon"
          variant="ghost"
          onClick={() => changeVocalVolume(vocalVolume > 0 ? 0 : 1)}
          aria-label={vocalVolume > 0 ? 'Mute vocals' : 'Unmute vocals'}
        >
          {vocalVolume > 0 ? <Mic /> : <MicOff />}
        </Button>
        <Slider
          className="w-40"
          value={[vocalVolume]}
          max={1}
          step={0.01}
          onValueChange={([v]) => changeVocalVolume(v)}
          aria-label="Vocal volume"
        />
        <Button
          className="ml-auto"
          size="sm"
          variant={hideUnsung ? 'secondary' : 'ghost'}
          onClick={() => setHideUnsung((h) => !h)}
          aria-pressed={hideUnsung}
          title="Hide lyric lines where the vocal stem is silent (Whisper tends to invent these)"
        >
          Hide lines with no vocals
        </Button>
      </div>
    </div>
  )
}
