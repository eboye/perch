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

function tweet(n, kind, nested = false) {
	const by = users[(n + (kind === 'foryou' ? 2 : 0)) % users.length];
	const id = String(900000 + n * 7 + (kind === 'foryou' ? 1 : 0));
	const t = {
		id,
		conversationId: id,
		createdAt: stamp(n * 37 + 2),
		entities: { hashtags: [], mentionedUsers: [], urls: [] },
		fullText: `${texts[n % texts.length]}`,
		lang: 'en',
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
		t.fullText = 'Going live in a Space about GTK app design — come hang out https://t.co/demoSpace';
		t.entities.urls = ['https://x.com/i/spaces/1DemoSpaceAbc'];
	}
	if (n % 10 === 8 && n > 0) {
		// A reply to the previous post, so the post page has a parent to show.
		const parent = tweet(n - 1, kind, true);
		t.replyTo = parent.id;
		t.fullText = `@${parent.tweetBy.userName} ${t.fullText}`;
	}
	if (nested) return t;
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
	const rows = [
		['bell_icon', [users[1], users[2], users[4]], 'Linus Ortega, Mira Chen and 1 other liked your post', 3],
		['bird_icon', [users[3]], 'GNOME Fan Club reposted your post', 18],
		['recommendation_icon', [users[2]], 'New post notifications for Mira Chen', 64],
		['live_icon', [users[3]], 'GNOME Fan Club is hosting a Space: GTK app design hangout', 95],
		['bell_icon', [users[4]], 'Sam Okafor followed you', 300],
	];
	return {
		list: rows.map(([icon, from, message, minutes], i) => ({
			id: `demo-n${i}`,
			message,
			receivedAt: iso(minutes),
			from: from.map((u) => u.id),
			target: [],
			icon,
			url: 'https://x.com/notifications',
		})),
		next: '',
		users: usersById(),
	};
}

const conversations = [
	['1001-1002', users[1], ['Did you see the new libadwaita release?', 'Yes! The breakpoints work great.', 'Want to pair on Perch later?'], 4, true],
	['1001-1003', users[2], ['Thanks for the review 🙏', 'Anytime'], 80, false],
	['1001-1005', users[4], ['Space tonight at 8?'], 600, false],
];

function inbox() {
	return {
		conversations: conversations.map(([id, other, texts, minutes, unread]) => ({
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
		cursor: '',
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
	messages.sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
	return { id: cid, messages, hasMore: false, cursor: '', participants: [users[0].id, other.id], users: usersById() };
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
	};
}

function userPosts(id, kind, cursor) {
	const page = cursor ? Number(cursor) : 0;
	// Likes are posts by other people; everything else is by the profile's owner.
	const by = kind === 'likes' ? null : users.find((u) => u.id === String(id)) || users[1];
	const list = Array.from({ length: 10 }, (_, i) => {
		const t = tweet(page * 10 + i + ({ replies: 40, media: 80, likes: 120 }[kind] || 0), 'following', true);
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

module.exports = {
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
	me: () => users[0],
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
		return { list, next: page < 4 ? String(page + 1) : '' };
	},
};
