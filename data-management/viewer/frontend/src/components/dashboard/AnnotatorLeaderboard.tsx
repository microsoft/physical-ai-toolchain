/**
 * Annotator leaderboard showing top contributors.
 */

import { formatDistanceToNow } from 'date-fns'
import { Award, Clock, Medal, Star, Trophy } from 'lucide-react'

import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

export interface AnnotatorInfo {
  annotator_id: string
  annotator_name: string
  episodes_annotated: number
  average_rating: number
  last_active: string
}

export interface AnnotatorLeaderboardProps {
  /** List of annotators with stats */
  annotators: AnnotatorInfo[]
  /** Maximum number to display */
  limit?: number
  /** Additional class names */
  className?: string
}

const RANK_ICONS = [
  <Trophy key="1" className="h-5 w-5 text-yellow-500" />,
  <Medal key="2" className="h-5 w-5 text-gray-400" />,
  <Award key="3" className="h-5 w-5 text-amber-600" />,
]

/**
 * Displays annotator leaderboard ranked by episodes annotated.
 */
export function AnnotatorLeaderboard({
  annotators,
  limit = 10,
  className,
}: AnnotatorLeaderboardProps) {
  const sortedAnnotators = [...annotators]
    .sort((a, b) => b.episodes_annotated - a.episodes_annotated)
    .slice(0, limit)

  const getInitials = (name: string) => {
    const parts = name.split(' ')
    if (parts.length >= 2) {
      return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
    }
    return name.slice(0, 2).toUpperCase()
  }

  const formatLastActive = (timestamp: string) => {
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
          <Trophy className="h-5 w-5 text-yellow-500" />
          Top Annotators
        </CardTitle>
      </CardHeader>
      <CardContent>
        {sortedAnnotators.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            No annotator activity yet
          </p>
        ) : (
          <div className="space-y-3">
            {sortedAnnotators.map((annotator, index) => (
              <div
                key={annotator.annotator_id}
                className={cn(
                  'flex items-center gap-3 rounded-lg p-2 transition-colors',
                  index < 3 && 'bg-muted/50',
                )}
              >
                {/* Rank */}
                <div className="flex w-8 items-center justify-center">
                  {index < 3 ? (
                    RANK_ICONS[index]
                  ) : (
                    <span className="text-sm font-medium text-muted-foreground">#{index + 1}</span>
                  )}
                </div>

                {/* Avatar */}
                <Avatar className="h-9 w-9">
                  <AvatarFallback className="bg-primary/10 text-xs font-medium">
                    {getInitials(annotator.annotator_name)}
                  </AvatarFallback>
                </Avatar>

                {/* Info */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{annotator.annotator_name}</span>
                    {index === 0 && (
                      <Badge variant="secondary" className="text-xs">
                        Top
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span>{annotator.episodes_annotated} episodes</span>
                    <span className="flex items-center gap-0.5">
                      <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />
                      {annotator.average_rating.toFixed(1)}
                    </span>
                  </div>
                </div>

                {/* Last active */}
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Clock className="h-3 w-3" />
                  <span className="hidden sm:inline">
                    {formatLastActive(annotator.last_active)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
