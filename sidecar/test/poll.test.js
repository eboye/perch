'use strict';
// node --test: reading polls from X's cards, and the forms for voting and creating one.

const test = require('node:test');
const assert = require('node:assert/strict');
const { parsePoll, voteForm, pollCardData } = require('../src/poll');

const s = (value) => ({ type: 'STRING', string_value: value });

// As in a GraphQL post: { rest_id, legacy: { name, url, binding_values: [{ key, value }] } }
const graphqlCard = {
	rest_id: 'card://1890000000000000001',
	legacy: {
		name: 'poll3choice_text_only',
		url: 'card://1890000000000000001',
		binding_values: [
			{ key: 'choice1_label', value: s('Yes') },
			{ key: 'choice1_count', value: s('10') },
			{ key: 'choice2_label', value: s('No') },
			{ key: 'choice2_count', value: s('5') },
			{ key: 'choice3_label', value: s('Maybe') },
			{ key: 'choice3_count', value: s('0') },
			{ key: 'end_datetime_utc', value: s('2026-11-20T10:00:00Z') },
			{ key: 'counts_are_final', value: { type: 'BOOLEAN', boolean_value: false } },
			{ key: 'duration_minutes', value: s('1440') },
		],
	},
};

test('a poll from a GraphQL post', () => {
	assert.deepEqual(parsePoll(graphqlCard), {
		cardUri: 'card://1890000000000000001',
		cardName: 'poll3choice_text_only',
		choices: [
			{ label: 'Yes', count: 10 },
			{ label: 'No', count: 5 },
			{ label: 'Maybe', count: 0 },
		],
		total: 15,
		endsAt: '2026-11-20T10:00:00Z',
		final: false,
		selected: null,
	});
});

test('a poll from the REST answer to a vote (a key -> value map), with your choice', () => {
	const rest = {
		name: 'poll2choice_text_only',
		url: 'card://42',
		binding_values: { choice1_label: s('A'), choice1_count: s('3'), choice2_label: s('B'), choice2_count: s('8'), selected_choice: s('2'), counts_are_final: { boolean_value: true } },
	};
	const poll = parsePoll(rest);
	assert.equal(poll.cardUri, 'card://42');
	assert.equal(poll.selected, 2);
	assert.equal(poll.final, true);
	assert.equal(poll.total, 11);
});

test('image polls count too; other cards are not polls', () => {
	assert.equal(parsePoll({ legacy: { name: 'poll2choice_image', binding_values: [{ key: 'choice1_label', value: s('x') }, { key: 'choice2_label', value: s('y') }] } }).choices.length, 2);
	assert.equal(parsePoll({ legacy: { name: 'summary_large_image', binding_values: [] } }), null);
	assert.equal(parsePoll(null), null);
	assert.equal(parsePoll({ legacy: { name: 'poll2choice_text_only', binding_values: [{ key: 'choice1_label', value: s('only one') }] } }), null);
});

test('the vote form', () => {
	const form = voteForm({ tweetId: '123', cardUri: 'card://42', cardName: 'poll2choice_text_only', choice: 2 });
	assert.equal(form.get('twitter:string:card_uri'), 'card://42');
	assert.equal(form.get('twitter:long:original_tweet_id'), '123');
	assert.equal(form.get('twitter:string:response_card_name'), 'poll2choice_text_only');
	assert.equal(form.get('twitter:string:selected_choice'), '2');
	assert.throws(() => voteForm({ tweetId: '1', cardUri: 'https://evil', cardName: 'poll2choice_text_only', choice: 1 }), /can’t be voted on/);
	assert.throws(() => voteForm({ tweetId: '1', cardUri: 'card://1', cardName: 'poll2choice_text_only', choice: 5 }), /Pick one/);
});

test('creating a poll: card data, and what is refused', () => {
	assert.deepEqual(pollCardData({ choices: [' GTK ', 'Qt'], minutes: 1440 }), {
		'twitter:card': 'poll2choice_text_only',
		'twitter:api:api:endpoint': '1',
		'twitter:long:duration_minutes': 1440,
		'twitter:string:choice1_label': 'GTK',
		'twitter:string:choice2_label': 'Qt',
	});
	assert.equal(pollCardData({ choices: ['a', 'b', 'c', 'd'], minutes: 5 })['twitter:card'], 'poll4choice_text_only');
	assert.throws(() => pollCardData({ choices: ['only'], minutes: 60 }), /2 to 4 choices/);
	assert.throws(() => pollCardData({ choices: ['a', ''], minutes: 60 }), /Fill in every choice/);
	assert.throws(() => pollCardData({ choices: ['a', 'x'.repeat(26)], minutes: 60 }), /up to 25 characters/);
	assert.throws(() => pollCardData({ choices: ['a', 'b'], minutes: 4 }), /5 minutes to 7 days/);
	assert.throws(() => pollCardData({ choices: ['a', 'b'], minutes: 7 * 1440 + 1 }), /5 minutes to 7 days/);
});
