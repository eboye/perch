'use strict';
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

const isShortLink = (url) => typeof url === 'string' && /^https?:\/\/t\.co\/\w+$/.test(url);

module.exports = { expandLinks };
