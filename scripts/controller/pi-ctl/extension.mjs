import { fileURLToPath } from "node:url";
import { createController } from "./core.mjs";

const wrapperPath = fileURLToPath(new URL("../pi-pidns-bash", import.meta.url));

export default function piControllerExtension(pi) {
	return createController(pi, {
		cwd: process.cwd(),
		env: process.env,
		pid: process.pid,
		wrapperPath,
	});
}
