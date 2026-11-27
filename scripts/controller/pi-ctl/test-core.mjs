import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const { createController } = await import(process.env.PI_CTL_CORE_MODULE ?? "./core.mjs");

function fixture(envExtra = {}) {
	const temp = fs.mkdtempSync(path.join(os.tmpdir(), "pi-ctl-core-"));
	const repo = path.join(temp, "repo");
	const root = path.join(temp, "state");
	fs.mkdirSync(repo, { mode: 0o700 });
	fs.writeFileSync(path.join(repo, ".git"), "gitdir: fake\n", { mode: 0o600 });
	const calls = [];
	const events = new Map();
	const tools = new Map();
	let sendsBeforeThrow = 0;
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
			if (sendsBeforeThrow > 0) {
				sendsBeforeThrow--;
				throw new Error("injected failure");
			}
			calls.push({ message, options });
		},
	};
	let currentTime = 1_800_000_000_000;
	const timers = {
		setInterval(fn, ms) {
			return { fn, ms, interval: true };
		},
		clearInterval() {},
		setTimeout(fn, ms) {
			return { fn, ms, interval: false };
		},
		clearTimeout() {},
	};
	const env = {
		HOME: temp,
		PI_CTL_NAME: "receiver",
		PI_CTL_ROOT: root,
		...envExtra,
	};
	const controller = createController(fakePi, {
		env,
		cwd: repo,
		pid: 987654321,
		now: () => currentTime,
		makeId: () => `id-${++id}`,
		timers,
	}, { root, name: env.PI_CTL_NAME });
	return {
		temp,
		repo,
		root,
		env,
		calls,
		events,
		tools,
		controller,
		timers,
		setTime(value) { currentTime = value; },
		throwOnSend(count = 1) { sendsBeforeThrow = count; },
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

function fakeApiNoop() {
	return {
		on() {},
		registerTool() {},
		sendMessage() {},
	};
}
