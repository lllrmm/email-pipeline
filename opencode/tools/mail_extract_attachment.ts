import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Extract readable text from one attachment listed in manifest.json.",
  args: { attachment_id: tool.schema.string().describe("Attachment id such as attachment-1") },
  async execute(args, context) {
    const script = `${process.env.HERMES_HOME || `${process.env.HOME}/.hermes`}/scripts/mail-agent-tools.py`
    const proc = Bun.spawn(["python3", script, "extract-attachment", "--workspace", context.directory, "--id", args.attachment_id], { stdout: "pipe", stderr: "pipe" })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    if ((await proc.exited) !== 0) throw new Error(stderr || stdout)
    return stdout.trim()
  },
})
