// Copy the repository's OpenCode plugins next to this package's node_modules so tsc resolves
// "@opencode/plugin" from the pinned version.
import { cpSync, mkdirSync, readdirSync, rmSync } from "node:fs"
import { join } from "node:path"
import { fileURLToPath } from "node:url"

const source = fileURLToPath(new URL("../../.opencode/plugins/", import.meta.url))
const build = fileURLToPath(new URL("./build/", import.meta.url))
rmSync(build, { recursive: true, force: true })
mkdirSync(build)
const plugins = readdirSync(source).filter((name) => name.endsWith(".ts"))
if (plugins.length === 0) throw new Error("no OpenCode plugins found")
for (const name of plugins) cpSync(join(source, name), join(build, name))
console.log(`type-checking ${plugins.join(", ")}`)
