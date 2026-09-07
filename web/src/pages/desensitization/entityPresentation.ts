import type { Entity, EntityDirectory } from "../../services/api/desensitization";

export type EntityGroup = {
  key: string;
  type: string;
  name: string;
  text: string;
  entities: Entity[];
  selected: number;
};

export function entityNames(directory: EntityDirectory | null): Record<string, string> {
  const names = { ...(directory?.entity_catalog || {}) };
  for (const item of directory?.entities || []) {
    names[item.code || item.type || ""] = item.name || item.label || item.code;
  }
  return names;
}

export function groupEntities(entities: Entity[], names: Record<string, string>): EntityGroup[] {
  const groups = new Map<string, EntityGroup>();
  for (const entity of entities) {
    const type = String(entity.type || "DEFAULT");
    const text = String(entity.text || `${entity.start}-${entity.end}`);
    const key = `${type}\u0000${text}`;
    const group = groups.get(key) || {
      key, type, name: entity.type_name || names[type] || type, text, entities: [], selected: 0,
    };
    group.entities.push(entity);
    if (entity.selected !== false) group.selected += 1;
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function entityMatchesGroup(entity: Entity, group: EntityGroup): boolean {
  return String(entity.type || "DEFAULT") === group.type
    && String(entity.text || `${entity.start}-${entity.end}`) === group.text;
}

export function selectEntityGroup(entities: Entity[], group: EntityGroup, selected: boolean): Entity[] {
  return entities.map((entity) => entityMatchesGroup(entity, group)
    ? { ...entity, selected }
    : entity);
}
