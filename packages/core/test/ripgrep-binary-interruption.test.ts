import { expect, test } from "bun:test"
import { Deferred, Effect, Exit, Fiber, Layer } from "effect"
import { FSUtil } from "../src/fs-util"
import { LayerNode } from "../src/effect/layer-node"
import { RipgrepBinary } from "../src/ripgrep/binary"

function controlledBinary(isFile: () => Effect.Effect<boolean>) {
  const filesystem = Layer.effect(
    FSUtil.Service,
    Effect.gen(function* () {
      const native = yield* FSUtil.Service
      return { ...native, isFile }
    }).pipe(Effect.provide(LayerNode.compile(FSUtil.node))),
  )
  return LayerNode.compile(RipgrepBinary.node, [[FSUtil.node, filesystem]])
}

test("binary discovery recovers after its first caller is interrupted", async () => {
  const entered = Deferred.makeUnsafe<void>()
  let lookups = 0
  const layer = controlledBinary(() =>
    Effect.gen(function* () {
      lookups += 1
      if (lookups === 1) {
        yield* Deferred.succeed(entered, undefined)
        return yield* Effect.never
      }
      return true
    }),
  )
  const program = Effect.scoped(
    Effect.gen(function* () {
      const binary = yield* RipgrepBinary.Service
      const first = yield* Effect.forkScoped(binary.filepath)
      yield* Deferred.await(entered)
      yield* Fiber.interrupt(first)
      return yield* binary.filepath
    }),
  ).pipe(Effect.provide(layer))
  const result = await Effect.runPromiseExit(program)
  expect(Exit.isSuccess(result)).toBe(true)
  expect(lookups).toBe(2)
})

test("interrupting a waiting caller preserves the shared discovery and successful cache", async () => {
  const entered = Deferred.makeUnsafe<void>()
  const release = Deferred.makeUnsafe<void>()
  const waiting = Deferred.makeUnsafe<void>()
  let lookups = 0
  const layer = controlledBinary(() =>
    Effect.gen(function* () {
      lookups += 1
      yield* Deferred.succeed(entered, undefined)
      yield* Deferred.await(release)
      return true
    }),
  )
  const result = await Effect.runPromise(
    Effect.scoped(
      Effect.gen(function* () {
        const binary = yield* RipgrepBinary.Service
        const first = yield* Effect.forkScoped(binary.filepath)
        yield* Deferred.await(entered)
        const second = yield* Effect.forkScoped(
          Effect.gen(function* () {
            yield* Deferred.succeed(waiting, undefined)
            return yield* binary.filepath
          }),
        )
        yield* Deferred.await(waiting)
        yield* Fiber.interrupt(second)
        yield* Deferred.succeed(release, undefined)
        const value = yield* Fiber.join(first)
        return [value, yield* binary.filepath]
      }),
    ).pipe(Effect.provide(layer)),
  )
  expect(result[0]).toBe(result[1])
  expect(lookups).toBe(1)
})

test("ordinary discovery failure remains observable and cached", async () => {
  let lookups = 0
  const layer = controlledBinary(() =>
    Effect.sync(() => {
      lookups += 1
    }).pipe(Effect.andThen(Effect.die(new Error("discovery failure fixture")))),
  )
  const results = await Effect.runPromise(
    Effect.gen(function* () {
      const binary = yield* RipgrepBinary.Service
      return [yield* Effect.exit(binary.filepath), yield* Effect.exit(binary.filepath)]
    }).pipe(Effect.provide(layer)),
  )
  expect(results.every(Exit.isFailure)).toBe(true)
  expect(lookups).toBe(1)
})
