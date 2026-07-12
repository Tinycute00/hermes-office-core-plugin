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


def run_jsonrpc(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    self_test = (
        "const { runProtocol } = require(process.argv[1]);"
        "runProtocol({"
        "tool:{name:'officecli',description:'test',inputSchema:{type:'object'}},"
        "callTool:async(args)=>{"
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
        "const runner=require(process.argv[1]);let input='';"
        "process.stdin.setEncoding('utf8');process.stdin.on('data',c=>input+=c);"
        "process.stdin.on('end',async()=>{const c=JSON.parse(input);try{"
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
        timeout=15,
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

    def test_screenshot_success_and_failure_cleanup(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"minimal"
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            base = Path(temporary)
            data_root = base / "plugin-data"
            candidate_root = data_root / "officecli-candidates"
            candidate_root.mkdir(parents=True)
            fake = base / "fake-screenshot.cjs"
            fake.write_text(
                "const fs=require('node:fs');const args=process.argv.slice(2);"
                "const out=args[args.indexOf('--out')+1];"
                "if(args.includes('fail')){fs.writeFileSync(out,'bad');process.exit(2);}"
                "fs.writeFileSync(out,Buffer.from('89504e470d0a1a0a6d696e696d616c','hex'));",
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
            leftovers = [path for path in candidate_root.rglob("*") if ".officecli-shot-" in path.name]
            self.assertEqual(leftovers, [])

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

    def test_child_environment_contains_only_managed_officecli_keys(self) -> None:
        manager = load_manager()
        with mock.patch.dict(
            os.environ,
            {
                "OFFICECLI_SKIP_UPDATE": "0",
                "OFFICECLI_BATCH_ALLOW_STDIN_REDIRECT": "1",
                "OFFICECLI_UNTRUSTED": "present",
                "UNRELATED": "preserved",
            },
            clear=False,
        ):
            environment = manager.side_effect_free_environment()
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
        self.assertEqual(environment["UNRELATED"], "preserved")

    def test_manager_prunes_only_safe_old_siblings(self) -> None:
        manager = load_manager()
        binary = b"verified-officecli"
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary) / "plugin-data"
            lock = manager.load_lock()
            asset = lock["assets"][manager.current_asset_key()]
            asset["sha256"] = hashlib.sha256(binary).hexdigest()
            current = manager.managed_binary_path(lock, asset, data_root)
            current.parent.mkdir(parents=True)
            current.write_bytes(binary)
            old = data_root / "runtimes" / "officecli" / "0.9.0"
            old.mkdir(parents=True)
            (old / "stale.bin").write_bytes(b"stale")
            sentinel = data_root / "outside-sentinel.bin"
            sentinel.write_bytes(b"outside")
            before = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            runtime = sys.modules["officecli_runtime"]
            with mock.patch.dict(os.environ, {"PLUGIN_DATA": os.fspath(data_root)}):
                with mock.patch.object(runtime, "load_lock", return_value=lock):
                    first = manager.install_runtime(True)
                    second = manager.install_runtime(True)
            self.assertEqual(first["status"], "already_installed")
            self.assertEqual(second["status"], "already_installed")
            self.assertTrue(current.is_file())
            self.assertFalse(old.exists())
            self.assertEqual(hashlib.sha256(sentinel.read_bytes()).hexdigest(), before)

    def test_manager_refuses_linked_runtime_paths(self) -> None:
        manager = load_manager()
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
                        manager.prune_old_versions("1.0.135")
                self.assertEqual(sentinel.read_bytes(), b"outside")
                self.assertTrue(current.is_dir())
            finally:
                if os.name == "nt" and linked.exists():
                    os.rmdir(linked)
                elif linked.is_symlink():
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

    def test_manager_detects_a_tampered_managed_binary(self) -> None:
        manager = load_manager()
        lock = manager.load_lock()
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            data_root = Path(temporary)
            asset_key = manager.current_asset_key()
            asset = lock["assets"][asset_key]
            target = manager.managed_binary_path(lock, asset, data_root)
            target.parent.mkdir(parents=True)
            target.write_bytes(b"not-officecli")
            status = manager.runtime_status(lock, data_root)
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
