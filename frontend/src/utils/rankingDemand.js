export const DEMAND_LEVEL_KEYS = ["high", "medium", "low"];

/** The backend `band` is the authoritative demand classification. */
export function getDemandLevel(unit) {
  const level = unit?.band ?? unit?.demand_level;
  return DEMAND_LEVEL_KEYS.includes(level) ? level : null;
}

/** Count the authoritative demand levels in one already-scoped unit set. */
export function countDemandLevels(units = []) {
  const counts = Object.fromEntries(DEMAND_LEVEL_KEYS.map((level) => [level, 0]));
  for (const unit of units) {
    const level = getDemandLevel(unit);
    if (level) counts[level] += 1;
  }
  return counts;
}
