"""
Prompt security layer / guardrails (PRD Feature 11).

Runs on every prompt *before* it is forwarded to any provider. Detects and acts on:

  - prompt injection / jailbreak attempts      -> score; block above threshold
  - PII (email, phone, SSN, credit card)       -> redact in place, allow
  - secrets (API keys, AWS keys, private keys)  -> hard block (never exfiltrate)

This is the boring-but-critical layer that makes a gateway enterprise-credible:
it means a careless prompt can't leak an API key to a third-party provider, and a
hostile user can't trivially override the system prompt. Detection here is
heuristic (regex + signal aggregation) — the same shape as real WAF-style
guardrails, and easily swapped for a fine-tuned classifier (e.g. Llama-Guard).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .config import (
    INJECTION_BLOCK_THRESHOLD,
    SECURITY_BLOCK_ON,
    SECURITY_REDACT_ON,
)

# ---- detectors ------------------------------------------------------------- #

_PII = [
    ("pii_email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("pii_phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("pii_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("pii_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
]

_SECRETS = [
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("api_key", re.compile(r"\b(?:api[_-]?key|secret)[\"'`:=\s]+[A-Za-z0-9_\-]{16,}\b", re.I)),
    ("credential", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),                 # AWS access key
    ("secret", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),  # private key
    ("credential", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),              # GitHub token
]

# Injection / jailbreak signals, each contributing weighted evidence.
_INJECTION = [
    (0.45, re.compile(r"\bignore (all|any|the|previous|prior)\b.*\b(instruction|prompt|rule)", re.I)),
    (0.45, re.compile(r"\bdisregard (the|all|your|previous)\b.*\b(instruction|system|rule)", re.I)),
    (0.40, re.compile(r"\b(you are now|act as|pretend to be|roleplay as)\b.*\b(dan|jailbreak|unfiltered|no restrictions)", re.I)),
    (0.35, re.compile(r"\bdeveloper mode\b|\bjailbreak\b|\bDAN\b")),
    (0.35, re.compile(r"\b(reveal|print|show|repeat) (your|the) (system prompt|instructions|prompt)\b", re.I)),
    (0.30, re.compile(r"\bdo anything now\b|\bwithout any (restrictions|filter|guardrail)", re.I)),
    (0.25, re.compile(r"\boverride (your|the|all) (safety|guardrail|instruction)", re.I)),
]


@dataclass
class SecurityVerdict:
    action: str                       # "allow" | "redact" | "block"
    injection_score: float
    findings: List[str] = field(default_factory=list)   # category labels
    redactions: int = 0
    sanitized_prompt: str = ""
    reason: str = ""


def _scan_secrets(text: str) -> List[str]:
    found = []
    for label, pat in _SECRETS:
        if pat.search(text):
            found.append(label)
    return found


def _redact_pii(text: str) -> Tuple[str, List[str], int]:
    found: List[str] = []
    count = 0
    out = text
    for label, pat in _PII:
        def _sub(m, _l=label):
            nonlocal count
            count += 1
            return f"[REDACTED:{_l.split('_')[1].upper()}]"
        new = pat.sub(_sub, out)
        if new != out:
            found.append(label)
            out = new
    return out, found, count


def _injection_score(text: str) -> Tuple[float, List[str]]:
    score = 0.0
    hits: List[str] = []
    for weight, pat in _INJECTION:
        if pat.search(text):
            score += weight
            hits.append(pat.pattern[:40])
    return min(1.0, score), hits


def scan(prompt: str) -> SecurityVerdict:
    findings: List[str] = []

    # 1. secrets — hard block, do not forward
    secrets = _scan_secrets(prompt)
    if secrets and (set(secrets) & SECURITY_BLOCK_ON):
        return SecurityVerdict(
            action="block", injection_score=0.0, findings=secrets,
            sanitized_prompt="", reason=f"blocked: detected {', '.join(sorted(set(secrets)))}",
        )

    # 2. injection / jailbreak
    inj_score, inj_hits = _injection_score(prompt)
    if inj_score >= INJECTION_BLOCK_THRESHOLD:
        return SecurityVerdict(
            action="block", injection_score=round(inj_score, 3),
            findings=["prompt_injection"], sanitized_prompt="",
            reason=f"blocked: prompt-injection score {inj_score:.2f} >= {INJECTION_BLOCK_THRESHOLD}",
        )

    # 3. PII — redact and allow
    sanitized, pii, count = _redact_pii(prompt)
    findings.extend(pii)
    action = "redact" if (set(pii) & SECURITY_REDACT_ON) else "allow"
    reason = f"redacted {count} PII span(s)" if count else "clean"
    if inj_score > 0:
        findings.append("injection_signal")

    return SecurityVerdict(
        action=action, injection_score=round(inj_score, 3), findings=findings,
        redactions=count, sanitized_prompt=sanitized, reason=reason,
    )
