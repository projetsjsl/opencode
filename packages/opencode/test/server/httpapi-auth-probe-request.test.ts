import { expect, test } from "bun:test"
import { Schema } from "effect"
import { Pty } from "@opencode-ai/schema/pty"
import { toAuthProbeRequest } from "./httpapi-exercise/auth-probe"
import type { ActiveScenario } from "./httpapi-exercise/types"

function scenario(path: string, method: ActiveScenario["method"] = "POST", authProbe?: ActiveScenario["authProbe"]) {
  return { path, method, authProbe }
}

for (const path of ["/pty", "/api/pty"]) {
  for (const credentials of ["missing", "valid"] as const) {
    test(`auth probe ${credentials} for ${path} cannot create a terminal`, async () => {
      const request = toAuthProbeRequest(scenario(path), credentials, new AbortController().signal)
      expect(request.method).toBe("POST")
      expect(new URL(request.url).pathname).toBe(path)
      expect(request.headers.get("authorization")).toBe(
        credentials === "valid" ? `Basic ${Buffer.from("opencode:secret").toString("base64")}` : null,
      )
      expect(() => Schema.decodeUnknownSync(Pty.CreateInput)({})).not.toThrow()
      const body = await request.json()
      expect(() => Schema.decodeUnknownSync(Pty.CreateInput)(body)).toThrow()
    })
  }

  test(`an explicit auth probe for ${path} also cannot launch a command`, async () => {
    const request = toAuthProbeRequest(
      scenario(path, "POST", { path, body: { command: "/bin/sh", args: ["-c", "sleep 30"] } }),
      "valid",
      new AbortController().signal,
    )
    const body = await request.json()
    expect(() => Schema.decodeUnknownSync(Pty.CreateInput)(body)).toThrow()
  })
}

test("non-creation probes preserve their body, path, method and cancellation signal", async () => {
  const controller = new AbortController()
  const request = toAuthProbeRequest(
    scenario("/pty/{ptyID}", "PATCH", { path: "/pty/known", body: { rows: 24 } }),
    "valid",
    controller.signal,
  )
  expect(request.method).toBe("PATCH")
  expect(new URL(request.url).pathname).toBe("/pty/known")
  expect(await request.json()).toEqual({ rows: 24 })
  controller.abort()
  expect(request.signal.aborted).toBe(true)
})

test("read-only probes keep an empty body", () => {
  const request = toAuthProbeRequest(scenario("/pty", "GET"), "missing", new AbortController().signal)
  expect(request.body).toBeNull()
})
