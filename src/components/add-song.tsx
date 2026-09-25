import { useEffect, useState, type FormEvent } from 'react'
import { LoaderCircle, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { addSong, getJob, languages } from '@/songs'

const POLL_MS = 3000
const STORAGE_KEY = 'music-wheel:pending-job'

// Storage can throw (private mode, blocked site data); resuming is best-effort.
function loadJob() {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function saveJob(callId: string | null) {
  try {
    if (callId) localStorage.setItem(STORAGE_KEY, callId)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}

type Props = { onAdded: (id: string) => void }

// Starts the download -> separate -> transcribe pipeline and polls until it finishes.
// The pending job survives reloads, so polling resumes on the next visit.
export function AddSong({ onAdded }: Props) {
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [language, setLanguage] = useState('en')
  const [callId, setCallId] = useState<string | null>(loadJob)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => saveJob(callId), [callId])

  useEffect(() => {
    if (!callId) return
    const timer = setInterval(async () => {
      try {
        const job = await getJob(callId)
        if (job.status === 'pending') return
        setCallId(null)
        if (job.status === 'done') {
          setUrl('')
          setName('')
          setLanguage('en')
          onAdded(job.result.name)
        } else {
          setError(job.error)
        }
      } catch {
        // Transient network error; try again next tick
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [callId, onAdded])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      setCallId(await addSong(url.trim(), name.trim(), language))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  const busy = submitting || callId !== null

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          type="url"
          required
          placeholder="YouTube URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={busy}
        />
        <Input
          className="sm:w-64"
          placeholder="artist_title (optional)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
        />
        <Select value={language} onValueChange={setLanguage} disabled={busy}>
          <SelectTrigger className="w-full sm:w-40" aria-label="Lyrics language">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {languages.map((l) => (
              <SelectItem key={l.code} value={l.code}>
                {l.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button type="submit" disabled={busy}>
          {busy ? <LoaderCircle className="animate-spin" /> : <Plus />}
          Add song
        </Button>
      </div>
      {callId && (
        <p className="text-muted-foreground text-sm">
          Downloading, separating and transcribing… usually about a minute.
        </p>
      )}
      {error && <p className="text-destructive text-sm">Couldn't add song: {error}</p>}
    </form>
  )
}
