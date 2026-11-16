'use strict';
// Uploading media for posts the way x.com does: INIT (with the type and X's category), the file in
// chunks (APPEND), FINALIZE, then STATUS until X has processed it (videos and GIFs are transcoded).
// rettiwt's own upload sends one chunk with no type or category, which is fine for images but not
// for video.

const { openSync, readSync, closeSync, statSync } = require('node:fs');
const { basename, extname } = require('node:path');

const UPLOAD_URL = 'https://upload.x.com/i/media/upload.json';
const CHUNK_BYTES = 4 * 1024 * 1024;
const PROCESSING_TIMEOUT_MS = 15 * 60 * 1000;
const MB = 1024 * 1024;

// What X takes in a post, and its limits.
const KINDS = {
	'.jpg': { type: 'image/jpeg', category: 'tweet_image', max: 5 * MB, kind: 'image' },
	'.jpeg': { type: 'image/jpeg', category: 'tweet_image', max: 5 * MB, kind: 'image' },
	'.png': { type: 'image/png', category: 'tweet_image', max: 5 * MB, kind: 'image' },
	'.webp': { type: 'image/webp', category: 'tweet_image', max: 5 * MB, kind: 'image' },
	'.gif': { type: 'image/gif', category: 'tweet_gif', max: 15 * MB, kind: 'gif' },
	'.mp4': { type: 'video/mp4', category: 'tweet_video', max: 512 * MB, kind: 'video' },
	'.m4v': { type: 'video/mp4', category: 'tweet_video', max: 512 * MB, kind: 'video' },
	'.mov': { type: 'video/quicktime', category: 'tweet_video', max: 512 * MB, kind: 'video' },
};

const LIMIT_TEXT = { image: '5 MB', gif: '15 MB', video: '512 MB' };

// { type, category, max, kind, size } for a file, or a friendly error.
function mediaInfo(path) {
	const info = KINDS[extname(String(path || '')).toLowerCase()];
	if (!info) throw new Error(`${basename(String(path || ''))}: X takes JPEG, PNG, WebP or GIF images and MP4 or MOV videos`);
	let size;
	try {
		size = statSync(path).size;
	} catch {
		throw new Error(`Couldn’t read ${basename(String(path))}`);
	}
	if (size > info.max) throw new Error(`${basename(String(path))} is larger than X allows for ${info.kind === 'image' ? 'an image' : info.kind === 'gif' ? 'a GIF' : 'a video'} (${LIMIT_TEXT[info.kind]})`);
	return { ...info, size };
}

// Uploads `path` and resolves to X's media id once it's ready to post.
// `fetcher(url, init)` makes a signed request; `onProgress({ phase: 'upload'|'processing', fraction })`.
async function uploadChunked(fetcher, path, { onProgress = () => {}, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), now = () => Date.now() } = {}) {
	const info = mediaInfo(path);
	const call = async (params, init = {}) => {
		const url = new URL(UPLOAD_URL);
		url.search = new URLSearchParams(params);
		const res = await fetcher(url, init);
		const text = await res.text();
		let body = {};
		try {
			body = text ? JSON.parse(text) : {};
		} catch {
			body = {};
		}
		if (!res.ok) {
			const detail = body?.errors?.[0]?.message || body?.error || '';
			throw Object.assign(new Error(`Upload failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`), { status: res.status });
		}
		return body;
	};

	const init = await call({ command: 'INIT', total_bytes: String(info.size), media_type: info.type, media_category: info.category }, { method: 'POST' });
	const id = init.media_id_string || (init.media_id ? String(init.media_id) : '');
	if (!id) throw new Error('X did not start the upload');

	const fd = openSync(path, 'r');
	try {
		let sent = 0;
		for (let index = 0; sent < info.size; index++) {
			const chunk = Buffer.alloc(Math.min(CHUNK_BYTES, info.size - sent));
			const read = readSync(fd, chunk, 0, chunk.length, sent);
			if (read <= 0) throw new Error(`Couldn’t read ${basename(path)}`);
			const form = new FormData();
			form.append('media', new Blob([chunk.subarray(0, read)], { type: info.type }), basename(path));
			await call({ command: 'APPEND', media_id: id, segment_index: String(index) }, { method: 'POST', body: form });
			sent += read;
			onProgress({ phase: 'upload', fraction: sent / info.size });
		}
	} finally {
		closeSync(fd);
	}

	let status = await call({ command: 'FINALIZE', media_id: id }, { method: 'POST' });
	const started = now();
	while (status.processing_info && status.processing_info.state !== 'succeeded') {
		const processing = status.processing_info;
		if (processing.state === 'failed') throw new Error(`X couldn’t process ${basename(path)}${processing.error?.message ? `: ${processing.error.message}` : ''}`);
		if (now() - started > PROCESSING_TIMEOUT_MS) throw new Error('X is taking too long to process the video; try again later');
		onProgress({ phase: 'processing', fraction: (processing.progress_percent || 0) / 100 });
		await sleep(Math.min(Math.max(Number(processing.check_after_secs) || 1, 1), 10) * 1000);
		status = await call({ command: 'STATUS', media_id: id }, { method: 'GET' });
	}
	onProgress({ phase: 'processing', fraction: 1 });
	return id;
}

module.exports = { mediaInfo, uploadChunked, CHUNK_BYTES };
