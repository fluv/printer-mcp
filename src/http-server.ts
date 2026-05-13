import type { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import express, { type Request, type Response } from "express";
import cors from "cors";
import http from "node:http";
import { randomUUID } from "node:crypto";
import { registry } from "./metrics.js";

interface ServerInfo {
  name: string;
  version: string;
}

// The SDK's Server class can only be connected to one transport at a
// time. claude.ai opens a new MCP session per conversation, so each
// session needs its own Server instance. The caller passes a factory
// we invoke when a new session is created.
type ServerFactory = () => Server;

export function startHttpServer(
  createMcpServer: ServerFactory,
  port: number,
  info: ServerInfo,
): http.Server {
  const app = express();

  app.use(express.json({ limit: "10mb" }));
  app.use(
    cors({
      origin: "*",
      methods: ["GET", "POST", "OPTIONS"],
      allowedHeaders: [
        "Origin",
        "X-Requested-With",
        "Content-Type",
        "Accept",
        "Mcp-Session-Id",
        "Authorization",
      ],
      exposedHeaders: ["Mcp-Session-Id", "Content-Type"],
      optionsSuccessStatus: 200,
    }),
  );

  // /metrics is unauthenticated — acceptable while the service is cluster-internal
  // only. If this service is ever exposed via ingress, add access control here.
  app.get("/metrics", async (_req: Request, res: Response) => {
    try {
      const metrics = await registry.metrics();
      res.setHeader("Content-Type", registry.contentType);
      res.send(metrics);
    } catch (err) {
      res.status(500).send("Failed to collect metrics");
    }
  });

  // Dedicated healthz endpoint for k8s probes and meaningful curl checks.
  // The MCP endpoint at /mcp cannot serve a plain GET probe — it opens an
  // SSE stream — so health needs its own route.
  app.get("/healthz", (_req: Request, res: Response) => {
    res.json({ status: "ok", service: info.name, version: info.version });
  });

  app.get("/", (_req: Request, res: Response) => {
    res.json({
      status: "ok",
      service: info.name,
      version: info.version,
      endpoints: { mcp: "/mcp", health: "/healthz" },
    });
  });

  const transports: Record<string, StreamableHTTPServerTransport> = {};
  const servers: Record<string, Server> = {};

  // GET /mcp — SSE keepalive channel. With enableJsonResponse: true the SDK
  // returns JSON on POST and rarely uses this path; we keep it open for
  // protocol compliance and send pings to hold the connection.
  app.get("/mcp", (req: Request, res: Response) => {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;
    if (!sessionId) {
      res.status(400).json({ error: "Missing Mcp-Session-Id header" });
      return;
    }

    if (!transports[sessionId]) {
      res.status(404).json({ error: `Session not found: ${sessionId}` });
      return;
    }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("Mcp-Session-Id", sessionId);
    res.flushHeaders();

    const keepalive = setInterval(() => {
      if (!res.writableEnded) res.write(": ping\n\n");
    }, 30000);

    res.on("close", () => clearInterval(keepalive));
  });

  // POST /mcp — main request channel (streamable HTTP transport).
  app.post("/mcp", async (req: Request, res: Response) => {
    try {
      const clientSessionId = req.headers["mcp-session-id"] as string | undefined;
      let transport: StreamableHTTPServerTransport | undefined;

      const accept = req.headers.accept || "";
      if (!accept.includes("application/json") && !accept.includes("text/event-stream")) {
        res.status(406).json({
          jsonrpc: "2.0",
          error: {
            code: -32000,
            message: "Not Acceptable: must accept application/json or text/event-stream",
          },
          id: req.body?.id ?? null,
        });
        return;
      }

      if (clientSessionId) {
        transport = transports[clientSessionId];
        if (!transport) {
          const isInitialize = req.body?.method === "initialize";
          if (!isInitialize) {
            res.status(404).json({
              jsonrpc: "2.0",
              error: {
                code: -32001,
                message: `Session not found: ${clientSessionId}. Please re-initialize.`,
              },
              id: req.body?.id ?? null,
            });
            return;
          }
        }
      }

      if (!transport) {
        const newSessionId = randomUUID();

        transport = new StreamableHTTPServerTransport({
          sessionIdGenerator: () => newSessionId,
          onsessioninitialized: (sid: string) => {
            console.error(`[MCP] Session initialized: ${sid}`);
          },
          enableJsonResponse: true,
        });

        const mcpServer = createMcpServer();
        transports[newSessionId] = transport;
        servers[newSessionId] = mcpServer;

        transport.onclose = () => {
          const closedId = transport?.sessionId || newSessionId;
          console.error(`[MCP] Session closed: ${closedId}`);
          delete transports[closedId];
          const s = servers[closedId];
          delete servers[closedId];
          if (s) s.close().catch((err) => console.error("[MCP] server.close failed:", err));
        };

        await mcpServer.connect(transport);
      }

      if (transport.sessionId) {
        res.setHeader("Mcp-Session-Id", transport.sessionId);
      }

      await transport.handleRequest(req, res, req.body);
    } catch (error) {
      console.error("[ERROR] POST /mcp failed:", error);
      if (!res.headersSent) {
        res.status(500).json({
          jsonrpc: "2.0",
          error: {
            code: -32000,
            message: `Internal server error: ${error instanceof Error ? error.message : String(error)}`,
          },
          id: req.body?.id ?? null,
        });
      }
    }
  });

  const server = http.createServer(app);
  server.on("error", (error) => {
    console.error(`[ERROR] HTTP server error: ${error.message}`);
  });
  server.listen(port, () => {
    console.error(`[HTTP] Listening on port ${port} — /mcp, /healthz`);
  });
  return server;
}
