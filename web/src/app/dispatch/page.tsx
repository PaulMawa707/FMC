"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "./dispatch.module.css";

type RouteRow = {
  route_name: string;
  base_stops: number;
  expanded_stops: number;
  deliveries: number;
  collections: number;
  dispatch_ready: boolean;
};

type FleetRow = { asset_name: string; itemid: number };

const ROUTE_VEHICLE_MAP: Record<string, string> = {
  "eastlands route": "FCL - KBT 227L",
  "ngong rd route": "FCL - KBV 586L",
  "southlands route": "FCL - KCF 844G",
};

function defaultWindow() {
  const now = new Date();
  const start = new Date(now);
  start.setHours(4, 0, 0, 0);
  const end = new Date(now);
  end.setHours(18, 0, 0, 0);
  return {
    start: start.toISOString().slice(0, 16),
    end: end.toISOString().slice(0, 16),
  };
}

export default function DispatchPage() {
  const [routes, setRoutes] = useState<RouteRow[]>([]);
  const [fleet, setFleet] = useState<FleetRow[]>([]);
  const [selectedRoute, setSelectedRoute] = useState("");
  const [selectedVehicle, setSelectedVehicle] = useState("");
  const [windowRange, setWindowRange] = useState(defaultWindow);
  const [message, setMessage] = useState<{ type: "ok" | "err"; text: string; url?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [dispatching, setDispatching] = useState(false);

  useEffect(() => {
    async function load() {
      setLoading(true);
      const [routesRes, fleetRes] = await Promise.all([fetch("/api/routes"), fetch("/api/fleet")]);
      const routesJson = await routesRes.json();
      const fleetJson = await fleetRes.json();
      if (routesRes.ok) {
        setRoutes(routesJson.routes ?? []);
        const first = routesJson.routes?.[0]?.route_name ?? "";
        setSelectedRoute(first);
      }
      if (fleetRes.ok) {
        setFleet(fleetJson.fleet ?? []);
      }
      setLoading(false);
    }
    load();
  }, []);

  useEffect(() => {
    const expected = ROUTE_VEHICLE_MAP[selectedRoute];
    if (expected && fleet.some((f) => f.asset_name === expected)) {
      setSelectedVehicle(expected);
    }
  }, [selectedRoute, fleet]);

  const selectedSummary = useMemo(
    () => routes.find((r) => r.route_name === selectedRoute),
    [routes, selectedRoute],
  );

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  async function dispatchRoute() {
    setDispatching(true);
    setMessage(null);
    const vehicle = fleet.find((f) => f.asset_name === selectedVehicle);
    if (!vehicle) {
      setMessage({ type: "err", text: "Select a valid vehicle." });
      setDispatching(false);
      return;
    }
    const response = await fetch("/api/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        routeName: selectedRoute,
        vehicleName: vehicle.asset_name,
        unitId: vehicle.itemid,
        startIso: new Date(windowRange.start).toISOString(),
        endIso: new Date(windowRange.end).toISOString(),
      }),
    });
    const payload = await response.json();
    setDispatching(false);
    if (!response.ok) {
      setMessage({ type: "err", text: payload.error ?? "Dispatch failed.", url: payload.planningUrl });
      return;
    }
    setMessage({ type: "ok", text: payload.message ?? "Route dispatched.", url: payload.planningUrl });
  }

  if (loading) {
    return <div className={styles.page}><p>Loading routes…</p></div>;
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Farmers Choice · FMC Logistics</p>
          <h1 className={styles.title}>Route Dispatch Console</h1>
        </div>
        <button type="button" className={styles.secondary} onClick={logout}>
          Logout
        </button>
      </header>

      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Available routes</h2>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th className={styles.th}>Route</th>
                <th className={styles.th}>Base</th>
                <th className={styles.th}>Expanded</th>
                <th className={styles.th}>DEL</th>
                <th className={styles.th}>COL</th>
              </tr>
            </thead>
            <tbody>
              {routes.map((route) => (
                <tr key={route.route_name}>
                  <td className={styles.td}>{route.route_name}</td>
                  <td className={styles.td}>{route.base_stops}</td>
                  <td className={styles.td}>{route.expanded_stops}</td>
                  <td className={styles.td}>{route.deliveries}</td>
                  <td className={styles.td}>{route.collections}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.grid}>
        <div className={styles.panel}>
          <h2 className={styles.panelTitle}>Dispatch settings</h2>
          <label className={styles.field}>
            Route
            <select className={styles.select} value={selectedRoute} onChange={(e) => setSelectedRoute(e.target.value)}>
              {routes.map((r) => (
                <option key={r.route_name} value={r.route_name}>
                  {r.route_name}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            Vehicle
            <select className={styles.select} value={selectedVehicle} onChange={(e) => setSelectedVehicle(e.target.value)}>
              {fleet.map((v) => (
                <option key={v.itemid} value={v.asset_name}>
                  {v.asset_name}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.row}>
            <label className={styles.field}>
              Start
              <input
                className={styles.input}
                type="datetime-local"
                value={windowRange.start}
                onChange={(e) => setWindowRange((w) => ({ ...w, start: e.target.value }))}
              />
            </label>
            <label className={styles.field}>
              End
              <input
                className={styles.input}
                type="datetime-local"
                value={windowRange.end}
                onChange={(e) => setWindowRange((w) => ({ ...w, end: e.target.value }))}
              />
            </label>
          </div>
          {selectedSummary?.dispatch_ready ? (
            <p className={styles.ok}>
              Dispatch check passed: {selectedSummary.deliveries} deliveries + {selectedSummary.collections}{" "}
              collections.
            </p>
          ) : (
            <p className={styles.err}>Dispatch check failed for this route.</p>
          )}
          <button type="button" className={styles.primary} disabled={dispatching} onClick={dispatchRoute}>
            {dispatching ? "Dispatching…" : "Dispatch selected route"}
          </button>
        </div>

        <div className={styles.panel}>
          <h2 className={styles.panelTitle}>Cargonz template</h2>
          <p>
            This console uses interim styling. After you purchase and download{" "}
            <strong>Cargonz</strong>, follow <code>docs/CARGONZ_SETUP.md</code> to merge the ThemeForest
            template skin and navigation.
          </p>
        </div>
      </section>

      {message ? (
        <div className={message.type === "ok" ? styles.bannerOk : styles.bannerErr}>
          <p>{message.text}</p>
          {message.url ? (
            <a className={styles.link} href={message.url} target="_blank" rel="noreferrer">
              Open in Logistics
            </a>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
