import { createBashTool, createLocalBashOperations } from "@earendil-works/pi-coding-agent";
import { fileURLToPath } from "node:url";

const shellPath = fileURLToPath(new URL("./pi-pidns-bash", import.meta.url));

export default function (pi) {
  const operations = createLocalBashOperations({ shellPath });

  // Replace the model-facing bash tool and route both `!` and `!!` commands
  // through the same shell backend. Pi's user_bash event does not fall back to
  // its local shell when a handler returns operations.
  pi.registerTool(createBashTool(process.cwd(), { operations }));
  pi.on("user_bash", () => ({ operations }));
}
