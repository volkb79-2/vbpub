// Idempotent account bootstrap, run INSIDE the Fluxer api container:
//   docker exec -i <api> node --input-type=module -e "<this file>"
// It talks to the API on 127.0.0.1:8080 (no host port, no network hop) and
// uses the image's own altcha-lib to solve the registration proof-of-work
// challenge, exactly as the web client does (CaptchaInterceptor.ts).
//
// stdin: JSON {"admin": {"username", "password"}, "users": [{"username",
// "password"}], "setup_token", "date_of_birth"}. Passwords therefore never
// reach an argv, a file in the container or a log. stdout: one JSON line of
// result CODES per account, never a credential or token.
import {solveChallenge} from 'altcha-lib';
import {deriveKey} from 'altcha-lib/algorithms/pbkdf2';

const BASE = 'http://127.0.0.1:8080';
const BASE_HEADERS = {'content-type': 'application/json', 'x-forwarded-for': '127.0.0.1'};
const CAPTCHA_CODES = new Set(['CAPTCHA_REQUIRED', 'INVALID_CAPTCHA']);

async function readStdin() {
	const chunks = [];
	for await (const chunk of process.stdin) chunks.push(chunk);
	return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function call(method, path, body, headers = {}) {
	const response = await fetch(BASE + path, {
		method,
		headers: {...BASE_HEADERS, ...headers},
		body: body === undefined ? undefined : JSON.stringify(body),
	});
	const text = await response.text();
	let json = null;
	try {
		json = text ? JSON.parse(text) : null;
	} catch {
		json = null;
	}
	return {status: response.status, json};
}

async function solveToken(challenge) {
	const solution = await solveChallenge({challenge, deriveKey, timeout: 120_000});
	if (!solution) return null;
	const payload = {challenge: {parameters: challenge.parameters, signature: challenge.signature}, solution};
	return Buffer.from(JSON.stringify(payload)).toString('base64');
}

// Retry once or twice with a solved proof-of-work token when the API asks.
async function withCaptcha(method, path, body, headers = {}) {
	let reply = await call(method, path, body, headers);
	for (let attempt = 0; attempt < 3; attempt++) {
		const challenge = reply.json?.altcha_challenge;
		if (reply.status !== 400 || !CAPTCHA_CODES.has(reply.json?.code) || !challenge) break;
		const token = await solveToken(challenge);
		if (!token) break;
		reply = await call(method, path, body, {...headers, 'x-captcha-token': token});
	}
	return reply;
}

const code = (reply) => reply.json?.code ?? `HTTP_${reply.status}`;

async function signIn(account) {
	const reply = await withCaptcha('POST', '/auth/login', {login: account.username, password: account.password});
	if (reply.status === 200 && typeof reply.json?.token === 'string') return {token: reply.json.token};
	return {error: code(reply)};
}

async function register(account, extra) {
	const reply = await withCaptcha('POST', '/auth/register', {
		username: account.username,
		password: account.password,
		consent: true,
		date_of_birth: input.date_of_birth,
		...extra,
	});
	if (reply.status === 200 && reply.json?.registration_pending_approval) return {pending: true};
	if (reply.status === 200 && typeof reply.json?.token === 'string') return {token: reply.json.token};
	return {error: code(reply)};
}

const input = await readStdin();
const result = {};
const authHeader = (token) => ({authorization: token});

// 1. Administrator: sign in if it already exists, otherwise register it with
// the setup token (the first account registered with it becomes the admin).
let admin = await signIn(input.admin);
if (admin.error) {
	admin = await register(input.admin, {setup_token: input.setup_token});
	result[input.admin.username] = admin.token ? 'admin_created' : `admin_failed:${admin.error}`;
} else {
	result[input.admin.username] = 'admin_exists';
}

if (admin.token) {
	// 2. Finish setup (what the browser wizard's last step does), so sign-ups
	// work for the other accounts.
	const config = await call('GET', '/admin/instance/config', undefined, authHeader(admin.token));
	const configured = config.json?.app_public?.setup?.configured === true;
	if (!configured) {
		const patch = await call(
			'PATCH',
			'/admin/instance/config',
			{app_public: {setup: {configured: true}}},
			authHeader(admin.token),
		);
		result._setup = patch.status === 200 ? 'configured' : `setup_failed:${code(patch)}`;
	} else {
		result._setup = 'already_configured';
	}

	// 3. Registration mode: open it only while the dummy users are created,
	// then enforce the configured mode (default "closed": a tryout reachable
	// from the internet must not accept sign-ups from anyone; the admin panel
	// can issue registration links when more accounts are wanted).
	const modeReply = await call('GET', '/admin/instance/config', undefined, authHeader(admin.token));
	let mode = modeReply.json?.registration?.mode ?? 'open';
	const setMode = async (next) => {
		const patch = await call('PATCH', '/admin/instance/config', {registration: {mode: next}}, authHeader(admin.token));
		if (patch.status === 200) mode = next;
		return patch.status === 200;
	};
	const pending = [];
	for (const user of input.users) {
		const existing = await signIn(user);
		if (existing.token) {
			result[user.username] = 'user_exists';
		} else {
			pending.push(user);
		}
	}
	if (pending.length > 0) {
		if (mode !== 'open' && !(await setMode('open'))) result._registration_mode = 'failed:open';
		for (const user of pending) {
			const created = await register(user, {});
			result[user.username] = created.token ? 'user_created' : created.pending ? 'user_pending_approval' : `user_failed:${created.error}`;
		}
	}
	const wanted = input.registration_mode_after;
	if (wanted && mode !== wanted) {
		result._registration_mode = (await setMode(wanted)) ? `set:${wanted}` : `failed:${wanted}`;
	} else {
		result._registration_mode ??= `kept:${mode}`;
	}
}

console.log(JSON.stringify(result));
