"""Muted words: X's own list (Settings → Muted words), managed here, and matching posts hidden in
Perch's timelines, lists and mentions as well."""

import re

from gi.repository import Adw, GLib, Gtk


def compile_words(words: list[str]) -> list[re.Pattern]:
    """One pattern per muted word or phrase: whole words only (``cat`` doesn't hide ``category``),
    case-insensitive; ``#tags`` and ``@names`` match as written."""
    patterns = []
    for word in words:
        word = word.strip()
        if word:
            patterns.append(re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE))
    return patterns


def tweet_text(tweet: dict) -> str:
    """The text a muted word is looked for in: the post, and the post it quotes or reposts."""
    shown = tweet.get("retweetedTweet") or tweet
    parts = [shown.get("fullText") or "", (shown.get("quoted") or {}).get("fullText") or ""]
    return "\n".join(parts)


def matches(patterns: list[re.Pattern], tweet: dict) -> bool:
    if not patterns:
        return False
    text = tweet_text(tweet)
    return any(pattern.search(text) for pattern in patterns)


class MutedWordsPage(Adw.NavigationPage):
    def __init__(self, controller):
        super().__init__(title="Muted Words", tag="muted-words")
        self._controller = controller

        page = Adw.PreferencesPage()
        add_group = Adw.PreferencesGroup(
            description="Posts containing these are hidden from your timelines, lists and mentions — in Perch and on X. Words match whole words, in any case.",
        )
        self._entry = Adw.EntryRow(title="Add a word, phrase or #tag", show_apply_button=True)
        self._entry.connect("apply", lambda *_: self._add())
        self._entry.connect("entry-activated", lambda *_: self._add())
        add_group.add(self._entry)
        page.add(add_group)

        self._group = Adw.PreferencesGroup(title="Muted")
        self._empty = Adw.ActionRow(title="You haven’t muted any words")
        self._empty.add_css_class("dim-label")
        page.add(self._group)
        self._rows: list[Gtk.Widget] = []

        headerbar = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        headerbar.pack_end(refresh)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self.reload()

    def reload(self) -> None:
        self._controller.sidecar.call("mutedWords.list", {}, self._on_list)

    def _on_list(self, result, error) -> None:
        if error:
            self._controller.report_error(error, "Couldn't load muted words")
            return
        for row in self._rows:
            self._group.remove(row)
        self._rows = []
        words = result.get("list") or []
        for word in words:
            row = Adw.ActionRow(title=GLib.markup_escape_text(word["keyword"]))
            remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=f"Unmute “{word['keyword']}”", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.connect("clicked", lambda button, w=word: self._remove(button, w))
            row.add_suffix(remove)
            self._group.add(row)
            self._rows.append(row)
        if not words:
            self._group.add(self._empty)
            self._rows.append(self._empty)
        self._controller.on_muted_words([w["keyword"] for w in words])

    def _add(self) -> None:
        word = self._entry.get_text().strip()
        if not word:
            return
        self._entry.set_sensitive(False)

        def done(_result, error):
            self._entry.set_sensitive(True)
            if error:
                self._controller.report_error(error, f"Couldn't mute “{word}”")
                return
            self._entry.set_text("")
            self._controller.toast(f"Muted “{word}”")
            self.reload()

        self._controller.sidecar.call("mutedWords.add", {"keyword": word}, done)

    def _remove(self, button: Gtk.Button, word: dict) -> None:
        button.set_sensitive(False)

        def done(_result, error):
            if error:
                button.set_sensitive(True)
                self._controller.report_error(error, f"Couldn't unmute “{word['keyword']}”")
                return
            self._controller.toast(f"Unmuted “{word['keyword']}”")
            self.reload()

        self._controller.sidecar.call("mutedWords.remove", {"id": word["id"]}, done)
