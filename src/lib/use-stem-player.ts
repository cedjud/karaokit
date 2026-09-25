import { useCallback, useEffect, useRef, useState } from 'react'

type Stems = { vocals: string; instrumental: string }

type Audio = {
  ctx: AudioContext
  vocalsGain: GainNode
}

// Plays vocal + instrumental stems in sample-accurate sync with Web Audio.
// Both stems are fully downloaded and decoded before playback is possible.
export function useStemPlayer({ vocals, instrumental }: Stems) {
  const audioRef = useRef<Audio>(null)
  const buffersRef = useRef<Record<keyof Stems, AudioBuffer>>(null)
  const sourcesRef = useRef<AudioBufferSourceNode[]>([])
  // Context time at which the song's 0:00 played; only meaningful while playing
  const startedAtRef = useRef(0)
  // Song position to resume from while paused
  const offsetRef = useRef(0)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  // Exposed so callers can inspect where there's actual singing
  const [vocalsBuffer, setVocalsBuffer] = useState<AudioBuffer | null>(null)

  const getAudio = () => {
    if (!audioRef.current) {
      const ctx = new AudioContext()
      const vocalsGain = ctx.createGain()
      vocalsGain.connect(ctx.destination)
      audioRef.current = { ctx, vocalsGain }
    }
    return audioRef.current
  }

  const stopSources = () => {
    for (const source of sourcesRef.current) {
      source.onended = null
      source.stop()
    }
    sourcesRef.current = []
  }

  const startSources = (offset: number) => {
    const buffers = buffersRef.current
    if (!buffers) return
    const { ctx, vocalsGain } = getAudio()

    const vocalsSource = ctx.createBufferSource()
    vocalsSource.buffer = buffers.vocals
    vocalsSource.connect(vocalsGain)

    const instrumentalSource = ctx.createBufferSource()
    instrumentalSource.buffer = buffers.instrumental
    instrumentalSource.connect(ctx.destination)
    instrumentalSource.onended = () => {
      sourcesRef.current = []
      offsetRef.current = 0
      setPlaying(false)
    }

    const now = ctx.currentTime
    vocalsSource.start(now, offset)
    instrumentalSource.start(now, offset)
    startedAtRef.current = now - offset
    sourcesRef.current = [vocalsSource, instrumentalSource]
  }

  const getTime = useCallback(() => {
    const ctx = audioRef.current?.ctx
    const buffers = buffersRef.current
    if (!ctx || !buffers || !sourcesRef.current.length) return offsetRef.current
    return Math.min(ctx.currentTime - startedAtRef.current, buffers.instrumental.duration)
  }, [])

  const play = async () => {
    if (!buffersRef.current || sourcesRef.current.length) return
    await getAudio().ctx.resume()
    startSources(offsetRef.current)
    setPlaying(true)
  }

  const pause = () => {
    offsetRef.current = getTime()
    stopSources()
    setPlaying(false)
  }

  const seek = (time: number) => {
    offsetRef.current = time
    if (sourcesRef.current.length) {
      stopSources()
      startSources(time)
    }
  }

  const setVocalVolume = (volume: number) => {
    const { ctx, vocalsGain } = getAudio()
    // Short ramp avoids clicks while dragging
    vocalsGain.gain.setTargetAtTime(volume, ctx.currentTime, 0.01)
  }

  useEffect(() => {
    const { ctx } = getAudio()
    const controller = new AbortController()
    const load = (url: string) =>
      fetch(url, { signal: controller.signal })
        .then((r) => r.arrayBuffer())
        .then((data) => ctx.decodeAudioData(data))

    Promise.all([load(vocals), load(instrumental)])
      .then(([vocalsBuffer, instrumentalBuffer]) => {
        if (controller.signal.aborted) return
        buffersRef.current = { vocals: vocalsBuffer, instrumental: instrumentalBuffer }
        setDuration(instrumentalBuffer.duration)
        setVocalsBuffer(vocalsBuffer)
        setReady(true)
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true)
      })

    return () => {
      controller.abort()
      stopSources()
      buffersRef.current = null
      offsetRef.current = 0
      setReady(false)
      setFailed(false)
      setPlaying(false)
      setDuration(0)
      setVocalsBuffer(null)
    }
  }, [vocals, instrumental])

  useEffect(
    () => () => {
      void audioRef.current?.ctx.close()
      audioRef.current = null
    },
    [],
  )

  return {
    ready,
    failed,
    playing,
    duration,
    vocalsBuffer,
    getTime,
    play,
    pause,
    seek,
    setVocalVolume,
  }
}
