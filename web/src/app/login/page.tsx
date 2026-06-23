"use client";

import { FormEvent, Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import styles from "./login.module.css";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/dispatch";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    setLoading(false);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      setError(payload.error ?? "Login failed.");
      return;
    }
    router.replace(next);
    router.refresh();
  }

  return (
    <form className={styles.card} onSubmit={onSubmit}>
      <p className={styles.eyebrow}>Farmers Choice</p>
      <h1 className={styles.title}>Route Dispatch</h1>
      <p className={styles.sub}>Sign in to access logistics route planning and dispatch.</p>
      <label className={styles.field}>
        Username
        <input className={styles.input} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
      </label>
      <label className={styles.field}>
        Password
        <input
          className={styles.input}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
      </label>
      {error ? <p className={styles.error}>{error}</p> : null}
      <button className={styles.submit} type="submit" disabled={loading}>
        {loading ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className={styles.wrap}>
      <Suspense fallback={<div className={styles.card}>Loading…</div>}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
