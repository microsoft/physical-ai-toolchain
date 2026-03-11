/**
 * Detection viewer with canvas bounding box overlay.
 */

import { useEffect,useRef } from 'react';

import type { Detection } from '@/types/detection';

interface DetectionViewerProps {
  imageUrl: string | null;
  detections: Detection[];
  showLabels?: boolean;
  boxOpacity?: number;
}

// Color palette for different classes
const CLASS_COLORS: Record<string, string> = {
  person: '#FF6B6B',
  car: '#4ECDC4',
  truck: '#45B7D1',
  bicycle: '#96CEB4',
  dog: '#FFEAA7',
  cat: '#DDA0DD',
  chair: '#98D8C8',
  bottle: '#F7DC6F',
  default: '#74B9FF',
};

function getClassColor(className: string): string {
  return CLASS_COLORS[className] || CLASS_COLORS.default;
}

export function DetectionViewer({
  imageUrl,
  detections,
  showLabels = true,
  boxOpacity = 0.8,
}: DetectionViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Draw bounding boxes when image or detections change
  useEffect(() => {
    if (!imageUrl || !canvasRef.current || !containerRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const img = new Image();
    img.onload = () => {
      // Set canvas size to match container
      const container = containerRef.current!;
      const containerWidth = container.clientWidth;
      const containerHeight = container.clientHeight;

      // Calculate scale to fit
      const scale = Math.min(containerWidth / img.width, containerHeight / img.height);
      const scaledWidth = img.width * scale;
      const scaledHeight = img.height * scale;

      canvas.width = scaledWidth;
      canvas.height = scaledHeight;

      // Draw image
      ctx.drawImage(img, 0, 0, scaledWidth, scaledHeight);

      // Draw bounding boxes
      detections.forEach((det) => {
        const [x1, y1, x2, y2] = det.bbox;
        const sx1 = x1 * scale;
        const sy1 = y1 * scale;
        const sWidth = (x2 - x1) * scale;
        const sHeight = (y2 - y1) * scale;

        const color = getClassColor(det.class_name);

        // Draw box
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.globalAlpha = boxOpacity;
        ctx.strokeRect(sx1, sy1, sWidth, sHeight);

        // Draw label
        if (showLabels) {
          const label = `${det.class_name} ${(det.confidence * 100).toFixed(0)}%`;
          ctx.font = '12px sans-serif';
          const textWidth = ctx.measureText(label).width;

          ctx.fillStyle = color;
          ctx.globalAlpha = 0.9;
          ctx.fillRect(sx1, sy1 - 18, textWidth + 8, 18);

          ctx.fillStyle = '#000';
          ctx.globalAlpha = 1;
          ctx.fillText(label, sx1 + 4, sy1 - 5);
        }
      });

      ctx.globalAlpha = 1;
    };
    img.src = imageUrl;
  }, [imageUrl, detections, showLabels, boxOpacity]);

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full flex items-center justify-center bg-black rounded-lg overflow-hidden"
    >
      <canvas ref={canvasRef} className="max-w-full max-h-full" />
      {detections.length > 0 && (
        <div className="absolute bottom-2 right-2 bg-black/70 text-white text-xs px-2 py-1 rounded">
          {detections.length} detection{detections.length !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
}
