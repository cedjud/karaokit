export type Word = { word: string; start: number; end: number }

export type Segment = { start: number; end: number; words: Word[] }

type RawSegment = { start: number; end: number; text: string; avg_logprob: number }

export type RawTranscript = {
  segments: RawSegment[]
  words: Word[]
}

// Whisper hallucinates during instrumental parts: zero-length repeats,
// low-confidence gibberish, and stock phrases from its training data.
// (no_speech_prob is unreliable for sung vocals, so it isn't used.)
function isNoise(s: RawSegment) {
  return (
    s.end - s.start < 0.25 ||
    s.avg_logprob < -1.5 ||
    /thanks for watching|subscribe/i.test(s.text)
  )
}

// Whether a word lines up with actual singing in the vocal stem
export type IsSung = (word: Word) => boolean

// A word counts as sung when the vocal stem's level over it is within 20 dB
// of the song's average vocal level. Whisper often gives words zero length,
// so at least `minWindow` seconds from the word's start are measured.
const minWindow = 0.3

export function sungDetector(vocals: AudioBuffer): IsSung {
  const channels = Array.from({ length: vocals.numberOfChannels }, (_, i) =>
    vocals.getChannelData(i),
  )
  const meanSquare = (from: number, to: number) => {
    let sum = 0
    for (const data of channels) for (let i = from; i < to; i++) sum += data[i] ** 2
    return sum / (Math.max(to - from, 1) * channels.length)
  }
  const threshold = meanSquare(0, vocals.length) * 10 ** (-20 / 10)
  const rate = vocals.sampleRate
  return ({ start, end }) => {
    const from = Math.min(Math.floor(start * rate), vocals.length - 1)
    const to = Math.min(Math.floor(Math.max(end, start + minWindow) * rate), vocals.length)
    return meanSquare(from, to) > threshold
  }
}

// Whisper also repeats earlier lyrics over instrumental parts with full
// confidence. Those segments have almost no sung words, real ones 75%+.
const minSungShare = 0.25

// Groups the flat word list into segments by start time, dropping noise
// segments along with their words. With `isSung`, segments sung over
// silence in the vocal stem are dropped too.
export function toSegments({ segments, words }: RawTranscript, isSung?: IsSung): Segment[] {
  const result = segments.map((s) => ({
    start: s.start,
    end: s.end,
    words: [] as Word[],
    noise: isNoise(s),
  }))
  let i = 0
  for (const word of words) {
    while (i < result.length - 1 && word.start >= result[i + 1].start) i++
    result[i].words.push(word)
  }
  const unsung = (s: Segment) =>
    isSung !== undefined && s.words.filter(isSung).length / s.words.length < minSungShare
  return result
    .filter((s) => !s.noise && s.words.length > 0 && !unsung(s))
    .map(({ start, end, words }) => ({ start, end, words }))
}

// Segment being sung at `time`, or the next upcoming one during gaps
export function activeSegmentIndex(segments: Segment[], time: number) {
  const next = segments.findIndex((s) => time < s.end)
  return next === -1 ? segments.length - 1 : next
}
