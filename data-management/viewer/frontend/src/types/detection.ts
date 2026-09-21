/**
 * TypeScript types for YOLO11 object detection.
 */

/**
 * Request parameters for running object detection.
 */
export interface DetectionRequest {
  /** Specific frame indices to process. If undefined, processes all frames. */
  frames?: number[]
  /** Minimum confidence threshold (0.0-1.0, default: 0.1) */
  confidence?: number
  /**
   * YOLO model variant. Closed-vocabulary defaults are `yolo11*`; open-vocabulary
   * variants such as `yolov8s-world` are selected automatically when `labels` is set.
   */
  model?: string
  /**
   * Optional open-vocabulary class names. When provided the backend switches to a
   * YOLO-World model and only returns boxes matching these labels.
   */
  labels?: string[]
  /**
   * Camera/stream key to source frames from (e.g. `observation.images.color`). When
   * omitted the backend uses the episode's first available camera.
   */
  camera?: string
}

/**
 * Single object detection result.
 */
export interface Detection {
  /** COCO class ID */
  class_id: number
  /** Human-readable class name */
  class_name: string
  /** Detection confidence score (0.0-1.0) */
  confidence: number
  /** Bounding box as [x1, y1, x2, y2] in pixels */
  bbox: [number, number, number, number]
}

/**
 * Detection results for a single frame.
 */
export interface DetectionResult {
  /** Frame index */
  frame: number
  /** Detections found in this frame */
  detections: Detection[]
  /** Inference time in milliseconds */
  processing_time_ms: number
}

/**
 * Summary statistics for a detection class.
 */
export interface ClassSummary {
  /** Total detections of this class */
  count: number
  /** Average confidence */
  avg_confidence: number
}

/**
 * Complete detection results for an episode.
 */
export interface EpisodeDetectionSummary {
  /** Total frames in episode */
  total_frames: number
  /** Number of frames processed */
  processed_frames: number
  /** Total detections across all frames */
  total_detections: number
  /** Detection results by frame */
  detections_by_frame: DetectionResult[]
  /** Detection statistics by class name */
  class_summary: Record<string, ClassSummary>
}
