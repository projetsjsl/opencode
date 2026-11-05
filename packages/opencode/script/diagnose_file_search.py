"""Compare the existing native file-search case without changing its assertions."""
import ctypes
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import time

BASE = "148842ef7cb1bc34192b3fff57d0f9721ab9c801"
TARGET = "9c800d4988fe489a288a76a62430da2ad2b3c635"
class DiagnosticGuardError(RuntimeError):
    pass


def guard(condition, code):
    if not condition:
        raise DiagnosticGuardError(code)


def parse_environment(log):
    markers = [line.removeprefix("FILE_SEARCH_ENV ") for line in log.splitlines() if line.startswith("FILE_SEARCH_ENV ")]
    guard(len(markers) == 1, "environment_marker_not_unique")
    value = json.loads(markers[0])
    guard(isinstance(value, dict), "environment_marker_invalid")
    guard(value.get("backend") in ("ripgrep", "fff"), "effective_backend_unobserved")
    guard(type(value.get("effectiveDisableFFF")) is bool, "effective_flag_unobserved")
    guard(value.get("availableAtSelection") is None or type(value["availableAtSelection"]) is bool, "availability_invalid")
    guard(all(isinstance(value.get(key), str) and value[key] for key in ("coreModule", "effectModule", "home", "tmp")), "environment_paths_unobserved")
    return value


def save():
    (REPORT / "report.json").write_text(json.dumps(report, indent=2) + "\n")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT, timeout=40).strip()


def link_directory(target, source):
    if not source.is_dir():
        return
    guard(not os.path.lexists(target), "dependency_target_exists")
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(target), str(source)], check=True, capture_output=True, timeout=10)
    else:
        target.symlink_to(source, target_is_directory=True)


def instrument(tree, original_test):
    package = tree / "packages/opencode"
    test = package / "test/server/httpapi-file.test.ts"
    text = test.read_text()
    guard(text == original_test, "native_case_sources_differ")
    opening = 'test("serves search endpoints", async () => {\n    await using tmp = await tmpdir({ git: true })'
    guard(text.count(opening) == 1, "native_case_shape_changed")
    fixture = '{ path: ' + json.dumps(str(FIXTURE)) + ', [Symbol.asyncDispose]: async () => {} }'
    text = text.replace(opening, 'test("serves search endpoints", async () => {\n    await using tmp = ' + fixture)
    observer = '''import { spyOn as diagnosticSpyOn } from "bun:test"
import { Fff as diagnosticFff } from "@opencode-ai/core/filesystem/fff.bun"
import { Flag as diagnosticFlag } from "@opencode-ai/core/flag/flag"
let diagnosticAvailable: boolean | null = null
const diagnosticOriginalAvailable = diagnosticFff.available
diagnosticSpyOn(diagnosticFff, "available").mockImplementation(() => {
  const value = diagnosticOriginalAvailable()
  diagnosticAvailable = value
  return value
})
let diagnosticLast: unknown
'''
    text = observer + text
    poll = '          const body = await response.json()\n          return body.includes("hello.txt")'
    guard(text.count(poll) == 1, "poll_shape_changed")
    text = text.replace(poll, '          const body = await response.json()\n          diagnosticLast = { status: response.status, body }\n          return body.includes("hello.txt")')
    hook = "afterEach(async () => {\n"
    guard(text.count(hook) == 1, "cleanup_shape_changed")
    text = text.replace(hook, hook + '  console.log("FILE_SEARCH_RESULT", JSON.stringify(diagnosticLast))\n'
                        + '  console.log("FILE_SEARCH_ENV", JSON.stringify({ disableFFF: process.env.OPENCODE_DISABLE_FFF ?? null, effectiveDisableFFF: diagnosticFlag.OPENCODE_DISABLE_FFF, availableAtSelection: diagnosticAvailable, backend: diagnosticFlag.OPENCODE_DISABLE_FFF || diagnosticAvailable === false ? "ripgrep" : diagnosticAvailable === true ? "fff" : "unobserved", coreModule: Bun.resolveSync("@opencode-ai/core/fs-util", import.meta.dir), effectModule: Bun.resolveSync("effect", import.meta.dir), home: process.env.OPENCODE_TEST_HOME, tmp: process.env.TMPDIR }))\n')
    guard(text.count("expect(") == original_test.count("expect("), "assertion_count_changed")
    test.write_text(text)
    preload = package / "test/preload.ts"
    content = preload.read_text()
    old = 'const dir = path.join(os.tmpdir(), "opencode-test-data-" + process.pid)'
    guard(content.count(old) == 1, "preload_shape_changed")
    preload.write_text(content.replace(old, 'const dir = process.env.OPENCODE_SEARCH_STATE_DIR!'))


def birth(process):
    if os.name != "nt":
        return subprocess.check_output(["ps", "-p", str(process.pid), "-o", "lstart="], text=True,
                                       env={**os.environ, "LC_ALL": "C", "TZ": "UTC"}).strip()
    from ctypes import wintypes
    values = [wintypes.FILETIME() for _ in range(4)]
    call = ctypes.windll.kernel32.GetProcessTimes
    call.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    call.restype = wintypes.BOOL
    guard(call(wintypes.HANDLE(int(process._handle)), *(ctypes.byref(value) for value in values)), "birth_unavailable")
    ticks = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    return datetime.datetime.fromtimestamp((ticks - 116444736000000000) / 10000000, datetime.timezone.utc).isoformat()


def run_case(label, tree, backend):
    key = label + "-" + backend
    if STATE.exists():
        STATE.rename(WORK / ("state-before-" + key))
    (STATE / "home/AppData/Local").mkdir(parents=True)
    (STATE / "home/AppData/Roaming").mkdir(parents=True)
    hello = FIXTURE / "hello.txt"
    if hello.exists():
        guard(hello.read_text() == "needle", "fixture_content_changed")
        hello.rename(WORK / ("prior-content-" + key))
    env = {**os.environ, "HOME": str(STATE / "home"), "USERPROFILE": str(STATE / "home"),
           "APPDATA": str(STATE / "home/AppData/Roaming"), "LOCALAPPDATA": str(STATE / "home/AppData/Local"),
           "TMPDIR": str(TMP), "TEMP": str(TMP), "TMP": str(TMP),
           "OPENCODE_SEARCH_STATE_DIR": str(STATE), "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true",
           "GITHUB_ACTIONS": "false"}
    env.pop("OPENCODE_DISABLE_FFF", None)
    env["OPENCODE_DISABLE_FFF"] = "1" if backend == "ripgrep" else "0"
    logfile = REPORT / (key + ".log")
    result = {"label": label, "requested_backend": backend, "log": str(logfile), "timeout": False}
    report["cases"].append(result)
    save()
    with logfile.open("wb") as log:
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen([shutil.which("bun"), "test", "test/server/httpapi-file.test.ts",
                                    "--test-name-pattern", "serves search endpoints", "--timeout", "30000", "--only-failures"],
                                   cwd=tree / "packages/opencode", env=env, stdout=log, stderr=subprocess.STDOUT, **options)
        result["pid"] = process.pid
        try:
            result["birth_utc"] = birth(process)
            save()
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                result["timeout"] = True
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    killed = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                            capture_output=True, text=True, timeout=15)
                    result["owned_tree_kill_rc"] = killed.returncode
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)
            result["rc"] = process.returncode
            result["parent_reaped"] = process.poll() is not None
            save()
    output = logfile.read_text(errors="replace")
    environment = parse_environment(output)
    result["effective_backend"] = environment["backend"]
    result["environment"] = environment
    save()
    print(json.dumps(result), flush=True)
    print(output, flush=True)
    if result["timeout"]:
        raise DiagnosticGuardError("probe_timeout")
    return result["rc"]


def main():
    global ROOT, REPORT, WORK, FIXTURE, STATE, TMP, report
    try:
        script_root = pathlib.Path(__file__).resolve().parents[3]
        ROOT = pathlib.Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                                    cwd=script_root, text=True).strip()).resolve()
        guard(ROOT == script_root, "unexpected_repository_root")
        parent = pathlib.Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
        guard(all(not os.path.lexists(p / ".git") for p in [parent, *parent.parents]), "temporary_parent_contains_git")
        guard(bool(os.environ.get("OPENCODE_SEARCH_REPORT_DIR")), "report_directory_required")
        REPORT = pathlib.Path(os.environ["OPENCODE_SEARCH_REPORT_DIR"]).resolve()
        REPORT.mkdir(parents=True, exist_ok=False)
        WORK = pathlib.Path(tempfile.mkdtemp(prefix="opencode-search-", dir=parent)).resolve()
        FIXTURE, STATE, TMP = WORK / "fixture", WORK / "state", WORK / "tmp"
        FIXTURE.mkdir()
        TMP.mkdir()
        report = {"base": BASE, "head": None, "platform": os.name, "work": str(WORK), "fixture": str(FIXTURE),
                  "home": str(STATE / "home"), "tmp": str(TMP), "per_process_limit_seconds": 45, "cases": [],
                  "mirrored_unit_environment": {"GITHUB_ACTIONS": "false", "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true"}}
        report["head"] = TARGET
        report["diagnostic_sha"] = git("rev-parse", "HEAD")
        for revision in [BASE, TARGET]:
            try:
                git("cat-file", "-e", revision + "^{commit}")
            except subprocess.CalledProcessError:
                git("fetch", "--depth=1", "origin", revision)
        guard(git("rev-parse", BASE + ":packages/core") == git("rev-parse", TARGET + ":packages/core") == git("rev-parse", "HEAD:packages/core"), "core_dependency_trees_differ")
        git("diff", "--exit-code", BASE, TARGET, "--", "bun.lock", "package.json", "packages/*/package.json")
        git("diff", "--exit-code", TARGET, "HEAD", "--", "packages/core", "bun.lock", "package.json", "packages/*/package.json")
        original = (ROOT / "packages/opencode/test/server/httpapi-file.test.ts").read_text()
        report["original_test_sha256"] = hashlib.sha256(original.encode()).hexdigest()
        trees = {}
        for label, revision in [("head", report["head"]), ("baseline", BASE)]:
            tree = WORK / label
            git("worktree", "add", "--detach", str(tree), revision)
            for dependency in [ROOT / "node_modules", *ROOT.glob("packages/*/node_modules")]:
                link_directory(tree / dependency.relative_to(ROOT), dependency)
            instrument(tree, original)
            trees[label] = tree
        git("init", "--quiet", str(FIXTURE))
        guard(pathlib.Path(git("-C", str(FIXTURE), "rev-parse", "--show-toplevel")).resolve() == FIXTURE, "fixture_git_root_changed")
        for name, value in [("core.fsmonitor", "false"), ("commit.gpgsign", "false"),
                            ("user.email", "fixture@opencode.test"), ("user.name", "Fixture")]:
            git("-C", str(FIXTURE), "config", name, value)
        git("-C", str(FIXTURE), "commit", "--allow-empty", "--quiet", "-m", "fixture")
        save()
        failures = []
        for backend in ["ripgrep", "fff"]:
            for label in ["head", "baseline"]:
                failures.append(run_case(label, trees[label], backend))
        report["complete"] = True
        save()
        return 1 if any(failures) else 0
    except Exception as error:
        if "report" in globals():
            report["error_code"] = str(error) if isinstance(error, DiagnosticGuardError) else "operation_failed"
            report["error_type"] = type(error).__name__
            save()
        print("FILE_SEARCH_DIAGNOSTIC_FAILED", type(error).__name__, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
