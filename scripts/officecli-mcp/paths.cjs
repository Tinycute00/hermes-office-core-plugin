"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

class PathPolicyError extends Error {}

function candidateRoot() {
  const data = process.env.PLUGIN_DATA || process.env.CLAUDE_PLUGIN_DATA || path.join(os.tmpdir(), "office-os-plugin-data");
  return path.resolve(data, "officecli-candidates");
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

function rejectLink(target) {
  const status = lstatOrNull(target);
  if (status && status.isSymbolicLink()) throw new PathPolicyError("Candidate paths must not contain links or reparse points.");
  return status;
}

function validateExistingComponents(root, existing) {
  rejectLink(root);
  const relative = path.relative(root, existing);
  let cursor = root;
  for (const part of relative.split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, part);
    rejectLink(cursor);
  }
}

function resolveCandidatePath(input, options = {}) {
  if (typeof input !== "string" || input.includes("\0")) throw new PathPolicyError("Candidate path must be a string without NUL.");
  if (/^(\\\\[?.]\\|\\\\)/.test(input) || /^[A-Za-z]:[^\\/]/.test(input)) {
    throw new PathPolicyError("UNC, device, and drive-relative paths are forbidden.");
  }
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
  validateExistingComponents(root, existing);
  const realRoot = fs.realpathSync.native(root);
  const realExisting = fs.realpathSync.native(existing);
  if (realExisting !== realRoot && !isContained(realRoot, realExisting)) {
    throw new PathPolicyError("Canonical path escapes the managed candidate root.");
  }
  if (options.mustExist !== false) {
    const finalStatus = rejectLink(lexical);
    if (!finalStatus || !finalStatus.isFile()) throw new PathPolicyError("Candidate file is missing or not ordinary.");
  }
  return lexical;
}

module.exports = {
  PathPolicyError,
  candidateRoot,
  isContained,
  resolveCandidatePath,
};
