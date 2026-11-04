import glob
import os
import re
import subprocess
import sys

from k7ctl import i18n

LOCALE = os.path.join(os.path.dirname(i18n.__file__), "locale")


def test_languages_from_environment():
    assert i18n.languages({"LANGUAGE": "pt_BR:de", "LANG": "en_US.UTF-8"}) == ["pt_BR", "pt", "de"]
    assert i18n.languages({"LANG": "de_DE.UTF-8"}) == ["de_DE", "de"]
    assert i18n.languages({"LANG": "C"}) == []


def test_every_translation_keeps_placeholders():
    for po in glob.glob(os.path.join(LOCALE, "*.po")):
        catalog = i18n.parse_po(open(po, encoding="utf-8").read())
        assert catalog, po
        for msgid, msgstr in catalog.items():
            assert sorted(re.findall(r"\{[^}]*\}", msgid)) == sorted(re.findall(r"\{[^}]*\}", msgstr)), \
                (os.path.basename(po), msgid)
            assert msgstr.format(**{k: "x" for k in re.findall(r"\{(\w+)\}", msgid)}, **{}) if "{}" not in msgid \
                else msgstr.format("x")


def test_load_picks_language(tmp_path):
    cat = i18n.load({"LANGUAGE": "de"})
    assert cat["Headphone EQ"] == "Kopfhörer-EQ"
    assert i18n.load({"LANGUAGE": "xx"}) == {}


def test_template_up_to_date():
    root = os.path.dirname(os.path.dirname(os.path.abspath(i18n.__file__)))
    tool = os.path.join(root, "tools", "update-translations.py")
    if not os.path.exists(tool):      # installed package without the source tree
        return
    r = subprocess.run([sys.executable, tool, "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
