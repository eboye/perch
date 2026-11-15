'use strict';
// Perch sidecar: newline-delimited JSON-RPC over stdio, wrapping rettiwt-api.
//
// Request:  {"id": 1, "method": "timeline", "params": {"kind": "following"}}
// Response: {"id": 1, "result": ...}  or  {"id": 1, "error": {"message", "kind", "status"}}
//
// stdout is reserved for protocol frames. Anything the library prints goes to stderr.

const writeFrame = process.stdout.write.bind(process.stdout);
for (const level of ['log', 'info', 'debug', 'warn']) {
	console[level] = (...args) => console.error(...args);
}

const readline = require('node:readline');
const { Rettiwt } = require('rettiwt-api');
const { signedFetch } = require('./xhttp');
const inbox = require('./inbox');
const demo = require('./demo');

let client = null;
const demoMode = process.env.PERCH_DEMO === '1';

function requireClient() {
	if (!client) {
		const err = new Error('Not signed in');
		err.kind = 'auth';
		throw err;
	}
	return client;
}

// Plain JSON for the UI; the library's models implement toJSON().
const plain = (value) => (value === undefined ? null : JSON.parse(JSON.stringify(value)));

const methods = {
	ping: async () => ({ pong: true, demo: demoMode }),

	init: async ({ apiKey }) => {
		if (demoMode) return { ok: true };
		if (!apiKey) throw Object.assign(new Error('API key is empty'), { kind: 'auth' });
		try {
			client = new Rettiwt({ apiKey: apiKey.trim(), timeout: 20000, maxRetries: 1 });
		} catch (err) {
			client = null;
			throw Object.assign(new Error(`not a valid rettiwt API key (${err?.message || err})`), { kind: 'auth' });
		}
		return { ok: true };
	},

	signout: async () => {
		client = null;
		return { ok: true };
	},

	me: async () => {
		if (demoMode) return demo.me();
		const user = await requireClient().user.details();
		if (!user) throw Object.assign(new Error('Could not load the signed-in account'), { kind: 'auth' });
		return plain(user);
	},

	timeline: async ({ kind, cursor }) => {
		if (demoMode) return demo.timeline(kind, cursor);
		const users = requireClient().user;
		const data = kind === 'foryou' ? await users.recommended(cursor || undefined) : await users.followed(cursor || undefined);
		return { list: data.list.map(serializeTweet), next: data.next };
	},

	// like | unlike | retweet | unretweet | bookmark | unbookmark
	'tweet.action': async ({ action, id }) => {
		if (!TWEET_ACTIONS.has(action)) throw new Error(`Unsupported action: ${action}`);
		if (!id) throw new Error('Missing tweet id');
		if (demoMode) return { ok: true };
		const ok = await requireClient().tweet[action](String(id));
		if (!ok) throw new Error(`X did not confirm "${action}"`);
		return { ok: true };
	},

	// { text, replyTo?, quote?, mediaPaths?: string[] } -> { id }
	'tweet.post': async ({ text, replyTo, quote, mediaPaths }) => {
		const paths = (mediaPaths || []).slice(0, 4);
		if (!text?.trim() && paths.length === 0) throw new Error('Nothing to post');
		if (demoMode) return { id: String(Date.now()) };
		const tweets = requireClient().tweet;
		const media = [];
		for (const path of paths) media.push({ id: await tweets.upload(path) });
		const id = await tweets.post({
			text: text || undefined,
			replyTo: replyTo || undefined,
			quote: quote || undefined,
			media: media.length ? media : undefined,
		});
		if (!id) throw new Error('X did not return the new post id');
		return { id };
	},

	'space.details': async ({ id }) => {
		if (demoMode) return demo.space(id);
		const space = await requireClient().space.details(String(id));
		if (!space) throw Object.assign(new Error('Space not found'), { kind: 'not_found' });
		return plain(space);
	},

	// HLS playlist for a live Space, or its replay once ended. rettiwt has no wrapper for this.
	'space.stream': async ({ mediaKey }) => {
		if (demoMode) throw new Error('Audio is not available in demo mode');
		if (!mediaKey) throw new Error('This Space has no media key');
		requireClient();
		const body = await streamStatus(mediaKey);
		const location = body?.source?.location || body?.source?.noRedirectPlaybackUrl;
		if (!location) throw new Error('X returned no audio stream for this Space');
		return { location, streamType: body?.source?.streamType ?? null };
	},
};

const TWEET_ACTIONS = new Set(['like', 'unlike', 'retweet', 'unretweet', 'bookmark', 'unbookmark']);

// Tweet.toJSON() drops the viewer's own state; pull it from the raw GraphQL payload.
function serializeTweet(tweet) {
	if (!tweet) return null;
	const out = plain(tweet);
	const legacy = tweet.raw?.legacy || {};
	out.liked = Boolean(legacy.favorited);
	out.retweeted = Boolean(legacy.retweeted);
	out.bookmarked = Boolean(legacy.bookmarked);
	if (tweet.quoted) out.quoted = serializeTweet(tweet.quoted);
	if (tweet.retweetedTweet) out.retweetedTweet = serializeTweet(tweet.retweetedTweet);
	return out;
}

const STREAM_HOSTS = ['https://api.x.com/1.1', 'https://x.com/i/api/1.1'];

async function streamStatus(mediaKey) {
	let lastError;
	for (const base of STREAM_HOSTS) {
		const url = new URL(`${base}/live_video_stream/status/${encodeURIComponent(mediaKey)}`);
		const res = await signedFetch(client, url);
		if (res.ok) return res.json();
		const text = (await res.text()).slice(0, 200);
		console.error(`[sidecar] stream status ${res.status} from ${url.host}: ${text}`);
		lastError =
			res.status === 404
				? Object.assign(new Error('No audio stream right now — the Space may not be broadcasting yet, or it just ended'), { kind: 'not_found', status: 404 })
				: Object.assign(new Error(`Stream lookup failed (HTTP ${res.status})`), { status: res.status });
		if (res.status !== 404) break;
	}
	throw lastError;
}

Object.assign(methods, inbox.methods({ requireClient, demoMode }));

function classify(err) {
	const status = err?.status ?? err?.response?.status ?? err?.code;
	let kind = err?.kind || 'error';
	if (status === 401 || status === 403) kind = 'auth';
	else if (status === 429 || status === 88) kind = 'rate_limit';
	else if (['ECONNREFUSED', 'ENOTFOUND', 'ETIMEDOUT', 'ECONNABORTED', 'EAI_AGAIN'].includes(err?.code)) kind = 'network';
	return { message: err?.message || String(err), kind, status: typeof status === 'number' ? status : null };
}

async function handle(line) {
	let request;
	try {
		request = JSON.parse(line);
	} catch {
		return;
	}
	const { id, method, params } = request;
	const fn = methods[method];
	try {
		if (!fn) throw new Error(`Unknown method: ${method}`);
		writeFrame(JSON.stringify({ id, result: await fn(params || {}) }) + '\n');
	} catch (err) {
		console.error(`[sidecar] ${method} failed:`, err?.message || err);
		writeFrame(JSON.stringify({ id, error: classify(err) }) + '\n');
	}
}

readline.createInterface({ input: process.stdin }).on('line', (line) => void handle(line));
process.stdin.on('end', () => process.exit(0));
