import { useCallback, useEffect, useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { AddSong } from '@/components/add-song'
import { Player } from '@/components/player'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { fetchSongs, songLabel, type Song } from '@/songs'

function App() {
  const [songs, setSongs] = useState<Song[]>([])
  const [songId, setSongId] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (select?: string) => {
    try {
      const list = await fetchSongs()
      setSongs(list)
      setSongId((current) => select ?? current ?? list[0]?.id ?? null)
      setFailed(false)
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const song = songs.find((s) => s.id === songId)

  return (
    <main className="flex min-h-svh items-center justify-center p-4">
      <div className="flex w-full max-w-3xl flex-col gap-10">
        <Select value={songId ?? undefined} onValueChange={setSongId} disabled={loading}>
          <SelectTrigger className="w-full sm:w-80" aria-busy={loading}>
            {loading ? (
              <span className="text-muted-foreground flex items-center gap-2">
                <LoaderCircle className="animate-spin" />
                Loading songs…
              </span>
            ) : (
              <SelectValue placeholder={failed ? "Couldn't load songs" : 'Select a song'} />
            )}
          </SelectTrigger>
          <SelectContent>
            {songs.map((s) => (
              <SelectItem key={s.id} value={s.id}>
                {songLabel(s)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {song && <Player song={song} />}

        <AddSong onAdded={load} />
      </div>
    </main>
  )
}

export default App
