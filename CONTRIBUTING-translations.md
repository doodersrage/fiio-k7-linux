# Translating k7ctl

The settings window and tray icon can be translated. (The `k7ctl` command line output stays in
English, like most Linux command line tools.)

Translations are ordinary gettext `.po` files, so any `.po` editor works: [Poedit](https://poedit.net),
Lokalize, GNOME Translation Editor, or a plain text editor.

## Add a language

1. Copy the template `k7ctl/locale/k7ctl.pot` to `k7ctl/locale/<code>.po`, where `<code>` is the
   language code: `de`, `fr`, `es`, `pt_BR`, `zh_CN`, …
2. Fill in the `msgstr ""` lines. Keep anything in braces, like `{profile}`, exactly as it is (it's
   replaced by a name or number), and keep the `…` ellipses.
3. Try it: `LANGUAGE=<code> python -m k7ctl gui` from the source folder.
4. Optionally translate the app menu names too: add `Name[<code>]=…` and `Comment[<code>]=…` lines to
   `data/k7ctl.desktop` and `data/k7ctl-tray.desktop`.
5. Open a pull request with the file(s).

Untranslated messages simply show in English, so a partial translation is fine.

## Update a language after the program changed

Run `python tools/update-translations.py`. It refreshes the template and every `.po` file: existing
translations are kept, new messages are added empty, and messages that no longer exist are removed. It
prints how much of each language is translated.

## For developers

Mark user-visible text in `k7ctl/window.py`, `k7ctl/tray.py`, `k7ctl/curvewidget.py` and `k7ctl/snapshot.py` (`why_resampled`) with `tr("…")`. For values, use a
placeholder and `.format()`: `tr("Removed {profile}.").format(profile=name)`, never an f-string,
so translators can move the value within the sentence. Then run `python tools/update-translations.py`;
CI fails if the template is out of date.

## Current languages

German (`de`), Spanish (`es`) and French (`fr`) were machine-translated as a starting point. If you
speak one of them, corrections are very welcome — even a single fixed message.
