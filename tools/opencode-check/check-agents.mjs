// Decode every project agent's frontmatter with OpenCode's own agent schema (ConfigAgent.Info),
// rejecting unknown keys, so a renamed field, a changed permission shape, or a misspelled key fails
// here instead of silently turning the agent's rules into no-ops. The reviewer must exist and deny
// everything first; whether later rules override as intended is verified live after upgrades.
import { readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { fileURLToPath } from "node:url"
import { Schema } from "effect"
import { ConfigAgent } from "@opencode/schema/config/agent"
import { Ruleset } from "@opencode/schema/permission"
import { parse } from "yaml"

const agents = fileURLToPath(new URL("../../.opencode/agents/", import.meta.url))
const decode = Schema.decodeUnknownSync(ConfigAgent.Info, { onExcessProperty: "error" })
const files = readdirSync(agents).filter((file) => file.endsWith(".md"))
let failures = 0
if (!files.includes("reviewer.md")) {
  failures += 1
  console.error("reviewer.md: missing")
}
for (const name of files) {
  try {
    const text = readFileSync(join(agents, name), "utf8").replaceAll("\r\n", "\n")
    const end = text.indexOf("\n---\n", 4)
    if (!text.startsWith("---\n") || end < 0) throw new Error("no frontmatter")
    const agent = decode(parse(text.slice(4, end)))
    const rules = agent.permissions ?? []
    if (name === "reviewer.md" && (rules[0]?.action !== "*" || rules[0]?.effect !== "deny")) {
      throw new Error("the reviewer must deny everything first")
    }
    console.log(`${name}: mode ${agent.mode ?? "primary"}, ${rules.length} permission rules`)
  } catch (error) {
    failures += 1
    console.error(`${name}: ${error.message}`)
  }
}
// The project config's rules ask before an agent edits a protected path (amber-a6d.9).
try {
  const config = JSON.parse(readFileSync(fileURLToPath(new URL("../../opencode.json", import.meta.url)), "utf8"))
  const rules = Schema.decodeUnknownSync(Ruleset, { onExcessProperty: "error" })(config.permissions)
  if (rules.length === 0 || rules.some((rule) => rule.action !== "edit" || rule.effect !== "ask")) {
    throw new Error("expected only edit rules that ask")
  }
  console.log(`opencode.json: ${rules.length} ask rules`)
} catch (error) {
  failures += 1
  console.error(`opencode.json: ${error.message}`)
}
process.exit(failures ? 1 : 0)
