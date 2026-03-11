/**
 * Frame crop tool using react-image-crop.
 *
 * Provides interactive crop selection with aspect ratio lock
 * and live preview feedback.
 */

import 'react-image-crop/dist/ReactCrop.css';

import { Lock, RotateCcw,Unlock } from 'lucide-react';
import { useCallback, useRef,useState } from 'react';
import ReactCrop, { type Crop, type PixelCrop } from 'react-image-crop';

import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { useTransformState } from '@/stores';
import type { CropRegion } from '@/types/episode-edit';

interface FrameCropToolProps {
  /** URL of the frame image to crop */
  frameUrl: string;
  /** Camera name for per-camera transforms */
  cameraName?: string;
  /** Additional CSS classes */
  className?: string;
  /** Callback when crop is applied */
  onCropApplied?: (crop: CropRegion) => void;
}

/**
 * Interactive frame cropping tool.
 *
 * @example
 * ```tsx
 * <FrameCropTool
 *   frameUrl="/api/frames/0"
 *   cameraName="top"
 *   onCropApplied={(crop) => console.log('Crop applied:', crop)}
 * />
 * ```
 */
export function FrameCropTool({
  frameUrl,
  cameraName,
  className,
  onCropApplied,
}: FrameCropToolProps) {
  const imgRef = useRef<HTMLImageElement>(null);
  const { globalTransform, setGlobalTransform, setCameraTransform } =
    useTransformState();

  // Get current crop from store
  const currentCrop = cameraName
    ? undefined // Would need to get from cameraTransforms
    : globalTransform?.crop;

  // Local crop state during editing
  const [crop, setCrop] = useState<Crop | undefined>(() => {
    if (currentCrop) {
      return {
        unit: 'px',
        x: currentCrop.x,
        y: currentCrop.y,
        width: currentCrop.width,
        height: currentCrop.height,
      };
    }
    return undefined;
  });

  const [completedCrop, setCompletedCrop] = useState<PixelCrop | undefined>();
  const [lockAspect, setLockAspect] = useState(false);
  const [aspect, setAspect] = useState<number | undefined>();

  // Calculate aspect ratio from image dimensions
  const handleImageLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const { naturalWidth, naturalHeight } = e.currentTarget;
      if (lockAspect) {
        setAspect(naturalWidth / naturalHeight);
      }
    },
    [lockAspect]
  );

  // Toggle aspect ratio lock
  const handleLockToggle = useCallback(() => {
    setLockAspect((prev) => {
      const newLock = !prev;
      if (newLock && imgRef.current) {
        const { naturalWidth, naturalHeight } = imgRef.current;
        setAspect(naturalWidth / naturalHeight);
      } else {
        setAspect(undefined);
      }
      return newLock;
    });
  }, []);

  // Apply the crop to the store
  const handleApplyCrop = useCallback(() => {
    if (!completedCrop) return;

    const cropRegion: CropRegion = {
      x: Math.round(completedCrop.x),
      y: Math.round(completedCrop.y),
      width: Math.round(completedCrop.width),
      height: Math.round(completedCrop.height),
    };

    if (cameraName) {
      setCameraTransform(cameraName, {
        crop: cropRegion,
      });
    } else {
      setGlobalTransform({
        ...globalTransform,
        crop: cropRegion,
      });
    }

    onCropApplied?.(cropRegion);
  }, [
    completedCrop,
    cameraName,
    globalTransform,
    setGlobalTransform,
    setCameraTransform,
    onCropApplied,
  ]);

  // Reset crop
  const handleReset = useCallback(() => {
    setCrop(undefined);
    setCompletedCrop(undefined);

    if (cameraName) {
      setCameraTransform(cameraName, null);
    } else {
      setGlobalTransform(
        globalTransform?.resize ? { resize: globalTransform.resize } : null
      );
    }
  }, [cameraName, globalTransform, setGlobalTransform, setCameraTransform]);

  return (
    <div className={cn('flex flex-col gap-4', className)}>
      {/* Controls */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <Checkbox
              id="lock-aspect"
              checked={lockAspect}
              onCheckedChange={() => handleLockToggle()}
            />
            <Label htmlFor="lock-aspect" className="flex items-center gap-1">
              {lockAspect ? (
                <Lock className="h-3 w-3" />
              ) : (
                <Unlock className="h-3 w-3" />
              )}
              Lock aspect ratio
            </Label>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleReset}>
            <RotateCcw className="h-4 w-4 mr-1" />
            Reset
          </Button>
          <Button
            size="sm"
            onClick={handleApplyCrop}
            disabled={!completedCrop}
          >
            Apply Crop
          </Button>
        </div>
      </div>

      {/* Crop area */}
      <div className="relative bg-muted rounded-lg overflow-hidden">
        <ReactCrop
          crop={crop}
          onChange={(c: Crop) => setCrop(c)}
          onComplete={(c: PixelCrop) => setCompletedCrop(c)}
          aspect={aspect}
          className="max-h-[400px]"
        >
          <img
            ref={imgRef}
            src={frameUrl}
            alt="Frame to crop"
            onLoad={handleImageLoad}
            className="max-w-full max-h-[400px] object-contain"
          />
        </ReactCrop>
      </div>

      {/* Crop info */}
      {completedCrop && (
        <div className="text-sm text-muted-foreground">
          Selection: {Math.round(completedCrop.width)} ×{' '}
          {Math.round(completedCrop.height)} px at ({Math.round(completedCrop.x)}
          , {Math.round(completedCrop.y)})
        </div>
      )}
    </div>
  );
}
