"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { candidateRoot, isContained, windowsReparsePoints } = require("./paths.cjs");

const MAX_CORE_RUN_STATES = 512;
const MAX_CORE_RUN_STATE_BYTES = 1024 * 1024;
const MAX_REPARSE_PATHS_PER_BATCH = 64;
const MUTABLE_COMMANDS = new Set(["set", "add", "remove", "move", "swap"]);
const ACTIVE_MUTATION_STATUSES = new Set(["executing", "validating", "publishing"]);
const RUN_ID = /^[0-9a-f]{32}$/;

class CoreRunAuthorityError extends Error {}

function samePath(left, right) {
  return process.platform === "win32"
    ? left.toLowerCase() === right.toLowerCase()
    : left === right;
}

function lstatOrNull(target) {
  try {
    return fs.lstatSync(target);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

function existingAncestors(target) {
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
  return entries;
}

function createRequestReparseInspector() {
  const observed = new Set();
  const reparsePoints = new Set();
  const inspect = (targets) => {
    const pending = [];
    const queued = new Set();
    for (const target of targets) {
      const absolute = path.resolve(target);
      if (!observed.has(absolute) && !queued.has(absolute)) {
        queued.add(absolute);
        pending.push(absolute);
      }
    }
    for (let index = 0; index < pending.length; index += MAX_REPARSE_PATHS_PER_BATCH) {
      const batch = pending.slice(index, index + MAX_REPARSE_PATHS_PER_BATCH);
      for (const target of windowsReparsePoints(batch)) reparsePoints.add(target);
      for (const target of batch) observed.add(target);
    }
  };
  return {
    inspectExisting(targets) {
      inspect(targets.filter((target) => lstatOrNull(target)));
    },
    isLinklike(target, status) {
      if (!status) return false;
      if (status.isSymbolicLink()) return true;
      inspect([target]);
      return reparsePoints.has(path.resolve(target));
    },
  };
}

function assertUnlinkedAncestors(target, inspector, message) {
  const entries = existingAncestors(target);
  inspector.inspectExisting(entries.map(([item]) => item));
  for (const [item, status] of entries) {
    if (inspector.isLinklike(item, status)) {
      throw new CoreRunAuthorityError(message);
    }
  }
}

function assertRegularDirectory(target, label, inspector) {
  const status = lstatOrNull(target);
  if (!status || inspector.isLinklike(target, status) || !status.isDirectory()) {
    throw new CoreRunAuthorityError(`${label} is linked, missing, or invalid.`);
  }
  return status;
}

function assertRegularStateFile(target, inspector) {
  const status = lstatOrNull(target);
  if (!status || inspector.isLinklike(target, status) || !status.isFile() || status.nlink > 1) {
    throw new CoreRunAuthorityError("Core run state is linked, missing, or invalid.");
  }
  if (status.size > MAX_CORE_RUN_STATE_BYTES) {
    throw new CoreRunAuthorityError("Core run state exceeds the bounded authorizer limit.");
  }
}

function parseState(target, inspector) {
  assertRegularStateFile(target, inspector);
  try {
    const value = JSON.parse(fs.readFileSync(target, "utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new CoreRunAuthorityError("Core run state is malformed.");
    }
    return value;
  } catch (error) {
    if (error instanceof CoreRunAuthorityError) throw error;
    throw new CoreRunAuthorityError("Core run state is malformed.");
  }
}

function describeRun(candidate) {
  const root = candidateRoot();
  const dataRoot = path.dirname(root);
  const lexicalCandidate = path.resolve(candidate);
  if (!isContained(root, lexicalCandidate)) {
    throw new CoreRunAuthorityError("Candidate is outside the managed candidate root.");
  }
  const [runId] = path.relative(root, lexicalCandidate).split(path.sep);
  if (!RUN_ID.test(runId || "")) {
    throw new CoreRunAuthorityError("Candidate has no Core-owned direct run directory.");
  }
  return { candidate: lexicalCandidate, dataRoot, root, runDirectory: path.join(root, runId), runId };
}

function directRunDirectory(run, inspector) {
  assertUnlinkedAncestors(run.dataRoot, inspector, "Plugin data path is linked or invalid.");
  assertRegularDirectory(run.dataRoot, "Plugin data root", inspector);
  assertRegularDirectory(run.root, "Candidate root", inspector);
  const lexicalRun = run.runDirectory;
  assertRegularDirectory(lexicalRun, "Core candidate run directory", inspector);
  const canonicalRoot = fs.realpathSync.native(run.root);
  const canonicalRun = fs.realpathSync.native(lexicalRun);
  const canonicalCandidate = fs.realpathSync.native(run.candidate);
  if (!samePath(path.dirname(canonicalRun), canonicalRoot) || !isContained(canonicalRun, canonicalCandidate)) {
    throw new CoreRunAuthorityError("Core candidate run directory escapes staging.");
  }
  return {
    candidate: canonicalCandidate,
    dataRoot: run.dataRoot,
    runDirectory: canonicalRun,
    runId: run.runId,
  };
}

function matchingState(state, run) {
  if (typeof state.candidate_directory !== "string" || !path.isAbsolute(state.candidate_directory)) {
    return false;
  }
  return samePath(path.resolve(state.candidate_directory), run.runDirectory);
}

function assertMatchingState(state, run, inspector) {
  if (state.run_id !== run.runId) {
    throw new CoreRunAuthorityError("Matching Core run state has an invalid run identity.");
  }
  if (typeof state.candidate_directory !== "string" || !path.isAbsolute(state.candidate_directory)) {
    throw new CoreRunAuthorityError("Matching Core run state has an invalid candidate directory.");
  }
  const lexical = path.resolve(state.candidate_directory);
  assertRegularDirectory(lexical, "Matching Core candidate run directory", inspector);
  const canonical = fs.realpathSync.native(lexical);
  if (!samePath(canonical, run.runDirectory)) {
    throw new CoreRunAuthorityError("Matching Core run state does not own this candidate directory.");
  }
  if (state.proposal_confirmed !== true) {
    throw new CoreRunAuthorityError("Core run has not received proposal confirmation.");
  }
  if (!ACTIVE_MUTATION_STATUSES.has(state.status)) {
    throw new CoreRunAuthorityError("Core run is stale or not active for mutations.");
  }
}

function authorizeMutation(candidate, fileCandidates = []) {
  if (!Array.isArray(fileCandidates) || !fileCandidates.every((value) => typeof value === "string")) {
    throw new CoreRunAuthorityError("File-bearing mutation properties are invalid.");
  }
  const inspector = createRequestReparseInspector();
  const describedRuns = [describeRun(candidate), ...fileCandidates.map((fileCandidate) => describeRun(fileCandidate))];
  const primary = describedRuns[0];
  const workspaces = path.join(primary.dataRoot, "workspaces");
  inspector.inspectExisting([
    ...existingAncestors(primary.dataRoot).map(([item]) => item),
    ...existingAncestors(workspaces).map(([item]) => item),
    primary.dataRoot,
    primary.root,
    workspaces,
    ...describedRuns.map((described) => described.runDirectory),
  ]);
  const run = directRunDirectory(primary, inspector);
  assertUnlinkedAncestors(workspaces, inspector, "Core workspace state path is linked or invalid.");
  assertRegularDirectory(workspaces, "Core workspace state root", inspector);
  const workspacesEntries = fs.readdirSync(workspaces);
  if (workspacesEntries.length > MAX_CORE_RUN_STATES) {
    throw new CoreRunAuthorityError("Core run-state authorizer limit is exceeded.");
  }
  const workspacesPaths = workspacesEntries.map((name) => path.join(workspaces, name));
  inspector.inspectExisting(workspacesPaths);
  const statePaths = [];
  for (const workspace of workspacesPaths) {
    assertRegularDirectory(workspace, "Core workspace state entry", inspector);
    const statePath = path.join(workspace, "run_state.json");
    if (lstatOrNull(statePath)) statePaths.push(statePath);
  }
  inspector.inspectExisting(statePaths);
  const matches = [];
  for (const statePath of statePaths) {
    const state = parseState(statePath, inspector);
    if (matchingState(state, run)) matches.push(state);
  }
  if (matches.length !== 1) {
    throw new CoreRunAuthorityError(
      matches.length === 0
        ? "No matching active Core run state authorizes this mutation."
        : "Duplicate Core run states authorize this candidate directory.",
    );
  }
  assertMatchingState(matches[0], run, inspector);
  for (const described of describedRuns.slice(1)) {
    const fileRun = directRunDirectory(described, inspector);
    if (!samePath(fileRun.runDirectory, run.runDirectory)) {
      throw new CoreRunAuthorityError("File-bearing property must stay in the authorized Core candidate run directory.");
    }
  }
  return run;
}

module.exports = {
  ACTIVE_MUTATION_STATUSES,
  CoreRunAuthorityError,
  MAX_CORE_RUN_STATES,
  MUTABLE_COMMANDS,
  authorizeMutation,
};
