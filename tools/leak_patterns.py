"""leak_patterns.py: secret and personal-data shapes, shared by the guards.

Used by the Claude Code hook (tools/hooks/claude_guard.py) to deny writes that
carry a real secret, and by chat_import.py to redact pasted chat logs. It lived
in export_public.py until that public export was retired on 2026-11-15; the
hook imported it lazily, so deleting the exporter would have left the secret
check silently off. Keep this module free of imports beyond `re`.
"""
import re

LEAK_PATTERNS = [
    ("identity number (11 digits)", re.compile(r"(?<!\d)\d{11}(?!\d)")),
    ("private key block", re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY")),
    ("hex secret (64 hex)", re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")),
    ("bot token", re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")),
    ("github token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}")),
    ("google api key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("anthropic key", re.compile(r"(?<![A-Za-z])sk-ant-[a-z]+\d*-[A-Za-z0-9_-]{20,}")),
    ("openai key", re.compile(r"(?<![A-Za-z])sk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("google oauth secret", re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}")),
    ("slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws access key", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![0-9A-Z])")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}")),
    ("iban (TR)", re.compile(r"TR\d{24}")),
    ("iban (GB)", re.compile(r"(?<![A-Z0-9])GB\d{2} ?[A-Z]{4}(?: ?\d{4}){3} ?\d{2}(?![0-9])")),
    ("tailscale ip", re.compile(r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}")),
    ("private ipv4", re.compile(r"(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}")),
    ("tailscale host", re.compile(r"[A-Za-z0-9-]+\.ts\.net")),
    ("phone (+90)", re.compile(r"\+90\d{10}")),
    ("e-mail address", re.compile(r"[\w.+-]+@(?!example\.|github\.com|report\.spiky\.ai)[\w-]+\.[\w.]+")),
]
