/**
 * Recent activity feed showing latest annotation actions.
 */

import { formatDistanceToNow } from 'date-fns'
import { Activity, CheckCircle, Clock, Edit, Eye, FileText } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { getActivityTypeTone, getSemanticToneClasses } from '@/lib/semantic-state'
import { cn } from '@/lib/utils'

export interface ActivityItem {
  id: string
  type: 'annotation' | 'review' | 'edit'
  episode_id: string
  annotator_name: string
  timestamp: string
  summary: string
}

export interface ActivityFeedProps {
  /** List of activity items */
  activities: ActivityItem[]
  /** Maximum number to display */
  limit?: number
  /** Maximum height for scroll area */
  maxHeight?: number
  /** Additional class names */
  className?: string
}

const TYPE_CONFIG = {
  annotation: {
    icon: CheckCircle,
    label: 'Annotated',
  },
  review: {
    icon: Eye,
    label: 'Reviewed',
  },
  edit: {
    icon: Edit,
    label: 'Edited',
  },
}

/**
 * Displays recent annotation activity feed.
 */
export function ActivityFeed({
  activities,
  limit = 20,
  maxHeight = 400,
  className,
}: ActivityFeedProps) {
  const sortedActivities = [...activities]
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, limit)

  const formatTime = (timestamp: string) => {
    try {
      return formatDistanceToNow(new Date(timestamp), { addSuffix: true })
    } catch {
      return 'Unknown'
    }
  }

  return (
    <Card className={cn('', className)}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Activity className="h-5 w-5" />
          Recent Activity
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {sortedActivities.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-12 text-center">
            <Clock className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">No recent activity</p>
          </div>
        ) : (
          <ScrollArea style={{ maxHeight }}>
            <div className="divide-y">
              {sortedActivities.map((activity) => {
                const config = TYPE_CONFIG[activity.type]
                const Icon = config.icon
                const tone = getActivityTypeTone(activity.type)

                return (
                  <div
                    key={activity.id}
                    className="flex items-start gap-3 px-4 py-3 transition-colors hover:bg-muted/50"
                  >
                    {/* Icon */}
                    <div
                      className={cn(
                        'flex h-8 w-8 shrink-0 items-center justify-center rounded-full border',
                        getSemanticToneClasses('surface', tone),
                      )}
                    >
                      <Icon className={cn('h-4 w-4', getSemanticToneClasses('icon', tone))} />
                    </div>

                    {/* Content */}
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate font-medium">{activity.annotator_name}</span>
                        <Badge variant="status" tone={tone} className="text-xs">
                          {config.label}
                        </Badge>
                      </div>
                      <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <FileText className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate">{activity.episode_id}</span>
                      </div>
                      {activity.summary && (
                        <p className="truncate text-xs text-muted-foreground">{activity.summary}</p>
                      )}
                    </div>

                    {/* Time */}
                    <div className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      <span>{formatTime(activity.timestamp)}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  )
}
