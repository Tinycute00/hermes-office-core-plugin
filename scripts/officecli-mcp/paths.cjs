"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const REPARSE_PATHS_ENV = "OFFICE_OS_REPARSE_PATHS";
const POWERSHELL_REPARSE_COMMAND = "$ErrorActionPreference='Stop';$items=(ConvertFrom-Json -InputObject $env:OFFICE_OS_REPARSE_PATHS).paths;$flags=@(foreach($itemPath in $items){$item=Get-Item -Force -LiteralPath $itemPath;[bool](($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)});[Console]::Out.Write((ConvertTo-Json -InputObject @($flags) -Compress))";

class PathPolicyError extends Error {}

function dataRoot() {
  const data = process.env.PLUGIN_DATA || process.env.CLAUDE_PLUGIN_DATA;
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

function lstatOrNull(target) {
  try {
    return fs.lstatSync(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function windowsReparsePoints(targets, execute = spawnSync) {
  if (process.platform !== "win32" || targets.length === 0) return new Set();
  const values = targets.map((target) => path.resolve(target));
  const result = execute("powershell.exe", ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", POWERSHELL_REPARSE_COMMAND], {
    encoding: "utf8",
    env: { ...process.env, [REPARSE_PATHS_ENV]: JSON.stringify({ paths: values }) },
    shell: false,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) throw new PathPolicyError("Could not inspect Windows reparse-point attributes.");
  let flags;
  try {
    flags = JSON.parse(result.stdout);
  } catch {
    throw new PathPolicyError("Could not inspect Windows reparse-point attributes.");
  }
  if (!Array.isArray(flags) || flags.length !== values.length || !flags.every((value) => typeof value === "boolean")) {
    throw new PathPolicyError("Could not inspect Windows reparse-point attributes.");
  }
  return new Set(values.filter((_target, index) => flags[index]));
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
  const lexical = path.resolve(root, input);
  if (!isContained(root, lexical)) throw new PathPolicyError("Path escapes the managed candidate root.");
  let existing = lexical;
  let status = lstatOrNull(existing);
  while (!status && isContained(root, existing)) {
    existing = path.dirname(existing);
    status = lstatOrNull(existing);
  }
  if (!status) throw new PathPolicyError("Candidate path has no contained existing parent.");
  const componentStatuses = validateExistingComponents(root, existing);
  const realData = fs.realpathSync.native(data);
  const realRoot = fs.realpathSync.native(root);
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
  linkedAncestor,
  resolveCandidatePath,
  windowsReparsePoints,
};
