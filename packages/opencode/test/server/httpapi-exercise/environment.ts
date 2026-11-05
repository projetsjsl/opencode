import { Flag } from "@opencode-ai/core/flag/flag"
import { Effect } from "effect"
import path from "path"
import fs from "fs/promises"
import os from "os"
import { fileURLToPath } from "url"

const preserveExerciseGlobalRoot = !!process.env.OPENCODE_HTTPAPI_EXERCISE_GLOBAL
export const exerciseGlobalRoot =
  process.env.OPENCODE_HTTPAPI_EXERCISE_GLOBAL ??
  path.join(process.env.TMPDIR ?? "/tmp", `opencode-httpapi-global-${process.pid}`)
process.env.XDG_DATA_HOME = path.join(exerciseGlobalRoot, "data")
process.env.XDG_CONFIG_HOME = path.join(exerciseGlobalRoot, "config")
process.env.XDG_STATE_HOME = path.join(exerciseGlobalRoot, "state")
process.env.XDG_CACHE_HOME = path.join(exerciseGlobalRoot, "cache")
process.env.OPENCODE_DISABLE_SHARE = "true"
process.env.OPENCODE_TEST_HOME = path.join(exerciseGlobalRoot, "home")
process.env.OPENCODE_TEST_MANAGED_CONFIG_DIR = path.join(exerciseGlobalRoot, "managed")
delete process.env.OPENCODE_CONFIG
delete process.env.OPENCODE_CONFIG_CONTENT
delete process.env.OPENCODE_CONFIG_DIR
Flag.OPENCODE_CONFIG = undefined
Flag.OPENCODE_CONFIG_CONTENT = undefined
export const exerciseConfigDirectory = path.join(exerciseGlobalRoot, "config", "opencode")
export const exerciseDataDirectory = path.join(exerciseGlobalRoot, "data", "opencode")

const preserveExerciseDatabase = !!process.env.OPENCODE_HTTPAPI_EXERCISE_DB
export const exerciseDatabasePath =
  process.env.OPENCODE_HTTPAPI_EXERCISE_DB ??
  path.join(process.env.TMPDIR ?? "/tmp", `opencode-httpapi-exercise-${process.pid}.db`)
process.env.OPENCODE_DB = exerciseDatabasePath
Flag.OPENCODE_DB = exerciseDatabasePath

let prepared: Promise<{ directory: string }> | undefined
let projectDirectory: string | undefined

export function prepareExercisePaths() {
  return (prepared ??= (async () => {
    projectDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "opencode-httpapi-project-"))
    const directory = await fs.realpath(projectDirectory)
    await fs.mkdir(process.env.OPENCODE_TEST_HOME!, { recursive: true })
    await fs.mkdir(process.env.OPENCODE_TEST_MANAGED_CONFIG_DIR!, { recursive: true })

    // Config installs its plugin dependency on first use. Reuse the real workspace
    // package before starting timed HTTP probes instead of fetching from npm.
    const plugin = fileURLToPath(new URL("../../../../plugin/", import.meta.url))
    const manifest = JSON.parse(await fs.readFile(path.join(plugin, "package.json"), "utf8"))
    if (manifest.name !== "@opencode-ai/plugin" || typeof manifest.version !== "string")
      throw new Error("HttpApi fixture needs the workspace plugin package")
    const target = path.join(exerciseConfigDirectory, "node_modules", "@opencode-ai", "plugin")
    await fs.mkdir(path.dirname(target), { recursive: true })
    await fs.symlink(plugin, target, process.platform === "win32" ? "junction" : "dir").catch(async (error) => {
      if (error.code !== "EEXIST" || (await fs.realpath(target)) !== (await fs.realpath(plugin))) throw error
    })
    const dependencies = { "@opencode-ai/plugin": `file:${plugin}` }
    for (const [name, value] of Object.entries({
      "package.json": { dependencies },
      "package-lock.json": {
        lockfileVersion: 3,
        packages: {
          "": { dependencies },
          "node_modules/@opencode-ai/plugin": { resolved: `file:${plugin}`, link: true },
        },
      },
    })) {
      const file = path.join(exerciseConfigDirectory, name)
      const content = JSON.stringify(value, null, 2) + "\n"
      await fs.writeFile(file, content, { flag: "wx" }).catch(async (error) => {
        if (error.code !== "EEXIST" || (await fs.readFile(file, "utf8")) !== content) throw error
      })
    }
    if (
      (await fs.realpath(Bun.resolveSync("@opencode-ai/plugin", exerciseConfigDirectory))) !==
      (await fs.realpath(path.join(plugin, "src", "index.ts")))
    )
      throw new Error("HttpApi fixture did not resolve its workspace plugin")
    return { directory }
  })())
}

export const original = {
  OPENCODE_SERVER_PASSWORD: Flag.OPENCODE_SERVER_PASSWORD,
  OPENCODE_SERVER_USERNAME: Flag.OPENCODE_SERVER_USERNAME,
}

export const cleanupExercisePaths = Effect.promise(async () => {
  if (projectDirectory) await fs.rm(projectDirectory, { recursive: true, force: true })
  if (!preserveExerciseDatabase) {
    await Promise.all(
      [exerciseDatabasePath, `${exerciseDatabasePath}-wal`, `${exerciseDatabasePath}-shm`].map((file) =>
        fs.rm(file, { force: true }).catch(() => undefined),
      ),
    )
  }
  if (!preserveExerciseGlobalRoot)
    await fs.rm(exerciseGlobalRoot, { recursive: true, force: true }).catch(() => undefined)
})
