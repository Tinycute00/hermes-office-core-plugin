"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const REPARSE_PROBE_TIMEOUT_MS = 2_000;
const MAX_REPARSE_PATHS_PER_BATCH = 64;
const ERROR_NOT_A_REPARSE_POINT = 4390;

class PathPolicyError extends Error {}

function dataRoot() {
  const data = process.env.PLUGIN_DATA;
  if (!data) throw new PathPolicyError("OfficeCLI requires the plugin-owned PLUGIN_DATA value.");
  return path.resolve(data);
}

function candidateRoot() {
  return path.join(dataRoot(), "officecli-candidates");
}

function isContained(root, target) {
  const relative = path.relative(root, target);
  return relative !== "" && !relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative);
}

function canonicalExistingPath(target) {
  return fs.realpathSync.native(path.resolve(target));
}

function lstatOrNull(target) {
  try {
    return fs.lstatSync(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function fsutilExecutable() {
  const systemRoot = process.env.SystemRoot || process.env.WINDIR;
  return systemRoot ? path.join(systemRoot, "System32", "fsutil.exe") : "fsutil.exe";
}

function inspectWindowsReparseBatch(targets, execute = spawnSync) {
  const reparsePoints = new Set();
  for (const target of targets) {
    const result = execute(fsutilExecutable(), ["reparsepoint", "query", target], {
      encoding: "utf8",
      shell: false,
      timeout: REPARSE_PROBE_TIMEOUT_MS,
      windowsHide: true,
    });
    const output = `${result.stdout || ""}\n${result.stderr || ""}`;
    if (!result.error && result.status === 0) {
      reparsePoints.add(target);
      continue;
    }
    if (!result.error && result.status === 1 && new RegExp(`\\b${ERROR_NOT_A_REPARSE_POINT}\\b`).test(output)) continue;
    throw new PathPolicyError("Could not inspect Windows reparse-point attributes.");
  }
  return reparsePoints;
}

function windowsReparsePoints(targets, inspectBatch = inspectWindowsReparseBatch) {
  if (process.platform !== "win32" || targets.length === 0) return new Set();
  const values = targets.map((target) => path.resolve(target));
  const reparsePoints = new Set();
  for (let index = 0; index < values.length; index += MAX_REPARSE_PATHS_PER_BATCH) {
    for (const target of inspectBatch(values.slice(index, index + MAX_REPARSE_PATHS_PER_BATCH))) {
      reparsePoints.add(target);
    }
  }
  return reparsePoints;
}

function isLinklike(target, status = lstatOrNull(target), reparsePoints = null) {
  if (!status) return false;
  if (status.isSymbolicLink()) return true;
  const inspected = reparsePoints || windowsReparsePoints([target]);
  return inspected.has(path.resolve(target));
}

function rejectLink(target, status = lstatOrNull(target), reparsePoints = null) {
  if (isLinklike(target, status, reparsePoints)) throw new PathPolicyError("Candidate paths must not contain links or reparse points.");
  return status;
}

function linkedAncestor(target) {
  const absolute = path.resolve(target);
  const filesystemRoot = path.parse(absolute).root;
  let cursor = filesystemRoot;
  const entries = [];
  const rootStatus = lstatOrNull(filesystemRoot);
  if (rootStatus) entries.push([filesystemRoot, rootStatus]);
  for (const part of path.relative(filesystemRoot, absolute).split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    const status = lstatOrNull(cursor);
    if (!status) break;
    entries.push([cursor, status]);
  }
  const reparsePoints = windowsReparsePoints(entries.map(([item]) => item));
  for (const [item, status] of entries) {
    if (isLinklike(item, status, reparsePoints)) return item;
  }
  return null;
}

function validateExistingComponents(root, existing) {
  const entries = [[root, lstatOrNull(root)]];
  const relative = path.relative(root, existing);
  let cursor = root;
  for (const part of relative.split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    entries.push([cursor, lstatOrNull(cursor)]);
  }
  const existingEntries = entries.filter(([_item, status]) => status);
  const reparsePoints = windowsReparsePoints(existingEntries.map(([item]) => item));
  for (const [item, status] of existingEntries) {
    rejectLink(item, status, reparsePoints);
  }
  return new Map(existingEntries);
}

function resolveCandidatePath(input, options = {}) {
  if (typeof input !== "string" || input.includes("\0")) throw new PathPolicyError("Candidate path must be a string without NUL.");
  if (/^(\\\\[?.]\\|\\\\)/.test(input) || /^[A-Za-z]:[^\\/]/.test(input)) {
    throw new PathPolicyError("UNC, device, and drive-relative paths are forbidden.");
  }
  const data = dataRoot();
  if (linkedAncestor(data)) throw new PathPolicyError("Plugin data ancestors must not contain links or reparse points.");
  const dataStatus = lstatOrNull(data);
  if (!dataStatus || !dataStatus.isDirectory()) throw new PathPolicyError("Plugin data root is missing, linked, or invalid.");
  const root = candidateRoot();
  const rootStatus = rejectLink(root);
  if (!rootStatus || !rootStatus.isDirectory()) throw new PathPolicyError("Candidate root is missing or invalid.");
  let lexical = path.resolve(root, input);
  let containmentRoot = root;
  let existing = lexical;
  let status = lstatOrNull(existing);
  if (!isContained(containmentRoot, lexical)) {
    // A Core state may legitimately carry a Windows 8.3 spelling while the
    // hook-injected root uses the long spelling.  Only accept that case after
    // the actual existing path is independently link-checked and proves to be
    // physically contained in the managed root.
    if (!status || linkedAncestor(lexical)) {
      throw new PathPolicyError("Path escapes the managed candidate root.");
    }
    const canonicalRoot = canonicalExistingPath(root);
    const canonicalCandidate = canonicalExistingPath(lexical);
    if (!isContained(canonicalRoot, canonicalCandidate)) {
      throw new PathPolicyError("Path escapes the managed candidate root.");
    }
    containmentRoot = canonicalRoot;
    lexical = canonicalCandidate;
    existing = lexical;
    status = lstatOrNull(existing);
  }
  while (!status && isContained(containmentRoot, existing)) {
    existing = path.dirname(existing);
    status = lstatOrNull(existing);
  }
  if (!status) throw new PathPolicyError("Candidate path has no contained existing parent.");
  const componentStatuses = validateExistingComponents(containmentRoot, existing);
  const realData = canonicalExistingPath(data);
  const realRoot = canonicalExistingPath(root);
  if (!isContained(realData, realRoot)) throw new PathPolicyError("Candidate root escapes plugin data.");
  const realExisting = fs.realpathSync.native(existing);
  if (realExisting !== realRoot && !isContained(realRoot, realExisting)) {
    throw new PathPolicyError("Canonical path escapes the managed candidate root.");
  }
  if (options.mustExist !== false) {
    const finalStatus = componentStatuses.get(lexical);
    if (!finalStatus || !finalStatus.isFile()) throw new PathPolicyError("Candidate file is missing or not ordinary.");
    if (finalStatus.nlink > 1) throw new PathPolicyError("Candidate files must not be hard linked.");
  }
  return lexical;
}

module.exports = {
  PathPolicyError,
  candidateRoot,
  isContained,
  isLinklike,
  inspectWindowsReparseBatch,
  linkedAncestor,
  REPARSE_PROBE_TIMEOUT_MS,
  resolveCandidatePath,
  windowsReparsePoints,
};
