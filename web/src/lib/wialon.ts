import {
  DEFAULT_ADVANCE_TIME_SECONDS,
  ORDER_FLAG_COMPLETE_ON_LEAVE,
  ORDER_FLAG_COMPLETE_ON_STOP,
  ORDER_FLAG_END_WAREHOUSE,
  ORDER_FLAG_START_WAREHOUSE,
  REMOTE_API_URL,
  ROUTE_FLAG_ANY_SEQUENCE,
  ROUTE_FLAG_STRICT_SEQUENCE,
  WAREHOUSES,
} from "./config";
import { normalizeRouteName, normalizeText } from "./text";
import type { StopOrder } from "./workbook";

type Point = { y: number; x: number };

export type ScheduleEntry = {
  rowIndex: number;
  row: StopOrder;
  plannedVisitTime: number;
  travelTimeSeconds: number;
  mileage: number;
  polyline: string | null;
  serviceTimeSeconds: number;
  stopType: string;
};

function calcDistanceKm(y1: number, x1: number, y2: number, x2: number): number {
  const r = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  y1 = toRad(y1);
  x1 = toRad(x1);
  y2 = toRad(y2);
  x2 = toRad(x2);
  const dlat = y2 - y1;
  const dlon = x2 - x1;
  const a = Math.sin(dlat / 2) ** 2 + Math.cos(y1) * Math.cos(y2) * Math.sin(dlon / 2) ** 2;
  return r * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

async function getOsrmPolyline(start: Point, end: Point) {
  try {
    const url = `https://router.project-osrm.org/route/v1/driving/${start.x},${start.y};${end.x},${end.y}?overview=full&geometries=polyline`;
    const response = await fetch(url, { signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error("OSRM failed");
    const payload = await response.json();
    if (payload?.routes?.[0]) {
      const route = payload.routes[0];
      return {
        polyline: route.geometry ?? null,
        mileage: Math.round(route.distance ?? 0),
        duration: Math.round(route.duration ?? 0),
      };
    }
  } catch {
    /* fallback below */
  }
  const distance = Math.round(calcDistanceKm(start.y, start.x, end.y, end.x) * 1000);
  const speed = (30 * 1000) / 3600;
  return { polyline: null, mileage: distance, duration: distance > 0 ? Math.round(distance / speed) : 0 };
}

export function makeRouteOrderUid(routeId: number, sequenceIndex: number): number {
  return routeId * 10000 + sequenceIndex;
}

export function allocateRouteId(): number {
  return Math.floor(Number(process.hrtime.bigint()) / 1_000_000);
}

function customerKey(row: StopOrder): string {
  return normalizeText(row.CUSTOMER_KEY || row.CUSTOMER_ID || row.CUSTOMER_NAME);
}

export async function computeStopSchedule(
  orders: StopOrder[],
  whLat: number,
  whLon: number,
  tf: number,
): Promise<ScheduleEntry[]> {
  const schedule: ScheduleEntry[] = [];
  let plannedVisitTime = tf;
  let prev: Point = { y: whLat, x: whLon };
  for (let i = 0; i < orders.length; i++) {
    const row = orders[i];
    const serviceTimeSeconds = row.SERVICE_TIME_SECONDS ?? 900;
    const { polyline, mileage, duration } = await getOsrmPolyline(prev, { y: row.LAT, x: row.LONG });
    plannedVisitTime += duration;
    schedule.push({
      rowIndex: i,
      row,
      plannedVisitTime,
      travelTimeSeconds: duration,
      mileage,
      polyline,
      serviceTimeSeconds,
      stopType: normalizeText(row.STOP_TYPE),
    });
    plannedVisitTime += serviceTimeSeconds;
    prev = { y: row.LAT, x: row.LONG };
  }
  return schedule;
}

function formatWialonError(payload: unknown, fallback = "Unknown error"): string {
  if (payload && typeof payload === "object") {
    const obj = payload as Record<string, unknown>;
    if (typeof obj.error === "number" && obj.error !== 0) {
      return String(obj.reason ?? obj.message ?? fallback);
    }
    if (typeof obj.message === "string") return obj.message;
  }
  return fallback;
}

async function wialonPost(body: Record<string, string>, timeoutMs = 90000) {
  const response = await fetch(REMOTE_API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  return response.json();
}

export async function loginWialonSession(token: string): Promise<string> {
  const result = await wialonPost({
    svc: "token/login",
    params: JSON.stringify({ token: token.trim() }),
  });
  if (!result?.eid) throw new Error(formatWialonError(result, "Token login failed"));
  return result.eid as string;
}

async function executeWialonBatch(sessionId: string, callParams: Record<string, unknown>[]) {
  return wialonPost({
    svc: "core/batch",
    params: JSON.stringify({ params: callParams, flags: 0 }),
    sid: sessionId,
  });
}

function buildRouteOrderPayload(args: {
  orderUid: number;
  orderId: number;
  orderName: string;
  location: string;
  lat: number;
  lon: number;
  unitId: number;
  routeId: number;
  sequenceIndex: number;
  plannedVisitTime: number;
  advanceTimeSeconds: number;
  serviceTimeSeconds: number;
  mileage: number;
  travelTimeSeconds: number;
  weightKg: number;
  costVal: number;
  priority: number;
  orderFlags: number;
  orderTf: number;
  orderTt: number;
  polyline: string | null;
  currentTime: number;
  customerKey: string;
  stopType: string;
  dependentUids?: number[];
}) {
  const orderPayload: Record<string, unknown> = {
    uid: args.orderUid,
    id: args.orderId,
    n: args.orderName,
    p: {
      ut: args.serviceTimeSeconds,
      rep: true,
      w: String(args.weightKg),
      c: String(Math.round(args.costVal)),
      r: {
        vt: args.plannedVisitTime,
        ndt: args.advanceTimeSeconds,
        id: args.routeId,
        i: args.sequenceIndex,
        m: args.mileage,
        t: args.travelTimeSeconds,
      },
      u: args.unitId,
      a: `${args.location} (${args.lat}, ${args.lon})`,
      weight: String(args.weightKg),
      cost: String(Math.round(args.costVal)),
      pr: args.priority,
      cid: args.customerKey && args.stopType ? `${args.customerKey}|${args.stopType}` : args.customerKey,
    },
    f: args.orderFlags,
    tf: args.orderTf,
    tt: args.orderTt,
    r: 100,
    y: args.lat,
    x: args.lon,
    rp: args.polyline,
    s: 0,
    sf: 0,
    trt: args.advanceTimeSeconds,
    st: args.currentTime,
    cnm: 0,
    callMode: "create",
    u: args.unitId,
    weight: String(args.weightKg),
    cost: String(Math.round(args.costVal)),
    cargo: { weight: String(args.weightKg), cost: String(Math.round(args.costVal)) },
    cmp: { unitRequirements: { values: [] } },
    gfn: { geofences: {} },
    ej: {},
    cf: {},
  };
  if (args.dependentUids?.length) orderPayload.dp = args.dependentUids;
  return orderPayload;
}

function extractRouteOrders(routeResult: unknown): Array<{ id: number; n?: string }> {
  const orders: Array<{ id: number; n?: string }> = [];
  const collect = (node: unknown) => {
    if (Array.isArray(node)) {
      node.forEach(collect);
      return;
    }
    if (!node || typeof node !== "object") return;
    const obj = node as Record<string, unknown>;
    if (Array.isArray(obj.orders)) {
      for (const order of obj.orders) {
        if (order && typeof order === "object" && (order as { id?: number }).id != null) {
          orders.push(order as { id: number; n?: string });
        }
      }
    }
    Object.values(obj).forEach(collect);
  };
  collect(routeResult);
  const seen = new Set<number>();
  return orders.filter((o) => {
    if (seen.has(o.id)) return false;
    seen.add(o.id);
    return true;
  });
}

async function assignRouteOrdersToUnit(
  sessionId: string,
  resourceId: number,
  unitId: number,
  createdOrders: Array<{ id: number }>,
): Promise<{ ok: boolean; message: string }> {
  const orderIds = createdOrders.map((o) => o.id).filter((id) => id != null);
  if (!orderIds.length) {
    return { ok: false, message: "Route was created but Logistics did not return order IDs." };
  }
  const chunkSize = 40;
  let assigned = 0;
  for (let offset = 0; offset < orderIds.length; offset += chunkSize) {
    const chunk = orderIds.slice(offset, offset + chunkSize);
    const batchCalls = chunk.map((orderId) => ({
      svc: "order/update",
      params: {
        itemId: resourceId,
        id: orderId,
        u: unitId,
        callMode: "assign",
      },
    }));
    const assignResult = await executeWialonBatch(sessionId, batchCalls);
    if (Array.isArray(assignResult)) {
      for (const item of assignResult) {
        if (item?.error) {
          return { ok: false, message: formatWialonError(item, "Failed to assign vehicle.") };
        }
      }
    } else if (assignResult?.error) {
      return { ok: false, message: formatWialonError(assignResult, "Failed to assign vehicle.") };
    }
    assigned += chunk.length;
  }
  return { ok: true, message: `Assigned vehicle to ${assigned} route orders.` };
}

export async function sendOrdersAndCreateRoute(args: {
  token: string;
  resourceId: number;
  unitId: number;
  vehicleName: string;
  routeName: string;
  orders: StopOrder[];
  tf: number;
  tt: number;
  warehouseChoice?: keyof typeof WAREHOUSES;
  strictVisitSequence?: boolean;
}): Promise<{ error: number; message: string; planningUrl?: string }> {
  const warehouseKey = args.warehouseChoice ?? "FMC";
  const warehouse = WAREHOUSES[warehouseKey];
  const sessionId = await loginWialonSession(args.token);
  const currentTime = Math.floor(Date.now() / 1000);
  const routeId = allocateRouteId();
  const stopSchedule = await computeStopSchedule(args.orders, warehouse.lat, warehouse.lon, args.tf);
  const routeOrders: Record<string, unknown>[] = [];
  const deliveryUidByKey: Record<string, number> = {};
  let sequenceIndex = 0;
  let plannedVisitTime = args.tf;

  routeOrders.push({
    uid: makeRouteOrderUid(routeId, sequenceIndex),
    id: 0,
    n: warehouseKey,
    p: {
      ut: 0,
      rep: true,
      w: "0",
      c: "0",
      r: { vt: plannedVisitTime, ndt: DEFAULT_ADVANCE_TIME_SECONDS, id: routeId, i: sequenceIndex, m: 0, t: 0 },
      u: args.unitId,
      a: `${warehouseKey} (${warehouse.lat}, ${warehouse.lon})`,
      weight: "0",
      cost: "0",
    },
    f: ORDER_FLAG_START_WAREHOUSE,
    tf: args.tf,
    tt: args.tt,
    r: 100,
    y: warehouse.lat,
    x: warehouse.lon,
    s: 0,
    sf: 0,
    trt: DEFAULT_ADVANCE_TIME_SECONDS,
    st: currentTime,
    cnm: 0,
    callMode: "create",
    u: args.unitId,
    weight: "0",
    cost: "0",
    cargo: { weight: "0", cost: "0" },
    cmp: { unitRequirements: { values: [] } },
    gfn: { geofences: {} },
    ej: {},
    cf: {},
  });

  for (const entry of stopSchedule) {
    const row = entry.row;
    const idx = entry.rowIndex;
    const stopType = entry.stopType || "Delivery";
    const key = customerKey(row);
    const orderName = row.DISPLAY_NAME || row.CUSTOMER_NAME || `Stop ${idx + 1}`;
    const location = row.LOCATION || row.CUSTOMER_NAME || "Unknown";
    const weightKg = Math.round((row.TONNAGE || 0) * 1000);
    const costVal = row.AMOUNT || 0;
    const priority = row.PRIORITY ?? idx + 1;
    const advance = row.ADVANCE_TIME_SECONDS ?? DEFAULT_ADVANCE_TIME_SECONDS;
    const plannedArrival = entry.plannedVisitTime;
    sequenceIndex += 1;
    const orderUid = makeRouteOrderUid(routeId, sequenceIndex);
    const isCollection = stopType.toLowerCase() === "collection";
    let orderTf: number;
    let orderTt: number;
    let dependentUids: number[] | undefined;
    if (isCollection) {
      orderTf = Math.max(plannedArrival - advance, args.tf);
      orderTt = plannedArrival + entry.serviceTimeSeconds + advance;
      const paired = deliveryUidByKey[key];
      dependentUids = paired ? [paired] : undefined;
    } else {
      orderTf = args.tf;
      orderTt = plannedArrival + entry.serviceTimeSeconds + advance;
      deliveryUidByKey[key] = orderUid;
    }
    if (orderTf >= args.tt) orderTf = args.tt - 1;
    orderTt = Math.min(orderTt, args.tt);
    if (orderTt <= orderTf) orderTt = orderTf + 1;

    routeOrders.push(
      buildRouteOrderPayload({
        orderUid,
        orderId: idx + 1,
        orderName,
        location,
        lat: row.LAT,
        lon: row.LONG,
        unitId: args.unitId,
        routeId,
        sequenceIndex,
        plannedVisitTime: entry.plannedVisitTime,
        advanceTimeSeconds: advance,
        serviceTimeSeconds: entry.serviceTimeSeconds,
        mileage: entry.mileage,
        travelTimeSeconds: entry.travelTimeSeconds,
        weightKg,
        costVal,
        priority,
        orderFlags: ORDER_FLAG_COMPLETE_ON_STOP | ORDER_FLAG_COMPLETE_ON_LEAVE,
        orderTf,
        orderTt,
        polyline: entry.polyline,
        currentTime,
        customerKey: key,
        stopType,
        dependentUids,
      }),
    );
  }

  const last = stopSchedule.at(-1);
  const prev = last ? { y: last.row.LAT, x: last.row.LONG } : { y: warehouse.lat, x: warehouse.lon };
  const back = await getOsrmPolyline(prev, { y: warehouse.lat, x: warehouse.lon });
  plannedVisitTime =
    (last?.plannedVisitTime ?? args.tf) + (last?.serviceTimeSeconds ?? 0) + back.duration;
  sequenceIndex += 1;
  routeOrders.push({
    uid: makeRouteOrderUid(routeId, sequenceIndex),
    id: routeOrders.length,
    n: warehouseKey,
    p: {
      ut: 0,
      rep: true,
      w: "0",
      c: "0",
      r: {
        vt: plannedVisitTime,
        ndt: DEFAULT_ADVANCE_TIME_SECONDS,
        id: routeId,
        i: sequenceIndex,
        m: back.mileage,
        t: back.duration,
      },
      u: args.unitId,
      a: `${warehouseKey} (${warehouse.lat}, ${warehouse.lon})`,
      weight: "0",
      cost: "0",
    },
    f: ORDER_FLAG_END_WAREHOUSE,
    tf: args.tf,
    tt: args.tt,
    r: 100,
    y: warehouse.lat,
    x: warehouse.lon,
    rp: back.polyline,
    s: 0,
    sf: 0,
    trt: DEFAULT_ADVANCE_TIME_SECONDS,
    st: currentTime,
    cnm: 0,
    callMode: "create",
    u: args.unitId,
    weight: "0",
    cost: "0",
    cargo: { weight: "0", cost: "0" },
    cmp: { unitRequirements: { values: [] } },
    gfn: { geofences: {} },
    ej: {},
    cf: {},
  });

  const totalMileage = routeOrders.reduce(
    (sum, o) => sum + Number((o.p as { r?: { m?: number } })?.r?.m ?? 0),
    0,
  );
  const finalRouteName = `${normalizeRouteName(args.routeName)} - ${args.vehicleName} - ${new Date()
    .toISOString()
    .slice(0, 16)
    .replace("T", " ")}`;

  const routeResult = await executeWialonBatch(sessionId, [
    {
      svc: "order/route_update",
      params: {
        itemId: args.resourceId,
        orders: routeOrders,
        routeId,
        callMode: "create",
        exp: 0,
        f: args.strictVisitSequence ? ROUTE_FLAG_STRICT_SEQUENCE : ROUTE_FLAG_ANY_SEQUENCE,
        n: finalRouteName,
        summary: {
          countOrders: routeOrders.length,
          mileage: totalMileage,
          priceMileage: 0,
          priceTotal: 0,
          weight: 0,
          cost: 0,
        },
      },
    },
  ]);

  const first = Array.isArray(routeResult) ? routeResult[0] : routeResult;
  if (first?.error) {
    return { error: 1, message: formatWialonError(first) };
  }

  const createdOrders = extractRouteOrders(routeResult);
  if (createdOrders.length < routeOrders.length) {
    return {
      error: 1,
      message: `Wialon created ${createdOrders.length} orders but ${routeOrders.length} were sent. Delete the partial route in Logistics and retry.`,
      planningUrl: `https://apps.wialon.com/logistics/?lang=en&sid=${sessionId}#/distrib/step3`,
    };
  }

  const assign = await assignRouteOrdersToUnit(sessionId, args.resourceId, args.unitId, createdOrders);
  const planningUrl = `https://apps.wialon.com/logistics/?lang=en&sid=${sessionId}#/distrib/step3`;
  if (!assign.ok) {
    return { error: 1, message: `Route created but vehicle was not picked up. ${assign.message}`, planningUrl };
  }
  return {
    error: 0,
    message: `Route created and vehicle assigned. ${assign.message} Verified ${createdOrders.length} orders.`,
    planningUrl,
  };
}
