'use strict';
// Ads. X sends a promoted post as a timeline entry ("promoted-tweet-…") whose item carries
// `promotedMetadata`. The library turns it into an ordinary post, so the raw responses a call gets
// are looked at here to note which posts were ads; they're then marked `promoted: true`.
//
// Each rettiwt service's `request` is wrapped once; the ids go to whichever call is running
// (AsyncLocalStorage), so calls that overlap don't mix up each other's ads.

const { AsyncLocalStorage } = require('node:async_hooks');

const running = new AsyncLocalStorage();

// The ids of the posts sent as ads anywhere in a GraphQL response.
function collectPromoted(root, into = new Set()) {
	const stack = [root];
	let visited = 0;
	while (stack.length && visited++ < 200000) {
		const node = stack.pop();
		if (!node || typeof node !== 'object') continue;
		if (node.promotedMetadata && node.tweet_results) {
			const result = node.tweet_results.result;
			const id = result?.rest_id || result?.tweet?.rest_id;
			if (id) into.add(String(id));
		}
		for (const value of Array.isArray(node) ? node : Object.values(node)) {
			if (value && typeof value === 'object') stack.push(value);
		}
	}
	return into;
}

// Note ads in every response `service` (a rettiwt service: user, tweet, list) gets.
function watch(service) {
	if (!service || service.perchWatchesAds) return;
	const original = service.request.bind(service);
	service.request = async (...args) => {
		const response = await original(...args);
		const ads = running.getStore();
		if (ads) collectPromoted(response?.data, ads);
		return response;
	};
	service.perchWatchesAds = true;
}

// Run `call` and return { data, ads }: what it returned, and the ids X sent as ads meanwhile.
async function trackAds(call) {
	const ads = new Set();
	const data = await running.run(ads, call);
	return { data, ads };
}

// Mark serialized posts that were ads.
function markAds(list, ads) {
	for (const post of list) if (post && ads.has(String(post.id))) post.promoted = true;
	return list;
}

module.exports = { collectPromoted, watch, trackAds, markAds };
