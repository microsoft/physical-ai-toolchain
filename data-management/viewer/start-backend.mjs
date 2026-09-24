import { spawn } from "node:child_process";

const backend = spawn(
  "uv",
  [
    "run",
    "--frozen",
    "--directory",
    "backend",
    "uvicorn",
    "src.api.main:app",
    "--reload",
    "--host",
    process.env.BACKEND_HOST ?? "127.0.0.1",
    "--port",
    process.env.BACKEND_PORT ?? "8000",
  ],
  {
    env: process.env,
    stdio: "inherit",
  },
);

let shutdownSignal;
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (!shutdownSignal) {
      shutdownSignal = signal;
      backend.kill(signal);
    }
  });
}

backend.on("error", (error) => {
  console.error(`Failed to start backend: ${error.message}`);
  process.exit(1);
});

backend.on("exit", (code, signal) => {
  const exitSignal = signal ?? shutdownSignal;
  const signalExitCode = exitSignal === "SIGINT" ? 130 : 143;
  process.exit(exitSignal ? signalExitCode : (code ?? 1));
});
