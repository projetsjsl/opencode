import type { ActiveScenario } from "./types"

export function toAuthProbeRequest(
  scenario: Pick<ActiveScenario, "method" | "path" | "authProbe">,
  credentials: "missing" | "valid",
  signal: AbortSignal,
) {
  const spec = scenario.authProbe ?? {
    path: authProbePath(scenario.path),
    body: scenario.method === "GET" ? undefined : {},
  }
  // Empty PTY creation input is valid and launches the default interactive shell.
  // Auth probes only need to reach decoding; effect scenarios exercise creation.
  const body = scenario.method === "POST" && ["/pty", "/api/pty"].includes(spec.path) ? { command: 1 } : spec.body
  const headers = {
    ...(body === undefined ? {} : { "content-type": "application/json" }),
    ...spec.headers,
    ...(credentials === "valid" ? { authorization: basic("opencode", "secret") } : {}),
  }
  return new Request(new URL(spec.path, "http://localhost"), {
    method: scenario.method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
}

function basic(username: string, password: string) {
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`
}

function authProbePath(path: string) {
  return path
    .replace(/\{([^}]+)\}/g, (_match, key: string) => `auth_${key}`)
    .replace(/:([^/]+)/g, (_match, key: string) => `auth_${key}`)
}
