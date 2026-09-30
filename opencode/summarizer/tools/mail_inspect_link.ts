import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Safely fetch one previously registered public HTTPS link without cookies or JavaScript.",
  args: { link_id: tool.schema.string().describe("Link id from mail_extract_links") },
  async execute(args, context) {
    const script = `${process.env.EMAIL_PIPELINE_CODE_ROOT || `${process.env.HOME}/email-pipeline-code`}/.venv/bin/email-pipeline`
    const proc = Bun.spawn([script, "tools", "inspect-link", "--workspace", context.directory, "--id", args.link_id], { stdout: "pipe", stderr: "pipe" })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    if ((await proc.exited) !== 0) throw new Error(stderr || stdout)
    return stdout.trim()
  },
})
