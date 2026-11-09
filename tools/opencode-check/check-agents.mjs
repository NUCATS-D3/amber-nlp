// Decode every project agent's permission rules with OpenCode's own schema, so a schema change
// fails here instead of silently turning the rules into no-ops.
import { readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { Schema } from "effect"
import { Ruleset } from "@opencode/schema/permission"

const agents = new URL("../../.opencode/agents/", import.meta.url).pathname
const decode = Schema.decodeUnknownSync(Ruleset)
let failures = 0
for (const name of readdirSync(agents).filter((file) => file.endsWith(".md"))) {
  const text = readFileSync(join(agents, name), "utf8")
  const front = text.startsWith("---\n") ? text.slice(4, text.indexOf("\n---\n", 4)) : ""
  const lines = front.split("\n")
  const start = lines.indexOf("permissions:")
  const items = []
  for (const line of start < 0 ? [] : lines.slice(start + 1)) {
    if (!line.startsWith("  - ")) break
    items.push(line.slice(4))
  }
  try {
    if (items.length === 0) throw new Error("no permissions list")
    const rules = decode(items.map((item) => JSON.parse(item)))
    if (name === "reviewer.md" && (rules[0].action !== "*" || rules[0].effect !== "deny")) {
      throw new Error("the reviewer must deny everything first")
    }
    console.log(`${name}: ${rules.length} rules`)
  } catch (error) {
    failures += 1
    console.error(`${name}: ${error.message}`)
  }
}
process.exit(failures ? 1 : 0)
