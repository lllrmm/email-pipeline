import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Safely fetch one previously registered public HTTPS link without cookies or JavaScript.",
  args: { link_id: tool.schema.string().describe("Link id from mail_extract_links") },
  async execute(args, context) {
    const script = `${process.env.HERMES_HOME || `${process.env.HOME}/.hermes`}/scripts/mail-agent-tools.py`
    const proc = Bun.spawn(["python3", script, "inspect-link", "--workspace", context.directory, "--id", args.link_id], { stdout: "pipe", stderr: "pipe" })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    if ((await proc.exited) !== 0) throw new Error(stderr || stdout)
    return stdout.trim()
  },
})
