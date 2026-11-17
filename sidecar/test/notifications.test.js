'use strict';
// node --test: what a notification without a target is about (Community Notes "needs your help").

const test = require('node:test');
const assert = require('node:assert/strict');
const { contextPost } = require('../src/inbox');

const excerpt = 'A post that may need a note.\n\n';
const postUrl = 'https://x.com/someone/status/1234567890123456789';
const noteUrl = 'https://x.com/i/birdwatch/n/1987654321';
const raw = {
	notification_icon: 'birdwatch_icon',
	notification_url: { url: 'https://twitter.com/i/birdwatch/n/1987654321?src=ntab&source=0', urlType: 'ExternalUrl' },
	template: {
		__typename: 'TimelineNotificationAggregateUserActions',
		additional_context: {
			text: `${excerpt}${postUrl} ${noteUrl}`,
			entities: [
				{ fromIndex: excerpt.length, toIndex: excerpt.length + postUrl.length, ref: { type: 'TimelineUrl', url: postUrl, urlType: 'ExternalUrl' } },
				{ fromIndex: excerpt.length + postUrl.length + 1, toIndex: excerpt.length + postUrl.length + 1 + noteUrl.length, ref: { type: 'TimelineUrl', url: noteUrl, urlType: 'ExternalUrl' } },
			],
		},
		from_users: [],
		target_objects: [],
	},
};

test('a Community Notes notification is about the post linked in its context text', () => {
	assert.deepEqual(contextPost(raw), { ids: ['1234567890123456789'], text: 'A post that may need a note.' });
});

test('notifications without a post in their context give nothing', () => {
	assert.deepEqual(contextPost({}), { ids: [], text: null });
	assert.deepEqual(contextPost(null), { ids: [], text: null });
	const onlyNote = { template: { additional_context: { text: `Rate it ${noteUrl}`, entities: [{ fromIndex: 8, toIndex: 8 + noteUrl.length, ref: { url: noteUrl } }] } } };
	assert.deepEqual(contextPost(onlyNote), { ids: [], text: null });
});
