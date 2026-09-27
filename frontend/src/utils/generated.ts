import type { GeneratedRun } from "../types/generated";

export function formatGeneratedMetaLine(
  run: Pick<GeneratedRun, "width" | "height" | "steps" | "true_cfg_scale" | "seed" | "duration">,
): string {
  return [
    `${run.width}×${run.height}`,
    `${run.steps} steps`,
    `CFG ${run.true_cfg_scale}`,
    `seed ${run.seed}`,
    `${run.duration}s`,
  ].join(" · ");
}

export function referenceLabel(index: number): string {
  return `image ${index + 1}`;
}
