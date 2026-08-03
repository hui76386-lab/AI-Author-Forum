const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const workspace = path.resolve(__dirname, "..");
const configured = process.env.PYTHON;
const candidates = [
  configured,
  process.platform === "win32"
    ? path.join(workspace, ".venv", "Scripts", "python.exe")
    : path.join(workspace, ".venv", "bin", "python"),
  process.platform === "win32" ? "python" : "python3",
].filter(Boolean);

let lastError;
for (const command of candidates) {
  if (path.isAbsolute(command) && !fs.existsSync(command)) {
    continue;
  }
  const result = spawnSync(command, process.argv.slice(2), {
    cwd: workspace,
    stdio: "inherit",
  });
  if (!result.error) {
    process.exit(result.status ?? 1);
  }
  lastError = result.error;
  if (result.error.code !== "ENOENT") {
    break;
  }
}

console.error(`Unable to run the project Python interpreter: ${lastError || "not found"}`);
process.exit(1);
