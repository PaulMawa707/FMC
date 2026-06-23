export function normalizeText(value: unknown): string {
  if (value == null) return "";
  return String(value)
    .replace(/\u00A0/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function normalizeHeader(value: unknown): string {
  return normalizeText(value).toUpperCase();
}

export function normalizeRouteName(name: string): string {
  const clean = normalizeText(name);
  if (!clean) return "Route";
  if (clean.toLowerCase().endsWith("route")) return clean;
  return `${clean} route`;
}

export function normalizePlate(value: unknown): string {
  if (typeof value !== "string") return "";
  return value.replace(/[^A-Z0-9]/gi, "").toUpperCase();
}

export function extractCoordinates(coordStr: unknown): { lat: number | null; lon: number | null } {
  const text = normalizeText(coordStr);
  if (!text) return { lat: null, lon: null };
  const paren = text.match(/\(([-\d.]+)\s*,\s*([-\d.]+)\)/);
  if (paren) {
    return { lat: Number(paren[1]), lon: Number(paren[2]) };
  }
  const parts = text.split(/[,;\s]+/).filter(Boolean);
  if (parts.length >= 2) {
    const lat = Number(parts[0]);
    const lon = Number(parts[1]);
    if (!Number.isNaN(lat) && !Number.isNaN(lon)) return { lat, lon };
  }
  return { lat: null, lon: null };
}
