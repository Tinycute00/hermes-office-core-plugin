"use strict";

const { resolveCandidatePath } = require("./paths.cjs");

const TOOL = {
  name: "officecli",
  description: "Run one bounded OfficeCLI command against a managed candidate file.",
  inputSchema: {
    type: "object",
    properties: {
      command: {
        type: "array",
        items: { type: "string" },
        minItems: 1,
        maxItems: 128,
      },
    },
    required: ["command"],
    additionalProperties: false,
  },
};
const FILE_PROPERTIES = new Set(["src", "file", "image", "template", "ole", "video", "audio"]);
const OUTPUT_PROPERTIES = new Set(["out", "output", "dest", "destination", "export", "path", "target"]);
const ISSUE_TYPES = new Set(["all", "overflow", "accessibility", "formula", "layout"]);
const POSITION_FLAGS = ["--index", "--after", "--before"];

class PolicyError extends Error {}

function bytes(value, maximum, label) {
  if (Buffer.byteLength(value, "utf8") > maximum) throw new PolicyError(`${label} exceeds ${maximum} bytes.`);
}

function integer(value, minimum, maximum, label) {
  if (!/^(0|[1-9]\d*)$/.test(value)) throw new PolicyError(`${label} must be an integer.`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) throw new PolicyError(`${label} is out of range.`);
}

function text(value, maximum = 4096, label = "Token") {
  bytes(value, maximum, label);
  if (value.includes("\0") || value === "--" || value.startsWith("@")) throw new PolicyError(`${label} contains a forbidden token.`);
}

function fields(value) {
  text(value, 1024, "Fields");
  if (!/^[A-Za-z][A-Za-z0-9_.-]*(,[A-Za-z][A-Za-z0-9_.-]*)*$/.test(value)) throw new PolicyError("Fields must be a safe CSV list.");
}

function columnNumber(column) {
  let value = 0;
  for (const character of column) value = value * 26 + character.charCodeAt(0) - 64;
  return value;
}

function columns(value) {
  text(value, 1024, "Columns");
  const items = value.split(",");
  if (!items.every((item) => /^[A-Z]{1,3}$/.test(item) && columnNumber(item) <= 16384)) throw new PolicyError("Columns must be A..XFD CSV.");
}

function parseOptions(argv, start, rules) {
  const seen = new Map();
  for (let index = start; index < argv.length; index += 1) {
    const flag = argv[index];
    const rule = rules[flag];
    if (!rule) throw new PolicyError(`Option is not allowed: ${flag}`);
    if (seen.has(flag) && !rule.repeat) throw new PolicyError(`Option may appear once: ${flag}`);
    if (rule.boolean) {
      seen.set(flag, true);
      continue;
    }
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) throw new PolicyError(`Option requires a value: ${flag}`);
    text(value, rule.maximum || 4096, flag);
    const normalized = rule.check ? rule.check(value) : undefined;
    if (typeof normalized === "string") argv[index + 1] = normalized;
    const values = seen.get(flag) || [];
    seen.set(flag, [...values, argv[index + 1]]);
    index += 1;
  }
  return seen;
}

function property(value, bare = false) {
  text(value, 4096, "Property");
  const separator = value.indexOf("=");
  if (bare) {
    if (separator !== -1 || !/^[A-Za-z][\w.-]*$/.test(value)) throw new PolicyError("Remove properties must be bare keys.");
    return value;
  }
  if (separator <= 0) throw new PolicyError("Property must be key=value.");
  const key = value.slice(0, separator).toLowerCase();
  const propertyValue = value.slice(separator + 1);
  if (!/^[A-Za-z][\w.-]*$/.test(key) || OUTPUT_PROPERTIES.has(key)) throw new PolicyError("Output-like property is forbidden.");
  if (FILE_PROPERTIES.has(key)) return `${key}=${resolveCandidatePath(propertyValue)}`;
  return value;
}

function validateCommon(command) {
  if (
    !Array.isArray(command) ||
    command.length < 1 ||
    command.length > 128 ||
    !command.every(
      (item) => typeof item === "string",
    )
  ) {
    throw new PolicyError("command must be an array of 1-128 strings.");
  }
  command.forEach((token) => text(token));
  const jsonIndexes = command.flatMap((token, index) => token === "--json" ? [index] : []);
  if (jsonIndexes.length > 1 || (jsonIndexes.length === 1 && jsonIndexes[0] !== command.length - 1)) throw new PolicyError("--json is allowed once at the end.");
  return { argv: jsonIndexes.length ? command.slice(0, -1) : [...command], json: jsonIndexes.length === 1 };
}

function baseFile(argv, position = 1) {
  if (!argv[position]) throw new PolicyError("Candidate file is required.");
  argv[position] = resolveCandidatePath(argv[position]);
}

function positionCount(seen) {
  return POSITION_FLAGS.filter((flag) => seen.has(flag)).length;
}

function parseView(argv) {
  if (argv.length < 3) throw new PolicyError("View mode is required.");
  const mode = argv[2];
  if (["text", "annotated", "outline"].includes(mode)) {
    parseOptions(argv, 3, {
      "--start": { check: (v) => integer(v, 1, 1000000, "start") },
      "--end": { check: (v) => integer(v, 1, 1000000, "end") },
      "--max-lines": { check: (v) => integer(v, 1, 500, "max-lines") },
      "--cols": { maximum: 1024, check: columns }, "--range": {},
    });
    return false;
  }
  if (mode === "stats") {
    if (argv.length !== 3) throw new PolicyError("Stats takes no options.");
    return false;
  }
  if (mode === "issues") {
    parseOptions(argv, 3, {
      "--type": { maximum: 1024, check: (v) => { if (!ISSUE_TYPES.has(v)) throw new PolicyError("Unknown issue subtype."); } },
      "--limit": { check: (v) => integer(v, 1, 500, "limit") },
    });
    return false;
  }
  if (mode === "screenshot") {
    parseOptions(argv, 3, {
      "--page": { check: (v) => integer(v, 1, 1000000, "page") }, "--range": {},
      "--screenshot-width": { check: (v) => integer(v, 320, 4096, "width") },
      "--screenshot-height": { check: (v) => integer(v, 240, 4096, "height") },
    });
    return true;
  }
  throw new PolicyError("View mode is forbidden.");
}

function parseMutation(argv) {
  const verb = argv[0];
  if (argv.length < 3) throw new PolicyError(`${verb} requires a DOM path.`);
  text(argv[2]);
  if (verb === "set") {
    const seen = parseOptions(argv, 3, { "--prop": { repeat: true, check: property }, "--find": {}, "--replace": {} });
    if (!seen.has("--prop") || seen.has("--find") !== seen.has("--replace")) throw new PolicyError("set requires properties and paired find/replace.");
  } else if (verb === "add") {
    const seen = parseOptions(argv, 3, {
      "--type": { maximum: 1024 }, "--from": {}, "--index": { check: (v) => integer(v, 0, 1000000, "index") },
      "--after": {}, "--before": {}, "--prop": { repeat: true, check: property },
    });
    if (seen.has("--type") === seen.has("--from") || positionCount(seen) > 1) throw new PolicyError("add requires one source and at most one position.");
  } else if (verb === "remove") {
    parseOptions(argv, 3, { "--shift": { check: (v) => { if (!["left", "up"].includes(v)) throw new PolicyError("Invalid shift."); } }, "--prop": { repeat: true, check: (v) => property(v, true) } });
  } else {
    const seen = parseOptions(argv, 3, {
      "--to": {}, "--index": { check: (v) => integer(v, 0, 1000000, "index") },
      "--after": {}, "--before": {}, "--prop": { repeat: true, check: property },
    });
    if (positionCount(seen) > 1) throw new PolicyError("move accepts at most one position.");
  }
}

function parseToolArguments(argumentsValue) {
  if (!argumentsValue || typeof argumentsValue !== "object" || Array.isArray(argumentsValue) || Object.keys(argumentsValue).length !== 1 || !("command" in argumentsValue)) {
    throw new PolicyError("Arguments must contain only command.");
  }
  const { argv, json } = validateCommon(argumentsValue.command);
  const verb = argv[0];
  if (!["validate", "get", "query", "view", "set", "add", "remove", "move", "swap"].includes(verb)) throw new PolicyError("Command family is forbidden.");
  baseFile(argv);
  let screenshot = false;
  if (verb === "validate") {
    if (argv.length !== 2) throw new PolicyError("validate accepts only a file.");
  } else if (verb === "get") {
    if (argv.length < 3 || argv[2] === "selected") throw new PolicyError("get requires a safe DOM path.");
    text(argv[2]);
    parseOptions(argv, 3, { "--depth": { check: (v) => integer(v, 0, 8, "depth") } });
  } else if (verb === "query") {
    if (argv.length < 3) throw new PolicyError("query requires a selector.");
    text(argv[2]);
    parseOptions(argv, 3, { "--find": {}, "--compact": { boolean: true }, "--fields": { maximum: 1024, check: fields } });
  } else if (verb === "view") screenshot = parseView(argv);
  else if (["set", "add", "remove", "move"].includes(verb)) parseMutation(argv);
  else {
    if (argv.length !== 4) throw new PolicyError("swap requires two DOM paths.");
    text(argv[2]); text(argv[3]);
  }
  if (json) argv.push("--json");
  return { argv, screenshot };
}

module.exports = { PolicyError, TOOL, parseToolArguments };
