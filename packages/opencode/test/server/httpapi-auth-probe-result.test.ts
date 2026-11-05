import { expect, spyOn, test } from "bun:test"
import { Effect } from "effect"
import * as backend from "./httpapi-exercise/backend"
import { http } from "./httpapi-exercise/dsl"
import { parseOptions } from "./httpapi-exercise/routing"
import { runScenario } from "./httpapi-exercise/runner"
import { TestLLMServer } from "../lib/llm-server"

async function authResult(missingStatus: number, validStatus: number, timedOut = false) {
  const probe = spyOn(backend, "callAuthProbe").mockImplementation((_scenario, credentials) =>
    Effect.succeed({
      status: credentials === "missing" ? missingStatus : validStatus,
      contentType: "",
      text: "",
      body: undefined,
      timedOut: credentials === "valid" && timedOut,
    }),
  )
  try {
    return await Effect.runPromise(
      runScenario(parseOptions(["--mode", "auth"]))(http.protected.get("/probe", "auth-fixture").global().json()).pipe(
        Effect.provide(TestLLMServer.layer),
        Effect.scoped,
      ),
    )
  } finally {
    probe.mockRestore()
  }
}

test("an authenticated timeout fails the auth scenario", async () => {
  const result = await authResult(401, 0, true)
  expect(result.status).toBe("fail")
})

test("status zero without a timeout marker is not an authenticated response", async () => {
  const result = await authResult(401, 0)
  expect(result.status).toBe("fail")
})

test("missing credentials must be refused and valid credentials must pass the auth gate", async () => {
  expect((await authResult(200, 200)).status).toBe("fail")
  expect((await authResult(401, 401)).status).toBe("fail")
  expect((await authResult(401, 400)).status).toBe("pass")
})
