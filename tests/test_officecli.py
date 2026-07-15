from __future__ import annotations

# noqa: SIZE_OK - plan-mandated consolidated integration suite for one OfficeCLI surface.

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "officecli_manager.py"
LOCK = ROOT / "vendor" / "officecli.lock.json"
LAUNCHER = ROOT / "scripts" / "officecli-mcp.cjs"
JSONRPC = ROOT / "scripts" / "officecli-mcp" / "jsonrpc.cjs"
POLICY = ROOT / "scripts" / "officecli-mcp" / "policy.cjs"
RUNNER = ROOT / "scripts" / "officecli-mcp" / "runner.cjs"
PATHS = ROOT / "scripts" / "officecli-mcp" / "paths.cjs"
AUTHORITY = ROOT / "scripts" / "officecli-mcp" / "authority.cjs"
CANDIDATES = ROOT / "skills" / "office-os" / "scripts" / "office_candidates.py"
CANDIDATE_RUNS = (
    ROOT / "skills" / "office-os" / "scripts" / "office_candidate_runs.py"
)
NODE = shutil.which("node")


def short_windows_path(path: Path) -> Path:
    buffer = ctypes.create_unicode_buffer(32_768)
    length = ctypes.windll.kernel32.GetShortPathNameW(
        os.fspath(path), buffer, len(buffer)
    )
    if not length:
        raise OfficeCLITestSetupError("Windows did not provide a short path for fixture.")
    return Path(buffer.value)


class OfficeCLITestSetupError(RuntimeError):
    pass


def load_manager():
    scripts = os.fspath(MANAGER.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("officecli_manager", MANAGER)
    if spec is None or spec.loader is None:
        raise OfficeCLITestSetupError("Cannot load OfficeCLI manager module.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_candidate_modules():
    scripts = os.fspath(CANDIDATES.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    sys.modules.pop("office_candidate_runs", None)
    sys.modules.pop("office_candidates", None)
    candidate_spec = importlib.util.spec_from_file_location(
        "office_candidates", CANDIDATES
    )
    if candidate_spec is None or candidate_spec.loader is None:
        raise OfficeCLITestSetupError("Cannot load OfficeCLI candidate module.")
    candidates = importlib.util.module_from_spec(candidate_spec)
    sys.modules[candidate_spec.name] = candidates
    candidate_spec.loader.exec_module(candidates)
    runs_spec = importlib.util.spec_from_file_location(
        "office_candidate_runs", CANDIDATE_RUNS
    )
    if runs_spec is None or runs_spec.loader is None:
        raise OfficeCLITestSetupError("Cannot load OfficeCLI candidate-run module.")
    runs = importlib.util.module_from_spec(runs_spec)
    sys.modules[runs_spec.name] = runs
    runs_spec.loader.exec_module(runs)
    return candidates, runs


def run_jsonrpc(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    self_test = (
        "const { runProtocol } = require(process.argv[1]);"
        "let calls=0;runProtocol({"
        "tool:{name:'officecli',description:'test',inputSchema:{type:'object'}},"
        "callTool:async(args)=>{"
        "calls+=1;if(args.reportCalls)return {calls};"
        "if(args.fail==='policy')return {content:[{type:'text',text:'denied'}],isError:true};"
        "if(args.fail==='internal')throw new Error('boom');"
        "return {content:[{type:'text',text:'ok'}]};}});"
    )
    return subprocess.run(
        [NODE or "node", "-e", self_test, os.fspath(JSONRPC)],
        cwd=ROOT,
        input=payload,
        capture_output=True,
        check=False,
    )


def run_policy(arguments: dict, data_root: Path) -> dict:  # noqa: DICT_OK
    script = (
        "const { TOOL, parseToolArguments } = require(process.argv[1]);"
        "let input='';process.stdin.setEncoding('utf8');"
        "process.stdin.on('data',chunk=>input+=chunk);"
        "process.stdin.on('end',()=>{try{"
        "const value=JSON.parse(input);"
        "process.stdout.write(JSON.stringify(value.schema?TOOL:parseToolArguments(value)));"
        "}catch(error){process.stdout.write(JSON.stringify({error:error.message}));}});"
    )
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = os.fspath(data_root)
    completed = subprocess.run(
        [NODE or "node", "-e", script, os.fspath(POLICY)],
        cwd=ROOT,
        env=environment,
        input=json.dumps(arguments),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def run_authorizer(candidate: Path, data_root: Path) -> dict:  # noqa: DICT_OK
    script = (
        "const authority=require(process.argv[1]);"
        "try{process.stdout.write(JSON.stringify({run:authority.authorizeMutation(process.argv[2])}));}"
        "catch(error){process.stdout.write(JSON.stringify({error:error.message}));}"
    )
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = os.fspath(data_root)
    completed = subprocess.run(
        [NODE or "node", "-e", script, os.fspath(AUTHORITY), os.fspath(candidate)],
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def run_authorizer_with_reparse_probe(
    candidate: Path, data_root: Path, file_candidate: Path | None = None, repeats: int = 1
) -> dict:  # noqa: DICT_OK
    script = (
        "const path=require('node:path');const paths=require(process.argv[1]);let probes=0;const batches=[];"
        "paths.windowsReparsePoints=(targets)=>{probes+=1;const batch=targets.map(target=>path.resolve(target));"
        "batches.push(batch);return new Set();};const authority=require(process.argv[2]);"
        "const files=process.argv[4]?[process.argv[4]]:[];const runs=[],errors=[];"
        "for(let index=0;index<Number(process.argv[5]);index+=1){try{"
        "runs.push(authority.authorizeMutation(process.argv[3],files));}"
        "catch(error){errors.push(error.message);}}"
        "process.stdout.write(JSON.stringify({run:runs[0],error:errors[0],runs,errors,probes,batches}));"
    )
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = os.fspath(data_root)
    completed = subprocess.run(
        [NODE or "node", "-e", script, os.fspath(PATHS), os.fspath(AUTHORITY), os.fspath(candidate),
         "" if file_candidate is None else os.fspath(file_candidate), str(repeats)],
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def run_runner(configuration: dict, data_root: Path) -> dict:  # noqa: DICT_OK
    # The nonsettling fake deliberately has no PID, so Unix group cleanup cannot
    # signal a real CI process while this harness exercises the terminal deadline.
    script = (
        "let input='';"
        "process.stdin.setEncoding('utf8');process.stdin.on('data',c=>input+=c);"
        "process.stdin.on('end',async()=>{const c=JSON.parse(input);try{"
        "if(c.nonsettlingKill){const {EventEmitter}=require('node:events');"
        "const childProcess=require('node:child_process');let launches=0;"
        "childProcess.spawn=()=>{const child=new EventEmitter();launches+=1;"
        "if(launches===1){child.stdout=new EventEmitter();child.stderr=new EventEmitter();}return child;};"
        "const runner=require(process.argv[1]);try{await runner.runProcess('fake',[],1,25);"
        "process.stdout.write(JSON.stringify({error:'settled'}));}catch(error){"
        "process.stdout.write(JSON.stringify({error:error.message}));}return;}"
        "const runner=require(process.argv[1]);"
        "for(const [key,value] of Object.entries(c.inheritedEnvironment||{}))process.env[key]=value;"
        "if(c.constants){process.stdout.write(JSON.stringify(runner.CONSTANTS));return;}"
        "if(c.environment){process.stdout.write(JSON.stringify(runner.childEnvironment()));return;}"
        "const result=await runner.runTool(process.execPath,c.parsed,c.options||{});"
        "process.stdout.write(JSON.stringify(result));"
        "}catch(error){process.stdout.write(JSON.stringify({error:error.message}));}});"
    )
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = os.fspath(data_root)
    completed = subprocess.run(
        [NODE or "node", "-e", script, os.fspath(RUNNER)],
        cwd=ROOT,
        env=environment,
        input=json.dumps(configuration),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=2 if configuration.get("nonsettlingKill") else 15,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def process_exists(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            capture_output=True,
            check=False,
        )
        return str(pid) in completed.stdout and "No tasks" not in completed.stdout
    if sys.platform.startswith("linux"):
        try:
            stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        _, separator, fields = stat.rpartition(")")
        if separator and fields.lstrip().startswith(("Z", "X")):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def run_adapter(
    payload: bytes,
    data_root: Path,
    foreign_cwd: Path,
    capture: Path,
    tamper_after_start: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    script = (
        "const fs=require('node:fs');const adapter=require(process.argv[1]);"
        "let checks=0,calls=0;adapter.start({"
        f"verifyRuntime:()=>{{checks+=1;if({str(tamper_after_start).lower()}&&checks>1)throw new adapter.RuntimeIntegrityError('runtime checksum mismatch');return process.execPath;}},"
        "execute:async(_binary,parsed)=>{calls+=1;fs.writeFileSync(process.argv[2],JSON.stringify({argv:parsed.argv,calls,checks}));return {content:[{type:'text',text:'ok'}]};}"
        "});"
    )
    environment = os.environ.copy()
    environment["PLUGIN_DATA"] = os.fspath(data_root)
    return subprocess.run(
        [NODE or "node", "-e", script, os.fspath(LAUNCHER), os.fspath(capture)],
        cwd=foreign_cwd,
        env=environment,
        input=payload,
        capture_output=True,
        check=False,
        timeout=10,
    )
class OfficeCLICase(unittest.TestCase):
    def test_adapter_canonical_config_foreign_cwd_and_no_upstream_mcp(self) -> None:
        expected = {
            "mcpServers": {
                "officecli": {
                    "command": "node",
                    "args": ["./scripts/officecli-mcp.cjs"],
                    "cwd": ".",
                }
            }
        }
        self.assertEqual(json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8")), expected)
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidates = data_root / "officecli-candidates"
            candidates.mkdir(parents=True)
            candidate = candidates / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            foreign = base / "foreign-workspace"
            foreign.mkdir()
            capture = base / "capture.json"
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "officecli", "arguments": {"command": ["validate", os.fspath(candidate)]}},
                },
            ]
            payload = b"\n".join(json.dumps(message).encode() for message in messages) + b"\n"
            completed = run_adapter(payload, data_root, foreign, capture)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual([response["id"] for response in responses], [1, 2, 3])
            self.assertEqual(
                responses[0]["result"]["serverInfo"],
                {"name": "office-os-officecli", "version": "1.0.135"},
            )
            tools = responses[1]["result"]["tools"]
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["name"], "officecli")
            self.assertEqual(tools[0]["inputSchema"]["properties"]["command"]["type"], "array")
            captured = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(captured["argv"][0], "validate")
            self.assertNotIn("mcp", captured["argv"])
            for line in completed.stdout.splitlines():
                json.loads(line)

    def test_adapter_revalidates_binary_before_every_call(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidates = data_root / "officecli-candidates"
            candidates.mkdir(parents=True)
            candidate = candidates / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            capture = base / "capture.json"
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "officecli", "arguments": {"command": ["validate", os.fspath(candidate)]}},
                },
            ]
            payload = b"\n".join(json.dumps(message).encode() for message in messages) + b"\n"
            completed = run_adapter(payload, data_root, base, capture, tamper_after_start=True)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual(responses[1]["id"], 2)
            self.assertTrue(responses[1]["result"]["isError"])
            self.assertIn("checksum mismatch", responses[1]["result"]["content"][0]["text"])
            self.assertFalse(capture.exists())

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_authorizer_accepts_short_alias_for_confirmed_candidate(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            run_id = "a" * 32
            run_directory = data_root / "officecli-candidates" / run_id
            run_directory.mkdir(parents=True)
            candidate = run_directory / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            workspace = data_root / "workspaces" / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "run_state.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "candidate_directory": os.fspath(short_windows_path(run_directory)),
                        "proposal_confirmed": True,
                        "status": "executing",
                    }
                ),
                encoding="utf-8",
            )
            short_candidate = short_windows_path(candidate)
            if os.path.normcase(os.fspath(short_candidate)) == os.path.normcase(
                os.fspath(candidate)
            ):
                self.skipTest("fixture path has no distinct Windows short alias")

            result = run_authorizer(short_candidate, data_root)
            self.assertNotIn("error", result)
            self.assertTrue(os.path.samefile(result["run"]["candidate"], candidate))

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_authorizer_short_alias_still_requires_direct_active_confirmation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            run_id = "d" * 32
            run_directory = data_root / "officecli-candidates" / run_id
            run_directory.mkdir(parents=True)
            candidate = run_directory / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            short_candidate = short_windows_path(candidate)
            if os.path.normcase(os.fspath(short_candidate)) == os.path.normcase(
                os.fspath(candidate)
            ):
                self.skipTest("fixture path has no distinct Windows short alias")
            workspace = data_root / "workspaces" / "workspace"
            workspace.mkdir(parents=True)
            state_path = workspace / "run_state.json"

            for label, confirmed, status in (
                ("unconfirmed", False, "executing"),
                ("stale", True, "completed"),
            ):
                state_path.write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "candidate_directory": os.fspath(short_windows_path(run_directory)),
                            "proposal_confirmed": confirmed,
                            "status": status,
                        }
                    ),
                    encoding="utf-8",
                )
                with self.subTest(label=label):
                    self.assertIn("error", run_authorizer(short_candidate, data_root))

            non_direct = data_root / "officecli-candidates" / "not-a-run" / "candidate.xlsx"
            non_direct.parent.mkdir()
            non_direct.write_bytes(b"candidate")
            self.assertIn("error", run_authorizer(short_windows_path(non_direct), data_root))

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_policy_accepts_short_alias_for_managed_candidate(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate = data_root / "officecli-candidates" / ("b" * 32) / "candidate.xlsx"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"candidate")
            short_candidate = short_windows_path(candidate)
            if os.path.normcase(os.fspath(short_candidate)) == os.path.normcase(
                os.fspath(candidate)
            ):
                self.skipTest("fixture path has no distinct Windows short alias")

            parsed = run_policy(
                {"command": ["validate", os.fspath(short_candidate)]}, data_root
            )
            self.assertEqual(parsed["argv"][0], "validate")
            self.assertTrue(os.path.samefile(parsed["argv"][1], candidate))

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_runner_cleans_with_short_authority_aliases(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            run_directory = data_root / "officecli-candidates" / ("c" * 32)
            run_directory.mkdir(parents=True)
            candidate = run_directory / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            short_run = short_windows_path(run_directory)
            short_candidate = short_windows_path(candidate)
            if os.path.normcase(os.fspath(short_run)) == os.path.normcase(
                os.fspath(run_directory)
            ):
                self.skipTest("fixture path has no distinct Windows short alias")
            overflow = (
                "const fs=require('node:fs');const path=require('node:path');"
                "for(let index=0;index<32;index+=1)fs.writeFileSync(path.join(process.argv[1],`overflow-${index}.tmp`),'x');"
            )

            result = run_runner(
                {
                    "parsed": {
                        "argv": ["-e", overflow, os.fspath(run_directory)],
                        "screenshot": False,
                    },
                    "options": {
                        "authority": {
                            "candidate": os.fspath(short_candidate),
                            "runDirectory": os.fspath(short_run),
                        }
                    },
                },
                data_root,
            )

            self.assertFalse(result.get("isError", False), result)
            self.assertEqual(list(run_directory.glob("overflow-*.tmp")), [])

    @unittest.skipUnless(os.name == "nt", "requires Windows short paths")
    def test_short_alias_does_not_relax_candidate_containment(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            outside_file = outside / "sentinel.xlsx"
            outside_file.write_bytes(b"outside")
            hard_link = candidate_root / "hard-link.xlsx"
            os.link(outside_file, hard_link)
            junction = candidate_root / "linked"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(junction), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            try:
                short_base = short_windows_path(base)
                short_root = short_windows_path(candidate_root)
                cases = {
                    "outside": short_base / outside.name / outside_file.name,
                    "hard-link": short_root / hard_link.name,
                    "junction": short_root / junction.name / outside_file.name,
                }
                if all(
                    os.path.normcase(os.fspath(alias))
                    == os.path.normcase(os.fspath(original))
                    for alias, original in (
                        (cases["outside"], outside_file),
                        (cases["hard-link"], hard_link),
                        (cases["junction"], junction / outside_file.name),
                    )
                ):
                    self.skipTest("fixture paths have no distinct Windows short aliases")
                for label, alias in cases.items():
                    with self.subTest(label=label):
                        result = run_policy(
                            {"command": ["validate", os.fspath(alias)]}, data_root
                        )
                        self.assertIn("error", result)
                self.assertEqual(outside_file.read_bytes(), b"outside")
            finally:
                if os.path.lexists(junction):
                    os.rmdir(junction)

    def test_mutations_require_confirmed_matching_core_run_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            generic = candidate_root / "generic.xlsx"
            generic.write_bytes(b"generic")
            run_id = "a" * 32
            run_directory = candidate_root / run_id
            run_directory.mkdir()
            candidate = run_directory / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            workspace = data_root / "workspaces" / "workspace"
            workspace.mkdir(parents=True)

            def call_mutation(target: Path, capture: Path) -> dict:  # noqa: DICT_OK
                messages = [
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "officecli",
                            "arguments": {
                                "command": [
                                    "set",
                                    os.fspath(target),
                                    "/Sheet1/A1",
                                    "--prop",
                                    "text=changed",
                                ]
                            },
                        },
                    },
                ]
                payload = b"\n".join(json.dumps(message).encode() for message in messages) + b"\n"
                completed = run_adapter(payload, data_root, base, capture)
                self.assertEqual(completed.returncode, 0, completed.stderr.decode())
                return json.loads(completed.stdout.splitlines()[-1])

            no_child_capture = base / "no-child.json"
            no_child = call_mutation(generic, no_child_capture)
            self.assertTrue(no_child["result"]["isError"])
            self.assertFalse(no_child_capture.exists())

            state_path = workspace / "run_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "candidate_directory": os.fspath(run_directory),
                        "proposal_confirmed": False,
                        "status": "awaiting_confirmation",
                    }
                ),
                encoding="utf-8",
            )
            preconfirmed_capture = base / "preconfirmed.json"
            preconfirmed = call_mutation(candidate, preconfirmed_capture)
            self.assertTrue(preconfirmed["result"]["isError"])
            self.assertFalse(preconfirmed_capture.exists())

            state_path.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "candidate_directory": os.fspath(run_directory),
                        "proposal_confirmed": True,
                        "status": "executing",
                    }
                ),
                encoding="utf-8",
            )
            allowed_capture = base / "allowed.json"
            allowed = call_mutation(candidate, allowed_capture)
            self.assertFalse(allowed["result"].get("isError", False))
            self.assertEqual(json.loads(allowed_capture.read_text(encoding="utf-8"))["argv"][0], "set")

    def test_mutations_reject_file_properties_from_another_core_run(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            # Given: two confirmed Core runs own separate ordinary candidates.
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            target_run_id = "a" * 32
            private_run_id = "b" * 32
            target_run = candidate_root / target_run_id
            private_run = candidate_root / private_run_id
            target_run.mkdir(parents=True)
            private_run.mkdir()
            target = target_run / "candidate.xlsx"
            private_image = private_run / "private.png"
            target.write_bytes(b"candidate")
            private_image.write_bytes(b"private")
            for workspace, run_id, directory in (
                ("target", target_run_id, target_run),
                ("private", private_run_id, private_run),
            ):
                state_directory = data_root / "workspaces" / workspace
                state_directory.mkdir(parents=True)
                (state_directory / "run_state.json").write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "candidate_directory": os.fspath(directory),
                            "proposal_confirmed": True,
                            "status": "executing",
                        }
                    ),
                    encoding="utf-8",
                )

            # When: the target run asks OfficeCLI to read an image from the other run.
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "officecli",
                        "arguments": {
                            "command": [
                                "add", os.fspath(target), "/Sheet1", "--type", "image",
                                "--prop", f"src={private_image}",
                            ]
                        },
                    },
                },
            ]
            payload = b"\n".join(json.dumps(message).encode() for message in messages) + b"\n"
            capture = base / "cross-run-child.json"
            completed = run_adapter(payload, data_root, base, capture)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            response = json.loads(completed.stdout.splitlines()[-1])

            # Then: the adapter extracts the file property and authority denies
            # it before the child executor is reached.
            self.assertTrue(response["result"].get("isError", False), response)
            self.assertFalse(capture.exists())

    def test_cross_run_file_property_batches_reparse_probes_per_request(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            target_run = candidate_root / ("a" * 32)
            private_run = candidate_root / ("b" * 32)
            target_run.mkdir(parents=True)
            private_run.mkdir()
            target = target_run / "candidate.xlsx"
            private_image = private_run / "private.png"
            target.write_bytes(b"candidate")
            private_image.write_bytes(b"private")
            for workspace, run_id, directory in (
                ("target", "a" * 32, target_run),
                ("private", "b" * 32, private_run),
            ):
                state_directory = data_root / "workspaces" / workspace
                state_directory.mkdir(parents=True)
                (state_directory / "run_state.json").write_text(
                    json.dumps({"run_id": run_id, "candidate_directory": os.fspath(directory),
                                "proposal_confirmed": True, "status": "executing"}),
                    encoding="utf-8",
                )

            result = run_authorizer_with_reparse_probe(target, data_root, private_image)
            self.assertIn("error", result)
            self.assertIn("File-bearing property", result["error"])
            self.assertLessEqual(result["probes"], 3)
            repeated = run_authorizer_with_reparse_probe(target, data_root, private_image, repeats=2)
            self.assertEqual(repeated["errors"], [result["error"], result["error"]])
            self.assertEqual(repeated["probes"], 6)

    def test_mutation_authorizer_fails_closed_for_stale_and_invalid_state_inventory(self) -> None:
        def fixture() -> tuple[Path, Path, Path, str]:
            temporary = tempfile.TemporaryDirectory(dir=ROOT)
            self.addCleanup(temporary.cleanup)
            base = Path(temporary.name)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            run_id = "b" * 32
            run_directory = candidate_root / run_id
            run_directory.mkdir(parents=True)
            candidate = run_directory / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            (data_root / "workspaces").mkdir()
            return data_root, run_directory, candidate, run_id

        def state(run_id: str, run_directory: Path, **overrides: object) -> dict:  # noqa: DICT_OK
            return {
                "run_id": run_id,
                "candidate_directory": os.fspath(run_directory),
                "proposal_confirmed": True,
                "status": "executing",
                **overrides,
            }

        data_root, run_directory, candidate, run_id = fixture()
        stale_workspace = data_root / "workspaces" / "stale"
        stale_workspace.mkdir()
        (stale_workspace / "run_state.json").write_text(
            json.dumps(state(run_id, run_directory, status="failed")), encoding="utf-8"
        )
        self.assertIn("error", run_authorizer(candidate, data_root))

        data_root, run_directory, candidate, _run_id = fixture()
        malformed_workspace = data_root / "workspaces" / "malformed"
        malformed_workspace.mkdir()
        (malformed_workspace / "run_state.json").write_text("{", encoding="utf-8")
        self.assertIn("error", run_authorizer(candidate, data_root))

        data_root, run_directory, candidate, run_id = fixture()
        linked_workspace = data_root / "workspaces" / "linked-state"
        linked_workspace.mkdir()
        outside_state = data_root.parent / "outside-run_state.json"
        outside_state.write_text(json.dumps(state(run_id, run_directory)), encoding="utf-8")
        os.link(outside_state, linked_workspace / "run_state.json")
        self.assertIn("error", run_authorizer(candidate, data_root))

        data_root, run_directory, candidate, run_id = fixture()
        for name in ("first", "second"):
            workspace = data_root / "workspaces" / name
            workspace.mkdir()
            (workspace / "run_state.json").write_text(
                json.dumps(state(run_id, run_directory)), encoding="utf-8"
            )
        duplicate = run_authorizer(candidate, data_root)
        self.assertIn("error", duplicate)
        self.assertIn("Duplicate", duplicate["error"])

        data_root, run_directory, candidate, run_id = fixture()
        outside = data_root.parent / "outside-workspace"
        outside.mkdir()
        (outside / "run_state.json").write_text(
            json.dumps(state(run_id, run_directory)), encoding="utf-8"
        )
        linked = data_root / "workspaces" / "linked"
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        else:
            linked.symlink_to(outside, target_is_directory=True)
        try:
            self.assertIn("error", run_authorizer(candidate, data_root))
        finally:
            if os.path.lexists(linked):
                if os.name == "nt":
                    os.rmdir(linked)
                else:
                    linked.unlink()

        data_root, run_directory, candidate, run_id = fixture()
        active_workspace = data_root / "workspaces" / "active"
        active_workspace.mkdir()
        (active_workspace / "run_state.json").write_text(
            json.dumps(state(run_id, run_directory)), encoding="utf-8"
        )
        for number in range(512):
            (data_root / "workspaces" / f"overflow-{number}").mkdir()
        over_limit = run_authorizer(candidate, data_root)
        self.assertIn("error", over_limit)
        self.assertIn("limit", over_limit["error"].lower())

    @unittest.skipUnless(os.name == "nt", "Windows reparse-point batching is Windows-specific")
    def test_mutation_authorizer_batches_bounded_reparse_scans(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            run_id = "d" * 32
            run_directory = candidate_root / run_id
            run_directory.mkdir(parents=True)
            candidate = run_directory / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            workspaces = data_root / "workspaces"
            workspaces.mkdir()
            for number in range(512):
                workspace = workspaces / f"workspace-{number:03d}"
                workspace.mkdir()
                state = {
                    "run_id": run_id,
                    "candidate_directory": os.fspath(
                        run_directory if number == 0 else candidate_root / f"other-{number:03d}"
                    ),
                    "proposal_confirmed": True,
                    "status": "executing",
                }
                (workspace / "run_state.json").write_text(
                    json.dumps(state), encoding="utf-8"
                )
            result = run_authorizer_with_reparse_probe(candidate, data_root)
            self.assertNotIn("error", result)
            self.assertEqual(result["run"]["runId"], run_id)
            self.assertLessEqual(result["probes"], 32)

    @unittest.skipUnless(os.name == "nt", "Windows reparse-point batching is Windows-specific")
    def test_mutation_authorizer_covers_non_divisible_reparse_batch_tails(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            run_id = "e" * 32
            run_directory = candidate_root / run_id
            run_directory.mkdir(parents=True)
            candidate = run_directory / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            workspaces = data_root / "workspaces"
            workspaces.mkdir()
            expected_workspaces: set[str] = set()
            expected_states: set[str] = set()
            for number in range(129):
                workspace = workspaces / f"workspace-{number:03d}"
                workspace.mkdir()
                state_path = workspace / "run_state.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "run_id": run_id,
                            "candidate_directory": os.fspath(
                                run_directory if number == 0 else candidate_root / f"other-{number:03d}"
                            ),
                            "proposal_confirmed": True,
                            "status": "executing",
                        }
                    ),
                    encoding="utf-8",
                )
                expected_workspaces.add(os.path.normcase(os.path.normpath(os.fspath(workspace))))
                expected_states.add(os.path.normcase(os.path.normpath(os.fspath(state_path))))

            result = run_authorizer_with_reparse_probe(candidate, data_root)
            self.assertNotIn("error", result)
            self.assertTrue(result["batches"])
            self.assertTrue(all(len(batch) <= 64 for batch in result["batches"]))
            flattened = {
                os.path.normcase(os.path.normpath(item))
                for batch in result["batches"]
                for item in batch
            }
            self.assertTrue(expected_workspaces.issubset(flattened))
            self.assertTrue(expected_states.issubset(flattened))

    def test_authorized_postflight_cleanup_preserves_candidate_root_and_sentinels(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            run_id = "c" * 32
            run_directory = candidate_root / run_id
            run_directory.mkdir(parents=True)
            source = run_directory / "candidate.xlsx"
            source.write_bytes(b"source")
            sibling = candidate_root / "generic-sibling"
            sibling.mkdir()
            sibling_sentinel = sibling / "sibling.xlsx"
            sibling_sentinel.write_bytes(b"sibling")
            outside_sentinel = base / "outside-sentinel.txt"
            outside_sentinel.write_text("outside", encoding="utf-8")
            overflow = (
                "const fs=require('node:fs');const path=require('node:path');"
                "for(let index=0;index<31;index+=1)fs.writeFileSync(path.join(process.argv[1],`overflow-${index}.tmp`),'x');"
            )
            result = run_runner(
                {
                    "parsed": {
                        "argv": ["-e", overflow, os.fspath(run_directory)],
                        "screenshot": False,
                    },
                    "options": {
                        "authority": {
                            "candidate": os.fspath(source),
                            "runDirectory": os.fspath(run_directory),
                        }
                    },
                },
                data_root,
            )
            self.assertFalse(result.get("isError", False))
            self.assertTrue(candidate_root.is_dir())
            self.assertTrue(run_directory.is_dir())
            self.assertEqual(source.read_bytes(), b"source")
            self.assertEqual(sibling_sentinel.read_bytes(), b"sibling")
            self.assertEqual(outside_sentinel.read_text(encoding="utf-8"), "outside")
            self.assertEqual(list(run_directory.glob("overflow-*.tmp")), [])

    def test_unconfirmed_termination_poisons_the_mcp_session(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            candidate = candidate_root / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            capture = base / "termination-counts.json"
            script = (
                "const fs=require('node:fs');const {EventEmitter}=require('node:events');"
                "const childProcess=require('node:child_process');let spawns=0,executions=0;"
                "childProcess.spawn=()=>{const child=new EventEmitter();child.pid=++spawns;"
                "child.stdout=new EventEmitter();child.stderr=new EventEmitter();return child;};"
                "const adapter=require(process.argv[1]);const runner=require(process.argv[2]);"
                "process.on('beforeExit',()=>fs.writeFileSync(process.argv[3],JSON.stringify({spawns,executions})));"
                "adapter.start({verifyRuntime:()=>process.execPath,execute:async(_binary,_parsed,options)=>{"
                "executions+=1;return runner.runTool('fake',{argv:[],screenshot:false},{...options,timeoutMs:1,terminationDeadlineMs:25});}});"
            )
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "officecli", "arguments": {"command": ["validate", os.fspath(candidate)]}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "officecli", "arguments": {"command": ["validate", os.fspath(candidate)]}},
                },
            ]
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            completed = subprocess.run(
                [NODE or "node", "-e", script, os.fspath(LAUNCHER), os.fspath(RUNNER), os.fspath(capture)],
                cwd=ROOT,
                env=environment,
                input=b"\n".join(json.dumps(message).encode() for message in messages) + b"\n",
                capture_output=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertTrue(responses[1]["result"]["isError"])
            self.assertTrue(responses[2]["result"]["isError"])
            self.assertIn("poisoned", responses[2]["result"]["content"][0]["text"].lower())
            self.assertEqual(json.loads(capture.read_text(encoding="utf-8"))["executions"], 1)

    def test_read_only_commands_do_not_require_core_mutation_authority(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            candidate = candidate_root / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            capture = base / "read-only.json"
            commands = [
                ["validate", os.fspath(candidate)],
                ["get", os.fspath(candidate), "/Sheet1/A1"],
                ["query", os.fspath(candidate), "cell"],
                ["view", os.fspath(candidate), "stats"],
                ["view", os.fspath(candidate), "screenshot"],
            ]
            messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
            messages.extend(
                {
                    "jsonrpc": "2.0",
                    "id": index + 2,
                    "method": "tools/call",
                    "params": {"name": "officecli", "arguments": {"command": command}},
                }
                for index, command in enumerate(commands)
            )
            payload = b"\n".join(json.dumps(message).encode() for message in messages) + b"\n"
            completed = run_adapter(payload, data_root, base, capture)
            self.assertEqual(completed.returncode, 0, completed.stderr.decode())
            responses = [json.loads(line) for line in completed.stdout.splitlines()]
            self.assertEqual([response["id"] for response in responses], list(range(1, 7)))
            self.assertTrue(all(not response["result"].get("isError", False) for response in responses[1:]))
            self.assertEqual(json.loads(capture.read_text(encoding="utf-8"))["calls"], len(commands))

    def test_runner_child_environment_replaces_all_officecli_case_variants(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            environment = run_runner(
                {
                    "environment": True,
                    "inheritedEnvironment": {
                        "OFFICECLI_UNTRUSTED": "upper",
                        "officecli_lower_untrusted": "lower",
                        "OfficeCli_Mixed_Untrusted": "mixed",
                        "ARBITRARY_SENTINEL_SECRET": "not-for-officecli",
                        "PLUGIN_DATA": os.fspath(data_root),
                    },
                },
                data_root,
            )
        officecli = {
            key: value
            for key, value in environment.items()
            if key.upper().startswith("OFFICECLI_")
        }
        self.assertEqual(
            officecli,
            {
                "OFFICECLI_SKIP_UPDATE": "1",
                "OFFICECLI_NO_AUTO_INSTALL": "1",
                "OFFICECLI_NO_AUTO_RESIDENT": "1",
            },
        )
        self.assertNotIn("ARBITRARY_SENTINEL_SECRET", environment)
        self.assertNotIn("PLUGIN_DATA", environment)

    def test_runner_child_process_drops_ambient_credentials_and_plugin_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            (data_root / "officecli-candidates").mkdir(parents=True)
            capture = base / "runner-child-environment.json"
            probe = (
                "const fs=require('node:fs');"
                "const names=['ARBITRARY_SENTINEL_SECRET','PLUGIN_DATA'];"
                "const visible=Object.fromEntries(names.map(name=>[name,"
                "Object.prototype.hasOwnProperty.call(process.env,name)]));"
                "fs.writeFileSync(process.argv[1],JSON.stringify(visible));"
            )
            result = run_runner(
                {
                    "parsed": {"argv": ["-e", probe, os.fspath(capture)], "screenshot": False},
                    "inheritedEnvironment": {
                        "ARBITRARY_SENTINEL_SECRET": "not-for-officecli",
                        "PLUGIN_DATA": os.fspath(data_root),
                    },
                },
                data_root,
            )
            self.assertFalse(result.get("isError", False))
            visible = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(
            visible,
            {"ARBITRARY_SENTINEL_SECRET": False, "PLUGIN_DATA": False},
        )

    def test_runner_kills_tree_on_timeout_and_overflow(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            (data_root / "officecli-candidates").mkdir(parents=True)
            fake = base / "fake-runner.cjs"
            fake.write_text(
                "const {spawn}=require('node:child_process');const fs=require('node:fs');"
                "const mode=process.argv[2],pidFile=process.argv[3];"
                "const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});"
                "fs.writeFileSync(pidFile,String(child.pid));"
                "if(mode==='overflow')process.stdout.write(Buffer.alloc(9*1024*1024,120));"
                "setInterval(()=>{},1000);",
                encoding="utf-8",
            )
            constants = run_runner({"constants": True}, data_root)
            self.assertEqual(constants["normalTimeoutMs"], 60_000)
            self.assertEqual(constants["screenshotTimeoutMs"], 120_000)
            self.assertEqual(constants["terminationGraceMs"], 5_000)
            self.assertEqual(constants["streamLimitBytes"], 8 * 1024 * 1024)
            self.assertEqual(constants["pngLimitBytes"], 16 * 1024 * 1024)
            environment = run_runner({"environment": True}, data_root)
            self.assertEqual(
                {key: value for key, value in environment.items() if key.startswith("OFFICECLI_")},
                {
                    "OFFICECLI_SKIP_UPDATE": "1",
                    "OFFICECLI_NO_AUTO_INSTALL": "1",
                    "OFFICECLI_NO_AUTO_RESIDENT": "1",
                },
            )
            for mode in ("hang", "overflow"):
                pid_file = base / f"{mode}.pid"
                result = run_runner(
                    {
                        "parsed": {
                            "argv": [os.fspath(fake), mode, os.fspath(pid_file)],
                            "screenshot": False,
                        },
                        "options": {"timeoutMs": 2_000},
                    },
                    data_root,
                )
                self.assertNotIn("error", result, result)
                self.assertTrue(result.get("isError", False), result)
                self.assertLess(len(result["content"][0]["text"]), 20_000)
                pid = int(pid_file.read_text(encoding="utf-8"))
                self.assertFalse(process_exists(pid), f"grandchild {pid} survived {mode}")

    def test_runner_settles_when_process_tree_termination_never_settles(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            result = run_runner({"nonsettlingKill": True}, data_root)
        self.assertEqual(result["error"], "OfficeCLI command timed out. Process termination did not complete.")

    def test_screenshot_success_and_failure_cleanup(self) -> None:
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000b49444154789c6360000200000500017a5eab3f0000000049454e44ae426082"
        )
        invalid_idat = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000349444154789c007edcb25c0000000049454e44ae426082"
        )
        malformed_pngs = {
            "invalid-ihdr": "89504e470d0a1a0a0000000d49484452000000010000000103000000004daeaa440000000949444154789c630000000100015eff7df90000000049454e44ae426082",
            "interlaced": "89504e470d0a1a0a0000000d49484452000000010000000108060000016812f41f0000000b49444154789c6360000200000500017a5eab3f0000000049454e44ae426082",
            "invalid-scanline": "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c6365000200001e0006bca97c690000000049454e44ae426082",
            "wrong-length": "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000c49444154789c63606060000000040001f61738550000000049454e44ae426082",
        }
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            fake = base / "fake-screenshot.cjs"
            fake.write_text(
                "const fs=require('node:fs');const args=process.argv.slice(2);"
                "const out=args[args.indexOf('--out')+1];"
                "if(args.includes('missing'))process.exit(0);"
                "if(args.includes('fail')){fs.writeFileSync(out,'bad');process.exit(2);}"
                "if(args.includes('signature')){fs.writeFileSync(out,Buffer.from('89504e470d0a1a0a','hex'));process.exit(0);}"
                "if(args.includes('truncated')){fs.writeFileSync(out,Buffer.from('89504e470d0a1a0a0000000d49484452','hex'));process.exit(0);}"
                f"if(args.includes('corrupt-crc')){{const png=Buffer.from('{png.hex()}','hex');png[29]^=1;fs.writeFileSync(out,png);process.exit(0);}}"
                f"if(args.includes('invalid-idat')){{fs.writeFileSync(out,Buffer.from('{invalid_idat.hex()}','hex'));process.exit(0);}}"
                f"if(args.includes('invalid-ihdr')){{fs.writeFileSync(out,Buffer.from('{malformed_pngs['invalid-ihdr']}','hex'));process.exit(0);}}"
                f"if(args.includes('interlaced')){{fs.writeFileSync(out,Buffer.from('{malformed_pngs['interlaced']}','hex'));process.exit(0);}}"
                f"if(args.includes('invalid-scanline')){{fs.writeFileSync(out,Buffer.from('{malformed_pngs['invalid-scanline']}','hex'));process.exit(0);}}"
                f"if(args.includes('wrong-length')){{fs.writeFileSync(out,Buffer.from('{malformed_pngs['wrong-length']}','hex'));process.exit(0);}}"
                f"fs.writeFileSync(out,Buffer.from('{png.hex()}','hex'));",
                encoding="utf-8",
            )
            success = run_runner(
                {"parsed": {"argv": [os.fspath(fake), "success"], "screenshot": True}},
                data_root,
            )
            self.assertFalse(success.get("isError", False))
            self.assertEqual(len(success["content"]), 1)
            image = success["content"][0]
            self.assertEqual((image["type"], image["mimeType"]), ("image", "image/png"))
            self.assertEqual(__import__("base64").b64decode(image["data"]), png)
            failure = run_runner(
                {"parsed": {"argv": [os.fspath(fake), "fail"], "screenshot": True}},
                data_root,
            )
            self.assertTrue(failure["isError"])
            missing = run_runner(
                {"parsed": {"argv": [os.fspath(fake), "missing"], "screenshot": True}},
                data_root,
            )
            self.assertTrue(missing["isError"])
            self.assertNotIn("error", missing)
            self.assertEqual(missing["content"][0]["text"], "Screenshot processing failed.")
            for mode in (
                "signature",
                "truncated",
                "corrupt-crc",
                "invalid-idat",
                "invalid-ihdr",
                "interlaced",
                "invalid-scanline",
                "wrong-length",
            ):
                with self.subTest(mode=mode):
                    malformed = run_runner(
                        {"parsed": {"argv": [os.fspath(fake), mode], "screenshot": True}},
                        data_root,
                    )
                    self.assertTrue(malformed.get("isError", False), malformed)
                    self.assertIn("not a PNG", malformed["content"][0]["text"])
            leftovers = [path for path in candidate_root.rglob("*") if ".officecli-shot-" in path.name]
            self.assertEqual(leftovers, [])

    def test_runner_rejects_preexisting_candidate_quota_before_child_process(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            for number in range(33):
                (candidate_root / f"candidate-{number}.xlsx").write_bytes(b"x")
            marker = base / "child-ran"
            result = run_runner(
                {
                    "parsed": {
                        "argv": [
                            "-e",
                            "require('node:fs').writeFileSync(process.argv[1],'ran')",
                            os.fspath(marker),
                        ],
                        "screenshot": False,
                    }
                },
                data_root,
            )
            self.assertNotIn("error", result, result)
            self.assertTrue(result.get("isError", False), result)
            self.assertIn("candidate limits", result["content"][0]["text"])
            self.assertFalse(marker.exists())

    def test_runner_reports_candidate_quota_growth_after_child_process(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            grow = (
                "const fs=require('node:fs'),path=require('node:path');"
                "for(let index=0;index<33;index+=1){"
                "fs.writeFileSync(path.join(process.argv[1],'candidate-'+index+'.xlsx'),'x');}"
            )
            result = run_runner(
                {
                    "parsed": {
                        "argv": ["-e", grow, os.fspath(candidate_root)],
                        "screenshot": False,
                    }
                },
                data_root,
            )
            self.assertTrue(result["isError"])
            self.assertIn("candidate limits", result["content"][0]["text"])
            self.assertEqual(len(list(candidate_root.iterdir())), 33)
            marker = base / "postflight-child-ran"
            blocked = run_runner(
                {
                    "parsed": {
                        "argv": [
                            "-e",
                            "require('node:fs').writeFileSync(process.argv[1],'ran')",
                            os.fspath(marker),
                        ],
                        "screenshot": False,
                    }
                },
                data_root,
            )
            self.assertTrue(blocked["isError"])
            self.assertIn("candidate limits", blocked["content"][0]["text"])
            self.assertFalse(marker.exists())

    def test_candidate_run_reservation_fails_closed_when_quota_is_exhausted(self) -> None:
        candidates, runs = load_candidate_modules()
        cases = (
            ("files", 3, b"", 2, 10),
            ("bytes", 1, b"xxx", 10, 2),
        )
        for label, count, content, maximum_files, maximum_bytes in cases:
            with self.subTest(limit=label):
                with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
                    data_root = Path(temporary) / "plugin-data"
                    root = data_root / "officecli-candidates"
                    root.mkdir(parents=True)
                    for number in range(count):
                        (root / f"candidate-{number}.xlsx").write_bytes(content)
                    run_id = "a" * 32
                    with mock.patch.object(
                        candidates, "MAX_CANDIDATE_FILES", maximum_files
                    ):
                        with mock.patch.object(
                            candidates, "MAX_CANDIDATE_BYTES", maximum_bytes
                        ):
                            with self.assertRaisesRegex(
                                candidates.CandidateLifecycleError, "limits"
                            ):
                                runs.reserve_candidate_directory(data_root, run_id)
                    self.assertFalse((root / run_id).exists())

    def test_tool_schema_is_array_only(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            tool = run_policy({"schema": True}, Path(temporary))
        self.assertEqual(tool["name"], "officecli")
        self.assertEqual(tool["inputSchema"]["required"], ["command"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        command = tool["inputSchema"]["properties"]["command"]
        self.assertEqual(command["type"], "array")
        expected_items = {"type": "string"}
        self.assertEqual(command["items"], expected_items)
        self.assertEqual((command["minItems"], command["maxItems"]), (1, 128))

    def test_allowed_and_denied_grammar_families(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            candidate = candidate_root / "candidate.xlsx"
            candidate.write_bytes(b"candidate")
            image = candidate_root / "image.png"
            image.write_bytes(b"image")
            allowed = [
                ["validate", os.fspath(candidate), "--json"],
                ["get", os.fspath(candidate), "/Sheet1/A1", "--depth", "2"],
                ["query", os.fspath(candidate), "cell:contains(Q1)", "--find", "Q1", "--compact", "--fields", "path,text"],
                ["view", os.fspath(candidate), "text", "--start", "1", "--end", "5", "--max-lines", "50", "--cols", "A,B", "--range", "A1:B5"],
                ["view", os.fspath(candidate), "stats"],
                ["view", os.fspath(candidate), "issues", "--limit", "10"],
                ["view", os.fspath(candidate), "screenshot", "--page", "1", "--screenshot-width", "640", "--screenshot-height", "480"],
                ["set", os.fspath(candidate), "/Sheet1/A1", "--prop", "text=done", "--find", "old", "--replace", "new"],
                ["add", os.fspath(candidate), "/Sheet1", "--type", "image", "--index", "0", "--prop", f"src={image}"],
                ["remove", os.fspath(candidate), "/Sheet1/A1", "--shift", "left", "--prop", "style"],
                ["move", os.fspath(candidate), "/Sheet1/A1", "--to", "/Sheet1", "--after", "/Sheet1/A2", "--prop", "text=done"],
                ["swap", os.fspath(candidate), "/Sheet1/A1", "/Sheet1/A2"],
            ]
            for command in allowed:
                with self.subTest(command=command):
                    result = run_policy({"command": command}, data_root)
                    self.assertNotIn("error", result)
                    self.assertEqual(result["argv"][0], command[0])
                    self.assertNotIn("mcp", result["argv"])
            denied = [
                {"command": "validate candidate.xlsx"},
                {"argv": ["validate", os.fspath(candidate)]},
                {"command": ["mcp"]},
                {"command": ["create", os.fspath(candidate)]},
                {"command": ["view", os.fspath(candidate), "html"]},
                {"command": ["validate", os.fspath(candidate), "--force"]},
                {"command": ["validate", os.fspath(candidate), "--"]},
                {"command": ["@response"]},
                {"command": ["get", os.fspath(candidate), "selected"]},
                {"command": ["set", os.fspath(candidate), "/A1", "--prop", "output=elsewhere"]},
                {"command": ["add", os.fspath(candidate), "/", "--type", "image", "--from", "/A1"]},
                {"command": ["move", os.fspath(candidate), "/A1", "--index", "1", "--after", "/A2"]},
                {"command": ["validate", "-"]},
                {"command": ["validate", f"{candidate}\0bad"]},
            ]
            denied.extend(
                {"command": [verb, os.fspath(candidate)]}
                for verb in (
                    "config", "install", "skills", "plugins", "resident", "watch",
                    "server", "import", "merge", "raw", "raw-set", "add-part",
                    "refresh", "save", "close", "open", "goto", "mark", "dump", "batch",
                )
            )
            denied.extend(
                {"command": ["view", os.fspath(candidate), "text", option]}
                for option in ("--browser", "--out", "-o", "--grid", "--render", "--page-count")
            )
            for arguments in denied:
                with self.subTest(arguments=arguments):
                    self.assertIn("error", run_policy(arguments, data_root))

    def test_candidate_containment_rejects_escape_prefix_and_links(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            outside = data_root / "officecli-candidates-evil"
            outside.mkdir()
            sentinel = outside / "sentinel.xlsx"
            sentinel.write_bytes(b"outside")
            linked = candidate_root / "linked"
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            else:
                linked.symlink_to(outside, target_is_directory=True)
            try:
                paths = [
                    sentinel,
                    candidate_root / ".." / "officecli-candidates-evil" / "sentinel.xlsx",
                    linked / "sentinel.xlsx",
                ]
                before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
                for candidate in paths:
                    result = run_policy({"command": ["validate", os.fspath(candidate)]}, data_root)
                    self.assertIn("error", result)
                self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
            finally:
                if os.name == "nt" and linked.exists():
                    os.rmdir(linked)
                elif linked.is_symlink():
                    linked.unlink()

    def test_candidate_containment_rejects_hard_links(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            outside = base / "outside.xlsx"
            outside.write_bytes(b"outside-source")
            linked = candidate_root / "candidate.xlsx"
            os.link(outside, linked)
            before = hashlib.sha256(outside.read_bytes()).hexdigest()
            result = run_policy(
                {"command": ["set", os.fspath(linked), "/Sheet1/A1", "--prop", "text=changed"]},
                data_root,
            )
            self.assertIn("error", result)
            self.assertIn("hard", result["error"].lower())
            self.assertEqual(hashlib.sha256(outside.read_bytes()).hexdigest(), before)

    def test_candidate_containment_rejects_linked_plugin_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            real_data = base / "real-plugin-data"
            candidate = real_data / "officecli-candidates" / "candidate.xlsx"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"candidate")
            linked_data = base / "linked-plugin-data"
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", os.fspath(linked_data), os.fspath(real_data)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            else:
                linked_data.symlink_to(real_data, target_is_directory=True)
            try:
                linked_candidate = linked_data / "officecli-candidates" / candidate.name
                result = run_policy(
                    {"command": ["validate", os.fspath(linked_candidate)]}, linked_data
                )
                self.assertIn("error", result)
                self.assertIn("link", result["error"].lower())
            finally:
                if os.path.lexists(linked_data):
                    if os.name == "nt":
                        os.rmdir(linked_data)
                    else:
                        linked_data.unlink()

    def test_candidate_containment_rejects_linked_plugin_data_ancestor(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            real_parent = base / "real-parent"
            candidate = real_parent / "plugin-data" / "officecli-candidates" / "candidate.xlsx"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"candidate")
            linked_parent = base / "linked-parent"
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", os.fspath(linked_parent), os.fspath(real_parent)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            else:
                linked_parent.symlink_to(real_parent, target_is_directory=True)
            try:
                linked_data = linked_parent / "plugin-data"
                linked_candidate = linked_data / "officecli-candidates" / candidate.name
                result = run_policy(
                    {"command": ["validate", os.fspath(linked_candidate)]}, linked_data
                )
                self.assertIn("error", result)
                self.assertIn("link", result["error"].lower())
            finally:
                if os.path.lexists(linked_parent):
                    if os.name == "nt":
                        os.rmdir(linked_parent)
                    else:
                        linked_parent.unlink()

    def test_paths_reject_native_reparse_points_beyond_node_symlinks(self) -> None:
        script = (
            "const path=require('node:path');const paths=require(process.argv[1]);"
            "const target=process.argv[2];const injected=paths.isLinklike(target,"
            "{isSymbolicLink:()=>false},new Set([path.resolve(target)]));"
            "const native=paths.windowsReparsePoints([target]).has(path.resolve(target));"
            "process.stdout.write(JSON.stringify({injected,native}));"
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            regular = base / "regular"
            regular.mkdir()
            completed = subprocess.run(
                [NODE or "node", "-e", script, os.fspath(PATHS), os.fspath(regular)],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["injected"])
            if os.name == "nt":
                outside = base / "outside"
                outside.mkdir()
                junction = base / "junction"
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", os.fspath(junction), os.fspath(outside)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                try:
                    real = subprocess.run(
                        [NODE or "node", "-e", script, os.fspath(PATHS), os.fspath(junction)],
                        cwd=ROOT,
                        text=True,
                        encoding="utf-8",
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(real.returncode, 0, real.stderr)
                    self.assertTrue(json.loads(real.stdout)["native"])
                finally:
                    if os.path.lexists(junction):
                        os.rmdir(junction)

    @unittest.skipUnless(os.name == "nt", "Windows reparse-point timeout is Windows-specific")
    def test_windows_reparse_probe_is_timeout_bounded(self) -> None:
        script = (
            "const paths=require(process.argv[1]);let command,argumentsValue,options;"
            "const result=paths.inspectWindowsReparseBatch([process.argv[2]],(name,args,value)=>{"
            "command=name;argumentsValue=args;options=value;"
            "return {status:1,stdout:'Error 4390: ordinary path.',stderr:''};});"
            "let rejected=false;try{paths.inspectWindowsReparseBatch([process.argv[2]],()=>({status:1,stdout:'Error 5'}));}"
            "catch(error){rejected=error.message.includes('Could not inspect');}"
            "process.stdout.write(JSON.stringify({command,argumentsValue,timeout:options.timeout,empty:result.size===0,rejected}));"
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            target = Path(temporary) / "regular"
            target.mkdir()
            completed = subprocess.run(
                [NODE or "node", "-e", script, os.fspath(PATHS), os.fspath(target)],
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertTrue(result["command"].lower().endswith("fsutil.exe"))
            self.assertEqual(result["argumentsValue"], ["reparsepoint", "query", os.fspath(target)])
            self.assertTrue(result["empty"])
            self.assertTrue(result["rejected"])
            self.assertGreater(result["timeout"], 0)
            self.assertLessEqual(result["timeout"], 5_000)

    @unittest.skipUnless(os.name == "nt", "Windows reparse-point process behavior is Windows-specific")
    def test_windows_reparse_probe_completes_when_reused_in_one_node_process(self) -> None:
        script = (
            "const paths=require(process.argv[1]);const target=process.argv[2];"
            "let work=Promise.resolve();for(let i=0;i<5;i+=1){"
            "work=work.then(()=>paths.resolveCandidatePath(target));}"
            "work.then(()=>process.stdout.write('ok'))"
            ".catch(error=>{process.stderr.write(error.stack||error.message);process.exitCode=1;});"
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            target = data_root / "officecli-candidates" / "candidate.xlsx"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"candidate")
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            process = subprocess.Popen(
                [NODE or "node", "-e", script, os.fspath(PATHS), os.fspath(target)],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                cleanup = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                process.communicate(timeout=5)
                self.fail(
                    "Repeated Windows candidate-path probes did not complete within 10 seconds; "
                    f"taskkill stdout={cleanup.stdout!r} stderr={cleanup.stderr!r}"
                )
            self.assertEqual(process.returncode, 0, stderr.decode("utf-8", "replace"))
            self.assertEqual(stdout, b"ok")

    def test_policy_rejects_case_and_option_smuggling_in_get_selector(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            candidate = data_root / "officecli-candidates" / "candidate.xlsx"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"candidate")
            selectors = ("selected", "Selected", "SELECTED", "--save=C:/outside.txt")
            for selector in selectors:
                with self.subTest(selector=selector):
                    result = run_policy(
                        {"command": ["get", os.fspath(candidate), selector]}, data_root
                    )
                    self.assertIn("error", result)

    def test_policy_distinguishes_text_from_option_like_values(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            candidate = data_root / "officecli-candidates" / "candidate.xlsx"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"candidate")
            commands = (
                ["view", os.fspath(candidate), "text", "--range", "-o"],
                ["view", os.fspath(candidate), "text", "--range", "-o=outside"],
                ["add", os.fspath(candidate), "/", "--from", "-o"],
                ["add", os.fspath(candidate), "/", "--type", "image", "--after", "-o=outside"],
                ["move", os.fspath(candidate), "/A1", "--before", "-o"],
                ["move", os.fspath(candidate), "/A1", "--to", "-o=outside"],
            )
            for command in commands:
                with self.subTest(command=command):
                    self.assertIn("error", run_policy({"command": command}, data_root))
            allowed = run_policy(
                {"command": ["query", os.fspath(candidate), "cell", "--find", "-123.45"]},
                data_root,
            )
            self.assertEqual(allowed["argv"][-1], "-123.45")
            forbidden = run_policy(
                {"command": ["query", os.fspath(candidate), "cell", "--find", "--"]},
                data_root,
            )
            self.assertIn("error", forbidden)

    def test_policy_rejects_inherited_object_property_option_names(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            candidate = data_root / "officecli-candidates" / "candidate.xlsx"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"candidate")
            for option in ("constructor", "toString", "__proto__"):
                with self.subTest(option=option):
                    result = run_policy(
                        {
                            "command": [
                                "query",
                                os.fspath(candidate),
                                "cell",
                                option,
                                "attacker-value",
                            ]
                        },
                        data_root,
                    )
                    self.assertIn("error", result)

    def test_jsonrpc_lifecycle_errors_notifications_and_recovery(self) -> None:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 4, "method": "unknown"},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "officecli", "arguments": {"fail": "policy"}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "officecli", "arguments": {"fail": "internal"}}},
            {"jsonrpc": "2.0", "method": "unknown-notification"},
            {"jsonrpc": "2.0", "id": 8, "method": "ping"},
        ]
        payload = b"not-json\n" + b"\n".join(
            json.dumps(message).encode() for message in messages
        ) + b"\n"
        completed = run_jsonrpc(payload)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(
            [response.get("id") for response in responses],
            [None, 1, 2, 3, 4, 5, 6, 7, 8],
        )
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["error"]["code"], -32600)
        self.assertEqual(responses[4]["error"]["code"], -32601)
        self.assertEqual(responses[5]["error"]["code"], -32602)
        self.assertTrue(responses[6]["result"]["isError"])
        self.assertEqual(responses[7]["error"]["code"], -32603)
        self.assertEqual(responses[8]["result"], {})
        self.assertEqual(completed.stderr, b"OfficeCLI adapter error: boom\n")

    def test_jsonrpc_rejects_oversized_and_invalid_messages(self) -> None:
        initialize = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        ).encode()
        ping = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}).encode()
        payload = initialize + b"\n" + (b"x" * (1024 * 1024 + 1)) + b"\n[]\n" + ping + b"\n"
        completed = run_jsonrpc(payload)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([response.get("id") for response in responses], [1, None, None, 2])
        self.assertEqual(responses[1]["error"]["code"], -32700)
        self.assertEqual(responses[2]["error"]["code"], -32600)
        self.assertEqual(responses[3]["result"], {})
        self.assertEqual(completed.stderr, b"")

    def test_jsonrpc_rejects_invalid_ids_without_handlers_and_recovers(self) -> None:
        invalid_call = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "officecli",
                "arguments": {"fail": "internal"},
            },
        }
        messages = [
            {"jsonrpc": "2.0", "id": {}, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {}},
            {**invalid_call, "id": {}},
            {**invalid_call, "id": []},
            {**invalid_call, "id": True},
        ]
        lines = [json.dumps(message).encode() for message in messages]
        lines.append(
            b'{"jsonrpc":"2.0","id":1e400,"method":"tools/call",'
            b'"params":{"name":"officecli","arguments":{"fail":"internal"}}}'
        )
        lines.extend(
            json.dumps(message).encode()
            for message in (
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "tools/call",
                    "params": {
                        "name": "officecli",
                        "arguments": {"reportCalls": True},
                    },
                },
                {"jsonrpc": "2.0", "id": None, "method": "ping"},
            )
        )
        completed = run_jsonrpc(b"\n".join(lines) + b"\n")
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(
            [response.get("id") for response in responses],
            [None, "init", None, None, None, None, 0, None],
        )
        self.assertEqual(responses[1]["result"]["protocolVersion"], "2024-11-05")
        for response in (responses[0], *responses[2:6]):
            self.assertEqual(response["error"]["code"], -32600)
        self.assertEqual(responses[6]["result"], {"calls": 1})
        self.assertEqual(responses[7]["result"], {})
        self.assertEqual(completed.stderr, b"")

    def test_lock_pins_correct_release(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["project"], "iOfficeAI/OfficeCLI")
        self.assertEqual(lock["version"], "1.0.135")
        self.assertEqual(
            lock["sourceCommit"],
            "d2d9c60f44537004c3e1f46680c24ea38d9659c2",
        )
        self.assertIn(lock["sourceCommit"], lock["license"]["licenseUrl"])
        self.assertIn(lock["sourceCommit"], lock["license"]["noticeUrl"])
        self.assertEqual(lock["license"]["spdx"], "Apache-2.0")
        self.assertEqual(lock["mcpProtocolVersion"], "2024-11-05")
        self.assertEqual(
            set(lock["assets"]),
            {
                "windows-x64",
                "windows-arm64",
                "macos-x64",
                "macos-arm64",
                "linux-x64",
                "linux-arm64",
                "linux-alpine-x64",
                "linux-alpine-arm64",
            },
        )
        for asset in lock["assets"].values():
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("/releases/download/v1.0.135/", asset["url"])
            self.assertTrue(asset["url"].endswith(asset["filename"]))

    def test_child_environment_contains_only_platform_and_managed_officecli_keys(self) -> None:
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        with mock.patch.dict(
            os.environ,
            {
                "OFFICECLI_SKIP_UPDATE": "0",
                "OFFICECLI_BATCH_ALLOW_STDIN_REDIRECT": "1",
                "OFFICECLI_UNTRUSTED": "present",
                "ARBITRARY_SENTINEL_SECRET": "not-for-officecli",
                "PLUGIN_DATA": "not-for-officecli",
            },
            clear=False,
        ):
            environment = runtime.side_effect_free_environment()
        officecli = {
            key: value
            for key, value in environment.items()
            if key.startswith("OFFICECLI_")
        }
        self.assertEqual(
            officecli,
            {
                "OFFICECLI_SKIP_UPDATE": "1",
                "OFFICECLI_NO_AUTO_INSTALL": "1",
                "OFFICECLI_NO_AUTO_RESIDENT": "1",
            },
        )
        self.assertNotIn("ARBITRARY_SENTINEL_SECRET", environment)
        self.assertNotIn("PLUGIN_DATA", environment)

    def test_runtime_verification_child_drops_ambient_credentials_and_plugin_data(self) -> None:
        load_manager()
        runtime = sys.modules["officecli_runtime"]
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            capture = base / "runtime-child-environment.json"
            probe = base / "capture_environment.py"
            probe.write_text(
                "import json\n"
                "import os\n"
                "from pathlib import Path\n"
                "names = ('ARBITRARY_SENTINEL_SECRET', 'PLUGIN_DATA')\n"
                f"Path({os.fspath(capture)!r}).write_text("
                "json.dumps({name: name in os.environ for name in names}), "
                "encoding='utf-8')\n"
                "print('1.0.135')\n",
                encoding="utf-8",
            )
            if os.name == "nt":
                binary = base / "fake-officecli.cmd"
                binary.write_text(
                    f'@echo off\r\n"{sys.executable}" "{probe}" %*\r\n',
                    encoding="utf-8",
                )
            else:
                binary = base / "fake-officecli"
                binary.write_text(
                    f"#!{sys.executable}\n{probe.read_text(encoding='utf-8')}",
                    encoding="utf-8",
                )
                binary.chmod(0o700)
            with mock.patch.dict(
                os.environ,
                {
                    "ARBITRARY_SENTINEL_SECRET": "not-for-officecli",
                    "PLUGIN_DATA": "not-for-officecli",
                },
                clear=False,
            ):
                self.assertIn("1.0.135", runtime.verify_version(binary, "1.0.135"))
            visible = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(
            visible,
            {"ARBITRARY_SENTINEL_SECRET": False, "PLUGIN_DATA": False},
        )

    def test_manager_prunes_only_safe_old_siblings(self) -> None:
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        binary = b"verified-officecli"
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            lock = runtime.load_lock()
            asset = lock["assets"][runtime.current_asset_key()]
            asset["sha256"] = hashlib.sha256(binary).hexdigest()
            current = runtime.managed_binary_path(lock, asset, data_root)
            current.parent.mkdir(parents=True)
            current.write_bytes(binary)
            old = data_root / "runtimes" / "officecli" / "0.9.0"
            old.mkdir(parents=True)
            (old / "stale.bin").write_bytes(b"stale")
            sentinel = data_root / "outside-sentinel.bin"
            sentinel.write_bytes(b"outside")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                with mock.patch.object(runtime, "load_lock", return_value=lock):
                    first = manager.install_runtime(True)
                    second = manager.install_runtime(True)
            self.assertEqual(first["status"], "already_installed")
            self.assertEqual(second["status"], "already_installed")
            self.assertTrue(current.is_file())
            self.assertFalse(old.exists())
            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_manager_preserves_old_runtime_when_post_install_status_is_unverified(self) -> None:
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            lock = runtime.load_lock()
            _asset_key, asset = runtime.selected_asset(lock)
            target = runtime.managed_binary_path(lock, asset, data_root)
            previous = data_root / "runtimes" / "officecli" / "0.9.0"
            previous.mkdir(parents=True)
            (previous / "officecli.bin").write_bytes(b"previous-verified-runtime")
            initial = {
                "version": lock["version"],
                "path": os.fspath(target),
                "installed": False,
                "integrity": "missing",
            }
            unverified = {**initial, "integrity": "checksum_mismatch"}
            pruned: list[str] = []

            def download(_url: str, destination: Path) -> None:
                destination.write_bytes(b"downloaded-runtime")

            with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                with mock.patch.object(runtime, "load_lock", return_value=lock):
                    with mock.patch.object(runtime, "runtime_status", side_effect=[initial, unverified]):
                        with mock.patch.object(runtime, "download_asset", side_effect=download):
                            with mock.patch.object(runtime, "sha256_file", return_value=asset["sha256"]):
                                with mock.patch.object(runtime, "verify_version", return_value="1.0.135"):
                                    with mock.patch.object(
                                        runtime,
                                        "prune_old_versions",
                                        side_effect=lambda _version: pruned.append(_version),
                                    ):
                                        with self.assertRaises(manager.OfficeCLIManagerError):
                                            manager.install_runtime(True)
            self.assertTrue(previous.is_dir())
            self.assertEqual(pruned, [])

    def test_manager_refuses_linked_runtime_paths(self) -> None:
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            runtime_root = data_root / "runtimes" / "officecli"
            current = runtime_root / "1.0.135"
            current.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.bin"
            sentinel.write_bytes(b"outside")
            linked = runtime_root / "0.9.0"
            if os.name == "nt":
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
            else:
                linked.symlink_to(outside, target_is_directory=True)
            try:
                with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                    with self.assertRaises(manager.OfficeCLIManagerError):
                        runtime.prune_old_versions("1.0.135")
                self.assertEqual(sentinel.read_bytes(), b"outside")
                self.assertTrue(current.is_dir())
            finally:
                if os.name == "nt" and linked.exists():
                    os.rmdir(linked)
                elif linked.is_symlink():
                    linked.unlink()

    def test_manager_refuses_every_linked_runtime_ancestor(self) -> None:
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        lock = runtime.load_lock()
        operations = {
            "status": lambda: manager.runtime_status(lock),
            "prune": lambda: runtime.prune_old_versions("1.0.135"),
            "uninstall": manager.uninstall_runtime,
            "install": lambda: manager.install_runtime(True),
        }
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            for ancestor in ("plugin-data-parent", "plugin-data", "runtimes", "officecli"):
                for operation_name, operation in operations.items():
                    with self.subTest(ancestor=ancestor, operation=operation_name):
                        case = base / f"{ancestor}-{operation_name}"
                        data_root = case / "plugin-data"
                        outside = case / "outside"
                        if ancestor == "plugin-data-parent":
                            linked = case / "linked-parent"
                            data_root = linked / "plugin-data"
                            version = outside / "plugin-data" / "runtimes" / "officecli" / "0.9.0"
                        elif ancestor == "plugin-data":
                            linked = data_root
                            version = outside / "runtimes" / "officecli" / "0.9.0"
                        elif ancestor == "runtimes":
                            linked = data_root / "runtimes"
                            version = outside / "officecli" / "0.9.0"
                        else:
                            linked = data_root / "runtimes" / "officecli"
                            version = outside / "0.9.0"
                        linked.parent.mkdir(parents=True)
                        version.mkdir(parents=True)
                        sentinel = version / "sentinel.bin"
                        sentinel.write_bytes(b"outside")
                        before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
                        if os.name == "nt":
                            completed = subprocess.run(
                                ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                                text=True,
                                capture_output=True,
                                check=False,
                            )
                            self.assertEqual(completed.returncode, 0, completed.stderr)
                        else:
                            linked.symlink_to(outside, target_is_directory=True)
                        try:
                            with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                                with mock.patch.object(
                                    runtime,
                                    "download_asset",
                                    side_effect=AssertionError("linked install reached download"),
                                ):
                                    with self.assertRaises(manager.OfficeCLIManagerError):
                                        operation()
                            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)
                            self.assertTrue(os.path.lexists(linked))
                        finally:
                            if os.path.lexists(linked):
                                if os.name == "nt":
                                    os.rmdir(linked)
                                else:
                                    linked.unlink()

    def test_uninstall_removes_only_managed_versions(self) -> None:
        manager = load_manager()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            runtime_root = data_root / "runtimes" / "officecli"
            for version in ("0.9.0", "1.0.135"):
                directory = runtime_root / version
                directory.mkdir(parents=True)
                (directory / "officecli.bin").write_bytes(version.encode())
            sentinel = data_root / "outside-sentinel.bin"
            sentinel.write_bytes(b"outside")
            with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                result = manager.uninstall_runtime()
            self.assertEqual(result["status"], "uninstalled")
            self.assertFalse(runtime_root.exists())
            self.assertFalse((data_root / "runtimes").exists())
            self.assertEqual(sentinel.read_bytes(), b"outside")

    def test_manager_status_is_read_only_and_reports_missing_runtime(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            completed = subprocess.run(
                [sys.executable, os.fspath(MANAGER), "status"],
                cwd=ROOT,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            status = json.loads(completed.stdout)
            self.assertFalse(status["installed"])
            self.assertEqual(status["version"], "1.0.135")
            self.assertFalse(data_root.exists())

    def test_manager_and_adapter_require_plugin_data_environment(self) -> None:
        self.assertIsNotNone(NODE, "Node.js is required for the OfficeCLI adapter tests")
        environment = os.environ.copy()
        environment.pop("PLUGIN_DATA", None)
        environment.pop("CLAUDE_PLUGIN_DATA", None)
        manager = subprocess.run(
            [sys.executable, os.fspath(MANAGER), "status"],
            cwd=ROOT,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(manager.returncode, 2)
        self.assertIn("PLUGIN_DATA", json.loads(manager.stdout)["error"])
        adapter = subprocess.run(
            [NODE or "node", os.fspath(LAUNCHER)],
            cwd=ROOT,
            env=environment,
            input="",
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(adapter.returncode, 2)
        self.assertIn("PLUGIN_DATA", adapter.stderr)

    def test_claude_plugin_data_alone_does_not_authorize_owned_runtime_paths(self) -> None:
        self.assertIsNotNone(NODE, "Node.js is required for the OfficeCLI adapter tests")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "claude-only-data"
            environment = os.environ.copy()
            environment.pop("PLUGIN_DATA", None)
            environment["CLAUDE_PLUGIN_DATA"] = os.fspath(data_root)
            manager = subprocess.run(
                [sys.executable, os.fspath(MANAGER), "status"],
                cwd=ROOT,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(manager.returncode, 2)
            self.assertIn("PLUGIN_DATA", json.loads(manager.stdout)["error"])
            adapter = subprocess.run(
                [NODE or "node", os.fspath(LAUNCHER)],
                cwd=ROOT,
                env=environment,
                input="",
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
        self.assertEqual(adapter.returncode, 2)
        self.assertIn("PLUGIN_DATA", adapter.stderr)
        for owned in (LAUNCHER, PATHS, ROOT / "scripts" / "officecli_runtime.py"):
            self.assertNotIn("CLAUDE_PLUGIN_DATA", owned.read_text(encoding="utf-8"))

    def test_adapter_rejects_linked_runtime_ancestors_before_checksum(self) -> None:
        self.assertIsNotNone(NODE, "Node.js is required for the OfficeCLI adapter tests")
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        lock = runtime.load_lock()
        asset = lock["assets"][runtime.current_asset_key()]
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            for ancestor in ("plugin-data-parent", "plugin-data", "runtimes", "officecli"):
                with self.subTest(ancestor=ancestor):
                    case = base / ancestor
                    data_root = case / "plugin-data"
                    outside = case / "outside"
                    if ancestor == "plugin-data-parent":
                        linked = case / "linked-parent"
                        data_root = linked / "plugin-data"
                        binary = (
                            outside
                            / "plugin-data"
                            / "runtimes"
                            / "officecli"
                            / "1.0.135"
                            / asset["filename"]
                        )
                    elif ancestor == "plugin-data":
                        linked = data_root
                        binary = (
                            outside
                            / "runtimes"
                            / "officecli"
                            / "1.0.135"
                            / asset["filename"]
                        )
                    elif ancestor == "runtimes":
                        linked = data_root / "runtimes"
                        binary = outside / "officecli" / "1.0.135" / asset["filename"]
                    else:
                        linked = data_root / "runtimes" / "officecli"
                        binary = outside / "1.0.135" / asset["filename"]
                    linked.parent.mkdir(parents=True)
                    binary.parent.mkdir(parents=True)
                    binary.write_bytes(b"outside-runtime")
                    before = hashlib.sha256(binary.read_bytes()).hexdigest()
                    if os.name == "nt":
                        created = subprocess.run(
                            ["cmd", "/c", "mklink", "/J", os.fspath(linked), os.fspath(outside)],
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        self.assertEqual(created.returncode, 0, created.stderr)
                    else:
                        linked.symlink_to(outside, target_is_directory=True)
                    try:
                        environment = os.environ.copy()
                        environment["PLUGIN_DATA"] = os.fspath(data_root)
                        completed = subprocess.run(
                            [NODE or "node", os.fspath(LAUNCHER)],
                            cwd=ROOT,
                            env=environment,
                            input="",
                            text=True,
                            encoding="utf-8",
                            capture_output=True,
                            check=False,
                        )
                        self.assertEqual(completed.returncode, 2)
                        self.assertIn("linked", completed.stderr)
                        self.assertIn("invalid", completed.stderr)
                        self.assertNotIn("checksum mismatch", completed.stderr)
                        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), before)
                        self.assertTrue(os.path.lexists(linked))
                    finally:
                        if os.path.lexists(linked):
                            if os.name == "nt":
                                os.rmdir(linked)
                            else:
                                linked.unlink()

    def test_manager_and_adapter_reject_hard_linked_runtime_binary(self) -> None:
        self.assertIsNotNone(NODE, "Node.js is required for the OfficeCLI adapter tests")
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        lock = runtime.load_lock()
        asset = lock["assets"][runtime.current_asset_key()]
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            outside = Path(temporary) / "outside-officecli.bin"
            outside.write_bytes(b"outside-runtime")
            binary = runtime.managed_binary_path(lock, asset, data_root)
            binary.parent.mkdir(parents=True)
            os.link(outside, binary)
            before = hashlib.sha256(outside.read_bytes()).hexdigest()
            with self.assertRaisesRegex(manager.OfficeCLIManagerError, "hard"):
                runtime.runtime_status(lock, data_root)
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            completed = subprocess.run(
                [NODE or "node", os.fspath(LAUNCHER)],
                cwd=ROOT,
                env=environment,
                input="",
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("hard", completed.stderr.lower())
            self.assertNotIn("checksum mismatch", completed.stderr.lower())
            self.assertEqual(hashlib.sha256(outside.read_bytes()).hexdigest(), before)

    def test_manager_detects_a_tampered_managed_binary(self) -> None:
        manager = load_manager()
        runtime = sys.modules["officecli_runtime"]
        lock = runtime.load_lock()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary)
            asset_key = runtime.current_asset_key()
            asset = lock["assets"][asset_key]
            target = runtime.managed_binary_path(lock, asset, data_root)
            target.parent.mkdir(parents=True)
            target.write_bytes(b"not-officecli")
            status = runtime.runtime_status(lock, data_root)
            self.assertFalse(status["installed"])
            self.assertEqual(status["integrity"], "checksum_mismatch")
            self.assertEqual(
                status["actual_sha256"], hashlib.sha256(b"not-officecli").hexdigest()
            )

    def test_mcp_launcher_fails_closed_when_runtime_is_missing(self) -> None:
        self.assertIsNotNone(NODE, "Node.js is required for the OfficeCLI adapter tests")
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            environment = os.environ.copy()
            environment["PLUGIN_DATA"] = os.fspath(data_root)
            completed = subprocess.run(
                [NODE or "node", os.fspath(LAUNCHER)],
                cwd=ROOT,
                env=environment,
                input="",
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("officecli_manager.py", completed.stderr)
            self.assertIn("install --accept-download", completed.stderr)
            self.assertFalse(data_root.exists())


if __name__ == "__main__":
    unittest.main()
