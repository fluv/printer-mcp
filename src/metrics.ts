import { Registry, Counter, Histogram } from "prom-client";

export const registry = new Registry();

export const toolCallsTotal = new Counter({
  name: "printer_mcp_tool_calls_total",
  help: "Total MCP tool calls",
  labelNames: ["tool", "outcome"] as const,
  registers: [registry],
});

export const toolCallDuration = new Histogram({
  name: "printer_mcp_tool_call_duration_seconds",
  help: "MCP tool call duration",
  labelNames: ["tool"] as const,
  registers: [registry],
});
