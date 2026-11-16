'use strict';
// Polls. X sends a poll as a "card" on the post (name poll2choice_text_only … poll4choice_image)
// whose values are a list of { key, value } (GraphQL) or a { key: value } map (the REST answer to a
// vote). Voting and creating polls go through X's cards API.

const VOTE_URL = 'https://caps.x.com/v2/capi/passthrough/1';
const CREATE_URL = 'https://caps.x.com/v2/cards/create.json';
const MAX_CHOICE = 25; // X's limit for a choice's text
const MIN_MINUTES = 5;
const MAX_MINUTES = 7 * 24 * 60;

function bindingMap(values) {
	const map = {};
	if (Array.isArray(values)) for (const item of values) map[item?.key] = item?.value;
	else if (values && typeof values === 'object') Object.assign(map, values);
	return map;
}

const valueOf = (value) => value?.string_value ?? value?.boolean_value ?? value?.scribe_key ?? null;

// { cardUri, cardName, choices: [{ label, count }], total, endsAt, final, selected } or null.
// `card` is the GraphQL card ({ rest_id, legacy: { name, url, binding_values } }) or a REST card.
function parsePoll(card) {
	const legacy = card?.legacy || card;
	const name = String(legacy?.name || '');
	const match = /^poll(\d)choice/.exec(name);
	if (!match) return null;
	const values = bindingMap(legacy.binding_values);
	const choices = [];
	for (let i = 1; i <= Number(match[1]); i++) {
		const label = valueOf(values[`choice${i}_label`]);
		if (label == null) continue;
		choices.push({ label: String(label), count: Number(valueOf(values[`choice${i}_count`])) || 0 });
	}
	if (choices.length < 2) return null;
	const selected = Number(valueOf(values.selected_choice));
	return {
		cardUri: String(card?.rest_id || legacy.url || ''),
		cardName: name,
		choices,
		total: choices.reduce((sum, c) => sum + c.count, 0),
		endsAt: valueOf(values.end_datetime_utc) || null,
		final: valueOf(values.counts_are_final) === true || valueOf(values.counts_are_final) === 'true',
		selected: Number.isInteger(selected) && selected >= 1 ? selected : null,
	};
}

// The form X's web client sends to vote. `choice` is 1-based.
function voteForm({ tweetId, cardUri, cardName, choice }) {
	if (!/^card:\/\/\d+$/.test(String(cardUri || ''))) throw new Error('This poll can’t be voted on');
	if (!/^poll\dchoice/.test(String(cardName || ''))) throw new Error('This poll can’t be voted on');
	const n = Number(choice);
	if (!Number.isInteger(n) || n < 1 || n > 4) throw new Error('Pick one of the choices');
	return new URLSearchParams({
		'twitter:string:card_uri': cardUri,
		'twitter:long:original_tweet_id': String(tweetId),
		'twitter:string:response_card_name': cardName,
		'twitter:string:cards_platform': 'Web-12',
		'twitter:string:selected_choice': String(n),
	});
}

// The card_data X's web client sends to create a poll. { choices: string[], minutes }
function pollCardData({ choices, minutes }) {
	const labels = (choices || []).map((c) => String(c || '').trim());
	if (labels.length < 2 || labels.length > 4) throw new Error('A poll needs 2 to 4 choices');
	if (labels.some((c) => !c)) throw new Error('Fill in every choice (or remove the empty one)');
	if (labels.some((c) => [...c].length > MAX_CHOICE)) throw new Error(`Choices can be up to ${MAX_CHOICE} characters`);
	const duration = Math.round(Number(minutes));
	if (!Number.isFinite(duration) || duration < MIN_MINUTES || duration > MAX_MINUTES) throw new Error('A poll runs from 5 minutes to 7 days');
	const data = {
		'twitter:card': `poll${labels.length}choice_text_only`,
		'twitter:api:api:endpoint': '1',
		'twitter:long:duration_minutes': duration,
	};
	labels.forEach((label, i) => (data[`twitter:string:choice${i + 1}_label`] = label));
	return data;
}

module.exports = { bindingMap, valueOf, parsePoll, voteForm, pollCardData, VOTE_URL, CREATE_URL, MAX_CHOICE };
