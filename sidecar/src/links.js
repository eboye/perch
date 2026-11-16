'use strict';
const { bindingMap, valueOf } = require('./poll');
// X sends a post's text with every link as a t.co short link. The library keeps only the list of
// real URLs, not which short link each one replaces, so the raw post is used here to put the real
// links back in the text.

// The text with each t.co link replaced by its real URL; the link X adds for attached photos /
// videos removed (they're shown as media); and the trailing link to a quoted post removed (it's
// shown as the quote). `raw` is the GraphQL tweet (legacy + note_tweet for long posts).
function expandLinks(text, raw, quotedId) {
	if (typeof text !== 'string' || !text) return text;
	const legacy = raw?.legacy || {};
	const note = raw?.note_tweet?.note_tweet_results?.result;
	const urls = [...(legacy.entities?.urls || []), ...(note?.entity_set?.urls || [])];
	const media = [...(legacy.extended_entities?.media || []), ...(legacy.entities?.media || [])];

	const real = new Map();
	for (const entity of urls) if (isShortLink(entity?.url) && entity.expanded_url) real.set(entity.url, entity.expanded_url);
	const mediaLinks = new Set(media.map((m) => m?.url).filter(isShortLink));
	const quoteLink = quotedId ? new RegExp(`/status/${quotedId}(?:\\D|$)`) : null;
	const end = text.trimEnd().length;

	// One pass over whole links, so a link that is a prefix of another is never touched by mistake.
	const out = text.replace(/https?:\/\/t\.co\/\w+/g, (short, at) => {
		if (mediaLinks.has(short)) return '';
		const expanded = real.get(short);
		if (!expanded) return short;
		if (quoteLink?.test(expanded) && at + short.length === end) return ''; // the quote's own link
		return expanded;
	});
	// Removing links can leave spaces at line ends (or a dangling line): tidy what was touched.
	return out === text ? text : out.replace(/[ \t]+$/gm, '').trimEnd();
}

// A link preview (X's own card for the post's link: X fetched the page, Perch doesn't).
// -> { url, title, description, domain, image: { url, width, height, alt } | null, style } or null.
// style: 'large' (a wide picture above), 'small' (a thumbnail beside) or 'player' (a video page).
const CARD_IMAGES = {
	summary_large_image: ['photo_image_full_size_large', 'summary_photo_image_large', 'photo_image_full_size', 'thumbnail_image_large'],
	player: ['player_image_large', 'player_image', 'thumbnail_image_large'],
	summary: ['thumbnail_image_large', 'thumbnail_image', 'summary_photo_image_large'],
};
const CARD_STYLE = { summary_large_image: 'large', player: 'player', summary: 'small' };

function parseLinkCard(card, raw) {
	const legacy = card?.legacy || card;
	const name = String(legacy?.name || '');
	if (!CARD_STYLE[name]) return null;
	const values = bindingMap(legacy.binding_values);
	const text = (key) => {
		const value = valueOf(values[key]);
		return typeof value === 'string' && value.trim() ? value.trim() : null;
	};
	// The card's t.co link, as the real address (from the post's links).
	const short = text('card_url');
	const urls = [...(raw?.legacy?.entities?.urls || []), ...(raw?.note_tweet?.note_tweet_results?.result?.entity_set?.urls || [])];
	const url = urls.find((u) => u?.url === short)?.expanded_url || (short && !isShortLink(short) ? short : null) || urls[0]?.expanded_url || null;
	const title = text('title');
	if (!url || !title) return null;
	let image = null;
	for (const key of CARD_IMAGES[name]) {
		const value = values[key]?.image_value;
		if (value?.url) {
			image = { url: value.url, width: Number(value.width) || null, height: Number(value.height) || null, alt: text(`${key.replace(/_(large|small|x_large|original)$/, '')}_alt_text`) };
			break;
		}
	}
	return { url, title, description: text('description'), domain: text('vanity_url') || text('domain') || hostOf(url), image, style: CARD_STYLE[name] };
}

function hostOf(url) {
	try {
		return new URL(url).host.replace(/^www\./, '');
	} catch {
		return null;
	}
}

const isShortLink = (url) => typeof url === 'string' && /^https?:\/\/t\.co\/\w+$/.test(url);

module.exports = { expandLinks, parseLinkCard };
