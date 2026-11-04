import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import desktop_upstream as upstream
import build_index
from filter_usable_fonts import usable_font, compact_catalog


class DesktopCatalogTests(unittest.TestCase):
    def test_compact_catalog_preserves_face_values_and_original_input(self):
        font = {"family": "Example", "style": "Regular", "license": "License text",
                "language_tags": ["en"], "commercial_use": False,
                "faces": [{"family": "Example", "style": "Regular", "license": "License text",
                           "language_tags": ["en"], "commercial_use": False},
                          {"family": "Alternate", "style": "Bold", "language_tags": ["zh"],
                           "license": "Different license", "commercial_use": True}]}
        original = json.loads(json.dumps(font))
        result = compact_catalog([font])
        self.assertEqual(len(result), 1)
        for old, new in zip(font["faces"], result[0]["faces"]):
            for key, value in old.items():
                self.assertEqual(new.get(key, result[0].get(key)), value)
        self.assertEqual(font, original)
        self.assertNotIn("license", result[0]["faces"][0])

    def test_brand_prefix_language_and_english_boundaries(self):
        rules = [
            {'id': 'founder', 'name': '方正', 'keywords': ['方正', 'Founder'], 'abbreviations': ['FZ']},
            {'id': 'adobe', 'name': 'Adobe', 'keywords': ['Adobe']},
            {'id': 'hanyi', 'name': '汉仪', 'keywords': ['汉仪', 'Hanyi'], 'abbreviations': ['HY']},
        ]
        def match(name, zh=None, maker=''):
            return build_index.brand_for({'family': name, 'family_zh': zh or [], 'manufacturer': maker}, rules, {})[0]
        self.assertEqual(match('Adobe Example', ['方正书宋']), 'founder')
        self.assertEqual(match('FZShuSong'), 'founder')
        self.assertEqual(match('HYQiHei'), 'hanyi')
        self.assertEqual(match('hanyi-QiHei'), 'hanyi')
        for name in ('Hydrogen', 'hymn', 'NotAdobe', 'Founderish', '普通黑体'):
            self.assertEqual(match(name), 'unknown')
        self.assertEqual(match('Example', maker='Adobe Systems Incorporated'), 'adobe')
        self.assertEqual(match('Example', maker='FontForge'), 'unknown')

    def test_complete_unicode_chinese_name_precedes_truncated_mac_name(self):
        from fontTools.ttLib import TTFont, newTable
        from fontTools.ttLib.tables._n_a_m_e import NameRecord
        font = TTFont()
        font['name'] = newTable('name')
        font['name'].names = []
        for platform, encoding, language, name in ((1, 25, 33, '站'), (3, 1, 2052, '站酷高端黑')):
            record = NameRecord()
            record.nameID, record.platformID, record.platEncID, record.langID = 1, platform, encoding, language
            record.string = name.encode('gb2312' if platform == 1 else 'utf-16-be')
            font['name'].names.append(record)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fixture.ttf'
            font.save(path)
            info = build_index.inspect(path)[0]
        self.assertEqual(info['family_zh'][0], '站酷高端黑')
        self.assertNotIn('站', info['family_zh'])
        rules = [{'id': 'zcool', 'name': '站酷', 'keywords': ['站酷', 'ZCOOL']}]
        self.assertEqual(build_index.brand_for({**info, 'family': 'unrelated', 'full_name': ''}, rules, {}), ('zcool', '站酷'))
        self.assertEqual(build_index.category(info['family_zh'][0], 'Regular'), '黑体')

    def test_formats_and_paths(self):
        base = {"format": "ttf", "path": "fonts/品牌/font.ttf", "size": 100, "sha256": "a" * 64}
        self.assertTrue(usable_font(base))
        for fmt in ("woff", "woff2", "ttc", "otc", "remote"):
            self.assertFalse(usable_font({**base, "format": fmt, "path": "fonts/a/font." + fmt}))
        for path in ("fonts/../font.ttf", "fonts//font.ttf", "fonts/a/font.woff2"):
            self.assertFalse(usable_font({**base, "path": path}))
        self.assertEqual(build_index.EXTENSIONS, {".ttf", ".otf"})

    def test_external_pinned_file(self):
        for source, repo, path in (("google-fonts", "google/fonts", "ofl/test/Test.ttf"),
                                    ("fontsource", "fontsource/font-files", "sources/test/files/Test.otf")):
            row = {"source": source, "format": Path(path).suffix[1:], "path": "fonts/" + source + "/" + path,
                   "download_url": upstream.raw_url(repo, "a" * 40, path), "size": 100, "sha256": "b" * 64}
            self.assertTrue(usable_font(row))
            for bad in ("https://example.com/font.ttf", row["download_url"].replace("a" * 40, "main"),
                        row["download_url"] + "?redirect=1", row["download_url"].replace("Test.", "Different.")):
                self.assertFalse(usable_font({**row, "download_url": bad}))

    def test_truncated_tree_is_completed(self):
        responses = [{"truncated": True, "tree": []}, {"tree": [{"type": "tree", "path": "family", "sha": "sub"}]},
                     {"truncated": False, "tree": [{"type": "blob", "path": "a.ttf", "sha": "blob"}]}]
        with patch.object(upstream, "api", side_effect=responses):
            self.assertEqual(list(upstream.walk_tree("google/fonts", "root", "ofl"))[0]["path"], "ofl/family/a.ttf")

    def test_content_hash_and_cache(self):
        binary = b"\x00\x01\x00\x00fixture"
        blob = hashlib.sha1(b"blob " + str(len(binary)).encode() + b"\0" + binary).hexdigest()
        item = {"path": "ofl/test/Test.ttf", "size": len(binary), "sha": blob}
        with tempfile.TemporaryDirectory() as folder, patch.object(upstream, "CACHE", Path(folder)):
            with patch.object(upstream, "read_url", return_value=binary), patch.object(upstream, "inspect", return_value=[{"unicode_codepoint_count": 20}]) as inspect:
                data = upstream.inspect_blob("google/fonts", "a" * 40, item)
                self.assertEqual(data["sha256"], hashlib.sha256(binary).hexdigest())
                self.assertEqual(upstream.inspect_blob("google/fonts", "a" * 40, item), data)
                self.assertEqual(inspect.call_count, 1)
            with patch.object(upstream, "read_url", return_value=b"corrupt"):
                with self.assertRaises(ValueError):
                    upstream.inspect_blob("google/fonts", "a" * 40, {**item, "sha": "b" * 40})


class OrganizerRegressionTests(unittest.TestCase):
    def run_repository(self, root, inspector):
        with patch.multiple(build_index, ROOT=root, FONTS=root / "fonts", INCOMING=root / "incoming", DIST=root / "dist"), patch.object(build_index, "inspect", side_effect=inspector) as parser:
            build_index.main()
            return parser.call_count

    @staticmethod
    def face(family):
        return [{"family": family, "style": "Regular", "full_name": family,
                 "manufacturer": "", "license": ""}]

    def test_rename_cycle_cache_and_reclassification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a = root / "fonts/未知品牌/alpha/font.ttf"
            b = root / "fonts/未知品牌/beta/font.ttf"
            for path, payload in ((a, b"beta"), (b, b"alpha")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            inode = {p.read_bytes(): p.stat().st_ino for p in (a, b)}
            self.assertEqual(self.run_repository(root, lambda p: self.face(p.read_text())), 2)
            for path in (a, b):
                self.assertEqual(path.stat().st_ino, inode[path.read_bytes()])
            self.assertEqual(a.read_bytes(), b"alpha")
            (root / "metadata").mkdir()
            (root / "metadata/overrides.json").write_text(json.dumps({"font.ttf": {"family": "New family"}}))
            self.assertEqual(self.run_repository(root, lambda p: self.fail("Warm cache reparsed font")), 0)
            catalog = json.loads((root / "dist/fonts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(catalog), 2)
            self.assertTrue(all(f["family"] == "New family" for f in catalog))
            self.assertEqual({p.read_bytes() for p in (root / "fonts").rglob("*.ttf")}, {b"alpha", b"beta"})

    def test_invalid_input_leaves_files_and_catalog_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "incoming").mkdir()
            font = root / "incoming/bad.ttf"
            font.write_bytes(b"bad")
            (root / "dist").mkdir()
            catalog = root / "dist/fonts.json"
            catalog.write_text("[]")
            with self.assertRaises(RuntimeError):
                self.run_repository(root, lambda p: (_ for _ in ()).throw(ValueError("Invalid font")))
            self.assertEqual(font.read_bytes(), b"bad")
            self.assertEqual(catalog.read_text(), "[]")

    def test_duplicate_content_inspected_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "incoming/a").mkdir(parents=True)
            (root / "incoming/b").mkdir()
            for parent in ("a", "b"):
                (root / "incoming" / parent / "font.ttf").write_bytes(b"same")
            self.assertEqual(self.run_repository(root, lambda p: self.face("Example")), 1)
            self.assertEqual(len(list((root / "fonts").rglob("*.ttf"))), 1)
            self.assertEqual(len(json.loads((root / "dist/fonts.json").read_text(encoding="utf-8"))), 1)


class ShardedCatalogTests(unittest.TestCase):
    def test_byte_and_record_bounded_shards_preserve_all_metadata(self):
        import shard_catalog as shards
        with tempfile.TemporaryDirectory() as folder:
            dist = Path(folder)
            fonts = [{"id": str(i), "format": "ttf", "license": "许可全文" * 15} for i in range(9)]
            (dist / "fonts.json").write_text(json.dumps(fonts), encoding="utf-8")
            (dist / "brands.json").write_text('[{"id":"test"}]')
            (dist / "manifest.json").write_text('{"schema_version":1}')
            with patch.object(shards, "TARGET_BYTES", 700), patch.object(shards, "MAX_RECORDS", 2):
                manifest = shards.publish(dist)
            restored = []
            for part in manifest["fonts_shards"]:
                rows = json.loads((dist / Path(part["path"]).name).read_bytes())
                self.assertLessEqual(len(rows), 2)
                restored.extend(rows)
            self.assertEqual(restored, fonts)
            target = dist / Path(manifest["fonts_shards"][0]["path"]).name
            target.write_bytes(b"[]")
            with self.assertRaises(ValueError):
                shards.validate(dist)

    def test_shards_handle_catalog_larger_than_single_file_limit(self):
        import shard_catalog as shards
        with tempfile.TemporaryDirectory() as folder:
            dist = Path(folder)
            fonts = [{"id": str(i), "format": "otf", "license": "x" * (2 * 1024 * 1024)} for i in range(13)]
            (dist / "fonts.json").write_bytes(shards.encode(fonts))
            (dist / "brands.json").write_text('[{"id":"test"}]')
            (dist / "manifest.json").write_text('{}')
            self.assertGreater((dist / "fonts.json").stat().st_size, 24 * 1024 * 1024)
            manifest = shards.publish(dist)
            self.assertEqual(manifest["catalog_count"], 13)
            self.assertTrue(all(p["size"] < shards.MAX_BYTES for p in manifest["fonts_shards"]))


if __name__ == "__main__":
    unittest.main()
