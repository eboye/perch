'use strict';
// node --test: putting real links back into post text.

const test = require('node:test');
const assert = require('node:assert/strict');
const { expandLinks, parseLinkCard } = require('../src/links');

const raw = (urls = [], media = [], note = null) => ({
	legacy: { entities: { urls }, extended_entities: { media } },
	...(note ? { note_tweet: { note_tweet_results: { result: { entity_set: { urls: note } } } } } : {}),
});
const link = (short, real) => ({ url: short, expanded_url: real, display_url: real.replace(/^https?:\/\//, '') });

test('a t.co link becomes the real one', () => {
	const text = 'Reading this https://t.co/abc123 today';
	assert.equal(expandLinks(text, raw([link('https://t.co/abc123', 'https://docs.flatpak.org/en/latest/')])), 'Reading this https://docs.flatpak.org/en/latest/ today');
});

test("a post that is only its video's link has no text left", () => {
	assert.equal(expandLinks('https://t.co/t9c0NxQU3B', raw([], [{ url: 'https://t.co/t9c0NxQU3B', type: 'video' }])), '');
});

test('the media link goes, wherever it is, and other links stay expanded', () => {
	const text = 'Look https://t.co/aaa1 and more\nhttps://t.co/med1';
	const out = expandLinks(text, raw([link('https://t.co/aaa1', 'https://example.com/a')], [{ url: 'https://t.co/med1' }, { url: 'https://t.co/med1' }]));
	assert.equal(out, 'Look https://example.com/a and more');
});

test("a quote's trailing link goes; the same link mid-text stays (expanded)", () => {
	const quoteUrl = 'https://twitter.com/someone/status/1234567890';
	assert.equal(expandLinks('So true https://t.co/q1', raw([link('https://t.co/q1', quoteUrl)]), '1234567890'), 'So true');
	assert.equal(expandLinks('See https://t.co/q1 here', raw([link('https://t.co/q1', quoteUrl)]), '1234567890'), `See ${quoteUrl} here`);
	// A link to another post (not the quoted one) stays.
	assert.equal(expandLinks('Also https://t.co/q2', raw([link('https://t.co/q2', 'https://x.com/a/status/12345678901')]), '1234567890'), 'Also https://x.com/a/status/12345678901');
});

test('long posts use the note entities', () => {
	const out = expandLinks('Long post … https://t.co/long1', raw([], [], [link('https://t.co/long1', 'https://example.org/long')]));
	assert.equal(out, 'Long post … https://example.org/long');
});

test('a short link that is a prefix of another is not mangled', () => {
	const out = expandLinks('a https://t.co/ab b https://t.co/abc', raw([link('https://t.co/ab', 'https://two.example'), link('https://t.co/abc', 'https://three.example')]));
	assert.equal(out, 'a https://two.example b https://three.example');
});

test('nothing to change: the text comes back as it was', () => {
	assert.equal(expandLinks('plain  text ', raw()), 'plain  text ');
	assert.equal(expandLinks('', raw()), '');
	assert.equal(expandLinks(undefined, raw()), undefined);
	assert.equal(expandLinks('x https://t.co/zz', null), 'x https://t.co/zz');
});

const str = (value) => ({ type: 'STRING', string_value: value });
const img = (url, width, height) => ({ type: 'IMAGE', image_value: { url, width, height } });
const card = (name, values) => ({ legacy: { name, binding_values: Object.entries(values).map(([key, value]) => ({ key, value })) } });
const rawWith = (short, real) => ({ legacy: { entities: { urls: [link(short, real)] } } });

test('a large link preview: the real link, title, description, site and picture', () => {
	const preview = parseLinkCard(
		card('summary_large_image', {
			card_url: str('https://t.co/card1'),
			title: str(' GTK 5 roadmap '),
			description: str('What is planned next'),
			vanity_url: str('blog.gtk.org'),
			domain: str('gtk.org'),
			photo_image_full_size_large: img('https://pbs.twimg.com/card_img/1/a?format=jpg&name=800x419', 800, 419),
			photo_image_full_size_alt_text: str('The GTK logo'),
		}),
		rawWith('https://t.co/card1', 'https://blog.gtk.org/2026/roadmap/'),
	);
	assert.deepEqual(preview, {
		url: 'https://blog.gtk.org/2026/roadmap/',
		title: 'GTK 5 roadmap',
		description: 'What is planned next',
		domain: 'blog.gtk.org',
		image: { url: 'https://pbs.twimg.com/card_img/1/a?format=jpg&name=800x419', width: 800, height: 419, alt: 'The GTK logo' },
		style: 'large',
	});
});

test('summary cards are small; player cards (video pages) are their own style; no picture is fine', () => {
	const small = parseLinkCard(card('summary', { card_url: str('https://t.co/s'), title: str('Docs'), thumbnail_image_large: img('https://pbs.twimg.com/t.jpg', 144, 144) }), rawWith('https://t.co/s', 'https://www.docs.example/x'));
	assert.equal(small.style, 'small');
	assert.equal(small.domain, 'docs.example'); // (no vanity_url / domain: from the link, without www.)
	const player = parseLinkCard(card('player', { card_url: str('https://t.co/p'), title: str('A video'), player_image_large: img('https://pbs.twimg.com/p.jpg', 1280, 720) }), rawWith('https://t.co/p', 'https://www.youtube.com/watch?v=1'));
	assert.equal(player.style, 'player');
	assert.equal(player.image.width, 1280);
	assert.equal(parseLinkCard(card('summary', { card_url: str('https://t.co/n'), title: str('Plain') }), rawWith('https://t.co/n', 'https://plain.example')).image, null);
});

test('not a link preview: polls, app cards, or no title / no link', () => {
	assert.equal(parseLinkCard(card('poll2choice_text_only', { title: str('x') }), rawWith('https://t.co/a', 'https://a.example')), null);
	assert.equal(parseLinkCard(card('unified_card', { title: str('x') }), rawWith('https://t.co/a', 'https://a.example')), null);
	assert.equal(parseLinkCard(card('summary', { card_url: str('https://t.co/a') }), rawWith('https://t.co/a', 'https://a.example')), null);
	assert.equal(parseLinkCard(card('summary', { card_url: str('https://t.co/zz'), title: str('t') }), { legacy: { entities: { urls: [] } } }), null);
	assert.equal(parseLinkCard(null, null), null);
});
