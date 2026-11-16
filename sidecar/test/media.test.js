'use strict';
// node --test: chunked media uploads (videos, GIFs) against a fake X.

const test = require('node:test');
const assert = require('node:assert/strict');
const { mkdtempSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { mediaInfo, uploadChunked, CHUNK_BYTES } = require('../src/media');

const dir = mkdtempSync(join(tmpdir(), 'perch-media-'));
function file(name, bytes) {
	const path = join(dir, name);
	writeFileSync(path, Buffer.alloc(bytes, 7));
	return path;
}

// A fake upload endpoint: records each call; FINALIZE / STATUS answer from `processing`.
function fakeX({ processing = [], failAppend = false } = {}) {
	const calls = [];
	const appended = [];
	const fetcher = async (url, init) => {
		const params = Object.fromEntries(url.searchParams);
		calls.push({ command: params.command, method: init.method, params });
		const ok = (body) => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });
		if (params.command === 'INIT') return ok({ media_id_string: '777' });
		if (params.command === 'APPEND') {
			if (failAppend) return { ok: false, status: 400, text: async () => JSON.stringify({ errors: [{ message: 'Segment too big' }] }) };
			appended.push({ index: Number(params.segment_index), size: init.body.get('media').size });
			return { ok: true, status: 204, text: async () => '' };
		}
		const next = processing.shift();
		return ok(next ? { media_id_string: '777', processing_info: next } : { media_id_string: '777' });
	};
	return { fetcher, calls, appended };
}

const noWait = { sleep: async () => {} };

test('a video goes up in 4 MB chunks with its type and category, then waits for processing', async () => {
	const path = file('clip.mp4', CHUNK_BYTES * 2 + 1000);
	const x = fakeX({ processing: [{ state: 'pending', check_after_secs: 1 }, { state: 'in_progress', progress_percent: 60, check_after_secs: 1 }, { state: 'succeeded', progress_percent: 100 }] });
	const progress = [];
	const id = await uploadChunked(x.fetcher, path, { ...noWait, onProgress: (p) => progress.push(p) });
	assert.equal(id, '777');
	assert.deepEqual(
		x.calls.map((c) => c.command),
		['INIT', 'APPEND', 'APPEND', 'APPEND', 'FINALIZE', 'STATUS', 'STATUS'],
	);
	assert.deepEqual(x.calls[0].params, { command: 'INIT', total_bytes: String(CHUNK_BYTES * 2 + 1000), media_type: 'video/mp4', media_category: 'tweet_video' });
	assert.deepEqual(x.appended, [
		{ index: 0, size: CHUNK_BYTES },
		{ index: 1, size: CHUNK_BYTES },
		{ index: 2, size: 1000 },
	]);
	assert.equal(x.calls.at(-1).method, 'GET'); // STATUS is a GET
	const uploads = progress.filter((p) => p.phase === 'upload').map((p) => p.fraction);
	assert.equal(uploads.at(-1), 1);
	assert.ok(uploads.every((f, i) => i === 0 || f > uploads[i - 1]), 'upload progress only goes up');
	assert.deepEqual(progress.at(-1), { phase: 'processing', fraction: 1 });
});

test('a GIF uses the tweet_gif category', async () => {
	const x = fakeX();
	await uploadChunked(x.fetcher, file('fun.gif', 2000), noWait);
	assert.equal(x.calls[0].params.media_category, 'tweet_gif');
	assert.equal(x.calls[0].params.media_type, 'image/gif');
});

test('processing that fails, or an APPEND that fails, rejects with the reason', async () => {
	const failed = fakeX({ processing: [{ state: 'failed', error: { message: 'Unsupported codec' } }] });
	await assert.rejects(uploadChunked(failed.fetcher, file('bad.mp4', 100), noWait), /couldn’t process bad\.mp4: Unsupported codec/);
	const append = fakeX({ failAppend: true });
	await assert.rejects(uploadChunked(append.fetcher, file('a.mp4', 100), noWait), /HTTP 400.*Segment too big/);
});

test('processing that never ends gives up', async () => {
	let clock = 0;
	const forever = { state: 'in_progress', progress_percent: 10, check_after_secs: 5 };
	const x = fakeX({ processing: Array.from({ length: 1000 }, () => ({ ...forever })) });
	await assert.rejects(uploadChunked(x.fetcher, file('slow.mp4', 100), { sleep: async (ms) => void (clock += ms), now: () => clock }), /taking too long/);
});

test('file checks: kind, size limits and unreadable files', () => {
	assert.equal(mediaInfo(file('p.PNG', 10)).kind, 'image');
	assert.equal(mediaInfo(file('m.mov', 10)).type, 'video/quicktime');
	assert.throws(() => mediaInfo(file('doc.pdf', 10)), /X takes JPEG, PNG, WebP or GIF images and MP4 or MOV videos/);
	assert.throws(() => mediaInfo(file('big.png', 5 * 1024 * 1024 + 1)), /larger than X allows for an image \(5 MB\)/);
	assert.throws(() => mediaInfo(join(dir, 'missing.mp4')), /Couldn’t read missing\.mp4/);
});
