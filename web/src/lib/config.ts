export const WAREHOUSES = {
  FMC: {
    lat: Number(process.env.WAREHOUSE_LAT ?? -1.188615),
    lon: Number(process.env.WAREHOUSE_LON ?? 36.911845),
  },
} as const;

export const DEFAULT_WORKBOOK = "route coordinates (004).xlsx";
export const DEFAULT_FLEET_WORKBOOK = "FCL_Vehicles.xlsx";
export const DEFAULT_DELIVERY_SUFFIX = "DEL";
export const DEFAULT_COLLECTION_SUFFIX = "COL";
export const DEFAULT_SERVICE_TIME_SECONDS = 15 * 60;
export const DEFAULT_ADVANCE_TIME_SECONDS = 0;
export const DEFAULT_COLLECTION_OFFSET_METERS = 25;

export const DEFAULT_ROUTE_VEHICLE_MAP: Record<string, string> = {
  "eastlands route": "FCL - KBT 227L",
  "ngong rd route": "FCL - KBV 586L",
  "southlands route": "FCL - KCF 844G",
};

export const ORDER_FLAG_COMPLETE_ON_STOP = 0x1;
export const ORDER_FLAG_COMPLETE_ON_LEAVE = 0x2;
export const ORDER_FLAG_START_WAREHOUSE = 0x4;
export const ORDER_FLAG_END_WAREHOUSE = 0x8;
export const ROUTE_FLAG_ANY_SEQUENCE = 0;
export const ROUTE_FLAG_STRICT_SEQUENCE = 1;

export const REMOTE_API_URL = "https://hst-api.wialon.com/wialon/ajax.html";
export const LOGISTICS_ROUTES_URL = "https://logistics.wialon.com/api/routes";

export function getLogisticsToken(): string {
  const token = process.env.LOGISTICS_TOKEN?.trim();
  if (!token) {
    throw new Error("LOGISTICS_TOKEN is not configured.");
  }
  return token;
}

export function getResourceId(): number {
  return Number(process.env.LOGISTICS_RESOURCE_ID ?? 28277390);
}

export const SESSION_COOKIE = "fmc_dispatch_session";
