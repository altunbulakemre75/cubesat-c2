import { useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createSatellite, uploadTLE } from '../api/satellites'

interface Props {
  open: boolean
  onClose: () => void
}

export function SatelliteAddModal({ open, onClose }: Props) {
  const qc = useQueryClient()
  const [id, setId] = useState('')
  const [name, setName] = useState('')
  const [noradId, setNoradId] = useState('')
  const [tle1, setTle1] = useState('')
  const [tle2, setTle2] = useState('')
  const [error, setError] = useState('')

  const reset = () => {
    setId(''); setName(''); setNoradId(''); setTle1(''); setTle2(''); setError('')
  }

  const mutation = useMutation({
    mutationFn: async () => {
      await createSatellite({
        id: id.trim(),
        name: name.trim() || undefined,
        norad_id: noradId ? parseInt(noradId, 10) : null,
      })
      if (tle1.trim() && tle2.trim()) {
        await uploadTLE(id.trim(), tle1.trim(), tle2.trim())
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['satellites'] })
      qc.invalidateQueries({ queryKey: ['tles'] })
      reset()
      onClose()
    },
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? String(e)
      setError(msg)
    },
  })

  if (!open) return null

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!id.trim()) {
      setError('Satellite ID zorunlu')
      return
    }
    if ((tle1 && !tle2) || (!tle1 && tle2)) {
      setError('TLE line 1 ve 2 birlikte girilmeli (veya ikisi de boş)')
      return
    }
    mutation.mutate()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
         onClick={onClose}>
      <div className="w-full max-w-lg rounded-lg border border-space-border bg-space-panel p-6"
           onClick={(e) => e.stopPropagation()}>
        <h2 className="mb-4 font-mono text-sm font-bold text-white uppercase tracking-wider">
          Add Satellite
        </h2>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1 uppercase tracking-wider">
              Satellite ID *
            </label>
            <input
              type="text"
              value={id}
              onChange={(e) => setId(e.target.value)}
              className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 font-mono text-sm text-white focus:outline-none focus:border-blue-500"
              placeholder="ISS / CUBESAT1 / MYSAT"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1 uppercase tracking-wider">
                Display Name
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 font-mono text-sm text-white focus:outline-none focus:border-blue-500"
                placeholder="ISS (ZARYA)"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1 uppercase tracking-wider">
                NORAD ID
              </label>
              <input
                type="number"
                value={noradId}
                onChange={(e) => setNoradId(e.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 font-mono text-sm text-white focus:outline-none focus:border-blue-500"
                placeholder="25544"
              />
            </div>
          </div>

          <div className="pt-2 border-t border-space-border">
            <p className="text-xs text-gray-500 mb-2 uppercase tracking-wider">
              TLE (optional — enables orbit tracking)
            </p>
            <input
              type="text"
              value={tle1}
              onChange={(e) => setTle1(e.target.value)}
              className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 font-mono text-xs text-white focus:outline-none focus:border-blue-500"
              placeholder="1 25544U 98067A ..."
            />
            <input
              type="text"
              value={tle2}
              onChange={(e) => setTle2(e.target.value)}
              className="mt-2 w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 font-mono text-xs text-white focus:outline-none focus:border-blue-500"
              placeholder="2 25544  51.64 ..."
            />
          </div>

          {error && <p className="text-red-400 text-xs font-mono">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded px-4 py-2 font-mono text-xs text-gray-400 hover:bg-gray-800"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutation.isPending}
              className="rounded bg-blue-600 px-4 py-2 font-mono text-xs font-semibold text-white hover:bg-blue-500 disabled:opacity-50"
            >
              {mutation.isPending ? 'Ekleniyor...' : 'Add Satellite'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
