// OpenCode (V2) port of the agent hooks in .claude/hooks (amber-a6d.5). The Python scripts hold
// the logic; this plugin feeds them Claude-shaped hook input and returns their verdicts:
// - after a successful edit or write, post_edit.py's failures are appended to the tool result;
// - when a top-level session in this project finishes a turn, stop_gate.py runs, and its failures
//   are sent back as a prompt so the agent keeps working. The next turn end passes
//   stop_hook_active, so the gate blocks at most once in a row, as in Claude Code.
// Types come from @opencode/plugin through a type-only import, which is erased at runtime, so the
// plugin needs no install; CI type-checks it against a pinned SDK (tools/opencode-check).
import type { Plugin } from "@opencode/plugin"
import { spawn } from "node:child_process"
import path from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..")
const HOOKS = path.join(ROOT, ".claude", "hooks")
const EDIT_TOOLS = new Set(["edit", "write"])
const GATE_SOURCE = "amber-stop-gate"

type Part = { type: "text"; text: string } | { type: "file"; uri: string; mime: string; name?: string }
type Content = string | ReadonlyArray<Part> | undefined
type HookResult = { code: number; stdout: string; stderr: string }

function runHook(script: string, event: Record<string, unknown>): Promise<HookResult> {
  return new Promise((resolve) => {
    const child = spawn("python3", [path.join(HOOKS, script)], { cwd: ROOT })
    let stdout = ""
    let stderr = ""
    child.stdout.on("data", (chunk) => (stdout += chunk))
    child.stderr.on("data", (chunk) => (stderr += chunk))
    child.on("error", (error) => resolve({ code: -1, stdout, stderr: String(error) }))
    child.on("close", (code) => resolve({ code: code ?? -1, stdout, stderr }))
    child.stdin.end(JSON.stringify(event))
  })
}

function appendText(content: Content, text: string): Content {
  if (content === undefined) return text
  if (typeof content === "string") return `${content}\n\n${text}`
  return [...content, { type: "text", text }]
}

function editedFile(input: unknown): string | undefined {
  if (typeof input !== "object" || input === null) return undefined
  const fields = input as Record<string, unknown>
  const file = fields.path ?? fields.filePath ?? fields.file_path
  return typeof file === "string" ? file : undefined
}

export default {
  id: "amber.harness",
  async setup(ctx) {
    await ctx.tool.hook("execute.after", async (event) => {
      if (event.status !== "completed" || !EDIT_TOOLS.has(event.tool)) return
      const file = editedFile(event.input)
      if (file === undefined) return
      const result = await runHook("post_edit.py", {
        hook_event_name: "PostToolUse",
        tool_name: event.tool,
        cwd: ctx.location.directory,
        tool_input: { file_path: file },
      })
      if (result.code !== 2) return
      event.result = { ...event.result, content: appendText(event.result?.content, result.stderr.trim()) }
    })

    const continuing = new Set<string>()
    const running = new Set<string>()

    async function gate(sessionID: string) {
      const session = await ctx.session.get({ sessionID })
      if (session.parentID || session.projectID !== ctx.location.project.id) return
      if (running.has(sessionID)) return
      running.add(sessionID)
      try {
        const active = continuing.delete(sessionID)
        const result = await runHook("stop_gate.py", {
          hook_event_name: "Stop",
          session_id: sessionID,
          cwd: ctx.location.directory,
          stop_hook_active: active,
        })
        if (result.code !== 2) return
        continuing.add(sessionID)
        await ctx.session.prompt({
          sessionID,
          text: `Stop gate (.claude/hooks/stop_gate.py):\n\n${result.stderr.trim()}`,
          metadata: { source: GATE_SOURCE },
          delivery: "queue",
        })
      } finally {
        running.delete(sessionID)
      }
    }

    const controller = new AbortController()
    void (async () => {
      const events = ctx.event.subscribe({
        types: ["session.execution.succeeded"],
        signal: controller.signal,
      })
      for await (const event of events) {
        const sessionID = event?.data?.sessionID
        if (typeof sessionID !== "string") continue
        void gate(sessionID).catch((error) => console.error(`[${GATE_SOURCE}]`, error))
      }
    })().catch((error) => {
      if (!controller.signal.aborted) console.error(`[${GATE_SOURCE}]`, error)
    })
    return () => controller.abort()
  },
} satisfies Plugin.Plugin
