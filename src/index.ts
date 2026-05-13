#!/usr/bin/env node
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import {
  CallToolRequestSchema,
  ErrorCode,
  ListToolsRequestSchema,
  McpError,
} from "@modelcontextprotocol/sdk/types.js";
import { startHttpServer } from "./http-server.js";
import { toolCallsTotal, toolCallDuration } from "./metrics.js";
import { TOOLS } from "./tools.js";

const VERSION = "0.1.0";
const SERVER_NAME = "printer-mcp";

const PORT = parseInt(process.env.PORT ?? "8080", 10);

function safeJson(data: unknown): string {
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return JSON.stringify({ error: "Failed to serialise response" });
  }
}

function toolError(message: string) {
  return {
    content: [{ type: "text", text: safeJson({ error: message }) }],
    isError: true,
  };
}

// Per-session Server factory.
function createMcpServer(): Server {
  const server = new Server(
    {
      name: SERVER_NAME,
      version: VERSION,
    },
    {
      capabilities: {
        tools: {},
      },
    },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: TOOLS,
  }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const toolName = request.params.name;
    // TODO: use args for tool logic once tools are implemented
    const args = (request.params.arguments ?? {}) as Record<string, unknown>;
    const endTimer = toolCallDuration.startTimer({ tool: toolName });

    const finish = (outcome: "ok" | "error") => {
      endTimer();
      toolCallsTotal.inc({ tool: toolName, outcome });
    };

    try {
      let result: { content: Array<{ type: string; text: string }>; isError?: boolean };

      switch (toolName) {
        case "print_latex": {
          result = {
            content: [{ type: "text", text: "not implemented yet" }],
            isError: true,
          };
          break;
        }

        case "watch_page": {
          result = {
            content: [{ type: "text", text: "not implemented yet" }],
            isError: true,
          };
          break;
        }

        default:
          throw new McpError(
            ErrorCode.MethodNotFound,
            `Unknown tool: ${toolName}`,
          );
      }

      finish("ok");
      return result;
    } catch (err) {
      if (err instanceof McpError) {
        finish("error");
        throw err;
      }
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[ERROR] Tool ${toolName} failed:`, msg);
      finish("error");
      return toolError(msg);
    }
  });

  server.onerror = (error) => console.error("[MCP Error]", error);

  return server;
}

process.on("SIGINT", () => {
  process.exit(0);
});

startHttpServer(createMcpServer, PORT, { name: SERVER_NAME, version: VERSION });

console.error(`[INIT] ${SERVER_NAME} v${VERSION} listening on port ${PORT}`);
