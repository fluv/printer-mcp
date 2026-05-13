import { it, expect } from "vitest";
import { TOOLS } from "./tools.js";

it("defines print_latex and watch_page tools", () => {
  expect(TOOLS).toHaveLength(2);
  expect(TOOLS[0].name).toBe("print_latex");
  expect(TOOLS[1].name).toBe("watch_page");
  expect(TOOLS[0].description).toBeDefined();
  expect(TOOLS[1].description).toBeDefined();
});
