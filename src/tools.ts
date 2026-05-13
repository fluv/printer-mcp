export const TOOLS = [
  {
    name: "print_latex",
    description: "Submit a LaTeX document for printing to the Brother HL-L2865DW",
    inputSchema: {
      type: "object" as const,
      properties: {
        source: {
          type: "string",
          description: "LaTeX source code",
        },
        copies: {
          type: "number",
          description: "Number of copies (default: 1)",
        },
      },
      required: ["source"],
    },
  },
  {
    name: "watch_page",
    description: "Poll the status of a print job",
    inputSchema: {
      type: "object" as const,
      properties: {
        job_id: {
          type: "string",
          description: "Job ID returned by print_latex",
        },
      },
      required: ["job_id"],
    },
  },
];
