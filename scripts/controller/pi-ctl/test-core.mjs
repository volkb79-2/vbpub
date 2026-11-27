import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { EventEmitter } from "node:events";

const { createController } = await import(process.env.PI_CTL_CORE_MODULE ?? "./core.mjs");

function fixture(envExtra = {}) {
	const temp = fs.mkdtempSync(path.join(os.tmpdir(), "pi-ctl-core-"));
	const repo = path.join(temp, "repo");
	const root = path.join(temp, "state");
	const wrapper = path.join(temp, "pi-pidns-bash");
	fs.mkdirSync(repo, { mode: 0o700 });
	fs.writeFileSync(path.join(repo, ".git"), "gitdir: fake\n", { mode: 0o600 });
	fs.writeFileSync(wrapper, "#!/bin/sh\n", { mode: 0o700 });
	fs.chmodSync(wrapper, 0o700);
	const calls = [];
	const events = new Map();
	const tools = new Map();
	let sendsBeforeThrow = 0;
	let onSend = null;
	let id = 0;
	const fakePi = {
		on(event, handler) {
			events.set(event, handler);
			return () => events.delete(event);
		},
		registerTool(tool) {
			tools.set(tool.name, tool);
		},
		sendMessage(message, options) {
			onSend?.(message, options);
			if (sendsBeforeThrow > 0) {
				sendsBeforeThrow--;
				throw new Error("injected failure");
			}
			calls.push({ message, options });
		},
	};
	let currentTime = 1_800_000_000_000;
	const timeoutTasks = new Set();
	const intervalTasks = new Set();
	const timers = {
		setInterval(fn, ms) {
			const task = { fn, ms, interval: true };
			intervalTasks.add(task);
			return task;
		},
		clearInterval(task) { intervalTasks.delete(task); },
		setTimeout(fn, ms) {
			const task = { fn, ms, interval: false };
			timeoutTasks.add(task);
			return task;
		},
		clearTimeout(task) { timeoutTasks.delete(task); },
	};
	let spawnImpl = () => { throw new Error("unexpected spawn"); };
	const env = {
		HOME: temp,
		PI_CTL_NAME: "receiver",
		PI_CTL_ROOT: root,
		...envExtra,
	};
	const controller = createController(fakePi, {
		env,
		cwd: repo,
		wrapperPath: wrapper,
		spawn: (...args) => spawnImpl(...args),
		pid: 987654321,
		now: () => currentTime,
		makeId: () => `id-${++id}`,
		timers,
	}, { root, name: env.PI_CTL_NAME });
	return {
		temp,
		repo,
		root,
		wrapper,
		env,
		calls,
		events,
		tools,
		controller,
		timers,
		setTime(value) { currentTime = value; },
		throwOnSend(count = 1) { sendsBeforeThrow = count; },
		setOnSend(fn) { onSend = fn; },
		setSpawn(fn) { spawnImpl = fn; },
		makeChild() {
			const child = new EventEmitter();
			child.stdout = new EventEmitter();
			child.stderr = new EventEmitter();
			child.signals = [];
			child.kill = (signal) => { child.signals.push(signal); return true; };
			return child;
		},
		fireTimeout(task) {
			assert.ok(timeoutTasks.has(task));
			timeoutTasks.delete(task);
			task.fn();
		},
		findTimeout(ms) { return [...timeoutTasks].find((task) => task.ms === ms); },
		get timeoutTasks() { return [...timeoutTasks]; },
		get intervalTasks() { return [...intervalTasks]; },
		cleanup() { fs.rmSync(temp, { recursive: true, force: true }); },
	};
}

test("requires a safe controller name and an absolute state root outside the repository", (t) => {
	const f = fixture();
	t.after(f.cleanup);
	assert.throws(() => createController(fakeApiNoop(), { env: { ...f.env, PI_CTL_NAME: "../escape" }, cwd: f.repo }, { root: f.root }), /PI_CTL_NAME/);
	assert.throws(() => createController(fakeApiNoop(), { env: { ...f.env, PI_CTL_ROOT: "relative" }, cwd: f.repo }), /absolute/);
	assert.throws(() => createController(fakeApiNoop(), { env: f.env, cwd: f.repo }, { root: path.join(f.repo, "inside") }), /outside the repository/);
	assert.equal(f.controller.config.pollMs, 1000);
	assert.equal(fs.statSync(f.root).mode & 0o777, 0o700);
	assert.equal(fs.statSync(path.join(f.root, "inbox", "receiver")).mode & 0o777, 0o700);
});

test("send_message validates recipient names, rate-limits peers, and never logs message bodies", async (t) => {
	const f = fixture({ PI_CTL_SEND_LIMIT: "1", PI_CTL_LOOP_WINDOW_SECONDS: "60" });
	t.after(f.cleanup);
	const tool = f.tools.get("send_message");
	assert.ok(tool);
	const bad = await tool.execute("call-1", { to: "../main", message: "no" });
	assert.match(bad.content[0].text, /refused/);
	const first = await tool.execute("call-2", { to: "main", message: "private-body-marker" });
	assert.match(first.content[0].text, /written to main/);
	const second = await tool.execute("call-3", { to: "main", message: "second" });
	assert.match(second.content[0].text, /rate limit/);
	const inbox = path.join(f.root, "inbox", "main");
	const messages = fs.readdirSync(inbox);
	assert.equal(messages.length, 1);
	assert.equal(fs.statSync(path.join(inbox, messages[0])).mode & 0o777, 0o600);
	const log = fs.readFileSync(path.join(f.root, "events", "receiver.jsonl"), "utf8");
	assert.doesNotMatch(log, /private-body-marker/);
});

test("claims inbox messages before injecting and retains a claim when injection fails", (t) => {
	const f = fixture();
	t.after(f.cleanup);
	const sender = createController({ ...fakeApiNoop() }, {
		env: { HOME: f.temp, PI_CTL_NAME: "peer", PI_CTL_ROOT: f.root },
		cwd: f.repo,
		pid: 987654322,
		now: () => 1_800_000_000_000,
		makeId: () => "sender-id",
		timers: f.timers,
	}, { root: f.root, name: "peer" });
	sender.writePeerMessage("receiver", "from peer");
	f.throwOnSend();
	f.controller.pollInbox();
	assert.equal(fs.readdirSync(path.join(f.root, "inbox", "receiver")).length, 0);
	assert.equal(fs.readdirSync(path.join(f.root, "processing", "receiver")).length, 1);
	// The injected fake records delivery only after the retry has read the claim.
	f.controller.pollInbox();
	assert.equal(f.calls.length, 1);
	assert.match(f.calls[0].message.content, /Peer message from peer/);
	assert.match(f.calls[0].message.content, /not operator approval/);
	assert.match(f.calls[0].message.content, /from peer/);
	assert.equal(fs.readdirSync(path.join(f.root, "processing", "receiver")).length, 0);
});

test("truncates oversized input with a visible marker and skips symlinks", (t) => {
	const f = fixture({ PI_CTL_MAX_MESSAGE_BYTES: "256" });
	t.after(f.cleanup);
	const senderPi = fakeApiNoop();
	const sender = createController(senderPi, {
		env: { HOME: f.temp, PI_CTL_NAME: "peer", PI_CTL_ROOT: f.root, PI_CTL_MAX_SEND_BYTES: "2048" },
		cwd: f.repo,
		pid: 987654322,
		now: () => 1_800_000_000_000,
		makeId: () => "sender-id",
		timers: f.timers,
	}, { root: f.root, name: "peer" });
	sender.writePeerMessage("receiver", "x".repeat(600));
	const inbox = path.join(f.root, "inbox", "receiver");
	fs.symlinkSync("/etc/passwd", path.join(inbox, "symlink.msg"));
	f.controller.pollInbox();
	assert.equal(f.calls.length, 1);
	assert.match(f.calls[0].message.content, /Message truncated at 256 bytes/);
	assert.ok(fs.lstatSync(path.join(inbox, "symlink.msg")).isSymbolicLink());
});

test("holds over-limit peer messages with one notice and delivers after the window", (t) => {
	const f = fixture({ PI_CTL_INBOUND_LIMIT: "1", PI_CTL_LOOP_WINDOW_SECONDS: "10" });
	t.after(f.cleanup);
	const sender = createController(fakeApiNoop(), {
		env: { HOME: f.temp, PI_CTL_NAME: "peer", PI_CTL_ROOT: f.root },
		cwd: f.repo,
		pid: 987654322,
		now: () => 1_800_000_000_000,
		makeId: (() => { let n = 0; return () => `peer-${++n}`; })(),
		timers: f.timers,
	}, { root: f.root, name: "peer" });
	sender.writePeerMessage("receiver", "first");
	sender.writePeerMessage("receiver", "second");
	f.controller.pollInbox();
	assert.equal(f.calls.filter((call) => call.message.customType === "pi-ctl-inbox").length, 1);
	assert.equal(f.calls.filter((call) => call.message.customType === "pi-ctl-notice").length, 1);
	f.controller.pollInbox();
	assert.equal(f.calls.filter((call) => call.message.customType === "pi-ctl-notice").length, 1);
	f.setTime(1_800_000_011_000);
	f.controller.pollInbox();
	assert.equal(f.calls.filter((call) => call.message.customType === "pi-ctl-inbox").length, 2);
	assert.equal(fs.readdirSync(path.join(f.root, "processing", "receiver")).length, 0);
});

test("wake-ups persist across restart, fire overdue once, and can be listed or cancelled", async (t) => {
	const f = fixture();
	t.after(f.cleanup);
	const scheduled = await f.tools.get("schedule_wakeup").execute("w1", { delay_seconds: 10, prompt: "resume this work" });
	const id = /Wake-up (wake-[^ ]+) scheduled/.exec(scheduled.content[0].text)?.[1];
	assert.ok(id);
	const listed = await f.tools.get("list_wakeups").execute("w2", {});
	assert.match(listed.content[0].text, new RegExp(id));
	const cancelled = await f.tools.get("cancel_wakeup").execute("w3", { id });
	assert.match(cancelled.content[0].text, /cancelled/);
	assert.equal(JSON.parse(fs.readFileSync(path.join(f.root, "wakeups", "receiver.json"), "utf8")).length, 0);

	const persisted = f.controller.scheduleWakeup(10, "original prompt");
	f.setTime(persisted.dueAt + 1);
	const restartedMessages = [];
	let restartedHandlers = new Map();
	const restartedPi = {
		on(event, handler) { restartedHandlers.set(event, handler); },
		registerTool() {},
		sendMessage(message, options) {
			assert.equal(JSON.parse(fs.readFileSync(path.join(f.root, "wakeups", "receiver.json"), "utf8")).length, 0,
				"the persisted wake-up is removed before injection");
			restartedMessages.push({ message, options });
		},
	};
	const restarted = createController(restartedPi, {
		env: f.env,
		cwd: f.repo,
		wrapperPath: f.wrapper,
		pid: 987654322,
		now: () => f.controller.config.loopWindowMs + persisted.dueAt + 1,
		timers: f.timers,
		makeId: () => "restart-id",
	}, { root: f.root, name: "receiver", wrapperPath: f.wrapper });
	assert.ok(restarted);
	restartedHandlers.get("session_start")();
	restartedHandlers.get("session_start")();
	assert.equal(restartedMessages.filter(({ message }) => message.customType === "pi-ctl-wakeup").length, 1);
	assert.match(restartedMessages[0].message.content, /original prompt/);
});

test("wake-up injection failure cannot make an overdue wake-up fire twice", (t) => {
	const f = fixture();
	t.after(f.cleanup);
	const wakeup = f.controller.scheduleWakeup(10, "wake once");
	f.setTime(wakeup.dueAt + 1);
	f.throwOnSend();
	f.controller.armWakeups();
	assert.equal(JSON.parse(fs.readFileSync(path.join(f.root, "wakeups", "receiver.json"), "utf8")).length, 0);
	f.controller.armWakeups();
	assert.equal(f.calls.length, 0);
});

test("monitor requires the executable PID namespace wrapper and reports isolation refusal", async (t) => {
	const f = fixture();
	t.after(f.cleanup);
	let spawnCalls = 0;
	f.setSpawn(() => { spawnCalls++; return f.makeChild(); });
	fs.unlinkSync(f.wrapper);
	const missing = await f.tools.get("start_monitor").execute("m1", { command: "printf x", max_seconds: 10 });
	assert.match(missing.content[0].text, /wrapper is missing/);
	assert.equal(spawnCalls, 0);
	fs.writeFileSync(f.wrapper, "#!/bin/sh\n", { mode: 0o700 });
	fs.chmodSync(f.wrapper, 0o700);
	const child = f.makeChild();
	f.setSpawn(() => child);
	const started = await f.tools.get("start_monitor").execute("m2", { command: "watcher command", max_seconds: 10 });
	assert.match(started.content[0].text, /started/);
	child.stderr.emit("data", "pi-pidns-bash: bwrap unavailable; command was not run (exit 125)\n");
	child.emit("close", 125, null);
	assert.equal(f.calls.length, 1);
	assert.match(f.calls[0].message.content, /could not establish the private PID namespace/);
	assert.match(f.calls[0].message.content, /Re-arm if still needed/);
});

test("monitor batches capped lines, stops only its own child, and reports exit or expiry", async (t) => {
	const f = fixture({ OPENDESIGN_API_KEY_VOLKER: "never-pass-this-key", OPENDESIGN_API_KEY_GITHUB2: "also-secret" });
	t.after(f.cleanup);
	const children = [];
	let spawnOptions;
	f.setSpawn((wrapper, args, options) => {
		assert.equal(wrapper, f.wrapper);
		assert.deepEqual(args, ["-c", "watcher command"]);
		spawnOptions = options;
		const child = f.makeChild();
		children.push(child);
		return child;
	});
	const started = await f.tools.get("start_monitor").execute("m1", { command: "watcher command", max_seconds: 20 });
	const id = /Monitor (monitor-[^ ]+) started/.exec(started.content[0].text)?.[1];
	assert.ok(id);
	assert.equal(spawnOptions.env.OPENDESIGN_API_KEY_VOLKER, undefined);
	assert.equal(spawnOptions.env.OPENDESIGN_API_KEY_GITHUB2, undefined);
	children[0].stdout.emit("data", Buffer.from("one\ntwo\n"));
	const flush = f.findTimeout(1000);
	assert.ok(flush);
	f.fireTimeout(flush);
	assert.equal(f.calls.length, 1);
	assert.match(f.calls[0].message.content, /one\ntwo/);
	const stopped = await f.tools.get("stop_monitor").execute("m2", { id });
	assert.match(stopped.content[0].text, /stopping/);
	assert.deepEqual(children[0].signals, ["SIGTERM"]);
	children[0].emit("close", null, "SIGTERM");
	assert.match(f.calls.at(-1).message.content, /stopped.*Re-arm if still needed/);

	const second = f.makeChild();
	f.setSpawn(() => second);
	await f.tools.get("start_monitor").execute("m3", { command: "watcher command", max_seconds: 1 });
	const expiry = f.findTimeout(1000);
	assert.ok(expiry);
	f.fireTimeout(expiry);
	assert.deepEqual(second.signals, ["SIGTERM"]);
	second.emit("close", null, "SIGTERM");
	const notices = f.calls.filter(({ message }) => message.customType === "pi-ctl-monitor" && /expired/.test(message.content));
	assert.equal(notices.length, 1);
	assert.match(notices[0].message.content, /Re-arm if still needed/);
});

function fakeApiNoop() {
	return {
		on() {},
		registerTool() {},
		sendMessage() {},
	};
}
