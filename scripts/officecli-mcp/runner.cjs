"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { candidateRoot } = require("./paths.cjs");

const CONSTANTS = Object.freeze({
  normalTimeoutMs: 60_000,
  screenshotTimeoutMs: 120_000,
  streamLimitBytes: 8 * 1024 * 1024,
  pngLimitBytes: 16 * 1024 * 1024,
});
const MANAGED_ENV = Object.freeze({
  OFFICECLI_SKIP_UPDATE: "1",
  OFFICECLI_NO_AUTO_INSTALL: "1",
  OFFICECLI_NO_AUTO_RESIDENT: "1",
});
const PNG_SIGNATURE = Buffer.from("89504e470d0a1a0a", "hex");

class RunnerError extends Error {
  constructor(message, stdout = Buffer.alloc(0), stderr = Buffer.alloc(0)) {
    super(message);
    this.stdout = stdout;
    this.stderr = stderr;
  }
}

function childEnvironment() {
  const environment = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (!key.startsWith("OFFICECLI_") && value !== undefined) environment[key] = value;
  }
  return { ...environment, ...MANAGED_ENV };
}

function killTree(child) {
  if (!child.pid) return Promise.resolve();
  if (process.platform === "win32") {
    return new Promise((resolve) => {
      const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "ignore",
        windowsHide: true,
        shell: false,
      });
      killer.on("error", resolve);
      killer.on("close", resolve);
    });
  }
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch (error) {
    if (error.code !== "ESRCH") child.kill("SIGKILL");
  }
  return Promise.resolve();
}

function appendBounded(chunks, chunk, current, limit) {
  const remaining = Math.max(0, limit - current);
  if (remaining > 0) chunks.push(chunk.subarray(0, remaining));
  return current + chunk.length;
}

function runProcess(binary, argv, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, argv, {
      env: childEnvironment(),
      stdio: ["ignore", "pipe", "pipe"],
      shell: false,
      windowsHide: true,
      detached: process.platform !== "win32",
    });
    const stdoutChunks = [];
    const stderrChunks = [];
    let stdoutSize = 0;
    let stderrSize = 0;
    let failure = null;
    let killPromise = Promise.resolve();
    const stop = (reason) => {
      if (failure !== null) return;
      failure = reason;
      killPromise = killTree(child);
    };
    const timer = setTimeout(() => stop("OfficeCLI command timed out."), timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdoutSize = appendBounded(stdoutChunks, chunk, stdoutSize, CONSTANTS.streamLimitBytes);
      if (stdoutSize > CONSTANTS.streamLimitBytes) stop("OfficeCLI stdout exceeded 8 MiB.");
    });
    child.stderr.on("data", (chunk) => {
      stderrSize = appendBounded(stderrChunks, chunk, stderrSize, CONSTANTS.streamLimitBytes);
      if (stderrSize > CONSTANTS.streamLimitBytes) stop("OfficeCLI stderr exceeded 8 MiB.");
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(new RunnerError(`OfficeCLI could not start: ${error.message}`));
    });
    child.on("close", async (code) => {
      clearTimeout(timer);
      await killPromise;
      const stdout = Buffer.concat(stdoutChunks);
      const stderr = Buffer.concat(stderrChunks);
      if (failure !== null) {
        reject(new RunnerError(failure, stdout, stderr));
      } else {
        resolve({ code: code === null ? 1 : code, stdout, stderr });
      }
    });
  });
}

function diagnosticText(error) {
  const parts = [error.message];
  for (const value of [error.stdout, error.stderr]) {
    if (Buffer.isBuffer(value) && value.length > 0) parts.push(value.subarray(0, 4096).toString("utf8"));
  }
  return parts.join("\n").slice(0, 16_384);
}

function commandArguments(argv, output) {
  const command = [...argv];
  const json = command.at(-1) === "--json" ? command.pop() : null;
  command.push("--out", output, "--render", "html");
  if (json) command.push(json);
  return command;
}

function assertPng(output) {
  const status = fs.lstatSync(output);
  if (!status.isFile() || status.isSymbolicLink()) throw new RunnerError("Screenshot output is not an ordinary file.");
  if (status.size > CONSTANTS.pngLimitBytes) throw new RunnerError("Screenshot exceeds 16 MiB.");
  const data = fs.readFileSync(output);
  if (data.length < PNG_SIGNATURE.length || !data.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE)) {
    throw new RunnerError("Screenshot output is not a PNG.");
  }
  return data;
}

async function runScreenshot(binary, parsed, options) {
  const root = candidateRoot();
  const rootStatus = fs.lstatSync(root);
  if (!rootStatus.isDirectory() || rootStatus.isSymbolicLink()) throw new RunnerError("Candidate root is linked or invalid.");
  const temporary = fs.mkdtempSync(path.join(root, ".officecli-shot-"));
  const output = path.join(temporary, "render.png");
  try {
    const completed = await runProcess(binary, commandArguments(parsed.argv, output), options.timeoutMs || CONSTANTS.screenshotTimeoutMs);
    if (completed.code !== 0) throw new RunnerError(`OfficeCLI exited with code ${completed.code}.`, completed.stdout, completed.stderr);
    const data = assertPng(output);
    return { content: [{ type: "image", data: data.toString("base64"), mimeType: "image/png" }] };
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

async function runTool(binary, parsed, options = {}) {
  try {
    if (parsed.screenshot) return await runScreenshot(binary, parsed, options);
    const completed = await runProcess(binary, parsed.argv, options.timeoutMs || CONSTANTS.normalTimeoutMs);
    const text = [completed.stdout, completed.stderr].filter((value) => value.length > 0).map((value) => value.toString("utf8")).join("\n").trim();
    return { content: [{ type: "text", text: text || `OfficeCLI exited with code ${completed.code}.` }], isError: completed.code !== 0 };
  } catch (error) {
    if (error instanceof RunnerError) return { content: [{ type: "text", text: diagnosticText(error) }], isError: true };
    throw error;
  }
}

module.exports = { CONSTANTS, RunnerError, childEnvironment, runProcess, runTool };
