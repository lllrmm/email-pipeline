import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Extract and register links from one HTML/text part or extracted attachment.",
  args: { source_id: tool.schema.string().describe("Part or attachment id from manifest.json") },
  async execute(args, context) {
    const script = `${process.env.HERMES_HOME || `${process.env.HOME}/.hermes`}/scripts/email-pipeline/mail-agent-tools.py`
    const proc = Bun.spawn(["python3", script, "extract-links", "--workspace", context.directory, "--id", args.source_id], { stdout: "pipe", stderr: "pipe" })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    if ((await proc.exited) !== 0) throw new Error(stderr || stdout)
    return stdout.trim()
  },
})
