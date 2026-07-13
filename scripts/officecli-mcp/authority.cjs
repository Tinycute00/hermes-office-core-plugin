"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { candidateRoot, isContained, isLinklike, linkedAncestor, windowsReparsePoints } = require("./paths.cjs");

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

function reparsePointsFor(targets) {
  const points = new Set();
  for (let index = 0; index < targets.length; index += MAX_REPARSE_PATHS_PER_BATCH) {
    for (const target of windowsReparsePoints(targets.slice(index, index + MAX_REPARSE_PATHS_PER_BATCH))) {
      points.add(target);
    }
  }
  return points;
}

function assertRegularDirectory(target, label, reparsePoints = null) {
  const status = lstatOrNull(target);
  if (!status || isLinklike(target, status, reparsePoints) || !status.isDirectory()) {
    throw new CoreRunAuthorityError(`${label} is linked, missing, or invalid.`);
  }
  return status;
}

function assertRegularStateFile(target, reparsePoints = null) {
  const status = lstatOrNull(target);
  if (!status || isLinklike(target, status, reparsePoints) || !status.isFile() || status.nlink > 1) {
    throw new CoreRunAuthorityError("Core run state is linked, missing, or invalid.");
  }
  if (status.size > MAX_CORE_RUN_STATE_BYTES) {
    throw new CoreRunAuthorityError("Core run state exceeds the bounded authorizer limit.");
  }
}

function parseState(target, reparsePoints = null) {
  assertRegularStateFile(target, reparsePoints);
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

function directRunDirectory(candidate) {
  const root = candidateRoot();
  const dataRoot = path.dirname(root);
  if (linkedAncestor(dataRoot)) {
    throw new CoreRunAuthorityError("Plugin data path is linked or invalid.");
  }
  assertRegularDirectory(dataRoot, "Plugin data root");
  assertRegularDirectory(root, "Candidate root");
  const lexicalCandidate = path.resolve(candidate);
  if (!isContained(root, lexicalCandidate)) {
    throw new CoreRunAuthorityError("Candidate is outside the managed candidate root.");
  }
  const [runId] = path.relative(root, lexicalCandidate).split(path.sep);
  if (!RUN_ID.test(runId || "")) {
    throw new CoreRunAuthorityError("Candidate has no Core-owned direct run directory.");
  }
  const lexicalRun = path.join(root, runId);
  assertRegularDirectory(lexicalRun, "Core candidate run directory");
  const canonicalRoot = fs.realpathSync.native(root);
  const canonicalRun = fs.realpathSync.native(lexicalRun);
  const canonicalCandidate = fs.realpathSync.native(lexicalCandidate);
  if (!samePath(path.dirname(canonicalRun), canonicalRoot) || !isContained(canonicalRun, canonicalCandidate)) {
    throw new CoreRunAuthorityError("Core candidate run directory escapes staging.");
  }
  return {
    candidate: canonicalCandidate,
    dataRoot,
    runDirectory: canonicalRun,
    runId,
  };
}

function matchingState(state, run) {
  if (typeof state.candidate_directory !== "string" || !path.isAbsolute(state.candidate_directory)) {
    return false;
  }
  return samePath(path.resolve(state.candidate_directory), run.runDirectory);
}

function assertMatchingState(state, run) {
  if (state.run_id !== run.runId) {
    throw new CoreRunAuthorityError("Matching Core run state has an invalid run identity.");
  }
  if (typeof state.candidate_directory !== "string" || !path.isAbsolute(state.candidate_directory)) {
    throw new CoreRunAuthorityError("Matching Core run state has an invalid candidate directory.");
  }
  const lexical = path.resolve(state.candidate_directory);
  assertRegularDirectory(lexical, "Matching Core candidate run directory");
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

function authorizeMutation(candidate) {
  const run = directRunDirectory(candidate);
  const workspaces = path.join(run.dataRoot, "workspaces");
  if (linkedAncestor(workspaces)) {
    throw new CoreRunAuthorityError("Core workspace state path is linked or invalid.");
  }
  assertRegularDirectory(workspaces, "Core workspace state root");
  const workspacesEntries = fs.readdirSync(workspaces);
  if (workspacesEntries.length > MAX_CORE_RUN_STATES) {
    throw new CoreRunAuthorityError("Core run-state authorizer limit is exceeded.");
  }
  const workspacesPaths = workspacesEntries.map((name) => path.join(workspaces, name));
  const workspaceReparsePoints = reparsePointsFor(workspacesPaths);
  const statePaths = [];
  for (const workspace of workspacesPaths) {
    assertRegularDirectory(workspace, "Core workspace state entry", workspaceReparsePoints);
    const statePath = path.join(workspace, "run_state.json");
    if (lstatOrNull(statePath)) statePaths.push(statePath);
  }
  const stateReparsePoints = reparsePointsFor(statePaths);
  const matches = [];
  for (const statePath of statePaths) {
    const state = parseState(statePath, stateReparsePoints);
    if (matchingState(state, run)) matches.push(state);
  }
  if (matches.length !== 1) {
    throw new CoreRunAuthorityError(
      matches.length === 0
        ? "No matching active Core run state authorizes this mutation."
        : "Duplicate Core run states authorize this candidate directory.",
    );
  }
  assertMatchingState(matches[0], run);
  return run;
}

module.exports = {
  ACTIVE_MUTATION_STATUSES,
  CoreRunAuthorityError,
  MAX_CORE_RUN_STATES,
  MUTABLE_COMMANDS,
  authorizeMutation,
};
