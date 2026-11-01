#!/usr/bin/env python3
"""Observable migration tests; no real account, network or warehouse access."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location("handoff", Path(__file__).with_name("handoff.py"))
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="handoff-test-")
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        H.init_project(self.project)
        for name in ("PROJECT_STATE.md", "NEXT_SESSION.md"):
            (self.project / ".handoff/context" / name).write_text(
                "# Verified fixture\n\n" + "Facts come from local fixture files; no production status is inferred. " * 5,
                encoding="utf-8")
        self.profile = H.load_profile(self.project)
        self.profile["conversation"]["enabled"] = False
        self.original = b"select whs_id, count(*) from example group by whs_id;\n"
        (self.project / "query.sql").write_bytes(self.original)
        (self.project / "copy.sql").write_bytes(self.original)
        (self.project / "empty.txt").write_bytes(b"")
        (self.project / "index.html").write_text("<h1>Offline fixture</h1>", encoding="utf-8")
        (self.project / ".env").write_text("UNSAFE_CONFIG=true", encoding="utf-8")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules/ignore.js").write_text("cache", encoding="utf-8")
        self.archive = self.base / "bundle.zip"

    def tearDown(self):
        self.temp.cleanup()

    def package(self):
        return H.make_bundle(self.project, self.profile, self.archive)

    def mutate_archive(self, mutator):
        changed = self.base / "changed.zip"
        with zipfile.ZipFile(self.archive) as source:
            entries = {n: source.read(n) for n in source.namelist()}
        mutator(entries)
        with zipfile.ZipFile(changed, "w") as out:
            for name, data in entries.items():
                out.writestr(name, data)
        return changed

    def test_full_roundtrip_and_deduplication(self):
        receipt = self.package()
        self.assertEqual(receipt["status"], "PASS")
        with zipfile.ZipFile(self.archive) as archive:
            records = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            queries = [r for r in records if r["path"] in ("query.sql", "copy.sql")]
            self.assertEqual(queries[0]["payload"], queries[1]["payload"])
            self.assertNotIn(".env", {r["path"] for r in records})
            self.assertNotIn("node_modules/ignore.js", {r["path"] for r in records})
        target = self.base / "restored"
        H.restore_bundle(self.archive, target)
        self.assertEqual((target / "query.sql").read_bytes(), self.original)
        self.assertEqual((target / "copy.sql").read_bytes(), self.original)
        (target / "query.sql").write_text("changed", encoding="utf-8")
        self.assertEqual((target / "copy.sql").read_bytes(), self.original)
        self.assertTrue(list((target / ".handoff/history").glob("*/chat/coverage.json")))

    def test_resume_is_idempotent(self):
        self.package()
        target = self.base / "restored"
        H.restore_bundle(self.archive, target)
        resumed = H.restore_bundle(self.archive, target, resume=True)
        self.assertEqual(resumed["restored_files"], 0)
        self.assertGreater(resumed["identical_files_skipped"], 0)

    def test_conflicting_file_is_not_overwritten(self):
        self.package()
        target = self.base / "target"
        target.mkdir()
        (target / "query.sql").write_text("user work", encoding="utf-8")
        with self.assertRaises(ValueError):
            H.restore_bundle(self.archive, target, resume=True)
        self.assertEqual((target / "query.sql").read_text(), "user work")

    def test_symlinks_are_not_followed(self):
        outside = self.base / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.project / "linked").symlink_to(outside)
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            audit = json.loads(archive.read("AUDIT.json"))
            self.assertIn("linked", {r["path"] for r in audit["excluded"]})
        target = self.base / "target"
        target.mkdir()
        (target / ".handoff").symlink_to(self.base / "outside-directory")
        with self.assertRaises(ValueError):
            H.restore_bundle(self.archive, target, resume=True)

    def test_payload_corruption_is_detected(self):
        self.package()
        def corrupt(entries):
            key = next(k for k in entries if k.startswith("payload/"))
            entries[key] += b"corruption"
        changed = self.mutate_archive(corrupt)
        with self.assertRaises(ValueError):
            H.verify_bundle(changed)

    def test_unsafe_zip_path_is_rejected(self):
        self.package()
        changed = self.mutate_archive(lambda e: e.update({"../escape": b"x"}))
        with self.assertRaises(ValueError):
            H.verify_bundle(changed)

    def test_payload_size_is_verified(self):
        self.package()
        def corrupt(entries):
            file_list = json.loads(entries["FILE_MANIFEST.json"])
            file_list["files"][0]["bytes"] += 1
            entries["FILE_MANIFEST.json"] = H.dumps(file_list)
            manifest = json.loads(entries["BUNDLE_MANIFEST.json"])
            manifest["member_hashes"]["FILE_MANIFEST.json"] = H.sha(entries["FILE_MANIFEST.json"])
            entries["BUNDLE_MANIFEST.json"] = H.dumps(manifest)
        changed = self.mutate_archive(corrupt)
        with self.assertRaises(ValueError):
            H.verify_bundle(changed)

    def test_account_source_cannot_be_renamed_into_safe_path(self):
        credentials = self.base / "credentials.json"
        credentials.write_text('{"opaque":"not-a-recognizable-token"}', encoding="utf-8")
        self.profile["external_sources"] = [
            {"source": str(credentials), "destination": "renamed.json", "required": False}]
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            files = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            self.assertNotIn("renamed.json", {r["path"] for r in files})

    def test_inline_credential_excluded_without_modifying_original(self):
        token = "sk-" + "z" * 48
        secret = self.project / "unsafe.txt"
        secret.write_text('api_key="' + token + '"', encoding="utf-8")
        self.package()
        self.assertIn(token, secret.read_text())
        with zipfile.ZipFile(self.archive) as archive:
            files = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            self.assertNotIn("unsafe.txt", {r["path"] for r in files})

    def test_context_escape_is_not_read(self):
        self.profile["context_dir"] = "../outside"
        with self.assertRaises(ValueError):
            self.package()

    def test_restored_external_snapshot_does_not_require_old_machine(self):
        destination = self.project / ".handoff/vendor/library.js"
        destination.parent.mkdir(parents=True)
        destination.write_text("original source", encoding="utf-8")
        self.profile["external_sources"] = [
            {"source": "/unavailable/old-machine/library.js",
             "destination": ".handoff/vendor/library.js", "required": True}]
        self.package()
        self.assertEqual(H.verify_bundle(self.archive)["status"], "PASS")

    def test_raw_session_logs_not_bundled(self):
        raw = self.project / "copied-session.jsonl"
        raw.write_bytes(H.dumps({"type": "session_meta", "payload": {"id": "do-not-export"}}))
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            files = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            self.assertNotIn(raw.name, {r["path"] for r in files})

    def test_visible_chat_byte_boundary_not_ordinal_or_reasoning(self):
        store = self.base / "sessions"
        store.mkdir()
        parent = store / "rollout-parent_segment.jsonl"
        def event(role, text, stamp, **extra):
            return {"type": "event_msg", "timestamp": stamp,
                    "payload": {"type": role, "message": text, **extra}}
        lines = [
            {"type": "session_meta", "payload": {"id": "logical_parent", "cwd": str(self.project)}},
            event("user_message", "earlier request", "2026-10-30T00:00:00Z"),
            event("agent_reasoning", "PRIVATE_REASONING", "2026-10-30T00:00:01Z"),
            event("agent_message", "ANALYSIS_PHASE", "2026-10-30T00:00:02Z", phase="analysis"),
            event("agent_message", "visible answer", "2026-10-30T00:00:03Z"),
        ]
        data = b"".join(H.dumps(r).replace(b"\n", b"") + b"\n" for r in lines)
        bound = len(data)
        parent.write_bytes(data + json.dumps(event("user_message", "FUTURE_BRANCH", "2026-11-01T00:00:00Z")).encode() + b"\n")
        child = store / "rollout-child.jsonl"
        token = "sk-" + "q" * 48
        child_lines = [
            {"type": "session_meta", "payload": {"id": "child", "cwd": str(self.project),
             "history_base": {"thread_id": "parent_segment", "end_byte_offset": bound,
                              "end_ordinal_exclusive": 99999}}},
            event("user_message", "continue", "2026-10-31T00:00:00Z"),
            event("agent_message", 'api_key="' + token + '"', "2026-10-31T00:00:01Z"),
            {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                                "content": "HIDDEN_INSTRUCTION"}},
        ]
        child.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in child_lines))
        self.profile["conversation"]["enabled"] = True
        files, coverage = H.export_conversations(self.project, self.profile, ["child"], str(store))
        text = files["chat/transcript.md"].decode()
        self.assertIn("earlier request", text)
        self.assertIn("visible answer", text)
        self.assertIn("continue", text)
        for excluded in ("PRIVATE_REASONING", "ANALYSIS_PHASE", "FUTURE_BRANCH", token, "HIDDEN_INSTRUCTION"):
            self.assertNotIn(excluded, text)
        self.assertEqual(coverage["message_count"], 4)
        self.assertEqual(len(coverage["segments"]), 2)
        self.assertEqual(coverage["redacted_messages"], 1)

    def test_extracted_bundle_ignores_only_os_metadata(self):
        self.package()
        extracted = self.base / "extracted"
        with zipfile.ZipFile(self.archive) as archive:
            archive.extractall(extracted)
        (extracted / ".DS_Store").write_bytes(b"finder")
        self.assertEqual(H.verify_bundle(extracted)["status"], "PASS")

    def test_sample_restore_is_explicit_and_selective(self):
        self.package()
        target = self.base / "sample"
        result = H.restore_bundle(self.archive, target, only=["query.sql"])
        self.assertEqual(result["scope"], "sample")
        self.assertEqual((target / "query.sql").read_bytes(), self.original)
        self.assertFalse((target / "copy.sql").exists())

    def test_current_item_completed_format_excludes_reasoning_and_tools(self):
        store = self.base / "sessions"
        store.mkdir()
        path = store / "rollout-current.jsonl"
        records = [{"type": "session_meta", "payload": {"id": "current", "cwd": str(self.project)}}]
        for number, kind in enumerate(("UserMessage", "Reasoning", "CommandExecution", "AgentMessage")):
            records.append({
                "type": "event_msg", "timestamp": "2026-10-31T00:00:0%dZ" % number,
                "payload": {"type": "item_completed", "item": {
                    "type": kind, "phase": "final_answer",
                    "content": [{"type": "Text", "text": kind + "_TEXT"}],
                }},
            })
        path.write_bytes(b"".join(json.dumps(r).encode() + b"\n" for r in records))
        self.profile["conversation"]["enabled"] = True
        files, coverage = H.export_conversations(self.project, self.profile, ["current"], str(store))
        text = files["chat/transcript.md"].decode()
        self.assertIn("UserMessage_TEXT", text)
        self.assertIn("AgentMessage_TEXT", text)
        self.assertNotIn("Reasoning_TEXT", text)
        self.assertNotIn("CommandExecution_TEXT", text)
        self.assertEqual(coverage["message_count"], 2)

    def test_nested_credentials_archive_is_excluded(self):
        nested = self.project / "old.zip"
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("credentials.json", '{"opaque":"private"}')
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            files = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            self.assertNotIn("old.zip", {r["path"] for r in files})

    def test_browser_state_is_excluded_even_when_renamed(self):
        browser_state = {"cookies": [{"name": "sessionid", "value": "synthetic-value", "domain": "example.test"}],
                         "origins": []}
        (self.project / "renamed.json").write_text(json.dumps(browser_state), encoding="utf-8")
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            files = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            self.assertNotIn("renamed.json", {r["path"] for r in files})

    def test_empty_directories_survive(self):
        (self.project / "data/incoming").mkdir(parents=True)
        (self.project / "outputs").mkdir()
        self.package()
        target = self.base / "restored"
        H.restore_bundle(self.archive, target)
        self.assertTrue((target / "data/incoming").is_dir())
        self.assertTrue((target / "outputs").is_dir())

    def test_late_resume_conflict_causes_no_earlier_writes(self):
        (self.project / "zzz-last.txt").write_text("original", encoding="utf-8")
        self.package()
        target = self.base / "target"
        target.mkdir()
        (target / "zzz-last.txt").write_text("user change", encoding="utf-8")
        before = sorted(p.relative_to(target).as_posix() for p in target.rglob("*"))
        with self.assertRaises(ValueError):
            H.restore_bundle(self.archive, target, resume=True)
        self.assertEqual(before, sorted(p.relative_to(target).as_posix() for p in target.rglob("*")))

    def test_signed_url_credential_is_redacted(self):
        secret = "a" * 40
        text = "https://example.test/data?access_token=" + secret + "&gid=123"
        redacted = H.redact_chat(text)
        self.assertNotIn(secret, redacted)
        self.assertIn("gid=123", redacted)

    def test_safe_nested_zip_is_preserved(self):
        nested = self.base / "inner.zip"
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("query.sql", self.original)
        with zipfile.ZipFile(self.project / "old.zip", "w") as archive:
            archive.writestr("inner.zip", nested.read_bytes())
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            files = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            self.assertIn("old.zip", {r["path"] for r in files})

    def test_inline_user_image_is_archived_as_bytes(self):
        import base64
        store = self.base / "sessions"
        store.mkdir()
        image = b"\x89PNG\r\n\x1a\nfixture-image-bytes"
        records = [
            {"type": "session_meta", "payload": {"id": "image-thread", "cwd": str(self.project)}},
            {"type": "event_msg", "timestamp": "2026-10-31T00:00:00Z",
             "payload": {"type": "item_completed", "item": {
                 "type": "UserMessage", "content": [
                     {"type": "text", "text": "Please inspect this"},
                     {"type": "image", "url": "data:image/png;base64," + base64.b64encode(image).decode()},
                 ]}}},
        ]
        (store / "rollout-image-thread.jsonl").write_bytes(
            b"".join(json.dumps(r).encode() + b"\n" for r in records))
        self.profile["conversation"]["enabled"] = True
        files, coverage = H.export_conversations(self.project, self.profile, ["image-thread"], str(store))
        self.assertEqual(coverage["copied_image_files"], 1)
        self.assertEqual(coverage["uncopied_attachment_references"], 0)
        image_name = next(k for k in files if k.startswith("chat/attachments/"))
        self.assertEqual(files[image_name], image)
        self.assertIn(image_name.removeprefix("chat/"), files["chat/transcript.md"].decode())

    def test_missing_local_image_can_use_matching_persisted_user_input(self):
        import base64
        store = self.base / "sessions"
        store.mkdir()
        image = b"\x89PNG\r\n\x1a\npersisted-user-image"
        url = "data:image/png;base64," + base64.b64encode(image).decode()
        records = [
            {"type": "session_meta", "payload": {"id": "recover-image", "cwd": str(self.project)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Check this screenshot"}, {"type": "input_image", "image_url": url}]}},
            {"type": "event_msg", "timestamp": "2026-10-31T00:00:00Z",
             "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [
                 {"type": "text", "text": "Check this screenshot"},
                 {"type": "local_image", "path": "/unavailable/pasted.png"}]}}},
        ]
        (store / "rollout-recover-image.jsonl").write_bytes(
            b"".join(json.dumps(r).encode() + b"\n" for r in records))
        self.profile["conversation"]["enabled"] = True
        files, coverage = H.export_conversations(self.project, self.profile, ["recover-image"], str(store))
        self.assertEqual(coverage["copied_image_files"], 1)
        self.assertEqual(coverage["uncopied_attachment_references"], 0)
        attachment = coverage["non_text_attachment_references"][0]
        self.assertEqual(attachment["recovery_source"], "persisted_visible_user_input_image")
        self.assertEqual(files[attachment["archived_path"]], image)

    def import_config(self, relative, **options):
        self.profile["conversation"] = {
            "enabled": True, "use_local_sessions": False,
            "imports": [{"source": relative, **options}],
        }

    def write_input(self, relative, records, jsonl=False):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b"".join(json.dumps(r).encode() + b"\n" for r in records)
            if jsonl else H.dumps({"messages": records}))
        return path

    def test_frontend_selected_materials_roundtrip(self):
        source = self.project / "src/app.js"
        source.parent.mkdir()
        source.write_text('export const title = "Workshop";\n', encoding="utf-8")
        self.profile["include_paths"] = ["src", "index.html"]
        self.package()
        target = self.base / "frontend-restored"
        H.restore_bundle(self.archive, target)
        self.assertEqual((target / "src/app.js").read_bytes(), source.read_bytes())
        self.assertTrue((target / "index.html").exists())
        self.assertTrue((target / ".handoff/context/NEXT_SESSION.md").exists())
        self.assertFalse((target / "query.sql").exists())

    def test_research_documents_preserve_binary_and_citations(self):
        materials = self.project / "research"
        materials.mkdir()
        document = materials / "draft.docx"
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", "<document>Draft research</document>")
        (materials / "source.pdf").write_bytes(b"%PDF-1.4\nlocal research fixture\n%%EOF\n")
        (materials / "citations.md").write_text("Source: https://example.test/study\n", encoding="utf-8")
        self.profile["include_paths"] = ["research"]
        self.package()
        target = self.base / "research-restored"
        H.restore_bundle(self.archive, target)
        for path in materials.iterdir():
            self.assertEqual((target / "research" / path.name).read_bytes(), path.read_bytes())
        self.assertFalse((target / "index.html").exists())

    def test_conversation_workspace_does_not_copy_calling_directory(self):
        workspace = self.base / "planning-only"
        H.init_project(workspace, workspace_only=True, name="Planning")
        profile = H.load_profile(workspace)
        self.assertEqual(profile["include_paths"], [])
        self.assertEqual(profile["project_name"], "Planning")
        self.assertIn(str(Path.cwd()), profile["conversation"]["allowed_cwds"])
        self.assertEqual(profile["timezone"], "local")
        records, _, _, _, _, _ = H.inspect_project(workspace, profile)
        self.assertEqual(
            {r["path"] for r in records},
            {".handoff/profile.json", ".handoff/context/PROJECT_STATE.md", ".handoff/context/NEXT_SESSION.md"})

    def test_workspace_refuses_existing_materials_without_touching_them(self):
        before = sorted(p.relative_to(self.project).as_posix() for p in self.project.rglob("*"))
        with self.assertRaises(ValueError):
            H.init_project(self.project, workspace_only=True)
        self.assertEqual(before, sorted(p.relative_to(self.project).as_posix() for p in self.project.rglob("*")))

    def test_empty_selection_retains_context_and_explicit_external_file(self):
        source = self.base / "meeting-notes.txt"
        source.write_text("Decision: review two layouts on Monday.\n", encoding="utf-8")
        self.profile["include_paths"] = []
        self.profile["external_sources"] = [
            {"source": str(source), "destination": "materials/notes.txt", "required": True}]
        self.package()
        target = self.base / "notes-restored"
        H.restore_bundle(self.archive, target)
        self.assertEqual((target / "materials/notes.txt").read_bytes(), source.read_bytes())
        self.assertFalse((target / "query.sql").exists())

    def test_missing_selected_material_fails(self):
        self.profile["include_paths"] = ["unavailable-draft.md"]
        with self.assertRaisesRegex(ValueError, "Selected input missing"):
            self.package()

    def test_legacy_profile_without_selection_still_includes_whole_directory(self):
        self.profile.pop("include_paths", None)
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            paths = {r["path"] for r in json.loads(archive.read("FILE_MANIFEST.json"))["files"]}
        self.assertIn("query.sql", paths)
        self.assertIn("index.html", paths)

    def test_home_and_account_roots_rejected_before_inventory(self):
        for root in (Path.home().resolve(), Path("/"), self.base / ".codex/sessions"):
            with self.subTest(root=root):
                with self.assertRaises(ValueError):
                    H.collect_files(root, {})

    def test_imported_json_filters_hidden_records_and_redacts_visible_secrets(self):
        relative = ".handoff/inputs/discussion.json"
        secret = "sk-" + "v" * 48
        records = [
            {"role": "system", "text": "SYSTEM_ONLY"},
            {"role": "developer", "text": "DEVELOPER_ONLY"},
            {"role": "tool", "text": "TOOL_ONLY"},
            {"role": "assistant", "phase": "analysis", "text": "REASONING_ONLY"},
            {"role": "user", "text": "Compare a compact layout with a spacious one."},
            {"role": "assistant", "content": 'api_key="' + secret + '"'},
        ]
        original = self.write_input(relative, records)
        self.import_config(relative, required=True)
        self.package()
        self.assertIn(secret, original.read_text())
        with zipfile.ZipFile(self.archive) as archive:
            text = archive.read("chat/transcript.md").decode()
            files = json.loads(archive.read("FILE_MANIFEST.json"))["files"]
            coverage = json.loads(archive.read("chat/coverage.json"))
        for excluded in ("SYSTEM_ONLY", "DEVELOPER_ONLY", "TOOL_ONLY", "REASONING_ONLY", secret):
            self.assertNotIn(excluded, text)
        self.assertIn("Compare a compact layout", text)
        self.assertEqual(coverage["message_count"], 2)
        self.assertEqual(coverage["imports"][0]["filtered_records"], 4)
        self.assertNotIn(relative, {r["path"] for r in files})

    def test_untimestamped_repeated_messages_are_preserved(self):
        relative = ".handoff/inputs/planning.jsonl"
        self.write_input(relative, [{"role": "user", "text": "Continue"}] * 3, jsonl=True)
        self.import_config(relative)
        files, coverage = H.export_conversations(self.project, self.profile)
        self.assertEqual(coverage["message_count"], 3)
        self.assertEqual(len(files["chat/transcript.jsonl"].splitlines()), 3)

    def test_partial_import_reports_bad_lines_without_inventing_messages(self):
        relative = ".handoff/inputs/partial.jsonl"
        path = self.write_input(relative, [{"role": "user", "text": "One available message"}], jsonl=True)
        with path.open("ab") as stream:
            stream.write(b"{truncated-json\n")
        self.import_config(relative, coverage="Only a supplied excerpt")
        _, coverage = H.export_conversations(self.project, self.profile)
        self.assertEqual(coverage["message_count"], 1)
        self.assertEqual(coverage["imports"][0]["malformed_records"], 1)
        self.assertEqual(coverage["status"], "partial_history_with_explicit_gaps")
        self.assertEqual(coverage["imports"][0]["declared_coverage"], "Only a supplied excerpt")

    def test_missing_required_and_optional_imports(self):
        self.import_config("missing.jsonl", required=True)
        with self.assertRaisesRegex(ValueError, "Required conversation import"):
            self.package()
        self.profile["conversation"]["imports"][0]["required"] = False
        _, coverage = H.export_conversations(self.project, self.profile)
        self.assertEqual(coverage["imports"][0]["status"], "missing")
        self.assertEqual(coverage["message_count"], 0)
        self.assertTrue(coverage["gaps"])

    def test_context_directory_does_not_bypass_raw_import_exclusion(self):
        relative = ".handoff/context/raw-import.json"
        private = "PRIVATE_PROVIDER_" + os.urandom(8).hex()
        self.write_input(relative, [{"role": "developer", "text": private},
                                    {"role": "user", "text": "Visible material"}])
        self.import_config(relative)
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            self.assertNotIn("context/raw-import.json", archive.namelist())
            for name in archive.namelist():
                self.assertNotIn(private.encode(), archive.read(name))

    def test_import_can_use_explicit_external_file_and_restore_without_original(self):
        external = self.base / "export.json"
        external.write_bytes(H.dumps([{"role": "user", "text": "Choose the small venue."}]))
        relative = ".handoff/inputs/external.json"
        self.profile["external_sources"] = [
            {"source": str(external), "destination": relative, "required": True}]
        self.import_config(relative, format="json", required=True)
        profile_path = self.project / ".handoff/profile.json"
        profile_path.write_bytes(H.dumps(self.profile))
        self.package()
        target = self.base / "restored-import"
        H.restore_bundle(self.archive, target)
        external.unlink()
        next_archive = self.base / "second-generation.zip"
        H.make_bundle(target, H.load_profile(target), next_archive)
        with zipfile.ZipFile(next_archive) as archive:
            coverage = json.loads(archive.read("chat/coverage.json"))
            self.assertIn("Choose the small venue.", archive.read("chat/transcript.md").decode())
        self.assertTrue(coverage["imports"][0]["recovered_from_previous_handoff"])
        self.assertEqual(coverage["message_count"], 1)

    def test_import_path_escape_and_symlink_parent_are_rejected(self):
        self.import_config("../outside.json")
        with self.assertRaises(ValueError):
            self.package()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "discussion.json").write_text("[]", encoding="utf-8")
        (self.project / "linked-input").symlink_to(outside, target_is_directory=True)
        self.import_config("linked-input/discussion.json")
        with self.assertRaises(ValueError):
            H.export_conversations(self.project, self.profile)

    def test_no_sources_reports_summary_only_gap(self):
        self.profile["conversation"] = {"enabled": True, "use_local_sessions": False}
        _, coverage = H.export_conversations(self.project, self.profile)
        self.assertEqual(coverage["message_count"], 0)
        self.assertTrue(coverage["gaps"])

    def test_configured_timezone_does_not_override_legacy_projects(self):
        self.assertEqual(H.profile_now({"timezone": "UTC"}).utcoffset().total_seconds(), 0)
        local = H.profile_now({"timezone": "local"})
        self.assertIsNotNone(local.tzinfo)
        self.assertEqual(H.profile_now({"timezone": "Asia/Shanghai"}).utcoffset().total_seconds(), 28800)

    def test_generated_handoff_does_not_require_optional_reproduction_or_preview(self):
        self.package()
        with zipfile.ZipFile(self.archive) as archive:
            start = archive.read("00_START_HERE.md").decode()
            self.assertNotIn("context/REPRODUCE.md", start)
            self.assertNotIn("preview/", start)
            self.assertIn("context/NEXT_SESSION.md", start)

    def test_new_helper_reads_legacy_v1_bundle(self):
        self.package()
        def legacy(entries):
            manifest = json.loads(entries["BUNDLE_MANIFEST.json"])
            manifest.pop("generator_version")
            entries["BUNDLE_MANIFEST.json"] = H.dumps(manifest)
        changed = self.mutate_archive(legacy)
        self.assertEqual(H.verify_bundle(changed)["status"], "PASS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
