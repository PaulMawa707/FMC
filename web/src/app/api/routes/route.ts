import { NextResponse } from "next/server";
import { getSessionUser } from "@/lib/auth";
import { DEFAULT_FLEET_WORKBOOK, DEFAULT_WORKBOOK } from "@/lib/config";
import {
  expandRouteOrders,
  loadRoutesFromWorkbook,
  readFleetAssets,
  readWorkbookBuffer,
  summarizeExpandedOrders,
  validateExpandedOrders,
} from "@/lib/workbook";

export async function GET() {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const workbookBuffer = await readWorkbookBuffer(DEFAULT_WORKBOOK);
    const routes = loadRoutesFromWorkbook(workbookBuffer, DEFAULT_WORKBOOK).map((route) => {
      const expanded = expandRouteOrders(route.orders);
      const summary = summarizeExpandedOrders(expanded);
      const check = validateExpandedOrders(expanded);
      return {
        sheet_name: route.sheet_name,
        route_name: route.route_name,
        parser: route.parser,
        base_stops: route.orders.length,
        expanded_stops: summary.total,
        deliveries: summary.deliveries,
        collections: summary.collections,
        dispatch_ready: check.ok,
        total_amount: route.orders.reduce((s, o) => s + (o.AMOUNT || 0), 0),
        total_tonnage: route.orders.reduce((s, o) => s + (o.TONNAGE || 0), 0),
        orders: route.orders,
        expanded_preview: expanded.slice(0, 5),
      };
    });
    return NextResponse.json({ routes });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Failed to load routes." }, { status: 500 });
  }
}
