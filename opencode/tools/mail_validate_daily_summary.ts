import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Validate a candidate daily summary JSON. Returns valid=true only when it exactly matches the public schema. It never modifies the JSON.",
  args: {
    summary_json: tool.schema.string().describe("The complete candidate daily summary as a JSON string"),
  },
  async execute(args, context) {
    const script = `${process.env.HERMES_HOME || `${process.env.HOME}/.hermes`}/scripts/email-pipeline/mail-agent-tools.py`
    const proc = Bun.spawn([
      "python3", script, "validate-daily-summary",
      "--workspace", context.directory,
      "--json", args.summary_json,
    ], { stdout: "pipe", stderr: "pipe" })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    if ((await proc.exited) !== 0) throw new Error(stderr || stdout)
    return stdout.trim()
  },
})
