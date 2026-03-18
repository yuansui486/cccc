export function replaceRuntimeBrandingForDisplay(text: string): string {
  let next = String(text || "");
  next = next.replace(/CCCC\./g, "OneColleague.");
  next = next.replace(/cccc\./g, "onecolleague.");
  next = next.replace(/\bCCCC\b(?!_)/g, "OneColleague");
  next = next.replace(/\bcccc\b(?!_)/g, "onecolleague");
  return next;
}
