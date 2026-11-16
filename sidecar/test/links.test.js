'use strict';
// node --test: putting real links back into post text.

const test = require('node:test');
const assert = require('node:assert/strict');
const { expandLinks } = require('../src/links');

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
