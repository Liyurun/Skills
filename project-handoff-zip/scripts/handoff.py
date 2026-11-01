#!/usr/bin/env python3
"""Create portable project snapshots without exporting authentication or reasoning."""
import argparse
import base64
import fnmatch
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo
import zipfile

FORMAT = "project-handoff/v1"
TOOL_VERSION = "1.1.0"
CHUNK = 1024 * 1024
MAX_CONTAINER_BYTES = 8 * 1024 ** 3
SKILL_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".ssh", ".gnupg", ".auth",
}
EXCLUDED_FILES = {
    ".ds_store", ".git-credentials", ".netrc", ".npmrc", ".pypirc", ".pgpass",
    "auth.json", "auth.yaml", "credentials.json", "credentials.yaml",
    "credentials.yml", "token.json", "tokens.json", "cookies.json",
    "cookies.sqlite", "storage-state.json", "storage_state.json",
    "browser-state.json", "browser_state.json", "session-state.json",
    "session_state.json", "browser-session.json",
}
TOKEN_PATTERNS = [
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("authorization", re.compile(
        rb"(?i)authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+([A-Za-z0-9_./+=-]{24,})")),
    ("api_token", re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b")),
    ("aws_key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("quoted_credential", re.compile(
        rb"(?i)[\"']?(?:api_key|access_token|refresh_token|client_secret|password|passwd)[\"']?"
        rb"\s*[:=]\s*[\"']([^\"'\r\n]{8,})[\"']")),
    ("jwt", re.compile(rb"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{16,}\b")),
    ("signed_url", re.compile(
        rb"(?i)(?:access_token|refresh_token|api_key|x-amz-signature|x-amz-security-token)"
        rb"=([A-Za-z0-9._%/+~=-]{16,})")),
]
PLACEHOLDERS = (
    b"${", b"process.env", b"os.environ", b"getenv(", b"<", b"redacted",
    b"placeholder", b"changeme", b"example", b"dummy", b"your_", b"test",
    b"self.", b"args[", b"config[",
)


def dumps(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid relative path")
    if any(ord(c) < 32 for c in value) or ":" in value:
        raise ValueError("Invalid path character")
    p = PurePosixPath(value)
    if p.is_absolute() or any(s in ("", ".", "..") for s in value.split("/")):
        raise ValueError("Unsafe path: " + value)
    return value


def project_path(root, relative):
    current = root
    for part in PurePosixPath(safe_path(relative)).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Project input contains a symlink: " + relative)
    return current


def account_source(path):
    for candidate in (path.absolute(), path.resolve()):
        parts = candidate.parts
        if forbidden_name(candidate.as_posix()) or (
                ".codex" in parts and any(p in parts for p in ("sessions", "memories"))):
            return True
        if any(p in parts for p in (
                ".config", ".aws", ".azure", ".kube", "Keychains", ".password-store")):
            return True
        if candidate.name == ".codex":
            return True
    return False


def check_root(root):
    if root == Path(root.anchor) or root == Path.home().resolve() or account_source(root):
        raise ValueError("Choose a dedicated work directory, not a home, filesystem or account root")
    if root.exists() and not root.is_dir():
        raise ValueError("Project root is not a directory")


def profile_now(profile):
    name = profile.get("timezone", "local")
    return datetime.now().astimezone() if name == "local" else datetime.now(ZoneInfo(name))


def forbidden_name(relative):
    parts = PurePosixPath(relative).parts
    if any(p in EXCLUDED_DIRS for p in parts):
        return "environment_or_account_directory"
    name = parts[-1].lower()
    if name in EXCLUDED_FILES:
        return "credential_or_os_file"
    if name == ".env" or (name.startswith(".env.") and not name.endswith((".example", ".sample", ".template"))):
        return "environment_credentials"
    if name.endswith((".pem", ".p12", ".pfx", ".key", ".keychain", ".keychain-db")):
        return "private_key_or_key_store"
    return None


def credential_hits(data):
    hits = set()
    lower = data.lower()
    markers = {
        "private_key": (b"private key",), "authorization": (b"authorization",),
        "api_token": (b"sk-",), "aws_key": (b"akia", b"asia"),
        "quoted_credential": (b"api_key", b"access_token", b"refresh_token",
                              b"client_secret", b"password", b"passwd"),
        "jwt": (b"eyj",),
        "signed_url": (b"access_token=", b"refresh_token=", b"api_key=",
                       b"x-amz-signature=", b"x-amz-security-token="),
    }
    for label, pattern in TOKEN_PATTERNS:
        if not any(marker in lower for marker in markers[label]):
            continue
        for match in pattern.finditer(data):
            value = match.group(1) if match.lastindex else match.group(0)
            if any(marker in value.lower() for marker in PLACEHOLDERS):
                continue
            if label == "jwt":
                try:
                    payload = value.split(b".")[1]
                    obj = json.loads(base64.urlsafe_b64decode(payload + b"=" * (-len(payload) % 4)))
                    if not isinstance(obj, dict) or not any(k in obj for k in ("exp", "iss", "aud")):
                        continue
                except (ValueError, TypeError):
                    continue
            hits.add(label)
    return sorted(hits)


def scan_stream(stream, limit=None):
    tail = b""
    consumed = 0
    findings = set()
    while True:
        block = stream.read(CHUNK)
        if not block:
            break
        consumed += len(block)
        if consumed == len(block) and block.lstrip().startswith(b"{") and re.search(
                rb'"type"\s*:\s*"session_meta"', block[:4096]):
            return ["raw_codex_session_not_exportable"]
        if consumed == len(block) and block.lstrip().startswith(b"{") and re.search(
                rb'"cookies"\s*:\s*\[', block) and re.search(rb'"origins"\s*:\s*\[', block):
            return ["browser_account_storage_state"]
        if limit is not None and consumed > limit:
            return ["container_scan_limit"]
        findings.update(credential_hits(tail + block))
        tail = block[-8192:]
    return sorted(findings)


def scan_zip(archive, depth=0, budget=None):
    budget = [0] if budget is None else budget
    if depth > 3:
        return ["nested_archive_depth_limit"]
    for item in archive.infolist():
        if item.is_dir():
            continue
        try:
            safe_path(item.filename)
        except ValueError:
            return ["unsafe_nested_archive_path"]
        reason = forbidden_name(item.filename)
        if reason and PurePosixPath(item.filename).name != ".DS_Store":
            return ["nested_credential_or_environment_file"]
        if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
            return ["nested_link_or_encrypted_member"]
        budget[0] += item.file_size
        if budget[0] > MAX_CONTAINER_BYTES:
            return ["container_scan_limit"]
        if item.filename.lower().endswith(".zip"):
            with tempfile.TemporaryFile() as temporary:
                with archive.open(item) as source:
                    shutil.copyfileobj(source, temporary, CHUNK)
                temporary.seek(0)
                with zipfile.ZipFile(temporary) as nested:
                    findings = scan_zip(nested, depth + 1, budget)
        elif item.filename.lower().endswith((".tar", ".tgz", ".tar.gz", ".7z", ".rar")):
            findings = ["unsupported_nested_container"]
        else:
            with archive.open(item) as stream:
                findings = scan_stream(stream, MAX_CONTAINER_BYTES)
        if findings:
            return findings
    return []


def scan_file(path):
    with path.open("rb") as stream:
        findings = scan_stream(stream)
    if findings:
        return findings
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                return scan_zip(archive)
        elif path.name.lower().endswith(".gz"):
            with gzip.open(path, "rb") as stream:
                return scan_stream(stream, MAX_CONTAINER_BYTES)
        elif path.name.lower().endswith((".tar", ".tgz", ".7z", ".rar")):
            return ["unsupported_container_requires_explicit_review"]
    except (OSError, RuntimeError, zipfile.BadZipFile, EOFError):
        return ["unreadable_container"]
    return []


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def redact_chat(text):
    data = text.encode("utf-8")
    data = re.sub(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        rb"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        b"[REDACTED_PRIVATE_KEY]", data, flags=re.S)
    for _, pattern in TOKEN_PATTERNS:
        def replace(match):
            if not credential_hits(match.group(0)):
                return match.group(0)
            if match.lastindex:
                start, end = match.span(1)
                original = match.group(0)
                offset = match.start()
                return original[:start - offset] + b"[REDACTED_SECRET]" + original[end - offset:]
            return b"[REDACTED_SECRET]"
        data = pattern.sub(replace, data)
    return data.decode("utf-8")


def read_metadata(path):
    with path.open("rb") as stream:
        record = json.loads(stream.readline())
    if record.get("type") != "session_meta":
        raise ValueError("Session has no metadata header")
    return record["payload"]


def locate_segment(store, identity, root=False):
    candidates = []
    for path in store.rglob("*" + identity + "*.jsonl"):
        if path.is_symlink():
            continue
        try:
            meta = read_metadata(path)
        except (OSError, ValueError, KeyError):
            continue
        exact_segment = path.stem.endswith(identity)
        if exact_segment or meta.get("id") == identity:
            candidates.append((path, meta, exact_segment))
    if not candidates:
        return None
    if root:
        return max(candidates, key=lambda x: (x[1].get("timestamp", ""), x[0].stat().st_mtime_ns))[:2]
    exact = [x for x in candidates if x[2]]
    return max(exact or candidates, key=lambda x: x[1].get("timestamp", ""))[:2]


def conversation_imports(root, profile):
    config = profile.get("conversation", {})
    for item in config.get("imports", []):
        relative = safe_path(item["source"])
        if forbidden_name(relative):
            raise ValueError("Conversation input must not be an account or credential file")
        key = sha(relative.encode("utf-8"))
        source = project_path(root, relative)
        recovered = False
        if not source.is_file():
            for external in profile.get("external_sources", []):
                if safe_path(external["destination"]) == relative:
                    candidate = Path(external["source"]).expanduser().absolute()
                    if not account_source(candidate) and not any(p.is_symlink() for p in (candidate, *candidate.parents)):
                        source = candidate
                    break
        if not source.is_file():
            history = project_path(root, ".handoff/history")
            for candidate in sorted(history.glob("*/chat/imports/" + key + ".jsonl"), reverse=True):
                if not any(p.is_symlink() for p in (candidate, *candidate.parents)):
                    source, recovered = candidate, True
                    break
        yield item, relative, key, source, recovered


def read_import_messages(source, format_name):
    with source.open("r", encoding="utf-8-sig") as stream:
        if format_name == "json":
            value = json.load(stream)
            records = value.get("messages") if isinstance(value, dict) else value
            if not isinstance(records, list):
                raise ValueError("JSON import must be a message list or an object with messages")
            for index, record in enumerate(records, 1):
                yield index, record
        elif format_name == "jsonl":
            for index, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    yield index, json.loads(line)
                except ValueError:
                    yield index, None
        else:
            raise ValueError("Conversation import format must be json or jsonl")


def export_conversations(root, profile, thread_ids=None, store_override=None):
    config = profile.get("conversation", {})
    local_enabled = config.get("enabled", True) and config.get("use_local_sessions", True)
    roots = (thread_ids or config.get("thread_ids") or [os.environ.get("CODEX_THREAD_ID", "")]) if local_enabled else []
    roots = list(dict.fromkeys(x for x in roots if x))
    store = Path(store_override or config.get("session_store", "~/.codex/sessions")).expanduser()
    allowed = {str(root.resolve())}
    allowed.update(str(Path(p).expanduser().resolve()) for p in config.get("allowed_cwds", []))
    coverage = {
        "scope": "Visible messages from explicitly selected local threads, bounded ancestors and supplied normalized exports",
        "source_thread_ids": roots, "segments": [], "imports": [], "gaps": [], "redacted_messages": 0,
        "history_completeness": "Not independently established; see selected sources, declared coverage and gaps",
        "non_text_attachment_references": [],
        "attachment_policy": "Whitelisted inline image bytes and registered local images are copied separately, not inlined into text. Unsupported/unavailable image references remain explicit.",
        "excluded": ["reasoning", "analysis", "tool payloads", "system/developer prompts", "raw session logs"],
        "checkpoint": "Messages persisted before this export; later packaging replies are not included.",
    }
    messages, seen_segments, seen_messages, image_files, import_files = [], set(), set(), {}, {}
    registered_images = {
        str(Path(item["source"]).expanduser().absolute())
        for item in profile.get("external_sources", [])
    }

    def capture_image(part, segment, timestamp):
        reference = next((part[k] for k in ("path", "url", "image_url")
                          if isinstance(part.get(k), str)), None)
        result = {"source_segment": segment, "timestamp": timestamp,
                  "type": part.get("type"), "reference": None, "status": "reference_only"}
        data = None
        if reference and reference.startswith("data:"):
            result["reference"] = "[inline image]"
            match = re.fullmatch(r"data:image/(png|jpeg|jpg|gif|webp);base64,([A-Za-z0-9+/=\r\n]+)", reference)
            if match and len(match.group(2)) <= 36 * CHUNK:
                try:
                    data = base64.b64decode(re.sub(r"\s", "", match.group(2)), validate=True)
                except ValueError:
                    result["status"] = "invalid_inline_image"
        elif reference:
            result["reference"] = redact_chat(reference)
            local = Path(reference).expanduser().absolute()
            if not forbidden_name(local.as_posix()) and (
                    str(local) in registered_images or (
                        not reference.startswith(("http:", "https:")) and local.is_relative_to(root))):
                if not local.is_symlink() and local.is_file() and local.stat().st_size <= 25 * CHUNK:
                    data = local.read_bytes()
                else:
                    result["status"] = "registered_local_image_unavailable"
        if data is not None and len(data) <= 25 * CHUNK:
            extension = (
                "png" if data.startswith(b"\x89PNG\r\n\x1a\n")
                else "jpg" if data.startswith(b"\xff\xd8\xff")
                else "gif" if data.startswith((b"GIF87a", b"GIF89a"))
                else "webp" if data.startswith(b"RIFF") and data[8:12] == b"WEBP"
                else None
            )
            if extension:
                digest = sha(data)
                name = "chat/attachments/" + digest + "." + extension
                image_files[name] = data
                result.update({"status": "copied", "archived_path": name,
                               "sha256": digest, "bytes": len(data)})
            else:
                result["status"] = "unsupported_image_signature"
        coverage["non_text_attachment_references"].append(result)
        return result
    if local_enabled and not roots:
        coverage["gaps"].append("No current thread identifier or explicit export was available.")

    def visit(identity, byte_limit=None, as_root=False, depth=0):
        if depth > 48:
            coverage["gaps"].append("Ancestor depth limit reached.")
            return
        located = locate_segment(store, identity, as_root)
        if not located:
            coverage["gaps"].append("Missing session segment: " + identity)
            return
        path, meta = located
        if str(Path(meta.get("cwd", "")).resolve()) not in allowed:
            coverage["gaps"].append("Ancestor outside authorized project: " + identity)
            return
        cutoff = min(byte_limit, path.stat().st_size) if byte_limit is not None else path.stat().st_size
        segment_key = (str(path), cutoff)
        if segment_key in seen_segments:
            return
        seen_segments.add(segment_key)
        base = meta.get("history_base") or {}
        if config.get("include_ancestors", True):
            parent = base.get("thread_id") or meta.get("forked_from_id")
            if parent:
                bound = base.get("end_byte_offset")
                if isinstance(bound, int) and bound >= 0:
                    visit(parent, bound, False, depth + 1)
                else:
                    coverage["gaps"].append("Ancestor boundary unavailable; not guessed: " + parent)
        segment = {
            "segment": path.name, "logical_thread_id": meta.get("id"),
            "requested_identity": identity, "byte_limit": cutoff,
            "requested_byte_limit": byte_limit, "visible_messages": 0, "malformed_records": 0,
        }
        if byte_limit is not None and path.stat().st_size < byte_limit:
            coverage["gaps"].append("Ancestor file shorter than requested boundary: " + identity)
        pending_user_images = None
        with path.open("rb") as stream:
            while stream.tell() < cutoff:
                line = stream.readline(cutoff - stream.tell())
                if not line.endswith(b"\n"):
                    if line.strip():
                        segment["malformed_records"] += 1
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    segment["malformed_records"] += 1
                    continue
                value = record.get("payload", {})
                if record.get("type") == "response_item" and value.get("type") == "message" and value.get("role") == "user":
                    content = value.get("content", [])
                    response_text = "\n".join(
                        part.get("text", "") for part in content
                        if isinstance(part, dict) and part.get("type") in ("text", "input_text"))
                    images = [part for part in content if isinstance(part, dict) and part.get("type") == "input_image"
                              and str(part.get("image_url", "")).startswith("data:image/")]
                    pending_user_images = {"text": response_text.strip(), "images": images} if images else None
                    continue
                if record.get("type") != "event_msg":
                    continue
                event_type = value.get("type")
                attachments = []
                if event_type in ("user_message", "agent_message"):
                    role = "user" if event_type == "user_message" else "assistant"
                    phase = value.get("phase")
                    text = value.get("message", "")
                elif event_type == "item_completed" and value.get("item", {}).get("type") in ("UserMessage", "AgentMessage"):
                    item = value["item"]
                    role = "user" if item["type"] == "UserMessage" else "assistant"
                    phase = item.get("phase")
                    content = item.get("content", [])
                    text = content if isinstance(content, str) else "\n".join(
                        part.get("text", "") for part in content
                        if isinstance(part, dict) and str(part.get("type", "")).lower() in ("text", "input_text", "output_text")
                    )
                    if role == "user" and isinstance(content, list):
                        image_index = 0
                        for part in content:
                            if not isinstance(part, dict) or str(part.get("type", "")).lower() in ("text", "input_text"):
                                continue
                            original_reference = next((part[k] for k in ("path", "url", "image_url")
                                                       if isinstance(part.get(k), str)), "")
                            recovered = False
                            if not original_reference.startswith("data:") and pending_user_images and text.strip() and (
                                    text.strip() in pending_user_images["text"] or pending_user_images["text"] in text.strip()):
                                images = pending_user_images["images"]
                                if image_index < len(images):
                                    part = images[image_index]
                                    recovered = True
                            attachment = capture_image(part, path.name, record.get("timestamp"))
                            if recovered:
                                attachment["recovery_source"] = "persisted_visible_user_input_image"
                                attachment["original_reference"] = original_reference
                                attachment["encoding_boundary"] = "Original temporary file may be unavailable; this preserves the persisted model-input image, not a claim about original file encoding."
                            attachments.append(attachment)
                            image_index += 1
                        pending_user_images = None
                else:
                    continue
                if phase in ("analysis", "reasoning"):
                    continue
                if not isinstance(text, str) or (not text.strip() and not attachments):
                    continue
                cleaned = redact_chat(text)
                coverage["redacted_messages"] += int(cleaned != text)
                key = (record.get("timestamp"), role, cleaned)
                if key in seen_messages:
                    continue
                seen_messages.add(key)
                messages.append({
                    "role": role, "timestamp": record.get("timestamp"), "text": cleaned,
                    "source_segment": path.name, "source_thread_id": meta.get("id"),
                    "attachments": attachments,
                })
                segment["visible_messages"] += 1
        if segment["malformed_records"]:
            coverage["gaps"].append("Incomplete or malformed records in: " + path.name)
        coverage["segments"].append(segment)

    if local_enabled:
        for identity in roots:
            visit(identity, as_root=True)
    if config.get("enabled", True):
        for item, relative, key, source, recovered in conversation_imports(root, profile):
            detail = {
                "source": relative, "label": item.get("label", relative),
                "declared_coverage": item.get("coverage", "User-supplied export; coverage not specified"),
                "visible_messages": 0, "filtered_records": 0, "malformed_records": 0,
                "sanitized_path": "chat/imports/" + key + ".jsonl",
                "recovered_from_previous_handoff": recovered,
            }
            coverage["imports"].append(detail)
            if not source.is_file():
                detail["status"] = "missing"
                if item.get("required", False):
                    raise ValueError("Required conversation import missing: " + relative)
                coverage["gaps"].append("Conversation import unavailable: " + relative)
                continue
            sanitized = []
            try:
                format_name = "jsonl" if recovered else item.get("format", source.suffix.lstrip("."))
                for index, record in read_import_messages(source, format_name):
                    if not isinstance(record, dict):
                        detail["malformed_records"] += 1
                        continue
                    role = record.get("role")
                    if role not in ("user", "assistant") or record.get("phase") in ("analysis", "reasoning"):
                        detail["filtered_records"] += 1
                        continue
                    text = record.get("text", record.get("content"))
                    timestamp = record.get("timestamp")
                    if not isinstance(text, str) or not text.strip() or (
                            timestamp is not None and not isinstance(timestamp, str)):
                        detail["malformed_records"] += 1
                        continue
                    cleaned = redact_chat(text)
                    coverage["redacted_messages"] += int(cleaned != text)
                    visible = {"role": role, "timestamp": timestamp, "text": cleaned}
                    sanitized.append(visible)
                    identity = (timestamp, role, cleaned) if timestamp else ("import", key, index)
                    if identity in seen_messages:
                        continue
                    seen_messages.add(identity)
                    messages.append({**visible, "source_import": relative, "source_record": index,
                                     "source_segment": None, "source_thread_id": None, "attachments": []})
                    detail["visible_messages"] += 1
            except (ValueError, UnicodeError) as error:
                detail["malformed_records"] += 1
                detail["parse_error"] = type(error).__name__
            if detail["malformed_records"] or not sanitized:
                coverage["gaps"].append("Incomplete or undecodable conversation import: " + relative)
            detail["status"] = "partial" if detail["malformed_records"] or not sanitized else "imported"
            import_files[detail["sanitized_path"]] = b"".join(
                (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8") for record in sanitized)
        if not local_enabled and not config.get("imports"):
            coverage["gaps"].append("No conversation source configured; context notes are not a verbatim transcript.")
    coverage["message_count"] = len(messages)
    coverage["copied_image_files"] = len(image_files)
    coverage["uncopied_attachment_references"] = sum(
        part["status"] != "copied" for part in coverage["non_text_attachment_references"])
    if config.get("enabled", True) and roots and not messages:
        coverage["gaps"].append("No visible messages decoded; do not claim a complete transcript.")
    coverage["status"] = (
        "not_exported_by_configuration" if not config.get("enabled", True)
        else "available_visible_history_exported" if not coverage["gaps"]
        else "partial_history_with_explicit_gaps"
    )
    markdown = "# 可见聊天记录\n\n原始推理、工具输出和系统提示未导出；完整性范围见 coverage.json。\n\n"
    for index, message in enumerate(messages, 1):
        markdown += "## %d. %s · %s\n\n%s\n\n" % (
            index, message["role"], message["timestamp"] or "timestamp unavailable", message["text"])
        for attachment in message["attachments"]:
            if attachment.get("archived_path"):
                markdown += "附件：![](" + attachment["archived_path"].removeprefix("chat/") + ")\n\n"
    return {
        **image_files,
        **import_files,
        "chat/transcript.jsonl": b"".join(
            (json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8") for m in messages),
        "chat/transcript.md": markdown.encode("utf-8"),
        "chat/coverage.json": dumps(coverage),
    }, coverage


def load_profile(root, path=None):
    location = Path(path).resolve() if path else root / ".handoff/profile.json"
    if not location.is_file():
        raise ValueError("Initialize .handoff/profile.json first")
    profile = json.loads(location.read_text(encoding="utf-8"))
    if profile.get("version") != 1:
        raise ValueError("Unsupported profile")
    return profile


def collect_files(root, profile, output=None):
    check_root(root)
    excluded, candidates, directories = [], {}, set()
    patterns = [".handoff/packages/**", ".serena/cache/**", *profile.get("exclude_globs", [])]

    def add_tree(source, destination="", external=False):
        if not source.exists():
            return False
        paths = [source] if source.is_file() or source.is_symlink() else None
        def add(path, relative):
            relative = safe_path(relative)
            reason = forbidden_name(relative)
            if path.is_symlink():
                reason = "symlink_not_followed"
            elif output and path.resolve() in (output.resolve(), Path(str(output) + ".partial").resolve()):
                reason = "current_archive_output"
            elif any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                reason = "configured_exclusion"
            if reason:
                excluded.append({"path": relative, "reason": reason})
                return
            if not path.is_file():
                excluded.append({"path": relative, "reason": "non_regular_file"})
                return
            if relative in candidates:
                if candidates[relative]["source"] != path:
                    raise ValueError("Two sources map to the same project path: " + relative)
                return
            parent = PurePosixPath(relative).parent
            while parent.as_posix() != ".":
                directories.add(safe_path(parent.as_posix()))
                parent = parent.parent
            candidates[relative] = {"source": path, "path": relative, "external": external}

        if paths is not None:
            add(source, destination or source.name)
            return True
        for directory, dirs, files in os.walk(source, followlinks=False):
            directory = Path(directory)
            prefix = directory.relative_to(source).as_posix()
            prefix = "" if prefix == "." else prefix
            for name in list(dirs):
                child = directory / name
                rel = "/".join(p for p in (destination, prefix, name) if p)
                reason = forbidden_name(rel + "/__directory__")
                if child.is_symlink():
                    reason = "symlink_directory_not_followed"
                if reason or any(fnmatch.fnmatch(rel + "/", p) or fnmatch.fnmatch(rel + "/x", p) for p in patterns):
                    excluded.append({"path": rel + "/", "reason": reason or "configured_exclusion"})
                    dirs.remove(name)
            current_relative = "/".join(p for p in (destination, prefix) if p)
            if current_relative:
                directories.add(safe_path(current_relative))
            dirs.sort()
            for name in sorted(files):
                rel = "/".join(p for p in (destination, prefix, name) if p)
                add(directory / name, rel)
        return True

    selected = profile.get("include_paths")
    if selected is None:
        add_tree(root)
    else:
        if not isinstance(selected, list):
            raise ValueError("include_paths must be null or a list of project-relative paths")
        automatic = [".handoff/profile.json", profile.get("context_dir", ".handoff/context"), ".handoff/history"]
        for relative in dict.fromkeys([*selected, *automatic]):
            source = project_path(root, relative)
            if not source.exists():
                if relative in selected:
                    raise ValueError("Selected input missing: " + relative)
                continue
            add_tree(source, relative)
    import_paths = {safe_path(item["source"]) for item in profile.get("conversation", {}).get("imports", [])}
    external_map = []
    for item in profile.get("external_sources", []):
        source = Path(item["source"]).expanduser().absolute()
        destination = safe_path(item["destination"])
        forbidden_source = account_source(source)
        if forbidden_source or source.is_symlink():
            for key in list(candidates):
                if key == destination or key.startswith(destination + "/"):
                    candidates.pop(key)
            if item.get("required", False):
                raise ValueError("External source is an account store or symlink")
            excluded.append({"path": destination, "reason": "external_account_store_or_symlink"})
            continue
        if destination in import_paths:
            external_map.append({"original_source": str(source), "project_path": destination,
                                 "state": "sanitized_conversation_input_only"})
            continue
        if (root / destination).exists() and not (root / destination).is_symlink():
            external_map.append({"original_source": str(source), "project_path": destination,
                                 "state": "using_existing_project_snapshot"})
            continue
        if not add_tree(source, destination, True):
            if item.get("required", False):
                raise ValueError("Required external source missing: " + str(source))
            excluded.append({"path": destination, "reason": "optional_external_source_missing"})
        external_map.append({"original_source": str(source), "project_path": destination})
    for relative in sorted(import_paths):
        if relative in candidates:
            candidates.pop(relative)
        excluded.append({"path": relative, "reason": "conversation_import_sanitized_separately"})
    return candidates, excluded, external_map, sorted(directories)


def inspect_project(root, profile, output=None, audit=False):
    candidates, excluded, external, directories = collect_files(root, profile, output)
    scanned, records, objects = {}, [], {}
    total = 0
    for index, (relative, item) in enumerate(sorted(candidates.items()), 1):
        path = item["source"]
        before = path.stat()
        digest = file_hash(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("Source changed during inventory: " + relative)
        if audit:
            if digest not in scanned:
                scanned[digest] = scan_file(path)
            if scanned[digest]:
                excluded.append({"path": relative, "reason": "credential_safety_exclusion",
                                 "categories": scanned[digest]})
                continue
        record = {
            "path": relative, "sha256": digest, "bytes": before.st_size,
            "mode": stat.S_IMODE(before.st_mode), "source_mtime_ns": before.st_mtime_ns,
            "payload": "payload/objects/" + digest,
            "origin": "registered_external_snapshot" if item["external"] else "project",
        }
        records.append(record)
        objects.setdefault(digest, {"source": path, "record": record})
        total += before.st_size
        if index % 2000 == 0:
            print(json.dumps({"phase": "inventory", "processed_files": index}, ensure_ascii=False), flush=True)
    included = {r["path"] for r in records}
    missing = [p for p in profile.get("required_paths", []) if safe_path(p) not in included]
    if missing:
        raise ValueError("Required paths missing or excluded: " + ", ".join(missing))
    summary = {
        "project_file_count": len(records), "unique_payload_files": len(objects),
        "logical_file_bytes": total, "unique_payload_bytes": sum(v["record"]["bytes"] for v in objects.values()),
        "excluded_count": len(excluded),
        "project_directory_count": len(directories),
    }
    return records, objects, excluded, external, summary, directories


def init_project(root, workspace_only=False, name=None, timezone_name="local"):
    check_root(root)
    if workspace_only and root.exists() and any(root.iterdir()):
        raise ValueError("A conversation/materials workspace must be new or empty")
    profile_now({"timezone": timezone_name})
    target = root / ".handoff"
    (target / "context").mkdir(parents=True, exist_ok=True)
    profile = target / "profile.json"
    if profile.exists():
        raise ValueError("Profile already exists; not overwritten")
    profile.write_bytes(dumps({
        "version": 1, "project_name": name or root.name, "timezone": timezone_name,
        "include_paths": [] if workspace_only else None,
        "context_dir": ".handoff/context", "exclude_globs": [], "required_paths": [],
        "previews": [], "external_sources": [],
        "conversation": {"enabled": True, "include_ancestors": True, "thread_ids": [],
                         "use_local_sessions": True, "imports": [],
                         "session_store": "~/.codex/sessions",
                         "allowed_cwds": list(dict.fromkeys([str(root), *([str(Path.cwd())] if workspace_only else [])]))},
    }))
    for name, heading in [
        ("PROJECT_STATE.md", "项目目标、当前事实、完成证据、未完成事项"),
        ("NEXT_SESSION.md", "新会话应先阅读的文件、下一步、验收条件"),
    ]:
        path = target / "context" / name
        if not path.exists():
            path.write_text("# " + heading + "\n\n请由当前助手基于实际证据填写，不能将模板当完成记录。\n", encoding="utf-8")
    return {"profile": str(profile), "next": "Fill the two context files from verified project evidence before pack."}


class BundleReader:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.archive = zipfile.ZipFile(self.path) if self.path.is_file() else None
        if self.archive:
            names = self.archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("Duplicate ZIP member")
            for item in self.archive.infolist():
                safe_path(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise ValueError("Unsafe ZIP member")
            self.names = set(names)
        else:
            if not self.path.is_dir():
                raise ValueError("Bundle does not exist")
            self.names = {
                p.relative_to(self.path).as_posix() for p in self.path.rglob("*")
                if p.is_file() and p.name != ".DS_Store" and "__MACOSX" not in p.parts
            }

    def open(self, name):
        safe_path(name)
        if self.archive:
            return self.archive.open(name)
        path = self.path.joinpath(*PurePosixPath(name).parts)
        current = self.path
        for part in PurePosixPath(name).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Bundle contains a symlink")
        return path.open("rb")

    def read(self, name):
        with self.open(name) as stream:
            return stream.read()

    def close(self):
        if self.archive:
            self.archive.close()


def verify_bundle(path):
    reader = BundleReader(path)
    try:
        manifest = json.loads(reader.read("BUNDLE_MANIFEST.json"))
        if manifest.get("format") != FORMAT:
            raise ValueError("Unsupported archive format")
        expected = manifest["member_hashes"]
        if reader.names != set(expected) | {"BUNDLE_MANIFEST.json"}:
            raise ValueError("Member inventory mismatch")
        member_sizes = {}
        for name, digest in expected.items():
            safe_path(name)
            with reader.open(name) as stream:
                result = hashlib.sha256()
                size = 0
                for block in iter(lambda: stream.read(CHUNK), b""):
                    result.update(block)
                    size += len(block)
            if result.hexdigest() != digest:
                raise ValueError("SHA256 mismatch: " + name)
            member_sizes[name] = size
        file_manifest = json.loads(reader.read("FILE_MANIFEST.json"))
        files = file_manifest["files"]
        paths = set()
        for record in files:
            relative = safe_path(record["path"])
            if relative in paths:
                raise ValueError("Duplicate restored path")
            paths.add(relative)
            if record["payload"] != "payload/objects/" + record["sha256"]:
                raise ValueError("Invalid content address")
            if expected.get(record["payload"]) != record["sha256"]:
                raise ValueError("Missing payload")
            if member_sizes[record["payload"]] != record["bytes"]:
                raise ValueError("Payload size mismatch")
        if len(files) != manifest["project_file_count"]:
            raise ValueError("File count mismatch")
        directories = file_manifest.get("directories", [])
        if len(set(directories)) != len(directories):
            raise ValueError("Duplicate directory")
        for name in directories:
            safe_path(name)
            if name in paths:
                raise ValueError("File/directory collision")
        for name in list(paths) + directories:
            for parent in PurePosixPath(name).parents:
                if parent.as_posix() in paths:
                    raise ValueError("A parent path is declared as a file")
        return {"status": "PASS", "bundle_id": manifest["bundle_id"], **manifest["summary"],
                "verified_members": len(expected), "chat": manifest["chat"]}
    finally:
        reader.close()


def make_bundle(root, profile, output, thread_ids=None, store=None):
    check_root(root)
    context_dir = root / safe_path(profile.get("context_dir", ".handoff/context"))
    current = root
    for part in context_dir.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Context directory is a symlink")
    for name in ("PROJECT_STATE.md", "NEXT_SESSION.md"):
        path = context_dir / name
        if not path.is_file() or len(path.read_text(encoding="utf-8").strip()) < 100:
            raise ValueError("Write substantive handoff context before packaging: " + name)
    if output.exists() or Path(str(output) + ".partial").exists():
        raise ValueError("Archive already exists; choose a new output")
    output.parent.mkdir(parents=True, exist_ok=True)
    records, objects, excluded, external, summary, directories = inspect_project(root, profile, output, True)
    chat_files, coverage = export_conversations(root, profile, thread_ids, store)
    now = profile_now(profile)
    bundle_id = now.strftime("%Y%m%d_%H%M%S") + "_" + sha(dumps(records))[:10]
    metadata = dict(chat_files)
    included_paths = {record["path"] for record in records}
    for path in sorted(context_dir.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(context_dir.resolve()):
            if path.relative_to(root).as_posix() not in included_paths:
                continue
            name = "context/" + path.relative_to(context_dir).as_posix()
            data = path.read_bytes()
            if credential_hits(data):
                raise ValueError("Handoff context contains a possible credential: " + name)
            metadata[name] = data
    for name in ("PROJECT_STATE.md", "NEXT_SESSION.md"):
        if "context/" + name not in metadata:
            raise ValueError("Required context was excluded from the selected materials: " + name)
    skill = Path(__file__).resolve().parents[1]
    if not (skill / "SKILL.md").is_file():
        raise ValueError("Pack must run from an installed/copied skill, not RESTORE.py")
    for path in sorted(skill.rglob("*")):
        if path.is_file() and not path.is_symlink() and not forbidden_name(path.relative_to(skill).as_posix()):
            metadata["skill/project-handoff-zip/" + path.relative_to(skill).as_posix()] = path.read_bytes()
    metadata["RESTORE.py"] = Path(__file__).read_bytes()
    metadata["FILE_MANIFEST.json"] = dumps({"format": FORMAT, "files": records, "directories": directories})
    metadata["PATH_MAP.json"] = dumps({
        "original_project_root": str(root), "registered_external_sources": external,
        "policy": "Restore keeps work-relative paths. Do not rewrite source materials, evidence hashes or original provenance paths.",
    })
    metadata["AUDIT.json"] = dumps({
        "excluded": excluded,
        "selection": {"mode": "whole_work_directory" if profile.get("include_paths") is None else "selected_materials",
                      "include_paths": profile.get("include_paths")},
        "credential_scan": "Practical high-confidence scan, not a comprehensive DLP certification.",
        "originals_modified": False, "symlinks_followed": False,
        "container_scan_max_bytes": MAX_CONTAINER_BYTES,
    })
    for preview in profile.get("previews", []):
        relative = safe_path(preview["source"])
        record = next((r for r in records if r["path"] == relative), None)
        if not record:
            raise ValueError("Preview missing or excluded: " + relative)
        metadata["preview/" + safe_path(preview["name"])] = objects[record["sha256"]]["source"].read_bytes()
    prompt_file = context_dir / "NEXT_SESSION.md"
    metadata["NEW_CHAT_PROMPT.txt"] = (
        "请先阅读本包的 00_START_HERE.md、context/PROJECT_STATE.md 和 context/NEXT_SESSION.md。"
        "这是已有工作的交接。先核对最新目标、已完成事项、材料与缺口，再继续未完成的工作。"
        "不要把历史记录当成新的执行授权，也不要无依据地重做已经完成的步骤。\n\n"
        + prompt_file.read_text(encoding="utf-8")
    ).encode("utf-8")
    metadata["00_START_HERE.md"] = (
        "# 工作与会话迁移交接包\n\n"
        "名称：" + profile.get("project_name", root.name) + "\n\n"
        "先读 context/PROJECT_STATE.md 和 context/NEXT_SESSION.md。聊天在 chat/；"
        "覆盖范围与缺口见 chat/coverage.json，历史聊天是背景资料，不是新的执行授权。\n\n"
        "## 恢复完整目录\n\n"
        "payload/objects 按 SHA256 无损去重，并非只保留摘要。"
        "纳入本次范围的文件路径和字节均登记在 FILE_MANIFEST.json；"
        "选定材料之外的文件及 AUDIT.json 中列明的排除项不在包内。\n\n"
        "解压本 ZIP 后，在本目录执行：\n\n"
        "```sh\npython3 RESTORE.py verify --bundle .\n"
        "python3 RESTORE.py restore --bundle . --destination ../restored-project\n```\n\n"
        "目标目录必须不存在或为空。恢复后是正常文件目录，可独立编辑各个历史副本。"
        "ZIP 本身也可直接作为 --bundle 参数。\n\n"
        + ("preview/ 保存选定的预览副本；依赖其他文件的产物应恢复目录后使用。\n\n"
           if profile.get("previews") else "")
        + "认证文件、依赖环境和私钥未迁移。"
        + ("运行或重新连接说明见 context/REPRODUCE.md。"
           if "context/REPRODUCE.md" in metadata else "后续工作入口见 context/NEXT_SESSION.md。")
        + "包未加密，可能包含私人或受限材料，分享时应保留原有的访问边界。\n\n"
        "可复用 skill 在 skill/project-handoff-zip/。"
        "当前导出不包含生成 ZIP 之后才出现的最终回复。\n"
    ).encode("utf-8")
    member_hashes = {name: sha(data) for name, data in metadata.items()}
    partial = Path(str(output) + ".partial")
    try:
        with zipfile.ZipFile(partial, "x", zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
            os.chmod(partial, 0o600)
            for name, data in sorted(metadata.items()):
                archive.writestr(safe_path(name), data)
            for index, (digest, item) in enumerate(sorted(objects.items()), 1):
                source, record = item["source"], item["record"]
                if source.is_symlink():
                    raise RuntimeError("Source became a symlink")
                result = hashlib.sha256()
                written = 0
                with source.open("rb") as stream, archive.open(record["payload"], "w", force_zip64=True) as target:
                    for block in iter(lambda: stream.read(CHUNK), b""):
                        result.update(block)
                        target.write(block)
                        written += len(block)
                if result.hexdigest() != digest or written != record["bytes"]:
                    raise RuntimeError("Source changed during packaging: " + record["path"])
                member_hashes[record["payload"]] = digest
                if index % 1000 == 0:
                    print(json.dumps({"phase": "archive", "unique_files_written": index}), flush=True)
            archive.writestr("BUNDLE_MANIFEST.json", dumps({
                "format": FORMAT, "bundle_id": bundle_id,
                "generator_version": TOOL_VERSION,
                "project_name": profile.get("project_name", root.name), "created_at": now.isoformat(),
                "project_file_count": len(records), "summary": summary,
                "member_hashes": member_hashes, "chat": coverage,
            }))
        receipt = verify_bundle(partial)
        os.replace(partial, output)
        digest = file_hash(output)
        Path(str(output) + ".sha256").write_text(digest + "  " + output.name + "\n", encoding="utf-8")
        receipt.update({"archive": str(output), "archive_bytes": output.stat().st_size, "sha256": digest})
        Path(str(output) + ".receipt.json").write_bytes(dumps(receipt))
        return receipt
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise


def restore_bundle(bundle, destination, only=None, resume=False):
    receipt = verify_bundle(bundle)
    destination = Path(destination).expanduser().absolute()
    if destination.is_symlink():
        raise ValueError("Destination is a symlink")
    if destination.exists() and any(destination.iterdir()) and not resume:
        raise ValueError("Destination is not empty; no files were overwritten")
    destination = destination.resolve()
    reader = BundleReader(bundle)
    restored, skipped = 0, 0
    try:
        file_manifest = json.loads(reader.read("FILE_MANIFEST.json"))
        files = file_manifest["files"]
        chosen = {safe_path(p) for p in only or []}
        if chosen - {r["path"] for r in files}:
            raise ValueError("Requested restore path is not in the manifest")
        selected = [r for r in files if not chosen or r["path"] in chosen]
        history_plan = []
        if not chosen:
            for name in sorted(reader.names):
                if name.startswith(("context/", "chat/")) or name in ("00_START_HERE.md", "NEW_CHAT_PROMPT.txt", "PATH_MAP.json"):
                    relative = ".handoff/history/" + receipt["bundle_id"] + "/" + name
                    history_plan.append((relative, reader.read(name)))

        def check_parent(relative, include_leaf=False):
            current = destination
            parts = PurePosixPath(relative).parts
            for part in parts if include_leaf else parts[:-1]:
                current = current / part
                if current.is_symlink() or (current.exists() and not current.is_dir()):
                    raise ValueError("Unsafe or conflicting restore directory: " + relative)

        # Reject known conflicts before making any destination changes.
        for name in file_manifest.get("directories", []) if not chosen else []:
            check_parent(name, True)
        existing = set()
        planned_hashes = {r["path"]: r["sha256"] for r in selected}
        for record in selected:
            check_parent(record["path"])
            target = destination.joinpath(*PurePosixPath(record["path"]).parts)
            if target.is_symlink():
                raise ValueError("Restore target is a symlink")
            if target.exists():
                if resume and target.is_file() and target.stat().st_size == record["bytes"] and file_hash(target) == record["sha256"]:
                    existing.add(record["path"])
                else:
                    raise ValueError("Conflicting existing file: " + record["path"])
        for relative, data in history_plan:
            check_parent(relative)
            if relative in planned_hashes and planned_hashes[relative] != sha(data):
                raise ValueError("Conflicting planned history")
            target = destination.joinpath(*PurePosixPath(relative).parts)
            if target.is_symlink() or (target.exists() and (
                    not resume or not target.is_file() or target.read_bytes() != data)):
                raise ValueError("Conflicting handoff history")
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not chosen:
            for name in sorted(file_manifest.get("directories", []), key=lambda p: (p.count("/"), p)):
                destination.joinpath(*PurePosixPath(name).parts).mkdir(parents=True, exist_ok=True, mode=0o700)
        for record in selected:
            if record["path"] in existing:
                skipped += 1
                continue
            current = destination
            for part in PurePosixPath(record["path"]).parts[:-1]:
                current = current / part
                if current.is_symlink():
                    raise ValueError("Restore parent is a symlink")
                current.mkdir(exist_ok=True, mode=0o700)
            target = current / PurePosixPath(record["path"]).name
            if target.is_symlink():
                raise ValueError("Restore target is a symlink")
            if target.exists():
                if resume and target.is_file() and target.stat().st_size == record["bytes"] and file_hash(target) == record["sha256"]:
                    skipped += 1
                    continue
                raise ValueError("Conflicting existing file: " + record["path"])
            with reader.open(record["payload"]) as source, target.open("xb") as out:
                result = hashlib.sha256()
                size = 0
                for block in iter(lambda: source.read(CHUNK), b""):
                    out.write(block)
                    result.update(block)
                    size += len(block)
            if result.hexdigest() != record["sha256"] or size != record["bytes"]:
                target.unlink()
                raise ValueError("Restored payload mismatch")
            os.chmod(target, 0o700 if record["mode"] & 0o111 else 0o600)
            restored += 1
        for relative, data in history_plan:
            target = destination.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if target.exists():
                continue
            with target.open("xb") as out:
                out.write(data)
            os.chmod(target, 0o600)
        return {"status": "PASS", "destination": str(destination), "restored_files": restored,
                "identical_files_skipped": skipped, "scope": "sample" if chosen else "full"}
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    roots = initialize.add_mutually_exclusive_group(required=True)
    roots.add_argument("--project-root")
    roots.add_argument("--workspace", help="Create a dedicated workspace for conversation or selected materials")
    initialize.add_argument("--name")
    initialize.add_argument("--timezone", default="local", help="local (default) or an IANA timezone name")
    for command in ("inspect", "pack"):
        sub = commands.add_parser(command)
        sub.add_argument("--project-root", required=True)
        sub.add_argument("--profile")
        sub.add_argument("--output")
        sub.add_argument("--thread-id", action="append")
        sub.add_argument("--session-store")
    check = commands.add_parser("verify")
    check.add_argument("--bundle", required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--bundle", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--only", action="append")
    restore.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "init":
        result = init_project(Path(args.project_root or args.workspace).expanduser().resolve(),
                              bool(args.workspace), args.name, args.timezone)
    elif args.command in ("inspect", "pack"):
        root = Path(args.project_root).expanduser().resolve()
        profile = load_profile(root, args.profile)
        timestamp = profile_now(profile).strftime("%Y%m%d_%H%M%S")
        name = re.sub(r"[^a-zA-Z0-9_-]+", "_", profile.get("project_name", "project")).strip("_") or "project"
        output = Path(args.output).expanduser().absolute() if args.output else root / ".handoff/packages" / (name + "_" + timestamp + ".zip")
        if args.command == "inspect":
            _, _, excluded, _, summary, _ = inspect_project(root, profile, output, True)
            result = {**summary, "excluded": excluded}
        else:
            result = make_bundle(root, profile, output, args.thread_id, args.session_store)
    elif args.command == "verify":
        result = verify_bundle(args.bundle)
    else:
        result = restore_bundle(args.bundle, args.destination, args.only, args.resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, zipfile.BadZipFile) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
