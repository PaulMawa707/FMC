import { NextResponse } from "next/server";
import { getSessionUser } from "@/lib/auth";
import { DEFAULT_FLEET_WORKBOOK } from "@/lib/config";
import { readFleetAssets, readWorkbookBuffer } from "@/lib/workbook";

export async function GET() {
  const user = await getSessionUser();
  if (!user) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  try {
    const buffer = await readWorkbookBuffer(DEFAULT_FLEET_WORKBOOK);
    const fleet = readFleetAssets(buffer);
    return NextResponse.json({ fleet });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to load fleet." },
      { status: 500 },
    );
  }
}
