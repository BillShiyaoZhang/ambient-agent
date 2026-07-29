const DEFAULT_MAX_FRAME_DIMENSION = 4096;


export function screencastDimensions(
  viewport,
  maxFrameDimension = DEFAULT_MAX_FRAME_DIMENSION,
) {
  const width = Math.max(1, Math.trunc(Number(viewport?.width) || 1));
  const height = Math.max(1, Math.trunc(Number(viewport?.height) || 1));
  const scale = Math.max(1, Math.min(2, Number(viewport?.deviceScaleFactor) || 1));
  const cap = Math.max(64, Math.trunc(maxFrameDimension));
  return {
    maxWidth: Math.min(cap, Math.max(64, Math.round(width * scale))),
    maxHeight: Math.min(cap, Math.max(64, Math.round(height * scale))),
  };
}
