'use strict';
// Offline fixtures (PERCH_DEMO=1) so the UI can be developed and checked without an account.

const users = [
	['1001', 'Ada Park', 'adapark', true],
	['1002', 'Linus Ortega', 'lortega', false],
	['1003', 'Mira Chen', 'mirachen', true],
	['1004', 'GNOME Fan Club', 'gnomefans', false],
	['1005', 'Sam Okafor', 'samokafor', false],
].map(([id, fullName, userName, isVerified]) => ({
	id,
	fullName,
	userName,
	isVerified,
	createdAt: 'Mon Jan 02 10:00:00 +0000 2017',
	description: `Hi, I am ${fullName}.`,
	followersCount: 1200,
	followingsCount: 300,
	likeCount: 5000,
	pinnedTweets: [],
	profileImage: '',
	statusesCount: 4200,
}));

const texts = [
	'Shipped a GTK4 + libadwaita app today. The new breakpoints API makes adaptive layouts so much nicer than they used to be.',
	'Hot take: a timeline should be chronological by default. #UX',
	'Reading through the Flatpak docs again. https://docs.flatpak.org/en/latest/ is honestly great.',
	'Coffee count today: 3. Bugs fixed: 1. Bugs introduced: 2.',
	'Long post incoming. Sometimes the best way to understand a protocol is to write a tiny client for it — you learn which fields actually matter, which ones are legacy, and how much state a "simple" feed really carries. Pagination cursors alone are an adventure.',
	'@adapark congrats on the release!',
	'Weekend plan: absolutely nothing.',
];

function stamp(minutesAgo) {
	const d = new Date(Date.now() - minutesAgo * 60000);
	const day = d.toUTCString().slice(0, 3);
	const mon = d.toUTCString().slice(8, 11);
	const pad = (n) => String(n).padStart(2, '0');
	return `${day} ${mon} ${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} +0000 ${d.getUTCFullYear()}`;
}

const votes = new Map(); // demo poll votes: tweet id -> choice (1-based)

function demoPoll(id, ended) {
	const counts = ended ? [412, 1310, 96] : [57, 31, 12, 4];
	const labels = ended ? ['Tabs', 'Spaces', 'Whatever the linter says'] : ['GTK', 'Qt', 'Electron', 'Something else'];
	const choice = ended ? 2 : votes.get(id) || null;
	const choices = labels.map((label, i) => ({ label, count: counts[i] + (votes.get(id) === i + 1 ? 1 : 0) }));
	return {
		cardUri: `card://${id}0`,
		cardName: `poll${labels.length}choice_text_only`,
		choices,
		total: choices.reduce((sum, c) => sum + c.count, 0),
		endsAt: new Date(Date.now() + (ended ? -2 : 26) * 3600 * 1000).toISOString(),
		final: ended,
		selected: choice,
	};
}

// Voting in a demo poll. -> the poll with your vote
function vote(id, choice) {
	votes.set(id, choice);
	return demoPoll(id, false);
}

// Posting in demo mode: uploads of videos / GIFs report progress like the real ones.
async function post({ media }, progress = () => {}) {
	if ((media || []).some((kind) => kind !== 'image')) {
		for (const fraction of [0.25, 0.5, 0.75, 1]) {
			progress({ phase: 'upload', fraction });
			await new Promise((resolve) => setTimeout(resolve, 150));
		}
		for (const fraction of [0.3, 0.8]) {
			progress({ phase: 'processing', fraction });
			await new Promise((resolve) => setTimeout(resolve, 150));
		}
	}
	return { id: String(Date.now()) };
}

function tweet(n, kind, nested = false) {
	const by = users[(n + (kind === 'foryou' ? 2 : 0)) % users.length];
	const id = String(900000 + n * 7 + (kind === 'foryou' ? 1 : 0));
	const t = {
		id,
		conversationId: id,
		createdAt: stamp(n * 37 + 2),
		entities: { hashtags: [], mentionedUsers: [], urls: [] },
		// One post in German on the Following timeline (for Translate Post).
		fullText: n === 3 && kind === 'following' && !nested ? 'Guten Morgen! Heute baue ich weiter an meiner GTK-App.' : `${texts[n % texts.length]}`,
		lang: n === 3 && kind === 'following' && !nested ? 'de' : 'en',
		likeCount: (n * 131) % 2400,
		quoteCount: n % 4,
		replyCount: (n * 7) % 90,
		retweetCount: (n * 13) % 300,
		viewCount: (n * 3571) % 250000,
		tweetBy: by,
		url: `https://x.com/${by.userName}/status/${id}`,
		liked: n % 3 === 1,
		retweeted: n % 7 === 2,
		bookmarked: n % 8 === 5,
	};
	if (n % 11 === 2) t.edited = true;
	if (n % 9 === 6) {
		t.fullText = 'Going live in a Space about GTK app design — come hang out https://x.com/i/spaces/1DemoSpaceAbc';
		t.entities.urls = ['https://x.com/i/spaces/1DemoSpaceAbc'];
	}
	if (n % 10 === 8 && n > 0) {
		// A reply to the previous post, so the post page has a parent to show.
		const parent = tweet(n - 1, kind, true);
		t.replyTo = parent.id;
		t.fullText = `@${parent.tweetBy.userName} ${t.fullText}`;
	}
	if (nested) return t;
	// Polls: one still open on Following, one that has ended (with your vote).
	if (kind === 'following' && (n === 5 || n === 12)) {
		t.poll = demoPoll(id, n === 12);
		// Someone else's poll (you can't vote in your own).
		if (t.tweetBy.id === users[0].id) {
			t.tweetBy = users[1];
			t.url = `https://x.com/${users[1].userName}/status/${id}`;
		}
	}
	// A link preview on the post that links the Flatpak docs: large on Following, small on For You.
	if (t.fullText.includes('docs.flatpak.org') && !t.retweetedTweet && n % 6 !== 3) {
		t.linkCard = {
			url: 'https://docs.flatpak.org/en/latest/',
			title: 'Flatpak — the future of application distribution',
			description: 'Build, distribute and run sandboxed desktop applications on Linux.',
			domain: 'docs.flatpak.org',
			image: { url: `data:image/png;base64,${DEMO_PHOTO_PNG}`, width: 2, height: 1, alt: 'The Flatpak logo' },
			style: kind === 'foryou' ? 'small' : 'large',
		};
	}
	// Ads (X marks them as promoted): one on Following, one on For You.
	if ((kind === 'following' && n === 11) || (kind === 'foryou' && n === 7)) t.promoted = true;
	if (n % 6 === 3) t.quoted = tweet(n + 50, kind, true);
	if (n % 5 === 4) {
		const original = tweet(n + 100, kind, true);
		t.fullText = `RT @${original.tweetBy.userName}: ${original.fullText}`;
		t.retweetedTweet = original;
	}
	return t;
}

const usersById = () => Object.fromEntries(users.map((u) => [u.id, u]));
const iso = (minutesAgo) => new Date(Date.now() - minutesAgo * 60000).toISOString();

function notifications(cursor) {
	if (cursor) return { list: [], next: '', users: usersById() };
	// The links X sends with each kind (a follow links the person; new posts from someone you get
	// notifications for, an internal API path; a Space, its page).
	const notificationUrls = {
		person_icon: `https://twitter.com/${users[4].userName}`,
		recommendation_icon: '/2/notifications/device_follow.json',
		live_icon: 'https://x.com/i/spaces/1demoSpace',
	};
	// Two separate likes on the same post (900000): Perch shows them as one row.
	const rows = [
		['heart_icon', [users[1], users[2]], 'Linus Ortega and Mira Chen liked your post', 3, '900000'],
		['retweet_icon', [users[3]], 'GNOME Fan Club reposted your post', 18, '900007'],
		['heart_icon', [users[4]], 'Sam Okafor liked your post', 40, '900000'],
		['recommendation_icon', [users[2]], 'New post notifications for Mira Chen', 64, null],
		['live_icon', [users[3]], 'GNOME Fan Club is hosting a Space: GTK app design hangout', 95, null],
		['person_icon', [users[4]], 'Sam Okafor followed you', 300, null],
	];
	return {
		list: rows.map(([icon, from, message, minutes, post], i) => ({
			id: `demo-n${i}`,
			message,
			receivedAt: iso(minutes),
			from: from.map((u) => u.id),
			target: post ? [post] : [],
			icon,
			url: notificationUrls[icon] || 'https://x.com/notifications',
			postText: post ? texts[(Number(post) - 900000) / 7 % texts.length] : null,
		})),
		next: '',
		users: usersById(),
	};
}

const conversations = [
	['1001-1002', users[1], ['Did you see the new libadwaita release?', 'Yes! The breakpoints work great.', 'Want to pair on Perch later?'], 4, true],
	['1001-1003', users[2], ['Thanks for the review 🙏', 'https://www.youtube.com/watch?v=abcdefghijk', 'Anytime'], 80, false],
	['1001-1005', users[4], ['Space tonight at 8?'], 600, false],
	// Second (older) inbox page, reached by scrolling.
	['1001-1004', users[3], ['Welcome to the club!'], 60 * 24 * 90, false],
	['1002-1001', users[1], ['Old thread from last year'], 60 * 24 * 400, false],
];
const INBOX_PAGE = 3;

function inbox(cursor) {
	const page = cursor === 'older' ? conversations.slice(INBOX_PAGE) : conversations.slice(0, INBOX_PAGE);
	return {
		conversations: page.map(([id, other, texts, minutes, unread]) => ({
			id,
			type: 'ONE_TO_ONE',
			name: null,
			avatarUrl: null,
			participants: [users[0].id, other.id],
			trusted: true,
			muted: false,
			lastActivityAt: iso(minutes),
			lastMessage: { id: `${id}-m${texts.length - 1}`, senderId: unread ? other.id : users[0].id, text: texts[texts.length - 1], createdAt: iso(minutes), isEncrypted: false, mediaUrls: [] },
			unread,
		})),
		cursor: cursor === 'older' ? '' : 'older',
		users: usersById(),
	};
}

function conversation(id) {
	const entry = conversations.find(([cid]) => cid === id) || conversations[0];
	const [cid, other, texts, minutes] = entry;
	const messages = texts.map((text, i) => ({
		id: `${cid}-m${i}`,
		senderId: i % 2 === 0 ? other.id : users[0].id,
		text,
		createdAt: iso(minutes + (texts.length - i) * 5),
		isEncrypted: false,
		mediaUrls: [],
	}));
	messages.splice(1, 0, { id: `${cid}-enc`, senderId: other.id, text: '', createdAt: iso(minutes + 30), isEncrypted: true, mediaUrls: [] });
	// A message type Perch can't show (no text, no media, not encrypted).
	if (cid === '1001-1003') messages.push({ id: `${cid}-blank`, senderId: other.id, text: '', createdAt: iso(minutes + 1), isEncrypted: false, mediaUrls: [] });
	// A photo from the other person (served by dmMedia below), a GIF, a reply and some reactions.
	if (cid === '1001-1005') {
		messages.push({ id: `${cid}-photo`, senderId: other.id, text: 'Here is the poster', createdAt: iso(minutes + 2), isEncrypted: false, mediaUrls: [DEMO_PHOTO_URL] });
		messages.push({ id: `${cid}-gif`, senderId: other.id, text: '', createdAt: iso(minutes + 1), isEncrypted: false, mediaUrls: [DEMO_GIF_URL] });
		messages.push({
			id: `${cid}-reply`,
			senderId: users[0].id,
			text: 'Love it!',
			createdAt: iso(minutes),
			isEncrypted: false,
			mediaUrls: [],
			replyTo: { id: `${cid}-photo`, text: 'Here is the poster' },
		});
	}
	for (const message of messages) {
		message.replyTo = message.replyTo || null;
		message.reactions = (demoReactions.get(message.id) || []).map(([emoji, senderId]) => ({ emoji, senderId }));
	}
	// A long conversation: enough recent messages for "Load Earlier Messages" to show.
	if (cid === '1001-1002') {
		for (let i = 0; i < 24; i++) {
			messages.push({ id: `${cid}-r${i}`, senderId: i % 2 ? users[0].id : other.id, text: `Recent message ${i + 1}`, createdAt: iso(minutes + 60 + (24 - i) * 10), isEncrypted: false, mediaUrls: [] });
		}
	}
	messages.sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
	return { id: cid, messages, hasMore: false, cursor: '', participants: [users[0].id, other.id], users: usersById() };
}

// Older history of the long demo conversation: two pages (30, then 10 messages), then the start.
function olderMessages(id, before) {
	const other = users[1];
	const secondPage = String(before).includes('-old1-');
	const count = secondPage ? 10 : 30;
	const base = secondPage ? 60 * 24 * 400 : 60 * 24 * 30;
	const prefix = secondPage ? 'old2' : 'old1';
	const messages = Array.from({ length: count }, (_, i) => ({
		id: `${id}-${prefix}-${i}`,
		senderId: i % 2 ? users[0].id : other.id,
		text: `${secondPage ? 'Very old' : 'Older'} message ${i + 1}`,
		createdAt: iso(base + (count - i) * 60),
		isEncrypted: false,
		mediaUrls: [],
	}));
	return { messages, cursor: secondPage ? '' : `${id}-old1-0`, users: usersById() };
}

const participant = (u) =>({ id: u.id, screenName: u.userName, displayName: u.fullName, avatarUrl: '', isVerified: u.isVerified });

function details(id) {
	const raw = Number(id) - 900000;
	const kind = raw % 7 === 1 ? 'foryou' : 'following';
	const n = Math.max(0, Math.floor((raw - (kind === 'foryou' ? 1 : 0)) / 7));
	return { ...tweet(n, kind), id: String(id) };
}

const replyTexts = ['Totally agree!', 'Not sure about this one 🤔', 'Great point.', 'Source?', 'This is the way.', 'Bookmarked.', 'Can you share more details?', '😂😂'];

function replies(id, cursor) {
	const page = cursor ? Number(cursor) : 0;
	const list = replyTexts.map((text, i) => {
		const by = users[(i + page) % users.length];
		const rid = `${id}${page}${i}`;
		return {
			id: rid,
			conversationId: String(id),
			createdAt: stamp(10 + i * 7 + page * 60),
			entities: { hashtags: [], mentionedUsers: [], urls: [] },
			fullText: text,
			lang: 'en',
			likeCount: (i * 17) % 90,
			replyCount: i % 3,
			retweetCount: i % 2,
			viewCount: 100 + i * 37,
			replyTo: String(id),
			tweetBy: by,
			url: `https://x.com/${by.userName}/status/${rid}`,
			liked: false,
			retweeted: false,
			bookmarked: false,
		};
	});
	return { list, next: page < 1 ? String(page + 1) : '' };
}

function profile(key) {
	const user = users.find((u) => u.id === String(key) || u.userName === String(key).replace(/^@/, '')) || users[1];
	return {
		...user,
		description: `${user.fullName} writes about GTK, Flatpak and the open desktop. Views my own. #GNOME`,
		location: 'Belgrade',
		website: 'https://gnome.org',
		createdAt: 'Mon Jan 02 10:00:00 +0000 2017',
		youFollow: user.id === '1002',
		followsYou: user.id !== '1004',
		protected: false,
		muted: mutedIds.has(user.id),
		blocked: blockedIds.has(user.id),
		highlightCount: user.id === '1003' ? 12 : 0,
	};
}

// First-page timeline fetches per kind (PERCH_DEMO_GROWING).
const firstPageFetches = {};

// Muted accounts, kept in memory for the demo session.
const mutedIds = new Set(['1005']);

function muted() {
	return { list: [...mutedIds].map((id) => profile(id)), next: '' };
}

function setMuted(id, mute) {
	if (mute) mutedIds.add(String(id));
	else mutedIds.delete(String(id));
	return { ok: true };
}

// The demo DM photo: a small blue PNG.
const DEMO_PHOTO_URL = 'https://ton.x.com/i/ton/data/dm/1/2/demo-photo.png';
const DEMO_PHOTO_PNG =
	'iVBORw0KGgoAAAANSUhEUgAAAEAAAAAwCAIAAAAuKetIAAAATklEQVR4nO3PAQkAIADAMBEr2dCoBjHGEbYGW/vc8bM5PidQE6gJ1ARqAjWBmkBNoCZQE6gJ1ARqAjWBmkBNoCZQE6gJ1ARqAjWBmkDtAbsWAgC3nm8oAAAAAElFTkSuQmCC';

const DEMO_GIF_URL = 'https://video.twimg.com/dm_gif/1/demo.mp4';

// Which demo user is signed in: key "demo" / "demo-1" is the first, "demo-2" the second (account tests).
let demoAccount = 0;

function signIn(apiKey) {
	const match = /^demo-(\d)$/.exec(String(apiKey || ''));
	if (apiKey && apiKey !== 'demo' && !match) throw Object.assign(new Error('not a valid demo key (use demo, demo-1 or demo-2)'), { kind: 'auth' });
	demoAccount = match ? Math.max(0, Math.min(users.length - 1, Number(match[1]) - 1)) : 0;
	return { ok: true };
}

// Reactions on demo messages, kept for the session: message id -> [[emoji, senderId]].
const demoReactions = new Map([['1001-1005-photo', [['❤️', '1005'], ['❤️', '1001']]]]);

function react(messageId, emoji, remove) {
	const list = (demoReactions.get(messageId) || []).filter(([e, by]) => !(e === emoji && by === '1001'));
	if (!remove) list.push([emoji, '1001']);
	demoReactions.set(messageId, list);
	return { ok: true };
}

// Muted words, kept for the session.
let demoMutedWords = [{ id: '7001', keyword: 'spoilers' }];

function mutedWords() {
	return { list: demoMutedWords, next: '' };
}

function addMutedWord(keyword) {
	demoMutedWords = [...demoMutedWords, { id: String(7000 + demoMutedWords.length + 2), keyword }];
	return { ok: true };
}

function removeMutedWord(id) {
	demoMutedWords = demoMutedWords.filter((w) => w.id !== id);
	return { ok: true };
}

function dmMedia() {
	return { type: 'image/png', data: DEMO_PHOTO_PNG };
}

// A post's "translation": the original text, marked, from German.
function translate(id) {
	return { text: `[Translated] ${details(id).fullText}`, from: 'German', to: 'en' };
}

// Blocked accounts, kept in memory for the demo session (starts empty).
const blockedIds = new Set();

function blocked() {
	return { list: [...blockedIds].map((id) => profile(id)), next: '' };
}

function setBlocked(id, block) {
	if (block) blockedIds.add(String(id));
	else blockedIds.delete(String(id));
	return { ok: true };
}

// Mentions: replies from other people that @mention you, two pages.
function mentions(cursor) {
	const page = cursor ? Number(cursor) : 0;
	const list = Array.from({ length: 8 }, (_, i) => {
		const by = users[1 + ((page * 8 + i) % 4)];
		const t = tweet(page * 8 + i + 200, 'following', true);
		return { ...t, tweetBy: by, fullText: `@${users[0].userName} ${t.fullText}`, replyTo: '900001', url: `https://x.com/${by.userName}/status/${t.id}` };
	});
	return { list, next: page < 1 ? String(page + 1) : '' };
}

// Who to follow: the demo accounts you don't follow (and aren't you).
function suggestions() {
	return { list: users.slice(1).map((u) => profile(u.id)).filter((u) => !u.youFollow), next: '' };
}

function userPosts(id, kind, cursor) {
	const page = cursor ? Number(cursor) : 0;
	// Likes are posts by other people; everything else (highlights too) is by the profile's owner.
	const by = kind === 'likes' ? null : users.find((u) => u.id === String(id)) || users[1];
	const list = Array.from({ length: 10 }, (_, i) => {
		const t = tweet(page * 10 + i + ({ replies: 40, media: 80, likes: 120, highlights: 160 }[kind] || 0), 'following', true);
		return by ? { ...t, tweetBy: by, url: `https://x.com/${by.userName}/status/${t.id}` } : t;
	});
	return { list, next: page < 1 ? String(page + 1) : '' };
}

function searchPosts(query, mode, cursor) {
	const page = cursor ? Number(cursor) : 0;
	const list = Array.from({ length: 8 }, (_, i) => {
		const t = tweet(page * 8 + i + (mode === 'latest' ? 20 : mode === 'media' ? 60 : 0), 'foryou', true);
		return { ...t, fullText: `${t.fullText} — matching “${query}”` };
	});
	return { list, next: page < 1 ? String(page + 1) : '' };
}

function searchUsers(query) {
	const q = query.toLowerCase();
	const matches = users.filter((u) => u.userName.includes(q) || u.fullName.toLowerCase().includes(q));
	return { list: (matches.length ? matches : users).map((u) => profile(u.id)), next: '' };
}

function bookmarks(folderId, cursor) {
	const page = cursor ? Number(cursor) : 0;
	const list = Array.from({ length: 6 }, (_, i) => ({ ...tweet(page * 6 + i + 120, 'foryou', true), bookmarked: true }));
	return { list, next: page < 1 ? String(page + 1) : '' };
}

function lists() {
	return {
		list: [
			['5001', 'GTK & GNOME', 'People building the GNOME desktop', 42, 310, false, users[0]],
			['5002', 'Read later', '', 7, 0, true, users[0]],
			['5003', 'Flatpak news', 'Packaging, portals and runtimes', 18, 1200, false, users[3]],
		].map(([id, name, description, memberCount, subscriberCount, isPrivate, owner]) => ({
			id,
			name,
			description: description || undefined,
			memberCount,
			subscriberCount,
			private: isPrivate,
			createdAt: '2025-03-01T10:00:00.000Z',
			createdBy: owner.id,
			ownerId: owner.id,
			ownerHandle: owner.userName,
			ownerName: owner.fullName,
			isFollowing: owner.id !== users[0].id,
			isMember: false,
			bannerUrl: null,
		})),
		next: '',
	};
}

// Edit history of a post: the current text plus two earlier drafts, newest first.
function history(id) {
	const base = details(id);
	const texts = [base.fullText, `${base.fullText} (first draft, with a typo fixed later)`, `${base.fullText} teh original`];
	const list = texts.map((fullText, i) => ({ ...base, id: String(BigInt(base.id) - BigInt(i)), fullText, createdAt: stamp(5 + i * 12), edited: true }));
	return { list, next: '' };
}

// Scheduled posts, kept in memory for the demo session.
let scheduledPosts = null;

function scheduled() {
	if (scheduledPosts === null) {
		const now = Math.floor(Date.now() / 1000);
		scheduledPosts = [
			{ id: '9001', text: 'Release notes for the next Perch build go out tomorrow morning.', executeAt: now + 20 * 3600, state: 'Scheduled', mediaCount: 0 },
			{ id: '9002', text: 'Weekly GTK tips thread — this week: list views and sections.', executeAt: now + 4 * 86400, state: 'Scheduled', mediaCount: 2 },
		];
	}
	return { list: [...scheduledPosts].sort((a, b) => a.executeAt - b.executeAt), next: '' };
}

function schedule(text, mediaCount, at) {
	scheduled();
	const id = String(9100 + scheduledPosts.length);
	scheduledPosts.push({ id, text: text || '', executeAt: at, state: 'Scheduled', mediaCount });
	return { id };
}

function unschedule(id) {
	scheduled();
	scheduledPosts = scheduledPosts.filter((item) => item.id !== String(id));
	return { ok: true };
}

// UI tests: "X can't be reached" while the file named by PERCH_DEMO_OFFLINE_FILE exists.
function offline() {
	const flag = process.env.PERCH_DEMO_OFFLINE_FILE;
	return Boolean(flag) && require('fs').existsSync(flag);
}

module.exports = {
	vote,
	post,
	offline,
	signIn,
	react,
	mutedWords,
	addMutedWord,
	removeMutedWord,
	dmMedia,
	translate,
	blocked,
	setBlocked,
	mentions,
	olderMessages,
	muted,
	setMuted,
	suggestions,
	bookmarks,
	history,
	scheduled,
	schedule,
	unschedule,
	lists,
	searchPosts,
	searchUsers,
	profile,
	userPosts,
	details,
	replies,
	notifications,
	inbox,
	conversation,
	me: () => users[demoAccount],
	space: (id) => ({
		id,
		state: 'Running',
		title: 'GTK app design hangout',
		mediaKey: '28_demo',
		startedAt: new Date(Date.now() - 42 * 60000).toISOString(),
		totalLiveListeners: 318,
		participantCount: 324,
		isSpaceAvailableForReplay: true,
		participants: {
			total: 324,
			admins: [participant(users[3])],
			speakers: [participant(users[0]), participant(users[2])],
			listeners: [participant(users[1]), participant(users[4])],
		},
	}),
	timeline: (kind, cursor) => {
		const page = cursor ? Number(cursor) : 0;
		const list = Array.from({ length: 15 }, (_, i) => tweet(page * 15 + i, kind));
		// PERCH_DEMO_GROWING=1 (UI tests): every new fetch of the first page has 2 more new posts on top.
		if (!page && process.env.PERCH_DEMO_GROWING === '1') {
			const fetches = (firstPageFetches[kind] = (firstPageFetches[kind] || 0) + 1);
			const fresh = Array.from({ length: (fetches - 1) * 2 }, (_, i) => ({ ...tweet(i, kind), id: String(990000 + i), fullText: `Brand new post ${i + 1}` }));
			list.unshift(...fresh.reverse());
		}
		return { list, next: page < 4 ? String(page + 1) : '' };
	},
};
