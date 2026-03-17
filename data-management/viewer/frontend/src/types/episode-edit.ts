/**
 * Type definitions for episode editing and export operations.
 *
 * Supports non-destructive frame editing with crop/resize transforms,
 * frame removal, and sub-task segmentation.
 */

// ============================================================================
// Image Transform Types
// ============================================================================

/** Crop region definition in pixel coordinates */
export interface CropRegion {
  /** X offset from left edge */
  x: number
  /** Y offset from top edge */
  y: number
  /** Width of crop region */
  width: number
  /** Height of crop region */
  height: number
}

/** Target resize dimensions */
export interface ResizeDimensions {
  /** Target width in pixels */
  width: number
  /** Target height in pixels */
  height: number
}

/** Color adjustment parameters for image processing */
export interface ColorAdjustment {
  /** Brightness adjustment (-1 to 1, 0 = no change) */
  brightness?: number
  /** Contrast adjustment (-1 to 1, 0 = no change) */
  contrast?: number
  /** Saturation adjustment (-1 to 1, 0 = no change) */
  saturation?: number
  /** Gamma correction (0.1 to 3.0, 1 = no change) */
  gamma?: number
  /** Hue rotation in degrees (-180 to 180) */
  hue?: number
}

/** Predefined color filter presets */
export type ColorFilterPreset = 'none' | 'grayscale' | 'sepia' | 'invert' | 'warm' | 'cool'

/** Combined image transform operations */
export interface ImageTransform {
  /** Crop region to apply */
  crop?: CropRegion
  /** Resize dimensions to apply after crop */
  resize?: ResizeDimensions
  /** Color adjustment parameters */
  colorAdjustment?: ColorAdjustment
  /** Predefined color filter preset */
  colorFilter?: ColorFilterPreset
}

// ============================================================================
// Frame Insertion Types
// ============================================================================

/** Specification for an interpolated frame insertion */
export interface FrameInsertion {
  /** Frame index after which to insert the new frame (original index space) */
  afterFrameIndex: number
  /** Interpolation factor between adjacent frames (0.0-1.0, default 0.5) */
  interpolationFactor: number
}

// ============================================================================
// Episode Edit Operations
// ============================================================================

/** XYZ position adjustment for a single frame */
export interface TrajectoryAdjustment {
  /** Frame index this adjustment applies to */
  frameIndex: number
  /** Delta adjustments for right arm XYZ (indices 0, 1, 2) */
  rightArmDelta?: [number, number, number]
  /** Delta adjustments for left arm XYZ (indices 8, 9, 10) */
  leftArmDelta?: [number, number, number]
  /** Override for right gripper value (index 7) */
  rightGripperOverride?: number
  /** Override for left gripper value (index 15) */
  leftGripperOverride?: number
}

/** Complete set of edit operations for an episode */
export interface EpisodeEditOperations {
  /** Dataset identifier */
  datasetId: string
  /** Episode index within the dataset */
  episodeIndex: number
  /** Transform applied to all cameras */
  globalTransform?: ImageTransform
  /** Per-camera transform overrides (camera name -> transform) */
  cameraTransforms?: Record<string, ImageTransform>
  /** Frame indices to exclude from export */
  removedFrames?: number[]
  /** Frames to insert via interpolation */
  insertedFrames?: FrameInsertion[]
  /** Sub-task segments for this episode */
  subtasks?: SubtaskSegment[]
  /** Trajectory adjustments per frame */
  trajectoryAdjustments?: TrajectoryAdjustment[]
}

// ============================================================================
// Sub-task Segmentation Types
// ============================================================================

/** Source of a sub-task segment */
export type SubtaskSource = 'manual' | 'auto'

/** Color presets for sub-task visualization */
export const SUBTASK_COLORS = [
  '#3b82f6', // blue
  '#10b981', // green
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#f97316', // orange
  '#84cc16', // lime
] as const

/** A labeled segment of frames representing a sub-task */
export interface SubtaskSegment {
  /** Unique identifier */
  id: string
  /** Human-readable label */
  label: string
  /** Frame range [start, end] inclusive */
  frameRange: [number, number]
  /** Display color (hex) */
  color: string
  /** How this segment was created */
  source: SubtaskSource
  /** Optional description */
  description?: string
}

// ============================================================================
// Export Types
// ============================================================================

/** HDF5 export format options */
export type HDF5ExportFormat = 'hdf5' | 'parquet'

/** Export request payload */
export interface ExportRequest {
  /** Episode indices to export */
  episodeIndices: number[]
  /** Output directory path */
  outputPath: string
  /** Whether to apply edit operations */
  applyEdits: boolean
  /** Whether to include sub-task metadata */
  includeSubtasks: boolean
  /** Output format */
  format: HDF5ExportFormat
}

/** Export progress update from SSE */
export interface ExportProgress {
  /** Current episode being processed */
  currentEpisode: number
  /** Total episodes to process */
  totalEpisodes: number
  /** Current frame being processed */
  currentFrame: number
  /** Total frames in current episode */
  totalFrames: number
  /** Overall progress percentage (0-100) */
  percentage: number
  /** Current operation description */
  status: string
}

/** Export completion result */
export interface ExportResult {
  /** Whether export completed successfully */
  success: boolean
  /** Output file paths */
  outputFiles: string[]
  /** Error message if failed */
  error?: string
  /** Export statistics */
  stats: {
    totalEpisodes: number
    totalFrames: number
    removedFrames: number
    durationMs: number
  }
}

// ============================================================================
// Helper Functions
// ============================================================================

/** Generate a unique ID for a new sub-task segment */
export function generateSubtaskId(): string {
  return `subtask-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

/** Get the next available color for a new segment */
export function getNextSubtaskColor(existingSegments: SubtaskSegment[]): string {
  const usedColors = new Set(existingSegments.map((s) => s.color))
  for (const color of SUBTASK_COLORS) {
    if (!usedColors.has(color)) {
      return color
    }
  }
  // Cycle back to first color if all used
  return SUBTASK_COLORS[existingSegments.length % SUBTASK_COLORS.length]
}

/** Create a default sub-task segment */
export function createDefaultSubtask(
  frameRange: [number, number],
  existingSegments: SubtaskSegment[] = [],
): SubtaskSegment {
  return {
    id: generateSubtaskId(),
    label: `Subtask ${existingSegments.length + 1}`,
    frameRange,
    color: getNextSubtaskColor(existingSegments),
    source: 'manual',
  }
}

/** Check if two frame ranges overlap */
export function rangesOverlap(a: [number, number], b: [number, number]): boolean {
  return a[0] <= b[1] && b[0] <= a[1]
}

/** Validate that segments don't overlap */
export function validateSegments(segments: SubtaskSegment[]): string[] {
  const errors: string[] = []

  for (let i = 0; i < segments.length; i++) {
    for (let j = i + 1; j < segments.length; j++) {
      if (rangesOverlap(segments[i].frameRange, segments[j].frameRange)) {
        errors.push(`Segments "${segments[i].label}" and "${segments[j].label}" overlap`)
      }
    }
  }

  return errors
}
