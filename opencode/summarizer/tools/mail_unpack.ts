import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Losslessly unpack message.eml into MIME parts, attachments, and manifest.json.",
  args: {},
  async execute(_args, context) {
    const script = `${process.env.EMAIL_PIPELINE_CODE_ROOT || `${process.env.HOME}/email-pipeline-code`}/.venv/bin/email-pipeline`
    const proc = Bun.spawn([script, "tools", "unpack", "--workspace", context.directory], { stdout: "pipe", stderr: "pipe" })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    if ((await proc.exited) !== 0) throw new Error(stderr || stdout)
    return stdout.trim()
  },
})
