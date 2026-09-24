// OpenCode plugin: refuse every tool call that is not one of the swiss_tip
// MCP server's.
//
// The configuration cannot simply turn the built-in tools off: since
// 17 September 2026 the OpenCode Zen free tier rejects a request without
// them ("OpenCode's free tier can only be used from within OpenCode"), and
// `tools: false` and `permission: deny` both leave them out of the request.
// So the built-in tools are offered to the model, and this hook stops any of
// them before it runs; the model reads the error and goes back to swiss_tip.
export const SwissTipToolsOnly = async () => ({
  "tool.execute.before": async (input) => {
    if (!input.tool.startsWith("swiss_tip_")) {
      throw new Error(`the tool ${input.tool} is disabled in this demo; use the swiss_tip tools`)
    }
  },
})
