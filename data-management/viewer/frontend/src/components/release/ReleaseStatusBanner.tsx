import { QueryClientProvider } from '@tanstack/react-query'
import { AlertCircle, CheckCircle2, Loader2, PackageCheck, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { useReleaseJobs } from '@/hooks/use-releases'
import { queryClient } from '@/lib/query-client'
import type { ReleaseJobState } from '@/types'

interface ReleaseStatusBannerProps {
  datasetId: string
  onViewStatus: () => void
}

const ACTIVE_STATES = new Set<ReleaseJobState>(['queued', 'running', 'verifying', 'publishing'])

function dismissedKey(datasetId: string): string {
  return `dataviewer:dismissed-release-job:${datasetId}`
}

function persistedJobKey(datasetId: string): string {
  return `dataviewer:release-job:${datasetId}`
}

function ReleaseStatusBannerContent({ datasetId, onViewStatus }: ReleaseStatusBannerProps) {
  const jobs = useReleaseJobs(datasetId)
  const [dismissedJobId, setDismissedJobId] = useState<string | null>(null)
  const latest = jobs.data?.[0]

  useEffect(() => {
    setDismissedJobId(window.localStorage.getItem(dismissedKey(datasetId)))
  }, [datasetId])

  if (!latest || (!ACTIVE_STATES.has(latest.state) && dismissedJobId === latest.jobId)) {
    return null
  }

  const active = ACTIVE_STATES.has(latest.state)
  const failed = latest.state === 'failed' || latest.state === 'conflict'
  const percent = latest.progress?.percent

  const dismiss = () => {
    window.localStorage.setItem(dismissedKey(datasetId), latest.jobId)
    setDismissedJobId(latest.jobId)
  }

  const viewStatus = () => {
    window.localStorage.setItem(persistedJobKey(datasetId), latest.jobId)
    onViewStatus()
  }

  return (
    <div className="bg-muted/50 flex flex-wrap items-center gap-3 border px-3 py-2 text-sm">
      {active ? (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
      ) : failed ? (
        <AlertCircle className="text-destructive h-4 w-4 shrink-0" aria-hidden="true" />
      ) : (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
      )}
      <PackageCheck className="h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-48 flex-1">
        <p className="font-medium">
          Release {latest.releaseId}: {latest.progress?.message ?? latest.state}
        </p>
        {active && (
          <p className="text-muted-foreground text-xs">
            Keep Data Viewer running. Reloading this page does not cancel the job.
          </p>
        )}
      </div>
      {active && percent !== null && percent !== undefined && (
        <div className="flex w-44 items-center gap-2">
          <Progress value={percent} className="h-2" aria-label="Estimated release progress" />
          <span className="text-muted-foreground w-10 text-right text-xs">{percent}%</span>
        </div>
      )}
      <Button type="button" size="sm" variant="outline" onClick={viewStatus}>
        View status
      </Button>
      {!active && (
        <Button type="button" size="icon" variant="ghost" onClick={dismiss} aria-label="Dismiss">
          <X className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}

export function ReleaseStatusBanner(props: ReleaseStatusBannerProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <ReleaseStatusBannerContent {...props} />
    </QueryClientProvider>
  )
}
