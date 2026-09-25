export type Song = {
  id: string
  artist: string
  title: string
}

export type Job =
  | { status: 'pending' }
  | { status: 'done'; result: { name: string } }
  | { status: 'failed'; error: string }

const apiUrl = import.meta.env.VITE_API_URL
// Empty = serve from ./public (local test files)
const mediaUrl = import.meta.env.VITE_MEDIA_URL ?? ''
const auth = { Authorization: `Bearer ${import.meta.env.VITE_API_TOKEN}` }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiUrl}${path}`, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const fetchSongs = (signal?: AbortSignal) => request<Song[]>('/songs', { signal })

// Whisper ISO-639-1 codes; 'auto' lets Whisper detect the language.
export const languages = [
  { code: 'en', label: 'English' },
  { code: 'auto', label: 'Auto-detect' },
  { code: 'fr', label: 'French' },
  { code: 'es', label: 'Spanish' },
  { code: 'de', label: 'German' },
  { code: 'it', label: 'Italian' },
  { code: 'pt', label: 'Portuguese' },
  { code: 'nl', label: 'Dutch' },
  { code: 'sv', label: 'Swedish' },
  { code: 'no', label: 'Norwegian' },
  { code: 'da', label: 'Danish' },
  { code: 'ja', label: 'Japanese' },
  { code: 'ko', label: 'Korean' },
]

// Resolves to a job id to poll with getJob
export const addSong = (url: string, name?: string, language = 'en') =>
  request<{ call_id: string }>('/songs', {
    method: 'POST',
    headers: { ...auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, name: name || null, language }),
  }).then((r) => r.call_id)

export const getJob = (callId: string) =>
  request<Job>(`/jobs/${encodeURIComponent(callId)}`, { headers: auth })

export const songLabel = (song: Song) =>
  song.artist ? `${song.artist} – ${song.title}` : song.title

export const stemUrl = (song: Song, stem: 'vocals' | 'no_vocals') =>
  `${mediaUrl}/stems/${encodeURIComponent(song.id)}/${stem}.mp3`
export const transcriptUrl = (song: Song) =>
  `${mediaUrl}/transcripts/${encodeURIComponent(song.id)}.json`
