/**
 * Rating distribution chart showing histogram of ratings.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export interface RatingDistributionProps {
  /** Rating distribution as record of rating -> count */
  distribution: Record<string, number>;
  /** Chart title */
  title?: string;
  /** Color scheme */
  colorScheme?: 'rating' | 'quality' | 'neutral';
  /** Additional class names */
  className?: string;
}

const RATING_COLORS = {
  1: '#ef4444', // red
  2: '#f97316', // orange
  3: '#eab308', // yellow
  4: '#84cc16', // lime
  5: '#22c55e', // green
};

const QUALITY_COLORS = {
  1: '#dc2626', // red
  2: '#ea580c', // orange
  3: '#ca8a04', // yellow
  4: '#65a30d', // lime
  5: '#16a34a', // green
};

const NEUTRAL_COLORS = {
  1: '#6b7280',
  2: '#6b7280',
  3: '#6b7280',
  4: '#6b7280',
  5: '#6b7280',
};

/**
 * Displays rating distribution as a bar chart.
 */
export function RatingDistribution({
  distribution,
  title = 'Rating Distribution',
  colorScheme = 'rating',
  className,
}: RatingDistributionProps) {
  // Convert distribution to chart data
  const data = [1, 2, 3, 4, 5].map((rating) => ({
    rating: rating.toString(),
    count: distribution[rating.toString()] || 0,
    label: `${rating} Star${rating !== 1 ? 's' : ''}`,
  }));

  const total = data.reduce((sum, d) => sum + d.count, 0);

  const colors =
    colorScheme === 'rating'
      ? RATING_COLORS
      : colorScheme === 'quality'
        ? QUALITY_COLORS
        : NEUTRAL_COLORS;

  return (
    <Card className={cn('', className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">{title}</CardTitle>
          <span className="text-sm text-muted-foreground">
            {total.toLocaleString()} total
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="rating"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12 }}
              />
              <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 12 }} />
              <Tooltip
                content={(props) => {
                  const { active, payload } = props as unknown as { active?: boolean; payload?: Array<{ payload: { label: string; count: number; rating: string } }> };
                  if (active && payload && payload.length) {
                    const data = payload[0].payload;
                    const percent =
                      total > 0 ? Math.round((data.count / total) * 100) : 0;
                    return (
                      <div className="rounded-lg border bg-background px-3 py-2 shadow-sm">
                        <p className="font-medium">{data.label}</p>
                        <p className="text-sm text-muted-foreground">
                          {data.count.toLocaleString()} episodes ({percent}%)
                        </p>
                      </div>
                    );
                  }
                  return null;
                }}
              />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {data.map((entry) => (
                  <Cell
                    key={entry.rating}
                    fill={colors[parseInt(entry.rating, 10) as keyof typeof colors]}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Legend */}
        <div className="mt-3 flex justify-center gap-4">
          {data.map((d) => (
            <div key={d.rating} className="flex items-center gap-1.5 text-xs">
              <div
                className="h-3 w-3 rounded-sm"
                style={{
                  backgroundColor: colors[parseInt(d.rating, 10) as keyof typeof colors],
                }}
              />
              <span className="text-muted-foreground">{d.rating}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
