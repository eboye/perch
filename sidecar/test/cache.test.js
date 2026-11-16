'use strict';
// node --test: the sidecar's response cache.

const test = require('node:test');
const assert = require('node:assert/strict');
const { ResponseCache } = require('../src/cache');

function setup() {
	let clock = 1_000_000;
	const cache = new ResponseCache(() => clock);
	const calls = [];
	const fetcher = (value) => async (params) => {
		calls.push(params);
		return typeof value === 'function' ? value(params) : structuredClone(value);
	};
	return { cache, calls, fetcher, advance: (s) => (clock += s * 1000) };
}

const page = () => ({ list: [{ id: '1', liked: false, likeCount: 5, bookmarked: false, retweetedTweet: null, quoted: { id: '2', liked: false, likeCount: 0 } }], next: 'c1' });

test('a read within its window is served from the cache', async () => {
	const { cache, calls, fetcher, advance } = setup();
	await cache.run('user.profile', { handle: 'a' }, fetcher({ id: '9' }));
	advance(30);
	const again = await cache.run('user.profile', { handle: 'a' }, fetcher({ id: '9' }));
	assert.equal(calls.length, 1);
	assert.deepEqual(again, { id: '9' });
});

test('after the window it asks X again', async () => {
	const { cache, calls, fetcher, advance } = setup();
	await cache.run('user.profile', { handle: 'a' }, fetcher({ id: '9' }));
	advance(61);
	await cache.run('user.profile', { handle: 'a' }, fetcher({ id: '9' }));
	assert.equal(calls.length, 2);
});

test('fresh: true always asks X, and is not passed on to the method', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	await cache.run('timeline', { kind: 'following', fresh: true }, fetcher(page()));
	assert.equal(calls.length, 2);
	assert.equal('fresh' in calls[1], false);
});

test('different params are different entries; empty params are ignored in the key', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('timeline', { kind: 'following', cursor: null }, fetcher(page()));
	await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	await cache.run('timeline', { kind: 'foryou' }, fetcher(page()));
	assert.equal(calls.length, 2);
});

test('older pages (with a cursor) keep longer', async () => {
	const { cache, calls, fetcher, advance } = setup();
	await cache.run('timeline', { kind: 'following', cursor: 'c1' }, fetcher(page()));
	advance(120);
	await cache.run('timeline', { kind: 'following', cursor: 'c1' }, fetcher(page()));
	assert.equal(calls.length, 1);
});

test('identical requests in flight share one call', async () => {
	const { cache, calls, fetcher } = setup();
	const [a, b] = await Promise.all([cache.run('me', {}, fetcher({ id: '1' })), cache.run('me', {}, fetcher({ id: '1' }))]);
	assert.equal(calls.length, 1);
	assert.deepEqual(a, b);
});

test('a like patches the post (and quoted copies) in every cached result', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	await cache.run('tweet.action', { action: 'like', id: '1' }, fetcher({ ok: true }));
	await cache.run('tweet.action', { action: 'like', id: '2' }, fetcher({ ok: true }));
	const cached = await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	assert.equal(calls.length, 3); // timeline once, two actions
	assert.equal(cached.list[0].liked, true);
	assert.equal(cached.list[0].likeCount, 6);
	assert.equal(cached.list[0].quoted.liked, true);
	assert.equal(cached.list[0].quoted.likeCount, 1);
});

test('liking twice does not double count; unlike undoes it', async () => {
	const { cache, fetcher } = setup();
	await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	await cache.run('tweet.action', { action: 'like', id: '1' }, fetcher({ ok: true }));
	await cache.run('tweet.action', { action: 'like', id: '1' }, fetcher({ ok: true }));
	let cached = await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	assert.equal(cached.list[0].likeCount, 6);
	await cache.run('tweet.action', { action: 'unlike', id: '1' }, fetcher({ ok: true }));
	cached = await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	assert.equal(cached.list[0].liked, false);
	assert.equal(cached.list[0].likeCount, 5);
});

test('a failed write changes nothing', async () => {
	const { cache, fetcher } = setup();
	await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	await assert.rejects(cache.run('tweet.action', { action: 'like', id: '1' }, async () => Promise.reject(new Error('nope'))));
	const cached = await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	assert.equal(cached.list[0].liked, false);
});

test('a like drops only your Likes tab, not other profile tabs', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('user.posts', { id: '1', kind: 'posts' }, fetcher(page()));
	await cache.run('user.posts', { id: '1', kind: 'likes' }, fetcher(page()));
	await cache.run('tweet.action', { action: 'like', id: '1' }, fetcher({ ok: true }));
	await cache.run('user.posts', { id: '1', kind: 'posts' }, fetcher(page()));
	await cache.run('user.posts', { id: '1', kind: 'likes' }, fetcher(page()));
	assert.equal(calls.filter((p) => p.kind === 'posts').length, 1);
	assert.equal(calls.filter((p) => p.kind === 'likes').length, 2);
});

test('bookmarking drops the cached Bookmarks list', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('bookmarks', {}, fetcher(page()));
	await cache.run('tweet.action', { action: 'bookmark', id: '1' }, fetcher({ ok: true }));
	await cache.run('bookmarks', {}, fetcher(page()));
	assert.equal(calls.filter((p) => !p.action).length, 2);
});

test('writes drop the reads they affect, and keep the rest', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('user.muted', {}, fetcher({ list: [], next: '' }));
	await cache.run('tweet.scheduled', {}, fetcher({ list: [], next: '' }));
	await cache.run('user.mute', { id: '5', mute: true }, fetcher({ ok: true }));
	await cache.run('user.muted', {}, fetcher({ list: [{ id: '5' }], next: '' }));
	await cache.run('tweet.scheduled', {}, fetcher({ list: [], next: '' }));
	assert.equal(calls.length, 4); // muted twice, scheduled once, mute once
});

test('an unknown write clears everything', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('me', {}, fetcher({ id: '1' }));
	await cache.run('something.new', {}, fetcher({ ok: true }));
	await cache.run('me', {}, fetcher({ id: '1' }));
	assert.equal(calls.length, 3);
});

test('a read that raced a write is not stored', async () => {
	const { cache, calls, fetcher } = setup();
	let release;
	const slow = new Promise((resolve) => (release = resolve));
	const pending = cache.run('user.muted', {}, async () => {
		calls.push('slow');
		await slow;
		return { list: [], next: '' };
	});
	await cache.run('user.mute', { id: '5', mute: true }, fetcher({ ok: true }));
	release();
	await pending;
	await cache.run('user.muted', {}, fetcher({ list: [{ id: '5' }], next: '' }));
	assert.equal(calls.length, 3);
});

test('on a rate limit, a recent copy is served and marked stale', async () => {
	const { cache, fetcher, advance } = setup();
	await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	advance(90);
	const result = await cache.run('timeline', { kind: 'following' }, async () => {
		throw Object.assign(new Error('Too Many Requests'), { status: 429 });
	});
	assert.equal(result.stale, 90);
	assert.equal(result.list.length, 1);
});

test('other errors, or copies older than 30 minutes, are not served', async () => {
	const { cache, fetcher, advance } = setup();
	await cache.run('timeline', { kind: 'following' }, fetcher(page()));
	advance(90);
	await assert.rejects(cache.run('timeline', { kind: 'following' }, async () => Promise.reject(Object.assign(new Error('gone'), { status: 404 }))));
	advance(31 * 60);
	await assert.rejects(cache.run('timeline', { kind: 'following' }, async () => Promise.reject(Object.assign(new Error('slow down'), { status: 429 }))));
});

test('errors are never cached', async () => {
	const { cache, calls, fetcher } = setup();
	await assert.rejects(
		cache.run('me', {}, async () => {
			calls.push(1);
			throw new Error('boom');
		}),
	);
	await cache.run('me', {}, fetcher({ id: '1' }));
	assert.equal(calls.length, 2);
});

test('sign-in clears the cache; live Spaces are never cached', async () => {
	const { cache, calls, fetcher } = setup();
	await cache.run('me', {}, fetcher({ id: '1' }));
	await cache.run('init', { apiKey: 'k' }, fetcher({ ok: true }));
	await cache.run('me', {}, fetcher({ id: '2' }));
	await cache.run('space.details', { id: 's' }, fetcher({ id: 's' }));
	await cache.run('space.details', { id: 's' }, fetcher({ id: 's' }));
	assert.equal(calls.length, 5);
});
