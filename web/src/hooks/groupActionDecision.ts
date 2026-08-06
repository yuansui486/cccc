import type { GroupServerControlState } from "../types";

export function shouldStartGroupAfterActivation(
  controlState: GroupServerControlState | null | undefined,
): boolean {
  return !!controlState && !controlState.runtime_running;
}
