export type GroupBridgeOperationKind = "load" | "action";

export interface GroupBridgeOperationToken {
  readonly groupId: string;
  readonly generation: number;
  readonly kind: GroupBridgeOperationKind;
  readonly operation: number;
}

export interface GroupBridgeGeneration {
  readonly groupId: string;
  readonly generation: number;
}

export function normalizeGroupBridgeGroupId(groupId?: string): string {
  return String(groupId || "").trim();
}

export function isGroupBridgeViewCurrent(selectedGroupId?: string, viewGroupId?: string): boolean {
  const selected = normalizeGroupBridgeGroupId(selectedGroupId);
  return Boolean(selected) && normalizeGroupBridgeGroupId(viewGroupId) === selected;
}

export class GroupBridgeLifecycle {
  private groupId = "";
  private generation = 0;
  private loadOperation = 0;
  private actionOperation = 0;
  private activeLoad = 0;
  private activeAction = 0;

  selectGroup(groupId?: string): GroupBridgeGeneration {
    const nextGroupId = normalizeGroupBridgeGroupId(groupId);
    if (nextGroupId !== this.groupId) {
      this.groupId = nextGroupId;
      this.generation += 1;
      this.loadOperation = 0;
      this.actionOperation = 0;
      this.activeLoad = 0;
      this.activeAction = 0;
    }
    return { groupId: this.groupId, generation: this.generation };
  }

  isViewCurrent(viewGroupId?: string): boolean {
    return isGroupBridgeViewCurrent(this.groupId, viewGroupId);
  }

  canMutate(groupId?: string): boolean {
    return this.isViewCurrent(groupId) && this.activeLoad === 0 && this.activeAction === 0;
  }

  beginLoad(groupId?: string): GroupBridgeOperationToken | null {
    if (!this.isViewCurrent(groupId)) return null;
    this.loadOperation += 1;
    this.activeLoad = this.loadOperation;
    return this.token("load", this.loadOperation);
  }

  beginAction(groupId?: string): GroupBridgeOperationToken | null {
    if (!this.canMutate(groupId)) return null;
    this.actionOperation += 1;
    this.activeAction = this.actionOperation;
    return this.token("action", this.actionOperation);
  }

  isCurrent(token: GroupBridgeOperationToken): boolean {
    if (token.groupId !== this.groupId || token.generation !== this.generation) return false;
    return token.kind === "load"
      ? token.operation === this.activeLoad
      : token.operation === this.activeAction;
  }

  commit(token: GroupBridgeOperationToken, apply: () => void): boolean {
    if (!this.isCurrent(token)) return false;
    apply();
    return true;
  }

  finish(token: GroupBridgeOperationToken): boolean {
    if (!this.isCurrent(token)) return false;
    if (token.kind === "load") this.activeLoad = 0;
    else this.activeAction = 0;
    return true;
  }

  private token(kind: GroupBridgeOperationKind, operation: number): GroupBridgeOperationToken {
    return { groupId: this.groupId, generation: this.generation, kind, operation };
  }
}
