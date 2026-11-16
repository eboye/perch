'use strict';
// Perch sidecar: newline-delimited JSON-RPC over stdio, wrapping rettiwt-api.
//
// Request:  {"id": 1, "method": "timeline", "params": {"kind": "following"}}
// Response: {"id": 1, "result": ...}  or  {"id": 1, "error": {"message", "kind", "status"}}
//
// stdout is reserved for protocol frames. Anything the library prints goes to stderr.

const writeFrame = process.stdout.write.bind(process.stdout);
for (const level of ['log', 'info', 'debug', 'warn']) {
	console[level] = (...args) => console.error(...args);
}

const readline = require('node:readline');
const { Rettiwt } = require('rettiwt-api');
const { signedFetch } = require('./xhttp');
const inbox = require('./inbox');
const demo = require('./demo');
const { ResponseCache } = require('./cache');
const { UserRequests } = require('rettiwt-api/dist/requests/User');
const { CursoredData } = require('rettiwt-api/dist/models/data/CursoredData');
const { BaseType } = require('rettiwt-api/dist/enums/Data');

let client = null;
const cache = new ResponseCache();
const demoMode = process.env.PERCH_DEMO === '1';

function requireClient() {
	if (!client) {
		const err = new Error('Not signed in');
		err.kind = 'auth';
		throw err;
	}
	return client;
}

// Plain JSON for the UI; the library's models implement toJSON().
const plain = (value) => (value === undefined ? null : JSON.parse(JSON.stringify(value)));

const methods = {
	ping: async () => ({ pong: true, demo: demoMode }),

	init: async ({ apiKey }) => {
		if (demoMode) return demo.signIn(apiKey);
		if (!apiKey) throw Object.assign(new Error('API key is empty'), { kind: 'auth' });
		try {
			client = new Rettiwt({ apiKey: apiKey.trim(), timeout: 20000, maxRetries: 1 });
		} catch (err) {
			client = null;
			throw Object.assign(new Error(`not a valid rettiwt API key (${err?.message || err})`), { kind: 'auth' });
		}
		return { ok: true };
	},

	signout: async () => {
		client = null;
		return { ok: true };
	},

	me: async () => {
		if (demoMode) return demo.me();
		const user = await requireClient().user.details();
		if (!user) throw Object.assign(new Error('Could not load the signed-in account'), { kind: 'auth' });
		return plain(user);
	},

	timeline: async ({ kind, cursor }) => {
		if (demoMode) return demo.timeline(kind, cursor);
		const users = requireClient().user;
		const data = kind === 'foryou' ? await users.recommended(cursor || undefined) : await users.followed(cursor || undefined);
		return { list: data.list.map(serializeTweet), next: data.next };
	},

	// { id } -> Tweet
	'tweet.details': async ({ id }) => {
		if (demoMode) return demo.details(id);
		const tweet = await requireClient().tweet.details(String(id));
		if (!tweet) throw Object.assign(new Error('This post is unavailable'), { kind: 'not_found' });
		return serializeTweet(tweet);
	},

	// Replies under a post. The conversation timeline also contains the post itself and its
	// ancestors; those are dropped so the list is only replies.
	// { id, cursor?, sort?: 'relevance' | 'latest' | 'likes' } -> { list, next }
	'tweet.replies': async ({ id, cursor, sort }) => {
		if (demoMode) return demo.replies(id, cursor);
		const sortBy = { latest: 'LATEST', likes: 'LIKES' }[sort] || 'RELEVANCE';
		const data = await requireClient().tweet.replies(String(id), cursor || undefined, sortBy);
		const list = data.list.filter((t) => t.id !== String(id) && t.replyTo).map(serializeTweet);
		return { list, next: data.next };
	},

	// { handle } or { id } -> user (+ youFollow, followsYou, website, protected)
	'user.profile': async ({ handle, id }) => {
		if (demoMode) return demo.profile(handle || id);
		const key = id || String(handle || '').replace(/^@/, '');
		if (!key) throw new Error('Missing user');
		const user = await requireClient().user.details(String(key));
		if (!user) throw Object.assign(new Error('This account doesn’t exist'), { kind: 'not_found' });
		return serializeUser(user);
	},

	// A user's posts / replies / media, or (kind 'likes') the signed-in account's own likes: X only
	// shows likes to their owner. { id, kind: 'posts'|'replies'|'media'|'likes', cursor? } -> { list, next }
	'user.posts': async ({ id, kind, cursor }) => {
		if (demoMode) return demo.userPosts(id, kind, cursor);
		const users = requireClient().user;
		if (kind === 'likes') {
			const data = await users.likes(20, cursor || undefined);
			return { list: data.list.map(serializeTweet), next: data.next };
		}
		if (kind === 'highlights') {
			const data = await users.highlights(String(id), 20, cursor || undefined);
			return { list: data.list.map(serializeTweet), next: data.list.length ? data.next : '' };
		}
		const fetcher = { replies: users.replies, media: users.media }[kind] || users.timeline;
		const data = await fetcher.call(users, String(id), 20, cursor || undefined);
		return { list: data.list.map(serializeTweet), next: data.next };
	},

	// Remove someone from your followers (they are not notified). { id } -> { ok }
	'user.removeFollower': async ({ id }) => {
		if (!id) throw new Error('Missing user id');
		if (demoMode) return { ok: true };
		const ok = await requireClient().user.removeFollower(String(id));
		if (!ok) throw new Error('X did not confirm removing the follower');
		return { ok: true };
	},

	// Mute or unmute someone: their posts stop showing in your timelines and they aren't told.
	// rettiwt only mutes lists, so this posts to the web client's v1.1 endpoint. { id, mute } -> { ok }
	'user.mute': async ({ id, mute }) => {
		if (!id) throw new Error('Missing user id');
		if (demoMode) return demo.setMuted(String(id), Boolean(mute));
		const action = mute ? 'create' : 'destroy';
		await postForm(`https://x.com/i/api/1.1/mutes/users/${action}.json`, new URLSearchParams({ user_id: String(id) }));
		return { ok: true };
	},

	// Your muted words (X's Settings → Muted words). {} -> { list: [{ id, keyword }] }
	'mutedWords.list': async () => {
		if (demoMode) return demo.mutedWords();
		requireClient();
		const res = await signedFetch(client, new URL('https://x.com/i/api/1.1/mutes/keywords/list.json'));
		if (!res.ok) throw Object.assign(new Error(`Couldn’t load muted words (HTTP ${res.status})`), { status: res.status });
		const body = await res.json();
		return { list: (body.muted_keywords || []).map((k) => ({ id: String(k.id), keyword: k.keyword })), next: '' };
	},

	// Mute a word, phrase or #tag everywhere X applies muted words, with no time limit. { keyword } -> { ok }
	'mutedWords.add': async ({ keyword }) => {
		const word = String(keyword || '').trim();
		if (!word) throw new Error('Type a word to mute');
		if (word.length > MAX_MUTED_WORD) throw new Error(`A muted word can be at most ${MAX_MUTED_WORD} characters`);
		if (demoMode) return demo.addMutedWord(word);
		await postForm(
			'https://x.com/i/api/1.1/mutes/keywords/create.json',
			new URLSearchParams({ keyword: word, mute_surfaces: 'notifications,home_timeline,tweet_replies', mute_option: '', duration: '' }),
		);
		return { ok: true };
	},

	// { id } -> { ok }
	'mutedWords.remove': async ({ id }) => {
		if (!id) throw new Error('Missing muted word');
		if (demoMode) return demo.removeMutedWord(String(id));
		await postForm('https://x.com/i/api/1.1/mutes/keywords/destroy.json', new URLSearchParams({ ids: String(id) }));
		return { ok: true };
	},

	// Block or unblock someone: a block also unfollows both ways and hides them from each other.
	// rettiwt has no block, so this posts to the web client's v1.1 endpoint. { id, block } -> { ok }
	'user.block': async ({ id, block }) => {
		if (!id) throw new Error('Missing user id');
		if (demoMode) return demo.setBlocked(String(id), Boolean(block));
		const action = block ? 'create' : 'destroy';
		await postForm(`https://x.com/i/api/1.1/blocks/${action}.json`, new URLSearchParams({ user_id: String(id), skip_status: '1' }));
		return { ok: true };
	},

	// Accounts you've blocked, most recent first. { cursor? } -> { list: users, next }
	'user.blocked': async ({ cursor }) => {
		if (demoMode) return demo.blocked();
		requireClient();
		const url = new URL('https://x.com/i/api/1.1/blocks/list.json');
		url.search = new URLSearchParams({ count: '40', skip_status: '1', include_entities: 'true', cursor: cursor || '-1' });
		const res = await signedFetch(client, url);
		if (!res.ok) throw Object.assign(new Error(`Couldn’t load blocked accounts (HTTP ${res.status})`), { status: res.status });
		const body = await res.json();
		const next = body.next_cursor_str && body.next_cursor_str !== '0' ? body.next_cursor_str : '';
		return { list: (body.users || []).map((u) => ({ ...serializeLegacyUser(u), blocked: true })), next };
	},

	// Posts that mention you (X's Notifications → Mentions). { cursor? } -> { list: posts, next }
	'notifications.mentions': async ({ cursor }) => {
		if (demoMode) return demo.mentions(cursor);
		requireClient();
		const config = UserRequests.notifications(20, cursor || undefined);
		const variables = { ...JSON.parse(config.params.variables), timeline_type: 'Mentions' };
		const url = new URL(config.url);
		url.search = new URLSearchParams({ ...config.params, variables: JSON.stringify(variables) });
		const res = await signedFetch(client, url);
		if (!res.ok) throw Object.assign(new Error(`Couldn’t load mentions (HTTP ${res.status})`), { status: res.status });
		const data = new CursoredData(await res.json(), BaseType.TWEET);
		return { list: data.list.map(serializeTweet), next: data.list.length ? data.next : '' };
	},

	// Accounts you've muted, most recent first. { cursor? } -> { list: users, next }
	'user.muted': async ({ cursor }) => {
		if (demoMode) return demo.muted();
		requireClient();
		const url = new URL('https://x.com/i/api/1.1/mutes/users/list.json');
		url.search = new URLSearchParams({ count: '40', skip_status: '1', include_entities: 'true', cursor: cursor || '-1' });
		const res = await signedFetch(client, url);
		if (!res.ok) throw Object.assign(new Error(`Couldn’t load muted accounts (HTTP ${res.status})`), { status: res.status });
		const body = await res.json();
		const next = body.next_cursor_str && body.next_cursor_str !== '0' ? body.next_cursor_str : '';
		return { list: (body.users || []).map((u) => ({ ...serializeLegacyUser(u), muted: true })), next };
	},

	// Accounts X suggests following (its Connect tab). {} -> { list: users, next }
	'user.suggestions': async () => {
		if (demoMode) return demo.suggestions();
		const data = await requireClient().user.suggestions();
		const list = data.list.map(serializeUser).filter((u) => !u.youFollow);
		return { list, next: '' };
	},

	// Edit your profile. Only the fields present are sent; an empty string clears that field
	// (rettiwt's updateProfile drops empty fields, so this posts the form itself).
	// { name?, description?, location?, url? } -> { ok }
	'profile.update': async (fields) => {
		const form = new URLSearchParams();
		for (const [key, limit] of Object.entries(PROFILE_LIMITS)) {
			if (fields[key] === undefined || fields[key] === null) continue;
			const value = String(fields[key]).trim();
			if (value.length > limit) throw new Error(`${PROFILE_LABELS[key]} can be at most ${limit} characters`);
			if (key === 'name' && !value) throw new Error('Name can’t be empty');
			form.set(key, value);
		}
		if (![...form.keys()].length) throw new Error('Nothing to update');
		if (demoMode) return { ok: true };
		await postForm('https://x.com/i/api/1.1/account/update_profile.json', form);
		return { ok: true };
	},

	// New profile photo or header. { kind: 'image'|'banner', data: base64 } -> { ok }
	'profile.picture': async ({ kind, data }) => {
		if (!data) throw new Error('Missing image');
		if (kind !== 'image' && kind !== 'banner') throw new Error(`Unknown picture kind: ${kind}`);
		if (demoMode) return { ok: true };
		const form = new URLSearchParams({ [kind]: String(data) });
		// X answers the banner upload with an empty 201, so success is the HTTP status, not a body.
		await postForm(`https://x.com/i/api/1.1/account/update_profile_${kind}.json`, form);
		return { ok: true };
	},

	// { id, follow: boolean } -> { ok }
	'user.follow': async ({ id, follow }) => {
		if (!id) throw new Error('Missing user id');
		if (demoMode) return { ok: true };
		const users = requireClient().user;
		const ok = follow ? await users.follow(String(id)) : await users.unfollow(String(id));
		if (!ok) throw new Error(`X did not confirm the ${follow ? 'follow' : 'unfollow'}`);
		return { ok: true };
	},

	// Post search. The query goes to X as typed (rettiwt joins includeWords verbatim), so X's own
	// operators work: from:user, #tag, "exact phrase", -word, lang:xx, min_faves:N …
	// { query, mode: 'top'|'latest'|'media', cursor? } -> { list, next }
	'search.posts': async ({ query, mode, cursor }) => {
		const q = String(query || '').trim();
		if (!q) return { list: [], next: '' };
		if (demoMode) return demo.searchPosts(q, mode, cursor);
		const raw = mode === 'media' ? `${q} filter:media` : q;
		const data = await requireClient().tweet.search({ includeWords: [raw], top: mode !== 'latest' }, 20, cursor || undefined);
		return { list: data.list.map(serializeTweet), next: data.next };
	},

	// People search. { query, cursor? } -> { list: users, next }
	'search.users': async ({ query, cursor }) => {
		const q = String(query || '').trim().replace(/^@/, '');
		if (!q) return { list: [], next: '' };
		if (demoMode) return demo.searchUsers(q, cursor);
		const data = await requireClient().user.search(q, 20, cursor || undefined);
		return { list: data.list.map(serializeUser), next: data.next };
	},

	// Bookmarked posts, all or one folder. { folderId?, cursor? } -> { list, next }
	bookmarks: async ({ folderId, cursor }) => {
		if (demoMode) return demo.bookmarks(folderId, cursor);
		const users = requireClient().user;
		const data = folderId
			? await users.bookmarkFolderTweets(String(folderId), 20, cursor || undefined)
			: await users.bookmarks(20, cursor || undefined);
		return { list: data.list.map(serializeTweet), next: data.next };
	},

	// Bookmark folders (an X Premium feature: most accounts have none). -> { list: [{ id, name }] }
	'bookmarks.folders': async () => {
		if (demoMode) return { list: [] };
		try {
			const data = await requireClient().user.bookmarkFolders();
			return { list: plain(data.list) || [] };
		} catch (err) {
			console.error('[sidecar] bookmark folders unavailable:', err?.message || err);
			return { list: [] };
		}
	},

	// Your lists (owned and followed). { cursor? } -> { list, next }
	lists: async ({ cursor }) => {
		if (demoMode) return demo.lists();
		const data = await requireClient().user.lists(100, cursor || undefined);
		// X hands back a cursor even on the last page; fewer than requested means there's no more.
		return { list: data.list.map(serializeList), next: data.list.length < 100 ? '' : data.next };
	},

	'list.details': async ({ id }) => {
		if (demoMode) return demo.lists().list.find((l) => l.id === String(id)) || demo.lists().list[0];
		const list = await requireClient().list.details(String(id));
		if (!list) throw Object.assign(new Error('This list doesn’t exist'), { kind: 'not_found' });
		return serializeList(list);
	},

	// { id, cursor? } -> { list: posts, next }
	'list.tweets': async ({ id, cursor }) => {
		if (demoMode) return demo.searchPosts('list', 'latest', cursor);
		const data = await requireClient().list.tweets(String(id), 20, cursor || undefined);
		return { list: data.list.map(serializeTweet), next: data.next };
	},

	// { id, cursor? } -> { list: users, next }
	'list.members': async ({ id, cursor }) => {
		if (demoMode) return demo.searchUsers('', cursor);
		const data = await requireClient().list.members(String(id), 50, cursor || undefined);
		return { list: data.list.map(serializeUser), next: data.next };
	},

	// { name, description?, isPrivate? } -> { id }
	'list.create': async ({ name, description, isPrivate }) => {
		if (!name?.trim()) throw new Error('A list needs a name');
		if (demoMode) return { id: String(Date.now()) };
		const id = await requireClient().list.create({ name: name.trim(), description: description || undefined, isPrivate: Boolean(isPrivate) });
		if (!id) throw new Error('X did not create the list');
		return { id };
	},

	// { id, name?, description?, isPrivate? } -> list
	'list.update': async ({ id, name, description, isPrivate }) => {
		if (demoMode) return { ...demo.lists().list[0], id: String(id), name, description, private: Boolean(isPrivate) };
		const list = await requireClient().list.update(String(id), { name, description, isPrivate });
		if (!list) throw new Error('X did not update the list');
		return serializeList(list);
	},

	'list.delete': async ({ id }) => {
		if (demoMode) return { ok: true };
		const ok = await requireClient().list.delete(String(id));
		if (!ok) throw new Error('X did not delete the list');
		return { ok: true };
	},

	// Is `userId` on list `listId`? rettiwt has no membership lookup, so scan the members (up to
	// 10 pages of 50). { listId, userId } -> { member: boolean, certain: boolean }
	'list.hasMember': async ({ listId, userId }) => {
		if (demoMode) return { member: listId === '5001', certain: true };
		const lists = requireClient().list;
		let cursor;
		for (let page = 0; page < 10; page++) {
			const data = await lists.members(String(listId), 50, cursor);
			if (data.list.some((u) => u.id === String(userId))) return { member: true, certain: true };
			if (!data.next || !data.list.length || data.next === cursor) return { member: false, certain: true };
			cursor = data.next;
		}
		return { member: false, certain: false };
	},

	// { listId, userId, add: boolean } -> { memberCount }
	'list.member': async ({ listId, userId, add }) => {
		if (!listId || !userId) throw new Error('Missing list or user');
		if (demoMode) return { memberCount: null };
		const lists = requireClient().list;
		const count = add ? await lists.addMember(String(listId), String(userId)) : await lists.removeMember(String(listId), String(userId));
		if (count === undefined) throw new Error(`X did not ${add ? 'add' : 'remove'} the member`);
		return { memberCount: count };
	},

	// like | unlike | retweet | unretweet | bookmark | unbookmark
	'tweet.action': async ({ action, id }) => {
		if (!TWEET_ACTIONS.has(action)) throw new Error(`Unsupported action: ${action}`);
		if (!id) throw new Error('Missing tweet id');
		if (demoMode) return { ok: true };
		const ok = await requireClient().tweet[action](String(id));
		if (!ok) throw new Error(`X did not confirm "${action}"`);
		return { ok: true };
	},

	// Delete one of your own posts. { id } -> { ok }
	'tweet.delete': async ({ id }) => {
		if (!id) throw new Error('Missing tweet id');
		if (demoMode) return { ok: true };
		const ok = await requireClient().tweet.unpost(String(id));
		if (!ok) throw new Error('X did not confirm the deletion');
		return { ok: true };
	},

	// X's translation of a post into your account's language. { id } -> { text, from, to }
	'tweet.translate': async ({ id }) => {
		if (!id) throw new Error('Missing tweet id');
		if (demoMode) return demo.translate(String(id));
		requireClient();
		const url = new URL(
			`https://x.com/i/api/1.1/strato/column/None/tweetId=${encodeURIComponent(String(id))},destinationLanguage=None,translationSource=Some(Google),feature=None,timeout=None,onlyCached=None/translation/service/translateTweet`,
		);
		const res = await signedFetch(client, url);
		if (!res.ok) throw Object.assign(new Error(`Couldn’t translate the post (HTTP ${res.status})`), { status: res.status });
		const body = await res.json();
		if (!body.translation) throw new Error('X has no translation for this post');
		let text = body.translation;
		for (const link of body.entities?.urls || []) {
			if (link.url && link.expanded_url) text = text.split(link.url).join(link.expanded_url);
		}
		return { text, from: body.localizedSourceLanguage || body.sourceLanguage || '', to: body.destinationLanguage || '' };
	},

	// Who liked / reposted a post. { id, kind: 'likers'|'retweeters', cursor? } -> { list: users, next }
	'tweet.engagers': async ({ id, kind, cursor }) => {
		if (!id) throw new Error('Missing tweet id');
		if (demoMode) return demo.searchUsers(kind || '', cursor);
		const tweets = requireClient().tweet;
		const fetcher = kind === 'retweeters' ? tweets.retweeters : tweets.likers;
		const data = await fetcher.call(tweets, String(id), 40, cursor || undefined);
		return { list: data.list.map(serializeUser), next: data.next };
	},

	// Every version of an edited post, newest first. { id } -> { list }
	'tweet.history': async ({ id }) => {
		if (!id) throw new Error('Missing tweet id');
		if (demoMode) return demo.history(id);
		const versions = await requireClient().tweet.history(String(id));
		const list = versions.map(serializeTweet).sort((a, b) => compareIds(b.id, a.id));
		return { list, next: '' };
	},

	// A user's followers / the accounts they follow. { id, kind: 'followers'|'following', cursor? }
	'user.connections': async ({ id, kind, cursor }) => {
		if (!id) throw new Error('Missing user id');
		if (demoMode) return demo.searchUsers(kind || '', cursor);
		const users = requireClient().user;
		const fetcher = kind === 'following' ? users.following : users.followers;
		const data = await fetcher.call(users, String(id), 40, cursor || undefined);
		return { list: data.list.map(serializeUser), next: data.next };
	},

	// { text, replyTo?, quote?, media?: [{ path, alt? }] (or legacy mediaPaths: string[]) } -> { id }
	'tweet.post': async ({ text, replyTo, quote, media: attachments, mediaPaths }) => {
		const items = (attachments || (mediaPaths || []).map((path) => ({ path }))).filter((m) => m?.path).slice(0, 4);
		if (!text?.trim() && items.length === 0) throw new Error('Nothing to post');
		if (demoMode) return { id: String(Date.now()) };
		const tweets = requireClient().tweet;
		const media = await uploadMedia(tweets, items);
		const id = await tweets.post({
			text: text || undefined,
			replyTo: replyTo || undefined,
			quote: quote || undefined,
			media: media.length ? media : undefined,
		});
		if (!id) throw new Error('X did not return the new post id');
		return { id };
	},

	// Schedule a new post (X can't schedule replies or quotes). { text, media?: [{ path, alt? }], at: unix seconds } -> { id }
	'tweet.schedule': async ({ text, media: attachments, at }) => {
		const items = (attachments || []).filter((m) => m?.path).slice(0, 4);
		if (!text?.trim() && items.length === 0) throw new Error('Nothing to schedule');
		const when = Number(at);
		if (!Number.isFinite(when) || when * 1000 < Date.now() + 60_000) throw new Error('Pick a time at least a minute from now');
		if (demoMode) return demo.schedule(text, items.length, when);
		const tweets = requireClient().tweet;
		const media = await uploadMedia(tweets, items);
		const id = await tweets.schedule({ text: text || undefined, media: media.length ? media : undefined, scheduleFor: new Date(when * 1000) });
		if (!id) throw new Error('X did not confirm the scheduled post');
		return { id };
	},

	// Your scheduled posts, soonest first. {} -> { list: [{ id, text, executeAt, state, mediaCount }], next }
	'tweet.scheduled': async () => {
		if (demoMode) return demo.scheduled();
		requireClient();
		const url = new URL('https://x.com/i/api/graphql/ITtjAzvlZni2wWXwf295Qg/FetchScheduledTweets');
		url.searchParams.set('variables', JSON.stringify({ ascending: true }));
		const res = await signedFetch(client, url);
		if (!res.ok) throw Object.assign(new Error(`Couldn’t load scheduled posts (HTTP ${res.status})`), { status: res.status });
		const body = await res.json();
		const list = (body?.data?.viewer?.scheduled_tweet_list || []).map((item) => ({
			id: String(item.rest_id),
			text: item.tweet_create_request?.status || '',
			// X takes execute_at in seconds but lists it in milliseconds.
			executeAt: toUnixSeconds(item.scheduling_info?.execute_at),
			state: item.scheduling_info?.state || '',
			mediaCount: (item.tweet_create_request?.media_ids || []).length,
		}));
		return { list, next: '' };
	},

	// Cancel a scheduled post. { id } -> { ok }
	'tweet.unschedule': async ({ id }) => {
		if (!id) throw new Error('Missing scheduled post id');
		if (demoMode) return demo.unschedule(id);
		const ok = await requireClient().tweet.unschedule(String(id));
		if (!ok) throw new Error('X did not confirm cancelling the scheduled post');
		return { ok: true };
	},

	'space.details': async ({ id }) => {
		if (demoMode) return demo.space(id);
		const space = await requireClient().space.details(String(id));
		if (!space) throw Object.assign(new Error('Space not found'), { kind: 'not_found' });
		return plain(space);
	},

	// HLS playlist for a live Space, or its replay once ended. rettiwt has no wrapper for this.
	'space.stream': async ({ mediaKey }) => {
		if (demoMode) throw new Error('Audio is not available in demo mode');
		if (!mediaKey) throw new Error('This Space has no media key');
		requireClient();
		const body = await streamStatus(mediaKey);
		const location = body?.source?.location || body?.source?.noRedirectPlaybackUrl;
		if (!location) throw new Error('X returned no audio stream for this Space');
		return { location, streamType: body?.source?.streamType ?? null };
	},
};

const TWEET_ACTIONS = new Set(['like', 'unlike', 'retweet', 'unretweet', 'bookmark', 'unbookmark']);
const MAX_ALT_TEXT = 1000; // X's limit for image descriptions
const MAX_MUTED_WORD = 140;
// X's limits for the editable profile fields.
const PROFILE_LIMITS = { name: 50, description: 160, location: 30, url: 100 };
const PROFILE_LABELS = { name: 'Name', description: 'Bio', location: 'Location', url: 'Website' };

// Seconds since the epoch from a timestamp in seconds or milliseconds.
function toUnixSeconds(value) {
	const n = Number(value);
	if (!Number.isFinite(n) || n <= 0) return null;
	return n > 1e11 ? Math.round(n / 1000) : n;
}

// Upload attachments, adding alt text after each upload and before the post. -> [{ id }]
async function uploadMedia(tweets, items) {
	const media = [];
	for (const item of items) {
		const mediaId = await tweets.upload(item.path);
		const alt = String(item.alt || '').trim().slice(0, MAX_ALT_TEXT);
		if (alt) await tweets.setMediaMetadata(mediaId, { altText: alt });
		media.push({ id: mediaId });
	}
	return media;
}

// POST a urlencoded form to a v1.1 account endpoint; throws with X's message on failure.
async function postForm(url, form) {
	const res = await signedFetch(requireClient(), url, {
		method: 'POST',
		headers: { 'content-type': 'application/x-www-form-urlencoded' },
		body: form.toString(),
	});
	if (res.ok) return;
	const text = await res.text();
	let message = '';
	try {
		message = JSON.parse(text)?.errors?.[0]?.message || '';
	} catch {
		message = '';
	}
	throw Object.assign(new Error(message || `X refused the change (HTTP ${res.status})`), { status: res.status });
}

// Snowflake ids are too big for Number; compare them as decimal strings.
function compareIds(a, b) {
	const x = String(a);
	const y = String(b);
	return x.length - y.length || (x < y ? -1 : x > y ? 1 : 0);
}

// User flags in rettiwt are named from the other side (isFollowed = you follow them); expose plain
// names, plus the website link and protected state from the raw payload.
function serializeUser(user) {
	const out = plain(user);
	const legacy = user.raw?.legacy || {};
	out.youFollow = Boolean(user.isFollowed);
	out.followsYou = Boolean(user.isFollowing);
	out.website = legacy.entities?.url?.urls?.[0]?.expanded_url || null;
	// Bios come with t.co links; show (and edit) the real ones.
	for (const link of legacy.entities?.description?.urls || []) {
		if (out.description && link.url && link.expanded_url) out.description = out.description.split(link.url).join(link.expanded_url);
	}
	out.protected = Boolean(legacy.protected ?? user.raw?.privacy?.protected);
	out.muted = Boolean(user.raw?.relationship_perspectives?.muting ?? legacy.muting);
	out.blocked = Boolean(user.raw?.relationship_perspectives?.blocking ?? legacy.blocking);
	out.highlightCount = Number(user.raw?.highlights_info?.highlighted_tweets) || 0;
	return out;
}

// A user from a v1.1 REST endpoint (e.g. the muted list), in the same shape as serializeUser.
function serializeLegacyUser(u) {
	let description = u.description || '';
	for (const link of u.entities?.description?.urls || []) {
		if (link.url && link.expanded_url) description = description.split(link.url).join(link.expanded_url);
	}
	return {
		id: String(u.id_str || u.id),
		userName: u.screen_name,
		fullName: u.name,
		description,
		location: u.location || '',
		profileImage: u.profile_image_url_https || null,
		profileBanner: u.profile_banner_url || null,
		isVerified: Boolean(u.verified || u.is_blue_verified),
		followersCount: u.followers_count || 0,
		followingsCount: u.friends_count || 0,
		statusesCount: u.statuses_count || 0,
		createdAt: u.created_at || null,
		youFollow: Boolean(u.following),
		followsYou: Boolean(u.followed_by),
		protected: Boolean(u.protected),
		muted: Boolean(u.muting),
	};
}

// List: add privacy, the owner's handle/name and a banner image from the raw payload.
function serializeList(list) {
	const out = plain(list);
	const raw = list.raw || {};
	const owner = raw.user_results?.result || {};
	out.private = String(raw.mode || '').toLowerCase() === 'private';
	out.ownerId = list.createdBy;
	out.ownerHandle = owner.core?.screen_name || owner.legacy?.screen_name || null;
	out.ownerName = owner.core?.name || owner.legacy?.name || null;
	const banner = raw.custom_banner_media || raw.default_banner_media;
	out.bannerUrl = banner?.media_info?.original_img_url || null;
	return out;
}

// Tweet.toJSON() drops the viewer's own state; pull it from the raw GraphQL payload.
// rettiwt picks the highest-bitrate MP4 (often ~4K). For in-app playback use the best variant whose
// longer side is <= 1280px (falls back to the smallest one); keep the original as `hdUrl`.
const MAX_VIDEO_SIDE = 1280;

function variantSide(url) {
	const match = /\/(\d+)x(\d+)\//.exec(url || '');
	return match ? Math.max(Number(match[1]), Number(match[2])) : Infinity;
}

// X's face boxes for a photo, normalised to 0..1 of the image, plus its aspect ratio. The UI keeps
// (real-sized) faces in view when cropping thumbnails. X's per-aspect "focus_rects" are not passed
// on: they are derived from these same face boxes, false hits included.
function addFocusHints(media, raw) {
	const info = raw?.original_info;
	if (!info?.width || !info?.height) return;
	const W = info.width;
	const H = info.height;
	media.aspect = W / H;
	const orig = raw.features?.orig?.faces || [];
	if (orig.length) media.faces = orig.map((f) => ({ x: f.x / W, y: f.y / H, w: f.w / W, h: f.h / H }));
}

function pickVideoVariants(out, legacy) {
	const rawMedia = legacy.extended_entities?.media || [];
	for (const media of out.media || []) {
		if (media.type === 'PHOTO') addFocusHints(media, rawMedia.find((m) => m.id_str === media.id));
		if (media.type !== 'VIDEO' && media.type !== 'GIF') continue;
		const raw = rawMedia.find((m) => m.id_str === media.id);
		const mp4s = (raw?.video_info?.variants || [])
			.filter((v) => v.content_type === 'video/mp4' && v.url)
			.sort((a, b) => (a.bitrate || 0) - (b.bitrate || 0));
		if (!mp4s.length) continue;
		const fitting = mp4s.filter((v) => variantSide(v.url) <= MAX_VIDEO_SIDE);
		media.hdUrl = media.url;
		media.url = (fitting.length ? fitting[fitting.length - 1] : mp4s[0]).url;
	}
}

function serializeTweet(tweet) {
	if (!tweet) return null;
	const out = plain(tweet);
	const legacy = tweet.raw?.legacy || {};
	out.liked = Boolean(legacy.favorited);
	out.retweeted = Boolean(legacy.retweeted);
	out.bookmarked = Boolean(legacy.bookmarked);
	// Every version's id (the original first); a plain post has one. X puts them at the top level for
	// unedited posts and under `edit_control_initial` once a post has been edited.
	const control = tweet.raw?.edit_control || {};
	const editIds = control.edit_tweet_ids || control.edit_control_initial?.edit_tweet_ids || [];
	out.edited = editIds.length > 1;
	pickVideoVariants(out, legacy);
	if (tweet.quoted) out.quoted = serializeTweet(tweet.quoted);
	if (tweet.retweetedTweet) out.retweetedTweet = serializeTweet(tweet.retweetedTweet);
	return out;
}

const STREAM_HOSTS = ['https://api.x.com/1.1', 'https://x.com/i/api/1.1'];

async function streamStatus(mediaKey) {
	let lastError;
	for (const base of STREAM_HOSTS) {
		const url = new URL(`${base}/live_video_stream/status/${encodeURIComponent(mediaKey)}`);
		const res = await signedFetch(client, url);
		if (res.ok) return res.json();
		const text = (await res.text()).slice(0, 200);
		console.error(`[sidecar] stream status ${res.status} from ${url.host}: ${text}`);
		lastError =
			res.status === 404
				? Object.assign(new Error('No audio stream right now — the Space may not be broadcasting yet, or it just ended'), { kind: 'not_found', status: 404 })
				: Object.assign(new Error(`Stream lookup failed (HTTP ${res.status})`), { status: res.status });
		if (res.status !== 404) break;
	}
	throw lastError;
}

Object.assign(methods, inbox.methods({ requireClient, demoMode }));

function classify(err) {
	const status = err?.status ?? err?.response?.status ?? err?.code;
	let kind = err?.kind || 'error';
	if (status === 401 || status === 403) kind = 'auth';
	else if (status === 429 || status === 88) kind = 'rate_limit';
	else if (['ECONNREFUSED', 'ENOTFOUND', 'ETIMEDOUT', 'ECONNABORTED', 'EAI_AGAIN'].includes(err?.code)) kind = 'network';
	return { message: err?.message || String(err), kind, status: typeof status === 'number' ? status : null };
}

async function handle(line) {
	let request;
	try {
		request = JSON.parse(line);
	} catch {
		return;
	}
	const { id, method, params } = request;
	const fn = methods[method];
	if (process.env.PERCH_DEBUG_RPC === '1') {
		// Development trace: method plus identifier-like params only (never text, keys or paths).
		const ids = Object.fromEntries(Object.entries(params || {}).filter(([k]) => /(^id$|Id$|^kind$|^action$)/.test(k)));
		console.error(`[rpc] ${method} ${JSON.stringify(ids)}`);
	}
	try {
		if (!fn) throw new Error(`Unknown method: ${method}`);
		const result = await cache.run(method, params, (clean) => fn(clean));
		writeFrame(JSON.stringify({ id, result }) + '\n');
	} catch (err) {
		console.error(`[sidecar] ${method} failed:`, err?.message || err);
		writeFrame(JSON.stringify({ id, error: classify(err) }) + '\n');
	}
}

readline.createInterface({ input: process.stdin }).on('line', (line) => void handle(line));
process.stdin.on('end', () => process.exit(0));
