/**
 * Detection statistics charts using Recharts.
 */

import {
  Bar,
  BarChart,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { EpisodeDetectionSummary } from '@/types/detection';

interface DetectionChartsProps {
  summary: EpisodeDetectionSummary;
}

const COLORS = [
  '#FF6B6B',
  '#4ECDC4',
  '#45B7D1',
  '#96CEB4',
  '#FFEAA7',
  '#DDA0DD',
  '#98D8C8',
  '#F7DC6F',
  '#74B9FF',
  '#A29BFE',
];

export function DetectionCharts({ summary }: DetectionChartsProps) {
  // Prepare class distribution data
  const classData = Object.entries(summary.class_summary)
    .map(([name, stats]) => ({
      name,
      count: stats.count,
      avgConfidence: stats.avg_confidence,
      percentage: 0,
    }))
    .sort((a, b) => b.count - a.count);

  // Calculate percentages
  const totalDetections = classData.reduce((sum, c) => sum + c.count, 0);
  classData.forEach((c) => {
    c.percentage = totalDetections > 0 ? (c.count / totalDetections) * 100 : 0;
  });

  // Prepare detections over time data
  const timeData = summary.detections_by_frame
    .filter((_, i) => i % Math.max(1, Math.floor(summary.detections_by_frame.length / 50)) === 0)
    .map((result) => ({
      frame: result.frame,
      detections: result.detections.length,
    }));

  // Confidence distribution histogram
  const confidenceBuckets = [0, 0, 0, 0, 0]; // 0-20, 20-40, 40-60, 60-80, 80-100
  summary.detections_by_frame.forEach((frame) => {
    frame.detections.forEach((det) => {
      const bucket = Math.min(4, Math.floor(det.confidence * 5));
      confidenceBuckets[bucket]++;
    });
  });
  const confidenceData = confidenceBuckets.map((count, i) => ({
    range: `${i * 20}-${(i + 1) * 20}%`,
    count,
  }));

  return (
    <div className="space-y-6">
      {/* Class distribution pie chart */}
      <div>
        <h4 className="text-sm font-medium mb-2">Class Distribution</h4>
        {classData.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-4">
            No detections to display
          </p>
        ) : (
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={classData}
                  dataKey="count"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  outerRadius={60}
                  label={({ name, percent }) =>
                    `${name} ${((percent ?? 0) * 100).toFixed(0)}%`
                  }
                  labelLine={false}
                >
                  {classData.map((entry, index) => (
                    <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value) => [
                    `${value} (${((Number(value) / totalDetections) * 100).toFixed(1)}%)`,
                    'Count',
                  ]}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Detections over time line chart */}
      <div>
        <h4 className="text-sm font-medium mb-2">Detections Over Time</h4>
        <div className="h-32">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={timeData}>
              <XAxis
                dataKey="frame"
                tick={{ fontSize: 10 }}
                interval="preserveStartEnd"
              />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip
                formatter={(value) => [`${value} detections`, 'Count']}
                labelFormatter={(label) => `Frame ${label}`}
              />
              <Line
                type="monotone"
                dataKey="detections"
                stroke="#4ECDC4"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Confidence distribution histogram */}
      <div>
        <h4 className="text-sm font-medium mb-2">Confidence Distribution</h4>
        <div className="h-32">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={confidenceData}>
              <XAxis dataKey="range" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} />
              <Tooltip
                formatter={(value) => [`${value} detections`, 'Count']}
              />
              <Bar dataKey="count" fill="#FF6B6B" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-4 text-center">
        <div className="bg-muted p-3 rounded-lg">
          <div className="text-2xl font-bold text-blue-500">
            {summary.total_detections}
          </div>
          <div className="text-xs text-muted-foreground">Total Detections</div>
        </div>
        <div className="bg-muted p-3 rounded-lg">
          <div className="text-2xl font-bold text-green-500">{classData.length}</div>
          <div className="text-xs text-muted-foreground">Unique Classes</div>
        </div>
        <div className="bg-muted p-3 rounded-lg">
          <div className="text-2xl font-bold text-purple-500">
            {summary.processed_frames}
          </div>
          <div className="text-xs text-muted-foreground">Frames Processed</div>
        </div>
      </div>

      {/* Class breakdown table */}
      {classData.length > 0 && (
        <div>
          <h4 className="text-sm font-medium mb-2">Class Breakdown</h4>
          <div className="rounded-lg border overflow-hidden">
            <Table>
              <TableHeader className="bg-muted">
                <TableRow>
                  <TableHead>Class</TableHead>
                  <TableHead className="text-right">Count</TableHead>
                  <TableHead className="text-right">%</TableHead>
                  <TableHead className="text-right">Avg Conf</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {classData.slice(0, 8).map((cls, i) => (
                  <TableRow key={cls.name}>
                    <TableCell className="flex items-center gap-2">
                      <span
                        className="w-3 h-3 rounded-full"
                        style={{ backgroundColor: COLORS[i % COLORS.length] }}
                      />
                      {cls.name}
                    </TableCell>
                    <TableCell className="text-right">{cls.count}</TableCell>
                    <TableCell className="text-right">{cls.percentage.toFixed(1)}%</TableCell>
                    <TableCell className="text-right">
                      {(cls.avgConfidence * 100).toFixed(0)}%
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  );
}
