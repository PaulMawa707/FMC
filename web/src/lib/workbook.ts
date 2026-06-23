import { createHash } from "crypto";
import path from "path";
import { readFile } from "fs/promises";
import * as XLSX from "xlsx";
import {
  DEFAULT_COLLECTION_OFFSET_METERS,
  DEFAULT_DELIVERY_SUFFIX,
  DEFAULT_COLLECTION_SUFFIX,
  DEFAULT_ADVANCE_TIME_SECONDS,
  DEFAULT_SERVICE_TIME_SECONDS,
} from "./config";
import { extractCoordinates, normalizeHeader, normalizePlate, normalizeRouteName, normalizeText } from "./text";

export type StopOrder = {
  CUSTOMER_ID: string;
  CUSTOMER_NAME: string;
  CUSTOMER_KEY: string;
  LOCATION: string;
  LAT: number;
  LONG: number;
  TONNAGE: number;
  AMOUNT: number;
  PRIORITY: number;
  STOP_TYPE?: string;
  DISPLAY_NAME?: string;
  SERVICE_TIME_SECONDS?: number;
  ADVANCE_TIME_SECONDS?: number;
  COL_OFFSET_BEARING?: number;
};

export type LoadedRoute = {
  sheet_name: string;
  route_name: string;
  parser: "coordinate" | "delivery";
  orders: StopOrder[];
};

function sheetToRows(buffer: Buffer, sheetName: string): unknown[][] {
  const workbook = XLSX.read(buffer, { type: "buffer" });
  const sheet = workbook.Sheets[sheetName];
  return XLSX.utils.sheet_to_json(sheet, { header: 1, defval: null }) as unknown[][];
}

function findHeaderRow(rows: unknown[][], required: Set<string>): number | null {
  for (let i = 0; i < rows.length; i++) {
    const headers = new Set(rows[i].map((cell) => normalizeHeader(cell)));
    if ([...required].every((h) => headers.has(h))) return i;
  }
  return null;
}

function readCoordinateSheet(rows: unknown[][]): StopOrder[] {
  const headerRow = findHeaderRow(rows, new Set(["OUTLET", "LATITUDE", "LONGITUDE"]));
  if (headerRow == null) throw new Error("Coordinate-sheet header not found.");
  const headers = rows[headerRow].map((c) => normalizeHeader(c));
  const idx = (name: string) => headers.indexOf(name);
  const orders: StopOrder[] = [];
  let priority = 1;
  for (let r = headerRow + 1; r < rows.length; r++) {
    const row = rows[r];
    const outlet = normalizeText(row[idx("OUTLET")]);
    if (!outlet) continue;
    const lat = Number(row[idx("LATITUDE")]);
    const lon = Number(row[idx("LONGITUDE")]);
    if (Number.isNaN(lat) || Number.isNaN(lon)) continue;
    orders.push({
      CUSTOMER_ID: outlet,
      CUSTOMER_NAME: outlet,
      CUSTOMER_KEY: outlet,
      LOCATION: outlet,
      LAT: lat,
      LONG: lon,
      TONNAGE: 0,
      AMOUNT: 0,
      PRIORITY: priority++,
    });
  }
  return orders;
}

function readDeliverySheet(rows: unknown[][]): StopOrder[] {
  const headerRow = findHeaderRow(
    rows,
    new Set(["CUSTOMER ID", "CUSTOMER NAME", "LOCATION", "COORDINATES"]),
  );
  if (headerRow == null) throw new Error("Delivery-sheet header not found.");
  const headers = rows[headerRow].map((c) => normalizeHeader(c));
  const idx = (name: string) => headers.indexOf(name);
  const orders: StopOrder[] = [];
  let priority = 1;
  for (let r = headerRow + 1; r < rows.length; r++) {
    const row = rows[r];
    const customerId = normalizeText(row[idx("CUSTOMER ID")]);
    if (!customerId) continue;
    const customerName = normalizeText(row[idx("CUSTOMER NAME")]);
    if (customerName.toUpperCase().includes("TOTAL")) continue;
    const coords = extractCoordinates(row[idx("COORDINATES")]);
    if (coords.lat == null || coords.lon == null) continue;
    const tonnageCol = idx("TONNAGE");
    const amountCol = idx("AMOUNT");
    orders.push({
      CUSTOMER_ID: customerId,
      CUSTOMER_NAME: customerName,
      CUSTOMER_KEY: customerId,
      LOCATION: normalizeText(row[idx("LOCATION")]) || customerName,
      LAT: coords.lat,
      LONG: coords.lon,
      TONNAGE: tonnageCol >= 0 ? Number(String(row[tonnageCol]).replace(/,/g, "")) || 0 : 0,
      AMOUNT: amountCol >= 0 ? Number(String(row[amountCol]).replace(/,/g, "")) || 0 : 0,
      PRIORITY: priority++,
    });
  }
  return orders;
}

export async function readWorkbookBuffer(filename: string): Promise<Buffer> {
  const dataPath = path.join(process.cwd(), "data", filename);
  return readFile(dataPath);
}

export function loadRoutesFromWorkbook(buffer: Buffer, sourceName: string): LoadedRoute[] {
  const workbook = XLSX.read(buffer, { type: "buffer" });
  const routes: LoadedRoute[] = [];
  for (const sheetName of workbook.SheetNames) {
    const rows = sheetToRows(buffer, sheetName);
    for (const [parser, reader] of [
      ["coordinate", readCoordinateSheet],
      ["delivery", readDeliverySheet],
    ] as const) {
      try {
        const orders = reader(rows);
        if (!orders.length) throw new Error("No valid stop rows found.");
        let routeName = normalizeRouteName(sheetName);
        if (parser === "delivery" && workbook.SheetNames.length === 1) {
          routeName = normalizeRouteName(path.parse(sourceName).name);
        }
        routes.push({ sheet_name: sheetName, route_name: routeName, parser, orders });
        break;
      } catch {
        /* try next parser */
      }
    }
  }
  if (!routes.length) throw new Error(`No valid route sheets found in ${sourceName}.`);
  return routes;
}

export type FleetAsset = { asset_name: string; asset_norm: string; itemid: number };

export function readFleetAssets(buffer: Buffer): FleetAsset[] {
  const workbook = XLSX.read(buffer, { type: "buffer" });
  const sheet = workbook.Sheets[workbook.SheetNames[0]];
  const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: null });
  if (!rows.length) return [];
  const headers = Object.keys(rows[0]).map((h) => normalizeHeader(h).replace(/\s/g, ""));
  const nameCol = ["REPORTNAME", "NAME", "UNIT", "UNITNAME"].find((c) => headers.includes(c));
  if (!nameCol || !headers.includes("ITEMID")) {
    throw new Error("Fleet file must include a vehicle name column and an itemId column.");
  }
  const originalNameKey = Object.keys(rows[0]).find((k) => normalizeHeader(k).replace(/\s/g, "") === nameCol)!;
  const itemIdKey = Object.keys(rows[0]).find((k) => normalizeHeader(k).replace(/\s/g, "") === "ITEMID")!;
  const seen = new Set<number>();
  const fleet: FleetAsset[] = [];
  for (const row of rows) {
    const itemid = Number(row[itemIdKey]);
    const asset_name = normalizeText(row[originalNameKey]);
    if (!asset_name || Number.isNaN(itemid) || seen.has(itemid)) continue;
    seen.add(itemid);
    fleet.push({ asset_name, asset_norm: normalizePlate(asset_name), itemid });
  }
  return fleet.sort((a, b) => a.asset_norm.localeCompare(b.asset_norm));
}

function collectionOffsetBearing(seed: string): number {
  const digest = createHash("sha256").update(normalizeText(seed)).digest("hex");
  return (parseInt(digest.slice(0, 8), 16) % 36000) / 100;
}

function offsetByBearing(lat: number, lon: number, meters: number, bearingDeg: number) {
  const bearing = (bearingDeg * Math.PI) / 180;
  const north = meters * Math.cos(bearing);
  const east = meters * Math.sin(bearing);
  const latOffset = north / 111_320;
  const lonScale = 111_320 * Math.cos((lat * Math.PI) / 180);
  return { lat: lat + latOffset, lon: lon + (lonScale ? east / lonScale : 0) };
}

export function expandRouteOrders(
  orders: StopOrder[],
  reverseCollectionOrder = true,
  collectionOffsetMeters = DEFAULT_COLLECTION_OFFSET_METERS,
): StopOrder[] {
  const base = orders.map((o) => ({
    ...o,
    CUSTOMER_KEY: normalizeText(o.CUSTOMER_KEY || o.CUSTOMER_ID || o.CUSTOMER_NAME),
  }));
  const deliveries: StopOrder[] = base.map((row, i) => ({
    ...row,
    STOP_TYPE: "Delivery",
    DISPLAY_NAME: `${normalizeText(row.CUSTOMER_NAME)} - ${DEFAULT_DELIVERY_SUFFIX}`,
    PRIORITY: i + 1,
    SERVICE_TIME_SECONDS: DEFAULT_SERVICE_TIME_SECONDS,
    ADVANCE_TIME_SECONDS: DEFAULT_ADVANCE_TIME_SECONDS,
  }));
  const collectionBase = reverseCollectionOrder ? [...base].reverse() : base;
  const collections: StopOrder[] = collectionBase.map((row, i) => ({
    ...row,
    STOP_TYPE: "Collection",
    DISPLAY_NAME: `${normalizeText(row.CUSTOMER_NAME)} - ${DEFAULT_COLLECTION_SUFFIX}`,
    TONNAGE: 0,
    AMOUNT: 0,
    PRIORITY: deliveries.length + i + 1,
    SERVICE_TIME_SECONDS: DEFAULT_SERVICE_TIME_SECONDS,
    ADVANCE_TIME_SECONDS: DEFAULT_ADVANCE_TIME_SECONDS,
  }));
  const expanded = [...deliveries, ...collections];
  if (collectionOffsetMeters > 0) {
    for (const row of expanded) {
      if (row.STOP_TYPE?.toLowerCase() !== "collection") continue;
      const bearing = collectionOffsetBearing(row.CUSTOMER_KEY);
      const shifted = offsetByBearing(row.LAT, row.LONG, collectionOffsetMeters, bearing);
      row.LAT = shifted.lat;
      row.LONG = shifted.lon;
      row.COL_OFFSET_BEARING = bearing;
    }
  }
  return expanded;
}

export function summarizeExpandedOrders(orders: StopOrder[]) {
  const deliveries = orders.filter((o) => o.STOP_TYPE?.toLowerCase() === "delivery").length;
  const collections = orders.filter((o) => o.STOP_TYPE?.toLowerCase() === "collection").length;
  return { total: orders.length, deliveries, collections };
}

export function validateExpandedOrders(orders: StopOrder[]): { ok: boolean; message: string } {
  const s = summarizeExpandedOrders(orders);
  if (!s.total) return { ok: false, message: "No stops to dispatch." };
  if (!s.deliveries || !s.collections) {
    return { ok: false, message: "Route must include both delivery and collection stops." };
  }
  if (s.deliveries !== s.collections) {
    return {
      ok: false,
      message: `Delivery/collection mismatch: ${s.deliveries} vs ${s.collections}.`,
    };
  }
  return {
    ok: true,
    message: `${s.deliveries} deliveries + ${s.collections} collections (${s.total} stops).`,
  };
}
