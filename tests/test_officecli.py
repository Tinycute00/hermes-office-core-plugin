from __future__ import annotations

# noqa: SIZE_OK - plan-mandated consolidated integration suite for one OfficeCLI surface.

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
CANDIDATES = ROOT / "skills" / "office-os" / "scripts" / "office_candidates.py"
CANDIDATE_RUNS = (
    ROOT / "skills" / "office-os" / "scripts" / "office_candidate_runs.py"
)
NODE = shutil.which("node")


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


def run_runner(configuration: dict, data_root: Path) -> dict:  # noqa: DICT_OK
    script = (
        "let input='';"
        "process.stdin.setEncoding('utf8');process.stdin.on('data',c=>input+=c);"
        "process.stdin.on('end',async()=>{const c=JSON.parse(input);try{"
        "if(c.nonsettlingKill){const {EventEmitter}=require('node:events');"
        "const childProcess=require('node:child_process');let launches=0;"
        "childProcess.spawn=()=>{const child=new EventEmitter();child.pid=++launches;"
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
                    "params": {"name": "officecli", "arguments": {"command": ["validate", os.fspath(candidate), "--json"]}},
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
                self.assertTrue(result["isError"])
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
            for mode in ("signature", "truncated"):
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
            self.assertTrue(result["isError"])
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
