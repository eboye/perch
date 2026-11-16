'use strict';
// Response cache for read methods, so moving around the app doesn't refetch the same data from X.
//
// - Each read method has a freshness window (seconds). Inside it the cached result is returned
//   without asking X; after it, X is asked again.
// - `fresh: true` in the params (explicit refresh, retries, the background poller) always asks X.
// - Identical requests in flight at the same time share one request to X.
// - Writes keep the cache honest: a like / repost / bookmark patches that post wherever it is
//   cached; other writes drop the cached results they can affect.
// - If X fails with a rate limit or network error and a recent copy exists (up to STALE_MAX),
//   that copy is returned with `stale: <age in seconds>` so the UI can say so.
//
// Memory only: nothing is written to disk. Cleared on sign-in and sign-out.

const MAX_ENTRIES = 400;
const STALE_MAX = 30 * 60; // seconds

// Freshness per read method, in seconds; older pages (a cursor is set) keep longer: they rarely change.
const TTL = {
	me: 300,
	timeline: 60,
	'tweet.details': 60,
	'tweet.replies': 60,
	'tweet.history': 600,
	'tweet.translate': 86400,
	'tweet.engagers': 120,
	'tweet.scheduled': 30,
	'user.profile': 60,
	'user.posts': 60,
	'user.connections': 120,
	'user.muted': 60,
	'user.blocked': 60,
	'notifications.mentions': 15,
	'user.suggestions': 600,
	'search.posts': 60,
	'search.users': 120,
	bookmarks: 60,
	'bookmarks.folders': 600,
	lists: 120,
	'list.details': 300,
	'list.tweets': 60,
	'list.members': 120,
	'list.hasMember': 60,
	notifications: 15,
	'dm.inbox': 15,
	'dm.conversation': 15,
	'dm.older': 300,
};
const OLDER_PAGE_TTL = 300;

const TWEET_READS = ['timeline', 'tweet.details', 'tweet.replies', 'user.posts', 'search.posts', 'bookmarks', 'list.tweets', 'notifications', 'notifications.mentions'];
const USER_READS = ['user.profile', 'user.connections', 'user.suggestions', 'user.muted', 'user.blocked', 'search.users', 'tweet.engagers', 'list.members'];
const LIST_READS = ['lists', 'list.details', 'list.tweets', 'list.members', 'list.hasMember'];
const DM_READS = ['dm.inbox', 'dm.conversation', 'dm.older'];

// Write method -> read methods whose cached results it can change. A write not listed here clears
// everything (safe default for anything added later).
const INVALIDATES = {
	'tweet.post': [...TWEET_READS, 'user.profile'],
	'tweet.delete': [...TWEET_READS, 'user.profile', 'tweet.history'],
	'tweet.schedule': ['tweet.scheduled'],
	'tweet.unschedule': ['tweet.scheduled'],
	'user.follow': [...USER_READS, 'timeline', 'me'],
	'user.removeFollower': [...USER_READS, 'me'],
	'user.mute': [...USER_READS, 'timeline', 'notifications', 'notifications.mentions'],
	'user.block': [...USER_READS, ...TWEET_READS, 'me'],
	'profile.update': ['me', ...USER_READS, ...TWEET_READS],
	'profile.picture': ['me', ...USER_READS, ...TWEET_READS],
	'list.create': LIST_READS,
	'list.update': LIST_READS,
	'list.delete': LIST_READS,
	'list.member': [...LIST_READS, 'user.profile'],
	'dm.send': DM_READS,
	'dm.markRead': DM_READS,
	'dm.markSeen': ['dm.inbox'],
	'dm.deleteConversation': DM_READS,
};
// Calls that never touch the cache.
const PASS_THROUGH = new Set(['ping', 'space.details', 'space.stream', 'dm.media']);

// like / unlike / retweet / unretweet / bookmark / unbookmark -> [flag, count field, value]
const TWEET_ACTIONS = {
	like: ['liked', 'likeCount', true],
	unlike: ['liked', 'likeCount', false],
	retweet: ['retweeted', 'retweetCount', true],
	unretweet: ['retweeted', 'retweetCount', false],
	bookmark: ['bookmarked', 'bookmarkCount', true],
	unbookmark: ['bookmarked', 'bookmarkCount', false],
};

// PERCH_DEBUG_RPC=1: log cache decisions (method names only) to stderr.
function trace(what, method) {
	if (process.env.PERCH_DEBUG_RPC === '1') console.error(`[cache] ${what} ${method}`);
}

function stableKey(method, params) {
	const keys = Object.keys(params || {})
		.filter((k) => k !== 'fresh' && params[k] !== undefined && params[k] !== null && params[k] !== '')
		.sort();
	return `${method} ${JSON.stringify(keys.map((k) => [k, params[k]]))}`;
}

const isTransient = (err) => {
	const status = err?.status ?? err?.response?.status;
	return err?.kind === 'rate_limit' || err?.kind === 'network' || status === 429 || ['ECONNREFUSED', 'ENOTFOUND', 'ETIMEDOUT', 'ECONNABORTED', 'EAI_AGAIN', 'ECONNRESET'].includes(err?.code);
};

class ResponseCache {
	constructor(now = () => Date.now()) {
		this.now = now;
		this.entries = new Map(); // key -> { method, value, at }
		this.inflight = new Map(); // key -> Promise
		this.generation = 0; // bumps on every invalidation, so a request that raced a write isn't stored
	}

	clear() {
		this.entries.clear();
		this.inflight.clear();
		this.generation++;
	}

	invalidate(methods) {
		const drop = new Set(methods);
		for (const [key, entry] of this.entries) {
			if (drop.has(entry.method)) this.entries.delete(key);
		}
		for (const key of this.inflight.keys()) {
			if (drop.has(key.slice(0, key.indexOf(' ')))) this.inflight.delete(key);
		}
		this.generation++;
	}

	// Drop the cached entries `predicate({ method, params })` picks.
	invalidateWhere(predicate) {
		for (const [key, entry] of this.entries) {
			if (predicate(entry)) this.entries.delete(key);
		}
		this.generation++;
	}

	// Update a post's own state (liked / reposted / bookmarked and its count) in every cached result.
	patchTweet(id, action) {
		const change = TWEET_ACTIONS[action];
		if (!change || !id) return;
		const [flag, countField, value] = change;
		const visit = (node) => {
			if (Array.isArray(node)) {
				node.forEach(visit);
				return;
			}
			if (!node || typeof node !== 'object') return;
			if (node.id === String(id) && flag in node && node[flag] !== value) {
				node[flag] = value;
				if (typeof node[countField] === 'number') node[countField] = Math.max(0, node[countField] + (value ? 1 : -1));
			}
			for (const child of Object.values(node)) {
				if (child && typeof child === 'object') visit(child);
			}
		};
		for (const entry of this.entries.values()) visit(entry.value);
	}

	ttlFor(method, params) {
		const base = TTL[method];
		if (base === undefined) return undefined;
		return params?.cursor ? Math.max(base, OLDER_PAGE_TTL) : base;
	}

	// Run `fetch(params)` for `method`, through the cache. Returns the result (possibly cached).
	async run(method, params, fetch) {
		const clean = { ...(params || {}) };
		const fresh = Boolean(clean.fresh);
		delete clean.fresh;
		if (PASS_THROUGH.has(method)) return fetch(clean);

		const ttl = this.ttlFor(method, clean);
		if (ttl === undefined) return this.write(method, clean, fetch);

		const key = stableKey(method, clean);
		const entry = this.entries.get(key);
		const age = entry ? (this.now() - entry.at) / 1000 : Infinity;
		if (entry && !fresh && age < ttl) {
			this.entries.delete(key); // move to the end (most recently used)
			this.entries.set(key, entry);
			trace('hit', method);
			return entry.value;
		}
		if (!fresh && this.inflight.has(key)) {
			trace('joined', method);
			return this.inflight.get(key);
		}
		trace(fresh ? 'fetch (fresh)' : entry ? 'fetch (expired)' : 'fetch', method);

		const generation = this.generation;
		const request = (async () => {
			try {
				const value = await fetch(clean);
				if (this.generation === generation) this.store(key, method, value, clean);
				return value;
			} catch (err) {
				if (entry && age < STALE_MAX && isTransient(err) && entry.value && typeof entry.value === 'object' && !Array.isArray(entry.value)) {
					console.error(`[cache] ${method}: X failed (${err?.message || err}); serving a copy from ${Math.round(age)}s ago`);
					return { ...entry.value, stale: Math.round(age) };
				}
				throw err;
			} finally {
				if (this.inflight.get(key) === request) this.inflight.delete(key);
			}
		})();
		this.inflight.set(key, request);
		return request;
	}

	async write(method, params, fetch) {
		const result = await fetch(params);
		if (method === 'tweet.action') {
			this.patchTweet(params.id, params.action);
			if (params.action === 'bookmark' || params.action === 'unbookmark') this.invalidate(['bookmarks']);
			if (params.action === 'like' || params.action === 'unlike') this.invalidateWhere((e) => e.method === 'user.posts' && e.params.kind === 'likes'); // your Likes tab
			if (params.action === 'retweet' || params.action === 'unretweet') this.invalidate(['timeline', 'user.posts']);
		} else if (method === 'init' || method === 'signout') {
			this.clear();
		} else if (INVALIDATES[method]) {
			this.invalidate(INVALIDATES[method]);
		} else {
			this.clear();
		}
		return result;
	}

	store(key, method, value, params = {}) {
		this.entries.delete(key);
		this.entries.set(key, { method, params, value, at: this.now() });
		while (this.entries.size > MAX_ENTRIES) this.entries.delete(this.entries.keys().next().value);
	}
}

module.exports = { ResponseCache, TTL, INVALIDATES, stableKey };
