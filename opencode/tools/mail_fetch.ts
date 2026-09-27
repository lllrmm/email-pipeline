import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Fetch the single email authorized by request.json into message.eml.",
  args: {},
  async execute(_args, context) {
    const script = `${process.env.HERMES_HOME || `${process.env.HOME}/.hermes`}/scripts/mail-agent-tools.py`
    const proc = Bun.spawn(["python3", script, "fetch", "--workspace", context.directory], { stdout: "pipe", stderr: "pipe" })
    const stdout = await new Response(proc.stdout).text()
    const stderr = await new Response(proc.stderr).text()
    if ((await proc.exited) !== 0) throw new Error(stderr || stdout)
    return stdout.trim()
  },
})
