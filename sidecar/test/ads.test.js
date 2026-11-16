'use strict';
// node --test: telling ads (promoted posts) apart in X's responses.

const test = require('node:test');
const assert = require('node:assert/strict');
const { collectPromoted, watch, trackAds, markAds } = require('../src/ads');

const entry = (id, promoted) => ({
	entryId: `${promoted ? 'promoted-tweet' : 'tweet'}-${id}`,
	content: { itemContent: { tweet_results: { result: { __typename: 'Tweet', rest_id: id } }, ...(promoted ? { promotedMetadata: { impressionId: 'x' } } : {}) } },
});
const response = (...entries) => ({ data: { home: { home_timeline_urt: { instructions: [{ type: 'TimelineAddEntries', entries }] } } } });

test('ads are found by their promotedMetadata, also inside modules and visibility wrappers', () => {
	const wrapped = { content: { items: [{ item: { itemContent: { promotedMetadata: {}, tweet_results: { result: { __typename: 'TweetWithVisibilityResults', tweet: { rest_id: '9' } } } } } }] } };
	const found = collectPromoted(response(entry('1', false), entry('2', true), wrapped, entry('3', false)));
	assert.deepEqual([...found].sort(), ['2', '9']);
});

test('each call gets the ads of its own responses, even when calls overlap', async () => {
	let release;
	const gate = new Promise((resolve) => (release = resolve));
	const service = {
		async request(which) {
			if (which === 'slow') await gate;
			return which === 'slow' ? response(entry('10', true)) : response(entry('20', true), entry('21', false));
		},
	};
	watch(service);
	watch(service); // (twice is harmless)
	const slow = trackAds(() => service.request('slow'));
	const fast = await trackAds(() => service.request('fast'));
	release();
	const done = await slow;
	assert.deepEqual([...fast.ads], ['20']);
	assert.deepEqual([...done.ads], ['10']);
	// Outside trackAds nothing is collected (and nothing breaks).
	assert.ok(await service.request('fast'));
});

test('markAds marks only the posts that were ads', () => {
	const posts = markAds([{ id: '1' }, { id: '2' }, null], new Set(['2']));
	assert.equal(posts[0].promoted, undefined);
	assert.equal(posts[1].promoted, true);
});
