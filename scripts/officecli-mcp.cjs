#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { InvalidParamsError, runProtocol } = require("./officecli-mcp/jsonrpc.cjs");
const { PathPolicyError, isLinklike, linkedAncestor } = require("./officecli-mcp/paths.cjs");
const { PolicyError, TOOL, parseToolArguments } = require("./officecli-mcp/policy.cjs");
const { runTool } = require("./officecli-mcp/runner.cjs");
const { CoreRunAuthorityError, MUTABLE_COMMANDS, authorizeMutation } = require("./officecli-mcp/authority.cjs");

const root = path.resolve(__dirname, "..");
const lockPath = path.join(root, "vendor", "officecli.lock.json");
const PROJECT = "iOfficeAI/OfficeCLI";
const VERSION = "1.0.135";
const SOURCE_COMMIT = "d2d9c60f44537004c3e1f46680c24ea38d9659c2";

class RuntimeIntegrityError extends Error {}

function normalizedArch() {
  if (process.arch === "x64") return "x64";
  if (process.arch === "arm64") return "arm64";
  throw new Error(`Unsupported OfficeCLI CPU architecture: ${process.arch}`);
}

function linuxUsesMusl() {
  if (fs.existsSync("/etc/alpine-release")) return true;
  const report = process.report?.getReport?.();
  return Boolean(report?.header) && !report.header.glibcVersionRuntime;
}

function assetKey() {
  const arch = normalizedArch();
  if (process.platform === "win32") return `windows-${arch}`;
  if (process.platform === "darwin") return `macos-${arch}`;
  if (process.platform === "linux") {
    return `${linuxUsesMusl() ? "linux-alpine" : "linux"}-${arch}`;
  }
  throw new Error(`Unsupported OfficeCLI operating system: ${process.platform}`);
}

function loadLock() {
  const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  if (lock.project !== PROJECT || lock.version !== VERSION || lock.sourceCommit !== SOURCE_COMMIT) {
    throw new RuntimeIntegrityError("managed lock identity mismatch");
  }
  return lock;
}

function pluginDataRoot() {
  const configured = process.env.PLUGIN_DATA;
  if (!configured) throw new RuntimeIntegrityError("OfficeCLI requires the plugin-owned PLUGIN_DATA value");
  return path.resolve(configured);
}

function managedBinary(lock) {
  const asset = lock.assets[assetKey()];
  if (!asset || typeof asset.filename !== "string" || !/^[0-9a-f]{64}$/.test(asset.sha256)) {
    throw new RuntimeIntegrityError(`No valid locked OfficeCLI asset for ${assetKey()}.`);
  }
  return {
    asset,
    binary: path.join(
      pluginDataRoot(),
      "runtimes",
      "officecli",
      VERSION,
      asset.filename,
    ),
  };
}

function linklike(filename) {
  return isLinklike(filename, fs.lstatSync(filename));
}

function contained(rootPath, candidate) {
  const relative = path.relative(rootPath, candidate);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

function verifyManagedRuntime() {
  const lock = loadLock();
  const { asset, binary } = managedBinary(lock);
  const dataRoot = pluginDataRoot();
  if (linkedAncestor(dataRoot)) {
    throw new RuntimeIntegrityError("managed runtime is missing, linked, or invalid");
  }
  const runtimesRoot = path.join(dataRoot, "runtimes");
  const runtimeRoot = path.dirname(path.dirname(binary));
  const versionRoot = path.dirname(binary);
  for (const candidate of [dataRoot, runtimesRoot, runtimeRoot, versionRoot]) {
    if (!fs.existsSync(candidate) || linklike(candidate) || !fs.lstatSync(candidate).isDirectory()) {
      throw new RuntimeIntegrityError("managed runtime is missing, linked, or invalid");
    }
  }
  if (!fs.existsSync(binary) || linklike(binary)) {
    throw new RuntimeIntegrityError("managed runtime is missing, linked, or invalid");
  }
  const status = fs.lstatSync(binary);
  if (!status.isFile()) throw new RuntimeIntegrityError("managed runtime is not an ordinary file");
  if (status.nlink > 1) throw new RuntimeIntegrityError("managed runtime must not be hard linked");
  const realRoot = fs.realpathSync.native(dataRoot);
  const realBinary = fs.realpathSync.native(binary);
  if (!contained(realRoot, realBinary)) throw new RuntimeIntegrityError("managed runtime canonical path escapes its root");
  const actual = crypto.createHash("sha256").update(fs.readFileSync(binary)).digest("hex");
  if (actual !== asset.sha256) {
    throw new RuntimeIntegrityError("managed runtime checksum mismatch");
  }
  return binary;
}

function validArgumentShape(argumentsValue) {
  return argumentsValue !== null &&
    typeof argumentsValue === "object" &&
    !Array.isArray(argumentsValue) &&
    Object.keys(argumentsValue).length === 1 &&
    Array.isArray(argumentsValue.command) &&
    argumentsValue.command.length >= 1 &&
    argumentsValue.command.length <= 128 &&
    argumentsValue.command.every(
      (item) => typeof item === "string",
    );
}

function toolFailure(message) {
  return { content: [{ type: "text", text: message }], isError: true };
}

function start(dependencies = {}) {
  const verifyRuntime = dependencies.verifyRuntime || verifyManagedRuntime;
  const execute = dependencies.execute || runTool;
  let sessionPoisoned = false;
  verifyRuntime();
  runProtocol({
    serverInfo: { name: "office-os-officecli", version: VERSION },
    tool: TOOL,
    callTool: async (argumentsValue) => {
      if (sessionPoisoned) return toolFailure("OfficeCLI MCP session is poisoned after unconfirmed process-tree termination.");
      if (!validArgumentShape(argumentsValue)) throw new InvalidParamsError("command must be an array of 1-128 strings");
      try {
        const parsed = parseToolArguments(argumentsValue);
        const authority = MUTABLE_COMMANDS.has(parsed.argv[0]) ? authorizeMutation(parsed.argv[1]) : null;
        const binary = verifyRuntime();
        return await execute(binary, parsed, {
          ...(authority ? { authority } : {}),
          onTerminationUnconfirmed: () => { sessionPoisoned = true; },
        });
      } catch (error) {
        if (error instanceof PolicyError || error instanceof PathPolicyError || error instanceof RuntimeIntegrityError || error instanceof CoreRunAuthorityError) {
          return toolFailure(error.message);
        }
        throw error;
      }
    },
  });
}

function failStartup(error) {
  process.stderr.write(`Office OS OfficeCLI MCP: ${error.message}\n`);
  process.stderr.write(`Run: python "${path.join(root, "scripts", "officecli_manager.py")}" install --accept-download\n`);
  process.exitCode = 2;
}

if (require.main === module) {
  try {
    start();
  } catch (error) {
    failStartup(error);
  }
}

module.exports = { RuntimeIntegrityError, start, verifyManagedRuntime };
