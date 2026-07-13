"use strict";

const MAX_LINE_BYTES = 1024 * 1024;
const hasOwn = (value, key) => Object.prototype.hasOwnProperty.call(value, key);

class InvalidParamsError extends Error {
  constructor(message) {
    super(message);
    this.rpcCode = -32602;
  }
}

function errorResponse(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function successResponse(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function writeMessage(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

async function dispatch(message, state, adapter) {
  const record = isRecord(message);
  const idPresent = record && hasOwn(message, "id");
  const validId = !idPresent || message.id === null || typeof message.id === "string" || (typeof message.id === "number" && Number.isFinite(message.id));
  if (!record || message.jsonrpc !== "2.0" || typeof message.method !== "string" || !validId) {
    return errorResponse(idPresent && validId ? message.id : null, -32600, "Invalid Request");
  }
  const notification = !idPresent;
  if (notification) {
    if (message.method === "notifications/initialized" && state.initialized) {
      state.clientReady = true;
    }
    return null;
  }
  const id = message.id;
  if (message.method === "initialize") {
    if (state.initialized || (hasOwn(message, "params") && !isRecord(message.params))) {
      return errorResponse(id, -32600, "Invalid Request");
    }
    state.initialized = true;
    return successResponse(id, {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: adapter.serverInfo || { name: "office-os-officecli", version: "1.0.0" },
    });
  }
  if (!state.initialized) {
    return errorResponse(id, -32600, "Initialize request required");
  }
  if (message.method === "ping") return successResponse(id, {});
  if (message.method === "tools/list") return successResponse(id, { tools: [adapter.tool] });
  if (message.method !== "tools/call") {
    return errorResponse(id, -32601, "Method not found");
  }
  const params = message.params;
  if (
    !isRecord(params) ||
    params.name !== adapter.tool.name ||
    !isRecord(params.arguments)
  ) {
    return errorResponse(id, -32602, "Invalid params");
  }
  try {
    return successResponse(id, await adapter.callTool(params.arguments));
  } catch (error) {
    if (error instanceof InvalidParamsError || error?.rpcCode === -32602) {
      return errorResponse(id, -32602, error.message || "Invalid params");
    }
    const messageText = error instanceof Error ? error.message : "Internal error";
    process.stderr.write(`OfficeCLI adapter error: ${messageText}\n`);
    return errorResponse(id, -32603, "Internal error");
  }
}

async function handleLine(line, state, adapter) {
  let message;
  try {
    message = JSON.parse(line.toString("utf8"));
  } catch (error) {
    writeMessage(errorResponse(null, -32700, "Parse error"));
    return;
  }
  const response = await dispatch(message, state, adapter);
  if (response !== null) writeMessage(response);
}

async function consumeInput(adapter) {
  const state = { initialized: false, clientReady: false };
  let buffered = Buffer.alloc(0);
  let draining = false;
  for await (const chunk of process.stdin) {
    let offset = 0;
    while (offset < chunk.length) {
      const newline = chunk.indexOf(0x0a, offset);
      if (draining) {
        if (newline === -1) break;
        draining = false;
        offset = newline + 1;
        continue;
      }
      if (newline === -1) {
        const remainder = chunk.subarray(offset);
        if (buffered.length + remainder.length > MAX_LINE_BYTES) {
          writeMessage(errorResponse(null, -32700, "Parse error"));
          buffered = Buffer.alloc(0);
          draining = true;
        } else {
          buffered = Buffer.concat([buffered, remainder]);
        }
        break;
      }
      const part = chunk.subarray(offset, newline);
      if (buffered.length + part.length > MAX_LINE_BYTES) {
        writeMessage(errorResponse(null, -32700, "Parse error"));
      } else {
        let line = Buffer.concat([buffered, part]);
        if (line.length > 0 && line[line.length - 1] === 0x0d) line = line.subarray(0, -1);
        if (line.length > 0) await handleLine(line, state, adapter);
      }
      buffered = Buffer.alloc(0);
      offset = newline + 1;
    }
  }
  if (!draining && buffered.length > 0) await handleLine(buffered, state, adapter);
}

function runProtocol(adapter) {
  consumeInput(adapter).catch((error) => {
    process.stderr.write(`OfficeCLI adapter fatal error: ${error.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = {
  InvalidParamsError,
  MAX_LINE_BYTES,
  runProtocol,
};
