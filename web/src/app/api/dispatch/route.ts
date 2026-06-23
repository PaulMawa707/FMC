import { NextRequest, NextResponse } from "next/server";
import { getSessionUser } from "@/lib/auth";
import { DEFAULT_ROUTE_VEHICLE_MAP, DEFAULT_WORKBOOK, getLogisticsToken, getResourceId } from "@/lib/config";
import {
  expandRouteOrders,
  loadRoutesFromWorkbook,
  readWorkbookBuffer,
  validateExpandedOrders,
} from "@/lib/workbook";
import { sendOrdersAndCreateRoute } from "@/lib/wialon";

export async function POST(request: NextRequest) {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const body = await request.json().catch(() => ({}));
  const routeName = String(body.routeName ?? "");
  const unitId = Number(body.unitId);
  const vehicleName = String(body.vehicleName ?? "");
  const startIso = String(body.startIso ?? "");
  const endIso = String(body.endIso ?? "");

  if (!routeName || !unitId || !vehicleName || !startIso || !endIso) {
    return NextResponse.json({ error: "Missing dispatch fields." }, { status: 400 });
  }

  const tf = Math.floor(new Date(startIso).getTime() / 1000);
  const tt = Math.floor(new Date(endIso).getTime() / 1000);
  if (!Number.isFinite(tf) || !Number.isFinite(tt) || tt <= tf) {
    return NextResponse.json({ error: "End time must be after start time." }, { status: 400 });
  }

  try {
    const buffer = await readWorkbookBuffer(DEFAULT_WORKBOOK);
    const routes = loadRoutesFromWorkbook(buffer, DEFAULT_WORKBOOK);
    const route = routes.find((r) => r.route_name === routeName);
    if (!route) return NextResponse.json({ error: "Route not found." }, { status: 404 });

    const expanded = expandRouteOrders(route.orders);
    const check = validateExpandedOrders(expanded);
    if (!check.ok) return NextResponse.json({ error: check.message }, { status: 400 });

    const expectedVehicle = DEFAULT_ROUTE_VEHICLE_MAP[routeName];
    const warnings: string[] = [];
    if (expectedVehicle && expectedVehicle !== vehicleName) {
      warnings.push(`${routeName} is usually assigned to ${expectedVehicle}.`);
    }

    const result = await sendOrdersAndCreateRoute({
      token: getLogisticsToken(),
      resourceId: getResourceId(),
      unitId,
      vehicleName,
      routeName,
      orders: expanded,
      tf,
      tt,
      strictVisitSequence: false,
    });

    if (result.error) {
      return NextResponse.json({ error: result.message, planningUrl: result.planningUrl, warnings }, { status: 502 });
    }

    return NextResponse.json({ ok: true, message: result.message, planningUrl: result.planningUrl, warnings });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Dispatch failed." },
      { status: 500 },
    );
  }
}
